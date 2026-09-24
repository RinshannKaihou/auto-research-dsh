import base64
import json
import sqlite3

import pytest

from auto_research.errors import ConflictError, ValidationError
from auto_research.native_store import NativeStore
from auto_research.service import NativeService


def project(tmp_path):
    store = NativeStore(tmp_path)
    store.initialize("研究低误报检测", "init")
    store.associate("host", "main", "associate")
    store.workflow(
        "host", "main", "register", {"role": "main", "cwd": str(tmp_path)}, "role"
    )
    return store


def test_node_question_is_atomically_anchored_and_revisions_are_immutable(tmp_path):
    store = project(tmp_path)
    node = store.propose("如何控制误报？", "现在", "分析阈值", "node")
    assert node["question_ref"] == "knowledge/K-001@1"
    original = store.reference_query(node["question_ref"])["value"]
    revised = store.revise_knowledge(
        {
            "ref": original["ref"],
            "expected_revision": 1,
            "changes": {"statement": "如何在吞吐约束下控制误报？"},
            "reason": "加入吞吐条件",
            "affected_scope_mode": "none",
            "change_kind": "narrow",
            "source_identity": {"kind": "agent"},
        },
        "revise",
    )
    assert revised["ref"] == "knowledge/K-001@2"
    assert store.reference_query(original["ref"])["value"]["statement"] == "如何控制误报？"
    with pytest.raises(ConflictError):
        store.revise_knowledge(
            {
                "ref": original["ref"],
                "expected_revision": 1,
                "changes": {"statement": "冲突写入"},
                "reason": "stale",
            },
            "stale-revision",
        )


def test_claims_require_evidence_and_publications_reference_fixed_knowledge(tmp_path):
    store = project(tmp_path)
    node = store.propose("Q", "now", "plan", "node")
    store.focus("host", "main", node["node_id"], "planner", "manual", "focus")
    with pytest.raises(ValidationError, match="requires"):
        store.record_knowledge(
            {"kind": "claim", "statement": "FPR < 0.01", "node_id": node["node_id"]},
            "unsupported",
        )
    note = store.note("host", "main", "fixture evidence", "condition", "note")
    claim = store.record_knowledge(
        {
            "kind": "claim",
            "statement": "在固定 fixture 上 FPR < 0.01",
            "conditions": {"dataset": "fixture-v1"},
            "evidence_refs": [note["note_id"]],
            "node_id": node["node_id"],
        },
        "claim",
    )
    published = store.publish_metadata(
        "host",
        "main",
        "partial",
        "fixture result",
        [],
        [
            {
                "item_id": "report",
                "kind": "text",
                "content": {},
                "knowledge_refs": [claim["ref"]],
            }
        ],
        "publish",
        [claim["ref"]],
    )
    assert published["refs"] == ["pub/P-001#report"]
    value = store.reference_query("pub/P-001")["value"]
    assert value["items"][0]["knowledge_refs"] == [claim["ref"]]
    todo = store.review_todo_next(node["node_id"])
    assert todo["trigger_ref"] == "pub/P-001"
    assert store.review_todo_state(todo["todo_id"], "completed", "todo-complete")["state"] == "completed"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("误报", True),
        ("长度奖励", True),
        ("奖励项", True),
        ("报误", False),
        ("长度无关", False),
        ("FPR 门", True),
        ("v4.2", True),
    ],
)
def test_cjk_bigram_and_exact_term_retrieval(tmp_path, query, expected):
    store = project(tmp_path)
    store.record_knowledge(
        {
            "kind": "hypothesis",
            "statement": "误报控制依赖长度奖励项与 FPR 门 v4.2",
            "conditions": {"吞吐": ">200 rps"},
        },
        "knowledge",
    )
    assert bool(store.knowledge_query(query=query)["items"]) is expected


def test_knowledge_search_filters_conditions_status_and_revision(tmp_path):
    store = project(tmp_path)
    item = store.record_knowledge(
        {
            "kind": "hypothesis",
            "statement": "条件化阈值",
            "conditions": {"dataset": "v1", "FPR": 0.01},
            "status": "working",
        },
        "knowledge",
    )
    revised = store.revise_knowledge(
        {
            "ref": item["ref"],
            "expected_revision": 1,
            "changes": {"status": "disputed"},
            "reason": "new counterexample",
            "affected_scope_mode": "versions",
            "affected_scope": [item["ref"]],
        },
        "revision",
    )
    assert store.knowledge_query(conditions={"dataset": "v1"}, status="disputed")["items"][0]["ref"] == revised["ref"]
    original = store.knowledge_query(revision=1)["items"][0]
    assert original["ref"] == item["ref"]
    assert original["match_reason"] == ["revision"]


def test_keyset_pages_hold_upper_bound_and_late_usage_correction_is_a_change(tmp_path):
    store = project(tmp_path)
    for index in range(5):
        store.propose(f"Q{index}", "now", "plan", f"node-{index}")
    first = store.history_page("nodes", limit=2)
    assert [row["node_id"] for row in first["items"]] == ["X-001", "X-002"]
    store.propose("late", "later", "plan", "late-node")
    cursor = {key: value for key, value in first["cursor"].items() if key != "collection"}
    second = store.history_page("nodes", limit=20, **cursor)
    assert [row["node_id"] for row in second["items"]] == ["X-003", "X-004", "X-005"]

    store.begin_usage("llm", "usage-start", host_id="host", session_id="main")
    cursor = store.changes_page(limit=200)["items"][-1]["event_id"]
    store.finish_usage("llm", 42, "actual", {"late": True}, "usage-finish")
    changes = store.changes_page(after=cursor)
    assert [item["kind"] for item in changes["items"]] == ["usage.adjusted"]
    assert changes["items"][0]["data"]["amount"] == 42


def test_context_body_is_stable_across_usage_only_changes_and_keeps_goal(tmp_path):
    store = project(tmp_path)
    node = store.propose("误报条件", "now", "plan", "node")
    store.focus("host", "main", node["node_id"], "planner", "auto", "focus")
    first = store.context_view("host", "main", max_chars=500)
    store.begin_usage("llm", "usage-start", host_id="host", session_id="main")
    store.finish_usage("llm", 9, "actual", {}, "usage-finish")
    second = store.context_view("host", "main", max_chars=500)
    assert second["text"] == first["text"]
    assert "研究低误报检测" in first["text"]
    assert "usage" not in first["text"].lower()
    manifest = {
        "body": first["text"], "host_id": "host", "session_id": "main",
        "source_sequence": first["source_sequence"],
    }
    assert store.record_context_request(manifest, "context-operation") == store.record_context_request(manifest, "context-operation")
    with store._read() as db:
        assert db.execute("SELECT COUNT(*) FROM context_requests").fetchone()[0] == 1


def test_context_keeps_frozen_discussion_background_and_selects_chinese_guidance(tmp_path):
    store = project(tmp_path)
    node = store.propose("如何控制误报？", "now", "分析阈值", "node")
    store.guidance_register(
        {
            "content": "# 通用原则\n保存来源。\n# 误报控制\n复核负样本条件。\n# 图像分割\n检查像素边界。",
            "path": "/fixture/guidance.md",
            "version": "fixture-v1",
        },
        "guidance",
    )
    store.associate("host", "discussion", "discussion-association")
    frozen = {
        "role": "discussion",
        "node": {"node_id": node["node_id"], "question": node["question"]},
        "publication_ids": ["P-fixed"],
        "instruction": "Discuss the frozen record.",
    }
    store.workflow(
        "host",
        "discussion",
        "register",
        {"role": "discussion", "cwd": str(tmp_path / "discussion"), "node_id": node["node_id"], "context": frozen},
        "discussion-role",
    )
    view = store.context_view("host", "discussion")
    assert "frozen_context" in view["text"]
    assert "P-fixed" in view["text"]
    assert "误报控制" in view["text"]
    assert "图像分割" not in view["text"]


def test_summary_and_pages_stay_bounded_with_large_ledgers(tmp_path):
    store = project(tmp_path)
    node = store.propose("scale", "now", "plan", "node")
    attempt = store.focus("host", "main", node["node_id"], "planner", "manual", "focus")
    association = store.control_state("host", "main")["association"]
    with store._connection() as db:
        db.executemany(
            "INSERT INTO notes VALUES(?,?,?,?,?)",
            ((f"N-scale-{index}", attempt["attempt_id"], "bounded", "progress", "2026-01-01") for index in range(100_000)),
        )
        db.executemany(
            "INSERT INTO usage_observations VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                (
                    f"O-scale-{index}", f"S-scale-{index}", association["association_id"],
                    attempt["attempt_id"], node["node_id"], "conversation", "fixture", "model",
                    1.0, "actual", "{}", "2026-01-01",
                )
                for index in range(100_000)
            ),
        )
    summary = store.control_state("host", "main")
    assert summary["counts"]["notes"] == 100_000
    assert summary["usage"]["known"] == 100_000
    assert "usage_observations" not in summary
    assert len(json.dumps(summary, ensure_ascii=False)) < 128_000
    assert len(store.history_page("notes")["items"]) == 50
    assert len(store.usage_page(limit=999)["items"]) == 200


def test_large_reference_expands_in_bounded_lossless_chunks(tmp_path):
    store = project(tmp_path)
    store.focus("host", "main", None, "planner", "manual", "focus")
    note = store.note("host", "main", "误报条件" * 5000, "condition", "large-note")
    preview = store.reference_query(note["note_id"])["value"]["body"]
    assert preview["truncated"] is True
    assert preview["expand_ref"] == note["note_id"]
    offset, encoded = 0, bytearray()
    while offset is not None:
        part = store.reference_chunk(note["note_id"], offset=offset, limit=1024)
        encoded.extend(base64.b64decode(part["chunk"]))
        offset = part["next_offset"]
    value = json.loads(encoded.decode("utf-8"))
    assert value["value"]["body"] == "误报条件" * 5000


def test_memory_projection_is_read_only_rebuilt_and_preserves_external_edits_as_draft(tmp_path):
    store = project(tmp_path)
    store.propose("first", "now", "plan", "node-one")
    projection = tmp_path / ".research" / "memory" / "PROJECT.md"
    assert projection.is_file()
    projection.chmod(0o644)
    projection.write_text(projection.read_text() + "external draft\n", encoding="utf-8")
    store.propose("second", "now", "plan", "node-two")
    assert "external draft" not in projection.read_text()
    drafts = list((projection.parent / "drafts").glob("PROJECT-*.md"))
    assert len(drafts) == 1
    assert "external draft" in drafts[0].read_text()
    assert projection.stat().st_mode & 0o222 == 0


def test_schema3_upgrade_is_backed_up_and_does_not_arm_execution(tmp_path):
    store = project(tmp_path)
    node = store.propose("legacy question", "now", "plan", "node")
    with sqlite3.connect(store.db_path) as db:
        for table in (
            "knowledge_entries",
            "knowledge_revisions",
            "node_questions",
            "node_checkpoints",
            "publication_knowledge",
            "knowledge_search",
            "context_packs",
            "context_requests",
            "specialist_tasks",
            "specialist_bindings",
            "guidance_sources",
            "review_todos",
        ):
            db.execute(f"DROP TABLE {table}")
        db.execute("DROP TABLE IF EXISTS knowledge_fts")
        db.execute("UPDATE nodes SET question_ref=NULL")
        db.execute("PRAGMA user_version=3")
    migrated = NativeStore(tmp_path)
    assert migrated.control_state("host", "main")["schema_version"] == 9
    with sqlite3.connect(tmp_path / ".research" / "schema-3-backup.sqlite3") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 3
    question_ref = migrated.reference_query(node["node_id"])["value"]["question_ref"]
    assert question_ref.startswith("knowledge/")
    assert migrated.control_state("host", "main")["workflow"]["run"]["state"] == "manual"


def test_specialist_is_registered_read_only_and_usage_freezes_to_parent_attempt(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")

    def call(method, session="main", operation=None, **fields):
        return service.handle(
            {
                "transport_id": operation or method,
                "operation_id": operation or method,
                "host_id": "host",
                "session_id": session,
                "method": method,
                **fields,
            }
        )["value"]

    root = tmp_path / "project"
    call("open", operation="open", root=str(root), goal="Goal")
    node = call(
        "propose", operation="node", question="Q", why_now="now", plan="plan"
    )
    attempt = call(
        "focus",
        operation="focus",
        node_id=node["node_id"],
        role="planner",
        mode="auto",
    )
    task = call(
        "specialist_create",
        operation="specialist",
        fields={
            "purpose": "review",
            "label": "review",
            "prompt": "check sources",
            "inputs": [node["question_ref"]],
        },
    )
    assert call(
        "specialist_create",
        operation="specialist",
        fields={
            "purpose": "review",
            "label": "review",
            "prompt": "check sources",
            "inputs": [node["question_ref"]],
        },
    )["task_id"] == task["task_id"]
    call(
        "specialist_bind_child",
        operation="bind",
        task_id=task["task_id"],
        child_session_id="child",
        node_id=node["node_id"],
        cwd=str(root),
    )
    context = call("memory_context", session="child", operation="child-context")
    assert "research-read-only" in context["text"]
    with pytest.raises(ValueError, match="cannot modify"):
        call(
            "note",
            session="child",
            operation="child-write",
            body="illegal",
            model_call=True,
        )
    observation = call(
        "usage_begin", session="child", operation="child-usage", source_key="child-llm"
    )
    assert observation["attempt_id"] == attempt["attempt_id"]
    assert observation["node_id"] == node["node_id"]
    settled = call(
        "specialist_finish",
        operation="settle",
        fields={
            "task_id": task["task_id"],
            "state": "incomplete",
            "result": {"gaps": ["missing"]},
            "exit_verified": True,
        },
    )
    assert settled["state"] == "incomplete"
    assert settled["exit_verified"] is True


def test_guidance_is_versioned_and_selected_into_stable_context(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")
    root = tmp_path / "project"
    guide = tmp_path / "guide.md"
    guide.write_text("# Common principles\nKeep negative results.\n# Throughput\nMeasure RPS.")

    def call(method, operation, **fields):
        return service.handle(
            {
                "transport_id": operation,
                "operation_id": operation,
                "host_id": "host",
                "session_id": "main",
                "method": method,
                **fields,
            }
        )["value"]

    call("open", "open", root=str(root), goal="Throughput research")
    registered = call("guidance_register", "guide", path=str(guide), version="v1")
    assert registered["version"] == "v1"
    assert call("guidance_status", "guide-status")["content_hash"] == registered["content_hash"]
    context = call("memory_context", "context")
    assert "method_guidance" in context["text"]
    assert "Measure RPS" in context["text"]
