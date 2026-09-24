import json

import pytest

from auto_research.errors import ValidationError
from test_memory_store import project
from test_workflow_store import fixture, request


def blocks(view):
    return {
        part.split("\n", 1)[0]: json.loads(part.split("\n", 1)[1])
        for part in view["text"].split("## ")[1:]
    }


def test_main_keeps_independent_agenda_corrections_and_cross_node_lessons(tmp_path):
    store = project(tmp_path)
    store.focus("host", "main", None, "planner", "manual", "focus")
    note = store.note("host", "main", "Source evidence", "condition", "note")
    lessons = []
    for i in range(30):
        node = store.propose(f"OPEN_QUESTION_{i}", "now", "plan", f"node-{i}")
        lessons.append(
            store.record_knowledge(
                {
                    "kind": "lesson",
                    "node_id": node["node_id"],
                    "statement": f"LESSON_{i}",
                    "conditions": {"dataset": f"v{i}"},
                    "evidence_refs": [note["note_id"]],
                },
                f"lesson-{i}",
            )
        )
    disputed = store.revise_knowledge(
        {
            "ref": lessons[0]["ref"],
            "expected_revision": 1,
            "changes": {"status": "disputed", "statement": "EARLY_DISPUTE"},
            "reason": "conflict",
            "affected_scope_mode": "versions",
            "affected_scope": [lessons[0]["ref"]],
        },
        "dispute",
    )
    revised = store.revise_knowledge(
        {
            "ref": lessons[1]["ref"],
            "expected_revision": 1,
            "changes": {"statement": "EARLY_CORRECTION"},
            "reason": "correction",
            "affected_scope_mode": "versions",
            "affected_scope": [lessons[1]["ref"]],
        },
        "correction",
    )
    first = store.context_view("host", "main")
    text = first["text"]
    assert "LESSON_29" in text and "OPEN_QUESTION_29" in text
    assert "EARLY_DISPUTE" in text and "EARLY_CORRECTION" in text
    assert "v29" in text and note["note_id"] in text
    assert disputed["ref"] in first["dependencies"] and revised["ref"] in first["dependencies"]
    assert {"knowledge", "questions"} <= blocks(first).keys()
    assert len(text) <= 12000
    store.begin_usage("usage", "usage-start", host_id="host", session_id="main")
    assert store.context_view("host", "main")["source_digest"] == first["source_digest"]


def test_memory_defaults_to_attempt_node_and_retries_keep_original_placement(tmp_path):
    service, root, store = fixture(tmp_path)
    a = store.propose("A", "now", "plan", "a")
    b = store.propose("B", "now", "plan", "b")
    request(service, "focus", node_id=a["node_id"], mode="manual", role="researcher", op="focus-a")
    fields = {"kind": "hypothesis", "statement": "testable"}
    saved = request(service, "memory_write", action="record", fields=fields, op="memory")
    assert saved["node_id"] == a["node_id"]
    legacy_fields = {**fields, "source_identity": {"host_id": "host", "session_id": "main"}}
    legacy = store.record_knowledge(legacy_fields, "legacy-memory")
    assert legacy["node_id"] is None
    assert (
        request(service, "memory_write", action="record", fields=fields, op="legacy-memory")
        == legacy
    )
    request(service, "finish", state="finished", op="finish-a")
    request(service, "focus", node_id=b["node_id"], mode="manual", role="researcher", op="focus-b")
    assert request(service, "memory_write", action="record", fields=fields, op="memory") == saved
    global_item = request(
        service,
        "memory_write",
        action="record",
        fields={**fields, "visibility": "project"},
        op="global",
    )
    assert global_item["node_id"] is None
    with pytest.raises(ValidationError, match="visibility"):
        request(
            service,
            "memory_write",
            action="record",
            fields={**fields, "visibility": "project", "node_id": a["node_id"]},
            op="conflict",
        )
    with pytest.raises(ValidationError, match="visibility"):
        request(
            service,
            "memory_write",
            action="record",
            fields={**fields, "visibility": "invalid"},
            op="invalid",
        )


def test_planning_without_node_requires_explicit_project_visibility(tmp_path):
    service, root, store = fixture(tmp_path)
    fields = {"kind": "hypothesis", "statement": "project-wide assumption"}
    with pytest.raises(ValidationError, match="visibility"):
        request(service, "memory_write", action="record", fields=fields, op="ambiguous")
    saved = request(
        service,
        "memory_write",
        action="record",
        fields={**fields, "visibility": "project"},
        op="explicit",
    )
    assert saved["node_id"] is None
