import hashlib
import io
import json
import sqlite3

import pytest

from auto_research.errors import ValidationError
from auto_research.migration import (
    _json_load,
    _max_counter,
    file_manifest,
    migrate_copy,
    preflight,
    project_file_manifest,
    recovery_preview,
)
from auto_research.service import MAX_REQUEST, ReadOnlyService, redact, serve
from auto_research.store import Store


@pytest.fixture
def legacy(tmp_path):
    store = Store(tmp_path)
    store.initialize("Legacy failed planning", budget=1000000)
    # Exact historical accounting values in synthetic data; never touch CASE.
    with store._connection() as db:
        for i, cost in enumerate([13070, 524659], 1):
            workspace = tmp_path / ".research" / "workspaces" / f"A-00{i}"
            workspace.mkdir(parents=True)
            (workspace / "probe.py").write_text(f"print({i})\n")
            fields = {
                "session_id": f"ari-legacy-{i}",
                "workspace": str(workspace),
                "error": {"code": "AGENT_ABORTED" if i == 1 else "timeout"},
                "command": "python probe.py",
                "api_key": "synthetic-secret-marker",
            }
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"A-00{i}",
                    None,
                    "coordinator",
                    "interrupted",
                    5000,
                    0,
                    cost,
                    "actual",
                    json.dumps(fields),
                    "2026-09-14",
                    "2026-09-14",
                )[:11],
            )
    return store


def test_preflight_and_recovery_do_not_reconcile(legacy, monkeypatch):
    from auto_research.runtime import Runtime

    monkeypatch.setattr(Runtime, "__init__", lambda *a: pytest.fail("Must not instantiate runtime"))
    before = legacy.snapshot()
    result = preflight(legacy.root)
    assert result["budget"]["known_spent"] == 537729
    assert result["budget"]["hold"] == 0
    assert result["control"] == "paused"
    assert result["nodes"] == []
    assert [a["cost"] for a in result["attempts"]] == [13070, 524659]
    assert all(a["native_session"] is None for a in result["attempts"])
    preview = recovery_preview(legacy.root, "A-002")
    assert len(preview["files"]) == 1
    assert preview["files"][0]["sha256"] == hashlib.sha256(b"print(2)\n").hexdigest()
    assert preview["creates_attempt"] is False
    assert preview["process_status"] == "unverified"
    assert legacy.snapshot() == before


def test_read_only_observes_committed_wal(legacy):
    with legacy._connection() as writer:
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE attempts SET cost=17 WHERE id='A-001'")
        assert preflight(legacy.root, files=False)["budget"]["known_spent"] == 524676


def test_future_schema_refused_without_downgrade(legacy):
    with legacy._connection() as db:
        db.execute("PRAGMA user_version=2")
    before = legacy.db_path.read_bytes()
    with pytest.raises(ValidationError, match="Schema 2"):
        Store(legacy.root)
    with pytest.raises(ValidationError, match="Schema 2"):
        legacy.configure(control="running")
    with sqlite3.connect(legacy.db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert legacy.db_path.read_bytes() == before


def test_inventory_does_not_follow_links_or_read_special_files(tmp_path):
    (tmp_path / "link").symlink_to("/etc/passwd")
    import os

    os.mkfifo(tmp_path / "fifo")
    (tmp_path / ".git").write_text("gitdir: /private/source/.git/worktrees/shared")
    rows = {r["path"]: r for r in file_manifest(tmp_path)}
    assert rows["link"]["status"] == "not-followed"
    assert rows["fifo"]["status"] == "not-read"
    assert rows[".git"]["kind"] == "git-metadata"
    assert "sha256" not in rows[".git"]


def test_private_service_rejects_mutations_and_path_escape(legacy):
    service = ReadOnlyService({"registered": str(legacy.root)})
    base = {"request_id": "r1", "project_id": "registered"}
    for method in ["start", "stop", "reserve", "bind_session"]:
        with pytest.raises(ValueError, match="Read-only"):
            service.handle({**base, "method": method})
    with pytest.raises(ValueError, match="Unknown project"):
        service.handle({**base, "project_id": "../../outside", "method": "status"})
    for path in [
        "../state.sqlite3",
        "/etc/passwd",
        "state.sqlite3",
        "workspaces/../../state.sqlite3",
    ]:
        with pytest.raises(ValueError):
            service.handle({**base, "method": "preview", "path": path})
    result = service.handle({**base, "method": "status"})
    assert "synthetic-secret-marker" not in json.dumps(result)
    assert result["value"]["attempts"][0]["command"] == "python probe.py"
    assert result["value"]["attempts"][0]["error"]["code"] == "AGENT_ABORTED"


def test_stdio_boundaries_and_recovery_after_invalid_json(legacy):
    service = ReadOnlyService({"p": str(legacy.root)})
    source = io.BytesIO(
        b"[]\ninvalid\n"
        + json.dumps({"request_id": "ok", "project_id": "p", "method": "status"}).encode()
        + b"\n"
    )
    output = io.BytesIO()
    serve(service, source, output)
    rows = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r["ok"] for r in rows] == [False, False, True]
    output = io.BytesIO()
    serve(service, io.BytesIO(b"x" * (MAX_REQUEST + 2) + b"\n{}\n"), output)
    assert len(output.getvalue().splitlines()) == 1


def test_cli_preflight_is_readonly(legacy, monkeypatch):
    from auto_research.cli import execute, parser

    monkeypatch.setattr(
        Store, "__init__", lambda *a: pytest.fail("No writable Store in migration CLI")
    )
    result = execute(
        parser().parse_args(["-p", str(legacy.root), "migration", "dry-run", "--no-files"])
    )
    assert result["budget"]["known_spent"] == 537729


def test_missing_or_corrupt_database_fails_without_creating_a_project(tmp_path):
    with pytest.raises(ValueError, match="No research database"):
        preflight(tmp_path / "missing")
    assert not (tmp_path / "missing").exists()
    metadata = tmp_path / ".research"
    metadata.mkdir()
    (metadata / "state.sqlite3").write_bytes(b"not a SQLite database")
    with pytest.raises(ValueError, match="No reconcile or immutable fallback"):
        preflight(tmp_path)
    assert file_manifest(tmp_path / "absent") == []


def test_worker_preview_and_unknown_usage_preserve_audit_fields(legacy):
    legacy.propose(
        {
            "question": "Q-001",
            "why_now": "Inspect partial work",
            "plan": "Read material",
            "inputs": [],
        }
    )
    with legacy._connection() as db:
        db.execute(
            "UPDATE attempts SET node_id='X-001',role='worker',cost=NULL,"
            "cost_kind='unknown',hold=5000 WHERE id='A-002'"
        )
    report = preflight(legacy.root, files=False)
    assert report["nodes"][0]["id"] == "X-001"
    assert report["budget"]["known_spent"] == 13070
    assert report["budget"]["unknown_attempts"] == ["A-002"]
    assert report["budget"]["hold"] == 5000
    preview = recovery_preview(legacy.root, "A-002")
    assert preview["attempt"]["role"] == "worker"
    assert preview["files"][0]["path"].endswith("probe.py")
    with pytest.raises(ValueError, match="Unknown attempt"):
        recovery_preview(legacy.root, "A-404")
    with legacy._connection() as db:
        db.execute("PRAGMA user_version=2")
    with pytest.raises(ValueError, match="Unsupported preflight schema"):
        preflight(legacy.root)


def test_file_preview_is_bounded_and_symlinks_are_refused(legacy):
    from auto_research.service import MAX_PREVIEW

    root = legacy.root / ".research/workspaces/A-002"
    (root / "large.txt").write_bytes(b"a" * (MAX_PREVIEW + 10))
    (root / "binary").write_bytes(b"\x00abc")
    (root / "escape").symlink_to("/etc/passwd")
    (root / "nested").mkdir()
    (root / "nested/.git").mkdir()
    (root / "nested/.git/config").write_text("Do not traverse")
    service = ReadOnlyService({"p": str(legacy.root)})
    base = {"request_id": "r", "project_id": "p", "method": "preview"}
    result = service.handle({**base, "path": "workspaces/A-002/large.txt"})["value"]
    assert result["truncated"] and len(result["text"]) == MAX_PREVIEW
    for path in ["binary", "escape"]:
        with pytest.raises((ValueError, OSError)):
            service.handle({**base, "path": "workspaces/A-002/" + path})
    inventory = service.handle({**base, "method": "inventory"})["value"]
    assert not any(f["path"].endswith(".git/config") for f in inventory["files"])
    assert (
        service.handle({**base, "method": "recovery_preview", "attempt_id": "A-002"})["value"][
            "can_start"
        ]
        is False
    )
    with pytest.raises(ValueError, match="request_id"):
        service.handle({**base, "request_id": ""})
    protected = redact(
        {
            "Authorization": "Bearer synthetic-marker",
            "command": "python /work/probe.py",
            "output": "Authorization: Bearer synthetic-marker\nresult=42",
        }
    )
    assert "synthetic-marker" not in json.dumps(protected)
    assert "result=42" in protected["output"]
    assert protected["command"] == "python /work/probe.py"


def test_service_main_is_private_and_response_limit_is_explicit(legacy, monkeypatch):
    import sys
    from types import SimpleNamespace
    from auto_research import service

    request = (
        json.dumps({"request_id": "r", "project_id": "p", "method": "status"}).encode() + b"\n"
    )
    source = io.BytesIO(request)
    output = io.BytesIO()
    monkeypatch.setattr(
        sys, "argv", ["service", "--registrations", json.dumps({"p": str(legacy.root)})]
    )
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=source))
    monkeypatch.setattr(sys, "stdout", SimpleNamespace(buffer=output))
    service.main()
    assert json.loads(output.getvalue())["ok"] is True
    monkeypatch.setattr(service, "MAX_RESPONSE", 32)
    output = io.BytesIO()
    service.serve(ReadOnlyService({"p": str(legacy.root)}), io.BytesIO(request), output)
    assert "Response exceeds" in json.loads(output.getvalue())["error"]["message"]


def test_migration_cli_without_json_flag_formats_its_own_read_model(legacy, capsys):
    from auto_research.cli import main

    main(["-p", str(legacy.root), "migration", "preflight", "--no-files"])
    text = capsys.readouterr().out
    report = json.loads(text)
    assert report["budget"]["known_spent"] == 537729
    assert "synthetic-secret-marker" not in text
    assert report["attempts"][0]["command"] == "python probe.py"


def test_readonly_connect_failure_never_retries_as_writable(legacy, monkeypatch):
    calls = []

    def unavailable(*args, **kwargs):
        calls.append((args, kwargs))
        raise sqlite3.OperationalError("sidecar unavailable")

    monkeypatch.setattr(sqlite3, "connect", unavailable)
    with pytest.raises(ValueError, match="No reconcile or immutable fallback"):
        preflight(legacy.root)
    assert len(calls) == 1
    assert calls[0][0][0].endswith("?mode=ro")


def test_copy_migration_scans_both_workspace_layouts_and_is_idempotent(legacy, tmp_path):
    root_layout = legacy.root / "workspaces" / "A-003"
    root_layout.mkdir(parents=True)
    (root_layout / "root-layout.txt").write_text("root workspace")
    before = file_manifest(legacy.root / ".research")
    destination = tmp_path.parent / f"{tmp_path.name}-migrated-copy"
    first = migrate_copy(legacy.root, destination)
    assert first["status"] == "migrated-copy"
    assert first["state"]["usage"]["known"] == 537729
    assert [row["attempt_id"] for row in first["state"]["attempts"]] == ["A-001", "A-002"]
    paths = {row["path"] for row in first["marker"]["file_manifest"]}
    assert ".research/workspaces/A-001/probe.py" in paths
    assert "workspaces/A-003/root-layout.txt" in paths
    assert (destination / ".research" / "legacy-schema1.sqlite3").is_file()
    second = migrate_copy(legacy.root, destination)
    assert second["status"] == "already-migrated"
    assert second["state"]["usage"]["known"] == 537729
    assert len(second["state"]["usage_observations"]) == 2
    assert file_manifest(legacy.root / ".research") == before


def test_copy_migration_imports_nodes_inputs_refs_and_rejects_unsafe_destinations(legacy, tmp_path):
    parent = legacy.propose(
        {
            "question": "Q-001",
            "why_now": "Create fixed history",
            "plan": "Preserve a finding",
            "inputs": [],
        }
    )
    old_ref = f"{parent['id']}/result#observation"
    with legacy._connection() as db:
        db.execute(
            "UPDATE nodes SET status='closed',closed_at='2026-09-14',result='{}' WHERE id=?",
            (parent["id"],),
        )
        db.execute(
            "INSERT INTO published_items VALUES (?,?,?,?,?)",
            (
                old_ref,
                parent["id"],
                "observation",
                "finding",
                json.dumps({"ref": old_ref, "text": "historical"}),
            ),
        )
    child = legacy.propose(
        {
            "question": "Q-001",
            "why_now": "Use fixed history",
            "plan": "Continue",
            "inputs": [{"ref": old_ref, "use": "historical anchor"}],
        }
    )
    destination = tmp_path.parent / f"{tmp_path.name}-nodes-copy"
    migrated = migrate_copy(legacy.root, destination)
    imported = {node["node_id"]: node for node in migrated["state"]["nodes"]}
    assert imported[child["id"]]["inputs"] == [old_ref]
    assert migrated["state"]["legacy_refs"][0]["ref"] == old_ref
    assert migrated["state"]["legacy_refs"][0]["item"]["text"] == "historical"

    with pytest.raises(ValueError, match="outside"):
        migrate_copy(legacy.root, legacy.root / "inside")
    existing = tmp_path.parent / f"{tmp_path.name}-existing"
    existing.mkdir()
    with pytest.raises(ValueError, match="absent"):
        migrate_copy(legacy.root, existing)
    foreign = tmp_path.parent / f"{tmp_path.name}-foreign"
    (foreign / ".research").mkdir(parents=True)
    (foreign / ".research" / "migration-schema1-to-2.json").write_text(
        json.dumps({"source_root": "/another/project"})
    )
    with pytest.raises(ValueError, match="another source"):
        migrate_copy(legacy.root, foreign)


def test_migration_manifest_registered_layouts_cleanup_and_parsing(tmp_path):
    root = tmp_path / "root"
    custom = root / "custom" / "workspace"
    custom.mkdir(parents=True)
    (custom / "state.txt").write_text("state")
    single = root / "single.txt"
    single.write_text("one")
    rows = project_file_manifest(
        root,
        [
            {"relative": "custom/workspace", "external": False, "field": "workspace"},
            {"relative": "single.txt", "external": False, "field": "job_dir"},
            {"relative": None, "external": True, "field": "workspace"},
        ],
    )
    layouts = {(row["path"], row["layout"]) for row in rows}
    assert ("custom/workspace/state.txt", "registered-workspace") in layouts
    assert ("single.txt", "registered-job_dir") in layouts
    assert _max_counter(["X-bad", "X-007", "A-100"], "X") == 7
    assert _json_load("not-json", {"fallback": True}) == {"fallback": True}

    empty = tmp_path / "empty"
    Store(empty)
    with pytest.raises(ValueError, match="not initialized"):
        preflight(empty)

    failed_destination = tmp_path / "failed-copy"
    native_source = destination_for_schema2(tmp_path)
    migrated = migrate_copy(native_source, tmp_path / "native-copy")
    assert migrated["state"]["schema_version"] == 9
    assert migrated["state"]["workflow"]["run"]["state"] == "cold"
    assert migrate_copy(native_source, tmp_path / "native-copy")["status"] == "already-migrated"
    with sqlite3.connect(native_source / ".research/state.sqlite3") as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError, match="Unsupported preflight schema"):
        migrate_copy(native_source, failed_destination)
    assert not failed_destination.exists()
    assert not list(tmp_path.glob(".failed-copy.migration-*"))


def destination_for_schema2(tmp_path):
    root = tmp_path / "schema2-source"
    from auto_research.native_store import NativeStore

    NativeStore(root).initialize("schema two", "init")
    return root
