"""0.6.10 workbench reads must stay bounded, exact, and ledger-neutral."""
from __future__ import annotations

import base64
import hashlib
import json

from auto_research import workbench_read
from auto_research.native_store import NativeStore
from auto_research.service import NativeService


def initialized(tmp_path):
    root = tmp_path / "research"
    store = NativeStore(root)
    project = store.initialize("An intentionally long scientific goal", "init")
    store.associate("host", "main", "associate")
    store.workflow("host", "main", "register", {"role": "main", "cwd": str(root)}, "role")
    return store, root, project


def service_call(service, method, operation, **fields):
    result = service.handle({"transport_id": operation, "operation_id": operation,
                             "host_id": "host", "session_id": "main", "method": method, **fields})
    assert result["ok"], result
    return result["value"]


def published(tmp_path, *, items):
    root = tmp_path / "research"
    service = NativeService(tmp_path / "registry.sqlite3")
    project = service_call(service, "open", "open", root=str(root), goal="Fixture")
    store = NativeStore(root)
    node = store.propose("Question?", "why", "plan", "node")
    service_call(service, "focus", "focus", node_id=node["node_id"], role="researcher", mode="manual")
    publication = service_call(service, "publish", "publish", status="partial",
                               summary="Documented outcome", gaps=[], items=items)
    return service, store, root, project, node, publication


def test_latest_knowledge_search_and_compact_page(tmp_path):
    store, _, _ = initialized(tmp_path)
    node = store.propose("Question?", "why", "A very long plan " * 1000, "node")
    first = store.record_knowledge({"kind": "open_question", "statement": "old answer"}, "k1")
    store.revise_knowledge({"ref": first["ref"], "reason": "new result",
                            "affected_scope_mode": "none", "change_kind": "reword",
                            "changes": {"statement": "new answer", "status": "working"}}, "k2")
    store.record_knowledge({"kind": "open_question", "statement": "mentions K-002"}, "k3")

    page = workbench_read.page(store, "nodes")
    assert page["total"] == 1
    assert page["items"][0]["node_id"] == node["node_id"]
    assert "plan" not in page["items"][0]
    assert len(json.dumps(page)) < 1200

    latest = workbench_read.knowledge_page(store, query="K-002")
    assert latest["total"] == 1
    assert latest["items"][0]["ref"] == "knowledge/K-002@2"
    history = workbench_read.knowledge_page(store, knowledge_id="K-002", history=True)
    assert [item["ref"] for item in history["items"]] == ["knowledge/K-002@1", "knowledge/K-002@2"]
    assert "knowledge/K-002@2" in [item["ref"] for item in workbench_read.knowledge_page(store, status="working")["items"]]


def test_presentation_reads_verified_frozen_values_without_ledger_write(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    (root / "REPORT.md").write_text("# Final report\nOne result.\n")
    (root / "metrics.json").write_text(json.dumps({"split": "audit", "score": 0.8}))
    service, store, _, project, node, _ = published(
        tmp_path, items=[{"item_id": "report", "kind": "report", "source_path": "REPORT.md"},
                         {"item_id": "metrics", "kind": "result", "source_path": "metrics.json"}]
    )
    ref = "pub/P-001#metrics"
    report_ref = "pub/P-001#report"
    document = {
        "schema": "research-presentation/v1", "project_id": project["project"]["project_id"],
        "source_publication_id": "P-001", "title": "Fixture", "summary": "A measured outcome",
        "summary_ref": report_ref, "report_ref": report_ref,
        "featured_node_id": node["node_id"], "nodes": {}, "deliverables": [],
        "metrics": [{"id": "score", "label": "Score", "split": "audit",
                     "baseline": {"ref": ref, "pointer": "/score"},
                     "current": {"ref": ref, "pointer": "/score"}}],
    }
    (root / "research.presentation.json").write_text(json.dumps(document))
    before = hashlib.sha256(store.db_path.read_bytes()).hexdigest()

    assert workbench_read.presentation(store, root)["value"]["metrics"][0]["current"]["value"] == 0.8
    first = workbench_read.reference_content(store, root, report_ref, limit=7)
    second = workbench_read.reference_content(store, root, report_ref,
                                              offset=first["next_offset"], limit=32768)
    assert base64.b64decode(first["chunk"] + "===") + base64.b64decode(second["chunk"] + "===") == b"# Final report\nOne result.\n"
    assert second["resolution"]["integrity"] == "verified_cached"
    assert hashlib.sha256(store.db_path.read_bytes()).hexdigest() == before

    document["project_id"] = "another-project"
    (root / "research.presentation.json").write_text(json.dumps(document))
    assert workbench_read.presentation(store, root)["status"] == "invalid"


def test_unavailable_frozen_file_never_falls_back_to_source(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    (root / "REPORT.md").write_text("original")
    _, store, _, _, _, pub = published(tmp_path, items=[{"item_id": "report", "source_path": "REPORT.md"}])
    ref = pub["refs"][0]
    with store._read() as db:
        version = db.execute("SELECT object_version FROM publication_items").fetchone()[0]
    archive = root / ".research" / "objects" / version
    archive.chmod(0o644)
    archive.write_text("corrupted")
    (root / "REPORT.md").write_text("current workspace text")
    result = workbench_read.reference_content(store, root, ref)
    assert result["resolution"]["outcome"] == "unavailable"
    assert result["resolution"]["reason"] == "object_corrupted"
    assert "chunk" not in result


def test_binary_and_oversized_materials_have_bounded_read_states(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    (root / "opaque.dat").write_bytes(b"\xff\xfe\x80" * 100)
    (root / "large.md").write_bytes(b"a" * (workbench_read.PREVIEW_LIMIT + 100))
    _, store, _, _, _, pub = published(tmp_path, items=[
        {"item_id": "opaque", "source_path": "opaque.dat"},
        {"item_id": "large", "source_path": "large.md"},
    ])
    assert workbench_read.reference_content(store, root, "pub/P-001#opaque")["kind"] == "binary"
    first = workbench_read.reference_content(store, root, "pub/P-001#large")
    assert first["kind"] == "text"
    assert first["truncated"] is True
    assert len(base64.b64decode(first["chunk"])) == workbench_read.CHUNK_LIMIT
    last = workbench_read.reference_content(store, root, "pub/P-001#large",
                                            offset=workbench_read.PREVIEW_LIMIT - workbench_read.CHUNK_LIMIT)
    assert last["next_offset"] is None
    assert last["total_bytes"] == workbench_read.PREVIEW_LIMIT + 100
    assert "chunk" not in workbench_read.reference_content(store, root, "pub/P-999#absent")


def test_specialist_page_retains_durable_parent_after_exit(tmp_path):
    store, _, _ = initialized(tmp_path)
    task = store.specialist_create({"parent_session_id": "parent-node", "purpose": "domain",
                                    "label": "expert", "prompt": "read only", "inputs": []}, "expert-create")
    store.specialist_bind({"task_id": task["task_id"], "parent_session_id": "parent-node",
                                 "session_id": "finished-child"}, "expert-bind")
    store.specialist_finish({"task_id": task["task_id"], "state": "cancelled",
                             "result": {}, "exit_verified": True}, "expert-finish")
    page = workbench_read.page(store, "specialists")
    assert page["items"][0]["parent_session_id"] == "parent-node"
    assert page["items"][0]["child_session_id"] == "finished-child"
    assert page["items"][0]["state"] == "cancelled"
    assert "prompt" not in page["items"][0]


def test_session_page_projects_bounded_specialist_identity_without_ledger_writes(tmp_path):
    store, _, _ = initialized(tmp_path)
    task = store.specialist_create({"parent_session_id": "main", "purpose": "domain",
                                    "label": "Check citation " * 50, "prompt": "private long prompt", "inputs": []}, "create")
    store.specialist_bind({"task_id": task["task_id"], "parent_session_id": "main",
                           "session_id": "child"}, "bind")
    store.workflow("host", "child", "register", {"role": "specialist"}, "register-child")
    store.specialist_finish({"task_id": task["task_id"], "state": "cancelled",
                             "result": {}, "exit_verified": True}, "finish")
    before = hashlib.sha256(store.db_path.read_bytes()).hexdigest()
    first = workbench_read.page(store, "sessions", limit=1)
    second = workbench_read.page(store, "sessions", limit=1, **first["cursor"])
    row = second["items"][0]
    assert row["session_id"] == "child" and row["parent_session_id"] == "main"
    assert row["task_id"] == task["task_id"] and row["label"].startswith("Check citation")
    assert row["specialist_state"] == "cancelled"
    assert len(row["label"]) <= 241 and "prompt" not in row
    assert first["items"][0]["role"] == "main"
    assert hashlib.sha256(store.db_path.read_bytes()).hexdigest() == before
