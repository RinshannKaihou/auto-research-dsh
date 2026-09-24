import base64
import json

import pytest

from auto_research.errors import ConflictError, NotFoundError, ValidationError
from auto_research.memory_store import parse_knowledge_ref
from auto_research.native_store import NativeStore
from auto_research.service import NativeService


def make_store(tmp_path):
    store = NativeStore(tmp_path)
    store.initialize("条件化在线检测", "init")
    store.associate("host", "main", "associate")
    store.workflow(
        "host", "main", "register", {"role": "main", "cwd": str(tmp_path)}, "role"
    )
    return store


def call(service, method, operation, session="main", **fields):
    return service.handle(
        {
            "transport_id": operation,
            "operation_id": operation,
            "host_id": "host",
            "session_id": session,
            "method": method,
            **fields,
        }
    )["value"]


def test_memory_validation_conflicts_and_reference_types(tmp_path):
    store = make_store(tmp_path)
    assert store.guidance_status() is None
    with pytest.raises(ValidationError):
        parse_knowledge_ref("knowledge/missing")
    with pytest.raises(ValidationError):
        store.record_knowledge({"kind": "bad", "statement": "x"}, "bad-kind")
    with pytest.raises(ValidationError):
        store.record_knowledge(
            {"kind": "hypothesis", "statement": "x", "status": "bad"}, "bad-status"
        )
    with pytest.raises(ValidationError):
        store.record_knowledge(
            {"kind": "hypothesis", "statement": "x", "dependencies": [1]}, "bad-deps"
        )
    with pytest.raises(NotFoundError):
        store.record_knowledge(
            {"kind": "hypothesis", "statement": "x", "node_id": "X-404"}, "bad-node"
        )

    node = store.propose("两字误报如何约束？", "now", "test", "node")
    item = store.record_knowledge(
        {
            "kind": "hypothesis",
            "statement": "误报与长度相关",
            "node_id": node["node_id"],
            "conditions": {"dataset": "v1"},
        },
        "item",
    )
    with pytest.raises(ValidationError):
        store.revise_knowledge(
            {"ref": item["ref"], "changes": {"unknown": 1}, "reason": "bad"}, "bad-change"
        )
    with pytest.raises(ValidationError):
        store.revise_knowledge(
            {"ref": item["ref"], "changes": {"status": "bad"}, "reason": "bad"},
            "bad-revision-state",
        )
    with pytest.raises(ValidationError):
        store.knowledge_query(conditions=["not", "a", "mapping"])
    with pytest.raises(ValidationError):
        store.knowledge_query(conditions={"bad key!": "x"})

    checkpoint = store.checkpoint(
        {"node_id": node["node_id"], "state": {"next": "measure"}}, "checkpoint"
    )
    with pytest.raises(ConflictError):
        store.checkpoint(
            {
                "node_id": node["node_id"],
                "state": {"next": "overwrite"},
                "expected_revision": checkpoint["revision"] - 1,
            },
            "checkpoint-conflict",
        )
    with pytest.raises(NotFoundError):
        store.checkpoint({"node_id": "X-404", "state": {}}, "checkpoint-missing")
    with pytest.raises(ValidationError):
        store.review_todo_state("missing", "invalid", "todo-state-invalid")
    with pytest.raises(NotFoundError):
        store.review_todo_state("missing", "completed", "todo-missing")


def test_specialist_limits_lineage_and_terminal_validation(tmp_path):
    store = make_store(tmp_path)
    node = store.propose("Q", "now", "plan", "node")
    with pytest.raises(ValidationError):
        store.specialist_create(
            {
                "parent_session_id": "main",
                "purpose": "invented",
                "label": "bad",
                "prompt": "bad",
            },
            "purpose",
        )
    with pytest.raises(NotFoundError):
        store.specialist_create(
            {
                "parent_session_id": "main",
                "node_id": "X-404",
                "purpose": "review",
                "label": "bad",
                "prompt": "bad",
            },
            "missing-specialist-node",
        )
    with pytest.raises(ValidationError):
        store.specialist_create(
            {
                "parent_session_id": "main",
                "node_id": node["node_id"],
                "purpose": "review",
                "label": "bad",
                "prompt": "bad",
                "fanout_limit": 0,
            },
            "fanout",
        )
    first = store.specialist_create(
        {
            "parent_session_id": "main",
            "node_id": node["node_id"],
            "purpose": "review",
            "label": "review",
            "prompt": "check",
            "fanout_limit": 1,
        },
        "specialist",
    )
    with pytest.raises(ConflictError):
        store.specialist_create(
            {
                "parent_session_id": "main",
                "node_id": node["node_id"],
                "purpose": "review",
                "label": "second",
                "prompt": "check",
                "fanout_limit": 1,
            },
            "specialist-two",
        )
    with pytest.raises(ConflictError):
        store.specialist_bind(
            {"task_id": first["task_id"], "session_id": "child", "parent_session_id": "other"},
            "wrong-parent",
        )
    store.specialist_bind(
        {"task_id": first["task_id"], "session_id": "child", "parent_session_id": "main"},
        "bind",
    )
    with pytest.raises(NotFoundError):
        store.specialist_get("S-missing")
    with pytest.raises(ValidationError):
        store.specialist_finish({"task_id": first["task_id"], "state": "bad"}, "finish-bad")
    with pytest.raises(ConflictError):
        store.specialist_finish(
            {"task_id": first["task_id"], "parent_session_id": "other"}, "finish-parent"
        )


def test_query_pages_decode_every_new_collection_and_validate_cursor(tmp_path):
    store = make_store(tmp_path)
    node = store.propose("Q", "now", "plan", "node")
    store.focus("host", "main", node["node_id"], "planner", "manual", "focus")
    checkpoint = store.checkpoint({"node_id": node["node_id"], "state": {"x": 1}}, "cp")
    store.revise_knowledge(
        {
            "ref": node["question_ref"],
            "expected_revision": 1,
            "changes": {"statement": "Q revised"},
            "reason": "exercise review queue",
            "affected_scope_mode": "none",
        },
        "revise-question",
    )
    specialist = store.specialist_create(
        {
            "parent_session_id": "main",
            "node_id": node["node_id"],
            "purpose": "review",
            "label": "review",
            "prompt": "check",
            "inputs": [node["question_ref"]],
        },
        "specialist",
    )
    for collection in ("sessions", "knowledge", "checkpoints", "review_todos", "specialists"):
        page = store.history_page(collection)
        assert page["collection"] == collection
        assert page["items"]
    assert store.reference_query(checkpoint["checkpoint_id"])["kind"] == "checkpoint"
    assert store.reference_query(specialist["task_id"])["kind"] == "specialist-task"
    with pytest.raises(ValidationError):
        store.history_page("missing")
    with pytest.raises(ValidationError):
        store.history_page("nodes", project_id="different")
    with pytest.raises(ValidationError):
        store.history_page("nodes", order="descending")


def test_service_routes_memory_pages_guidance_and_chunks(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")
    root = tmp_path / "project"
    call(service, "open", "open", root=str(root), goal="Goal")
    node = call(service, "propose", "node", question="误报问题", why_now="now", plan="plan")
    recorded = call(
        service,
        "memory_write",
        "memory-record",
        action="record",
        fields={"kind": "hypothesis", "statement": "误报条件", "node_id": node["node_id"]},
    )
    revised = call(
        service,
        "memory_write",
        "memory-revise",
        action="revise",
        fields={
            "ref": recorded["ref"],
            "expected_revision": 1,
            "changes": {"status": "disputed"},
            "reason": "counterexample",
            "affected_scope_mode": "versions",
            "affected_scope": [recorded["ref"]],
        },
    )
    call(
        service,
        "memory_write",
        "memory-checkpoint",
        action="checkpoint",
        fields={"node_id": node["node_id"], "state": {"next": "test"}},
    )
    assert call(service, "query", "query-ref", ref=revised["ref"])["kind"] == "knowledge"
    assert call(service, "query", "query-search", query="误报")["items"]
    assert call(service, "query", "query-history", collection="nodes")["items"]
    assert call(service, "query", "query-usage", collection="usage")["collection"] == "usage"
    assert call(service, "query", "query-changes", collection="changes")["collection"] == "changes"
    assert call(service, "query", "query-summary")["project"]["goal"] == "Goal"
    assert call(service, "status", "status")["project"]["goal"] == "Goal"
    assert call(service, "history_page", "history", collection="knowledge")["items"]
    assert call(service, "usage_page", "usage-page")["collection"] == "usage"
    assert call(service, "changes_page", "changes")["items"]
    context = call(service, "memory_context", "context")
    manifest = call(
        service,
        "context_record",
        "context-record",
        fields={"body": context["text"], "source_sequence": context["source_sequence"]},
    )
    assert manifest["context_id"]
    chunk = call(service, "query", "query-chunk", ref=revised["ref"], offset=0, limit=16)
    assert base64.b64decode(chunk["chunk"])
    guide = tmp_path / "guide.md"
    guide.write_text("# 误报\n保留负结果。", encoding="utf-8")
    assert call(service, "guidance_register", "guidance", path=str(guide))["active"] is True
    assert call(service, "guidance_status", "guidance-status")["section_index"]
    assert call(service, "project_sessions", "sessions")
    todo = call(service, "review_todo_next", "review-next", node_id=node["node_id"])
    assert call(
        service,
        "review_todo_state",
        "review-done",
        todo_id=todo["todo_id"],
        state="completed",
    )["state"] == "completed"
    relation = call(
        service,
        "relate",
        "relation",
        source_ref=node["node_id"],
        target_ref=revised["ref"],
        label="tests",
        note="routing coverage",
    )
    assert relation["label"] == "tests"
    assert call(service, "close_node", "close-node", node_id=node["node_id"])["status"] == "closed"
    with pytest.raises(ValueError):
        call(service, "memory_write", "bad-action", action="bad", fields={})
    with pytest.raises(ValueError):
        service.handle({"transport_id": "bad", "method": "project_sessions", "host_id": "", "session_id": ""})
