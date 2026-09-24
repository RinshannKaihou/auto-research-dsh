import json
import pytest

from test_memory_store import project
from test_workflow_store import fixture, request
from auto_research.errors import ConflictError


def test_sixty_nodes_keep_input_lesson_and_long_task(tmp_path):
    store = project(tmp_path)
    first = store.propose("Source question", "now", "plan", "source")
    store.focus("host", "main", first["node_id"], "researcher", "manual", "focus")
    note = store.note("host", "main", "Negative result on short traces", "condition", "note")
    lesson = store.record_knowledge(
        {
            "kind": "lesson",
            "statement": "EARLY_NEGATIVE: short traces violate FPR",
            "conditions": {"length": "<10"},
            "node_id": first["node_id"],
            "evidence_refs": [note["note_id"]],
        },
        "lesson",
    )
    pub = store.publish_metadata(
        "host", "main", "partial", "result", [], [], "pub", [lesson["ref"]]
    )
    store.finish("host", "main", "finished", {}, "finish")
    for i in range(60):
        store.propose(f"Unrelated {i}", "now", "plan", f"unrelated-{i}")
    target = store.propose(
        "CURRENT_QUESTION",
        "now",
        "LONG_PLAN " * 2000,
        "target",
        inputs=[f"pub/{pub['publication_id']}"],
    )
    store.workflow("host", "main", "run", {"state": "running"}, "run")
    task = store.workflow("host", "main", "task", {"node_id": target["node_id"]}, "dispatch")
    store.associate("host", task["session_id"], "assoc-child")
    store.workflow(
        "host",
        task["session_id"],
        "register",
        {
            "role": "exploration",
            "node_id": target["node_id"],
            "cwd": str(tmp_path),
            "context": task["context"],
        },
        "child",
    )
    view = store.context_view("host", task["session_id"])
    assert len(view["text"]) <= 12000
    assert "## task\n" in view["text"]
    assert "CURRENT_QUESTION" in view["text"]
    assert "EARLY_NEGATIVE" in view["text"]
    assert "length" in view["text"]
    assert lesson["ref"] in view["dependencies"]
    assert "Unrelated" not in view["text"]


def test_control_does_not_load_two_thousand_historical_session_contexts(tmp_path):
    store = project(tmp_path)
    with store._connection() as db:
        db.executemany(
                "INSERT INTO workflow_sessions "
                "(session_id,host_id,role,node_id,cwd,pause_reason,waiting,context,goal_id,goal_revision,detached,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"old-{i}",
                    "host",
                    "exploration",
                    None,
                    "/tmp/old",
                    "finished",
                    "[]",
                    json.dumps({"plan": "x" * 2400}),
                    None,
                    None,
                    0,
                    "old",
                )
                for i in range(2000)
            ],
        )
    state = store.control_state("host", "main")
    assert len(json.dumps(state)) < 20000
    assert [s["session_id"] for s in state["workflow"]["sessions"]] == ["main"]
    assert "context" not in state["workflow"]["sessions"][0]
    assert len(store.history_page("sessions")["items"]) == 50


def test_control_pages_cover_all_active_sessions_without_task_bodies(tmp_path):
    service, root, store = fixture(tmp_path)
    with store._connection() as db:
        db.executemany(
                "INSERT INTO workflow_sessions "
                "(session_id,host_id,role,node_id,cwd,pause_reason,waiting,context,goal_id,goal_revision,detached,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                (
                    f"active-{i}",
                    "host",
                    "exploration",
                    None,
                    "/tmp/active",
                    "project",
                    "[]",
                    json.dumps({"plan": "x" * 20000}),
                    None,
                    None,
                    0,
                    "old",
                )
                for i in range(205)
            ],
        )
    view = request(service, "control_state")
    assert len(json.dumps(view)) < 60000
    seen = {s["session_id"] for s in view["workflow"]["sessions"]}
    cursor = view["workflow"]["cursors"]
    while cursor.get("sessions"):
        page = request(service, "workflow_page", op=f"page-{len(seen)}", cursors=cursor)
        seen.update(s["session_id"] for s in page["sessions"])
        cursor = page["cursors"]
    assert len(seen) == 206


def test_task_recovery_receipts_and_atomic_attempt_binding(tmp_path):
    service, root, store = fixture(tmp_path)
    request(service, "workflow", action="run", fields={"state": "running"}, op="run")
    node = request(service, "propose", question="Task Q", why_now="now", plan="long " * 4000)
    task = request(
        service, "workflow", action="task", fields={"node_id": node["node_id"]}, op="task"
    )
    context = request(service, "task_context", task_id=task["task_id"])
    assert context["question"] == "Task Q"
    assert len(json.dumps(context)) < 6000
    assert request(service, "task_creation_clear", task_id=task["task_id"])["clear"]
    request(
        service,
        "workflow",
        action="task_state",
        fields={"task_id": task["task_id"], "state": "unverified", "error": "creation failed"},
        op="failed",
    )
    request(
        service,
        "workflow",
        action="task_retry",
        fields={"task_id": task["task_id"], "absent_verified": True},
        op="requeue",
    )
    request(
        service,
        "open",
        sid=task["session_id"],
        root=str(root),
        session_role="exploration",
        node_id=node["node_id"],
    )
    assert not request(service, "task_creation_clear", task_id=task["task_id"])["clear"]
    attempt = request(
        service,
        "focus",
        sid=task["session_id"],
        node_id=node["node_id"],
        role="branch",
        mode="auto",
    )
    request(
        service,
        "workflow",
        sid=task["session_id"],
        action="task_resumed",
        fields={
            "task_id": task["task_id"],
            "attempt_id": attempt["attempt_id"],
            "retry_of": "A-previous",
        },
        op="bind",
    )
    assert (
        request(service, "query", sid=task["session_id"], ref=attempt["attempt_id"])["value"][
            "details"
        ]["retry_of"]
        == "A-previous"
    )
    assert request(service, "recovery_receipt", key="retry-one") is None
    request(
        service,
        "workflow",
        action="intent",
        fields={
            "intent_id": "retry-one",
            "kind": "recovery-receipt",
            "state": "complete",
            "details": {"attempt_id": attempt["attempt_id"]},
        },
        op="receipt",
    )
    assert (
        request(service, "recovery_receipt", key="retry-one")["attempt_id"] == attempt["attempt_id"]
    )
    with pytest.raises(ConflictError):
        request(
            service,
            "workflow",
            action="task_retry",
            fields={"task_id": task["task_id"]},
            op="invalid-retry",
        )
    with pytest.raises(ValueError):
        request(service, "task_context", task_id=node["node_id"])
    with pytest.raises(ValueError):
        request(service, "task_creation_clear", task_id="missing")


def test_redispatch_of_failed_node_keeps_one_task_identity_then_explicit_retry(tmp_path):
    service, root, store = fixture(tmp_path)
    node = store.propose("Q", "now", "plan", "node")
    store.workflow("host", "main", "run", {"state": "running"}, "run")
    first = store.workflow("host", "main", "task", {"node_id": node["node_id"]}, "first")
    store.workflow(
        "host", "main", "task_state", {"task_id": first["task_id"], "state": "failed"}, "fail"
    )
    second = store.workflow("host", "main", "task", {"node_id": node["node_id"]}, "second")
    assert second["task_id"] == first["task_id"]
    assert second["session_id"] == first["session_id"]
    assert second["state"] == "failed"
    store.workflow(
        "host", "main", "task_retry", {"task_id": first["task_id"]}, "retry"
    )
    assert store.reference_query(first["task_id"])["value"]["state"] == "starting"
