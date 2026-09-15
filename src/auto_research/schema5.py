"""Schema-5 protocol ledgers and transactional schema-4 upgrade."""

from __future__ import annotations

from pathlib import Path
import sqlite3
from uuid import uuid4

from .errors import ValidationError


SCHEMA5_DDL = """
CREATE TABLE IF NOT EXISTS node_dependencies (
 dependency_id TEXT PRIMARY KEY,
 predecessor_node_id TEXT NOT NULL REFERENCES nodes(node_id),
 successor_node_id TEXT NOT NULL REFERENCES nodes(node_id),
 relation_type TEXT NOT NULL,
 rationale TEXT NOT NULL, input_refs TEXT NOT NULL,
 scheduling INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
 UNIQUE(predecessor_node_id,successor_node_id,relation_type)
);
CREATE INDEX IF NOT EXISTS node_dependencies_successor
 ON node_dependencies(successor_node_id,relation_type);
CREATE TABLE IF NOT EXISTS material_consumptions (
 consumption_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
 node_id TEXT NOT NULL REFERENCES nodes(node_id),
 attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
 association_id TEXT NOT NULL REFERENCES associations(association_id),
 source_ref TEXT NOT NULL, use_text TEXT NOT NULL,
 relation_type TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS material_consumptions_node
 ON material_consumptions(node_id,created_at);
CREATE TABLE IF NOT EXISTS usage_coverage_gaps (
 gap_id TEXT PRIMARY KEY, source_key TEXT UNIQUE NOT NULL,
 session_id TEXT, purpose TEXT NOT NULL, reason TEXT NOT NULL,
 details TEXT NOT NULL, state TEXT NOT NULL,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def _execute(db: sqlite3.Connection, schema: str) -> None:
    for statement in schema.split(";"):
        if statement.strip():
            db.execute(statement)


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}


def ensure_schema5_columns(db: sqlite3.Connection) -> None:
    node_columns = _columns(db, "nodes")
    if "origin_kind" not in node_columns:
        db.execute("ALTER TABLE nodes ADD COLUMN origin_kind TEXT")
    if "root_reason" not in node_columns:
        db.execute("ALTER TABLE nodes ADD COLUMN root_reason TEXT")

    relation_columns = _columns(db, "relations")
    if relation_columns and "relation_type" not in relation_columns:
        db.execute("ALTER TABLE relations ADD COLUMN relation_type TEXT NOT NULL DEFAULT 'legacy'")
    if relation_columns and "scheduling" not in relation_columns:
        db.execute("ALTER TABLE relations ADD COLUMN scheduling INTEGER NOT NULL DEFAULT 0")

    goal_columns = _columns(db, "owned_goals")
    if goal_columns and "activation" not in goal_columns:
        db.execute("ALTER TABLE owned_goals ADD COLUMN activation TEXT")
    if goal_columns and "change_reason" not in goal_columns:
        db.execute("ALTER TABLE owned_goals ADD COLUMN change_reason TEXT")
    if goal_columns and "source_sequence" not in goal_columns:
        db.execute("ALTER TABLE owned_goals ADD COLUMN source_sequence INTEGER")

    workflow_columns = _columns(db, "workflow_sessions")
    if workflow_columns and "close_state" not in workflow_columns:
        db.execute("ALTER TABLE workflow_sessions ADD COLUMN close_state TEXT")
    if workflow_columns and "close_attempt_id" not in workflow_columns:
        db.execute("ALTER TABLE workflow_sessions ADD COLUMN close_attempt_id TEXT")
    if workflow_columns and "close_reason" not in workflow_columns:
        db.execute("ALTER TABLE workflow_sessions ADD COLUMN close_reason TEXT")


def migrate_schema5(path: Path) -> None:
    """Back up schema 4, then add explicit protocol state without inventing provenance."""
    with sqlite3.connect(path, isolation_level=None) as db:
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 5:
            return
        if version != 4:
            raise ValidationError(f"Expected schema 4, found {version}")
        backup = path.parent / "schema-4-backup.sqlite3"
        if not backup.exists():
            temporary = path.parent / f"schema-4-backup-{uuid4().hex}.tmp"
            try:
                with sqlite3.connect(temporary) as target:
                    db.backup(target)
                temporary.replace(backup)
            finally:
                if temporary.exists():
                    temporary.unlink()
        db.execute("BEGIN IMMEDIATE")
        try:
            ensure_schema5_columns(db)
            _execute(db, SCHEMA5_DDL)
            db.execute(
                "UPDATE nodes SET origin_kind='legacy_unresolved' WHERE origin_kind IS NULL"
            )
            db.execute("PRAGMA user_version=5")
            if db.execute("PRAGMA foreign_key_check").fetchone():
                raise ValidationError("Schema 5 migration failed foreign-key validation")
            db.commit()
        except BaseException:
            db.rollback()
            raise
