import sqlite3

import pytest

from auto_research.errors import ConflictError, ValidationError
from auto_research.native_store import NativeStore
from auto_research.schema5 import ensure_schema5_columns, migrate_schema5
from auto_research.service import NativeService


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


def fixture(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")
    root = tmp_path / "project"
    call(service, "open", "open", root=str(root), goal="Goal")
    return service, root


def test_schema5_column_upgrade_covers_all_schema4_projection_tables(tmp_path):
    database = tmp_path / "columns.sqlite3"
    with sqlite3.connect(database) as db:
        db.executescript(
            """
            CREATE TABLE nodes(node_id TEXT PRIMARY KEY);
            CREATE TABLE relations(relation_id TEXT PRIMARY KEY);
            CREATE TABLE owned_goals(session_id TEXT PRIMARY KEY);
            CREATE TABLE workflow_sessions(session_id TEXT PRIMARY KEY);
            """
        )
        ensure_schema5_columns(db)
        ensure_schema5_columns(db)
        assert {row[1] for row in db.execute("PRAGMA table_info(nodes)")} >= {
            "origin_kind",
            "root_reason",
        }
        assert {row[1] for row in db.execute("PRAGMA table_info(relations)")} >= {
            "relation_type",
            "scheduling",
        }
        assert {row[1] for row in db.execute("PRAGMA table_info(owned_goals)")} >= {
            "activation",
            "change_reason",
            "source_sequence",
        }
        assert {row[1] for row in db.execute("PRAGMA table_info(workflow_sessions)")} >= {
            "close_state",
            "close_attempt_id",
            "close_reason",
        }


def test_schema5_migration_rejects_wrong_source_and_is_idempotent_at_five(tmp_path):
    invalid = tmp_path / "invalid.sqlite3"
    with sqlite3.connect(invalid) as db:
        db.execute("PRAGMA user_version=3")
    with pytest.raises(ValidationError, match="Expected schema 4"):
        migrate_schema5(invalid)

    current = tmp_path / "current.sqlite3"
    with sqlite3.connect(current) as db:
        db.execute("PRAGMA user_version=5")
    migrate_schema5(current)


def test_schema5_upgrade_creates_consistent_schema4_backup_and_marks_legacy_nodes(tmp_path):
    service, root = fixture(tmp_path)
    node = call(
        service,
        "propose",
        "legacy-node",
        question="Legacy question",
        why_now="Legacy fixture",
        plan="Inspect",
    )
    service.stores.clear()
    database = root / ".research/state.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("DROP TABLE IF EXISTS node_dependencies")
        db.execute("DROP TABLE IF EXISTS material_consumptions")
        db.execute("DROP TABLE IF EXISTS usage_coverage_gaps")
        db.execute("UPDATE nodes SET origin_kind=NULL,root_reason=NULL WHERE node_id=?", (node["node_id"],))
        db.execute("PRAGMA user_version=4")

    upgraded = NativeStore(root)
    assert upgraded.query()["schema_version"] == 9
    with sqlite3.connect(database) as db:
        migrated = db.execute(
            "SELECT origin_kind,root_reason FROM nodes WHERE node_id=?", (node["node_id"],)
        ).fetchone()
        assert migrated == ("legacy_unresolved", None)
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    with sqlite3.connect(root / ".research/schema-4-backup.sqlite3") as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 4


def test_explicit_root_or_predecessors_are_atomic_and_inputs_are_owned_by_predecessor(tmp_path):
    service, _ = fixture(tmp_path)
    with pytest.raises(ValidationError, match="root_reason or predecessors"):
        call(
            service,
            "propose",
            "missing-origin",
            question="Ambiguous",
            why_now="Now",
            plan="Try",
            model_call=True,
        )
    root = call(
        service,
        "propose",
        "root",
        question="Independent root",
        why_now="Now",
        plan="Measure",
        root_reason="Independent baseline",
        model_call=True,
    )
    unrelated = call(
        service,
        "propose",
        "other-root",
        question="Another root",
        why_now="Now",
        plan="Measure",
        root_reason="Independent control",
        model_call=True,
    )
    with pytest.raises(ValidationError, match="owned by predecessor"):
        call(
            service,
            "propose",
            "bad-child",
            question="Invalid child",
            why_now="After root",
            plan="Compare",
            predecessors=[
                {
                    "node_id": root["node_id"],
                    "relation_type": "depends_on",
                    "rationale": "Needs the root result",
                    "input_refs": [unrelated["question_ref"]],
                }
            ],
            model_call=True,
        )
    child = call(
        service,
        "propose",
        "child",
        question="Dependent child",
        why_now="After root",
        plan="Compare",
        predecessors=[
            {
                "node_id": root["node_id"],
                "relation_type": "depends_on",
                "rationale": "Needs the root question",
                "input_refs": [root["question_ref"]],
            }
        ],
        model_call=True,
    )
    assert child["origin_kind"] == "derived"
    assert child["inputs"] == [root["question_ref"]]
    dependencies = call(
        service, "history_page", "dependencies", collection="dependencies"
    )["items"]
    assert dependencies[0]["predecessor_node_id"] == root["node_id"]
    assert dependencies[0]["successor_node_id"] == child["node_id"]
    assert dependencies[0]["scheduling"] is True


def test_material_consumption_is_append_only_idempotent_and_attempt_frozen(tmp_path):
    service, _ = fixture(tmp_path)
    source = call(
        service,
        "propose",
        "source",
        question="Source",
        why_now="Now",
        plan="Measure",
        root_reason="Independent source",
        model_call=True,
    )
    target = call(
        service,
        "propose",
        "target",
        question="Target",
        why_now="Now",
        plan="Use evidence",
        root_reason="Independent target",
        model_call=True,
    )
    call(service, "focus", "focus-target", node_id=target["node_id"])
    first = call(
        service,
        "consume",
        "consume-once",
        source_ref=source["question_ref"],
        use="Use as an external comparison",
        relation_type="context",
        model_call=True,
    )
    assert call(
        service,
        "consume",
        "consume-once",
        source_ref=source["question_ref"],
        use="Use as an external comparison",
        relation_type="context",
        model_call=True,
    ) == first
    with pytest.raises(ConflictError):
        call(
            service,
            "consume",
            "consume-once",
            source_ref=source["question_ref"],
            use="Changed meaning",
            relation_type="context",
            model_call=True,
        )
    rows = call(service, "history_page", "consumptions", collection="consumptions")["items"]
    assert len(rows) == 1
    assert rows[0]["attempt_id"] == first["attempt_id"]
    assert rows[0]["association_id"] == first["association_id"]


def test_owned_goal_projection_is_monotonic_and_keeps_nested_lifecycle_facts(tmp_path):
    service, _ = fixture(tmp_path)
    current = call(
        service,
        "own_goal",
        "goal-r3",
        goal_id="owned",
        revision=3,
        phase="active",
        activation="armed",
        change_reason="resume",
        source_sequence=30,
    )
    stale = call(
        service,
        "own_goal",
        "goal-r2",
        goal_id="owned",
        revision=2,
        phase="paused",
        activation="disarmed",
        change_reason="late replay",
        source_sequence=20,
    )
    assert stale["revision"] == current["revision"] == 3
    assert stale["phase"] == "active"
    assert stale["activation"] == "armed"
    with pytest.raises(ConflictError, match="same goal revision"):
        call(
            service,
            "own_goal",
            "goal-r3-conflict",
            goal_id="owned",
            revision=3,
            phase="complete",
            activation="disarmed",
            source_sequence=31,
        )


def test_pending_review_context_reports_total_instead_of_calling_eight_the_total(tmp_path):
    service, _ = fixture(tmp_path)
    for index in range(12):
        node = call(
            service,
            "propose",
            f"review-node-{index}",
            question=f"Question {index}",
            why_now="Now",
            plan="Review",
        )
        call(
            service,
            "memory_write",
            f"review-revision-{index}",
            action="revise",
            fields={
                "ref": node["question_ref"],
                "expected_revision": 1,
                "changes": {"statement": f"Revised question {index}"},
                "reason": "queue fixture",
                "affected_scope_mode": "none",
                "change_kind": "reword",
            },
        )
    context = call(service, "memory_context", "review-context")
    block = next(
        part for part in context["text"].split("## ") if part.startswith("pending_review\n")
    )
    pending = __import__("json").loads(block.split("\n", 1)[1])
    assert pending["pending_total"] == 12
    assert pending["shown_count"] == 8
    assert pending["has_more"] is True
    assert len(pending["items"]) == 8
