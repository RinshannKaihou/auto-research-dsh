"""Schema 9: explicit field-check declarations and immutable evaluation rows."""
from pathlib import Path
import sqlite3

from .errors import ValidationError


FIELD_CHECK_SCHEMA = """
CREATE TABLE IF NOT EXISTS field_checks (
 target_ref TEXT NOT NULL, check_index INTEGER NOT NULL,
 spec TEXT NOT NULL, input_ref TEXT NOT NULL,
 object_version TEXT, input_sha256 TEXT, observed_text TEXT,
 tolerance_rule TEXT, checker_version TEXT NOT NULL,
 result TEXT NOT NULL, reason TEXT,
 created_at TEXT NOT NULL, source_sequence INTEGER NOT NULL,
 PRIMARY KEY(target_ref, check_index),
 CHECK(result IN ('consistent','inconsistent','not_checkable'))
)
"""


def ensure_schema9(db: sqlite3.Connection) -> None:
    columns = {row[1] for row in db.execute('PRAGMA table_info(knowledge_revisions)')}
    if columns and 'checks' not in columns:
        db.execute("ALTER TABLE knowledge_revisions ADD COLUMN checks TEXT NOT NULL DEFAULT '[]'")
    db.execute(FIELD_CHECK_SCHEMA)


def migrate_schema9(path: Path) -> None:
    with sqlite3.connect(path, isolation_level=None) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version == 9:
            return
        if version != 8:
            raise ValidationError(f'Expected schema 8, found {version}')
        busy, _, _ = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if busy:
            raise ValidationError('Cannot checkpoint schema 8 for backup')
        db.execute('BEGIN IMMEDIATE')
        try:
            backup = path.parent / 'schema-8-backup.sqlite3'
            try:
                with backup.open('xb') as target:
                    target.write(path.read_bytes())
            except FileExistsError:
                pass
            ensure_schema9(db)
            db.execute('PRAGMA user_version=9')
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
