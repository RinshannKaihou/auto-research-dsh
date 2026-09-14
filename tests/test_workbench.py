import concurrent.futures
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from auto_research.errors import NotFoundError, ValidationError
from auto_research.io import read_json
from auto_research.runtime import Runtime, check, coordinator_lock
from auto_research.store import Store
from auto_research.workbench import Workbench


EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for workbench state")


class FakeFactory:
    def __init__(self):
        self.instances = []
        self.allow_first_hook = threading.Event()
        self.allow_first_hook.set()
        self.drain = threading.Event()
        self.drain.set()
        self.finish = threading.Event()
        self.exit_reached = threading.Event()
        self.allow_exit = threading.Event()
        self.allow_exit.set()
        self.lock = threading.Lock()
        self.start_calls = 0
        self.active = 0
        self.max_active = 0
        self.ticks = 0
        self.stop_calls = 0
        self.reconcile_calls = 0
        self.fail_once = False

    def __call__(self, root):
        runtime = FakeRuntime(root, self)
        self.instances.append(runtime)
        return runtime


class FakeRuntime:
    def __init__(self, root, factory):
        self.root = root
        self.store = Store(root)
        self.factory = factory
        self.thread = None

    def touch(self):
        current = threading.get_ident()
        if self.thread is None:
            self.thread = current
        assert self.thread == current, "Runtime instance was called from multiple threads"

    def start(self, *, control_hook):
        self.touch()
        f = self.factory
        with coordinator_lock(self.root):
            with f.lock:
                f.start_calls += 1
                f.active += 1
                f.max_active = max(f.active, f.max_active)
            try:
                self.store.configure(control="running")
                assert f.allow_first_hook.wait(5)
                while True:
                    control_hook(self)
                    if f.fail_once:
                        f.fail_once = False
                        raise RuntimeError("Simulated runtime failure")
                    control = self.store.project()["config"]["control"]
                    if control == "running":
                        f.ticks += 1
                    if f.finish.is_set():
                        self.store.configure(control="paused")
                        control = "paused"
                    if control != "running" and f.drain.is_set():
                        f.exit_reached.set()
                        assert f.allow_exit.wait(5)
                        return self.store.snapshot()
                    time.sleep(0.005)
            finally:
                with f.lock:
                    f.active -= 1

    def stop(self):
        self.touch()
        self.factory.stop_calls += 1
        self.store.configure(control="stopped")
        return self.store.snapshot()

    def reconcile(self):
        self.touch()
        self.factory.reconcile_calls += 1
        return self.store.snapshot()


def fake_project(tmp_path, factory=None):
    factory = factory or FakeFactory()
    bench = Workbench(tmp_path / "research", runtime_factory=factory)
    state = bench.create({"name": "耦合系统", "goal": "局部稳定性条件能否推广？"})
    return bench, state["id"], factory


def idle(bench, identifier):
    state = bench.project_state(identifier)
    return state if not state["controller_running"] and not state.get("pending_action") else None


def test_create_is_read_only_until_explicit_start_and_defaults_are_public(tmp_path):
    factory = FakeFactory()
    resource = tmp_path / "资料.txt"
    resource.write_text("Existing research", encoding="utf-8")
    command = [sys.executable, "secret-backend-path.py"]
    bench = Workbench(
        tmp_path / "workspace",
        runtime_factory=factory,
        defaults={"budget": 80, "estimate": 4, "backend_command": command},
    )
    state = bench.create({"name": "稳定性研究", "goal": "保留最初问题", "resources": [str(resource)]})
    store = bench.get_store(state["id"])
    assert state["control"] == "paused"
    assert state["controller_running"] is False
    assert state["active_attempts"] == state["node_count"] == 0
    assert state["settings"]["budget"] == 80
    assert state["settings"]["resources"] == [str(resource.resolve())]
    assert state["agenda"]["questions"] == [{"id": "Q-001", "text": "保留最初问题"}]
    assert store.project()["config"]["backend_command"] == command
    assert set(bench.defaults) == {"budget", "concurrency", "estimate", "timeout"}
    assert "secret-backend-path" not in json.dumps(state) + json.dumps(bench.defaults)
    assert factory.instances == []
    assert check(store.root)["ok"]
    bench.close()


@pytest.mark.parametrize(
    "invalid",
    [
        {"name": "../escape"},
        {"name": "nested/name"},
        {"name": "a\\b"},
        {"name": ".."},
        {"goal": " "},
        {"budget": -1},
        {"budget": float("nan")},
        {"budget": True},
        {"concurrency": 0},
        {"concurrency": 2.5},
        {"estimate": -5},
        {"timeout": 0},
        {"resources": "not-a-list"},
        {"resources": ["/no/such/research/resource"]},
        {"dsh_config": "/not/real.json"},
        {"backend_command": ["untrusted"]},
        {"config": {"backend_command": ["untrusted"]}},
    ],
)
def test_invalid_create_leaves_no_destination_or_workspace(tmp_path, invalid):
    workspace = tmp_path / "workspace"
    target = workspace / "target"
    bench = Workbench(workspace)
    with pytest.raises((ValidationError, ValueError)):
        bench.create({"name": "新研究", "goal": "Question", "path": str(target), **invalid})
    assert not workspace.exists()


@pytest.mark.parametrize("kind", ["directory", "file", "symlink", "dangling"])
def test_create_never_overwrites_existing_destination(tmp_path, kind):
    target = tmp_path / "occupied"
    if kind == "directory":
        target.mkdir()
    elif kind == "file":
        target.write_text("keep")
    else:
        other = tmp_path / "other"
        if kind == "symlink":
            other.mkdir()
        target.symlink_to(other, target_is_directory=True)
    bench = Workbench(tmp_path)
    with pytest.raises(ValidationError, match="已存在"):
        bench.create({"name": "Project", "goal": "Question", "path": str(target)})
    assert not (target / ".research").exists()
    if kind == "dangling":
        assert not (tmp_path / "other").exists()


def test_scan_and_explicit_open_use_canonical_identity_without_initializing_dirs(tmp_path):
    workspace = tmp_path / "workspace"
    direct = Store(workspace / "direct")
    direct.initialize("Direct")
    deep = Store(workspace / "nested" / "deep")
    deep.initialize("Deep")
    external = Store(tmp_path / "outside")
    external.initialize("Outside")
    (workspace / "alias").symlink_to(external.root, target_is_directory=True)
    bench = Workbench(workspace)
    assert [p["goal"] for p in bench.list_projects()] == ["Direct"]
    opened = bench.open(external.root)
    assert bench.open(workspace / "alias")["id"] == opened["id"]
    assert len(bench.list_projects()) == 2
    assert bench.open(deep.root)["goal"] == "Deep"
    uninitialized = tmp_path / "empty"
    uninitialized.mkdir()
    with pytest.raises(ValidationError):
        bench.open(uninitialized)
    assert list(uninitialized.iterdir()) == []
    with pytest.raises(NotFoundError):
        bench.project_state("made-up")


def test_update_preserves_goal_results_and_unknown_configuration(tmp_path):
    bench, identifier, _ = fake_project(tmp_path)
    store = bench.get_store(identifier)
    store.configure(other_setting={"retained": True})
    node = store.propose(
        {"question": "Q-001", "why_now": "Partial proof", "plan": "Save work", "inputs": []}
    )
    attempt = store.reserve(node["id"], estimate=0)
    store.settle(attempt["id"], 0, cost_kind="estimated", state="completed")
    store.publish(
        node["id"],
        {
            "close_reason": "Working fragment",
            "limitations": "Open question",
            "findings": [],
            "products": [],
            "next": [],
        },
    )
    previous = store.get_node(node["id"])
    state = bench.update(
        identifier,
        {"budget": 5, "concurrency": 3, "estimate": 1, "timeout": 12, "notes": "优先完成必要引理，允许交接半成品。"},
    )
    revision = store.project()["config"]["steer_revision"]
    assert state["goal"] == "局部稳定性条件能否推广？"
    assert state["notes"]["text"].startswith("优先")
    assert state["settings"] == {
        "budget": 5,
        "concurrency": 3,
        "estimate": 1,
        "timeout": 12,
        "resources": [],
    }
    assert store.project()["config"]["coordinator_estimate"] == 1
    assert store.project()["config"]["other_setting"] == {"retained": True}
    assert store.get_node(node["id"]) == previous
    bench.update(identifier, {"notes": "继续"})
    assert store.project()["config"]["steer_revision"] > revision
    with pytest.raises(ValidationError):
        bench.update(identifier, {"goal": "Replace original"})
    before = store.snapshot()
    with pytest.raises(ValidationError):
        bench.update(identifier, {"notes": "Do not save this", "resources": ["/missing/resource"]})
    assert store.snapshot() == before
    with pytest.raises(NotFoundError):
        bench.update(identifier, {"budget": 50, "notes": "Check X-999/result#nonexistent"})
    assert store.snapshot() == before


def test_start_and_pause_before_first_hook_never_dispatch_or_claim_lock_early(tmp_path):
    factory = FakeFactory()
    factory.allow_first_hook.clear()
    bench, identifier, _ = fake_project(tmp_path, factory)
    state = bench.action(identifier, "start")
    assert not state["controller_running"]
    assert state["pending_action"] == "start"
    wait_for(lambda: factory.start_calls == 1)
    state = bench.action(identifier, "pause")
    assert state["pending_action"] == "pause"
    factory.allow_first_hook.set()
    final = wait_for(lambda: idle(bench, identifier))
    assert final["control"] == "paused"
    assert factory.ticks == 0
    bench.close()


def test_duplicate_clicks_and_controls_use_one_runtime_thread(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: bench.action(identifier, "start"), range(20)))
    wait_for(lambda: factory.ticks > 0)
    assert factory.start_calls == factory.max_active == 1
    bench.action(identifier, "reconcile")
    wait_for(lambda: factory.reconcile_calls == 1)
    bench.action(identifier, "stop")
    state = wait_for(lambda: idle(bench, identifier))
    assert state["control"] == "stopped"
    assert factory.stop_calls == 1
    assert len(factory.instances) == 1
    bench.close()


def test_resume_during_pause_draining_reuses_controller(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    factory.drain.clear()
    bench.action(identifier, "start")
    wait_for(lambda: factory.ticks > 0)
    bench.action(identifier, "pause")
    wait_for(lambda: bench.project_state(identifier)["control"] == "paused")
    before = factory.ticks
    bench.action(identifier, "resume")
    wait_for(lambda: factory.ticks > before)
    assert factory.start_calls == 1
    factory.drain.set()
    bench.close()
    wait_for(lambda: idle(bench, identifier))


def test_resume_after_last_hook_is_not_lost_at_thread_exit(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    factory.allow_exit.clear()
    bench.action(identifier, "start")
    wait_for(lambda: factory.ticks > 0)
    bench.action(identifier, "pause")
    assert factory.exit_reached.wait(3)
    bench.action(identifier, "resume")
    factory.allow_exit.set()
    wait_for(
        lambda: factory.start_calls == 2 and bench.project_state(identifier)["control"] == "running"
    )
    assert factory.max_active == 1
    bench.close()
    wait_for(lambda: idle(bench, identifier))


def test_external_coordinator_is_not_adopted_or_reconciled(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    store = bench.get_store(identifier)
    store.configure(control="running")
    with coordinator_lock(store.root):
        for action in ("start", "reconcile", "stop"):
            bench.action(identifier, action)
            state = wait_for(
                lambda: bench.project_state(identifier)
                if bench.project_state(identifier).get("error")
                else None
            )
            assert "其他协调器" in state["error"]
            assert state["controller_running"] is False
        bench.close()
        assert store.project()["config"]["control"] == "running"
    assert factory.start_calls == factory.reconcile_calls == factory.stop_calls == 0


def test_pause_external_controller_updates_only_control(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    store = bench.get_store(identifier)
    store.configure(control="running")
    with coordinator_lock(store.root):
        state = bench.action(identifier, "pause")
    assert state["control"] == "paused"
    assert factory.instances == []


def test_runtime_error_is_visible_and_retry_is_possible(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    factory.fail_once = True
    bench.action(identifier, "start")
    state = wait_for(
        lambda: bench.project_state(identifier)
        if bench.project_state(identifier).get("error")
        else None
    )
    assert state["control"] == "paused"
    assert not state["controller_running"]
    assert "Simulated runtime failure" in state["error"]
    bench.action(identifier, "resume")
    wait_for(lambda: factory.ticks > 0)
    assert "error" not in bench.project_state(identifier)
    bench.close()
    wait_for(lambda: idle(bench, identifier))


def test_close_requests_pause_but_keeps_draining_work_and_rejects_new_actions(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    factory.drain.clear()
    bench.action(identifier, "start")
    wait_for(lambda: factory.ticks > 0)
    bench.close()
    state = wait_for(
        lambda: bench.project_state(identifier)
        if bench.project_state(identifier)["control"] == "paused"
        else None
    )
    assert state["controller_running"] is True
    assert factory.stop_calls == 0
    assert bench.project_state(identifier)["goal"] == "局部稳定性条件能否推广？"
    with pytest.raises(ValidationError, match="closed"):
        bench.action(identifier, "resume")
    factory.drain.set()
    wait_for(lambda: idle(bench, identifier))
    bench.close()


def test_one_off_reconcile_never_starts_new_research(tmp_path):
    bench, identifier, factory = fake_project(tmp_path)
    bench.action(identifier, "reconcile")
    wait_for(lambda: idle(bench, identifier))
    assert factory.start_calls == factory.ticks == 0
    assert factory.reconcile_calls >= 1
    assert bench.get_store(identifier).attempts() == []


def test_relative_destination_is_under_workspace_and_resources_cannot_expose_control(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    bench = Workbench(workspace)
    with pytest.raises(ValidationError, match="上级目录"):
        bench.create(
            {
                "name": "Rejected",
                "goal": "Question",
                "path": "rejected",
                "resources": [str(workspace)],
            }
        )
    assert not (workspace / "rejected").exists()
    state = bench.create({"name": "Relative", "goal": "Question", "path": "nested/project"})
    root = (workspace / "nested" / "project").resolve()
    assert state["path"] == str(root)
    store = bench.get_store(state["id"])
    before = store.snapshot()
    for resource in (root, root.parent, root / ".research", root / ".research" / "state.sqlite3"):
        with pytest.raises(ValidationError):
            bench.update(state["id"], {"resources": [str(resource)], "notes": "Do not save"})
        assert store.snapshot() == before
    private = root / "workspaces" / "other-attempt"
    private.mkdir(parents=True)
    with pytest.raises(ValidationError, match="工作目录"):
        bench.update(state["id"], {"resources": [str(private)]})
    source = root / "source-data"
    source.mkdir()
    assert bench.update(state["id"], {"resources": [str(source)]})["settings"]["resources"] == [
        str(source)
    ]


def test_deleted_or_moved_project_is_not_recreated_by_read_or_start(tmp_path):
    bench, identifier, _ = fake_project(tmp_path)
    root = bench.get_store(identifier).root
    moved = tmp_path / "moved"
    root.rename(moved)
    for read in (
        lambda: bench.get_store(identifier),
        lambda: bench.project_state(identifier),
        lambda: bench.action(identifier, "start"),
    ):
        with pytest.raises(NotFoundError, match="已不存在"):
            read()
        assert not root.exists()
    listing = bench.list_projects()
    assert len(listing) == 1 and "已不存在" in listing[0]["error"]
    assert not root.exists()
    bench.open(moved)
    assert len(bench.list_projects()) == 2


def test_natural_pause_reason_is_visible_without_research_claim(tmp_path):
    bench, identifier, _ = fake_project(tmp_path)
    bench.get_store(identifier).configure(
        control="paused", idle_reason="Budget cannot reserve next call"
    )
    state = bench.project_state(identifier)
    assert state["idle_reason"] == "Budget cannot reserve next call"
    assert state["node_count"] == 0


def test_real_scripted_backend_runs_parallel_graph_from_gui(tmp_path):
    command = [sys.executable, str(EXAMPLES / "scripted_backend.py"), "{request}", "{output}"]
    bench = Workbench(tmp_path / "workspace", defaults={"backend_command": command})
    state = bench.create(
        {
            "name": "Scripted demo",
            "goal": "Demo only; no real model research",
            "budget": 100,
            "concurrency": 2,
            "estimate": 2,
            "timeout": 10,
        }
    )
    identifier = state["id"]
    try:
        bench.action(identifier, "start")

        def completed():
            state = bench.project_state(identifier)
            assert not state.get("error"), state.get("error")
            return (
                state
                if state["node_count"] == 5
                and state["active_attempts"] == 0
                and idle(bench, identifier)
                else None
            )

        final = wait_for(completed, timeout=30)
        store = bench.get_store(identifier)
        nodes = {n["why_now"]: n for n in store.list_nodes()}
        assert all(n["status"] == "closed" for n in nodes.values())
        workers = {a["node_id"]: a for a in store.attempts() if a["role"] == "worker"}
        states = {
            label: read_json(Path(workers[nodes[label]["id"]]["job_dir"]) / "status.json")
            for label in ("B", "C")
        }
        assert states["B"]["started_at"] < states["C"]["finished_at"]
        assert states["C"]["started_at"] < states["B"]["finished_at"]
        assert final["budget"]["spent"] == len(store.attempts()) == 11
        assert check(store.root)["ok"]
    finally:
        bench.close()


def test_real_stop_terminates_jobs_and_preserves_open_nodes(tmp_path):
    backend = tmp_path / "waiting_backend.py"
    backend.write_text("import time\ntime.sleep(30)\n")
    command = [sys.executable, str(backend), "{request}", "{output}"]
    bench = Workbench(tmp_path / "workspace", defaults={"backend_command": command})
    state = bench.create(
        {
            "name": "Stop demo",
            "goal": "Fake process only",
            "budget": 100,
            "concurrency": 2,
            "estimate": 2,
            "timeout": 40,
        }
    )
    identifier = state["id"]
    store = bench.get_store(identifier)
    for label in ("A", "B"):
        store.propose(
            {"question": "Q-001", "why_now": label, "plan": "Wait until stopped", "inputs": []}
        )
    try:
        bench.action(identifier, "start")

        def spawned():
            state = bench.project_state(identifier)
            assert not state.get("error"), state.get("error")
            attempts = store.attempts()
            return (
                len(attempts) == 2
                and all(
                    read_json(Path(a["job_dir"]) / "status.json", {}).get("child_pid")
                    for a in attempts
                    if a.get("job_dir")
                )
                and all(a.get("job_dir") for a in attempts)
            )

        wait_for(spawned)
        bench.action(identifier, "stop")
        state = wait_for(lambda: idle(bench, identifier))
        assert state["control"] == "stopped"
        assert state["active_attempts"] == 0
        assert all(n["status"] == "open" for n in store.list_nodes())
        assert all(a["state"] == "interrupted" for a in store.attempts())
        assert len(store.attempts()) == 2
    finally:
        if bench.project_state(identifier)["active_attempts"]:
            bench.action(identifier, "stop")
            wait_for(lambda: idle(bench, identifier))
        bench.close()
