"""Local operator CLI. Workers use scoped files, never this privileged interface."""

import argparse
import json
from pathlib import Path
import shutil
import sys
import time

from . import __version__
from .errors import ResearchError
from .io import read_json
from .runtime import Runtime, backup, check, export_views, validate_config
from .store import Store
from .visualization import serve_view


def parser() -> argparse.ArgumentParser:
    app = argparse.ArgumentParser(prog="ari", description="Persistent graph research")
    app.add_argument("--version", action="version", version=__version__)
    app.add_argument("-p", "--project", type=Path, default=Path.cwd())
    app.add_argument("--json", action="store_true", help="Machine-readable output")
    commands = app.add_subparsers(dest="command", required=True)
    migration = commands.add_parser(
        "migration", help="Read-only migration preflight and recovery preview"
    )
    migration.add_argument(
        "action", choices=["preflight", "dry-run", "recovery-preview", "migrate-copy"]
    )
    migration.add_argument("--attempt")
    migration.add_argument("--destination", type=Path)
    migration.add_argument("--no-files", action="store_true")
    gui = commands.add_parser("gui", help="Open the graphical research workbench")
    gui.add_argument("--workspace", type=Path, default=Path.home() / "work" / "research")
    gui.add_argument("--port", type=int, default=0)
    gui.add_argument("--no-open", action="store_true")
    init = commands.add_parser("init", help="Initialize without an evaluator")
    init.add_argument("--goal", required=True)
    init.add_argument("--questions", type=Path, help="JSON list of {id,text}")
    init.add_argument("--budget", type=float, default=100000, help="Token accounting cap")
    init.add_argument("--concurrency", type=int, default=2)
    init.add_argument("--estimate", type=float, default=5000)
    init.add_argument("--timeout", type=float, default=900)
    init.add_argument("--resource", type=Path, action="append", default=[])
    init.add_argument("--source-git", type=Path)
    init.add_argument(
        "--backend-command", type=Path, help="JSON argv array; optional operator adapter"
    )
    init.add_argument("--dsh-config", type=Path)
    for name in ("start", "resume"):
        run = commands.add_parser(name)
        run.add_argument("--max-cycles", type=int)
        run.add_argument("--poll", type=float, default=0.25)
    for name in ("status", "pause", "stop", "check", "export"):
        commands.add_parser(name)
    view = commands.add_parser("view", help="Open the interactive, read-only research map")
    view.add_argument("--port", type=int, default=0, help="Local port; 0 chooses an available port")
    view.add_argument(
        "--no-open", action="store_true", help="Print the URL without opening a browser"
    )
    show = commands.add_parser("show", help="Node or published ref")
    show.add_argument("ref")
    propose = commands.add_parser("propose", help="Submit a bounded exploration")
    propose.add_argument("file", type=Path)
    propose.add_argument("--request-id")
    steer = commands.add_parser("steer", help="Version agenda/notes and update limits")
    steer.add_argument("--questions", type=Path)
    steer.add_argument("--agenda", type=Path)
    steer.add_argument("--notes", type=Path)
    steer.add_argument("--budget", type=float)
    steer.add_argument("--concurrency", type=int)
    steer.add_argument("--coordinator-calls", type=int)
    steer.add_argument("--estimate", type=float)
    steer.add_argument("--config", type=Path, help="JSON configuration updates")
    reconcile = commands.add_parser(
        "reconcile", help="Inspect jobs or confirm a stopped unknown job"
    )
    reconcile.add_argument("--attempt")
    reconcile.add_argument("--confirm-stopped", action="store_true")
    reconcile.add_argument("--cost", type=float)
    reconcile.add_argument(
        "--cost-kind", choices=["actual", "estimated", "unknown"], default="unknown"
    )
    settle = commands.add_parser("settle", help="Account for a terminal attempt with missing usage")
    settle.add_argument("attempt")
    settle.add_argument("--cost", type=float, required=True)
    settle.add_argument("--cost-kind", choices=["actual", "estimated"], required=True)
    save = commands.add_parser("backup")
    save.add_argument("destination", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("source", type=Path)
    return app


def _json_file(path: Path):
    return json.loads(path.read_text())


def execute(args):
    if args.command == "migration":
        from .migration import migrate_copy, preflight, recovery_preview
        from .service import redact

        if args.action == "recovery-preview":
            if not args.attempt:
                raise ValueError("recovery-preview requires --attempt")
            return redact(recovery_preview(args.project, args.attempt))
        if args.action == "migrate-copy":
            if not args.destination:
                raise ValueError("migrate-copy requires --destination")
            return redact(migrate_copy(args.project, args.destination))
        return redact(preflight(args.project, files=not args.no_files))
    if args.command == "gui":
        from .gui import serve_gui

        serve_gui(args.workspace, port=args.port, open_browser=not args.no_open)
        return None
    root = args.project.expanduser().resolve()
    if args.command == "restore":
        if root.exists():
            raise ValueError("Restore requires a new destination project directory")
        if not (args.source / ".research" / "state.sqlite3").is_file():
            raise ValueError("Source is not a research backup")
        shutil.copytree(args.source, root, symlinks=True)
        store = Store(root)
        original = Path(
            read_json(args.source / ".research" / "backup.json", {}).get(
                "source_root", str(args.source.resolve())
            )
        )
        for attempt in store.attempts():
            updates = {}
            for key in ("workspace", "job_dir"):
                value = attempt.get(key)
                if value and Path(value).is_relative_to(original):
                    updates[key] = str(root / Path(value).relative_to(original))
            if updates:
                store.set_attempt(attempt["id"], **updates)
        store.configure(control="paused")
        return check(root)
    if args.command != "init" and not (root / ".research" / "state.sqlite3").is_file():
        raise ValueError("Project is not initialized; use ari -p PATH init --goal ...")
    store = Store(root)
    if args.command == "init":
        if args.concurrency < 1 or args.estimate < 0 or args.timeout <= 0:
            raise ValueError("concurrency/timeout must be positive, estimate nonnegative")
        resources = [str(p.expanduser().resolve(strict=True)) for p in args.resource]
        config = {
            "concurrency": args.concurrency,
            "worker_estimate": args.estimate,
            "coordinator_estimate": args.estimate,
            "timeout_s": args.timeout,
            "resources": resources,
            "max_coordinator_calls": 20,
        }
        if args.source_git:
            config["source_git"] = str(args.source_git.expanduser().resolve(strict=True))
        if args.backend_command:
            config["backend_command"] = _json_file(args.backend_command)
        if args.dsh_config:
            config["dsh_config"] = str(args.dsh_config.expanduser().resolve(strict=True))
        validate_config(config)
        store.initialize(
            args.goal,
            _json_file(args.questions) if args.questions else None,
            budget=args.budget,
            config=config,
        )
        export_views(store)
        return store.snapshot()
    runtime = Runtime(root)
    if args.command in {"start", "resume"}:
        if args.poll <= 0 or (args.max_cycles is not None and args.max_cycles < 1):
            raise ValueError("poll and max-cycles must be positive")
        return runtime.start(args.poll, args.max_cycles)
    if args.command == "status":
        return store.snapshot()
    if args.command == "view":
        serve_view(store, port=args.port, open_browser=not args.no_open)
        return None
    if args.command == "show":
        return store.resolve(args.ref) if "/result#" in args.ref else store.get_node(args.ref)
    if args.command == "propose":
        result = store.propose(_json_file(args.file), request_id=args.request_id)
        export_views(store)
        return result
    if args.command == "pause":
        store.configure(control="paused", idle_reason="Paused by user; running jobs may finish")
        return store.snapshot()
    if args.command == "stop":
        return runtime.stop()
    if args.command == "steer":
        if args.questions or args.agenda:
            store.agenda(
                text=args.agenda.read_text() if args.agenda else None,
                questions=_json_file(args.questions) if args.questions else None,
            )
        if args.notes:
            store.notes(args.notes.read_text())
        updates = _json_file(args.config) if args.config else {}
        if args.concurrency is not None:
            if args.concurrency < 1:
                raise ValueError("concurrency must be positive")
            updates["concurrency"] = args.concurrency
        if args.coordinator_calls is not None:
            if args.coordinator_calls < 0:
                raise ValueError("coordinator-calls must be nonnegative")
            updates["max_coordinator_calls"] = args.coordinator_calls
        if args.estimate is not None:
            if args.estimate < 0:
                raise ValueError("estimate must be nonnegative")
            updates.update(worker_estimate=args.estimate, coordinator_estimate=args.estimate)
        updates["steer_revision"] = time.time_ns()
        validate_config({**store.project()["config"], **updates})
        store.configure(config=updates, budget=args.budget)
        export_views(store)
        return store.snapshot()
    if args.command == "reconcile":
        if args.attempt:
            if not args.confirm_stopped:
                raise ValueError(
                    "Explicit --confirm-stopped is required to terminate an unknown attempt"
                )
            return runtime.confirm_stopped(args.attempt, args.cost, args.cost_kind)
        runtime.reconcile()
        return store.snapshot()
    if args.command == "settle":
        attempt = store.get_attempt(args.attempt)
        if attempt["state"] not in {"completed", "failed", "interrupted"}:
            raise ValueError(
                "Only terminal attempts can be settled here; use reconcile for uncertain jobs"
            )
        return store.settle(
            args.attempt, args.cost, cost_kind=args.cost_kind, state=attempt["state"]
        )
    if args.command == "export":
        directory = export_views(store)
        return {"path": str(directory), "graph": str(directory / "research-map.html")}
    if args.command == "backup":
        backup(root, args.destination)
        return {"backup": str(args.destination.resolve())}
    if args.command == "check":
        return check(root)
    raise ValueError("Unknown command")


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        result = execute(args)
    except (ResearchError, ValueError, RuntimeError, OSError, StopIteration) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
    if result is None:
        return
    if (
        args.command == "migration"
        or args.json
        or not isinstance(result, dict)
        or "project" not in result
    ):
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        config = result["project"]["config"]
        print(f"Project: {args.project.resolve()}")
        print(f"Goal: {result['project']['goal']}")
        print(f"State: {config.get('control')}  {config.get('idle_reason', '')}")
        for node in result["nodes"]:
            print(f"  {node['id']}  {node['status']:<8} {node['question']}  {node['why_now']}")
        print("Budget: " + json.dumps(result["budget"], ensure_ascii=False))
    if isinstance(result, dict) and result.get("ok") is False:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
