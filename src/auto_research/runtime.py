"""Single-coordinator execution driver with durable, detached job supervisors."""

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from typing import Callable

from .artifacts import ArtifactStore
from .io import ProcessInspectionError, atomic_json, atomic_text, process_identity, read_json
from .prompts import COORDINATOR_SCHEMA, WORKER_SCHEMA, make_prompt
from .store import Store
from .visualization import export_graph


ACTIVE = {"reserved", "running", "unknown"}


def validate_config(config: dict) -> None:
    for key, default, minimum in (("concurrency", 2, 1), ("max_coordinator_calls", 20, 0)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise ValueError(f"{key} must be an integer >= {minimum}")
    for key, default in (
        ("worker_estimate", 5000),
        ("coordinator_estimate", 5000),
        ("timeout_s", 900),
    ):
        value = config.get(key, default)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or value < 0
        ):
            raise ValueError(f"{key} must be finite and nonnegative")
        if key == "timeout_s" and not 0 < value <= 86400:
            raise ValueError("timeout_s must be between 0 and 86400 seconds")


@contextmanager
def coordinator_lock(root: Path):
    path = root / ".research" / "coordinator.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError("This project already has a running coordinator") from error
        yield


def backend_command(config: dict) -> list[str]:
    if config.get("backend_command"):
        command = config["backend_command"]
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(x, str) for x in command)
        ):
            raise ValueError("backend_command must be a nonempty argv array")
        return command
    configured = config.get("dsh_worker") or os.environ.get("ARI_DSH_WORKER")
    candidates = (
        [Path(configured)]
        if configured
        else [
            Path(__file__).resolve().parents[2] / "apps" / "dsh" / "worker.mjs",
            Path(sys.prefix) / "share" / "auto-research" / "dsh" / "worker.mjs",
        ]
    )
    worker = next((p for p in candidates if p.is_file()), None)
    if worker is None:
        raise ValueError("DSH adapter not found; configure dsh_worker or ARI_DSH_WORKER")
    return [shutil.which("node") or "node", str(worker.resolve()), "{request}", "{output}"]


def export_views(store: Store) -> Path:
    root = store.root
    directory = root / ".research" / "views"
    state = store.snapshot()
    state["project"] = {k: v for k, v in state["project"].items() if k != "config"}
    atomic_json(directory / "index.json", state)
    lines = [
        "# Research status",
        "",
        str(state["project"].get("goal", "")),
        "",
        "| Node | Question | Status | Why now |",
        "|---|---|---|---|",
    ]
    for node in state["nodes"]:
        atomic_json(directory / f"{node['id']}.json", node)
        why = str(node.get("why_now", "")).replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {node['id']} | {node['question']} | {node['status']} | {why} |")
    lines += ["", "## Budget", "", "```json", json.dumps(state["budget"], indent=2), "```", ""]
    # The human view is generated; it is not a second source of truth.
    atomic_text(directory / "INDEX.md", "\n".join(lines))
    export_graph(store, directory, state=state)
    return directory


def _copy_progress(source: Path, destination: Path) -> None:
    """Carry private work forward without following links or copying Git internals."""
    if not source.is_dir():
        return
    for entry in source.iterdir():
        if entry.name in {".git", "inputs", "history", "prior-attempt"} or entry.is_symlink():
            continue
        target = destination / entry.name
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            _copy_progress(entry, target)
        elif entry.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(entry, target)


class Runtime:
    def __init__(self, root: Path | str):
        self.root = Path(root).resolve()
        self.store = Store(self.root)
        self.artifacts = ArtifactStore(self.root)
        self.jobs = self.root / ".research" / "jobs"
        self.children: dict[str, subprocess.Popen] = {}

    def config(self) -> dict:
        return self.store.project().get("config", {})

    def context(self, node: dict | None = None, attempt: dict | None = None) -> dict:
        snapshot = self.store.snapshot()
        if attempt:
            snapshot["agenda"] = self.store.agenda(version=attempt["agenda_version"])
            snapshot["notes"] = self.store.notes(version=attempt["notes_version"])
        # Full per-node material is queryable in views; avoid repeating every result in prompts.
        snapshot["nodes"] = [
            {
                "id": n["id"],
                "question": n["question"],
                "status": n["status"],
                "why_now": n.get("why_now"),
                "inputs": n.get("inputs", []),
                "result": n.get("result"),
            }
            for n in snapshot["nodes"]
        ]
        # Materials are part of research context, even though provider settings are not.
        snapshot["resources"] = list(snapshot["project"].get("config", {}).get("resources", []))
        # Backend configuration can name local configuration locations; agents do not need it.
        snapshot["project"] = {k: v for k, v in snapshot["project"].items() if k != "config"}
        snapshot["attempts"] = [
            {
                k: a.get(k)
                for k in ("id", "node_id", "role", "state", "progress", "cost", "cost_kind")
            }
            for a in snapshot["attempts"]
        ]
        snapshot["history_dir"] = str(self.root / ".research" / "views")
        if node:
            snapshot["current_node"] = node
            snapshot["resolved_inputs"] = [self.store.resolve(x["ref"]) for x in node["inputs"]]
        return snapshot

    def dispatch(self, node: dict | None, request_id: str | None = None) -> dict:
        config = self.config()
        validate_config(config)
        role = "worker" if node else "coordinator"
        command = backend_command(config)
        process_identity(
            os.getpid()
        )  # fail before reservation/spawn if the host cannot inspect jobs
        estimate = float(config.get("worker_estimate" if node else "coordinator_estimate", 5000))
        attempt = self.store.reserve(
            node["id"] if node else None, role=role, estimate=estimate, request_id=request_id
        )
        # Idempotency replays the original reservation response; inspect current state before spawn.
        attempt = self.store.get_attempt(attempt["id"])
        attempt_id = attempt["id"]
        if attempt.get("dispatch_prepared") or attempt["state"] != "reserved":
            return attempt
        job_dir = self.jobs / attempt_id
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            inputs = []
            copied = set()
            if node:
                for item in node["inputs"]:
                    resolved = self.store.resolve(item["ref"])
                    if resolved["kind"] == "product" and resolved["ref"] not in copied:
                        copied.add(resolved["ref"])
                        inputs.append(
                            {
                                "node_id": resolved["node_id"],
                                "product_id": resolved["id"],
                                "product": resolved["item"],
                            }
                        )
            workspace = self.artifacts.prepare_workspace(
                attempt_id,
                inputs,
                source_git=config.get("source_git")
                if node and node.get("modifies_artifact")
                else None,
            )
            earlier = [
                a
                for a in self.store.attempts()
                if node
                and a["node_id"] == node["id"]
                and a["id"] != attempt_id
                and a.get("workspace")
            ]
            if earlier:
                _copy_progress(Path(earlier[-1]["workspace"]), workspace / "prior-attempt")
            views = export_views(self.store)
            context = self.context(node, attempt)
            context["workspace"] = str(workspace)
            context["attempt_id"] = attempt_id
            request = {
                "attempt_id": attempt_id,
                "role": role,
                "workspace": str(workspace),
                "history_dir": str(views),
                "context": context,
                "prompt": make_prompt(role, context),
                "output_schema": WORKER_SCHEMA if node else COORDINATOR_SCHEMA,
                "timeout_s": float(config.get("timeout_s", 900)),
                "read_roots": [str(workspace), str(views), str(self.root / ".research" / "objects")]
                + config.get("resources", []),
                "write_roots": [str(workspace)],
                "readonly_roots": [str(workspace / "inputs"), str(workspace / "history")],
                "backend_command": command,
            }
            # Only explicit non-secret adapter settings cross the request boundary.
            if config.get("dsh_config"):
                request["dsh_config"] = config["dsh_config"]
            atomic_json(job_dir / "request.json", request)
            self.store.set_attempt(
                attempt_id,
                workspace=str(workspace),
                job_dir=str(job_dir),
                dispatch_prepared=True,
                dispatch_at=time.time(),
            )
            environment = dict(os.environ)
            package_source = str(Path(__file__).resolve().parent.parent)
            environment["PYTHONPATH"] = (
                package_source + os.pathsep + environment.get("PYTHONPATH", "")
            )
            log = (job_dir / "supervisor.log").open("ab")
            try:
                process = subprocess.Popen(
                    [sys.executable, "-m", "auto_research.job", str(job_dir)],
                    cwd=workspace,
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            finally:
                log.close()
            self.children[attempt_id] = process
            self.store.set_attempt(
                attempt_id, state="running", pid=process.pid, identity=process_identity(process.pid)
            )
        except Exception as error:
            # A prepared dispatch can have crossed the spawn boundary. Do not assume it cost zero.
            current = next(a for a in self.store.attempts() if a["id"] == attempt_id)
            if current.get("dispatch_prepared"):
                self.store.set_attempt(attempt_id, state="unknown", error=str(error))
            else:
                self.store.settle(attempt_id, 0.0, cost_kind="actual", state="failed")
                self.store.set_attempt(attempt_id, error=str(error))
            raise
        return next(a for a in self.store.attempts() if a["id"] == attempt_id)

    def _archive_result(self, attempt: dict, result: dict) -> dict:
        if not isinstance(result, dict):
            raise ValueError("worker result must be an object")
        workspace = Path(attempt["workspace"])
        frozen = []
        for product in result.get("products", []):
            relative = product.get("path", "")
            if not relative or Path(relative).is_absolute():
                raise ValueError("product paths must be relative to the private workspace")
            material = self.artifacts.freeze(workspace / relative, allowed_root=workspace)
            frozen.append({**product, **material})
        return {**result, "products": frozen}

    def collect(self, attempt: dict, state: dict) -> None:
        attempt_id = attempt["id"]
        output = read_json(self.jobs / attempt_id / "output.json", {})
        usage = output.get("usage") if isinstance(output, dict) else None
        cost = usage.get("tokens") if isinstance(usage, dict) else None
        if (
            isinstance(cost, bool)
            or not isinstance(cost, (int, float))
            or not math.isfinite(cost)
            or cost < 0
        ):
            cost = None
        terminal = state["phase"]
        error = state.get("reason")
        if terminal == "completed":
            try:
                result = output["result"]
                if attempt["role"] == "worker":
                    node = self.store.get_node(attempt["node_id"])
                    if node["status"] != "closed":
                        result = self._archive_result(attempt, result)
                        self.store.publish(node["id"], result, request_id=f"publish:{attempt_id}")
                else:
                    if not isinstance(result, dict) or not isinstance(
                        result.get("proposals"), list
                    ):
                        raise ValueError("coordinator must return proposals array")
                    if len(result["proposals"]) > 4:
                        raise ValueError("coordinator may propose at most four nodes per decision")
                    for index, proposal in enumerate(result["proposals"]):
                        self.store.propose(proposal, request_id=f"proposal:{attempt_id}:{index}")
                    if result.get("notes"):
                        self.store.notes(result["notes"], request_id=f"notes:{attempt_id}")
                    self.store.configure(coordinator_note=result.get("pause_reason", ""))
            except Exception as problem:
                terminal = "failed"
                error = f"Result validation failed: {problem}"
        self.store.settle(
            attempt_id, cost, cost_kind="actual" if cost is not None else "unknown", state=terminal
        )
        self.store.set_attempt(
            attempt_id,
            collected=True,
            error=error,
            session_id=output.get("session_id") if isinstance(output, dict) else None,
        )
        if terminal in {"failed", "interrupted"} and self.config().get("control") == "running":
            self.store.configure(control="paused", idle_reason=error or terminal)

    def reconcile(self) -> list[dict]:
        for attempt in self.store.attempts():
            if attempt["state"] not in ACTIVE or attempt.get("collected"):
                continue
            path = self.jobs / attempt["id"]
            state = read_json(path / "status.json", {})
            if state.get("terminal"):
                self.collect(attempt, state)
            elif state and state.get("identity") and self._confirmed_live(state):
                self.store.set_attempt(
                    attempt["id"], state="running", pid=state["pid"], identity=state["identity"]
                )
            elif not attempt.get("dispatch_prepared"):
                # The durable flag precedes spawn: an unprepared reservation did not invoke a model.
                self.store.settle(attempt["id"], 0.0, cost_kind="actual", state="interrupted")
            elif time.time() - attempt.get("dispatch_at", 0) > 10:
                self.store.set_attempt(
                    attempt["id"],
                    state="unknown",
                    error="Execution status cannot be confirmed; no automatic redispatch",
                )
            if attempt.get("workspace"):
                progress = read_json(Path(attempt["workspace"]) / "progress.json")
                if isinstance(progress, dict) and progress != attempt.get("progress"):
                    self.store.set_attempt(attempt["id"], progress=progress)
        for attempt_id, process in list(self.children.items()):
            if process.poll() is not None:
                process.wait()
                del self.children[attempt_id]
        export_views(self.store)
        return self.store.attempts()

    @staticmethod
    def _confirmed_live(state: dict) -> bool:
        try:
            return process_identity(state["pid"]) == state["identity"]
        except ProcessInspectionError:
            return False  # leads to unknown; never authorizes replay or settlement

    def _frontier_key(self) -> str:
        state = self.store.snapshot()
        value = {
            "closed": [n["id"] for n in state["nodes"] if n["status"] == "closed"],
            "agenda": state["agenda"],
            "steer": self.config().get("steer_revision", 0),
        }
        return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

    def tick(self) -> dict:
        attempts = self.reconcile()
        config = self.config()
        active = [a for a in attempts if a["state"] in ACTIVE]
        if config.get("control") != "running":
            return {"active": len(active), "dispatched": 0, "control": config.get("control")}
        if any(a["state"] == "unknown" for a in active):
            self.store.configure(control="paused", idle_reason="An execution needs reconciliation")
            return {"active": len(active), "dispatched": 0, "control": "paused"}
        concurrency = int(config.get("concurrency", 2))
        occupied = {a["node_id"] for a in active if a["role"] == "worker"}
        candidates = [
            n
            for n in self.store.list_nodes()
            if n["status"] in {"proposed", "open"} and n["id"] not in occupied
        ]
        dispatched = 0
        for node in candidates:
            if len(occupied) >= concurrency:
                break
            try:
                self.dispatch(node)
            except Exception as error:
                self.store.configure(control="paused", idle_reason=str(error))
                break
            occupied.add(node["id"])
            dispatched += 1
        config = self.config()
        if config.get("control") == "running" and not candidates and len(occupied) < concurrency:
            frontier = self._frontier_key()
            coordinator_active = any(a["role"] == "coordinator" for a in active)
            calls = sum(a["role"] == "coordinator" for a in attempts)
            if not coordinator_active and config.get("last_coordinator_key") != frontier:
                if calls >= int(config.get("max_coordinator_calls", 20)):
                    self.store.configure(
                        control="paused", idle_reason="Coordinator call limit reached"
                    )
                else:
                    try:
                        self.dispatch(
                            None, request_id=f"coordinate:{frontier}:{config.get('generation', 0)}"
                        )
                        self.store.configure(last_coordinator_key=frontier)
                        dispatched += 1
                    except Exception as error:
                        self.store.configure(control="paused", idle_reason=str(error))
            elif not coordinator_active and not active:
                self.store.configure(
                    control="paused",
                    idle_reason=config.get("coordinator_note") or "No next proposal",
                )
        active_now = [a for a in self.store.attempts() if a["state"] in ACTIVE]
        return {
            "active": len(active_now),
            "dispatched": dispatched,
            "control": self.config().get("control"),
        }

    def start(
        self,
        poll_interval: float = 0.25,
        max_cycles: int | None = None,
        control_hook: Callable[["Runtime"], None] | None = None,
    ) -> dict:
        validate_config(self.config())
        with coordinator_lock(self.root):
            updates = {
                "control": "running",
                "idle_reason": "",
                "generation": int(self.config().get("generation", 0)) + 1,
            }
            previous = [a for a in self.store.attempts() if a["role"] == "coordinator"]
            if previous and previous[-1]["state"] in {"failed", "interrupted"}:
                updates["last_coordinator_key"] = None
            self.store.configure(**updates)
            cycles = 0
            try:
                while True:
                    if control_hook is not None:
                        control_hook(self)
                    result = self.tick()
                    cycles += 1
                    if max_cycles and cycles >= max_cycles:
                        break
                    if result["control"] != "running":
                        # pause drains verifiably live jobs; unknown jobs require explicit reconciliation.
                        live = [
                            a
                            for a in self.store.attempts()
                            if a["state"] in {"running", "reserved"}
                        ]
                        if not live:
                            break
                    time.sleep(poll_interval)
            except KeyboardInterrupt:
                self.store.configure(
                    control="paused", idle_reason="Coordinator interrupted; jobs retained"
                )
            return self.store.snapshot()

    def stop(self) -> dict:
        self.store.configure(control="stopped", idle_reason="Stopped by user")
        for attempt in self.store.attempts():
            if attempt["state"] not in ACTIVE:
                continue
            state = read_json(self.jobs / attempt["id"] / "status.json", {})
            identity = state.get("identity") or attempt.get("identity")
            pid = state.get("pid") or attempt.get("pid")
            if pid and identity and process_identity(pid) == identity:
                try:
                    os.kill(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
            elif (
                state.get("child_identity")
                and process_identity(state.get("child_pid", 0)) == state["child_identity"]
            ):
                try:
                    os.killpg(state["child_pid"], signal.SIGTERM)
                except ProcessLookupError:
                    pass
        return self.store.snapshot()

    def confirm_stopped(self, attempt_id: str, cost: float | None, kind: str) -> dict:
        attempt = next(a for a in self.store.attempts() if a["id"] == attempt_id)
        state = read_json(self.jobs / attempt_id / "status.json", {})
        pid = state.get("pid") or attempt.get("pid")
        identity = state.get("identity") or attempt.get("identity")
        if pid and identity and process_identity(pid) == identity:
            raise ValueError("Execution is still alive; stop it before confirming termination")
        if (
            state.get("child_identity")
            and process_identity(state.get("child_pid", 0)) == state["child_identity"]
        ):
            raise ValueError("Backend is still alive even though its supervisor ended")
        terminal = (
            attempt["state"]
            if attempt["state"] in {"completed", "failed", "interrupted"}
            else "interrupted"
        )
        self.store.settle(attempt_id, cost, cost_kind=kind, state=terminal)
        self.store.set_attempt(attempt_id, collected=True, manual_reconciliation=True)
        return self.store.snapshot()


def backup(root: Path, destination: Path) -> None:
    root, destination = root.resolve(), destination.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Backup destination must be outside the project")
    runtime = Runtime(root)
    with coordinator_lock(root):
        runtime.reconcile()
        if any(a["state"] in ACTIVE for a in runtime.store.attempts()):
            raise ValueError("Pause and finish/reconcile active executions before backup")
        if runtime.config().get("control") == "running":
            raise ValueError("Pause the project before backup")
        shutil.copytree(
            root,
            destination,
            symlinks=True,
            ignore=shutil.ignore_patterns("state.sqlite3*", "coordinator.lock"),
        )
        with sqlite3.connect(root / ".research" / "state.sqlite3") as source:
            with sqlite3.connect(destination / ".research" / "state.sqlite3") as target:
                source.backup(target)
        atomic_json(
            destination / ".research" / "backup.json",
            {"source_root": str(root), "created_at": time.time(), "format": 1},
        )


def check(root: Path) -> dict:
    store, artifacts = Store(root), ArtifactStore(root)
    problems = []
    for node in store.list_nodes():
        for source in node.get("inputs", []):
            try:
                store.resolve(source["ref"])
            except Exception as error:
                problems.append(f"{node['id']}: {error}")
        for product in (node.get("result") or {}).get("products", []):
            try:
                artifacts.verify(product)
            except Exception as error:
                problems.append(f"{node['id']}/{product.get('id')}: {error}")
    with sqlite3.connect(root / ".research" / "state.sqlite3") as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            problems.append(integrity)
    return {"ok": not problems, "problems": problems, "budget": store.budget()}
