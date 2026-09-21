"""Schema-7 epistemic tables and the transactional schema-6 upgrade.

Implements the storage half of ``docs/067/A0_SCHEMA.md``: the materialised
knowledge support edges (rule R2), change events, scope revisions, impact
records, dispositions and publication check snapshots.

Derived values are deliberately absent from these tables. ``needs_action``,
``residual_use_risk``, ``in_use`` and ``scope_unconfirmed`` are computed at
query time against a read boundary, because their correct value changes with
that boundary (rules R8, R18, R20).
"""

from __future__ import annotations

from pathlib import Path
import sqlite3

from .errors import ValidationError
from .memory_store import support_refs, write_support_edge


SCHEMA7_DDL = """
CREATE TABLE IF NOT EXISTS knowledge_support_edges (
 user_ref TEXT NOT NULL, used_ref TEXT NOT NULL,
 from_dependencies INTEGER NOT NULL DEFAULT 0,
 from_evidence INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 PRIMARY KEY(user_ref,used_ref),
 CHECK(from_dependencies + from_evidence > 0)
);
CREATE INDEX IF NOT EXISTS idx_support_used ON knowledge_support_edges(used_ref);
CREATE TABLE IF NOT EXISTS knowledge_changes (
 change_id TEXT PRIMARY KEY,
 knowledge_id TEXT NOT NULL REFERENCES knowledge_entries(knowledge_id),
 new_revision INTEGER NOT NULL, kind TEXT NOT NULL,
 affected_scope_mode TEXT NOT NULL, affected_scope TEXT NOT NULL,
 reason TEXT NOT NULL, operation_id TEXT NOT NULL,
 created_at TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 UNIQUE(knowledge_id,new_revision),
 CHECK(kind IN ('retract','correct','narrow','reword')),
 CHECK(affected_scope_mode IN ('versions','none','unknown')),
 CHECK(affected_scope_mode = 'versions' OR affected_scope = '[]')
);
CREATE TABLE IF NOT EXISTS knowledge_scope_revisions (
 scope_revision_id TEXT PRIMARY KEY,
 change_id TEXT NOT NULL REFERENCES knowledge_changes(change_id),
 affected_scope_mode TEXT NOT NULL, affected_scope TEXT NOT NULL,
 reason TEXT NOT NULL, operation_id TEXT NOT NULL,
 created_at TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 CHECK(affected_scope_mode IN ('versions','none','unknown'))
);
CREATE INDEX IF NOT EXISTS idx_scope_rev_change
 ON knowledge_scope_revisions(change_id,source_sequence);
CREATE TABLE IF NOT EXISTS knowledge_impacts (
 impact_id TEXT PRIMARY KEY,
 change_id TEXT NOT NULL REFERENCES knowledge_changes(change_id),
 affected_version TEXT NOT NULL, hop INTEGER NOT NULL,
 edge_source TEXT NOT NULL, review_state TEXT NOT NULL DEFAULT 'pending',
 disposition_ref TEXT,
 voided_by_scope_revision TEXT REFERENCES knowledge_scope_revisions(scope_revision_id),
 detected_at TEXT NOT NULL, detected_sequence INTEGER NOT NULL,
 UNIQUE(change_id,affected_version),
 CHECK(review_state IN ('pending','running','proposal_ready')),
 CHECK(edge_source IN ('dependencies','evidence_refs','both','root'))
);
CREATE INDEX IF NOT EXISTS idx_impact_version ON knowledge_impacts(affected_version);
CREATE TABLE IF NOT EXISTS knowledge_dispositions (
 disposition_id TEXT PRIMARY KEY,
 change_id TEXT NOT NULL, affected_version TEXT NOT NULL,
 kind TEXT NOT NULL, reason TEXT NOT NULL,
 evidence_refs TEXT NOT NULL DEFAULT '[]', replacement_ref TEXT,
 author TEXT NOT NULL, operation_id TEXT NOT NULL,
 created_at TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 FOREIGN KEY(change_id,affected_version)
  REFERENCES knowledge_impacts(change_id,affected_version),
 CHECK(kind IN ('unresolved','retained_with_evidence','revised','retracted'))
);
CREATE INDEX IF NOT EXISTS idx_disp_key
 ON knowledge_dispositions(change_id,affected_version,source_sequence);
CREATE INDEX IF NOT EXISTS idx_pubknow_version
 ON publication_knowledge(knowledge_id,revision);
CREATE TABLE IF NOT EXISTS publication_checks (
 check_id TEXT PRIMARY KEY,
 publication_id TEXT NOT NULL REFERENCES publications(publication_id),
 sequence_bound INTEGER NOT NULL, check_scope TEXT NOT NULL, result TEXT NOT NULL,
 targets_complete INTEGER NOT NULL, targets_truncated INTEGER NOT NULL,
 paths_truncated INTEGER NOT NULL, created_at TEXT NOT NULL,
 UNIQUE(publication_id,sequence_bound)
);
"""


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return bool(
        db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    )


# Statements that touch a table this module does not itself create. Partially
# built schema-2 databases reach the migration chain before ``_create_schema``
# has laid down the base tables, so these are skipped when their prerequisite
# is absent; ``_create_schema`` calls this module again once everything exists.
_DEPENDENT_STATEMENTS = {"idx_pubknow_version": "publication_knowledge"}


def _execute(db: sqlite3.Connection, schema: str) -> None:
    for statement in schema.split(";"):
        statement = statement.strip()
        if not statement:
            continue
        required = next(
            (table for key, table in _DEPENDENT_STATEMENTS.items() if key in statement), None
        )
        if required and not _has_table(db, required):
            continue
        db.execute(statement)


def ensure_schema7_columns(db: sqlite3.Connection) -> None:
    """Add the two additive columns. Callers must also use explicit column
    names in every ``knowledge_revisions`` INSERT: the historical statements
    are positional with thirteen placeholders and break on the new column."""
    for table, column, definition in (
        ("knowledge_revisions", "motivated_by", "TEXT NOT NULL DEFAULT '[]'"),
        ("relations", "operation_id", "TEXT"),
    ):
        if not _has_table(db, table):
            continue
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def rebuild_support_edges(db: sqlite3.Connection) -> int:
    """Recompute every support edge from the revisions themselves.

    Used by the migration backfill and by offline repair. Returns the edge
    count. On a ledger whose references are all whole snapshots this is
    legitimately zero: the backfill never infers an edge from prose.
    """
    if not _has_table(db, "knowledge_revisions"):
        return 0
    db.execute("DELETE FROM knowledge_support_edges")
    # Positional unpacking on purpose: this runs during migration and from the
    # offline repair path, where the caller's row_factory is not ours to assume.
    rows = db.execute(
        "SELECT knowledge_id,revision,dependencies,evidence_refs,created_at,source_sequence"
        " FROM knowledge_revisions"
    ).fetchall()
    total = 0
    for knowledge_id, revision, dependencies, evidence, created_at, sequence in rows:
        user_ref = f"knowledge/{knowledge_id}@{revision}"
        for used_ref, flags in support_refs(dependencies, evidence).items():
            write_support_edge(db, user_ref, used_ref, flags, created_at, int(sequence or 0))
            total += 1
    return total


def migrate_schema7(path: Path) -> None:
    with sqlite3.connect(path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 7:
            return
        if version != 6:
            raise ValidationError(f"Expected schema 6, found {version}")
        backup = path.parent / "schema-6-backup.sqlite3"
        if not backup.exists():
            temporary = backup.with_suffix(".tmp")
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.replace(backup)
        db.execute("BEGIN IMMEDIATE")
        try:
            ensure_schema7_columns(db)
            _execute(db, SCHEMA7_DDL)
            rebuild_support_edges(db)
            db.execute("PRAGMA user_version=7")
            db.execute("COMMIT")
        except BaseException:
            db.execute("ROLLBACK")
            raise
