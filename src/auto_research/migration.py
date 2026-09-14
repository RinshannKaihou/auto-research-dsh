"""Legacy inventory and explicit copy-only migration into schema 2.

Preflight reads committed SQLite state without reconciling execution. Migration
creates a new copy, an integrity-checked database backup, and an idempotent
schema-2 import; it never mutates the source project.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
from typing import Iterator
from uuid import uuid4

from .artifacts import _open_source
from .native_store import NativeStore, _json as _dump_json


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def readonly_database(root: str | Path) -> Iterator[sqlite3.Connection]:
    path = Path(root).expanduser().resolve() / ".research" / "state.sqlite3"
    if not path.is_file():
        raise ValueError(f"No research database: {path}")
    db = None
    try:
        db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA query_only=ON")
        db.execute("BEGIN")
        yield db
    except sqlite3.Error as exc:
        message = (
            f"Read-only SQLite inventory failed: {exc}. "
            "No reconcile or immutable fallback was performed."
        )
        raise ValueError(message) from exc
    finally:
        if db is not None:
            db.rollback()
            db.close()


def file_manifest(root: Path) -> list[dict]:
    """Hash regular files without following links, blocking on FIFOs, or reading .git."""
    rows = []
    if not root.is_dir():
        return rows
    for parent, directories, files in os.walk(root, followlinks=False):
        for name in sorted(directories + files):
            path = Path(parent) / name
            row = {"path": path.relative_to(root).as_posix()}
            try:
                info = path.lstat()
                if name == ".git":
                    row.update(kind="git-metadata", status="excluded; isolate before restore")
                    if name in directories:
                        directories.remove(name)
                elif stat.S_ISLNK(info.st_mode):
                    row.update(kind="symlink", target=os.readlink(path), status="not-followed")
                elif stat.S_ISDIR(info.st_mode):
                    continue
                elif stat.S_ISREG(info.st_mode):
                    with _open_source(path.relative_to(root), root) as fd:
                        with os.fdopen(os.dup(fd), "rb") as stream:
                            before = os.fstat(stream.fileno())
                            digest = hashlib.file_digest(stream, "sha256").hexdigest()
                            after = os.fstat(stream.fileno())
                    signature = lambda s: (
                        s.st_dev,
                        s.st_ino,
                        s.st_size,
                        s.st_mtime_ns,
                        s.st_ctime_ns,
                    )
                    stable = (
                        signature(info)
                        == signature(before)
                        == signature(after)
                        == signature(path.lstat())
                    )
                    row.update(
                        kind="file",
                        bytes=after.st_size,
                        mtime_ns=after.st_mtime_ns,
                        sha256=digest if stable else None,
                        status="observed" if stable else "changing",
                    )
                else:
                    row.update(kind="special", status="not-read")
            except (OSError, ValueError) as exc:
                row.update(status="unreadable", error=str(exc))
            rows.append(row)
    return sorted(rows, key=lambda row: row["path"])


def project_file_manifest(root: Path, path_mapping: list[dict]) -> list[dict]:
    """Inventory every known legacy workspace layout using project-relative paths."""
    rows: dict[tuple[str, str], dict] = {}

    def add(directory: Path, prefix: str, layout: str) -> None:
        for raw in file_manifest(directory):
            item = dict(raw)
            item["path"] = f"{prefix.rstrip('/')}/{raw['path']}"
            item["layout"] = layout
            rows[(item["path"], layout)] = item

    add(root / ".research", ".research", "historical-metadata")
    add(root / "workspaces", "workspaces", "project-root-workspaces")
    for mapping in path_mapping:
        relative = mapping.get("relative")
        if not relative or mapping.get("external"):
            continue
        if relative == ".research" or relative.startswith(".research/"):
            continue
        if relative == "workspaces" or relative.startswith("workspaces/"):
            continue
        candidate = root / relative
        if candidate.is_dir():
            add(candidate, relative, f"registered-{mapping['field']}")
        elif candidate.exists():
            parent_rows = file_manifest(candidate.parent)
            for raw in parent_rows:
                if raw["path"] == candidate.name:
                    item = dict(raw)
                    item["path"] = relative
                    item["layout"] = f"registered-{mapping['field']}"
                    rows[(relative, item["layout"])] = item
    return sorted(rows.values(), key=lambda row: (row["path"], row["layout"]))


def _json(value, fallback):
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return fallback


def preflight(root: str | Path, *, files: bool = True) -> dict:
    root = Path(root).expanduser().resolve()
    with readonly_database(root) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        tables = [
            r[0]
            for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        ]
        if version != 1:
            raise ValueError(f"Unsupported preflight schema {version}; expected legacy schema 1")
        project = db.execute("SELECT * FROM project WHERE id=1").fetchone()
        if project is None:
            raise ValueError("Project is not initialized")
        project = dict(project)
        config = _json(project.pop("config"), {})
        attempts = []
        for row in db.execute("SELECT * FROM attempts ORDER BY created_at,id"):
            item = dict(row)
            fields = _json(item.pop("fields"), {})
            item = {**fields, **item}
            item["legacy_session_id"] = item.get("session_id")
            item["native_session"] = None
            item["trajectory_status"] = "legacy-not-persisted-or-unverified"
            item["usage"] = {
                "value": item["cost"],
                "unit": "tokens",
                "source": "legacy_backend_reported",
                "cost_kind": item["cost_kind"],
                "completeness": (
                    "unknown"
                    if item["cost"] is None or item["cost_kind"] == "unknown"
                    else "legacy-aggregate-only"
                ),
            }
            attempts.append(item)
        nodes = []
        for row in db.execute("SELECT * FROM nodes ORDER BY created_at,id"):
            item = dict(row)
            item["spec"] = _json(item["spec"], {})
            item["result"] = _json(item["result"], None)
            nodes.append(item)
        counts = {
            name: db.execute('SELECT count(*) FROM "' + name.replace('"', '""') + '"').fetchone()[0]
            for name in tables
        }
        refs = [dict(row) for row in db.execute("SELECT * FROM published_items ORDER BY ref")]
        integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
        foreign_keys = [tuple(row) for row in db.execute("PRAGMA foreign_key_check")]
    paths = []
    for attempt in attempts:
        for key in ("workspace", "job_dir"):
            value = attempt.get(key)
            if value:
                path = Path(value)
                if not path.is_absolute():
                    path = root / path
                inside = path.is_relative_to(root)
                paths.append(
                    {
                        "attempt_id": attempt["id"],
                        "field": key,
                        "original": value,
                        "relative": str(path.relative_to(root)) if inside else None,
                        "external": not inside,
                        "exists": path.exists(),
                    }
                )
    manifest = project_file_manifest(root, paths) if files else None
    # Never infer native bindings, publications, or per-request usage from legacy IDs.
    return {
        "format": "ari-migration-preflight-v1",
        "observed_at": now(),
        "root": str(root),
        "schema_version": version,
        "project": project,
        "control": config.get("control", "unknown"),
        "nodes": nodes,
        "attempts": attempts,
        "counts": counts,
        "legacy_refs": refs,
        "budget": {
            "unit": "tokens",
            "known_spent": sum(a["cost"] or 0 for a in attempts),
            "hold": sum(a["hold"] for a in attempts),
            "unknown_attempts": [
                a["id"] for a in attempts if a["cost"] is None or a["cost_kind"] == "unknown"
            ],
            "source": "legacy_backend_reported",
        },
        "active_or_unknown": [
            a["id"] for a in attempts if a["state"] in {"reserved", "running", "unknown"}
        ],
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "external_dependencies": [
            {"path": str(p), "available": Path(p).expanduser().exists()}
            for p in config.get("resources", [])
        ],
        "path_mapping": paths,
        "files": manifest,
        "file_consistency": "individual-observations; no-frozen-window",
        "execution_enabled": False,
        "migration": {
            "mode": "dry-run",
            "target_schema": 2,
            "status": "copy-migration-available",
            "preserve": [
                "attempt IDs/states/costs/holds/times",
                "requests",
                "events",
                "published_items",
                "workspace bytes",
            ],
            "add_tables": [
                "migration_history",
                "project_identity",
                "session_bindings",
                "commands",
                "execution_observations",
                "process_observations",
                "usage_observations",
                "publications",
                "publication_items",
                "snapshots",
                "relations",
            ],
            "no_native_bindings_inferred": True,
        },
    }


def recovery_preview(root: str | Path, attempt_id: str) -> dict:
    report = preflight(root)
    attempt = next((a for a in report["attempts"] if a["id"] == attempt_id), None)
    if attempt is None:
        raise ValueError(f"Unknown attempt: {attempt_id}")
    mapping = next(
        (
            p
            for p in report["path_mapping"]
            if p["attempt_id"] == attempt_id and p["field"] == "workspace"
        ),
        None,
    )
    prefix = mapping["relative"].rstrip("/") + "/" if mapping and mapping["relative"] else None
    return {
        "format": "ari-recovery-preview-v1",
        "observed_at": report["observed_at"],
        "goal": report["project"]["goal"],
        "attempt": attempt,
        "files": [f for f in report["files"] if prefix and f["path"].startswith(prefix)],
        "workspace_mapping": mapping,
        "published_refs": [r for r in report["legacy_refs"] if r["node_id"] == attempt["node_id"]],
        "can_start": False,
        "reason": (
            "Preview only; source process termination and usage ownership require verification"
        ),
        "process_status": "unverified",
        "creates_attempt": False,
    }


def _max_counter(values: list[str], prefix: str) -> int:
    result = 0
    for value in values:
        if value.startswith(prefix + "-"):
            try:
                result = max(result, int(value.split("-", 1)[1]))
            except ValueError:
                pass
    return result


def migrate_copy(source: str | Path, destination: str | Path) -> dict:
    """Copy a schema-1 project and import it into schema 2 without mutating source."""
    source = Path(source).expanduser().resolve(strict=True)
    destination = Path(destination).expanduser().resolve()
    if destination.is_relative_to(source):
        raise ValueError("Migration destination must be outside the source project")
    marker = destination / ".research" / "migration-schema1-to-2.json"
    if marker.is_file():
        previous = json.loads(marker.read_text())
        if previous.get("source_root") != str(source):
            raise ValueError("Destination already contains a migration from another source")
        state = NativeStore(destination).query()
        return {"status": "already-migrated", "marker": previous, "state": state}
    if destination.exists():
        raise ValueError("Migration destination must be absent or a prior matching migration")

    temporary = destination.parent / f".{destination.name}.migration-{uuid4().hex}"
    temporary.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            source,
            temporary,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git"),
        )
        report = preflight(temporary)
        for mapping in report["path_mapping"]:
            original = Path(mapping["original"])
            if original.is_absolute() and original.is_relative_to(source):
                relative = original.relative_to(source)
                mapping.update(
                    relative=str(relative),
                    external=False,
                    exists=(temporary / relative).exists(),
                )
        report["files"] = project_file_manifest(temporary, report["path_mapping"])
        metadata = temporary / ".research"
        metadata.mkdir(parents=True, exist_ok=True)
        legacy_db = metadata / "legacy-schema1.sqlite3"
        with readonly_database(temporary) as source_db, sqlite3.connect(legacy_db) as target_db:
            source_db.backup(target_db)
        state_db = metadata / "state.sqlite3"
        if state_db.exists():
            state_db.unlink()
        store = NativeStore(temporary)
        project = store.initialize(
            report["project"]["goal"],
            "migration:initialize",
        )
        imported_at = now()
        with sqlite3.connect(legacy_db) as legacy, store._connection() as db:
            legacy.row_factory = sqlite3.Row
            db.execute("BEGIN IMMEDIATE")
            try:
                node_ids = []
                for row in legacy.execute("SELECT * FROM nodes ORDER BY created_at,id"):
                    node_ids.append(row["id"])
                    spec = _json_load(row["spec"], {})
                    inputs = []
                    for item in spec.get("inputs", []):
                        if isinstance(item, str):
                            inputs.append(item)
                        elif isinstance(item, dict) and isinstance(item.get("ref"), str):
                            inputs.append(item["ref"])
                    db.execute(
                        "INSERT INTO nodes "
                        "(node_id,question,why_now,plan,inputs,purpose,strategy,anchor_ref,"
                        "status,created_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            row["id"],
                            spec.get("question") or spec.get("title") or row["id"],
                            spec.get("why_now") or "Imported from schema 1",
                            spec.get("plan") or "Continue from imported material",
                            _dump_json(inputs),
                            spec.get("purpose") or "legacy-import",
                            "continue",
                            None,
                            row["status"],
                            row["created_at"],
                            row["closed_at"],
                        ),
                    )
                attempt_ids = []
                for row in legacy.execute("SELECT * FROM attempts ORDER BY created_at,id"):
                    attempt_ids.append(row["id"])
                    fields = _json_load(row["fields"], {})
                    terminal = row["state"] in {"completed", "failed", "interrupted"}
                    state = (
                        "finished" if row["state"] in {"completed", "failed"}
                        else "stopped" if row["state"] == "interrupted"
                        else "unknown"
                    )
                    details = {
                        "legacy_state": row["state"],
                        "legacy_estimate": row["estimate"],
                        "legacy_hold": row["hold"],
                        "legacy_cost_kind": row["cost_kind"],
                        "legacy_fields": fields,
                        "native_session": None,
                    }
                    db.execute(
                        "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            row["id"], None, row["node_id"], row["role"], state, "manual",
                            row["created_at"], row["updated_at"] if terminal else None,
                            _dump_json(details),
                        ),
                    )
                    completeness = (
                        "unknown"
                        if row["cost"] is None or row["cost_kind"] == "unknown"
                        else "legacy-aggregate-only"
                    )
                    db.execute(
                        "INSERT INTO usage_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            str(uuid4()), f"legacy:{project['project_id']}:{row['id']}",
                            None, row["id"], row["node_id"], "legacy-attempt", None, None,
                            row["cost"], completeness,
                            _dump_json(
                                {"source": "schema1.attempt.cost", "cost_kind": row["cost_kind"]}
                            ),
                            imported_at,
                        ),
                    )
                for row in legacy.execute("SELECT * FROM published_items ORDER BY ref"):
                    db.execute(
                        "INSERT INTO legacy_refs VALUES (?,?,?,?,?,?)",
                        (
                            row["ref"], row["node_id"], row["local_id"], row["kind"],
                            row["item"], imported_at,
                        ),
                    )
                for name, value in {
                    "node": _max_counter(node_ids, "X"),
                    "attempt": _max_counter(attempt_ids, "A"),
                }.items():
                    db.execute(
                        "INSERT INTO counters(name,value) VALUES (?,?) "
                        "ON CONFLICT(name) DO UPDATE SET value=max(value,excluded.value)",
                        (name, value),
                    )
                db.commit()
            except BaseException:
                db.rollback()
                raise
        source_db_digest = hashlib.sha256(legacy_db.read_bytes()).hexdigest()
        marker_value = {
            "format": "ari-schema1-copy-migration-v1",
            "source_root": str(source),
            "source_database_sha256": source_db_digest,
            "migrated_at": imported_at,
            "project_id": project["project_id"],
            "legacy_known_usage": report["budget"]["known_spent"],
            "legacy_unknown_attempts": report["budget"]["unknown_attempts"],
            "file_manifest": report["files"],
            "source_preserved": True,
        }
        (metadata / "migration-schema1-to-2.json").write_text(
            json.dumps(marker_value, ensure_ascii=False, indent=2)
        )
        os.replace(temporary, destination)
        return {
            "status": "migrated-copy",
            "destination": str(destination),
            "marker": marker_value,
            "state": NativeStore(destination).query(),
        }
    except BaseException:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def _json_load(value: object, fallback: object) -> object:
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return fallback
