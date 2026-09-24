import json
from pathlib import Path

import pytest

from auto_research.errors import ConflictError, NotFoundError, ValidationError
from auto_research.native_store import NativeStore
from auto_research.service import NativeService, ProjectRegistry


def initialized(tmp_path: Path) -> NativeStore:
    store = NativeStore(tmp_path)
    store.initialize("Find a robust result", "init")
    store.associate("host", "session", "associate")
    return store


def test_turn_and_usage_ownership_are_frozen_across_focus_switches(tmp_path):
    store = initialized(tmp_path)
    # Simulate a schema-2 database created before 0.3.1. The retained column
    # must not leak into the API or limit work after its old value is crossed.
    with store._connection() as db:
        db.execute("UPDATE project SET budget_limit=5")
    a = store.propose("A?", "first", "work A", "node-a")
    b = store.propose("B?", "second", "work B", "node-b")
    attempt_a = store.focus("host", "session", a["node_id"], "researcher", "manual", "focus-a")
    turn_a = store.bind_turn("host", "session", 1, "turn-a")
    usage_a = store.begin_usage("request-a", "usage-a-begin", host_id="host", session_id="session")
    queued = store.focus(
        "host", "session", b["node_id"], "researcher", "manual", "queue-b", defer=True
    )
    assert queued["current_attempt_id"] == attempt_a["attempt_id"]
    finished = store.finish("host", "session", "finished", {}, "finish-a")
    assert finished["next_node_id"] == b["node_id"]
    store.finish_usage("request-a", 7, "actual", {"late": True}, "usage-a-finish")
    state = store.query("host", "session")
    assert turn_a["node_id"] == a["node_id"]
    assert usage_a["node_id"] == a["node_id"]
    assert state["attempt"]["node_id"] == b["node_id"]
    assert state["usage_observations"][0]["node_id"] == a["node_id"]
    assert state["usage"]["known"] == 7.0
    assert state["usage"]["unknown_count"] == 0
    assert "budget_limit" not in state["project"]
    # Usage observation never gates research writes.
    assert store.note("host", "session", "Continue manually", "progress", "manual-note")


def test_publications_are_versioned_idempotent_and_allow_empty_findings(tmp_path):
    store = initialized(tmp_path)
    node = store.propose("Q?", "now", "derive", "node")
    store.focus("host", "session", node["node_id"], "researcher", "manual", "focus")
    first = store.publish_metadata(
        "host",
        "session",
        "partial",
        "A derivation with unresolved assumptions",
        ["No experiment yet"],
        [{"item_id": "draft", "kind": "derivation", "content": {}}],
        "publish-one",
    )
    assert first == store.publish_metadata(
        "host",
        "session",
        "partial",
        "A derivation with unresolved assumptions",
        ["No experiment yet"],
        [{"item_id": "draft", "kind": "derivation", "content": {}}],
        "publish-one",
    )
    second = store.publish_metadata(
        "host",
        "session",
        "partial",
        "Second version",
        [],
        [{"item_id": "draft", "kind": "derivation", "content": {"revision": 2}}],
        "publish-two",
    )
    assert first["refs"] == ["pub/P-001#draft"]
    assert second["refs"] == ["pub/P-002#draft"]
    relation = store.relate(first["refs"][0], second["refs"][0], "revised-by", "Clarified", "rel")
    assert relation["label"] == "revised-by"
    assert len(store.query("host", "session")["publications"]) == 2


def request(service, method, *, operation="operation", **fields):
    response = service.handle(
        {
            "transport_id": operation,
            "operation_id": operation,
            "host_id": "host",
            "session_id": "session",
            "method": method,
            **fields,
        }
    )
    assert response["ok"] is True
    return response["value"]


def test_native_service_uses_registry_and_retries_publication_from_fixed_intent(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = NativeService(tmp_path / "registry.sqlite3")
    request(service, "open", root=str(root), goal="Goal", operation="open")
    request(service, "focus", node_id=None, role="planner", mode="manual", operation="focus")
    source = root / "draft.txt"
    source.write_text("version one")
    fields = {
        "status": "partial",
        "summary": "Draft",
        "gaps": [],
        "items": [{"item_id": "draft", "kind": "text", "source_path": "draft.txt"}],
    }
    first = request(service, "publish", operation="publish", **fields)
    source.write_text("version two")
    retry = request(service, "publish", operation="publish", **fields)
    assert retry == first
    state = request(
        service, "history_page", collection="publications", operation="query-publications"
    )
    assert len(state["items"]) == 1
    item = state["items"][0]["items"][0]
    assert item["object_version"] != __import__("hashlib").sha256(b"version two").hexdigest()


def test_snapshot_restore_creates_new_copy_and_preserves_source_history(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = NativeService(tmp_path / "registry.sqlite3")
    request(service, "open", root=str(root), goal="Goal", operation="open")
    request(service, "focus", node_id=None, role="planner", mode="manual", operation="focus")
    (root / "notes.txt").write_text("handoff")
    snapshot = request(service, "snapshot", paths=["notes.txt"], operation="snapshot")
    request(
        service,
        "finish",
        state="finished",
        details={"reason": "handoff"},
        operation="finish-before-restore",
    )
    preview = request(
        service, "restore_preview", snapshot_id=snapshot["snapshot_id"], operation="preview"
    )
    prepared = request(
        service,
        "prepare_restore",
        snapshot_id=snapshot["snapshot_id"],
        operation="prepare-restore",
        preview_id=preview["preview_id"],
    )
    assert Path(prepared["workspace"]).is_dir()
    assert (Path(prepared["workspace"]) / "notes.txt").read_text() == "handoff"
    assert (
        request(
            service,
            "prepare_restore",
            snapshot_id=snapshot["snapshot_id"],
            operation="prepare-restore",
            preview_id=preview["preview_id"],
        )
        == prepared
    )


def test_branch_materializes_each_fixed_publication_version(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = NativeService(tmp_path / "registry.sqlite3")
    request(service, "open", root=str(root), goal="Goal", operation="open")
    request(service, "focus", node_id=None, role="planner", mode="manual", operation="focus")
    source = root / "draft.txt"
    source.write_text("version one")
    first = request(
        service,
        "publish",
        status="partial",
        summary="First",
        gaps=[],
        items=[{"item_id": "draft", "kind": "text", "source_path": "draft.txt"}],
        operation="publish-one",
    )
    source.write_text("version two")
    second = request(
        service,
        "publish",
        status="partial",
        summary="Second",
        gaps=[],
        items=[{"item_id": "draft", "kind": "text", "source_path": "draft.txt"}],
        operation="publish-two",
    )
    node = request(
        service,
        "propose",
        question="Compare?",
        why_now="Two fixed versions exist",
        plan="Compare exact bytes",
        inputs=[first["refs"][0], second["refs"][0]],
        strategy="anchor",
        anchor_ref=first["refs"][0],
        operation="propose",
    )
    prepared = request(
        service, "prepare_branch", node_id=node["node_id"], operation="prepare-branch",
    )
    workspace = Path(prepared["workspace"])
    values = sorted(path.read_text() for path in (workspace / "inputs" / "input").iterdir())
    assert values == ["version one", "version two"]
    assert [item["ref"] for item in prepared["inputs"]] == first["refs"] + second["refs"]
    assert (
        request(service, "prepare_branch", node_id=node["node_id"], operation="prepare-branch",)
        == prepared
    )


def test_snapshot_rejects_control_and_likely_credential_paths(tmp_path):
    store = initialized(tmp_path)
    store.focus("host", "session", None, "planner", "manual", "focus")
    (tmp_path / ".env").write_text("TOKEN=synthetic")
    service = NativeService(tmp_path / "registry.sqlite3")
    project_id = store.query("host", "session")["project"]["project_id"]
    service.registry.register(project_id, tmp_path, "host", "session")
    value = request(service, "snapshot", paths=[".env", ".research"], operation="unsafe")
    assert value["complete"] is False
    assert all(item["status"] == "incomplete" for item in value["manifest"])
    assert "synthetic" not in json.dumps(value)


def test_schema2_store_full_lifecycle_and_expansion(tmp_path):
    store = NativeStore(tmp_path)
    with pytest.raises(NotFoundError):
        store.query()
    project = store.initialize("Lifecycle", "init")
    assert project["control"] == "manual"
    with pytest.raises(ConflictError):
        store.initialize("Lifecycle", "init-again")
    with pytest.raises(ConflictError):
        store.initialize("different", "init")
    association = store.associate("host", "session", "associate", started_seq=4)
    assert store.associate("host", "session", "associate-existing") == association

    with pytest.raises(ValidationError):
        store.propose("Q", "now", "plan", "bad-inputs", inputs="bad")
    with pytest.raises(ValidationError):
        store.propose("Q", "now", "plan", "bad-strategy", strategy="invented")
    with pytest.raises(ValidationError):
        store.propose("Q", "now", "plan", "missing-anchor", strategy="anchor")
    with pytest.raises(NotFoundError):
        store.propose("Q", "now", "plan", "unknown-input", inputs=["missing"])
    node = store.propose("Q", "now", "plan", "node")
    with pytest.raises(ValidationError):
        store.focus("host", "session", node["node_id"], "worker", "invalid", "mode")
    with pytest.raises(NotFoundError):
        store.focus("host", "session", "X-404", "worker", "manual", "missing-node")
    attempt = store.focus("host", "session", node["node_id"], "worker", "manual", "focus")
    with pytest.raises(ConflictError):
        store.focus("host", "session", None, "planner", "manual", "focus-conflict")
    with pytest.raises(ConflictError):
        store.detach("host", "session", "detach-active")
    with pytest.raises(ConflictError):
        store.close_node(node["node_id"], "close-active")
    first_turn = store.bind_turn("host", "session", 1, "turn")
    assert store.bind_turn("host", "session", 1, "turn-existing") == first_turn
    note = store.note("host", "session", "condition " * 30, "condition", "note")
    assert note["attempt_id"] == attempt["attempt_id"]

    for status, gaps, items, message in [
        ("invalid", [], [], "publication status"),
        ("partial", "bad", [], "gaps"),
        ("partial", [], ["bad"], "items"),
        ("partial", [], [{"item_id": "same"}, {"item_id": "same"}], "Duplicate",),
    ]:
        with pytest.raises(ValidationError, match=message):
            store.publish_metadata(
                "host", "session", status, "summary", gaps, items, f"bad-{message}"
            )
    published = store.publish_metadata(
        "host",
        "session",
        "partial",
        "Long summary " * 30,
        ["gap"],
        [{"item_id": "draft", "kind": "derivation", "content": {"v": 1}}],
        "publish",
    )
    ref = published["refs"][0]
    assert store.query(ref=node["node_id"])["selected"]["kind"] == "node"
    assert store.query(ref="pub/P-001")["selected"]["kind"] == "publication"
    assert store.query(ref=ref)["selected"]["kind"] == "publication-item"
    assert store.relate(node["node_id"], ref, "supports", "structure", "relation")
    with pytest.raises(NotFoundError):
        store.query(ref="missing")

    observation = store.begin_usage(
        "llm-one",
        "usage-begin",
        host_id="host",
        session_id="session",
        purpose="conversation",
        provider="fixture",
        model="model",
    )
    assert observation["attempt_id"] == attempt["attempt_id"]
    with pytest.raises(ValidationError):
        store.finish_usage("llm-one", -1, "actual", {}, "negative-usage")
    with pytest.raises(NotFoundError):
        store.finish_usage("missing", 1, "actual", {}, "missing-usage")
    store.finish_usage("llm-one", 3, "estimated", {}, "usage-estimate")
    adjusted = store.finish_usage("llm-one", 4, "actual", {}, "usage-actual")
    assert adjusted["previous_amount"] == 3
    assert store.begin_usage("project-only", "usage-project")["association_id"] is None

    assert store.record_host_event("host", "session", "tool/result", 12, {}, "event")
    assert store.own_goal("host", "session", "goal", 1, "active", "goal")
    assert store.own_goal("host", "session", "goal", 2, "paused", "goal-update")
    assert store.set_control("auto", "control")["control"] == "auto"
    with pytest.raises(ValidationError):
        store.set_control("invalid", "bad-control")
    with pytest.raises(ValidationError):
        store.finish("host", "session", "open", {}, "bad-finish")

    store.finish("host", "session", "finished", {"reason": "done"}, "finish")
    assert store.close_node(node["node_id"], "close")["status"] == "closed"
    assert store.close_node(node["node_id"], "close-again")["status"] == "closed"
    with pytest.raises(NotFoundError):
        store.close_node("X-404", "close-missing")
    with pytest.raises(ConflictError):
        store.focus("host", "session", node["node_id"], "worker", "manual", "closed-focus")
    detached = store.detach("host", "session", "detach", ended_seq=15)
    assert detached["association_id"] == association["association_id"]
    with pytest.raises(NotFoundError):
        store.note("host", "session", "after detach", "progress", "no-association")
    with pytest.raises(NotFoundError):
        store.memory_view("host", "session", max_chars=200)


def test_schema2_store_restoration_and_legacy_ref_views(tmp_path):
    store = initialized(tmp_path)
    store.focus("host", "session", None, "planner", "manual", "focus")
    snapshot = store.record_snapshot(
        "host", "session", [{"source_path": "notes.txt", "status": "fixed"}], True, "snap"
    )
    restored = store.record_restore(
        "host",
        "session",
        snapshot["snapshot_id"],
        snapshot["attempt_id"],
        str(tmp_path / "workspaces" / "copy"),
        "restore",
    )
    assert restored["target_attempt_id"] == snapshot["attempt_id"]
    with store._connection() as db:
        db.execute(
            "INSERT INTO legacy_refs VALUES (?,?,?,?,?,?)",
            ("X-OLD/result#draft", "X-OLD", "draft", "finding", '{"text":"old"}', "now"),
        )
    assert store.query(ref="X-OLD/result#draft")["selected"]["kind"] == "legacy-ref"


def test_schema2_compatibility_columns_and_validation(tmp_path):
    metadata = tmp_path / ".research"
    metadata.mkdir()
    database = metadata / "state.sqlite3"
    with __import__("sqlite3").connect(database) as db:
        db.executescript(
            """
            CREATE TABLE nodes (
              node_id TEXT PRIMARY KEY, question TEXT, why_now TEXT, plan TEXT,
              inputs TEXT, purpose TEXT, status TEXT, created_at TEXT, closed_at TEXT
            );
            CREATE TABLE publication_items (
              publication_id TEXT, item_id TEXT, kind TEXT, content TEXT,
              object_version TEXT, source_path TEXT
            );
            PRAGMA user_version=2;
            """
        )
    NativeStore(tmp_path)
    with __import__("sqlite3").connect(database) as db:
        assert {row[1] for row in db.execute("PRAGMA table_info(nodes)")} >= {
            "strategy",
            "anchor_ref",
        }
        assert "object_kind" in {
            row[1] for row in db.execute("PRAGMA table_info(publication_items)")
        }

    wrong = tmp_path / "wrong"
    (wrong / ".research").mkdir(parents=True)
    with __import__("sqlite3").connect(wrong / ".research" / "state.sqlite3") as db:
        db.execute("PRAGMA user_version=10")
    with pytest.raises(ValidationError, match="Schema 10"):
        NativeStore(wrong)
    with pytest.raises(ValidationError):
        NativeStore(tmp_path / "text").initialize("", "text")


def test_native_service_control_projection_and_registry_conflicts(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    service = NativeService(tmp_path / "registry.sqlite3")
    assert request(service, "capabilities", operation="capabilities")["schema_version"] == 9
    state = request(service, "open", root=str(root), goal="Goal", operation="open")
    reopened = request(service, "open", root=str(root), operation="reopen")
    assert reopened["project"] == state["project"]
    request(service, "focus", node_id=None, role="planner", mode="manual", operation="focus")
    assert request(service, "memory", max_chars=1000, operation="memory")["text"]
    assert request(
        service,
        "host_event",
        event_type="tool/result",
        sequence=3,
        facts={"name": "bash"},
        operation="event",
    )
    assert request(
        service, "own_goal", goal_id="goal", revision=1, phase="active", operation="goal",
    )
    with pytest.raises(ValueError, match="Unknown native research method"):
        service.handle(
            {
                "transport_id": "removed-budget",
                "operation_id": "removed-budget",
                "host_id": "host",
                "session_id": "session",
                "method": "budget",
                "budget": 20,
            }
        )
    assert request(service, "control", control="paused", operation="control")["control"] == "paused"
    begun = request(
        service,
        "usage_begin",
        source_key="llm",
        purpose="session-title",
        provider="fixture",
        model="model",
        operation="usage-begin",
    )
    assert begun["attempt_id"] is not None
    assert (
        request(
            service,
            "usage_finish",
            source_key="llm",
            amount=2,
            completeness="actual",
            details={},
            operation="usage-finish",
        )["amount"]
        == 2
    )
    assert len(request(service, "project_sessions", operation="sessions")) == 1

    (root / "workspaces").mkdir()
    (root / "workspaces" / "text.txt").write_text("visible")
    assert (
        request(service, "preview", path="workspaces/text.txt", operation="preview")["text"]
        == "visible"
    )
    (root / "workspaces" / "binary.bin").write_bytes(b"\0binary")
    with pytest.raises(ValueError, match="Binary"):
        request(service, "preview", path="workspaces/binary.bin", operation="binary")
    for path in ("/etc/passwd", "../outside", "other/file"):
        with pytest.raises(ValueError):
            request(service, "preview", path=path, operation=f"preview-{path}")

    snapshot = request(service, "snapshot", paths=["workspaces/text.txt"], operation="snapshot")
    with pytest.raises(ValueError, match="active or unverified"):
        request(
            service,
            "prepare_restore",
            snapshot_id=snapshot["snapshot_id"],
            operation="restore-active",
            preview_id=request(
                service,
                "restore_preview",
                snapshot_id=snapshot["snapshot_id"],
                operation="active-preview",
            )["preview_id"],
        )
    request(service, "finish", state="stopped", details={}, operation="finish")
    prepared = request(
        service,
        "prepare_restore",
        snapshot_id=snapshot["snapshot_id"],
        operation="restore",
        preview_id=request(
            service,
            "restore_preview",
            snapshot_id=snapshot["snapshot_id"],
            operation="restore-preview",
        )["preview_id"],
    )
    child_identity = {
        "transport_id": "child-open",
        "operation_id": "child-open",
        "host_id": "host",
        "session_id": "child",
        "method": "open",
        "root": str(root),
    }
    assert service.handle(child_identity)["ok"] is True
    child = NativeStore(root).focus("host", "child", None, "handoff", "manual", "child-focus")
    recorded = service.handle(
        {
            "transport_id": "record",
            "operation_id": "record",
            "host_id": "host",
            "session_id": "child",
            "method": "record_restore",
            "snapshot_id": snapshot["snapshot_id"],
            "source_attempt_id": snapshot["attempt_id"],
            "workspace": prepared["workspace"],
        }
    )["value"]
    assert recorded["target_attempt_id"] == child["attempt_id"]
    NativeStore(root).finish("host", "child", "finished", {}, "child-finish")
    assert service.handle(
        {
            "transport_id": "detach",
            "operation_id": "detach",
            "host_id": "host",
            "session_id": "child",
            "method": "detach",
        }
    )["value"]["ended_at"]

    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError, match="already associated"):
        service.handle(
            {
                "transport_id": "other",
                "operation_id": "other",
                "host_id": "host",
                "session_id": "session",
                "method": "open",
                "root": str(other),
                "goal": "Other",
            }
        )
    with pytest.raises(ValueError, match="Unknown native"):
        request(service, "unknown", operation="unknown")

    registry = ProjectRegistry(tmp_path / "separate-registry.sqlite3")
    with pytest.raises(ValueError, match="not associated"):
        registry.root_for("host", "missing")
    with pytest.raises(ValueError, match="not associated"):
        registry.sessions_for("host", "missing")
