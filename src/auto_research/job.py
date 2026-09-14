"""Detached local supervisor. It never reads or writes the research database."""

import argparse
import fcntl
import os
import signal
import subprocess
import time
from pathlib import Path

from .io import atomic_json, process_identity, read_json


def supervise(job_dir: Path) -> int:
    request = read_json(job_dir / "request.json")
    if not request:
        return 2
    with (job_dir / "execution.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 3
        if read_json(job_dir / "status.json", {}).get("terminal"):
            return 0
        state = {
            "attempt_id": request["attempt_id"],
            "pid": os.getpid(),
            "identity": process_identity(os.getpid()),
            "terminal": False,
            "started_at": time.time(),
            "heartbeat": time.time(),
            "phase": "starting",
        }
        atomic_json(job_dir / "status.json", state)
        stopped = []

        def stop(signum, frame):
            if not stopped:
                stopped.append("interrupted")

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        process = None
        try:
            command = [
                arg.replace("{request}", str(job_dir / "request.json")).replace(
                    "{output}", str(job_dir / "output.json")
                )
                for arg in request["backend_command"]
            ]
            with (job_dir / "stdout.log").open("ab") as stdout, (job_dir / "stderr.log").open(
                "ab"
            ) as stderr:
                process = subprocess.Popen(
                    command,
                    cwd=request["workspace"],
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
                state.update(
                    phase="running",
                    child_pid=process.pid,
                    child_identity=process_identity(process.pid),
                )
                deadline = time.monotonic() + float(request.get("timeout_s", 900))
                termination_started = None
                while process.poll() is None:
                    if time.monotonic() >= deadline and not stopped:
                        stopped.append("timeout")
                    if stopped and termination_started is None:
                        termination_started = time.monotonic()
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except ProcessLookupError:
                            pass
                    if termination_started and time.monotonic() - termination_started > 5:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except ProcessLookupError:
                            pass
                    state["heartbeat"] = time.time()
                    atomic_json(job_dir / "status.json", state)
                    time.sleep(0.1)
                # Descendant tools must not outlive a completed backend invocation.
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    time.sleep(0.2)
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                state.update(returncode=process.returncode)
                output = read_json(job_dir / "output.json")
                if stopped:
                    state.update(phase="interrupted", reason=stopped[0])
                elif process.returncode != 0 or not isinstance(output, dict):
                    state.update(
                        phase="failed", reason="backend failed or omitted valid output.json"
                    )
                else:
                    state.update(phase="completed")
        except Exception as error:
            state.update(phase="failed", reason=f"{type(error).__name__}: {error}")
        finally:
            if process and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)
                except (ProcessLookupError, subprocess.TimeoutExpired):
                    pass
            state.update(terminal=True, finished_at=time.time(), heartbeat=time.time())
            atomic_json(job_dir / "status.json", state)
        return 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("job_dir", type=Path)
    args = parser.parse_args()
    raise SystemExit(supervise(args.job_dir.resolve()))


if __name__ == "__main__":
    main()
