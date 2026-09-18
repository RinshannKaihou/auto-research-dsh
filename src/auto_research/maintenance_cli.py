"""Offline inventory, migration, validation, and export commands.

This entry point never starts a scheduler, worker, model client, or GUI.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import sys
from urllib.parse import quote

from . import __version__
from .errors import ResearchError
from .migration import migrate_copy, preflight, recovery_preview
from .native_store import NativeStore
from .service import redact


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(prog="ari", description="Offline research project maintenance")
    app.add_argument("--version", action="version", version=__version__)
    app.add_argument("-p", "--project", type=Path, default=Path.cwd())
    commands = app.add_subparsers(dest="command", required=True)

    migration = commands.add_parser("migration", help="Inspect or copy a legacy project")
    migration.add_argument(
        "action", choices=["preflight", "dry-run", "recovery-preview", "migrate-copy"]
    )
    migration.add_argument("--attempt")
    migration.add_argument("--destination", type=Path)
    migration.add_argument("--no-files", action="store_true")

    commands.add_parser("validate", help="Read-only SQLite integrity and schema validation")
    export = commands.add_parser("export", help="Export a schema-6 state view as JSON")
    export.add_argument("--output", type=Path, required=True)
    return app


def validate_project(root: Path) -> dict:
    root = root.expanduser().resolve()
    database = root / ".research" / "state.sqlite3"
    if not database.is_file():
        raise ValueError(f"Research database does not exist: {database}")
    uri = f"file:{quote(database.as_posix(), safe='/')}?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        db.execute("PRAGMA query_only=ON")
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        integrity = [row[0] for row in db.execute("PRAGMA integrity_check")]
        foreign_keys = [list(row) for row in db.execute("PRAGMA foreign_key_check")]
        tables = {}
        rows = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for row in rows:
            name = row[0]
            quoted = name.replace('"', '""')
            tables[name] = int(db.execute(f'SELECT count(*) FROM "{quoted}"').fetchone()[0])
    supported = version in {1, 2, 3, 4, 5, 6}
    return {
        "root": str(root),
        "schema_version": version,
        "supported": supported,
        "integrity": integrity,
        "foreign_key_errors": foreign_keys,
        "tables": tables,
        "ok": supported and integrity == ["ok"] and not foreign_keys,
    }


def execute(args: argparse.Namespace) -> dict:
    root = args.project.expanduser().resolve()
    if args.command == "validate":
        return validate_project(root)
    if args.command == "export":
        result = validate_project(root)
        if result["schema_version"] != 6 or not result["ok"]:
            raise ValueError("Export requires a valid schema-6 project")
        output = args.output.expanduser().resolve()
        if output.exists():
            raise ValueError("Export output must not already exist")
        output.parent.mkdir(parents=True, exist_ok=True)
        state = redact(NativeStore(root, readonly=True).query())
        output.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")
        return {"output": str(output), "schema_version": 6}
    if args.command == "migration":
        if args.action == "recovery-preview":
            if not args.attempt:
                raise ValueError("recovery-preview requires --attempt")
            return redact(recovery_preview(root, args.attempt))
        if args.action == "migrate-copy":
            if not args.destination:
                raise ValueError("migrate-copy requires --destination")
            return redact(migrate_copy(root, args.destination))
        return redact(preflight(root, files=not args.no_files))
    raise ValueError(f"Unknown offline command: {args.command}")


def main(argv: list[str] | None = None) -> None:
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ResearchError, ValueError, RuntimeError, OSError, sqlite3.Error) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2) from error
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
