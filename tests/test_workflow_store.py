import json
import sqlite3
from pathlib import Path

import pytest

from auto_research.native_store import NativeStore
from auto_research.service import NativeService
from auto_research.workflow_store import migrate_schema3
from auto_research.errors import ConflictError, ValidationError, NotFoundError


def request(service, method, sid="main", op=None, **fields):
    return service.handle(
        {
            "transport_id": "transport",
            "operation_id": op or method + sid,
            "host_id": "host",
            "session_id": sid,
            "method": method,
            **fields,
        }
    )["value"]


def fixture(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")
    root = tmp_path / "project"
    request(service, "open", root=str(root), goal="Goal")
    return service, root, NativeStore(root)


def test_schema2_upgrade_backup_roles_and_idempotent_totals(tmp_path):
    service, root, store = fixture(tmp_path)
    request(service, "focus", mode="auto", role="planner")
    request(service, "own_goal", goal_id="ours", revision=3, phase="paused")
    for i, amount in enumerate([13070, 524659]):
        request(service, "usage_begin", source_key=f"usage{i}", op=f"begin{i}")
        request(
            service,
            "usage_finish",
            source_key=f"usage{i}",
            amount=amount,
            completeness="actual",
            op=f"finish{i}",
        )
    request(service, "open", sid="branch", root=str(root))
    request(service, "focus", sid="branch", mode="auto", role="branch")
    request(service, "open", sid="unknown", root=str(root))
    before = store.query()
    with sqlite3.connect(store.db_path) as db:
        for name in [
            "workflow_sessions",
            "workflow_project",
            "exploration_tasks",
            "workflow_notifications",
            "workflow_intents",
            "snapshot_handoffs",
            "projection_cursors",
        ]:
            db.execute("DROP TABLE " + name)
        db.execute("PRAGMA user_version=2")
    upgraded = NativeStore(root)
    state = upgraded.query("host", "main")
    assert state["schema_version"] == 9
    assert {s["session_id"]: s["role"] for s in state["workflow"]["sessions"]} == {
        "main": "main",
        "branch": "exploration",
        "unknown": "legacy",
    }
    assert state["workflow"]["session"]["goal_id"] == "ours"
    assert state["workflow"]["run"]["state"] == "cold"
    assert state["attempts"] == before["attempts"]
    assert state["usage"]["known"] == 537729
    with sqlite3.connect(root / ".research/schema-2-backup.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT SUM(amount) FROM usage_observations").fetchone()[0] == 537729
    assert NativeStore(root).query()["usage_observations"] == state["usage_observations"]


def test_roles_guards_and_queue_state_machine(tmp_path):
    service, root, store = fixture(tmp_path)

    def wf(action, fields={}, op=None, sid="main"):
        return request(
            service,
            "workflow",
            sid=sid,
            action=action,
            fields=fields,
            op=op or action + json.dumps(fields),
        )

    with pytest.raises(ValidationError):
        wf("register", {"role": "alien"}, sid="main")
    with pytest.raises(ValidationError):
        wf("run", {"state": "alien"})
    with pytest.raises(ValidationError):
        wf("session", {"role": "exploration"})
    wf("run", {"state": "running", "new_generation": True})
    node = request(service, "propose", question="Node?", why_now="Now", plan="Plan")
    task = wf("task", {"node_id": node["node_id"]}, "dispatch")
    assert wf("task", {"node_id": node["node_id"]}, "dispatch")["task_id"] == task["task_id"]
    assert wf("task", {"node_id": node["node_id"]}, "different")["task_id"] == task["task_id"]
    with pytest.raises(ConflictError):
        wf("task", {"node_id": "missing"})
    with pytest.raises(NotFoundError):
        wf("task_state", {"task_id": "missing", "state": "running"})
    with pytest.raises(ValidationError):
        wf("task_state", {"task_id": task["task_id"], "state": "alien"})
    request(
        service,
        "open",
        sid=task["session_id"],
        root=str(root),
        session_role="exploration",
        node_id=node["node_id"],
    )
    with pytest.raises(ConflictError):
        wf("task", {"node_id": node["node_id"]}, sid=task["session_id"])
    wf("task_state", {"task_id": task["task_id"], "state": "running", "cwd": str(root)})
    notice = wf(
        "notify",
        {"key": "pub", "kind": "published", "reference": "pub/P-1", "gaps": ["gap"]},
        sid=task["session_id"],
    )["notifications"][0]
    assert (
        len(
            wf(
                "notify",
                {"key": "pub", "kind": "published", "reference": "pub/P-1", "gaps": ["gap"]},
                sid=task["session_id"],
            )["notifications"]
        )
        == 1
    )
    wf("notification_state", {"notification_id": notice["notification_id"], "state": "claimed"})
    with pytest.raises(ValidationError):
        wf("notification_state", {"notification_id": notice["notification_id"], "state": "bad"})
    wf("intent", {"intent_id": "stop", "kind": "stop", "state": "requested"})
    wf("session", {"waiting": [task["task_id"]], "pause_reason": "wait"})
    request(service, "usage_begin", source_key="interrupted")
    wf("cold")
    state = store.query("host", "main")
    assert state["workflow"]["session"]["pause_reason"] == "cold"
    assert state["usage"]["missing"] == 1
    wf("run", {"state": "stopping"})
    with pytest.raises(ConflictError):
        wf("task", {"node_id": node["node_id"]}, "paused-dispatch")
    with pytest.raises(ValidationError):
        wf("unknown")
    assert wf("notify", {"key": "no-task", "kind": "progress"}) == {"ignored": True}


def test_discussion_frozen_files_and_read_only_preview(tmp_path):
    service, root, store = fixture(tmp_path)
    node = request(service, "propose", question="N?", why_now="Now", plan="Plan")
    request(service, "focus", node_id=node["node_id"])
    (root / "draft.txt").write_text("v1")
    first = request(
        service,
        "publish",
        summary="v1",
        items=[{"item_id": "draft", "source_path": "draft.txt"}],
        op="p1",
    )
    (root / "draft.txt").write_text("v2")
    request(
        service,
        "publish",
        summary="v2",
        items=[{"item_id": "draft", "source_path": "draft.txt"}],
        op="p2",
    )
    prepared = request(service, "discussion_prepare", node_id=node["node_id"])
    assert [
        Path(prepared["workspace"], i["path"]).read_text()
        for i in prepared["context"]["material_index"]
    ] == ["v1", "v2"]
    request(
        service,
        "open",
        sid=prepared["session_id"],
        root=str(root),
        session_role="discussion",
        node_id=node["node_id"],
        cwd=prepared["workspace"],
        context=prepared["context"],
    )
    assert request(service, "discussion_prepare", node_id=node["node_id"], op="repeat")["existing"]
    fresh = request(service, "discussion_prepare", node_id=node["node_id"], fresh=True, op="fresh")
    assert fresh["session_id"] != prepared["session_id"]
    for method in [
        "focus",
        "note",
        "publish",
        "snapshot",
        "finish",
        "close_node",
        "relate",
        "propose",
    ]:
        with pytest.raises(ValueError, match="Discussion"):
            request(service, method, sid=prepared["session_id"], model_call=True)
    with pytest.raises(ValueError, match="host controller"):
        request(service, "workflow", model_call=True, action="session", fields={"role": "main"})
    snap = request(service, "snapshot", paths=["draft.txt"])
    request(service, "note", body="later note", op="later")
    before = {p for p in root.rglob("*") if not p.name.endswith(("-wal", "-shm"))}
    preview = request(service, "restore_preview", snapshot_id=snap["snapshot_id"])
    assert {p for p in root.rglob("*") if not p.name.endswith(("-wal", "-shm"))} == before
    assert not preview["eligible"]
    assert preview["context"]["notes"] == []
    assert preview["context"]["provenance"] == "captured-at-snapshot"
    with pytest.raises(ValueError):
        request(service, "prepare_restore", snapshot_id=snap["snapshot_id"], preview_id="bad")
    for path in ["/tmp/file", "../file", ".git/HEAD", ".env"]:
        with pytest.raises(ValueError):
            service._safe_source(path)


def test_frozen_turn_usage_after_attempt_closed(tmp_path):
    service, root, store = fixture(tmp_path)
    a = request(service, "focus")
    request(service, "bind_turn", turn=10)
    request(service, "finish", state="finished")
    request(service, "focus", op="new-focus")
    value = request(service, "usage_begin", source_key="late-start", turn=10)
    assert value["attempt_id"] == a["attempt_id"]
    request(service, "usage_finish", source_key="late-start", amount=3, completeness="estimated")
    assert store.query()["usage"]["estimated"] == 3
    request(
        service,
        "host_events",
        events=[{"event_type": "turn/end", "sequence": 9, "facts": {"turn": 10}}],
        cursor=9,
    )
    request(
        service,
        "host_events",
        events=[{"event_type": "turn/end", "sequence": 9, "facts": {"turn": 10}}],
        cursor=9,
        op="replay",
    )
    with store._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM events WHERE kind='host.event'").fetchone()[0] == 1
        assert db.execute("SELECT sequence FROM projection_cursors").fetchone()[0] == 9


def test_native_usage_reconciliation_requires_unique_turn_and_step(tmp_path):
    service, root, store = fixture(tmp_path)
    request(service, "usage_begin", source_key="one", turn=4, step=2)
    request(service, "usage_finish", source_key="one", amount=None, completeness="unknown")
    event = {
        "event_type": "assistant/message",
        "sequence": 21,
        "facts": {"turn": 4, "step": 2, "usage": {"inputTokens": 10, "outputTokens": 2}},
    }
    request(service, "host_events", events=[event], cursor=21, op="events")
    request(service, "host_events", events=[event], cursor=21, op="replay")
    assert store.query()["usage"]["known"] == 12
    for i in range(2):
        request(service, "usage_begin", source_key=f"ambiguous{i}", turn=4, step=3, op=f"begin{i}")
    event["sequence"] = 22
    event["facts"]["step"] = 3
    request(service, "host_events", events=[event], cursor=22, op="ambiguous")
    assert store.query()["usage"]["known"] == 12
    assert sum(x["amount"] is None for x in store.query()["usage_observations"]) == 2
    before = store.db_path.read_bytes()
    assert NativeStore(root, readonly=True).query()["usage"]["known"] == 12
    assert store.db_path.read_bytes() == before
    with pytest.raises(sqlite3.OperationalError):
        NativeStore(root, readonly=True).initialize("no write", "readonly")
