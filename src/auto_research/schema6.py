"""One additive upgrade: persist specialist context policy across recovery."""
from pathlib import Path
import sqlite3


def migrate_schema6(path: Path) -> None:
    with sqlite3.connect(path) as db:
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 6:
            return
        if version != 5:
            raise ValueError(f"Expected schema 5, found {version}")
        backup = path.parent / "schema-5-backup.sqlite3"
        if not backup.exists():
            with sqlite3.connect(backup) as target:
                db.backup(target)
        with db:
            db.execute("BEGIN IMMEDIATE")
            if "context_mode" not in {r[1] for r in db.execute("PRAGMA table_info(specialist_tasks)")}:
                db.execute("ALTER TABLE specialist_tasks ADD COLUMN context_mode TEXT NOT NULL DEFAULT 'research'")
            db.execute("PRAGMA user_version=6")
