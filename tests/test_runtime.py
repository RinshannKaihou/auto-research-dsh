import concurrent.futures
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from auto_research.cli import execute, parser
from auto_research.io import atomic_json, process_identity, read_json
from auto_research.runtime import (
    Runtime,
    backup,
    check,
    coordinator_lock,
    export_views,
    validate_config,
)
from auto_research.store import Store


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def project(tmp_path, command=None, budget=100, **config):
    store = Store(tmp_path / "project")
    store.initialize(
        "Explore a persistent open question",
        budget=budget,
        config={
            "backend_command": command
            or [sys.executable, str(EXAMPLES / "scripted_backend.py"), "{request}", "{output}"],
            "worker_estimate": 2,
            "coordinator_estimate": 2,
            "concurrency": 2,
            "timeout_s": 10,
            **config,
        },
    )
    return Runtime(store.root)


def propose(runtime, label="A"):
    return runtime.store.propose(
        {
            "question": "Q-001",
            "why_now": label,
            "plan": "Continue work",
            "inputs": [],
            "modifies_artifact": False,
        }
    )


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.03)
    raise AssertionError("Timed out waiting for observable process state")


def terminal(runtime, attempt_id):
    runtime.reconcile()
    attempt = runtime.store.get_attempt(attempt_id)
    return attempt if attempt["state"] in {"completed", "failed", "interrupted"} else None


def custom_backend(tmp_path, body):
    path = tmp_path / "backend.py"
    path.write_text("import json,sys,time\nfrom pathlib import Path\n" + body)
    return [sys.executable, str(path), "{request}", "{output}"]


EMPTY_RESULT = {
    "close_reason": "Partial work saved",
    "limitations": "Unresolved",
    "products": [],
    "findings": [],
    "next": [],
    "inputs": [],
}


@pytest.mark.parametrize(
    "config",
    [
        {"concurrency": 0},
        {"concurrency": "2"},
        {"max_coordinator_calls": -1},
        {"timeout_s": 0},
        {"timeout_s": 90000},
        {"worker_estimate": float("nan")},
        {"coordinator_estimate": -1},
    ],
)
def test_invalid_configuration_cannot_spin_or_disable_timeouts(config):
    with pytest.raises(ValueError):
        validate_config(config)


def test_complete_graph_parallel_handoff_revision_and_backup(tmp_path):
    runtime = project(tmp_path)
    result = runtime.start(poll_interval=0.05, max_cycles=500)
    nodes = {n["why_now"]: n for n in result["nodes"]}
    assert set(nodes) == set("ABCDE")
    assert all(n["status"] == "closed" for n in nodes.values())
    assert nodes["A"]["result"]["findings"] == []
    assert nodes["B"]["result"]["products"][0]["status"] == "partial"
    assert len(nodes["E"]["inputs"]) == 2
    assert nodes["C"]["result"]["findings"][0]["revises"] == nodes["B"]["id"] + "/result#local"
    assert runtime.store.resolve(nodes["B"]["id"] + "/result#local")["kind"] == "finding"
    worker_attempts = {a["node_id"]: a for a in result["attempts"] if a["role"] == "worker"}
    states = {
        key: read_json(Path(worker_attempts[node["id"]]["job_dir"]) / "status.json")
        for key, node in nodes.items()
    }
    assert states["B"]["started_at"] < states["C"]["finished_at"]
    assert states["C"]["started_at"] < states["B"]["finished_at"]
    assert states["D"]["started_at"] < states["C"]["finished_at"]
    assert result["budget"]["spent"] == len(result["attempts"])
    assert result["budget"]["held"] == 0
    assert check(runtime.root)["ok"]
    saved = tmp_path / "backup"
    backup(runtime.root, saved)
    restored = tmp_path / "restored"
    args = parser().parse_args(["-p", str(restored), "restore", str(saved)])
    assert execute(args)["ok"]
    assert Store(restored).get_node(nodes["A"]["id"])["result"] == nodes["A"]["result"]
    assert all(Path(a["workspace"]).is_relative_to(restored) for a in Store(restored).attempts())


def test_new_coordinator_collects_existing_supervisor_without_duplicate(tmp_path):
    command = custom_backend(
        tmp_path,
        "time.sleep(.8)\nPath(sys.argv[2]).write_text(json.dumps("
        + repr({"result": EMPTY_RESULT, "usage": {"tokens": 3}})
        + "))\n",
    )
    runtime = project(tmp_path, command)
    node = propose(runtime)
    attempt = runtime.dispatch(node)
    wait_for(lambda: read_json(Path(attempt["job_dir"]) / "status.json", {}).get("child_pid"))
    replacement = Runtime(runtime.root)
    replacement.reconcile()
    assert len(replacement.store.attempts()) == 1
    assert replacement.store.get_attempt(attempt["id"])["state"] == "running"
    wait_for(lambda: terminal(replacement, attempt["id"]))
    assert replacement.store.get_node(node["id"])["status"] == "closed"
    assert replacement.store.budget()["spent"] == 3
    assert len(replacement.store.attempts()) == 1
    runtime.reconcile()


@pytest.mark.parametrize(
    "usage", [None, [], {"tokens": "unknown"}, {"tokens": -1}, {"tokens": True}]
)
def test_missing_or_bad_usage_retains_hold(tmp_path, usage):
    command = custom_backend(
        tmp_path,
        "Path(sys.argv[2]).write_text(json.dumps("
        + repr({"result": EMPTY_RESULT, "usage": usage})
        + "))\n",
    )
    runtime = project(tmp_path, command)
    attempt = runtime.dispatch(propose(runtime))
    settled = wait_for(lambda: terminal(runtime, attempt["id"]))
    assert settled["state"] == "completed"
    assert settled["cost"] is None
    assert runtime.store.budget()["held"] == 2
    assert runtime.store.budget()["unknown_count"] == 1


def test_pause_drains_and_stop_preserves_open_node(tmp_path):
    command = custom_backend(
        tmp_path,
        "Path('progress.json').write_text(json.dumps({'pending':'continue here'}))\ntime.sleep(20)\n",
    )
    runtime = project(tmp_path, command)
    node = propose(runtime)
    attempt = runtime.dispatch(node)
    wait_for(lambda: (Path(attempt["workspace"]) / "progress.json").exists())
    runtime.store.configure(control="paused")
    assert runtime.tick()["dispatched"] == 0
    with pytest.raises(ValueError, match="still alive"):
        runtime.confirm_stopped(attempt["id"], None, "unknown")
    runtime.stop()
    settled = wait_for(lambda: terminal(runtime, attempt["id"]))
    assert settled["state"] == "interrupted"
    assert runtime.store.get_node(node["id"])["status"] == "open"
    assert settled["progress"]["pending"] == "continue here"
    assert runtime.store.budget()["held"] == 2


def test_unknown_launch_is_never_automatically_retried(tmp_path):
    runtime = project(tmp_path)
    node = propose(runtime)
    attempt = runtime.store.reserve(node["id"], estimate=2)
    runtime.store.set_attempt(attempt["id"], dispatch_prepared=True, dispatch_at=0)
    runtime.store.configure(control="running")
    result = runtime.tick()
    assert result["control"] == "paused"
    assert runtime.store.get_attempt(attempt["id"])["state"] == "unknown"
    assert len(runtime.store.attempts()) == 1
    runtime.confirm_stopped(attempt["id"], 1, "estimated")
    assert runtime.store.budget()["held"] == 0
    assert runtime.store.get_node(node["id"])["status"] == "open"


def test_reservation_without_dispatch_can_be_safely_recovered(tmp_path):
    runtime = project(tmp_path)
    attempt = runtime.store.reserve(propose(runtime)["id"], estimate=2)
    runtime.reconcile()
    assert runtime.store.get_attempt(attempt["id"])["state"] == "interrupted"
    assert runtime.store.budget()["held"] == 0
    assert runtime.store.budget()["unknown_count"] == 0


def test_budget_blocks_before_spawn(tmp_path):
    runtime = project(tmp_path, budget=1)
    propose(runtime)
    runtime.store.configure(control="running")
    assert runtime.tick()["dispatched"] == 0
    assert runtime.store.attempts() == []
    assert runtime.store.get_node("X-001")["status"] == "proposed"


def test_invalid_output_does_not_publish_or_become_scientific_negative(tmp_path):
    invalid = {**EMPTY_RESULT, "products": [{"id": "escape", "path": "../outside"}]}
    command = custom_backend(
        tmp_path,
        "Path(sys.argv[2]).write_text(json.dumps("
        + repr({"result": invalid, "usage": {"tokens": 1}})
        + "))\n",
    )
    runtime = project(tmp_path, command)
    runtime.store.configure(control="running")
    node = propose(runtime)
    attempt = runtime.dispatch(node)
    settled = wait_for(lambda: terminal(runtime, attempt["id"]))
    assert settled["state"] == "failed"
    assert runtime.store.get_node(node["id"])["result"] is None
    assert runtime.config()["control"] == "paused"
    assert runtime.store.budget()["spent"] == 1


def test_coordinator_failure_can_retry_on_explicit_resume(tmp_path):
    command = custom_backend(
        tmp_path, "Path(sys.argv[2]).write_text(json.dumps({'result':{},'usage':{'tokens':1}}))\n"
    )
    runtime = project(tmp_path, command)
    runtime.start(poll_interval=0.03, max_cycles=150)
    first = runtime.store.attempts()
    assert len(first) == 1 and first[0]["state"] == "failed"
    runtime.start(poll_interval=0.03, max_cycles=150)
    attempts = runtime.store.attempts()
    assert len(attempts) == 2 and attempts[-1]["state"] == "failed"


def test_lock_rejects_second_coordinator_and_backup(tmp_path):
    runtime = project(tmp_path)
    with coordinator_lock(runtime.root):
        with pytest.raises(RuntimeError, match="already"):
            runtime.start(max_cycles=1)
        with pytest.raises(RuntimeError, match="already"):
            backup(runtime.root, tmp_path / "copy")
    with pytest.raises(ValueError, match="outside"):
        backup(runtime.root, runtime.root / "backup")


def test_concurrent_generated_views_and_damaged_archive(tmp_path):
    runtime = project(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        paths = list(pool.map(lambda _: export_views(Store(runtime.root)), range(20)))
    assert all((p / "index.json").is_file() for p in paths)
    assert "config" not in read_json(paths[0] / "index.json")["project"]
    assert check(runtime.root)["ok"]


def test_backup_restored_failed_attempt_continues_from_new_location(tmp_path):
    runtime = project(tmp_path)
    node = propose(runtime)
    attempt = runtime.store.reserve(node["id"], estimate=2)
    workspace = runtime.artifacts.prepare_workspace(attempt["id"])
    (workspace / "scratch" / "important.txt").write_text("unfinished proof")
    runtime.store.set_attempt(
        attempt["id"], workspace=str(workspace), job_dir=str(runtime.jobs / attempt["id"])
    )
    runtime.store.settle(attempt["id"], 1, state="interrupted", cost_kind="actual")
    saved = tmp_path / "saved"
    backup(runtime.root, saved)
    restored = tmp_path / "restored"
    execute(parser().parse_args(["-p", str(restored), "restore", str(saved)]))
    # Rename rather than delete the source to prove the new attempt does not read it.
    runtime.root.rename(tmp_path / "old-offline")
    resumed = Runtime(restored)
    fresh = resumed.dispatch(resumed.store.get_node(node["id"]))
    assert (
        Path(fresh["workspace"]) / "prior-attempt/scratch/important.txt"
    ).read_text() == "unfinished proof"
    wait_for(lambda: terminal(resumed, fresh["id"]))


def test_killed_supervisor_does_not_hide_live_backend(tmp_path):
    command = custom_backend(tmp_path, "time.sleep(20)\n")
    runtime = project(tmp_path, command)
    attempt = runtime.dispatch(propose(runtime))
    path = Path(attempt["job_dir"]) / "status.json"
    state = wait_for(
        lambda: (s if s.get("child_identity") else None) if (s := read_json(path, {})) else None
    )
    os.kill(state["pid"], signal.SIGKILL)
    runtime.children[attempt["id"]].wait(timeout=3)
    runtime.store.set_attempt(attempt["id"], dispatch_at=0)
    runtime.reconcile()
    assert runtime.store.get_attempt(attempt["id"])["state"] == "unknown"
    with pytest.raises(ValueError, match="Backend is still alive"):
        runtime.confirm_stopped(attempt["id"], None, "unknown")
    runtime.stop()
    wait_for(lambda: process_identity(state["child_pid"]) is None)
    runtime.confirm_stopped(attempt["id"], None, "unknown")
