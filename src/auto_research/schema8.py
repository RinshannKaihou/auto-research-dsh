"""Add write-time attribution, retaining historical rows as JSON null."""
from pathlib import Path
import sqlite3

from .errors import ValidationError


def ensure_schema8_columns(db: sqlite3.Connection) -> None:
    for table, column, default in (
        ('knowledge_revisions', 'asserted_at', 'null'),
        ('knowledge_revisions', 'execution_refs', '[]'),
        ('relations', 'asserted_at', 'null'),
        ('publications', 'asserted_at', 'null'),
    ):
        columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        if columns and column not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT NOT NULL DEFAULT '{default}'")


def migrate_schema8(path: Path) -> None:
    with sqlite3.connect(path, isolation_level=None) as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        if version == 8:
            return
        if version != 7:
            raise ValidationError(f'Expected schema 7, found {version}')
        # A checkpoint makes a byte copy a complete database even with WAL.
        busy, _, _ = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if busy:
            raise ValidationError('Cannot checkpoint schema 7 for backup')
        db.execute('BEGIN IMMEDIATE')
        try:
            backup = path.parent / 'schema-7-backup.sqlite3'
            try:
                with backup.open('xb') as target:
                    target.write(path.read_bytes())
            except FileExistsError:
                pass
            ensure_schema8_columns(db)
            db.execute('PRAGMA user_version=8')
            db.execute('COMMIT')
        except BaseException:
            db.execute('ROLLBACK')
            raise
