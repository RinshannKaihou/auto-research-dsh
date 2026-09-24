"""Read-only, bounded projections for the DSH Research workbench.

This module does not write to the research ledger. Presentation metadata is a
separate optional file; every numeric value is read from a verified frozen ref.
"""

from __future__ import annotations

import base64
from collections import OrderedDict
import json
import math
from pathlib import Path
import re
import stat
from typing import Any

from . import frozen_refs
from .artifacts import _open_source
from .errors import ValidationError
from .publication_display import project_publication


PREVIEW_LIMIT = 2 * 1024 * 1024
CHUNK_LIMIT = 32 * 1024
PRESENTATION_LIMIT = 256 * 1024
_preview_cache: OrderedDict[tuple[str, str, str], tuple[bytes, bool, int]] = OrderedDict()


def _page_size(value: Any) -> int:
    return max(1, min(int(value or 20), 100))


def _short(value: Any, limit: int = 180) -> str:
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    return value[:limit] + ("…" if len(value) > limit else "")


def summary(store, host_id: str, session_id: str) -> dict:
    # The native control state owns all status semantics. Project it here so
    # neither the long workflow payload nor 19 collections cross IPC.
    state = store.control_state(host_id, session_id)
    with store._read() as db:
        revision = int(db.execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()[0])
        final = db.execute(
            "SELECT publication_id,summary,status,node_id FROM publications "
            "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        final = project_publication(db, dict(final)) if final else None
    workflow = state.get("workflow", {})
    return {
        "schema_version": state["schema_version"],
        "project": {key: state["project"].get(key) for key in ("project_id", "goal", "control", "created_at")},
        "revision": revision,
        "counts": state["counts"],
        "review_queue": state["review_queue"],
        "run": workflow.get("run", {}),
        "session": workflow.get("session"),
        "attempt": state.get("attempt"),
        "final_publication": final,
    }


_PAGE_TABLES = {
    "nodes": ("nodes", "node_id", ("node_id", "status", "question", "origin_kind", "root_reason", "strategy", "created_at")),
    "dependencies": ("node_dependencies", "dependency_id", ("dependency_id", "predecessor_node_id", "successor_node_id", "relation_type", "scheduling")),
    "relations": ("relations", "relation_id", ("relation_id", "source_ref", "target_ref", "relation_type", "label", "note")),
    "publications": ("publications", "publication_id", ("publication_id", "node_id", "summary", "status", "created_at")),
    "attempts": ("attempts", "attempt_id", ("attempt_id", "node_id", "state", "mode", "started_at", "ended_at")),
    "sessions": ("workflow_sessions", "session_id", ("session_id", "node_id", "role", "pause_reason", "detached", "close_state")),
    "tasks": ("exploration_tasks", "task_id", ("task_id", "node_id", "state", "error")),
    "review_todos": ("review_todos", "todo_id", ("todo_id", "node_id", "state", "trigger_kind", "trigger_ref")),
    "checkpoints": ("node_checkpoints", "checkpoint_id", ("checkpoint_id", "node_id", "revision", "created_at")),
    "snapshots": ("snapshots", "snapshot_id", ("snapshot_id", "attempt_id", "complete", "created_at")),
    "specialists": ("specialist_tasks", "task_id", ("task_id", "node_id", "state", "purpose", "label", "parent_session_id", "child_session_id")),
    "restorations": ("restorations", "restoration_id", ("restoration_id", "snapshot_id", "source_attempt_id", "target_attempt_id")),
}


def page(store, collection: str, *, after: int = 0, upper_id: int | None = None,
         limit: int = 20, node_id: str | None = None, kind: str | None = None) -> dict:
    if collection == "hints":
        with store._read() as db:
            from .hints import structure_hints
            result = structure_hints(db, limit=_page_size(limit), offset=max(0, int(after or 0)), node_id=node_id, klass=kind)
        return {"collection": collection, "items": result["items"], "total": result["total"],
                "cursor": {"after": result["next_offset"]} if result["next_offset"] is not None else None}
    spec = _PAGE_TABLES.get(collection)
    if spec is None:
        raise ValidationError("Unsupported workbench collection")
    table, _, fields = spec
    limit = _page_size(limit)
    after = max(0, int(after or 0))
    if node_id is not None and collection in {"nodes", "publications", "attempts", "sessions", "tasks", "review_todos", "checkpoints", "specialists"}:
        if not isinstance(node_id, str) or len(node_id) > 128:
            raise ValidationError("Invalid node filter")
    else:
        node_id = None
    columns = ",".join(fields)
    with store._read() as db:
        if upper_id is None:
            upper_id = int(db.execute(f"SELECT COALESCE(MAX(rowid),0) FROM {table}").fetchone()[0])
        else:
            upper_id = max(0, int(upper_id))
        node_clause = " AND node_id=?" if node_id else ""
        params = [after, upper_id] + ([node_id] if node_id else [])
        total = int(db.execute(f"SELECT COUNT(*) FROM {table} WHERE rowid<=?{node_clause}",
                               [upper_id] + ([node_id] if node_id else [])).fetchone()[0])
        rows = list(db.execute(
            f"SELECT rowid AS cursor,{columns} FROM {table} WHERE rowid>? AND rowid<=?{node_clause} "
            "ORDER BY rowid LIMIT ?", params + [limit + 1]
        ))
        visible = [dict(row) for row in rows[:limit]]
        if collection == "sessions" and visible:
            ids = [item["session_id"] for item in visible]
            task_rows = db.execute(
                "SELECT task_id,node_id,label,purpose,state,parent_session_id,child_session_id "
                f"FROM specialist_tasks WHERE child_session_id IN ({','.join('?' for _ in ids)}) ORDER BY rowid",
                ids,
            )
            tasks = {row["child_session_id"]: dict(row) for row in task_rows}
            for item in visible:
                task = tasks.get(item["session_id"])
                if task:
                    item.update(role="specialist", node_id=task["node_id"] or item["node_id"],
                                task_id=task["task_id"], label=task["label"], purpose=task["purpose"],
                                specialist_state=task["state"], parent_session_id=task["parent_session_id"])
        if collection == "publications":
            for record in visible:
                project_publication(db, record)
    for item in visible:
        for key in ("question", "summary", "note", "error", "purpose", "label"):
            if key in item:
                item[key] = _short(item[key], 240)
    return {"collection": collection, "items": visible, "total": total, "upper_id": upper_id,
            "cursor": {"after": visible[-1]["cursor"], "upper_id": upper_id}
            if len(rows) > limit and visible else None}


def knowledge_page(store, *, query: str = "", kind: str | None = None,
                   status: str | None = None, node_id: str | None = None,
                   knowledge_id: str | None = None, history: bool = False,
                   after: int = 0, upper_id: int | None = None, limit: int = 20) -> dict:
    if query and (not isinstance(query, str) or len(query) > 256):
        raise ValidationError("Knowledge query is too long")
    if history and not knowledge_id:
        raise ValidationError("History requires a knowledge ID")
    limit = _page_size(limit)
    after = max(0, int(after or 0))
    with store._read() as db:
        if upper_id is None:
            upper_id = int(db.execute("SELECT COALESCE(MAX(rowid),0) FROM knowledge_revisions").fetchone()[0])
        else:
            upper_id = max(0, int(upper_id))
        clauses = ["r.rowid<=?"]
        args: list[Any] = [upper_id]
        if not history:
            clauses.append("r.rowid=(SELECT MAX(r2.rowid) FROM knowledge_revisions r2 "
                           "WHERE r2.knowledge_id=r.knowledge_id AND r2.rowid<=?)")
            args.append(upper_id)
        if knowledge_id:
            clauses.append("r.knowledge_id=?")
            args.append(knowledge_id)
        if kind:
            clauses.append("e.kind=?")
            args.append(kind)
        if status:
            clauses.append("r.status=?")
            args.append(status)
        if node_id == "__unassigned__":
            clauses.append("e.node_id IS NULL")
        elif node_id:
            clauses.append("e.node_id=?")
            args.append(node_id)
        if query:
            if re.fullmatch(r"K-\d+", query.strip(), re.I):
                clauses.append("r.knowledge_id=?")
                args.append(query.strip().upper())
            else:
                clauses.append("(r.statement LIKE ? OR r.knowledge_id LIKE ?)")
                args.extend((f"%{query}%", f"%{query}%"))
        where = " AND ".join(clauses)
        base = " FROM knowledge_revisions r JOIN knowledge_entries e USING(knowledge_id) WHERE "
        total = int(db.execute("SELECT COUNT(*)" + base + where, args).fetchone()[0])
        rows = list(db.execute(
            "SELECT r.rowid AS cursor,r.knowledge_id,r.revision,r.status,r.statement,"
            "r.evidence_refs,r.supersedes,e.kind,e.node_id" + base + where +
            " AND r.rowid>? ORDER BY r.rowid LIMIT ?", args + [after, limit + 1]
        ))
    items = []
    for row in rows[:limit]:
        item = dict(row)
        item["ref"] = f"knowledge/{item['knowledge_id']}@{item['revision']}"
        item["statement"] = _short(item["statement"], 320)
        item["evidence_refs"] = json.loads(item["evidence_refs"])
        item["supersedes"] = json.loads(item["supersedes"])
        items.append(item)
    return {"items": items, "total": total, "upper_id": upper_id,
            "cursor": {"after": items[-1]["cursor"], "upper_id": upper_id}
            if len(rows) > limit and items else None}


def _verified_path(store, root: Path, ref: str):
    if not frozen_refs.is_frozen(ref):
        raise ValidationError("A frozen publication or snapshot reference is required")
    with store._read() as db:
        return frozen_refs.open(db, root, ref)


def reference_entries(store, root: Path, ref: str, *, after: int = 0, limit: int = 50) -> dict:
    resolution, path = _verified_path(store, root, ref)
    if path is None:
        return {"ref": ref, "resolution": resolution, "items": [], "cursor": None}
    if not path.is_dir():
        return {"ref": ref, "resolution": resolution, "items": [], "cursor": None, "kind": "file"}
    if path.is_symlink():
        return {"ref": ref, "resolution": {**resolution, "outcome": "unsupported", "reason": "path_escape"}, "items": [], "cursor": None}
    names = sorted(path.iterdir(), key=lambda item: item.name)
    names = [item for item in names if not item.name.startswith("._")]
    start = max(0, int(after or 0))
    visible = names[start:start + _page_size(limit)]
    items = []
    for item in visible:
        mode = item.lstat().st_mode
        if stat.S_ISLNK(mode):
            continue
        items.append({"name": item.name, "kind": "directory" if stat.S_ISDIR(mode) else "file",
                      "ref": ref.rstrip("/") + "/" + item.name,
                      "bytes": item.stat().st_size if stat.S_ISREG(mode) else None})
    return {"ref": ref, "resolution": resolution, "kind": "directory", "items": items,
            "cursor": {"after": start + len(visible)} if start + len(visible) < len(names) else None}


def reference_content(store, root: Path, ref: str, *, offset: int = 0,
                      limit: int = CHUNK_LIMIT) -> dict:
    offset = max(0, int(offset or 0))
    limit = max(1, min(int(limit or CHUNK_LIMIT), CHUNK_LIMIT))
    if not frozen_refs.is_frozen(ref):
        raise ValidationError("A frozen publication or snapshot reference is required")
    resolution = path = cached = key = None
    if offset:
        # A later chunk may reuse the exact bytes verified at offset zero.
        # Resolve again to bind the cache to the current immutable object ID.
        with store._read() as db:
            resolution = frozen_refs.resolve(db, root, ref)
        obj = resolution.get("object")
        if resolution["outcome"] == "resolved" and obj:
            key = (str(root), ref, obj["version"])
            cached = _preview_cache.get(key)
            if cached is not None:
                _preview_cache.move_to_end(key)
                path = root / ".research" / "objects" / obj["version"]
                if resolution.get("subpath"):
                    path = path / resolution["subpath"]
                resolution["integrity"] = "verified_cached"
    if cached is None:
        resolution, path = _verified_path(store, root, ref)
    if path is None:
        return {"ref": ref, "resolution": resolution}
    if path.is_dir():
        return {"ref": ref, "resolution": resolution, "kind": "directory"}
    key = (str(root), ref, resolution["object"]["version"])
    if cached is None:
        with _open_source(path, root / ".research" / "objects") as descriptor:
            import os
            total_bytes = os.fstat(descriptor).st_size
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                contents = source.read(PREVIEW_LIMIT + 1)
        cached = (contents[:PREVIEW_LIMIT], len(contents) > PREVIEW_LIMIT, total_bytes)
        _preview_cache[key] = cached
        while len(_preview_cache) > 4:
            _preview_cache.popitem(last=False)
    contents, truncated, total_bytes = cached
    probe = contents[:4096]
    binary = b"\0" in probe
    if not binary:
        try:
            probe.decode("utf-8")
        except UnicodeDecodeError as exc:
            binary = exc.start < len(probe) - 4
    if binary:
        return {"ref": ref, "resolution": resolution, "kind": "binary",
                "total_bytes": total_bytes}
    chunk = contents[offset:offset + limit]
    next_offset = offset + len(chunk) if offset + len(chunk) < len(contents) else None
    return {"ref": ref, "resolution": resolution, "kind": "text", "offset": offset,
            "chunk": base64.b64encode(chunk).decode("ascii"), "encoding": "base64-utf8",
            "next_offset": next_offset, "total_bytes": total_bytes, "truncated": truncated}


def _pointer(document: Any, pointer: str) -> Any:
    if not isinstance(pointer, str) or not pointer.startswith("/") or len(pointer) > 512:
        raise ValueError("Invalid JSON pointer")
    value = document
    for segment in pointer[1:].split("/"):
        key = segment.replace("~1", "/").replace("~0", "~")
        value = value[int(key)] if isinstance(value, list) else value[key]
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError("Metric value is not numeric")
    return value


def presentation(store, root: Path) -> dict:
    path = root / "research.presentation.json"
    try:
        with _open_source(path, root) as descriptor:
            import os
            with os.fdopen(descriptor, "rb", closefd=False) as source:
                raw = source.read(PRESENTATION_LIMIT + 1)
        if len(raw) > PRESENTATION_LIMIT:
            raise ValueError("Presentation file is too large")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("schema") != "research-presentation/v1":
            raise ValueError("Unsupported presentation schema")
        with store._read() as db:
            project = store._project(db)
            last = db.execute("SELECT publication_id FROM publications ORDER BY rowid DESC LIMIT 1").fetchone()
        if data.get("project_id") != project["project_id"]:
            raise ValueError("Presentation belongs to another project")
        for key in ("title", "summary"):
            if not isinstance(data.get(key), str) or len(data[key]) > 2000:
                raise ValueError(f"Invalid {key}")
        if not isinstance(data.get("nodes", {}), dict) or not isinstance(data.get("metrics", []), list):
            raise ValueError("Invalid presentation entries")
        if len(data["metrics"]) > 24:
            raise ValueError("Too many presentation metrics")
        output = {key: data.get(key) for key in ("schema", "project_id", "title", "summary",
                  "summary_ref", "nodes", "featured_node_id", "report_ref", "deliverables", "source_publication_id")}
        output["stale"] = bool(last and data.get("source_publication_id") != last[0])
        output["link_warnings"] = []
        linked = [data.get("summary_ref"), data.get("report_ref")]
        if isinstance(data.get("deliverables"), list):
            linked.extend(item.get("ref") for item in data["deliverables"] if isinstance(item, dict))
        for ref in linked:
            if ref is None:
                continue
            if not isinstance(ref, str) or not frozen_refs.is_frozen(ref):
                output["link_warnings"].append({"ref": ref, "reason": "invalid_reference"})
                continue
            resolution, _ = _verified_path(store, root, ref)
            if resolution["outcome"] != "resolved":
                output["link_warnings"].append({"ref": ref, "reason": resolution["reason"]})
        output["metrics"] = []
        for entry in data["metrics"]:
            if not isinstance(entry, dict):
                raise ValueError("Invalid metric entry")
            decimals = entry.get("display_decimals")
            if decimals is not None and (not isinstance(decimals, int) or isinstance(decimals, bool) or not 0 <= decimals <= 8):
                raise ValueError("Invalid display_decimals")
            result = {key: entry.get(key) for key in ("id", "label", "unit", "direction", "split",
                      "population", "protocol", "threshold", "display_decimals")}
            documents = {}
            for side in ("baseline", "current"):
                binding = entry.get(side)
                if not isinstance(binding, dict) or not isinstance(binding.get("ref"), str):
                    raise ValueError("Invalid metric binding")
                resolution, frozen = _verified_path(store, root, binding["ref"])
                result[side] = {"ref": binding["ref"], "pointer": binding.get("pointer"),
                                "resolution": resolution, "value": None}
                if frozen is not None and frozen.is_file() and frozen.stat().st_size <= PREVIEW_LIMIT:
                    with _open_source(frozen, root / ".research" / "objects") as descriptor:
                        import os
                        with os.fdopen(descriptor, "rb", closefd=False) as source:
                            try:
                                documents[side] = json.load(source)
                                result[side]["value"] = _pointer(documents[side], binding.get("pointer"))
                            except (ValueError, KeyError, IndexError, TypeError, UnicodeDecodeError) as exc:
                                result[side]["error"] = str(exc)
                else:
                    result[side]["error"] = "Frozen metric file is unavailable or too large"
            if len(documents) == 2 and all(isinstance(document, dict) for document in documents.values()):
                conflicting = [key for key in ("schema", "split", "manifest_sha256", "corpus_id", "n_evaluated")
                               if key in documents["baseline"] and key in documents["current"]
                               and documents["baseline"][key] != documents["current"][key]]
                if entry.get("split") and any(
                    document.get("split") not in (None, entry["split"]) for document in documents.values()
                ):
                    conflicting.append("declared_split")
                if conflicting:
                    result["comparison_warning"] = "Different measurement contexts: " + ", ".join(conflicting)
                    result["baseline"]["value"] = result["current"]["value"] = None
            output["metrics"].append(result)
        return {"status": "ready", "value": output}
    except FileNotFoundError:
        return {"status": "missing"}
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "message": str(exc)}
