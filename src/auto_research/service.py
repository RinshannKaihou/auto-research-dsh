"""Private stdio RPC for native DSH research state and migration operations.

The process owns transactional local state, but has no scheduler, model client,
or HTTP socket. The host supplies a fixed registration map, never a path from a
browser or model.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import sys
import tempfile

from .artifacts import ArtifactStore, _open_source
from .migration import preflight, recovery_preview
from .native_store import NativeStore

MAX_REQUEST = 64 * 1024
MAX_RESPONSE = 4 * 1024 * 1024
MAX_PREVIEW = 64 * 1024
_SECRET_KEY = re.compile(
    r"(authorization|cookie|api[_-]?key|access[_-]?token|secret|password)", re.I
)
_SECRET_TEXT = re.compile(
    r"(?i)((?:authorization|cookie|api[_-]?key|access[_-]?token|password|secret)"
    r"\s*[=:]\s*[\"\']?)([^\s\"\',;}]+(?:\s+[^\s\"\',;}]+)?|[^\n]+)"
)


def redact(value):
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _SECRET_KEY.search(key) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _SECRET_TEXT.sub(lambda m: m[1] + "[REDACTED]", value)
    return value


class ReadOnlyService:
    def __init__(self, registrations: dict[str, str]):
        self.roots = {
            key: Path(value).expanduser().resolve() for key, value in registrations.items()
        }

    def handle(self, request: dict) -> dict:
        request_id = request.get("request_id")
        if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
            raise ValueError("request_id must be a nonempty string of at most 128 characters")
        project_id = request.get("project_id")
        if project_id not in self.roots:
            raise ValueError("Unknown project registration")
        root = self.roots[project_id]
        method = request.get("method")
        if method == "status":
            value = preflight(root, files=False)
            # Paths/errors/commands are retained; credentials alone are redacted.
        elif method == "inventory":
            value = preflight(root)
        elif method == "recovery_preview":
            value = recovery_preview(root, request.get("attempt_id"))
        elif method == "preview":
            path = request.get("path")
            if not isinstance(path, str) or Path(path).is_absolute():
                raise ValueError("Preview requires a relative .research path")
            if path.split("/")[0] not in {"workspaces", "objects", "jobs"}:
                raise ValueError(
                    "Only research workspaces, objects, and legacy jobs are previewable"
                )
            with _open_source(path, root / ".research") as fd:
                import os

                content = os.read(fd, MAX_PREVIEW + 1)
            if b"\0" in content:
                raise ValueError("Binary file; use the inventory digest")
            value = {
                "path": path,
                "text": content[:MAX_PREVIEW].decode("utf-8", errors="replace"),
                "truncated": len(content) > MAX_PREVIEW,
                "limit_bytes": MAX_PREVIEW,
                "provenance": "legacy-file; not-native-transcript",
            }
        else:
            raise ValueError("Read-only service: method is not available")
        return {
            "request_id": request_id,
            "project_id": project_id,
            "ok": True,
            "value": redact(value),
        }


class ProjectRegistry:
    """Host-owned mapping from native sessions to research roots."""

    def __init__(self, path: str | Path):
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    project_id TEXT PRIMARY KEY, root TEXT NOT NULL UNIQUE,
                    registered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    host_id TEXT NOT NULL, session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id),
                    associated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    detached_at TEXT,
                    PRIMARY KEY(host_id, session_id, associated_at)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_live_project_per_session
                    ON sessions(host_id, session_id) WHERE detached_at IS NULL;
                """
            )

    def _connection(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA journal_mode=WAL")
        return db

    def register(self, project_id: str, root: Path, host_id: str, session_id: str) -> None:
        root_text = str(root.resolve())
        with self._connection() as db:
            db.execute(
                "INSERT INTO projects(project_id,root) VALUES (?,?) "
                "ON CONFLICT(project_id) DO UPDATE SET root=excluded.root",
                (project_id, root_text),
            )
            live = db.execute(
                "SELECT project_id FROM sessions WHERE host_id=? AND session_id=? "
                "AND detached_at IS NULL",
                (host_id, session_id),
            ).fetchone()
            if live and live["project_id"] != project_id:
                raise ValueError("Native session is already associated with another project")
            if not live:
                db.execute(
                    "INSERT INTO sessions(host_id,session_id,project_id) VALUES (?,?,?)",
                    (host_id, session_id, project_id),
                )

    def detach(self, host_id: str, session_id: str) -> None:
        with self._connection() as db:
            db.execute(
                "UPDATE sessions SET detached_at=CURRENT_TIMESTAMP "
                "WHERE host_id=? AND session_id=? AND detached_at IS NULL",
                (host_id, session_id),
            )

    def root_for(self, host_id: str, session_id: str) -> Path:
        with self._connection() as db:
            row = db.execute(
                "SELECT p.root FROM sessions s JOIN projects p USING(project_id) "
                "WHERE s.host_id=? AND s.session_id=? AND s.detached_at IS NULL",
                (host_id, session_id),
            ).fetchone()
        if not row:
            raise ValueError("Native session is not associated with a research project")
        return Path(row["root"])

    def sessions_for(self, host_id: str, session_id: str) -> list[dict]:
        with self._connection() as db:
            project = db.execute(
                "SELECT project_id FROM sessions WHERE host_id=? AND session_id=? "
                "AND detached_at IS NULL",
                (host_id, session_id),
            ).fetchone()
            if not project:
                raise ValueError("Native session is not associated with a research project")
            return [
                dict(row)
                for row in db.execute(
                    "SELECT host_id,session_id,associated_at FROM sessions "
                    "WHERE project_id=? AND detached_at IS NULL ORDER BY associated_at",
                    (project["project_id"],),
                )
            ]


class NativeService:
    """Private schema-5 research service. It never invokes a model or schedules work."""

    def __init__(self, registry: str | Path):
        self.registry = ProjectRegistry(registry)
        self.stores = {}

    @staticmethod
    def _identity(request: dict) -> tuple[str, str]:
        host_id = request.get("host_id")
        session_id = request.get("session_id")
        if not isinstance(host_id, str) or not host_id:
            raise ValueError("host_id is required")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError("session_id is required")
        return host_id, session_id

    @staticmethod
    def _operation(request: dict) -> str:
        value = request.get("operation_id")
        if not isinstance(value, str) or not value or len(value) > 256:
            raise ValueError("operation_id must be a nonempty string of at most 256 characters")
        return value

    def _store(self, request: dict) -> tuple[NativeStore, Path, str, str]:
        host_id, session_id = self._identity(request)
        root = self.registry.root_for(host_id, session_id)
        store = self.stores.setdefault(str(root), None)
        if store is None:
            store = self.stores[str(root)] = NativeStore(root)
        return store, root, host_id, session_id

    @staticmethod
    def _intent(root: Path, operation_id: str, kind: str) -> tuple[Path, dict | None]:
        digest = hashlib.sha256(operation_id.encode()).hexdigest()
        directory = root / ".research" / f"{kind}-intents" / digest
        manifest = directory / "manifest.json"
        if manifest.is_file():
            return manifest, json.loads(manifest.read_text())
        directory.mkdir(parents=True, exist_ok=True)
        return manifest, None

    @staticmethod
    def _write_intent(path: Path, value: dict) -> None:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
        descriptor, temporary = tempfile.mkstemp(prefix="manifest-", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _safe_source(source: object) -> str:
        if not isinstance(source, str) or not source:
            raise ValueError("source_path must be nonempty text")
        if Path(source).is_absolute() or ".." in Path(source).parts:
            raise ValueError("Archive paths must be relative to the native session cwd")
        parts = Path(source).parts
        lowered = {part.lower() for part in parts}
        if ".research" in lowered or ".git" in lowered:
            raise ValueError("Runtime control directories and Git metadata cannot be archived")
        if any(
            part == ".env" or "credential" in part or "secret" in part or "token" in part
            for part in lowered
        ):
            raise ValueError("Potential credential files cannot be archived")
        return source

    def _publish_items(
        self, root: Path, operation_id: str, items: list[dict], cwd=None
    ) -> list[dict]:
        intent_path, saved = self._intent(root, operation_id, "publish")
        if saved is not None:
            return saved["items"]
        if not isinstance(items, list):
            raise ValueError("items must be a list")
        artifacts = ArtifactStore(root)
        fixed = []
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("publication items must be objects")
            value = dict(item)
            source = value.get("source_path")
            if source is not None:
                source = self._safe_source(source)
                product = artifacts.freeze(source, cwd or root)
                value["object_version"] = product["version"]
                value["object_kind"] = product["kind"]
            fixed.append(value)
        self._write_intent(intent_path, {"operation_id": operation_id, "items": fixed})
        return fixed

    def _snapshot_manifest(
        self, root: Path, operation_id: str, paths: object, cwd=None
    ) -> tuple[list[dict], bool]:
        intent_path, saved = self._intent(root, operation_id, "snapshot")
        if saved is not None:
            return saved["manifest"], bool(saved["complete"])
        if not isinstance(paths, list):
            raise ValueError("paths must be a list")
        artifacts = ArtifactStore(root)
        manifest = []
        complete = True
        for source in paths:
            try:
                source = self._safe_source(source)
                product = artifacts.freeze(source, cwd or root)
                manifest.append({"source_path": source, **product, "status": "fixed"})
            except Exception as exc:
                manifest.append({"source_path": source, "status": "incomplete", "error": str(exc)})
                complete = False
        self._write_intent(
            intent_path,
            {"operation_id": operation_id, "manifest": manifest, "complete": complete,},
        )
        return manifest, complete

    def _prepare_branch(
        self, store: NativeStore, root: Path, node_id: object, operation_id: str
    ) -> dict:
        if not isinstance(node_id, str) or not node_id:
            raise ValueError("node_id is required")
        intent_path, saved = self._intent(root, operation_id, "branch")
        if saved is not None:
            return saved
        try:
            selected_node = store.reference_query(node_id, full=True)
        except Exception:
            selected_node = None
        node = selected_node["value"] if selected_node and selected_node["kind"] == "node" else None
        if node is None or node["status"] == "closed":
            raise ValueError(f"Unknown or closed research node: {node_id}")
        refs = list(node["inputs"])
        if node.get("anchor_ref") and node["anchor_ref"] not in refs:
            refs.append(node["anchor_ref"])
        materialized = []
        index = []
        for ref in refs:
            product = None
            entry = {"ref": ref, "materialized": False}
            selected = store.reference_query(ref, full=True)
            if selected["kind"] == "publication-item":
                item = selected["value"]
                entry.update(
                    {
                        "kind": item["kind"],
                        "content": item["content"],
                        "publication_id": item["publication_id"],
                    }
                )
                if item.get("object_version"):
                    object_kind = item.get("object_kind")
                    if object_kind is None:
                        archived = root / ".research" / "objects" / item["object_version"]
                        object_kind = "directory" if archived.is_dir() else "file"
                    product = {
                        "path": f".research/objects/{item['object_version']}",
                        "version": item["object_version"],
                        "kind": object_kind,
                    }
            elif selected["kind"] == "legacy-ref":
                old = selected["value"]
                entry.update({"kind": old["kind"], "legacy": True})
                if old["kind"] == "product" and isinstance(old["item"], dict):
                    product = {key: old["item"].get(key) for key in ("path", "version", "kind")}
            elif selected["kind"] in {"node", "knowledge"}:
                entry.update({"kind": selected["kind"]})
            else:
                raise ValueError(f"Unknown research reference: {ref}")
            if product and all(product.values()):
                token = hashlib.sha256(ref.encode()).hexdigest()[:16]
                materialized.append(
                    {"node_id": "input", "product_id": f"ref-{token}", "product": product,}
                )
                entry["materialized"] = True
                entry["path"] = f"inputs/input/ref-{token}"
            index.append(entry)
        digest = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
        workspace = ArtifactStore(root).prepare_workspace(f"branch-{digest}", materialized)
        index_path = workspace / "research-inputs.json"
        index_path.write_text(
            json.dumps(
                {
                    "node_id": node_id,
                    "strategy": node.get("strategy", "continue"),
                    "anchor_ref": node.get("anchor_ref"),
                    "inputs": index,
                },
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        value = {
            "node_id": node_id,
            "workspace": str(workspace),
            "project_root": str(root),
            "strategy": node.get("strategy", "continue"),
            "anchor_ref": node.get("anchor_ref"),
            "inputs": index,
        }
        self._write_intent(intent_path, value)
        return value

    def restore_preview(self, store, root, snapshot_id, session_id):
        selected = store.reference_query(snapshot_id, full=True)
        if selected["kind"] != "snapshot":
            raise ValueError("Unknown snapshot")
        snapshot = selected["value"]
        attempt = store.reference_query(snapshot["attempt_id"], full=True)["value"]
        manifest = snapshot["manifest"]
        with store._connection() as db:
            saved = db.execute(
                "SELECT context FROM snapshot_handoffs WHERE snapshot_id=?", (snapshot_id,)
            ).fetchone()
        context = (
            json.loads(saved["context"])
            if saved
            else {
                "goal": store.control_state()["project"]["goal"],
                "source_attempt_id": attempt["attempt_id"],
                "source_node_id": attempt["node_id"],
                "files": manifest,
                "provenance": "legacy-snapshot; no contemporaneous handoff summary",
            }
        )
        context["snapshot_id"] = snapshot_id
        token = hashlib.sha256(
            json.dumps([snapshot, session_id], sort_keys=True).encode()
        ).hexdigest()
        return {
            "preview_id": token,
            "snapshot_id": snapshot_id,
            "manifest": manifest,
            "workspace": str(root / "workspaces" / ("restore-" + token[:16])),
            "complete": snapshot["complete"],
            "context": context,
            "eligible": attempt["ended_at"] is not None and attempt["state"] != "unknown",
            "missing": [m for m in manifest if m.get("status") != "fixed"],
        }

    def prepare_discussion(self, store, root, host_id, session_id, request):
        selected = store.reference_query(request["node_id"], full=True)
        node = selected["value"] if selected["kind"] == "node" else None
        if node is None:
            raise ValueError("Unknown discussion node")
        if not request.get("fresh"):
            with store._connection() as db:
                row = db.execute(
                    "SELECT * FROM workflow_sessions WHERE role='discussion' AND detached=0 "
                    "AND node_id=? ORDER BY rowid DESC LIMIT 1",
                    (node["node_id"],),
                ).fetchone()
                existing = dict(row) if row else None
                if existing:
                    existing["context"] = json.loads(existing["context"])
            if existing:
                return {
                    "session_id": existing["session_id"],
                    "workspace": existing["cwd"],
                    "context": existing["context"],
                    "existing": True,
                }
        intent, saved = self._intent(root, self._operation(request), "discussion")
        if saved:
            return saved
        digest = hashlib.sha256(self._operation(request).encode()).hexdigest()[:20]
        workspace = root / "workspaces" / ("discussion-" + digest)
        workspace.mkdir(parents=True, exist_ok=True)
        refs = set(node.get("inputs", []))
        referenced_publications = {
            ref[4:].split("#", 1)[0] for ref in refs if ref.startswith("pub/")
        }
        with store._connection() as db:
            attempt_ids = {
                row["attempt_id"]
                for row in db.execute(
                    "SELECT attempt_id FROM attempts WHERE node_id=?", (node["node_id"],)
                )
            }
            publication_rows = list(
                db.execute(
                    "SELECT rowid AS _cursor,* FROM publications WHERE node_id=? "
                    "OR publication_id IN (SELECT value FROM json_each(?)) ORDER BY rowid",
                    (node["node_id"], json.dumps(sorted(referenced_publications))),
                )
            )
            publications = [
                store._decode_page_row(db, "publications", row, full=True)
                for row in publication_rows
            ]
            notes = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM notes WHERE attempt_id IN (SELECT value FROM json_each(?)) ORDER BY rowid",
                    (json.dumps(sorted(attempt_ids)),),
                )
            ]
            goal = store._project(db)["goal"]
        context = {
            "role": "discussion",
            "goal": goal,
            "node": node,
            "publications": publications,
            "notes": notes,
            "instruction": "Discuss these frozen references. Query for newer records explicitly. Do not mutate the research ledger or dispatch research.",
        }
        index = []
        artifacts = ArtifactStore(root)
        for publication in publications:
            for item in publication["items"]:
                entry = {"ref": item["ref"], "content": item["content"]}
                if item.get("object_version"):
                    version = item["object_version"]
                    destination = (
                        workspace / "inputs" / publication["publication_id"] / item["item_id"]
                    )
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    artifacts.materialize(
                        {
                            "path": f".research/objects/{version}",
                            "version": version,
                            "kind": item.get("object_kind") or "file",
                        },
                        destination,
                        readonly=True,
                    )
                    entry["path"] = str(destination.relative_to(workspace))
                index.append(entry)
        context["material_index"] = index
        # Keep the full frozen record on disk; IPC and system context carry a bounded view.
        self._write_intent(workspace / "RESEARCH_CONTEXT.json", context)
        if len(json.dumps(context, ensure_ascii=False).encode()) > 24000:
            context = {
                "role": "discussion",
                "goal": context["goal"][:2000],
                "node": {k: str(node[k])[:2000] for k in ("node_id", "question", "plan", "status")},
                "record_path": "RESEARCH_CONTEXT.json",
                "publication_ids": [p["publication_id"] for p in publications][-40:],
                "instruction": context["instruction"]
                + " Read RESEARCH_CONTEXT.json for the full frozen material index and background.",
            }
        value = {
            "session_id": "discussion-" + digest,
            "workspace": str(workspace),
            "context": context,
            "existing": False,
        }
        self._write_intent(intent, value)
        return value

    def handle(self, request: dict) -> dict:
        transport_id = request.get("transport_id", request.get("request_id"))
        if not isinstance(transport_id, str) or not transport_id or len(transport_id) > 128:
            raise ValueError("transport_id must be a nonempty string of at most 128 characters")
        method = request.get("method")
        if method == "capabilities":
            value = {
                "schema_version": 5,
                "execution_owner": "dsh",
                "model_loop": "native-goals",
                "manual_research": True,
                "autonomous_research": True,
                "usage_accounting": "observation-only",
                "strict_cross_session_read_isolation": False,
                "knowledge_revisions": True,
                "native_specialists": "foreground-one-shot",
            }
        elif method == "project_sessions":
            host_id, session_id = self._identity(request)
            value = self.registry.sessions_for(host_id, session_id)
        elif method == "specialist_bind_child":
            host_id, parent_session_id = self._identity(request)
            root = self.registry.root_for(host_id, parent_session_id)
            store = self.stores.get(str(root))
            if store is None:
                store = self.stores[str(root)] = NativeStore(root)
            child_session_id = request.get("child_session_id")
            if not isinstance(child_session_id, str) or not child_session_id:
                raise ValueError("child_session_id is required")
            operation_id = self._operation(request)
            project_id = store.control_state()["project"]["project_id"]
            self.registry.register(project_id, root, host_id, child_session_id)
            store.associate(host_id, child_session_id, operation_id + ":associate")
            store.workflow(
                host_id,
                child_session_id,
                "register",
                {
                    "role": "specialist",
                    "cwd": request.get("cwd", str(root)),
                    "node_id": request.get("node_id"),
                    "context": {"specialist_task_id": request.get("task_id")},
                },
                operation_id + ":role",
            )
            value = store.specialist_bind(
                {
                    "task_id": request.get("task_id"),
                    "session_id": child_session_id,
                    "parent_session_id": parent_session_id,
                },
                operation_id,
            )
        elif method == "open":
            host_id, session_id = self._identity(request)
            root_value = request.get("root")
            if not isinstance(root_value, str) or not Path(root_value).is_absolute():
                raise ValueError("open requires an absolute host-supplied root")
            root = Path(root_value).expanduser().resolve()
            store = self.stores.get(str(root))
            if store is None:
                store = self.stores[str(root)] = NativeStore(root)
            try:
                state = store.control_state()
            except Exception as exc:
                if "not initialized" not in str(exc).lower():
                    raise
                state = {"project": store.initialize(request.get("goal"), self._operation(request))}
            project_id = state["project"]["project_id"]
            self.registry.register(project_id, root, host_id, session_id)
            store.associate(host_id, session_id, self._operation(request) + ":associate")
            store.workflow(
                host_id,
                session_id,
                "register",
                {
                    **({"role": request["session_role"]} if request.get("session_role") else {}),
                    "cwd": request.get("cwd", str(root)),
                    "node_id": request.get("node_id"),
                    "context": request.get("context", {}),
                },
                self._operation(request) + ":role",
            )
            value = store.control_state(host_id, session_id)
            value["project_root"] = str(root)
        else:
            store, root, host_id, session_id = self._store(request)
            with store._connection() as db:
                row = db.execute(
                    "SELECT * FROM workflow_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                role = dict(row) if row else None
            writes = {
                "focus",
                "propose",
                "note",
                "publish",
                "relate",
                "snapshot",
                "finish",
                "close_node",
                "memory_write",
                "specialist_create",
                "consume",
            }
            if (
                role
                and role["role"] in {"discussion", "handoff", "specialist"}
                and method in writes
            ):
                raise ValueError(
                    f"{role['role'].title()} sessions cannot modify the research ledger"
                )
            if method == "guidance_register" and (not role or role["role"] != "main"):
                raise ValueError("Only the research main session may configure method guidance")
            if request.get("model_call") and method not in writes | {"query", "status"}:
                raise ValueError("This operation requires the host controller")
            if request.get("model_call") and method in {"note", "publish", "snapshot"}:
                with store._connection() as db:
                    prior = db.execute(
                        "SELECT 1 FROM requests WHERE request_id=?", (self._operation(request),)
                    ).fetchone()
                    live = db.execute(
                        "SELECT a.* FROM attempts a JOIN associations s USING(association_id) WHERE s.session_id=? AND s.ended_at IS NULL AND a.ended_at IS NULL",
                        (session_id,),
                    ).fetchone()
                if not prior and not live:
                    node_id = role["node_id"] if role else None
                    with store._connection() as db:
                        node = db.execute(
                            "SELECT status FROM nodes WHERE node_id=?", (node_id,)
                        ).fetchone()
                    if node and node["status"] == "closed":
                        node_id = None
                    store.focus(
                        host_id,
                        session_id,
                        node_id,
                        "researcher",
                        "manual",
                        self._operation(request) + ":manual-attempt",
                    )
            if method == "workflow":
                value = store.workflow(
                    host_id,
                    session_id,
                    request["action"],
                    request.get("fields", {}),
                    self._operation(request),
                )
            elif method == "discussion_prepare":
                value = self.prepare_discussion(store, root, host_id, session_id, request)
            elif method == "restore_preview":
                value = self.restore_preview(store, root, request["snapshot_id"], session_id)
            elif method == "host_events":
                value = []
                for event in request.get("events", []):
                    facts = event.get("facts", {})
                    goal = facts.get("goal") if event.get("event_type") == "goal/change" else None
                    if not isinstance(goal, dict) or not goal.get("id"):
                        continue
                    with store._read() as db:
                        owner = db.execute(
                            "SELECT goal_id FROM workflow_sessions WHERE session_id=?",
                            (session_id,),
                        ).fetchone()
                    if owner and owner["goal_id"] == goal["id"]:
                        store.own_goal(
                            host_id,
                            session_id,
                            goal["id"],
                            goal.get("revision", 0),
                            goal.get("phase", "unknown"),
                            f"goal-projection:{session_id}:{event['sequence']}",
                            activation=goal.get("activation"),
                            change_reason=facts.get("reason"),
                            source_sequence=event["sequence"],
                        )
                for event in request.get("events", []):
                    if event["event_type"] != "assistant/message" or not event.get("facts", {}).get(
                        "usage"
                    ):
                        continue
                    facts = event["facts"]
                    with store._connection() as db:
                        observations = list(
                            db.execute(
                                "SELECT u.* FROM usage_observations u JOIN associations a USING(association_id) WHERE a.host_id=? AND a.session_id=? AND u.purpose='conversation' AND json_extract(u.details,'$.turn')=? AND json_extract(u.details,'$.step')=?",
                                (host_id, session_id, facts.get("turn"), facts.get("step")),
                            )
                        )
                    if len(observations) == 1 and observations[0]["amount"] is None:
                        usage = facts["usage"]
                        amount = usage.get("totalTokens")
                        if amount is None:
                            values = [
                                usage[k]
                                for k in (
                                    "inputTokens",
                                    "outputTokens",
                                    "cacheReadTokens",
                                    "cacheWriteTokens",
                                )
                                if isinstance(usage.get(k), (float, int))
                            ]
                            amount = sum(values) if values else None
                        if amount is not None:
                            store.finish_usage(
                                observations[0]["source_key"],
                                amount,
                                "actual",
                                {"phase": "reconciled", "native_sequence": event["sequence"]},
                                f"reconcile:{session_id}:{event['sequence']}",
                            )
                for event in request.get("events", []):
                    value.append(
                        store.record_host_event(
                            host_id,
                            session_id,
                            event["event_type"],
                            event["sequence"],
                            event.get("facts", {}),
                            f"{session_id}:event:{event['sequence']}",
                        )
                    )
                with store._connection() as db:
                    db.execute(
                        "INSERT INTO projection_cursors VALUES(?,?) ON CONFLICT(session_id) DO UPDATE SET sequence=MAX(sequence,excluded.sequence)",
                        (session_id, request.get("cursor", 0)),
                    )
            elif method == "recovery_receipt":
                with store._read() as db:
                    row = db.execute(
                        "SELECT details FROM workflow_intents WHERE intent_id=? AND session_id=? AND kind='recovery-receipt' AND state='complete'",
                        (request["key"], session_id),
                    ).fetchone()
                    value = json.loads(row["details"]) if row else None
            elif method == "task_context":
                from .memory_store import bounded_value

                selected = store.reference_query(request["task_id"], full=True)
                if selected["kind"] != "exploration-task":
                    raise ValueError("Unknown exploration task")
                value = bounded_value(selected["value"]["context"], 6000)
            elif method == "task_creation_clear":
                with store._read() as db:
                    task = db.execute(
                        "SELECT session_id FROM exploration_tasks WHERE task_id=?",
                        (request["task_id"],),
                    ).fetchone()
                    if not task:
                        raise ValueError("Unknown exploration task")
                    sid = task["session_id"]
                    rows = db.execute(
                        "SELECT 1 FROM associations WHERE session_id=? UNION ALL SELECT 1 FROM workflow_sessions WHERE session_id=? UNION ALL SELECT 1 FROM workflow_intents WHERE session_id=? LIMIT 1",
                        (sid, sid, sid),
                    ).fetchone()
                    value = {"clear": rows is None}
            elif method == "workflow_page":
                with store._read() as db:
                    value = store.workflow_control(db, session_id, request.get("cursors"))
            elif method == "status" or method == "control_state":
                value = store.control_state(host_id, session_id)
                value["project_root"] = str(root)
            elif method == "query":
                if request.get("ref") and request.get("offset") is not None:
                    value = store.reference_chunk(
                        request["ref"],
                        offset=request.get("offset", 0),
                        limit=request.get("limit", 8192),
                    )
                elif request.get("ref"):
                    value = store.reference_query(request["ref"])
                elif any(
                    request.get(key) is not None
                    for key in ("query", "kind", "node_id", "status", "revision", "conditions")
                ):
                    value = store.knowledge_query(
                        query=request.get("query"),
                        node_id=request.get("node_id"),
                        kind=request.get("kind"),
                        status=request.get("status"),
                        revision=request.get("revision"),
                        conditions=request.get("conditions"),
                        after=request.get("after", 0),
                        limit=request.get("limit", 50),
                    )
                elif request.get("collection"):
                    if request["collection"] == "usage":
                        value = store.usage_page(
                            after=request.get("after", 0),
                            upper_id=request.get("upper_id"),
                            limit=request.get("limit", 50),
                        )
                    elif request["collection"] == "changes":
                        value = store.changes_page(
                            after=request.get("after", 0), limit=request.get("limit", 50)
                        )
                    else:
                        value = store.history_page(
                            request["collection"],
                            after=request.get("after", 0),
                            upper_id=request.get("upper_id"),
                            limit=request.get("limit", 50),
                        )
                else:
                    value = store.control_state(host_id, session_id)
                    value["project_root"] = str(root)
            elif method == "history_page":
                value = store.history_page(
                    request.get("collection"),
                    after=request.get("after", 0),
                    upper_id=request.get("upper_id"),
                    limit=request.get("limit", 50),
                )
            elif method == "usage_page":
                value = store.usage_page(
                    after=request.get("after", 0),
                    upper_id=request.get("upper_id"),
                    limit=request.get("limit", 50),
                )
            elif method == "changes_page":
                value = store.changes_page(
                    after=request.get("after", 0), limit=request.get("limit", 50)
                )
            elif method in {"memory", "memory_context"}:
                value = store.context_view(
                    host_id, session_id, int(request.get("max_chars", 12000))
                )
            elif method == "context_record":
                value = store.record_context_request(
                    {**request.get("fields", {}), "host_id": host_id, "session_id": session_id,},
                    self._operation(request),
                )
            elif method == "memory_write":
                action = request.get("action")
                fields = {
                    **request.get("fields", {}),
                    "source_identity": {
                        "host_id": host_id,
                        "session_id": session_id,
                        **request.get("fields", {}).get("source_identity", {}),
                    },
                }
                if action == "record":
                    value = store.record_knowledge(
                        fields, self._operation(request), execution_identity=(host_id, session_id)
                    )
                elif action == "revise":
                    value = store.revise_knowledge(fields, self._operation(request))
                elif action == "checkpoint":
                    value = store.checkpoint(fields, self._operation(request))
                else:
                    raise ValueError("memory_write action must be record, revise, or checkpoint")
            elif method == "specialist_create":
                if not role or role["role"] not in {"main", "node_core", "exploration"}:
                    raise ValueError("Only managed research agents may delegate specialists")
                with store._connection() as db:
                    association = store._association(db, host_id, session_id)
                    attempt = db.execute(
                        "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                        (association["association_id"],),
                    ).fetchone()
                fields = request.get("fields", {})
                value = store.specialist_create(
                    {
                        **fields,
                        "parent_session_id": session_id,
                        "node_id": fields.get("node_id")
                        or (attempt["node_id"] if attempt else role["node_id"]),
                        "attempt_id": attempt["attempt_id"] if attempt else None,
                    },
                    self._operation(request),
                )
            elif method == "specialist_finish":
                value = store.specialist_finish(
                    {**request.get("fields", {}), "parent_session_id": session_id,},
                    self._operation(request),
                )
            elif method == "specialist_get":
                value = store.specialist_get(request.get("task_id"))
            elif method == "review_todo_next":
                value = store.review_todo_next(request.get("node_id"))
            elif method == "review_todo_state":
                value = store.review_todo_state(
                    request.get("todo_id"), request.get("state"), self._operation(request)
                )
            elif method == "guidance_register":
                path_value = request.get("path")
                if not isinstance(path_value, str) or not Path(path_value).is_absolute():
                    raise ValueError("Guidance requires an absolute host-supplied path")
                path = Path(path_value).expanduser().resolve()
                if not path.is_file():
                    raise ValueError("Guidance document does not exist")
                content = path.read_text(encoding="utf-8")
                if len(content.encode("utf-8")) > 2 * 1024 * 1024:
                    raise ValueError("Guidance document exceeds 2 MiB")
                value = store.guidance_register(
                    {
                        "path": str(path),
                        "version": request.get("version")
                        or hashlib.sha256(content.encode()).hexdigest()[:12],
                        "content": content,
                    },
                    self._operation(request),
                )
            elif method == "guidance_status":
                value = store.guidance_status()
            elif method == "propose":
                value = store.propose(
                    request.get("question"),
                    request.get("why_now"),
                    request.get("plan"),
                    self._operation(request),
                    request.get("inputs"),
                    request.get("purpose", "explore"),
                    request.get("strategy", "continue"),
                    request.get("anchor_ref"),
                    request.get("question_ref"),
                    request.get("root_reason"),
                    request.get("predecessors"),
                    enforce_protocol=bool(request.get("model_call")),
                )
            elif method == "consume":
                value = store.consume(
                    host_id,
                    session_id,
                    request.get("source_ref"),
                    request.get("use"),
                    request.get("relation_type", "adopts"),
                    self._operation(request),
                )
            elif method == "focus":
                value = store.focus(
                    host_id,
                    session_id,
                    request.get("node_id"),
                    request.get("role", "researcher"),
                    request.get("mode", "manual"),
                    self._operation(request),
                    defer=bool(request.get("defer", False)),
                )
            elif method == "bind_turn":
                value = store.bind_turn(
                    host_id, session_id, request.get("turn"), self._operation(request)
                )
            elif method == "note":
                value = store.note(
                    host_id,
                    session_id,
                    request.get("body"),
                    request.get("kind", "progress"),
                    self._operation(request),
                )
            elif method == "publish":
                operation_id = self._operation(request)
                items = self._publish_items(
                    root,
                    operation_id,
                    request.get("items", []),
                    Path(role["cwd"]) if role and role["cwd"] else root,
                )
                value = store.publish_metadata(
                    host_id,
                    session_id,
                    request.get("status", "partial"),
                    request.get("summary"),
                    request.get("gaps", []),
                    items,
                    operation_id,
                    request.get("knowledge_refs", []),
                )
            elif method == "relate":
                value = store.relate(
                    request.get("source_ref"),
                    request.get("target_ref"),
                    request.get("label"),
                    request.get("note"),
                    self._operation(request),
                )
            elif method == "finish":
                value = store.finish(
                    host_id,
                    session_id,
                    request.get("state", "finished"),
                    request.get("details", {}),
                    self._operation(request),
                )
            elif method == "close_node":
                value = store.close_node(request.get("node_id"), self._operation(request))
            elif method == "control":
                value = store.set_control(request.get("control"), self._operation(request))
            elif method == "host_event":
                value = store.record_host_event(
                    host_id,
                    session_id,
                    request.get("event_type"),
                    request.get("sequence"),
                    request.get("facts", {}),
                    self._operation(request),
                )
            elif method == "usage_begin":
                value = store.begin_usage(
                    request.get("source_key"),
                    self._operation(request),
                    host_id=host_id,
                    session_id=session_id,
                    purpose=request.get("purpose", "conversation"),
                    provider=request.get("provider"),
                    model=request.get("model"),
                    turn=request.get("turn"),
                    step=request.get("step"),
                )
            elif method == "usage_finish":
                value = store.finish_usage(
                    request.get("source_key"),
                    request.get("amount"),
                    request.get("completeness", "unknown"),
                    request.get("details", {}),
                    self._operation(request),
                )
            elif method == "usage_gap":
                value = store.record_usage_gap(
                    request.get("source_key"),
                    session_id,
                    request.get("purpose", "conversation"),
                    request.get("reason"),
                    request.get("details", {}),
                    self._operation(request),
                )
            elif method == "own_goal":
                value = store.own_goal(
                    host_id,
                    session_id,
                    request.get("goal_id"),
                    request.get("revision"),
                    request.get("phase"),
                    self._operation(request),
                    activation=request.get("activation"),
                    change_reason=request.get("change_reason"),
                    source_sequence=request.get("source_sequence"),
                )
            elif method == "snapshot":
                operation_id = self._operation(request)
                manifest, complete = self._snapshot_manifest(
                    root,
                    operation_id,
                    request.get("paths", []),
                    Path(role["cwd"]) if role and role["cwd"] else root,
                )
                value = store.record_snapshot(host_id, session_id, manifest, complete, operation_id)
            elif method == "prepare_branch":
                value = self._prepare_branch(
                    store, root, request.get("node_id"), self._operation(request)
                )
            elif method == "prepare_restore":
                operation_id = self._operation(request)
                preview = self.restore_preview(store, root, request["snapshot_id"], session_id)
                if request.get("preview_id") != preview["preview_id"]:
                    raise ValueError("A current restore preview_id is required")
                intent_path, saved = self._intent(root, operation_id, "restore")
                if saved is not None:
                    value = saved
                else:
                    snapshot_id = request.get("snapshot_id")
                    selected_snapshot = store.reference_query(snapshot_id, full=True)
                    if selected_snapshot["kind"] != "snapshot":
                        raise ValueError(f"Unknown snapshot: {snapshot_id}")
                    snapshot = selected_snapshot["value"]
                    source_attempt = store.reference_query(snapshot["attempt_id"], full=True)[
                        "value"
                    ]
                    if source_attempt["state"] == "unknown" or source_attempt["ended_at"] is None:
                        raise ValueError(
                            "The source work segment is still active or unverified; "
                            "finish or stop it before takeover"
                        )
                    digest = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
                    workspace = Path(preview["workspace"])
                    workspace.mkdir(parents=True, exist_ok=True)
                    artifacts = ArtifactStore(root)
                    restored = []
                    for index, item in enumerate(snapshot["manifest"]):
                        if item.get("status") != "fixed":
                            continue
                        source_name = self._safe_source(item["source_path"])
                        destination = workspace / source_name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        artifacts.materialize(
                            {key: item[key] for key in ("path", "version", "kind")},
                            destination,
                            readonly=False,
                        )
                        restored.append(str(destination.relative_to(root)))
                    value = {
                        "snapshot_id": snapshot_id,
                        "source_attempt_id": snapshot["attempt_id"],
                        "node_id": source_attempt["node_id"],
                        "workspace": str(workspace),
                        "project_root": str(root),
                        "restored": restored,
                        "complete": snapshot["complete"],
                    }
                    value["context"] = preview["context"]
                    self._write_intent(workspace / "RESEARCH_HANDOFF.json", preview["context"])
                    self._write_intent(intent_path, value)
            elif method == "record_restore":
                value = store.record_restore(
                    host_id,
                    session_id,
                    request.get("snapshot_id"),
                    request.get("source_attempt_id"),
                    request.get("workspace"),
                    self._operation(request),
                )
            elif method == "detach":
                value = store.detach(
                    host_id, session_id, self._operation(request), request.get("ended_seq")
                )
                self.registry.detach(host_id, session_id)
            elif method == "inventory":
                value = preflight(root)
            elif method == "recovery_preview":
                value = recovery_preview(root, request.get("attempt_id"))
            elif method == "preview":
                path = request.get("path")
                if not isinstance(path, str) or Path(path).is_absolute():
                    raise ValueError("Preview requires a project-relative research path")
                if path.split("/")[0] not in {"workspaces", ".research"}:
                    raise ValueError("Only registered project research paths are previewable")
                with _open_source(path, root) as fd:
                    content = os.read(fd, MAX_PREVIEW + 1)
                if b"\0" in content:
                    raise ValueError("Binary file; use the inventory digest")
                value = {
                    "path": path,
                    "text": content[:MAX_PREVIEW].decode("utf-8", errors="replace"),
                    "truncated": len(content) > MAX_PREVIEW,
                    "limit_bytes": MAX_PREVIEW,
                }
            else:
                raise ValueError(f"Unknown native research method: {method}")
        return {"request_id": transport_id, "ok": True, "value": redact(value)}


def serve(service, source, output):
    while True:
        raw = source.readline(MAX_REQUEST + 1)
        if not raw:
            return
        request_id = None
        try:
            if len(raw) > MAX_REQUEST:
                # Close an invalid stream rather than interpreting the tail as another request.
                raise OverflowError("Request exceeds byte limit; connection closed")
            request = json.loads(raw)
            if not isinstance(request, dict):
                raise ValueError("Request must be an object")
            request_id = request.get("transport_id", request.get("request_id"))
            response = service.handle(request)
            encoded = json.dumps(response, ensure_ascii=False, allow_nan=False).encode()
            if len(encoded) > MAX_RESPONSE:
                raise ValueError("Response exceeds byte limit; use a narrower view")
        except Exception as exc:
            encoded = json.dumps(
                {
                    "request_id": request_id,
                    "ok": False,
                    "error": {"code": type(exc).__name__, "message": str(exc)},
                }
            ).encode()
        output.write(encoded + b"\n")
        output.flush()
        if len(raw) > MAX_REQUEST:
            return


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--registry", help="Host-owned native project registry")
    mode.add_argument("--registrations", help="Legacy read-only registration JSON")
    args = parser.parse_args()
    if args.registry:
        serve(NativeService(args.registry), sys.stdin.buffer, sys.stdout.buffer)
        return
    registrations = json.loads(args.registrations)
    if not isinstance(registrations, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in registrations.items()
    ):
        parser.error("registrations must map strings to paths")
    serve(ReadOnlyService(registrations), sys.stdin.buffer, sys.stdout.buffer)


if __name__ == "__main__":
    main()
