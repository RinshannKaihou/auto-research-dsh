"""Bounded, keyset-paginated reads for the native plugin.

Each request owns an ordinary SQLite read transaction.  ``upper_id`` freezes a
single traversal boundary; it does not claim a snapshot shared with later RPCs.
"""

from __future__ import annotations

import base64
import json
import sqlite3
from typing import Any

from . import frozen_refs
from .errors import NotFoundError, ValidationError
from .memory_store import parse_knowledge_ref


COLLECTIONS = {
    "hints": (None, None),  # Derived current-state collection, not a SQL table.
    "nodes": ("nodes", "rowid"),
    "attempts": ("attempts", "rowid"),
    "notes": ("notes", "rowid"),
    "publications": ("publications", "rowid"),
    "relations": ("relations", "rowid"),
    "dependencies": ("node_dependencies", "rowid"),
    "consumptions": ("material_consumptions", "rowid"),
    "usage_gaps": ("usage_coverage_gaps", "rowid"),
    "legacy_refs": ("legacy_refs", "rowid"),
    "snapshots": ("snapshots", "rowid"),
    "restorations": ("restorations", "rowid"),
    "associations": ("associations", "rowid"),
    "sessions": ("workflow_sessions", "rowid"),
    "tasks": ("exploration_tasks", "rowid"),
    "notifications": ("workflow_notifications", "rowid"),
    "specialists": ("specialist_tasks", "rowid"),
    "knowledge": ("knowledge_revisions", "rowid"),
    "checkpoints": ("node_checkpoints", "rowid"),
    "review_todos": ("review_todos", "rowid"),
    "impacts": ("knowledge_impacts", "rowid"),
}


def _bounded_text(value: str, expand_ref: str, maximum: int = 8192) -> dict | str:
    if len(value.encode("utf-8")) <= maximum:
        return value
    preview = value.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")
    return {"preview": preview, "truncated": True, "expand_ref": expand_ref}


class QueryStore:
    @staticmethod
    def _decode_page_row(
        db: sqlite3.Connection, collection: str, row: sqlite3.Row, *, full: bool = False
    ) -> dict:
        value = dict(row)
        cursor = value.pop("_cursor")
        if "asserted_at" in value:
            value["asserted_at"] = json.loads(value["asserted_at"])
        if "execution_refs" in value:
            value["execution_refs"] = json.loads(value["execution_refs"])
        if collection == "impacts":
            # The row alone cannot be read: an affected version means nothing
            # without the path back to what changed, and the two predicates are
            # derived, never stored.
            from . import epistemic

            roots = epistemic.change_roots(db, value["change_id"])
            version = value["affected_version"]
            value["explanation"] = epistemic.explain_path(db, version, roots)
            value["scope_unconfirmed"] = (
                epistemic.effective_scope(db, value["change_id"])["mode"] == "unknown"
            )
            value["residual_use_risk"] = epistemic.residual_use_risk(db, version)
            value["version_notices"] = epistemic.version_notices(db, version)
            disposition = epistemic.current_disposition(db, value["change_id"], version)
            value["disposition"] = disposition["kind"] if disposition else None
            value["in_use"] = epistemic.in_use(db, version)["in_use"]
            # Rows voided by a narrowing stay on disk for replay (R9); the
            # default view must not show them as live work.
            bound = epistemic.read_bound(db)
            value["valid"] = any(
                row["impact_id"] == value["impact_id"]
                for row in epistemic.valid_impacts(db, bound, version=version)
            )
        elif collection == "nodes":
            value["inputs"] = json.loads(value["inputs"])
        elif collection == "attempts":
            value["details"] = json.loads(value["details"])
        elif collection == "dependencies":
            value["input_refs"] = json.loads(value["input_refs"])
            value["scheduling"] = bool(value["scheduling"])
        elif collection == "usage_gaps":
            value["details"] = json.loads(value["details"])
        elif collection == "publications":
            value["gaps"] = json.loads(value["gaps"])
            value["items"] = []
            for item in db.execute(
                "SELECT * FROM publication_items WHERE publication_id=? ORDER BY item_id",
                (value["publication_id"],),
            ):
                decoded = {
                    **dict(item),
                    "content": json.loads(item["content"]),
                    "knowledge_refs": json.loads(item["knowledge_refs"]),
                    "ref": f"pub/{item['publication_id']}#{item['item_id']}",
                }
                encoded_content = json.dumps(decoded["content"], ensure_ascii=False, sort_keys=True)
                if not full and len(encoded_content.encode("utf-8")) > 8192:
                    decoded["content"] = {
                        "preview": encoded_content.encode("utf-8")[:8192].decode(
                            "utf-8", errors="ignore"
                        ),
                        "truncated": True,
                        "expand_ref": decoded["ref"],
                    }
                value["items"].append(decoded)
        elif collection == "snapshots":
            value["manifest"] = json.loads(value["manifest"])
            value["complete"] = bool(value["complete"])
        elif collection == "legacy_refs":
            value["item"] = json.loads(value["item"])
        elif collection == "tasks":
            value["context"] = json.loads(value["context"])
        elif collection == "sessions":
            value["waiting"] = json.loads(value["waiting"])
            value["context"] = json.loads(value["context"])
        elif collection == "notifications":
            value["payload"] = json.loads(value["payload"])
        elif collection == "specialists":
            value["inputs"] = json.loads(value["inputs"])
            value["result"] = json.loads(value["result"]) if value["result"] else None
            value["exit_verified"] = bool(value["exit_verified"])
        elif collection == "knowledge":
            entry = db.execute(
                "SELECT kind,node_id FROM knowledge_entries WHERE knowledge_id=?",
                (value["knowledge_id"],),
            ).fetchone()
            value.update(dict(entry))
            for key in (
                "scope",
                "conditions",
                "evidence_refs",
                "source_identity",
                "dependencies",
                "supersedes",
            ):
                value[key] = json.loads(value[key])
            value["ref"] = f"knowledge/{value['knowledge_id']}@{value['revision']}"
        elif collection == "checkpoints":
            value["state"] = json.loads(value["state"])
            value["source_identity"] = json.loads(value["source_identity"])
        expand_ref = {
            "nodes": value.get("node_id"),
            "attempts": value.get("attempt_id"),
            "notes": value.get("note_id"),
            "publications": f"pub/{value.get('publication_id')}",
            "relations": value.get("relation_id"),
            "dependencies": value.get("dependency_id"),
            "consumptions": value.get("consumption_id"),
            "usage_gaps": value.get("gap_id"),
            "legacy_refs": value.get("ref"),
            "snapshots": value.get("snapshot_id"),
            "restorations": value.get("restoration_id"),
            "associations": value.get("association_id"),
            "sessions": value.get("session_id"),
            "tasks": value.get("task_id"),
            "notifications": value.get("notification_id"),
            "specialists": value.get("task_id"),
            "knowledge": value.get("ref"),
            "checkpoints": value.get("checkpoint_id"),
            "review_todos": value.get("todo_id"),
        }.get(collection) or f"{collection}/{cursor}"
        if not full:
            for key in ("body", "summary", "note", "statement", "prompt", "error"):
                if isinstance(value.get(key), str):
                    value[key] = _bounded_text(value[key], expand_ref)
            for key in (
                "content",
                "manifest",
                "details",
                "context",
                "payload",
                "inputs",
                "result",
                "state",
                "scope",
                "conditions",
                "evidence_refs",
                "dependencies",
            ):
                if key in value and not isinstance(value[key], str):
                    encoded = json.dumps(value[key], ensure_ascii=False, sort_keys=True)
                    if len(encoded.encode("utf-8")) > 8192:
                        value[key] = {
                            "preview": encoded.encode("utf-8")[:8192].decode(
                                "utf-8", errors="ignore"
                            ),
                            "truncated": True,
                            "expand_ref": expand_ref,
                        }
        value["cursor"] = cursor
        return value

    def hints_query(self, *, limit: int = 8, offset: int = 0, node_id=None, klass=None) -> dict:
        from .hints import structure_hints
        with self._read() as db:
            page = structure_hints(db, limit=max(1, min(int(limit), 200)),
                                   offset=offset, node_id=node_id, klass=klass)
        return {"collection": "hints", **page,
                "cursor": {"offset": page["next_offset"]} if page["next_offset"] is not None else None}

    def history_page(
        self,
        collection: str,
        *,
        after: int = 0,
        upper_id: int | None = None,
        limit: int = 50,
        project_id: str | None = None,
        order: str | None = None,
        offset: int = 0,
    ) -> dict:
        if collection == "hints":
            return self.hints_query(limit=limit, offset=offset)
        if collection not in COLLECTIONS:
            raise ValidationError(f"Unsupported history collection: {collection}")
        table, key = COLLECTIONS[collection]
        after = max(0, int(after or 0))
        limit = max(1, min(int(limit or 50), 200))
        with self._read() as db:
            actual_project = self._project(db)["project_id"]
            if project_id is not None and project_id != actual_project:
                raise ValidationError("Cursor belongs to another research project")
            if order is not None and order != "rowid-asc":
                raise ValidationError("Cursor order does not match this collection")
            if upper_id is None:
                upper_id = int(
                    db.execute(f"SELECT COALESCE(MAX({key}),0) FROM {table}").fetchone()[0]
                )
            else:
                upper_id = max(0, int(upper_id))
            rows = list(
                db.execute(
                    f"SELECT {key} AS _cursor,* FROM {table} "
                    f"WHERE {key}>? AND {key}<=? ORDER BY {key} LIMIT ?",
                    (after, upper_id, limit + 1),
                )
            )
            visible = rows[:limit]
            items = [self._decode_page_row(db, collection, row) for row in visible]
            next_after = int(visible[-1]["_cursor"]) if len(rows) > limit and visible else None
            return {
                "collection": collection,
                "items": items,
                "cursor": {
                    "project_id": actual_project,
                    "collection": collection,
                    "after": next_after,
                    "upper_id": upper_id,
                    "order": "rowid-asc",
                }
                if next_after is not None
                else None,
                "upper_id": upper_id,
                "has_more": len(rows) > limit,
            }

    def usage_page(self, *, after: int = 0, upper_id: int | None = None, limit: int = 50) -> dict:
        after = max(0, int(after or 0))
        limit = max(1, min(int(limit or 50), 200))
        with self._read() as db:
            if upper_id is None:
                upper_id = int(
                    db.execute("SELECT COALESCE(MAX(rowid),0) FROM usage_observations").fetchone()[
                        0
                    ]
                )
            rows = list(
                db.execute(
                    "SELECT rowid AS _cursor,* FROM usage_observations "
                    "WHERE rowid>? AND rowid<=? ORDER BY rowid LIMIT ?",
                    (after, int(upper_id), limit + 1),
                )
            )
            items = []
            for row in rows[:limit]:
                value = dict(row)
                value["cursor"] = value.pop("_cursor")
                value["details"] = json.loads(value["details"])
                value["adjustments"] = [
                    {**dict(item), "details": json.loads(item["details"])}
                    for item in db.execute(
                        "SELECT * FROM usage_adjustments WHERE observation_id=? " "ORDER BY rowid",
                        (value["observation_id"],),
                    )
                ]
                items.append(value)
            next_after = items[-1]["cursor"] if len(rows) > limit and items else None
            return {
                "collection": "usage",
                "items": items,
                "cursor": {
                    "project_id": self._project(db)["project_id"],
                    "collection": "usage",
                    "after": next_after,
                    "upper_id": int(upper_id),
                    "order": "rowid-asc",
                }
                if next_after is not None
                else None,
                "upper_id": int(upper_id),
                "has_more": len(rows) > limit,
            }

    def changes_page(self, *, after: int = 0, limit: int = 50) -> dict:
        after = max(0, int(after or 0))
        limit = max(1, min(int(limit or 50), 200))
        with self._read() as db:
            rows = list(
                db.execute(
                    "SELECT * FROM events WHERE event_id>? ORDER BY event_id LIMIT ?",
                    (after, limit + 1),
                )
            )
            items = []
            for row in rows[:limit]:
                value = dict(row)
                value["data"] = json.loads(value["data"])
                items.append(value)
            next_after = items[-1]["event_id"] if len(rows) > limit and items else None
            return {
                "collection": "changes",
                "items": items,
                "cursor": {"after": next_after, "order": "event-id-asc"}
                if next_after is not None
                else None,
                "has_more": len(rows) > limit,
            }

    def _frozen_query(self, db, ref, *, full=False):
        resolution = frozen_refs.resolve(db, self.root, ref)
        if resolution["outcome"] == "not_found":
            raise NotFoundError(resolution["message"])
        if resolution["outcome"] == "unsupported":
            raise ValidationError(resolution["message"])
        kind = resolution["kind"].replace("_", "-")
        value = {}
        if resolution.get("publication_id"):
            row = db.execute("SELECT rowid AS _cursor,* FROM publications WHERE publication_id=?",
                             (resolution["publication_id"],)).fetchone()
            if row:
                value = self._decode_page_row(db, "publications", row, full=full)
                if resolution.get("item_id"):
                    value = next((i for i in value["items"] if i["item_id"] == resolution["item_id"]), {})
        elif resolution.get("snapshot_id"):
            if resolution["kind"] == "snapshot":
                row = db.execute("SELECT rowid AS _cursor,* FROM snapshots WHERE snapshot_id=?",
                                 (resolution["snapshot_id"],)).fetchone()
                value = self._decode_page_row(db, "snapshots", row, full=full)
            else:
                value = {**resolution.get("entry", {}), "snapshot_id": resolution["snapshot_id"]}
        if resolution.get("subpath"):
            value["subpath"] = resolution["subpath"]
        return {"kind": kind, "value": value, "resolution": resolution}

    def reference_query(self, ref: str, *, full: bool = False) -> dict:
        with self._read() as db:
            if frozen_refs.is_frozen(ref) and not db.execute(
                "SELECT 1 FROM specialist_tasks WHERE task_id=?", (ref,)
            ).fetchone():
                return self._frozen_query(db, ref, full=full)
            if ref.startswith("knowledge/"):
                kid, revision = parse_knowledge_ref(ref)
                row = db.execute(
                    "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e "
                    "JOIN knowledge_revisions r USING(knowledge_id) "
                    "WHERE e.knowledge_id=? AND r.revision=?",
                    (kid, revision),
                ).fetchone()
                if row:
                    return {"kind": "knowledge", "value": self._decode_knowledge(row)}
            node = db.execute(
                "SELECT rowid AS _cursor,* FROM nodes WHERE node_id=?", (ref,)
            ).fetchone()
            if node:
                return {
                    "kind": "node",
                    "value": self._decode_page_row(db, "nodes", node, full=full),
                }
            note = db.execute(
                "SELECT rowid AS _cursor,* FROM notes WHERE note_id=?", (ref,)
            ).fetchone()
            if note:
                return {
                    "kind": "note",
                    "value": self._decode_page_row(db, "notes", note, full=full),
                }
            attempt = db.execute(
                "SELECT rowid AS _cursor,* FROM attempts WHERE attempt_id=?", (ref,)
            ).fetchone()
            if attempt:
                return {
                    "kind": "attempt",
                    "value": self._decode_page_row(db, "attempts", attempt, full=full),
                }
            for kind, collection, column in (
                ("relation", "relations", "relation_id"),
                ("restoration", "restorations", "restoration_id"),
                ("association", "associations", "association_id"),
                ("session", "sessions", "session_id"),
                ("exploration-task", "tasks", "task_id"),
                ("notification", "notifications", "notification_id"),
                ("specialist-task", "specialists", "task_id"),
                ("checkpoint", "checkpoints", "checkpoint_id"),
                ("review-todo", "review_todos", "todo_id"),
            ):
                table, _ = COLLECTIONS[collection]
                row = db.execute(
                    f"SELECT rowid AS _cursor,* FROM {table} WHERE {column}=? ORDER BY rowid DESC LIMIT 1",
                    (ref,),
                ).fetchone()
                if row:
                    return {
                        "kind": kind,
                        "value": self._decode_page_row(db, collection, row, full=full),
                    }
            legacy = db.execute("SELECT * FROM legacy_refs WHERE ref=?", (ref,)).fetchone()
            if legacy:
                value = dict(legacy)
                value["item"] = json.loads(value["item"])
                return {"kind": "legacy-ref", "value": value}
        raise NotFoundError(f"Unknown research reference: {ref}")

    def reference_chunk(self, ref: str, *, offset: int = 0, limit: int = 8192) -> dict:
        """Expand one fixed reference without allowing an unbounded IPC response."""
        offset = max(0, int(offset or 0))
        limit = max(1, min(int(limit or 8192), 32768))
        selected = self.reference_query(ref, full=True)
        encoded = json.dumps(selected, ensure_ascii=False, sort_keys=True).encode("utf-8")
        chunk = base64.b64encode(encoded[offset : offset + limit]).decode("ascii")
        next_offset = offset + limit if offset + limit < len(encoded) else None
        return {
            "ref": ref,
            "encoding": "base64-json-utf8",
            "offset": offset,
            "chunk": chunk,
            "next_offset": next_offset,
            "total_bytes": len(encoded),
        }

    def control_state(self, host_id: str | None = None, session_id: str | None = None) -> dict:
        with self._read() as db:
            project = self._project(db)
            association = attempt = owned_goal = None
            if host_id and session_id:
                association = db.execute(
                    "SELECT * FROM associations WHERE host_id=? AND session_id=? "
                    "AND ended_at IS NULL",
                    (host_id, session_id),
                ).fetchone()
                if association:
                    attempt = db.execute(
                        "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                        (association["association_id"],),
                    ).fetchone()
                    owned_goal = db.execute(
                        "SELECT * FROM owned_goals WHERE association_id=?",
                        (association["association_id"],),
                    ).fetchone()
            from .hints import structure_hints
            hints = structure_hints(db)
            workflow = self.workflow_control(db, session_id)
            usage = db.execute(
                "SELECT COALESCE(SUM(amount),0) known,"
                "SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) unknown_count,"
                "SUM(CASE WHEN completeness='estimated' THEN 1 ELSE 0 END) estimated_count "
                "FROM usage_observations"
            ).fetchone()
            usage_value = {
                "known": float(usage["known"]),
                "unknown_count": int(usage["unknown_count"] or 0),
                "estimated_count": int(usage["estimated_count"] or 0),
            }
            usage_value.update(self.workflow_usage(db))
            specialists = [
                {
                    key: row[key]
                    for key in (
                        "task_id",
                        "parent_session_id",
                        "child_session_id",
                        "node_id",
                        "attempt_id",
                        "state",
                        "exit_verified",
                    )
                }
                for row in db.execute(
                    "SELECT * FROM specialist_tasks WHERE state IN ('starting','running','unverified') "
                    "ORDER BY created_at LIMIT 100"
                )
            ]
            return {
                "schema_version": 8,
                "project": project,
                "association": dict(association) if association else None,
                "attempt": {**dict(attempt), "details": json.loads(attempt["details"])}
                if attempt
                else None,
                "owned_goal": dict(owned_goal) if owned_goal else None,
                "workflow": workflow,
                "usage": usage_value,
                "specialists": specialists,
                "review_queue": {
                    "pending_total": int(
                        db.execute("SELECT COUNT(*) FROM review_todos WHERE state='pending'").fetchone()[0]
                    ),
                    "running_total": int(
                        db.execute("SELECT COUNT(*) FROM review_todos WHERE state='running'").fetchone()[0]
                    ),
                    "semantics": "advisory-consolidation-queue",
                },
                "structure_hints": hints,
                "counts": {
                    **{collection: int(db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
                       for collection, (table, _) in COLLECTIONS.items() if table is not None},
                    "hints": hints["total"],
                },
                "pagination": {
                    "default_limit": 50,
                    "maximum_limit": 200,
                    "collections": list(COLLECTIONS) + ["usage", "changes"],
                },
            }
