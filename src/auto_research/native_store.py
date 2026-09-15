"""Schema-4 domain store for the native DSH research plugin.

DSH owns model execution, tools, sessions, and transcripts.  This module only
owns research metadata, immutable publications, observations, and idempotency.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sqlite3
from typing import Any, Callable, Iterator
from uuid import uuid4

from .errors import ConflictError, NotFoundError, ValidationError
from .memory_store import MemoryStore, migrate_schema4, parse_knowledge_ref
from .query_store import QueryStore


SCHEMA_VERSION = 4
ATTEMPT_STATES = frozenset({"open", "finished", "stopped", "unknown"})
NODE_STATES = frozenset({"proposed", "open", "closed"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Value is not finite JSON: {exc}") from exc


def _copy(value: Any) -> Any:
    return json.loads(_json(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be nonempty text")
    return value.strip()


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a finite nonnegative number")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValidationError(f"{label} must be a finite nonnegative number")
    return result


from .workflow_store import WorkflowStore, DDL, migrate_schema3


class NativeStore(QueryStore, MemoryStore, WorkflowStore):
    """Transactional store used only by the native DSH plugin."""

    def __init__(self, root: str | Path, *, readonly: bool = False):
        self.root = Path(root).expanduser().resolve()
        self.meta = self.root / ".research"
        self.readonly = readonly
        self.db_path = self.meta / "state.sqlite3"
        if readonly:
            with self._connection() as db:
                if db.execute("PRAGMA user_version").fetchone()[0] != SCHEMA_VERSION:
                    raise ValidationError("Read-only view requires schema 4")
            return
        self.meta.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            with sqlite3.connect(self.db_path) as probe:
                version = int(probe.execute("PRAGMA user_version").fetchone()[0])
            if version == 2:
                migrate_schema3(self.db_path)
                version = 3
            if version == 3:
                migrate_schema4(self.db_path)
                version = 4
            if version not in {0, SCHEMA_VERSION}:
                raise ValidationError(
                    f"Schema {version} must be migrated before native plugin writes"
                )
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            self._create_schema(db)
            has_project = bool(db.execute("SELECT 1 FROM project LIMIT 1").fetchone())
        if has_project:
            self.rebuild_memory_projection()

    @staticmethod
    def _create_schema(db: sqlite3.Connection) -> None:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS project (
                project_id TEXT PRIMARY KEY, goal TEXT NOT NULL,
                budget_limit REAL NOT NULL CHECK(budget_limit >= 0),
                control TEXT NOT NULL CHECK(control IN ('manual','auto','paused','stopped')),
                config TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS nodes (
                node_id TEXT PRIMARY KEY, question TEXT NOT NULL, why_now TEXT NOT NULL,
                plan TEXT NOT NULL, inputs TEXT NOT NULL, purpose TEXT NOT NULL,
                strategy TEXT NOT NULL, anchor_ref TEXT, question_ref TEXT,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL, closed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS associations (
                association_id TEXT PRIMARY KEY, host_id TEXT NOT NULL,
                session_id TEXT NOT NULL, started_seq INTEGER,
                ended_seq INTEGER, started_at TEXT NOT NULL, ended_at TEXT,
                UNIQUE(host_id, session_id, started_at)
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_association
                ON associations(host_id, session_id) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS attempts (
                attempt_id TEXT PRIMARY KEY,
                association_id TEXT REFERENCES associations(association_id),
                node_id TEXT REFERENCES nodes(node_id), role TEXT NOT NULL,
                state TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('manual','auto')),
                started_at TEXT NOT NULL, ended_at TEXT, details TEXT NOT NULL
            );
            CREATE UNIQUE INDEX IF NOT EXISTS one_live_attempt_per_association
                ON attempts(association_id) WHERE ended_at IS NULL;
            CREATE TABLE IF NOT EXISTS focus_queue (
                association_id TEXT PRIMARY KEY REFERENCES associations(association_id),
                node_id TEXT REFERENCES nodes(node_id), role TEXT NOT NULL,
                mode TEXT NOT NULL, requested_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS turn_bindings (
                host_id TEXT NOT NULL, session_id TEXT NOT NULL, turn INTEGER NOT NULL,
                association_id TEXT NOT NULL REFERENCES associations(association_id),
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                node_id TEXT REFERENCES nodes(node_id), started_at TEXT NOT NULL,
                PRIMARY KEY(host_id, session_id, turn)
            );
            CREATE TABLE IF NOT EXISTS notes (
                note_id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                body TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publications (
                publication_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                node_id TEXT REFERENCES nodes(node_id), status TEXT NOT NULL,
                summary TEXT NOT NULL, gaps TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS publication_items (
                publication_id TEXT NOT NULL REFERENCES publications(publication_id),
                item_id TEXT NOT NULL, kind TEXT NOT NULL, content TEXT NOT NULL,
                object_version TEXT, object_kind TEXT, source_path TEXT,
                knowledge_refs TEXT NOT NULL DEFAULT '[]',
                PRIMARY KEY(publication_id, item_id)
            );
            CREATE TABLE IF NOT EXISTS relations (
                relation_id TEXT PRIMARY KEY, source_ref TEXT NOT NULL,
                target_ref TEXT NOT NULL, label TEXT NOT NULL, note TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS legacy_refs (
                ref TEXT PRIMARY KEY, node_id TEXT,
                local_id TEXT NOT NULL, kind TEXT NOT NULL,
                item TEXT NOT NULL, imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                manifest TEXT NOT NULL, complete INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS restorations (
                restoration_id TEXT PRIMARY KEY,
                snapshot_id TEXT NOT NULL REFERENCES snapshots(snapshot_id),
                source_attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id),
                target_association_id TEXT NOT NULL REFERENCES associations(association_id),
                target_attempt_id TEXT REFERENCES attempts(attempt_id),
                workspace TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_observations (
                observation_id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE,
                association_id TEXT REFERENCES associations(association_id),
                attempt_id TEXT REFERENCES attempts(attempt_id),
                node_id TEXT REFERENCES nodes(node_id), purpose TEXT NOT NULL,
                provider TEXT, model TEXT, amount REAL,
                completeness TEXT NOT NULL, details TEXT NOT NULL,
                observed_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS usage_adjustments (
                adjustment_id TEXT PRIMARY KEY,
                observation_id TEXT NOT NULL REFERENCES usage_observations(observation_id),
                previous_amount REAL, amount REAL,
                completeness TEXT NOT NULL, details TEXT NOT NULL,
                adjusted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS owned_goals (
                association_id TEXT PRIMARY KEY REFERENCES associations(association_id),
                goal_id TEXT NOT NULL, revision INTEGER NOT NULL,
                phase TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                data TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
                payload TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS counters (
                name TEXT PRIMARY KEY, value INTEGER NOT NULL
            );
            PRAGMA user_version=4;
            """
        )
        db.executescript(DDL)
        # Schema 2 was developed incrementally before its first release. Keep
        # those local databases readable without inventing another public
        # schema version.
        node_columns = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
        if "strategy" not in node_columns:
            db.execute("ALTER TABLE nodes ADD COLUMN strategy TEXT NOT NULL DEFAULT 'continue'")
        if "anchor_ref" not in node_columns:
            db.execute("ALTER TABLE nodes ADD COLUMN anchor_ref TEXT")
        if "question_ref" not in node_columns:
            db.execute("ALTER TABLE nodes ADD COLUMN question_ref TEXT")
        item_columns = {row[1] for row in db.execute("PRAGMA table_info(publication_items)")}
        if "object_kind" not in item_columns:
            db.execute("ALTER TABLE publication_items ADD COLUMN object_kind TEXT")
        if "knowledge_refs" not in item_columns:
            db.execute(
                "ALTER TABLE publication_items ADD COLUMN knowledge_refs TEXT NOT NULL DEFAULT '[]'"
            )
        MemoryStore._create_memory_schema(db)
        db.execute("PRAGMA user_version=4")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(
            self.db_path.as_uri() + "?mode=ro" if self.readonly else self.db_path,
            uri=self.readonly,
            timeout=30,
            isolation_level=None,
        )
        if self.readonly:
            db.execute("PRAGMA query_only=ON")
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA synchronous=FULL")
        try:
            yield db
        finally:
            db.close()

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            db.execute("BEGIN")
            try:
                yield db
            finally:
                db.rollback()

    def _mutate(
        self,
        operation: str,
        payload: Any,
        request_id: str,
        work: Callable[[sqlite3.Connection], Any],
    ) -> Any:
        request_id = _text(request_id, "request_id")
        encoded = _json(payload)
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT operation,payload,response FROM requests WHERE request_id=?",
                    (request_id,),
                ).fetchone()
                if previous:
                    if previous["operation"] != operation or previous["payload"] != encoded:
                        raise ConflictError(f"request_id {request_id!r} was reused")
                    result = json.loads(previous["response"])
                    db.commit()
                    if operation in {"propose", "knowledge.record", "knowledge.revise", "memory.checkpoint", "guidance.register"}:
                        self.rebuild_memory_projection()
                    return result
                result = work(db)
                db.execute(
                    "INSERT INTO requests VALUES (?,?,?,?,?)",
                    (request_id, operation, encoded, _json(result), _now()),
                )
                db.commit()
                if operation in {"propose", "knowledge.record", "knowledge.revise", "memory.checkpoint", "guidance.register"}:
                    self.rebuild_memory_projection()
                return result
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _next(db: sqlite3.Connection, name: str, prefix: str) -> str:
        db.execute("INSERT INTO counters VALUES (?,0) ON CONFLICT(name) DO NOTHING", (name,))
        db.execute("UPDATE counters SET value=value+1 WHERE name=?", (name,))
        value = db.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]
        return f"{prefix}-{value:03d}"

    @staticmethod
    def _event(db: sqlite3.Connection, kind: str, data: Any) -> None:
        db.execute(
            "INSERT INTO events(kind,data,created_at) VALUES (?,?,?)", (kind, _json(data), _now()),
        )

    @staticmethod
    def _project(db: sqlite3.Connection) -> dict:
        row = db.execute("SELECT * FROM project LIMIT 1").fetchone()
        if not row:
            raise NotFoundError("Project is not initialized")
        value = dict(row)
        value["config"] = json.loads(value["config"])
        # Kept physically for schema-2 database compatibility. Quotas are no
        # longer part of the native plugin API or any runtime decision.
        value.pop("budget_limit", None)
        return value

    @staticmethod
    def _association(db: sqlite3.Connection, host_id: str, session_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM associations WHERE host_id=? AND session_id=? AND ended_at IS NULL",
            (host_id, session_id),
        ).fetchone()
        if not row:
            raise NotFoundError("Session is not associated with this project")
        return row

    @staticmethod
    def _attempt(db: sqlite3.Connection, association_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL", (association_id,),
        ).fetchone()
        if not row:
            raise NotFoundError("No active research work segment")
        return row

    def initialize(self, goal: str, request_id: str) -> dict:
        payload = {"goal": _text(goal, "goal")}

        def work(db: sqlite3.Connection) -> dict:
            if db.execute("SELECT 1 FROM project").fetchone():
                raise ConflictError("Project is already initialized")
            value = {
                "project_id": str(uuid4()),
                **payload,
                "control": "manual",
                "config": {},
                "created_at": _now(),
            }
            db.execute(
                "INSERT INTO project VALUES (?,?,?,?,?,?)",
                (
                    value["project_id"],
                    value["goal"],
                    0,
                    value["control"],
                    _json(value["config"]),
                    value["created_at"],
                ),
            )
            self._event(db, "project.initialized", value)
            return value

        return self._mutate("initialize", payload, request_id, work)

    def associate(
        self, host_id: str, session_id: str, request_id: str, started_seq: int | None = None
    ) -> dict:
        payload = {
            "host_id": _text(host_id, "host_id"),
            "session_id": _text(session_id, "session_id"),
            "started_seq": started_seq,
        }

        def work(db: sqlite3.Connection) -> dict:
            self._project(db)
            existing = db.execute(
                "SELECT * FROM associations WHERE host_id=? AND session_id=? AND ended_at IS NULL",
                (payload["host_id"], payload["session_id"]),
            ).fetchone()
            if existing:
                return dict(existing)
            value = {
                "association_id": str(uuid4()),
                **payload,
                "ended_seq": None,
                "started_at": _now(),
                "ended_at": None,
            }
            db.execute("INSERT INTO associations VALUES (?,?,?,?,?,?,?)", tuple(value.values()))
            self._event(db, "session.associated", value)
            return value

        return self._mutate("associate", payload, request_id, work)

    def detach(
        self, host_id: str, session_id: str, request_id: str, ended_seq: int | None = None
    ) -> dict:
        payload = {"host_id": host_id, "session_id": session_id, "ended_seq": ended_seq}

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            if db.execute(
                "SELECT 1 FROM attempts WHERE association_id=? AND ended_at IS NULL",
                (association["association_id"],),
            ).fetchone():
                raise ConflictError("Finish the active work segment before detaching")
            at = _now()
            db.execute(
                "UPDATE associations SET ended_seq=?,ended_at=? WHERE association_id=?",
                (ended_seq, at, association["association_id"]),
            )
            value = {"association_id": association["association_id"], "ended_at": at}
            self._event(db, "session.detached", value)
            return value

        return self._mutate("detach", payload, request_id, work)

    def propose(
        self,
        question: str,
        why_now: str,
        plan: str,
        request_id: str,
        inputs: list[str] | None = None,
        purpose: str = "explore",
        strategy: str = "continue",
        anchor_ref: str | None = None,
        question_ref: str | None = None,
    ) -> dict:
        if inputs is None:
            inputs = []
        if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
            raise ValidationError("inputs must be research reference strings")
        if strategy not in {"continue", "redirect", "anchor"}:
            raise ValidationError("strategy must be continue, redirect, or anchor")
        if strategy == "anchor" and not anchor_ref:
            raise ValidationError("anchor strategy requires anchor_ref")
        payload = {
            "question": _text(question, "question"),
            "why_now": _text(why_now, "why_now"),
            "plan": _text(plan, "plan"),
            "inputs": inputs,
            "purpose": _text(purpose, "purpose"),
            "strategy": strategy,
            "anchor_ref": anchor_ref,
            "question_ref": question_ref,
        }

        def work(db: sqlite3.Connection) -> dict:
            self._project(db)
            node_id = self._next(db, "node", "X")
            qref = payload["question_ref"]
            if qref:
                kid, revision = parse_knowledge_ref(qref)
                question_row = db.execute(
                    "SELECT e.kind,r.statement FROM knowledge_entries e "
                    "JOIN knowledge_revisions r USING(knowledge_id) "
                    "WHERE e.knowledge_id=? AND r.revision=?",
                    (kid, revision),
                ).fetchone()
                if not question_row:
                    raise NotFoundError(f"Unknown research reference: {qref}")
                if question_row["kind"] != "open_question":
                    raise ValidationError("question_ref must identify an open_question")
            else:
                knowledge = self._record_knowledge_in_tx(
                    db,
                    {
                        "kind": "open_question",
                        "statement": payload["question"],
                        "scope": {"purpose": payload["purpose"]},
                        "conditions": {},
                        "status": "working",
                        "evidence_refs": [],
                        "source_identity": {"kind": "node-proposal", "request_id": request_id},
                        "dependencies": list(payload["inputs"]),
                        "author": "research-agent",
                        "node_id": None,
                    },
                )
                qref = knowledge["ref"]
                kid, revision = parse_knowledge_ref(qref)
            value = {
                "node_id": node_id,
                **payload,
                "question_ref": qref,
                "status": "proposed",
                "created_at": _now(),
                "closed_at": None,
            }
            for ref in inputs:
                self._require_ref(db, ref)
            if anchor_ref:
                self._require_ref(db, anchor_ref)
            db.execute(
                "INSERT INTO nodes "
                "(node_id,question,why_now,plan,inputs,purpose,strategy,anchor_ref,question_ref,"
                "status,created_at,closed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["node_id"],
                    value["question"],
                    value["why_now"],
                    value["plan"],
                    _json(value["inputs"]),
                    value["purpose"],
                    value["strategy"],
                    value["anchor_ref"],
                    value["question_ref"],
                    value["status"],
                    value["created_at"],
                    value["closed_at"],
                ),
            )
            db.execute(
                "INSERT OR REPLACE INTO node_questions VALUES(?,?,?,?)",
                (node_id, kid, revision, value["created_at"]),
            )
            db.execute(
                "UPDATE knowledge_entries SET node_id=COALESCE(node_id,?) WHERE knowledge_id=?",
                (node_id, kid),
            )
            self._event(db, "node.proposed", value)
            return value

        return self._mutate("propose", payload, request_id, work)

    def focus(
        self,
        host_id: str,
        session_id: str,
        node_id: str | None,
        role: str,
        mode: str,
        request_id: str,
        *,
        defer: bool = False,
    ) -> dict:
        if mode not in {"manual", "auto"}:
            raise ValidationError("mode must be manual or auto")
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "node_id": node_id,
            "role": _text(role, "role"),
            "mode": mode,
            "defer": bool(defer),
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            if node_id is not None:
                node = db.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
                if not node:
                    raise NotFoundError(f"Unknown node: {node_id}")
                if node["status"] == "closed":
                    raise ConflictError("Closed nodes cannot receive new work")
            live = db.execute(
                "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                (association["association_id"],),
            ).fetchone()
            if defer:
                db.execute(
                    "INSERT INTO focus_queue VALUES (?,?,?,?,?) "
                    "ON CONFLICT(association_id) DO UPDATE SET "
                    "node_id=excluded.node_id,role=excluded.role,mode=excluded.mode,"
                    "requested_at=excluded.requested_at",
                    (association["association_id"], node_id, role, mode, _now()),
                )
                return {"queued": True, "current_attempt_id": live["attempt_id"] if live else None}
            if live:
                if live["node_id"] == node_id and live["mode"] == mode:
                    return dict(live)
                raise ConflictError("Finish the current work segment before changing focus")
            attempt_id = self._next(db, "attempt", "A")
            at = _now()
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    attempt_id,
                    association["association_id"],
                    node_id,
                    role,
                    "open",
                    mode,
                    at,
                    None,
                    "{}",
                ),
            )
            if node_id is not None:
                db.execute(
                    "UPDATE nodes SET status='open' WHERE node_id=? AND status='proposed'",
                    (node_id,),
                )
            value = {
                "attempt_id": attempt_id,
                "association_id": association["association_id"],
                "node_id": node_id,
                "role": role,
                "mode": mode,
                "state": "open",
                "started_at": at,
            }
            self._event(db, "attempt.started", value)
            return value

        return self._mutate("focus", payload, request_id, work)

    def bind_turn(self, host_id: str, session_id: str, turn: int, request_id: str) -> dict:
        payload = {"host_id": host_id, "session_id": session_id, "turn": int(turn)}

        def work(db: sqlite3.Connection) -> dict:
            existing = db.execute(
                "SELECT * FROM turn_bindings WHERE host_id=? AND session_id=? AND turn=?",
                (host_id, session_id, turn),
            ).fetchone()
            if existing:
                return dict(existing)
            association = self._association(db, host_id, session_id)
            attempt = self._attempt(db, association["association_id"])
            value = {
                **payload,
                "association_id": association["association_id"],
                "attempt_id": attempt["attempt_id"],
                "node_id": attempt["node_id"],
                "started_at": _now(),
            }
            db.execute("INSERT INTO turn_bindings VALUES (?,?,?,?,?,?,?)", tuple(value.values()))
            return value

        return self._mutate("bind_turn", payload, request_id, work)

    def note(self, host_id: str, session_id: str, body: str, kind: str, request_id: str) -> dict:
        payload = {"body": _text(body, "body"), "kind": _text(kind, "kind")}

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            attempt = self._attempt(db, association["association_id"])
            value = {
                "note_id": self._next(db, "note", "N"),
                "attempt_id": attempt["attempt_id"],
                **payload,
                "created_at": _now(),
            }
            db.execute("INSERT INTO notes VALUES (?,?,?,?,?)", tuple(value.values()))
            self._event(db, "research.noted", value)
            if kind == "correction":
                self.queue_review_in_tx(db, "correction-note", value["note_id"], attempt["node_id"])
            return value

        return self._mutate(
            "note", {"host_id": host_id, "session_id": session_id, **payload}, request_id, work
        )

    def finish(
        self, host_id: str, session_id: str, state: str, details: dict, request_id: str
    ) -> dict:
        if state not in ATTEMPT_STATES - {"open"}:
            raise ValidationError("Invalid terminal work-segment state")
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "state": state,
            "details": _copy(details),
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            attempt = self._attempt(db, association["association_id"])
            at = _now()
            db.execute(
                "UPDATE attempts SET state=?,ended_at=?,details=? WHERE attempt_id=?",
                (state, at, _json(details), attempt["attempt_id"]),
            )
            queued = db.execute(
                "SELECT * FROM focus_queue WHERE association_id=?", (association["association_id"],)
            ).fetchone()
            value = {"attempt_id": attempt["attempt_id"], "state": state, "ended_at": at}
            self.notify_in_transaction(
                db,
                session_id,
                {
                    "key": attempt["attempt_id"],
                    "kind": state,
                    "summary": details.get("reason", ""),
                    "gaps": [],
                },
            )
            if attempt["mode"] == "auto":
                db.execute(
                    "UPDATE workflow_sessions SET pause_reason=? WHERE session_id=?",
                    ("finished" if state == "finished" else "stop", session_id),
                )
                if state == "finished":
                    db.execute(
                        "UPDATE exploration_tasks SET state='finished',updated_at=? WHERE session_id=?",
                        (at, session_id),
                    )
            self._event(db, "attempt.finished", value)
            self.queue_review_in_tx(db, "attempt-finished", attempt["attempt_id"], attempt["node_id"])
            if queued:
                next_id = self._next(db, "attempt", "A")
                db.execute(
                    "INSERT INTO attempts VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        next_id,
                        association["association_id"],
                        queued["node_id"],
                        queued["role"],
                        "open",
                        queued["mode"],
                        at,
                        None,
                        "{}",
                    ),
                )
                db.execute(
                    "DELETE FROM focus_queue WHERE association_id=?",
                    (association["association_id"],),
                )
                if queued["node_id"] is not None:
                    db.execute(
                        "UPDATE nodes SET status='open' WHERE node_id=? AND status='proposed'",
                        (queued["node_id"],),
                    )
                value["next_attempt_id"] = next_id
                value["next_node_id"] = queued["node_id"]
            return value

        return self._mutate("finish", payload, request_id, work)

    def publish_metadata(
        self,
        host_id: str,
        session_id: str,
        status: str,
        summary: str,
        gaps: list[str],
        items: list[dict],
        request_id: str,
        knowledge_refs: list[str] | None = None,
    ) -> dict:
        if status not in {"partial", "complete"}:
            raise ValidationError("publication status must be partial or complete")
        if not isinstance(gaps, list) or not all(isinstance(x, str) for x in gaps):
            raise ValidationError("gaps must be text entries")
        knowledge_refs = knowledge_refs or []
        if not isinstance(knowledge_refs, list) or not all(
            isinstance(ref, str) for ref in knowledge_refs
        ):
            raise ValidationError("knowledge_refs must be research reference strings")
        normalized = []
        seen = set()
        for raw in items:
            if not isinstance(raw, dict):
                raise ValidationError("publication items must be objects")
            item_id = _text(raw.get("item_id"), "item_id")
            if item_id in seen:
                raise ValidationError(f"Duplicate item_id: {item_id}")
            seen.add(item_id)
            normalized.append(
                {
                    "item_id": item_id,
                    "kind": _text(raw.get("kind", "product"), "kind"),
                    "content": _copy(raw.get("content", {})),
                    "object_version": raw.get("object_version"),
                    "object_kind": raw.get("object_kind"),
                    "source_path": raw.get("source_path"),
                    "knowledge_refs": _copy(raw.get("knowledge_refs", [])),
                }
            )
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "status": status,
            "summary": _text(summary, "summary"),
            "gaps": gaps,
            "items": normalized,
            "knowledge_refs": knowledge_refs,
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            attempt = self._attempt(db, association["association_id"])
            for ref in knowledge_refs:
                kid, revision = parse_knowledge_ref(ref)
                if not db.execute(
                    "SELECT 1 FROM knowledge_revisions WHERE knowledge_id=? AND revision=?",
                    (kid, revision),
                ).fetchone():
                    raise NotFoundError(f"Unknown research reference: {ref}")
            publication_id = self._next(db, "publication", "P")
            at = _now()
            db.execute(
                "INSERT INTO publications VALUES (?,?,?,?,?,?,?)",
                (
                    publication_id,
                    attempt["attempt_id"],
                    attempt["node_id"],
                    status,
                    summary,
                    _json(gaps),
                    at,
                ),
            )
            refs = []
            for item in normalized:
                db.execute(
                    "INSERT INTO publication_items "
                    "(publication_id,item_id,kind,content,object_version,object_kind,source_path,knowledge_refs) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (
                        publication_id,
                        item["item_id"],
                        item["kind"],
                        _json(item["content"]),
                        item["object_version"],
                        item["object_kind"],
                        item["source_path"],
                        _json(item["knowledge_refs"]),
                    ),
                )
                for ref in item["knowledge_refs"]:
                    kid, revision = parse_knowledge_ref(ref)
                    if not db.execute(
                        "SELECT 1 FROM knowledge_revisions WHERE knowledge_id=? AND revision=?",
                        (kid, revision),
                    ).fetchone():
                        raise NotFoundError(f"Unknown research reference: {ref}")
                    db.execute(
                        "INSERT OR IGNORE INTO publication_knowledge VALUES(?,?,?)",
                        (publication_id, kid, revision),
                    )
                refs.append(f"pub/{publication_id}#{item['item_id']}")
            for ref in knowledge_refs:
                kid, revision = parse_knowledge_ref(ref)
                db.execute(
                    "INSERT OR IGNORE INTO publication_knowledge VALUES(?,?,?)",
                    (publication_id, kid, revision),
                )
            value = {"publication_id": publication_id, "refs": refs, "created_at": at}
            self.notify_in_transaction(
                db,
                session_id,
                {
                    "key": publication_id,
                    "kind": "published",
                    "reference": f"pub/{publication_id}",
                    "summary": summary,
                    "gaps": gaps,
                    "refs": refs,
                },
            )
            self._event(db, "publication.created", value)
            self.queue_review_in_tx(db, "publication", f"pub/{publication_id}", attempt["node_id"])
            return value

        return self._mutate("publish", payload, request_id, work)

    def relate(
        self, source_ref: str, target_ref: str, label: str, note: str, request_id: str
    ) -> dict:
        payload = {
            "source_ref": _text(source_ref, "source_ref"),
            "target_ref": _text(target_ref, "target_ref"),
            "label": _text(label, "label"),
            "note": _text(note, "note"),
        }

        def work(db: sqlite3.Connection) -> dict:
            self._require_ref(db, source_ref)
            self._require_ref(db, target_ref)
            value = {
                "relation_id": self._next(db, "relation", "R"),
                **payload,
                "created_at": _now(),
            }
            db.execute("INSERT INTO relations VALUES (?,?,?,?,?,?)", tuple(value.values()))
            self._event(db, "relation.created", value)
            return value

        return self._mutate("relate", payload, request_id, work)

    @staticmethod
    def _require_ref(db: sqlite3.Connection, ref: str) -> None:
        if ref.startswith("knowledge/"):
            kid, revision = parse_knowledge_ref(ref)
            if db.execute(
                "SELECT 1 FROM knowledge_revisions WHERE knowledge_id=? AND revision=?",
                (kid, revision),
            ).fetchone():
                return
        if ref.startswith("pub/"):
            publication_id, _, item_id = ref[4:].partition("#")
            if item_id and db.execute(
                "SELECT 1 FROM publication_items WHERE publication_id=? AND item_id=?",
                (publication_id, item_id),
            ).fetchone():
                return
            if not item_id and db.execute(
                "SELECT 1 FROM publications WHERE publication_id=?", (publication_id,)
            ).fetchone():
                return
        if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (ref,)).fetchone():
            return
        if db.execute("SELECT 1 FROM legacy_refs WHERE ref=?", (ref,)).fetchone():
            return
        for table, column in (("notes", "note_id"), ("attempts", "attempt_id"), ("snapshots", "snapshot_id")):
            if db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (ref,)).fetchone():
                return
        raise NotFoundError(f"Unknown research reference: {ref}")

    def close_node(self, node_id: str, request_id: str) -> dict:
        payload = {"node_id": _text(node_id, "node_id")}

        def work(db: sqlite3.Connection) -> dict:
            node = db.execute("SELECT * FROM nodes WHERE node_id=?", (node_id,)).fetchone()
            if not node:
                raise NotFoundError(f"Unknown node: {node_id}")
            if node["status"] == "closed":
                return {"node_id": node_id, "status": "closed", "closed_at": node["closed_at"]}
            active = db.execute(
                "SELECT 1 FROM attempts WHERE node_id=? AND ended_at IS NULL", (node_id,)
            ).fetchone()
            if active:
                raise ConflictError("Finish active work before closing its node")
            at = _now()
            db.execute(
                "UPDATE nodes SET status='closed',closed_at=? WHERE node_id=?", (at, node_id)
            )
            value = {"node_id": node_id, "status": "closed", "closed_at": at}
            self._event(db, "node.closed", value)
            return value

        return self._mutate("close_node", payload, request_id, work)

    def begin_usage(
        self,
        source_key: str,
        request_id: str,
        *,
        host_id: str | None = None,
        session_id: str | None = None,
        purpose: str = "conversation",
        provider: str | None = None,
        model: str | None = None,
        turn: int | None = None,
        step: int | None = None,
    ) -> dict:
        payload = {
            "source_key": _text(source_key, "source_key"),
            "host_id": host_id,
            "session_id": session_id,
            "purpose": purpose,
            "provider": provider,
            "model": model,
            "turn": turn,
            "step": step,
        }

        def work(db: sqlite3.Connection) -> dict:
            association_id = attempt_id = node_id = None
            if host_id and session_id:
                association = db.execute(
                    "SELECT * FROM associations WHERE host_id=? AND session_id=? "
                    "AND ended_at IS NULL",
                    (host_id, session_id),
                ).fetchone()
                if association:
                    association_id = association["association_id"]
                    attempt = db.execute(
                        "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                        (association_id,),
                    ).fetchone()
                    if attempt:
                        attempt_id, node_id = attempt["attempt_id"], attempt["node_id"]
                    else:
                        specialist = db.execute(
                            "SELECT attempt_id,node_id FROM specialist_bindings WHERE session_id=?",
                            (session_id,),
                        ).fetchone()
                        if specialist:
                            attempt_id, node_id = specialist["attempt_id"], specialist["node_id"]
            if host_id and session_id and turn is not None:
                frozen = db.execute(
                    "SELECT * FROM turn_bindings WHERE host_id=? AND session_id=? AND turn=?",
                    (host_id, session_id, turn),
                ).fetchone()
                if frozen:
                    association_id, attempt_id, node_id = (
                        frozen["association_id"],
                        frozen["attempt_id"],
                        frozen["node_id"],
                    )
            value = {
                "observation_id": str(uuid4()),
                "source_key": source_key,
                "association_id": association_id,
                "attempt_id": attempt_id,
                "node_id": node_id,
                "purpose": purpose,
                "provider": provider,
                "model": model,
                "amount": None,
                "completeness": "unknown",
                "details": {"phase": "started", "turn": turn, "step": step},
                "observed_at": _now(),
            }
            db.execute(
                "INSERT INTO usage_observations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    value["observation_id"],
                    value["source_key"],
                    value["association_id"],
                    value["attempt_id"],
                    value["node_id"],
                    value["purpose"],
                    value["provider"],
                    value["model"],
                    None,
                    value["completeness"],
                    _json(value["details"]),
                    value["observed_at"],
                ),
            )
            return value

        return self._mutate("begin_usage", payload, request_id, work)

    def finish_usage(
        self,
        source_key: str,
        amount: float | None,
        completeness: str,
        details: dict,
        request_id: str,
    ) -> dict:
        if amount is not None:
            amount = _number(amount, "amount")
        payload = {
            "source_key": _text(source_key, "source_key"),
            "amount": amount,
            "completeness": _text(completeness, "completeness"),
            "details": _copy(details),
        }

        def work(db: sqlite3.Connection) -> dict:
            observation = db.execute(
                "SELECT * FROM usage_observations WHERE source_key=?", (source_key,)
            ).fetchone()
            if not observation:
                raise NotFoundError(f"Unknown usage observation: {source_key}")
            at = _now()
            merged_details = {**json.loads(observation["details"]), **details, "phase": "completed"}
            db.execute(
                "INSERT INTO usage_adjustments VALUES (?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    observation["observation_id"],
                    observation["amount"],
                    amount,
                    completeness,
                    _json(details),
                    at,
                ),
            )
            db.execute(
                "UPDATE usage_observations SET amount=?,completeness=?,details=? "
                "WHERE observation_id=?",
                (amount, completeness, _json(merged_details), observation["observation_id"]),
            )
            value = {
                "observation_id": observation["observation_id"],
                "source_key": source_key,
                "association_id": observation["association_id"],
                "attempt_id": observation["attempt_id"],
                "node_id": observation["node_id"],
                "previous_amount": observation["amount"],
                "amount": amount,
                "completeness": completeness,
                "adjusted_at": at,
            }
            self._event(db, "usage.adjusted", value)
            return value

        return self._mutate("finish_usage", payload, request_id, work)

    def record_snapshot(
        self, host_id: str, session_id: str, manifest: list[dict], complete: bool, request_id: str,
    ) -> dict:
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "manifest": _copy(manifest),
            "complete": bool(complete),
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            attempt = self._attempt(db, association["association_id"])
            value = {
                "snapshot_id": self._next(db, "snapshot", "S"),
                "attempt_id": attempt["attempt_id"],
                "manifest": manifest,
                "complete": bool(complete),
                "created_at": _now(),
            }
            db.execute(
                "INSERT INTO snapshots VALUES (?,?,?,?,?)",
                (
                    value["snapshot_id"],
                    value["attempt_id"],
                    _json(manifest),
                    int(value["complete"]),
                    value["created_at"],
                ),
            )
            context = {
                "goal": self._project(db)["goal"],
                "source_attempt_id": attempt["attempt_id"],
                "source_node_id": attempt["node_id"],
                "files": manifest,
                "notes": [
                    dict(n)
                    for n in db.execute(
                        "SELECT * FROM notes WHERE attempt_id=?", (attempt["attempt_id"],)
                    )
                ],
                "publications": [
                    {
                        **dict(p),
                        "gaps": json.loads(p["gaps"]),
                        "refs": [
                            f"pub/{p['publication_id']}#{i['item_id']}"
                            for i in db.execute(
                                "SELECT item_id FROM publication_items WHERE publication_id=?",
                                (p["publication_id"],),
                            )
                        ],
                    }
                    for p in db.execute(
                        "SELECT * FROM publications WHERE attempt_id=?", (attempt["attempt_id"],)
                    )
                ],
                "provenance": "captured-at-snapshot",
            }
            db.execute(
                "INSERT INTO snapshot_handoffs VALUES(?,?)", (value["snapshot_id"], _json(context))
            )
            self._event(db, "snapshot.created", value)
            return value

        return self._mutate("snapshot", payload, request_id, work)

    def set_control(self, control: str, request_id: str) -> dict:
        if control not in {"manual", "auto", "paused", "stopped"}:
            raise ValidationError("Invalid project control")
        payload = {"control": control}

        def work(db: sqlite3.Connection) -> dict:
            project = self._project(db)
            db.execute(
                "UPDATE project SET control=? WHERE project_id=?", (control, project["project_id"]),
            )
            value = {"project_id": project["project_id"], "control": control}
            self._event(db, "project.control", value)
            return value

        return self._mutate("set_control", payload, request_id, work)

    def record_restore(
        self,
        host_id: str,
        session_id: str,
        snapshot_id: str,
        source_attempt_id: str,
        workspace: str,
        request_id: str,
    ) -> dict:
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "snapshot_id": snapshot_id,
            "source_attempt_id": source_attempt_id,
            "workspace": workspace,
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            attempt = db.execute(
                "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                (association["association_id"],),
            ).fetchone()
            value = {
                "restoration_id": self._next(db, "restoration", "H"),
                "snapshot_id": snapshot_id,
                "source_attempt_id": source_attempt_id,
                "target_association_id": association["association_id"],
                "target_attempt_id": attempt["attempt_id"] if attempt else None,
                "workspace": workspace,
                "created_at": _now(),
            }
            db.execute("INSERT INTO restorations VALUES (?,?,?,?,?,?,?)", tuple(value.values()))
            self._event(db, "snapshot.restored", value)
            return value

        return self._mutate("record_restore", payload, request_id, work)

    def record_host_event(
        self,
        host_id: str,
        session_id: str,
        event_type: str,
        sequence: int,
        facts: dict,
        request_id: str,
    ) -> dict:
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "event_type": _text(event_type, "event_type"),
            "sequence": int(sequence),
            "facts": _copy(facts),
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            value = {
                "association_id": association["association_id"],
                "host_id": host_id,
                "session_id": session_id,
                "event_type": event_type,
                "sequence": int(sequence),
                "facts": facts,
            }
            self._event(db, "host.event", value)
            return value

        return self._mutate("host_event", payload, request_id, work)

    def own_goal(
        self,
        host_id: str,
        session_id: str,
        goal_id: str,
        revision: int,
        phase: str,
        request_id: str,
    ) -> dict:
        payload = {
            "host_id": host_id,
            "session_id": session_id,
            "goal_id": goal_id,
            "revision": int(revision),
            "phase": phase,
        }

        def work(db: sqlite3.Connection) -> dict:
            association = self._association(db, host_id, session_id)
            db.execute(
                "INSERT INTO owned_goals VALUES (?,?,?,?,?) ON CONFLICT(association_id) "
                "DO UPDATE SET goal_id=excluded.goal_id,revision=excluded.revision,"
                "phase=excluded.phase,updated_at=excluded.updated_at",
                (association["association_id"], goal_id, revision, phase, _now()),
            )
            return {"association_id": association["association_id"], **payload}

        return self._mutate("own_goal", payload, request_id, work)

    def query(
        self, host_id: str | None = None, session_id: str | None = None, ref: str | None = None,
    ) -> dict:
        with self._read() as db:
            project = self._project(db)
            usage = db.execute(
                "SELECT COALESCE(SUM(amount),0) AS known,"
                "SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END) AS unknown "
                "FROM usage_observations"
            ).fetchone()
            association = attempt = None
            if host_id and session_id:
                association = db.execute(
                    "SELECT * FROM associations "
                    "WHERE host_id=? AND session_id=? AND ended_at IS NULL",
                    (host_id, session_id),
                ).fetchone()
                if association:
                    attempt = db.execute(
                        "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                        (association["association_id"],),
                    ).fetchone()
            publications = []
            for row in db.execute("SELECT * FROM publications ORDER BY created_at"):
                value = dict(row)
                value["gaps"] = json.loads(value["gaps"])
                value["items"] = [
                    {
                        **dict(item),
                        "content": json.loads(item["content"]),
                        "knowledge_refs": json.loads(item["knowledge_refs"]),
                        "ref": f"pub/{item['publication_id']}#{item['item_id']}",
                    }
                    for item in db.execute(
                        "SELECT * FROM publication_items WHERE publication_id=? ORDER BY item_id",
                        (row["publication_id"],),
                    )
                ]
                publications.append(value)
            attempts = []
            for row in db.execute("SELECT * FROM attempts ORDER BY started_at"):
                value = dict(row)
                value["details"] = json.loads(value["details"])
                attempts.append(value)
            snapshots = []
            for row in db.execute("SELECT * FROM snapshots ORDER BY created_at"):
                value = dict(row)
                value["manifest"] = json.loads(value["manifest"])
                value["complete"] = bool(value["complete"])
                snapshots.append(value)
            usage_observations = []
            for row in db.execute("SELECT * FROM usage_observations ORDER BY observed_at"):
                value = dict(row)
                value["details"] = json.loads(value["details"])
                usage_observations.append(value)
            owned_goal = None
            if association:
                owned_goal = db.execute(
                    "SELECT * FROM owned_goals WHERE association_id=?",
                    (association["association_id"],),
                ).fetchone()
            result = {
                "schema_version": SCHEMA_VERSION,
                "project": project,
                "association": dict(association) if association else None,
                "associations": [
                    dict(row)
                    for row in db.execute("SELECT * FROM associations ORDER BY started_at")
                ],
                "attempt": (
                    {**dict(attempt), "details": json.loads(attempt["details"])}
                    if attempt
                    else None
                ),
                "nodes": [
                    {**dict(row), "inputs": json.loads(row["inputs"])}
                    for row in db.execute("SELECT * FROM nodes ORDER BY created_at")
                ],
                "attempts": attempts,
                "publications": publications,
                "relations": [
                    dict(row) for row in db.execute("SELECT * FROM relations ORDER BY created_at")
                ],
                "legacy_refs": [
                    {**dict(row), "item": json.loads(row["item"])}
                    for row in db.execute("SELECT * FROM legacy_refs ORDER BY ref")
                ],
                "notes": [
                    dict(row) for row in db.execute("SELECT * FROM notes ORDER BY created_at")
                ],
                "snapshots": snapshots,
                "restorations": [
                    dict(row)
                    for row in db.execute("SELECT * FROM restorations ORDER BY created_at")
                ],
                "owned_goal": dict(owned_goal) if owned_goal else None,
                "usage": {
                    "known": float(usage["known"]),
                    "unknown_count": int(usage["unknown"] or 0),
                },
                "usage_observations": usage_observations,
            }
            result["workflow"] = self.workflow_view(db, session_id)
            result["knowledge"] = [
                self._decode_knowledge(row)
                for row in db.execute(
                    "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e "
                    "JOIN knowledge_revisions r USING(knowledge_id) "
                    "WHERE r.revision=(SELECT MAX(revision) FROM knowledge_revisions "
                    "WHERE knowledge_id=e.knowledge_id) ORDER BY e.rowid"
                )
            ]
            result["checkpoints"] = [
                {
                    **dict(row),
                    "state": json.loads(row["state"]),
                    "source_identity": json.loads(row["source_identity"]),
                }
                for row in db.execute(
                    "SELECT * FROM node_checkpoints ORDER BY rowid"
                )
            ]
            result["specialists"] = [
                self._decode_specialist(row)
                for row in db.execute(
                    "SELECT * FROM specialist_tasks ORDER BY created_at"
                )
            ]
            result["usage"].update(self.workflow_usage(db))
            if ref:
                if ref.startswith("knowledge/"):
                    kid, revision = parse_knowledge_ref(ref)
                    selected_knowledge = db.execute(
                        "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e "
                        "JOIN knowledge_revisions r USING(knowledge_id) "
                        "WHERE e.knowledge_id=? AND r.revision=?",
                        (kid, revision),
                    ).fetchone()
                    if not selected_knowledge:
                        raise NotFoundError(f"Unknown research reference: {ref}")
                    result["selected"] = {
                        "kind": "knowledge",
                        "value": self._decode_knowledge(selected_knowledge),
                    }
                    return result
                selected = db.execute("SELECT * FROM nodes WHERE node_id=?", (ref,)).fetchone()
                if selected:
                    value = dict(selected)
                    value["inputs"] = json.loads(value["inputs"])
                    result["selected"] = {"kind": "node", "value": value}
                else:
                    publication_id = ref[4:] if ref.startswith("pub/") and "#" not in ref else None
                    publication = next(
                        (item for item in publications if item["publication_id"] == publication_id),
                        None,
                    )
                    if publication:
                        result["selected"] = {"kind": "publication", "value": publication}
                    else:
                        legacy = db.execute(
                            "SELECT * FROM legacy_refs WHERE ref=?", (ref,)
                        ).fetchone()
                        if legacy:
                            value = dict(legacy)
                            value["item"] = json.loads(value["item"])
                            result["selected"] = {"kind": "legacy-ref", "value": value}
                        else:
                            item = next(
                                (
                                    publication_item
                                    for publication_value in publications
                                    for publication_item in publication_value["items"]
                                    if publication_item["ref"] == ref
                                ),
                                None,
                            )
                            if item:
                                result["selected"] = {"kind": "publication-item", "value": item}
                            else:
                                raise NotFoundError(f"Unknown research reference: {ref}")
            return result

    def memory_view(self, host_id: str, session_id: str, max_chars: int = 12_000) -> dict:
        return self.context_view(host_id, session_id, max_chars)
