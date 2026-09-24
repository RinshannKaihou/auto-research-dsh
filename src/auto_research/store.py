"""Transactional research metadata, independent of the model or experiment runner.

Files are frozen by ArtifactStore before publication.  The database owns metadata;
node cards are exports, not another writable source of truth.  A closed node is a
recorded stopping point, not a scientific verdict.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sqlite3
from typing import Any, Callable, Iterator

from .errors import BudgetError, ConflictError, NotFoundError, ValidationError


ACTIVE_STATES = frozenset({"reserved", "running", "unknown"})
TERMINAL_STATES = frozenset({"completed", "failed", "interrupted"})
ATTEMPT_STATES = ACTIVE_STATES | TERMINAL_STATES
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_REF = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)/result#([A-Za-z0-9][A-Za-z0-9_.-]*)\Z")
_NOTE_REF = re.compile(
    r"(?<![A-Za-z0-9_.-])(X-[A-Za-z0-9][A-Za-z0-9_.-]*/result#[A-Za-z0-9][A-Za-z0-9_.-]*)"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"Value is not finite JSON: {exc}") from exc


def _copy(value: Any) -> Any:
    return json.loads(_dump(value))


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a nonempty string")
    return value


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValidationError(f"Invalid {label}: {value!r}")
    return value


def _money(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{label} must be a finite nonnegative number")
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValidationError(f"{label} must be a finite nonnegative number")
    return value


def _questions(value: Any) -> list[dict]:
    if not isinstance(value, list) or not value:
        raise ValidationError("questions must be a nonempty list")
    result = _copy(value)
    ids: set[str] = set()
    for question in result:
        if not isinstance(question, dict):
            raise ValidationError("Each question must be an object")
        identifier = _identifier(question.get("id"), "question id")
        _text(question.get("text"), "question text")
        if "status" in question and not isinstance(question["status"], str):
            raise ValidationError("question status must be text")
        if identifier in ids:
            raise ValidationError(f"Duplicate question id: {identifier}")
        ids.add(identifier)
    return result


class Store:
    """A project store; each call uses its own SQLite connection.

    ``request_id`` is scoped to the entire project. Reusing it with the same
    operation and arguments returns the original response; changing the request
    is an error. Mutation and idempotency records commit in the same transaction.
    """

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.metadata_dir = self.root / ".research"
        self.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.metadata_dir / "state.sqlite3"
        with self._connection() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS project (
                    id INTEGER PRIMARY KEY CHECK(id = 1), goal TEXT NOT NULL,
                    budget_limit REAL NOT NULL CHECK(budget_limit >= 0),
                    config TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS agenda_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                    questions TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS notes_versions (
                    version INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS nodes (
                    id TEXT PRIMARY KEY, spec TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('proposed','open','closed')),
                    agenda_version INTEGER NOT NULL REFERENCES agenda_versions(version),
                    created_at TEXT NOT NULL, opened_at TEXT, closed_at TEXT, result TEXT
                );
                CREATE TABLE IF NOT EXISTS attempts (
                    id TEXT PRIMARY KEY, node_id TEXT REFERENCES nodes(id),
                    role TEXT NOT NULL, state TEXT NOT NULL,
                    estimate REAL NOT NULL CHECK(estimate >= 0),
                    hold REAL NOT NULL CHECK(hold >= 0),
                    cost REAL CHECK(cost >= 0), cost_kind TEXT NOT NULL,
                    fields TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS one_active_attempt_per_node
                    ON attempts(node_id) WHERE node_id IS NOT NULL
                    AND state IN ('reserved','running','unknown');
                CREATE TABLE IF NOT EXISTS published_items (
                    ref TEXT PRIMARY KEY, node_id TEXT NOT NULL REFERENCES nodes(id),
                    local_id TEXT NOT NULL, kind TEXT NOT NULL CHECK(kind IN ('product','finding')),
                    item TEXT NOT NULL, UNIQUE(node_id, local_id)
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,
                    data TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS requests (
                    request_id TEXT PRIMARY KEY, operation TEXT NOT NULL,
                    payload TEXT NOT NULL, response TEXT NOT NULL, created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS counters (name TEXT PRIMARY KEY, value INTEGER NOT NULL);
                PRAGMA user_version=1;
            """
            )

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version > 1:
            db.close()
            raise ValidationError(
                f"Schema {version} requires the native DSH service; legacy Store supports at most 1"
            )
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
        request_id: str | None,
        work: Callable[[sqlite3.Connection], Any],
    ) -> Any:
        encoded = _dump(payload)
        if request_id is not None:
            _text(request_id, "request_id")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if request_id is not None:
                    previous = db.execute(
                        "SELECT * FROM requests WHERE request_id=?", (request_id,)
                    ).fetchone()
                    if previous is not None:
                        if previous["operation"] != operation or previous["payload"] != encoded:
                            raise ConflictError(
                                f"request_id {request_id!r} was used for a different request"
                            )
                        result = json.loads(previous["response"])
                        db.commit()
                        return result
                result = work(db)
                if request_id is not None:
                    db.execute(
                        "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                        (request_id, operation, encoded, _dump(result), _now()),
                    )
                db.commit()
                return result
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def _record(db: sqlite3.Connection, kind: str, data: Any) -> dict:
        at = _now()
        cursor = db.execute(
            "INSERT INTO events(kind,data,created_at) VALUES (?,?,?)", (kind, _dump(data), at)
        )
        return {"id": cursor.lastrowid, "kind": kind, "data": _copy(data), "created_at": at}

    @staticmethod
    def _next_id(db: sqlite3.Connection, name: str, prefix: str) -> str:
        db.execute(
            "INSERT INTO counters(name,value) VALUES (?,0) ON CONFLICT(name) DO NOTHING", (name,)
        )
        db.execute("UPDATE counters SET value=value+1 WHERE name=?", (name,))
        value = db.execute("SELECT value FROM counters WHERE name=?", (name,)).fetchone()[0]
        return f"{prefix}-{value:03d}"

    @staticmethod
    def _project(db: sqlite3.Connection) -> dict:
        row = db.execute("SELECT * FROM project WHERE id=1").fetchone()
        if row is None:
            raise NotFoundError("Project has not been initialized")
        return {
            "goal": row["goal"],
            "budget": row["budget_limit"],
            "config": json.loads(row["config"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _agenda(db: sqlite3.Connection, version: int | None = None) -> dict:
        if version is None:
            row = db.execute(
                "SELECT * FROM agenda_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
        else:
            row = db.execute("SELECT * FROM agenda_versions WHERE version=?", (version,)).fetchone()
        if row is None:
            raise NotFoundError("Agenda version does not exist")
        return {
            "version": row["version"],
            "text": row["text"],
            "questions": json.loads(row["questions"]),
            "created_at": row["created_at"],
        }

    @staticmethod
    def _notes(db: sqlite3.Connection, version: int | None = None) -> dict:
        if version is None:
            row = db.execute(
                "SELECT * FROM notes_versions ORDER BY version DESC LIMIT 1"
            ).fetchone()
        else:
            row = db.execute("SELECT * FROM notes_versions WHERE version=?", (version,)).fetchone()
        if row is None:
            raise NotFoundError("Notes version does not exist")
        return dict(row)

    def initialize(
        self,
        goal: str,
        questions: list[dict] | None = None,
        budget: float = 10.0,
        config: dict | None = None,
        request_id: str | None = None,
    ) -> dict:
        goal = _text(goal, "goal")
        questions = _questions(
            questions if questions is not None else [{"id": "Q-001", "text": goal}]
        )
        budget = _money(budget, "budget")
        if config is not None and not isinstance(config, dict):
            raise ValidationError("config must be an object")
        config = {"control": "paused", **_copy(config or {})}
        payload = {"goal": goal, "questions": questions, "budget": budget, "config": config}

        def work(db: sqlite3.Connection) -> dict:
            if db.execute("SELECT 1 FROM project").fetchone():
                raise ConflictError("Project is already initialized")
            at = _now()
            db.execute("INSERT INTO project VALUES (1,?,?,?,?)", (goal, budget, _dump(config), at))
            agenda_text = "\n".join(f"{q['id']}: {q['text']}" for q in questions)
            db.execute(
                "INSERT INTO agenda_versions(text,questions,created_at) VALUES (?,?,?)",
                (agenda_text, _dump(questions), at),
            )
            db.execute("INSERT INTO notes_versions(text,created_at) VALUES ('',?)", (at,))
            self._record(db, "project.initialized", payload)
            return self._project(db)

        return self._mutate("initialize", payload, request_id, work)

    def project(self) -> dict:
        with self._read() as db:
            return self._project(db)

    def configure(
        self,
        config: dict | None = None,
        budget: float | None = None,
        request_id: str | None = None,
        **config_updates: Any,
    ) -> dict:
        if config is not None and not isinstance(config, dict):
            raise ValidationError("config must be an object")
        updates = {**_copy(config or {}), **_copy(config_updates)}
        if budget is not None:
            budget = _money(budget, "budget")

        def work(db: sqlite3.Connection) -> dict:
            project = self._project(db)
            project["config"].update(updates)
            db.execute(
                "UPDATE project SET config=?,budget_limit=? WHERE id=1",
                (_dump(project["config"]), project["budget"] if budget is None else budget),
            )
            self._record(db, "project.configured", {"updates": updates, "budget": budget})
            return self._project(db)

        return self._mutate("configure", {"updates": updates, "budget": budget}, request_id, work)

    def agenda(
        self,
        text: str | None = None,
        questions: list[dict] | None = None,
        *,
        version: int | None = None,
        request_id: str | None = None,
    ) -> dict:
        if text is None and questions is None:
            with self._read() as db:
                self._project(db)
                return self._agenda(db, version)
        if version is not None:
            raise ValidationError("An old agenda version cannot be edited")
        if text is not None and not isinstance(text, str):
            raise ValidationError("agenda text must be a string")
        if questions is not None:
            questions = _questions(questions)

        def work(db: sqlite3.Connection) -> dict:
            self._project(db)
            previous = self._agenda(db)
            db.execute(
                "INSERT INTO agenda_versions(text,questions,created_at) VALUES (?,?,?)",
                (
                    previous["text"] if text is None else text,
                    _dump(previous["questions"] if questions is None else questions),
                    _now(),
                ),
            )
            result = self._agenda(db)
            self._record(db, "agenda.updated", result)
            return result

        return self._mutate("agenda", {"text": text, "questions": questions}, request_id, work)

    def notes(
        self, text: str | None = None, *, version: int | None = None, request_id: str | None = None
    ) -> dict:
        if text is None:
            with self._read() as db:
                self._project(db)
                return self._notes(db, version)
        if version is not None or not isinstance(text, str):
            raise ValidationError("Notes updates need text, not an old version")

        def work(db: sqlite3.Connection) -> dict:
            self._project(db)
            for ref in _NOTE_REF.findall(text):
                # Prefer the exact id; a final prose period is not usually part
                # of it. Backtick/Markdown delimiters are already excluded.
                try:
                    self._resolve(db, ref)
                except NotFoundError:
                    if ref.endswith("."):
                        self._resolve(db, ref.rstrip("."))
                    else:
                        raise
            db.execute("INSERT INTO notes_versions(text,created_at) VALUES (?,?)", (text, _now()))
            result = self._notes(db)
            self._record(db, "notes.updated", result)
            return result

        return self._mutate("notes", {"text": text}, request_id, work)

    @staticmethod
    def _node(db: sqlite3.Connection, node_id: str) -> dict:
        row = db.execute("SELECT * FROM nodes WHERE id=?", (node_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Node does not exist: {node_id}")
        return {
            **json.loads(row["spec"]),
            "id": row["id"],
            "status": row["status"],
            "agenda_version": row["agenda_version"],
            "created_at": row["created_at"],
            "opened_at": row["opened_at"],
            "closed_at": row["closed_at"],
            "result": json.loads(row["result"]) if row["result"] is not None else None,
        }

    def get_node(self, node_id: str) -> dict:
        with self._read() as db:
            return self._node(db, node_id)

    def list_nodes(self, status: str | None = None) -> list[dict]:
        with self._read() as db:
            query, args = (
                ("SELECT id FROM nodes ORDER BY created_at,id", ())
                if status is None
                else ("SELECT id FROM nodes WHERE status=? ORDER BY created_at,id", (status,))
            )
            return [self._node(db, row[0]) for row in db.execute(query, args)]

    def _verify_product(self, product: dict) -> None:
        _text(product.get("path"), "product path")
        _text(product.get("version"), "product version")
        if "status" in product and (
            not isinstance(product["status"], str) or product["status"] not in {"partial", "usable"}
        ):
            raise ValidationError("product status must be partial or usable")
        if "interface" in product and not isinstance(product["interface"], str):
            raise ValidationError("product interface must be text")
        if "gaps" in product and (
            not isinstance(product["gaps"], list)
            or any(not isinstance(gap, str) for gap in product["gaps"])
        ):
            raise ValidationError("product gaps must be a list of text")
        from .artifacts import ArtifactStore

        try:
            ArtifactStore(self.root).verify(product)
        except (ValueError, OSError) as exc:
            raise ValidationError(
                f"Invalid archived product {product.get('id', '')}: {exc}"
            ) from exc

    def _resolve(self, db: sqlite3.Connection, ref: str) -> dict:
        if not isinstance(ref, str) or not _REF.fullmatch(ref):
            raise ValidationError(f"Invalid result reference: {ref!r}")
        row = db.execute(
            "SELECT i.*,n.status FROM published_items i JOIN nodes n ON n.id=i.node_id WHERE i.ref=?",
            (ref,),
        ).fetchone()
        if row is None or row["status"] != "closed":
            raise NotFoundError(f"Reference is not a published result: {ref}")
        item = json.loads(row["item"])
        if row["kind"] == "product":
            self._verify_product(item)
        return {
            "ref": ref,
            "node_id": row["node_id"],
            "id": row["local_id"],
            "kind": row["kind"],
            "item": item,
        }

    def resolve(self, ref: str) -> dict:
        with self._read() as db:
            return self._resolve(db, ref)

    def _inputs(self, db: sqlite3.Connection, inputs: Any) -> list[dict]:
        if not isinstance(inputs, list):
            raise ValidationError("inputs must be a list")
        result = []
        seen = set()
        for item in _copy(inputs):
            if not isinstance(item, dict):
                raise ValidationError("Each input must be an object")
            _text(item.get("use"), "input use")
            self._resolve(db, item.get("ref"))
            identity = (item["ref"], item["use"])
            if identity not in seen:
                result.append(item)
                seen.add(identity)
        return result

    def propose(self, spec: dict, request_id: str | None = None) -> dict:
        if not isinstance(spec, dict):
            raise ValidationError("spec must be an object")
        spec = _copy(spec)
        _identifier(spec.get("question"), "question")
        _text(spec.get("why_now"), "why_now")
        if not (isinstance(spec.get("plan"), str) and spec["plan"].strip()) and not (
            isinstance(spec.get("plan"), dict) and spec["plan"]
        ):
            raise ValidationError("plan must be nonempty text or an object")
        if "modifies_artifact" in spec and not isinstance(spec["modifies_artifact"], bool):
            raise ValidationError("modifies_artifact must be boolean")

        def work(db: sqlite3.Connection) -> dict:
            self._project(db)
            current_agenda = self._agenda(db)
            if spec["question"] not in {q["id"] for q in current_agenda["questions"]}:
                raise ValidationError(f"Question is not in the current agenda: {spec['question']}")
            stored = {**spec, "inputs": self._inputs(db, spec.get("inputs", []))}
            if "id" in spec:
                node_id = _identifier(spec["id"], "node id")
                if db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
                    raise ConflictError(f"Node already exists: {node_id}")
            else:
                node_id = self._next_id(db, "node", "X")
                while db.execute("SELECT 1 FROM nodes WHERE id=?", (node_id,)).fetchone():
                    node_id = self._next_id(db, "node", "X")
            db.execute(
                "INSERT INTO nodes(id,spec,status,agenda_version,created_at) VALUES (?,?,'proposed',?,?)",
                (node_id, _dump(stored), current_agenda["version"], _now()),
            )
            self._record(
                db,
                "node.proposed",
                {"node_id": node_id, "spec": stored, "agenda_version": current_agenda["version"]},
            )
            return self._node(db, node_id)

        return self._mutate("propose", spec, request_id, work)

    @staticmethod
    def _attempt(db: sqlite3.Connection, attempt_id: str) -> dict:
        row = db.execute("SELECT * FROM attempts WHERE id=?", (attempt_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Attempt does not exist: {attempt_id}")
        result = json.loads(row["fields"])
        result.update({key: row[key] for key in row.keys() if key != "fields"})
        return result

    @staticmethod
    def _budget(db: sqlite3.Connection) -> dict:
        project = Store._project(db)
        rows = db.execute("SELECT state,cost,cost_kind,hold FROM attempts").fetchall()
        spent = sum(row["cost"] or 0.0 for row in rows)
        held = sum(row["hold"] for row in rows)
        actual = sum(row["cost"] or 0.0 for row in rows if row["cost_kind"] == "actual")
        estimated = sum(row["cost"] or 0.0 for row in rows if row["cost_kind"] == "estimated")
        unknown = sum(
            row["cost"] is None and (row["state"] == "unknown" or row["state"] in TERMINAL_STATES)
            for row in rows
        )
        remaining = project["budget"] - spent - held
        return {
            "total": project["budget"],
            "limit": project["budget"],
            "spent": spent,
            "held": held,
            "reserved": held,
            "available": max(0.0, remaining),
            "remaining": remaining,
            "actual": actual,
            "estimated": estimated,
            "unknown_count": unknown,
            "over_budget": remaining < -1e-9,
        }

    def budget(self) -> dict:
        with self._read() as db:
            return self._budget(db)

    def reserve(
        self,
        node_id: str | None = None,
        role: str = "worker",
        estimate: float = 0.0,
        request_id: str | None = None,
    ) -> dict:
        estimate = _money(estimate, "estimate")
        role = _text(role, "role")
        payload = {"node_id": node_id, "role": role, "estimate": estimate}

        def work(db: sqlite3.Connection) -> dict:
            self._project(db)
            current_agenda = self._agenda(db)
            if node_id is not None:
                node = self._node(db, node_id)
                if node["status"] == "closed":
                    raise ConflictError(f"Node is already closed: {node_id}")
                question = next(
                    (q for q in current_agenda["questions"] if q["id"] == node["question"]), None
                )
                if question is None or question.get("status") in {
                    "stopped",
                    "closed",
                    "answered",
                    "paused",
                    "deferred",
                }:
                    raise ConflictError(
                        f"Question is no longer active in the current agenda: {node['question']}"
                    )
                active = db.execute(
                    "SELECT id FROM attempts WHERE node_id=? AND state IN ('reserved','running','unknown')",
                    (node_id,),
                ).fetchone()
                if active:
                    raise ConflictError(f"Node already has active attempt {active['id']}")
            budget = self._budget(db)
            if budget["remaining"] + 1e-9 < estimate:
                raise BudgetError(
                    f"Insufficient budget: requested {estimate}, available {budget['remaining']}"
                )
            attempt_id = self._next_id(db, "attempt", "A")
            at = _now()
            context_versions = {
                "agenda_version": current_agenda["version"],
                "notes_version": self._notes(db)["version"],
            }
            db.execute(
                "INSERT INTO attempts VALUES (?,?,?,'reserved',?,?,NULL,'unknown',?,?,?)",
                (attempt_id, node_id, role, estimate, estimate, _dump(context_versions), at, at),
            )
            if node_id is not None:
                db.execute(
                    "UPDATE nodes SET status='open',opened_at=COALESCE(opened_at,?) WHERE id=?",
                    (at, node_id),
                )
            self._record(
                db, "attempt.reserved", {"attempt_id": attempt_id, **payload, **context_versions}
            )
            return self._attempt(db, attempt_id)

        return self._mutate("reserve", payload, request_id, work)

    def get_attempt(self, attempt_id: str) -> dict:
        with self._read() as db:
            return self._attempt(db, attempt_id)

    def attempts(
        self,
        *,
        node_id: str | None = None,
        role: str | None = None,
        state: str | None = None,
        active_only: bool = False,
    ) -> list[dict]:
        clauses, args = [], []
        for key, value in (("node_id", node_id), ("role", role), ("state", state)):
            if value is not None:
                clauses.append(f"{key}=?")
                args.append(value)
        if active_only:
            clauses.append("state IN ('reserved','running','unknown')")
        query = (
            "SELECT id FROM attempts"
            + (" WHERE " + " AND ".join(clauses) if clauses else "")
            + " ORDER BY created_at,id"
        )
        with self._read() as db:
            return [self._attempt(db, row[0]) for row in db.execute(query, args)]

    def set_attempt(self, attempt_id: str, *, request_id: str | None = None, **fields: Any) -> dict:
        immutable = {
            "id",
            "node_id",
            "role",
            "estimate",
            "hold",
            "cost",
            "cost_kind",
            "created_at",
            "updated_at",
        }
        if immutable.intersection(fields):
            raise ValidationError(
                "Attempt identity and accounting must not be edited with set_attempt"
            )
        fields = _copy(fields)
        if "state" in fields and (
            not isinstance(fields["state"], str) or fields["state"] not in ATTEMPT_STATES
        ):
            raise ValidationError(f"Invalid attempt state: {fields['state']}")

        def work(db: sqlite3.Connection) -> dict:
            previous = self._attempt(db, attempt_id)
            state = fields.get("state", previous["state"])
            if previous["state"] in TERMINAL_STATES and state != previous["state"]:
                raise ConflictError("A finished attempt cannot be restarted; reserve a new attempt")
            stored = json.loads(
                db.execute("SELECT fields FROM attempts WHERE id=?", (attempt_id,)).fetchone()[0]
            )
            stored.update({key: value for key, value in fields.items() if key != "state"})
            db.execute(
                "UPDATE attempts SET state=?,fields=?,updated_at=? WHERE id=?",
                (state, _dump(stored), _now(), attempt_id),
            )
            self._record(db, "attempt.updated", {"attempt_id": attempt_id, "fields": fields})
            return self._attempt(db, attempt_id)

        return self._mutate(
            "set_attempt", {"attempt_id": attempt_id, "fields": fields}, request_id, work
        )

    def settle(
        self,
        attempt_id: str,
        cost: float | None = None,
        cost_kind: str = "actual",
        state: str = "completed",
        request_id: str | None = None,
    ) -> dict:
        if not isinstance(state, str) or state not in TERMINAL_STATES:
            raise ValidationError("settle state must be completed, failed, or interrupted")
        if cost is None:
            cost_kind = "unknown"
        else:
            cost = _money(cost, "cost")
            if cost_kind not in {"actual", "estimated"}:
                raise ValidationError("Known costs must be actual or estimated")
        payload = {"attempt_id": attempt_id, "cost": cost, "cost_kind": cost_kind, "state": state}

        def work(db: sqlite3.Connection) -> dict:
            previous = self._attempt(db, attempt_id)
            if previous["state"] in TERMINAL_STATES and previous["state"] != state:
                raise ConflictError("A settled attempt's terminal state cannot be changed")
            if previous["cost"] is not None:
                if cost is None:
                    raise ConflictError("Known cost cannot be replaced with unknown cost")
                if previous["cost_kind"] == "actual" and (
                    previous["cost"] != cost or cost_kind != "actual"
                ):
                    raise ConflictError("An actual settled cost cannot be silently rewritten")
            hold = previous["hold"] if cost is None else 0.0
            db.execute(
                "UPDATE attempts SET state=?,cost=?,cost_kind=?,hold=?,updated_at=? WHERE id=?",
                (state, cost, cost_kind, hold, _now(), attempt_id),
            )
            self._record(db, "attempt.settled", payload)
            return self._attempt(db, attempt_id)

        return self._mutate("settle", payload, request_id, work)

    def publish(self, node_id: str, result: dict, request_id: str | None = None) -> dict:
        if not isinstance(result, dict):
            raise ValidationError("result must be an object")
        result = _copy(result)
        _text(result.get("close_reason"), "close_reason")
        if "limitations" in result and not isinstance(result["limitations"], str):
            raise ValidationError("limitations must be text")
        for key in ("products", "findings", "next"):
            if key in result and not isinstance(result[key], list):
                raise ValidationError(f"{key} must be a list")

        def work(db: sqlite3.Connection) -> dict:
            node = self._node(db, node_id)
            if node["status"] != "open":
                raise ConflictError(
                    "Only an open node can publish; zero-run work still needs a reserved attempt"
                )
            published = {
                "products": [],
                "findings": [],
                "limitations": "",
                "next": [],
                **_copy(result),
            }
            local: dict[str, tuple[str, dict]] = {}
            for kind, key in (("product", "products"), ("finding", "findings")):
                for item in published[key]:
                    if not isinstance(item, dict):
                        raise ValidationError(f"Each {kind} must be an object")
                    local_id = _identifier(item.get("id"), f"{kind} id")
                    if local_id in local:
                        raise ValidationError(f"Duplicate result id: {local_id}")
                    item["ref"] = f"{node_id}/result#{local_id}"
                    local[local_id] = (kind, item)
                    if kind == "product":
                        self._verify_product(item)

            for finding in published["findings"]:
                _text(finding.get("text"), "finding text")
                if "conditions" in finding and not isinstance(finding["conditions"], str):
                    raise ValidationError("finding conditions must be text")
                evidence = finding.get("evidence")
                if not isinstance(evidence, list) or not evidence:
                    raise ValidationError(
                        "Every finding must cite evidence; findings may instead be empty"
                    )
                normalized = []
                for ref in evidence:
                    if not isinstance(ref, str):
                        raise ValidationError("Evidence references must be strings")
                    canonical = f"{node_id}/result{ref}" if ref.startswith("#") else ref
                    match = _REF.fullmatch(canonical)
                    if match and match[1] == node_id:
                        target = local.get(match[2])
                        if target is None or target[0] != "product":
                            raise ValidationError(
                                "Local evidence must reference a product in this publication"
                            )
                    else:
                        self._resolve(db, canonical)
                    normalized.append(canonical)
                finding["evidence"] = normalized
                if finding.get("revises") is not None:
                    target = self._resolve(db, finding["revises"])
                    if target["kind"] != "finding":
                        raise ValidationError("revises must reference an existing finding")

            appended_inputs = self._inputs(db, published.pop("inputs", []))
            stored_spec = {
                key: value
                for key, value in node.items()
                if key
                not in {
                    "id",
                    "status",
                    "agenda_version",
                    "created_at",
                    "opened_at",
                    "closed_at",
                    "result",
                }
            }
            stored_spec["inputs"] = self._inputs(db, node.get("inputs", []) + appended_inputs)
            for local_id, (kind, item) in local.items():
                db.execute(
                    "INSERT INTO published_items(ref,node_id,local_id,kind,item) VALUES (?,?,?,?,?)",
                    (item["ref"], node_id, local_id, kind, _dump(item)),
                )
            db.execute(
                "UPDATE nodes SET spec=?,result=?,status='closed',closed_at=? WHERE id=?",
                (_dump(stored_spec), _dump(published), _now(), node_id),
            )
            self._record(
                db,
                "node.published",
                {"node_id": node_id, "result": published, "added_inputs": appended_inputs},
            )
            return self._node(db, node_id)

        return self._mutate("publish", {"node_id": node_id, "result": result}, request_id, work)

    def event(self, kind: str, data: Any = None, request_id: str | None = None) -> dict:
        _text(kind, "event kind")
        return self._mutate(
            "event",
            {"kind": kind, "data": data},
            request_id,
            lambda db: self._record(db, kind, data),
        )

    def events(self, after: int = 0, limit: int | None = None) -> list[dict]:
        if isinstance(after, bool) or not isinstance(after, int) or after < 0:
            raise ValidationError("after must be a nonnegative event id")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 0
        ):
            raise ValidationError("limit must be a nonnegative integer")
        with self._read() as db:
            query = "SELECT * FROM events WHERE id>? ORDER BY id"
            args = [after]
            if limit is not None:
                query += " LIMIT ?"
                args.append(limit)
            return [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "data": json.loads(row["data"]),
                    "created_at": row["created_at"],
                }
                for row in db.execute(query, args)
            ]

    def snapshot(self) -> dict:
        with self._read() as db:
            return {
                "project": self._project(db),
                "agenda": self._agenda(db),
                "notes": self._notes(db),
                "nodes": [
                    self._node(db, row[0])
                    for row in db.execute("SELECT id FROM nodes ORDER BY created_at,id")
                ],
                "attempts": [
                    self._attempt(db, row[0])
                    for row in db.execute("SELECT id FROM attempts ORDER BY created_at,id")
                ],
                "budget": self._budget(db),
            }
