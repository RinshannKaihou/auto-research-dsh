"""Real subprocess tests of supervisor ownership, termination and recovery markers."""

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest

from auto_research.io import atomic_json, process_identity, read_json


BACKEND = r'''
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

mode, output = sys.argv[1], Path(sys.argv[2])
with Path("executions.log").open("a") as stream:
    stream.write(str(os.getpid()) + "\n")
Path("cwd.txt").write_text(str(Path.cwd()))
Path("ready").write_text(str(os.getpid()))
value = {"result": {"close_reason": "complete"}, "usage": {"tokens": 1}}
if mode in {"wait", "ignore"}:
    if mode == "ignore":
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        Path("ignoring").write_text("ready")
    time.sleep(60)
elif mode == "orphan":
    code = """import os, signal, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path('orphan.pid').write_text(str(os.getpid()))
time.sleep(60)
"""
    subprocess.Popen([sys.executable, "-c", code])
    deadline = time.monotonic() + 5
    while not Path("orphan.pid").exists():
        if time.monotonic() > deadline:
            raise RuntimeError("orphan startup failed")
        time.sleep(.01)
elif mode == "invalid":
    output.write_text("{bad json")
    raise SystemExit(0)
elif mode == "missing":
    raise SystemExit(0)
elif mode == "list":
    output.write_text("[]")
    raise SystemExit(0)
output.write_text(json.dumps(value))
if mode == "nonzero":
    raise SystemExit(7)
'''


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.job = self.root / "job"
        self.job.mkdir()
        self.backend = self.root / "backend.py"
        self.backend.write_text(BACKEND)
        self.processes = []
        self.logs = []

    def tearDown(self):
        state = read_json(self.job / "status.json", {})
        child_pid = state.get("child_pid")
        if child_pid:
            try:
                os.killpg(child_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        for process in self.processes:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        for stream in self.logs:
            stream.close()
        self.temporary.cleanup()

    def request(self, mode="good", **updates):
        request = {
            "attempt_id": "A-job-test",
            "workspace": str(self.workspace),
            "timeout_s": 20,
            "backend_command": [sys.executable, str(self.backend), mode, "{output}"],
            **updates,
        }
        atomic_json(self.job / "request.json", request)

    def launch(self):
        environment = dict(os.environ)
        source = str(Path(__file__).resolve().parents[1] / "src")
        environment["PYTHONPATH"] = source + os.pathsep + environment.get("PYTHONPATH", "")
        command = [sys.executable, "-m", "auto_research.job", str(self.job)]
        coverage_module = sys.modules.get("coverage")
        running_coverage = coverage_module.Coverage.current() if coverage_module else None
        if running_coverage:
            # Preserve normal stdlib-only test execution. A coverage run may
            # opt its real children into separate data files for later combine.
            environment["COVERAGE_FILE"] = str(running_coverage.config.data_file)
            command = [
                sys.executable,
                "-m",
                "coverage",
                "run",
                "--parallel-mode",
                "--source=auto_research.job",
                "-m",
                "auto_research.job",
                str(self.job),
            ]
        log = (self.root / f"supervisor-{len(self.processes)}.log").open("wb")
        self.logs.append(log)
        process = subprocess.Popen(
            command,
            cwd=self.workspace,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.processes.append(process)
        return process

    def wait_for(self, predicate, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.03)
        self.fail("Expected supervisor state did not arrive before timeout")

    def finished(self, process):
        self.assertEqual(process.wait(timeout=12), 0)
        state = read_json(self.job / "status.json")
        self.assertTrue(state["terminal"])
        self.assertGreaterEqual(state["finished_at"], state["started_at"])
        return state

    def assert_backend_stopped(self, state):
        self.wait_for(lambda: process_identity(state["child_pid"]) is None)

    def test_success_records_actual_working_directory_and_preserves_output(self):
        self.request()
        state = self.finished(self.launch())
        self.assertEqual(state["phase"], "completed")
        self.assertEqual(state["returncode"], 0)
        self.assertEqual((self.workspace / "cwd.txt").read_text(), str(self.workspace))
        self.assertEqual(read_json(self.job / "output.json")["usage"]["tokens"], 1)
        self.assertEqual(state["attempt_id"], "A-job-test")
        self.assert_backend_stopped(state)

    def test_missing_request_does_not_start_a_backend(self):
        process = self.launch()
        self.assertEqual(process.wait(timeout=5), 2)
        self.assertFalse((self.job / "status.json").exists())
        self.assertFalse((self.workspace / "executions.log").exists())

    def test_spawn_failure_becomes_terminal_failure(self):
        self.request(backend_command=[str(self.root / "nonexistent-backend")])
        state = self.finished(self.launch())
        self.assertEqual(state["phase"], "failed")
        self.assertIn("FileNotFoundError", state["reason"])

    def test_bad_or_missing_output_and_nonzero_exit_fail(self):
        for mode in ("invalid", "missing", "list", "nonzero"):
            with self.subTest(mode=mode):
                for file in ("status.json", "output.json"):
                    (self.job / file).unlink(missing_ok=True)
                self.request(mode)
                state = self.finished(self.launch())
                self.assertEqual(state["phase"], "failed")
                self.assertEqual(state["returncode"], 7 if mode == "nonzero" else 0)
                self.assert_backend_stopped(state)

    def test_timeout_kills_a_backend_which_ignores_sigterm(self):
        self.request("ignore", timeout_s=0.2)
        process = self.launch()
        self.wait_for(lambda: (self.workspace / "ignoring").exists())
        state = self.finished(process)
        self.assertEqual(state["phase"], "interrupted")
        self.assertEqual(state["reason"], "timeout")
        self.assertEqual(state["returncode"], -signal.SIGKILL)
        self.assert_backend_stopped(state)

    def test_explicit_interrupt_terminates_backend_before_terminal_state(self):
        self.request("wait")
        process = self.launch()
        self.wait_for(lambda: read_json(self.job / "status.json", {}).get("phase") == "running")
        os.kill(process.pid, signal.SIGTERM)
        state = self.finished(process)
        self.assertEqual(state["phase"], "interrupted")
        self.assertEqual(state["reason"], "interrupted")
        self.assert_backend_stopped(state)

    def test_duplicate_live_supervisor_cannot_execute_twice(self):
        self.request("wait")
        first = self.launch()
        self.wait_for(lambda: (self.workspace / "ready").exists())
        second = self.launch()
        self.assertEqual(second.wait(timeout=5), 3)
        self.assertEqual(len((self.workspace / "executions.log").read_text().splitlines()), 1)
        os.kill(first.pid, signal.SIGINT)
        state = self.finished(first)
        self.assertEqual(state["phase"], "interrupted")
        self.assert_backend_stopped(state)

    def test_completed_supervisor_is_idempotent(self):
        self.request()
        original = self.finished(self.launch())
        self.finished(self.launch())
        self.assertEqual(read_json(self.job / "status.json"), original)
        self.assertEqual(len((self.workspace / "executions.log").read_text().splitlines()), 1)

    def test_backend_exit_cleans_a_sigterm_ignoring_descendant(self):
        self.request("orphan")
        state = self.finished(self.launch())
        self.assertEqual(state["phase"], "completed")
        orphan = int((self.workspace / "orphan.pid").read_text())
        self.wait_for(lambda: process_identity(orphan) is None)
        self.assert_backend_stopped(state)

    def test_exception_after_spawn_still_kills_backend(self):
        self.request("wait", timeout_s="invalid-number")
        state = self.finished(self.launch())
        self.assertEqual(state["phase"], "failed")
        self.assertIn("ValueError", state["reason"])
        self.assert_backend_stopped(state)


if __name__ == "__main__":
    unittest.main()
