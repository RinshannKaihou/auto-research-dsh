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
    """Private schema-2 research service. It never invokes a model or schedules work."""

    def __init__(self, registry: str | Path):
        self.registry = ProjectRegistry(registry)

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
        return NativeStore(root), root, host_id, session_id

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

    def _publish_items(self, root: Path, operation_id: str, items: list[dict]) -> list[dict]:
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
                product = artifacts.freeze(source, root)
                value["object_version"] = product["version"]
                value["object_kind"] = product["kind"]
            fixed.append(value)
        self._write_intent(intent_path, {"operation_id": operation_id, "items": fixed})
        return fixed

    def _snapshot_manifest(
        self, root: Path, operation_id: str, paths: object
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
                product = artifacts.freeze(source, root)
                manifest.append({"source_path": source, **product, "status": "fixed"})
            except Exception as exc:
                manifest.append(
                    {"source_path": source, "status": "incomplete", "error": str(exc)}
                )
                complete = False
        self._write_intent(
            intent_path,
            {
                "operation_id": operation_id,
                "manifest": manifest,
                "complete": complete,
            },
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
        state = store.query()
        node = next((item for item in state["nodes"] if item["node_id"] == node_id), None)
        if node is None or node["status"] == "closed":
            raise ValueError(f"Unknown or closed research node: {node_id}")
        refs = list(node["inputs"])
        if node.get("anchor_ref") and node["anchor_ref"] not in refs:
            refs.append(node["anchor_ref"])
        publications = {
            item["ref"]: (publication, item)
            for publication in state["publications"]
            for item in publication["items"]
        }
        legacy = {item["ref"]: item for item in state["legacy_refs"]}
        materialized = []
        index = []
        for ref in refs:
            product = None
            entry = {"ref": ref, "materialized": False}
            if ref in publications:
                publication, item = publications[ref]
                entry.update(
                    {
                        "kind": item["kind"],
                        "content": item["content"],
                        "publication_id": publication["publication_id"],
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
            elif ref in legacy:
                old = legacy[ref]
                entry.update({"kind": old["kind"], "legacy": True})
                if old["kind"] == "product" and isinstance(old["item"], dict):
                    product = {
                        key: old["item"].get(key) for key in ("path", "version", "kind")
                    }
            elif any(item["node_id"] == ref for item in state["nodes"]):
                entry.update({"kind": "node"})
            else:
                raise ValueError(f"Unknown research reference: {ref}")
            if product and all(product.values()):
                token = hashlib.sha256(ref.encode()).hexdigest()[:16]
                materialized.append(
                    {
                        "node_id": "input",
                        "product_id": f"ref-{token}",
                        "product": product,
                    }
                )
                entry["materialized"] = True
                entry["path"] = f"inputs/input/ref-{token}"
            index.append(entry)
        digest = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
        workspace = ArtifactStore(root).prepare_workspace(
            f"branch-{digest}", materialized
        )
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

    def handle(self, request: dict) -> dict:
        transport_id = request.get("transport_id", request.get("request_id"))
        if not isinstance(transport_id, str) or not transport_id or len(transport_id) > 128:
            raise ValueError("transport_id must be a nonempty string of at most 128 characters")
        method = request.get("method")
        if method == "capabilities":
            value = {
                "schema_version": 2,
                "execution_owner": "dsh",
                "model_loop": "native-goals",
                "manual_research": True,
                "autonomous_research": True,
                "usage_accounting": "observation-only",
                "strict_cross_session_read_isolation": False,
            }
        elif method == "project_sessions":
            host_id, session_id = self._identity(request)
            value = self.registry.sessions_for(host_id, session_id)
        elif method == "open":
            host_id, session_id = self._identity(request)
            root_value = request.get("root")
            if not isinstance(root_value, str) or not Path(root_value).is_absolute():
                raise ValueError("open requires an absolute host-supplied root")
            root = Path(root_value).expanduser().resolve()
            store = NativeStore(root)
            try:
                state = store.query()
            except Exception as exc:
                if "not initialized" not in str(exc).lower():
                    raise
                state = {
                    "project": store.initialize(request.get("goal"), self._operation(request))
                }
            project_id = state["project"]["project_id"]
            self.registry.register(project_id, root, host_id, session_id)
            store.associate(host_id, session_id, self._operation(request) + ":associate")
            value = store.query(host_id, session_id)
        else:
            store, root, host_id, session_id = self._store(request)
            if method in {"query", "status"}:
                value = store.query(host_id, session_id, request.get("ref"))
                value["project_root"] = str(root)
            elif method == "memory":
                value = store.memory_view(host_id, session_id, int(request.get("max_chars", 12000)))
            elif method == "propose":
                value = store.propose(
                    request.get("question"), request.get("why_now"), request.get("plan"),
                    self._operation(request), request.get("inputs", []),
                    request.get("purpose", "explore"), request.get("strategy", "continue"),
                    request.get("anchor_ref"),
                )
            elif method == "focus":
                value = store.focus(
                    host_id, session_id, request.get("node_id"), request.get("role", "researcher"),
                    request.get("mode", "manual"), self._operation(request),
                    defer=bool(request.get("defer", False)),
                )
            elif method == "bind_turn":
                value = store.bind_turn(
                    host_id, session_id, request.get("turn"), self._operation(request)
                )
            elif method == "note":
                value = store.note(
                    host_id, session_id, request.get("body"), request.get("kind", "progress"),
                    self._operation(request),
                )
            elif method == "publish":
                operation_id = self._operation(request)
                items = self._publish_items(root, operation_id, request.get("items", []))
                value = store.publish_metadata(
                    host_id, session_id, request.get("status", "partial"),
                    request.get("summary"), request.get("gaps", []), items, operation_id,
                )
            elif method == "relate":
                value = store.relate(
                    request.get("source_ref"), request.get("target_ref"), request.get("label"),
                    request.get("note"), self._operation(request),
                )
            elif method == "finish":
                value = store.finish(
                    host_id, session_id, request.get("state", "finished"),
                    request.get("details", {}), self._operation(request),
                )
            elif method == "close_node":
                value = store.close_node(request.get("node_id"), self._operation(request))
            elif method == "control":
                value = store.set_control(request.get("control"), self._operation(request))
            elif method == "host_event":
                value = store.record_host_event(
                    host_id, session_id, request.get("event_type"), request.get("sequence"),
                    request.get("facts", {}), self._operation(request),
                )
            elif method == "usage_begin":
                value = store.begin_usage(
                    request.get("source_key"), self._operation(request), host_id=host_id,
                    session_id=session_id, purpose=request.get("purpose", "conversation"),
                    provider=request.get("provider"), model=request.get("model"),
                )
            elif method == "usage_finish":
                value = store.finish_usage(
                    request.get("source_key"), request.get("amount"),
                    request.get("completeness", "unknown"), request.get("details", {}),
                    self._operation(request),
                )
            elif method == "own_goal":
                value = store.own_goal(
                    host_id, session_id, request.get("goal_id"), request.get("revision"),
                    request.get("phase"), self._operation(request),
                )
            elif method == "snapshot":
                operation_id = self._operation(request)
                manifest, complete = self._snapshot_manifest(
                    root, operation_id, request.get("paths", [])
                )
                value = store.record_snapshot(
                    host_id, session_id, manifest, complete, operation_id
                )
            elif method == "prepare_branch":
                value = self._prepare_branch(
                    store, root, request.get("node_id"), self._operation(request)
                )
            elif method == "prepare_restore":
                operation_id = self._operation(request)
                intent_path, saved = self._intent(root, operation_id, "restore")
                if saved is not None:
                    value = saved
                else:
                    state = store.query(host_id, session_id)
                    snapshot_id = request.get("snapshot_id")
                    snapshot = next(
                        (row for row in state["snapshots"] if row["snapshot_id"] == snapshot_id),
                        None,
                    )
                    if snapshot is None:
                        raise ValueError(f"Unknown snapshot: {snapshot_id}")
                    source_attempt = next(
                        row for row in state["attempts"]
                        if row["attempt_id"] == snapshot["attempt_id"]
                    )
                    if source_attempt["state"] == "unknown" or source_attempt["ended_at"] is None:
                        raise ValueError(
                            "The source work segment is still active or unverified; "
                            "finish or stop it before takeover"
                        )
                    digest = hashlib.sha256(operation_id.encode()).hexdigest()[:12]
                    workspace = root / "workspaces" / f"restore-{digest}"
                    workspace.mkdir(parents=True, exist_ok=False)
                    artifacts = ArtifactStore(root)
                    restored = []
                    for index, item in enumerate(snapshot["manifest"]):
                        if item.get("status") != "fixed":
                            continue
                        source_name = Path(item["source_path"]).name or f"item-{index}"
                        destination = workspace / f"{index:03d}-{source_name}"
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
                    self._write_intent(intent_path, value)
            elif method == "record_restore":
                value = store.record_restore(
                    host_id, session_id, request.get("snapshot_id"),
                    request.get("source_attempt_id"), request.get("workspace"),
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
