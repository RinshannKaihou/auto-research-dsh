"""Schema-4 knowledge, retrieval, context, and specialist ledgers.

The database is authoritative. Markdown files under ``.research/memory`` are
read-only projections and can always be rebuilt from these tables.
"""

from __future__ import annotations

from . import frozen_refs

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

from .errors import ConflictError, NotFoundError, ValidationError


MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge_entries (
 knowledge_id TEXT PRIMARY KEY, kind TEXT NOT NULL, node_id TEXT,
 created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_revisions (
 knowledge_id TEXT NOT NULL REFERENCES knowledge_entries(knowledge_id),
 revision INTEGER NOT NULL, statement TEXT NOT NULL, scope TEXT NOT NULL,
 conditions TEXT NOT NULL, status TEXT NOT NULL, evidence_refs TEXT NOT NULL,
 source_identity TEXT NOT NULL, dependencies TEXT NOT NULL,
 supersedes TEXT NOT NULL, author TEXT NOT NULL, created_at TEXT NOT NULL,
 source_sequence INTEGER,
 PRIMARY KEY(knowledge_id,revision)
);
CREATE TABLE IF NOT EXISTS node_questions (
 node_id TEXT PRIMARY KEY REFERENCES nodes(node_id), knowledge_id TEXT NOT NULL,
 revision INTEGER NOT NULL, created_at TEXT NOT NULL,
 FOREIGN KEY(knowledge_id,revision) REFERENCES knowledge_revisions(knowledge_id,revision)
);
CREATE TABLE IF NOT EXISTS node_checkpoints (
 checkpoint_id TEXT PRIMARY KEY, node_id TEXT REFERENCES nodes(node_id),
 revision INTEGER NOT NULL, state TEXT NOT NULL, source_identity TEXT NOT NULL,
 created_at TEXT NOT NULL, UNIQUE(node_id,revision)
);
CREATE TABLE IF NOT EXISTS publication_knowledge (
 publication_id TEXT NOT NULL REFERENCES publications(publication_id),
 knowledge_id TEXT NOT NULL, revision INTEGER NOT NULL,
 PRIMARY KEY(publication_id,knowledge_id,revision),
 FOREIGN KEY(knowledge_id,revision) REFERENCES knowledge_revisions(knowledge_id,revision)
);
CREATE TABLE IF NOT EXISTS knowledge_search (
 knowledge_ref TEXT PRIMARY KEY, original TEXT NOT NULL,
 zh_bigrams TEXT NOT NULL, zh_chars TEXT NOT NULL, terms TEXT NOT NULL,
 tokenizer_version TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_packs (
 pack_id TEXT PRIMARY KEY, body TEXT NOT NULL, dependencies TEXT NOT NULL,
 policy_version TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS context_requests (
 context_id INTEGER PRIMARY KEY AUTOINCREMENT, operation_id TEXT UNIQUE NOT NULL,
 host_id TEXT NOT NULL,
 session_id TEXT NOT NULL, turn INTEGER, step INTEGER, purpose TEXT,
 pack_id TEXT REFERENCES context_packs(pack_id), source_sequence INTEGER,
 selection TEXT NOT NULL, size_estimate INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS specialist_tasks (
 task_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
 parent_session_id TEXT NOT NULL, child_session_id TEXT UNIQUE,
 node_id TEXT, attempt_id TEXT, purpose TEXT NOT NULL, label TEXT NOT NULL,
 prompt TEXT NOT NULL, inputs TEXT NOT NULL, state TEXT NOT NULL,
 result TEXT, error TEXT, exit_verified INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 context_mode TEXT NOT NULL DEFAULT 'research'
);
CREATE TABLE IF NOT EXISTS specialist_bindings (
 session_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES specialist_tasks(task_id),
 parent_session_id TEXT NOT NULL, node_id TEXT, attempt_id TEXT,
 permission TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS guidance_sources (
 guidance_id TEXT PRIMARY KEY, path TEXT NOT NULL, version TEXT NOT NULL,
 content_hash TEXT NOT NULL, section_index TEXT NOT NULL,
 active INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_todos (
 todo_id TEXT PRIMARY KEY, trigger_kind TEXT NOT NULL, trigger_ref TEXT NOT NULL,
 node_id TEXT, state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
 UNIQUE(trigger_kind,trigger_ref)
);
"""

KINDS = {"observation", "hypothesis", "lesson", "decision", "open_question", "claim"}
STATUSES = {"proposed", "working", "disputed", "superseded", "retracted"}
TOKENIZER_VERSION = "cjk-bigram-v1"
_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]+")
_TERMS = re.compile(r"[A-Za-z][A-Za-z0-9_.:/#@+-]*|\d+(?:\.\d+)*")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _encoded(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def bounded_value(value: Any, limit: int) -> Any:
    """Keep valid structured previews, preserving every mapping key at small limits."""
    if len(_encoded(value)) <= limit:
        return value

    def trim(item, width):
        if isinstance(item, str):
            return item if len(item) <= width else item[:width] + "… [expand source]"
        if isinstance(item, dict):
            return {key: trim(val, width) for key, val in item.items()}
        if isinstance(item, list):
            kept = [trim(val, width) for val in item[: max(1, width // 40)]]
            if len(kept) < len(item):
                kept.append({"omitted_count": len(item) - len(kept)})
            return kept
        return item

    for width in (1200, 600, 300, 150, 60, 20, 0):
        preview = trim(value, width)
        if len(_encoded(preview)) <= limit:
            return preview
    return {"preview": "Source exceeds this view; use research_query", "truncated": True}


def _require_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be nonempty text")
    return value.strip()


def _json_value(value: Any, label: str, default: Any) -> Any:
    if value is None:
        return default
    try:
        return json.loads(_encoded(value))
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{label} must be finite JSON") from exc


def tokenize(text: str) -> tuple[str, str, str]:
    bigrams: list[str] = []
    chars: list[str] = []
    for run in _CJK.findall(text):
        chars.extend(run)
        bigrams.extend(run[i : i + 2] for i in range(max(0, len(run) - 1)))
    terms = [item.casefold() for item in _TERMS.findall(text)]
    return " ".join(bigrams), " ".join(chars), " ".join(terms)


# Explicit column list. The historical statements were positional with
# thirteen placeholders, so schema 7's added ``motivated_by`` column broke
# them silently at write time; never reintroduce ``VALUES(?,?,...)`` here.
_LEGACY_REVISION_COLUMNS = (
    "knowledge_id,revision,statement,scope,conditions,status,evidence_refs,"
    "source_identity,dependencies,supersedes,author,created_at,source_sequence"
)
_REVISION_COLUMNS = _LEGACY_REVISION_COLUMNS + ",motivated_by,asserted_at,execution_refs"
_REVISION_INSERT = (
    f"INSERT INTO knowledge_revisions ({_REVISION_COLUMNS})"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)
_REVISION_INSERT_IGNORE = (
    f"INSERT OR IGNORE INTO knowledge_revisions ({_LEGACY_REVISION_COLUMNS})"
    " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


def write_support_edge(
    db: sqlite3.Connection,
    user_ref: str,
    used_ref: str,
    flags: dict,
    created_at: str,
    sequence: int,
) -> None:
    """Insert or merge one edge.

    Must not be ``INSERT OR IGNORE``: the same pair of versions can be linked
    through ``dependencies`` and ``evidence_refs`` at once, and ignoring the
    second write would drop a source label that rule R2 requires be kept.
    """
    db.execute(
        "INSERT INTO knowledge_support_edges"
        " (user_ref,used_ref,from_dependencies,from_evidence,created_at,source_sequence)"
        " VALUES (?,?,?,?,?,?)"
        " ON CONFLICT(user_ref,used_ref) DO UPDATE SET"
        "  from_dependencies=MAX(from_dependencies,excluded.from_dependencies),"
        "  from_evidence=MAX(from_evidence,excluded.from_evidence)",
        (
            user_ref,
            used_ref,
            flags["from_dependencies"],
            flags["from_evidence"],
            created_at,
            sequence,
        ),
    )


def _knowledge_ref(knowledge_id: str, revision: int) -> str:
    return f"knowledge/{knowledge_id}@{revision}"


def parse_knowledge_ref(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"knowledge/([A-Za-z0-9_-]+)@(\d+)", value or "")
    if not match:
        raise ValidationError(f"Invalid knowledge reference: {value}")
    return match.group(1), int(match.group(2))


def is_knowledge_ref(value: Any) -> bool:
    return isinstance(value, str) and bool(
        re.fullmatch(r"knowledge/([A-Za-z0-9_-]+)@(\d+)", value)
    )


def support_refs(dependencies: Any, evidence_refs: Any) -> dict[str, dict]:
    """The unified knowledge propagation basis of rule R2.

    ``dedup(K-typed dependencies ∪ K-typed evidence_refs)``, each entry
    carrying which field produced it. Propagation, impact queries and the
    publication check all consume this one view; reading ``dependencies``
    alone is the single most likely implementation error, because clearing
    that field does not detach a version whose ``evidence_refs`` still cites
    the same knowledge version.

    References to frozen material (snapshots, publication items) are terminal
    and never appear here.
    """

    def listed(value: Any) -> list:
        if isinstance(value, str):
            try:
                value = json.loads(value or "[]")
            except json.JSONDecodeError:
                return []
        return value if isinstance(value, list) else []

    edges: dict[str, dict] = {}
    for field, refs in (
        ("from_dependencies", listed(dependencies)),
        ("from_evidence", listed(evidence_refs)),
    ):
        for ref in refs:
            if not is_knowledge_ref(ref):
                continue
            flags = edges.setdefault(ref, {"from_dependencies": 0, "from_evidence": 0})
            flags[field] = 1
    return edges


def _execute_schema(db: sqlite3.Connection, schema: str = MEMORY_SCHEMA) -> None:
    """Execute the simple schema DDL without ``executescript``'s implicit commit."""
    for statement in schema.split(";"):
        statement = statement.strip()
        if statement:
            db.execute(statement)


def migrate_schema4(path: Path) -> None:
    with sqlite3.connect(path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        version = int(db.execute("PRAGMA user_version").fetchone()[0])
        if version == 4:
            return
        if version != 3:
            raise ValidationError(f"Expected schema 3, found {version}")
        backup = path.parent / "schema-3-backup.sqlite3"
        if not backup.exists():
            temporary = backup.with_suffix(".tmp")
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.replace(backup)
        db.execute("BEGIN IMMEDIATE")
        try:
            _execute_schema(db)
            node_columns = {row[1] for row in db.execute("PRAGMA table_info(nodes)")}
            if "question_ref" not in node_columns:
                db.execute("ALTER TABLE nodes ADD COLUMN question_ref TEXT")
            item_columns = {row[1] for row in db.execute("PRAGMA table_info(publication_items)")}
            if "knowledge_refs" not in item_columns:
                db.execute(
                    "ALTER TABLE publication_items ADD COLUMN knowledge_refs TEXT NOT NULL DEFAULT '[]'"
                )
            for node in db.execute("SELECT * FROM nodes WHERE question_ref IS NULL ORDER BY rowid"):
                kid = "K-legacy-" + hashlib.sha256(node["node_id"].encode()).hexdigest()[:16]
                created = node["created_at"] or _now()
                source = {"kind": "legacy-node", "node_id": node["node_id"]}
                db.execute(
                    "INSERT OR IGNORE INTO knowledge_entries VALUES(?,?,?,?)",
                    (kid, "open_question", node["node_id"], created),
                )
                db.execute(
                    _REVISION_INSERT_IGNORE,
                    (
                        kid,
                        1,
                        node["question"],
                        "{}",
                        "{}",
                        "working",
                        "[]",
                        _encoded(source),
                        "[]",
                        "[]",
                        "legacy-migration",
                        created,
                        None,
                    ),
                )
                ref = _knowledge_ref(kid, 1)
                db.execute(
                    "UPDATE nodes SET question_ref=? WHERE node_id=?", (ref, node["node_id"])
                )
                db.execute(
                    "INSERT OR IGNORE INTO node_questions VALUES(?,?,?,?)",
                    (node["node_id"], kid, 1, created),
                )
            db.execute("PRAGMA user_version=4")
            db.commit()
        except BaseException:
            db.rollback()
            raise


def _epistemic():
    """Imported lazily: :mod:`epistemic` depends on this module's helpers."""
    from . import epistemic

    return epistemic


class MemoryStore:
    @staticmethod
    def _create_memory_schema(db: sqlite3.Connection) -> None:
        _execute_schema(db)
        try:
            db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS knowledge_fts USING fts5(knowledge_ref UNINDEXED,original,zh_bigrams,zh_chars,terms,tokenize='unicode61')"
            )
        except sqlite3.OperationalError:
            pass

    @staticmethod
    def _latest_revision(db: sqlite3.Connection, knowledge_id: str) -> sqlite3.Row:
        row = db.execute(
            "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) WHERE e.knowledge_id=? ORDER BY r.revision DESC LIMIT 1",
            (knowledge_id,),
        ).fetchone()
        if not row:
            raise NotFoundError(f"Unknown knowledge item: {knowledge_id}")
        return row

    @staticmethod
    def _decode_knowledge(row: sqlite3.Row) -> dict:
        value = dict(row)
        for key in (
            "scope",
            "conditions",
            "evidence_refs",
            "source_identity",
            "dependencies",
            "supersedes",
        ):
            value[key] = json.loads(value[key])
        # Added by schema 7; decode it too or it leaks as a raw JSON string.
        value["motivated_by"] = json.loads(value.get("motivated_by") or "[]")
        value["asserted_at"] = json.loads(value.get("asserted_at") or "null")
        value["execution_refs"] = json.loads(value.get("execution_refs") or "[]")
        value["ref"] = _knowledge_ref(value["knowledge_id"], value["revision"])
        return value

    def _validate_evidence(self, db: sqlite3.Connection, refs: list[str]) -> None:
        for ref in refs:
            if ref.startswith("knowledge/"):
                kid, revision = parse_knowledge_ref(ref)
                if not db.execute(
                    "SELECT 1 FROM knowledge_revisions WHERE knowledge_id=? AND revision=?",
                    (kid, revision),
                ).fetchone():
                    raise NotFoundError(f"Unknown research reference: {ref}")
            else:
                self._require_legacy_ref(db, ref)

    def _require_legacy_ref(self, db: sqlite3.Connection, ref: str) -> None:
        if frozen_refs.is_frozen(ref):
            frozen_refs.require(db, self.root, ref)
            return
        if db.execute("SELECT 1 FROM nodes WHERE node_id=?", (ref,)).fetchone():
            return
        if db.execute("SELECT 1 FROM legacy_refs WHERE ref=?", (ref,)).fetchone():
            return
        if db.execute("SELECT 1 FROM notes WHERE note_id=?", (ref,)).fetchone():
            return
        if db.execute("SELECT 1 FROM attempts WHERE attempt_id=?", (ref,)).fetchone():
            return
        if db.execute("SELECT 1 FROM snapshots WHERE snapshot_id=?", (ref,)).fetchone():
            return
        raise NotFoundError(f"Unknown research reference: {ref}")

    @staticmethod
    def _index_knowledge(db: sqlite3.Connection, value: dict) -> None:
        ref = value["ref"]
        original = "\n".join(
            [value["statement"], _encoded(value["scope"]), _encoded(value["conditions"])]
        )
        bigrams, chars, terms = tokenize(original)
        db.execute(
            "INSERT OR REPLACE INTO knowledge_search VALUES(?,?,?,?,?,?)",
            (ref, original, bigrams, chars, terms, TOKENIZER_VERSION),
        )
        try:
            db.execute("DELETE FROM knowledge_fts WHERE knowledge_ref=?", (ref,))
            db.execute(
                "INSERT INTO knowledge_fts VALUES(?,?,?,?,?)",
                (ref, original, bigrams, chars, terms),
            )
        except sqlite3.OperationalError:
            pass

    def _write_relations_in_tx(
        self, db: sqlite3.Connection, version_ref: str, relations: list, created_at: str, asserted_at: dict | None = None
    ) -> list:
        epistemic = _epistemic()
        checked = epistemic.normalise_relations(relations, version_ref)
        return epistemic.write_declared_relations(
            db,
            version_ref=version_ref,
            relations=checked,
            operation_id=asserted_at["operation_id"] if asserted_at else created_at,
            asserted_at=asserted_at,
            created_at=created_at,
            next_id=self._next,
        )

    @staticmethod
    def sync_support_edges(db: sqlite3.Connection, value: dict) -> None:
        """Materialise rule R2's basis view for one freshly written version.

        Edges belong to the version that declared them, so a revision only
        adds its own rows; earlier versions keep theirs and stay traceable
        (R3). ``motivated_by`` is deliberately not consulted: heuristic links
        do not propagate (R4).
        """
        for used_ref, flags in support_refs(
            value.get("dependencies"), value.get("evidence_refs")
        ).items():
            write_support_edge(
                db,
                value["ref"],
                used_ref,
                flags,
                value["created_at"],
                int(value.get("source_sequence") or 0),
            )

    @staticmethod
    def queue_review_in_tx(
        db: sqlite3.Connection, trigger_kind: str, trigger_ref: str, node_id: str | None
    ) -> None:
        at = _now()
        todo_id = "RV-" + hashlib.sha256(f"{trigger_kind}:{trigger_ref}".encode()).hexdigest()[:20]
        db.execute(
            "INSERT OR IGNORE INTO review_todos VALUES(?,?,?,?,?,?,?)",
            (todo_id, trigger_kind, trigger_ref, node_id, "pending", at, at),
        )

    @staticmethod
    def _offline_asserted_at(operation_id: str) -> dict:
        return dict(host_id="native_store", session_id="native_store", turn=None, operation_id=operation_id)

    @staticmethod
    def _reject_asserted_at(fields: dict) -> None:
        if "asserted_at" in fields or (isinstance(fields.get("changes"), dict) and "asserted_at" in fields["changes"]):
            raise ValidationError("asserted_at is server-managed")

    def _execution_refs(self, db: sqlite3.Connection, refs: list) -> list[dict]:
        if not isinstance(refs, list) or not all(isinstance(ref, str) for ref in refs):
            raise ValidationError("execution_refs must be a list of strings")
        checked = []
        for ref in refs:
            prefix, _, identifier = ref.partition(":")
            if prefix == "session":
                exists = db.execute("SELECT 1 FROM workflow_sessions WHERE session_id=? UNION ALL SELECT 1 FROM exploration_tasks WHERE session_id=? LIMIT 1", (identifier, identifier)).fetchone()
                reason = None if exists else "not_found"
            elif prefix in {"attempt", "event"}:
                table, column = ("attempts", "attempt_id") if prefix == "attempt" else ("events", "event_id")
                reason = None if db.execute(f"SELECT 1 FROM {table} WHERE {column}=?", (identifier,)).fetchone() else "not_found"
            elif frozen_refs.is_frozen(ref):
                resolution = frozen_refs.resolve(db, self.root, ref)
                reason = resolution["reason"] if resolution["outcome"] != "resolved" else (None if resolution["object"] else "not_an_object")
            else:
                reason = "unsupported_form"
            checked.append(dict(ref=ref, status="linked" if reason is None else "unlinked", reason=reason))
        return checked

    def record_knowledge(
        self, fields: dict, request_id: str, *, execution_identity: tuple[str, str] | None = None,
        asserted_at: dict | None = None
    ) -> dict:
        self._reject_asserted_at(fields)
        asserted_at = asserted_at or self._offline_asserted_at(request_id)
        kind = fields.get("kind")
        if kind not in KINDS:
            raise ValidationError("Invalid knowledge kind")
        status = fields.get("status", "proposed")
        if status not in STATUSES:
            raise ValidationError("Invalid knowledge status")
        statement = _require_text(fields.get("statement"), "statement")
        evidence = _json_value(fields.get("evidence_refs"), "evidence_refs", [])
        dependencies = _json_value(fields.get("dependencies"), "dependencies", [])
        for parameter in ("affected_scope_mode", "affected_scope", "change_kind"):
            if fields.get(parameter):
                # Change-event parameters belong to a revision. Accepting them
                # here would silently drop a declaration the caller believed
                # they had made (A0_SCHEMA.md §7.1).
                raise ValidationError(f"{parameter} applies to revise, not record")
        relations = _epistemic().normalise_relations(fields.get("relations"))
        for target in _epistemic().grounded_targets(relations):
            if target not in dependencies:
                dependencies.append(target)
        if not isinstance(evidence, list) or not all(isinstance(x, str) for x in evidence):
            raise ValidationError("evidence_refs must be strings")
        if not isinstance(dependencies, list) or not all(isinstance(x, str) for x in dependencies):
            raise ValidationError("dependencies must be strings")
        if kind in {"observation", "claim", "lesson"} and not evidence:
            raise ValidationError(f"{kind} requires at least one evidence reference")
        payload = {
            "kind": kind,
            "statement": statement,
            "scope": _json_value(fields.get("scope"), "scope", {}),
            "conditions": _json_value(fields.get("conditions"), "conditions", {}),
            "status": status,
            "evidence_refs": evidence,
            "source_identity": _json_value(fields.get("source_identity"), "source_identity", {}),
            "dependencies": dependencies,
            "motivated_by": _json_value(fields.get("motivated_by"), "motivated_by", []),
            "relations": relations,
            "supersedes": [],
            "author": _require_text(fields.get("author", "research-agent"), "author"),
            "node_id": fields.get("node_id"),
            "source_sequence": fields.get("source_sequence"),
        }
        if "execution_refs" in fields:
            payload["execution_refs"] = fields["execution_refs"]
        if execution_identity is not None:
            visibility = fields.get("visibility", "node")
            if visibility not in {"node", "project"}:
                raise ValidationError("visibility must be node or project")
            if visibility == "project" and fields.get("node_id") is not None:
                raise ValidationError("project visibility cannot also specify node_id")
            # Keep old request fingerprints valid: a pre-upgrade global record
            # replays its original receipt rather than changing placement.
            if "visibility" in fields:
                payload["visibility"] = visibility

        def work(db: sqlite3.Connection) -> dict:
            effective = dict(payload)
            if execution_identity is not None and visibility == "node" and not payload["node_id"]:
                association = self._association(db, *execution_identity)
                attempt = db.execute(
                    "SELECT node_id FROM attempts WHERE association_id=? AND ended_at IS NULL",
                    (association["association_id"],),
                ).fetchone()
                if not attempt or not attempt["node_id"]:
                    raise ValidationError(
                        "No current node; specify node_id or visibility='project'"
                    )
                effective["node_id"] = attempt["node_id"]
            return self._record_knowledge_in_tx(db, effective, asserted_at=asserted_at)

        return self._mutate("knowledge.record", payload, request_id, work)

    def _record_knowledge_in_tx(self, db: sqlite3.Connection, payload: dict, *, asserted_at: dict | None = None) -> dict:
        node_id = payload.get("node_id")
        if node_id and not db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone():
            raise NotFoundError(f"Unknown node: {node_id}")
        evidence = payload.get("evidence_refs", [])
        dependencies = payload.get("dependencies", [])
        self._validate_evidence(db, evidence)
        self._validate_evidence(db, dependencies)
        # Heuristic links must resolve too, but they never become support
        # edges: a motivation does not propagate invalidation (R4).
        self._validate_evidence(db, payload.get("motivated_by", []))
        kid = self._next(db, "knowledge", "K")
        at = _now()
        db.execute(
            "INSERT INTO knowledge_entries VALUES(?,?,?,?)", (kid, payload["kind"], node_id, at),
        )
        db.execute(
            _REVISION_INSERT,
            (
                kid,
                1,
                payload["statement"],
                _encoded(payload.get("scope", {})),
                _encoded(payload.get("conditions", {})),
                payload.get("status", "proposed"),
                _encoded(evidence),
                _encoded(payload.get("source_identity", {})),
                _encoded(dependencies),
                "[]",
                payload.get("author", "research-agent"),
                at,
                payload.get("source_sequence")
                if payload.get("source_sequence") is not None
                else int(
                    db.execute("SELECT COALESCE(MAX(event_id),0)+1 FROM events").fetchone()[0]
                ),
                _encoded(payload.get("motivated_by", [])),
                _encoded(asserted_at),
                _encoded(self._execution_refs(db, payload.get("execution_refs", []))),
            ),
        )
        value = self._decode_knowledge(self._latest_revision(db, kid))
        self._index_knowledge(db, value)
        self.sync_support_edges(db, value)
        self._write_relations_in_tx(db, value["ref"], payload.get("relations", []), at, asserted_at)
        _epistemic().late_reference_impacts(
            db, value["ref"], value["created_at"], int(value.get("source_sequence") or 0)
        )
        self._event(db, "knowledge.recorded", {"ref": value["ref"], "node_id": node_id})
        return value

    def revise_knowledge(self, fields: dict, request_id: str, *, asserted_at: dict | None = None) -> dict:
        self._reject_asserted_at(fields)
        asserted_at = asserted_at or self._offline_asserted_at(request_id)
        ref = _require_text(fields.get("ref"), "ref")
        kid, referenced_revision = parse_knowledge_ref(ref)
        expected = int(fields.get("expected_revision", referenced_revision))
        payload = {
            "ref": ref,
            "expected_revision": expected,
            "changes": _json_value(fields.get("changes"), "changes", {}),
            "reason": _require_text(fields.get("reason"), "reason"),
            "source_identity": _json_value(fields.get("source_identity"), "source_identity", {}),
            "relations": _epistemic().normalise_relations(fields.get("relations")),
            "affected_scope_mode": fields.get("affected_scope_mode"),
            "affected_scope": _json_value(fields.get("affected_scope"), "affected_scope", []),
            "change_kind": fields.get("change_kind"),
        }

        if "execution_refs" in fields:
            payload["execution_refs"] = fields["execution_refs"]

        def work(db: sqlite3.Connection) -> dict:
            current = self._decode_knowledge(self._latest_revision(db, kid))
            if current["revision"] != expected:
                raise ConflictError(
                    f"knowledge revision conflict: expected {expected}, current {current['revision']}"
                )
            changes = payload["changes"]
            allowed = {
                "statement",
                "scope",
                "conditions",
                "status",
                "evidence_refs",
                "dependencies",
                "motivated_by",
                "author",
            }
            if not isinstance(changes, dict) or set(changes) - allowed:
                raise ValidationError("Unsupported knowledge revision fields")
            value = {**current, **changes}
            if value["status"] not in STATUSES:
                raise ValidationError("Invalid knowledge status")
            value["statement"] = _require_text(value["statement"], "statement")
            for key in ("scope", "conditions"):
                value[key] = _json_value(value[key], key, {})
            for target in _epistemic().grounded_targets(payload["relations"]):
                listed = value.get("dependencies") or []
                if not isinstance(listed, list):
                    listed = []
                if target not in listed:
                    value["dependencies"] = [*listed, target]
            for key in ("evidence_refs", "dependencies", "motivated_by"):
                value[key] = _json_value(value[key], key, [])
                if not isinstance(value[key], list) or not all(
                    isinstance(x, str) for x in value[key]
                ):
                    raise ValidationError(f"{key} must be strings")
                self._validate_evidence(db, value[key])
            revision = current["revision"] + 1
            at = _now()
            supersedes = [current["ref"]]
            db.execute(
                _REVISION_INSERT,
                (
                    kid,
                    revision,
                    value["statement"],
                    _encoded(value["scope"]),
                    _encoded(value["conditions"]),
                    value["status"],
                    _encoded(value["evidence_refs"]),
                    _encoded(payload["source_identity"]),
                    _encoded(value["dependencies"]),
                    _encoded(supersedes),
                    value.get("author", current["author"]),
                    at,
                    int(db.execute("SELECT COALESCE(MAX(event_id),0)+1 FROM events").fetchone()[0]),
                    _encoded(value["motivated_by"]),
                    _encoded(asserted_at),
                    # R37: attribution belongs to this write; never inherit it from current.
                    _encoded(self._execution_refs(db, payload.get("execution_refs", []))),
                ),
            )
            fresh = self._decode_knowledge(self._latest_revision(db, kid))
            self._index_knowledge(db, fresh)
            self.sync_support_edges(db, fresh)
            self._write_relations_in_tx(db, fresh["ref"], payload["relations"], at, asserted_at)
            mode, resolved_scope, change_kind = _epistemic().validate_change(
                mode=payload["affected_scope_mode"],
                scope=payload["affected_scope"],
                kind=payload["change_kind"],
                knowledge_id=kid,
                new_revision=revision,
                retracts=(
                    value["status"] == "retracted" and current["status"] != "retracted"
                ),
            )
            sequence = int(fresh.get("source_sequence") or 0)
            change = _epistemic().apply_change(
                db,
                change_id=self._next(db, "knowledge_changes", "CH"),
                knowledge_id=kid,
                new_revision=revision,
                kind=change_kind,
                mode=mode,
                scope=resolved_scope,
                reason=payload["reason"],
                operation_id=request_id,
                created_at=at,
                sequence=sequence,
            )
            # A revision may itself cite a version that an earlier change
            # already invalidated; that is a late reference, not a new change.
            _epistemic().late_reference_impacts(db, fresh["ref"], at, sequence)
            self._event(
                db,
                "knowledge.revised",
                {
                    "ref": fresh["ref"],
                    "supersedes": current["ref"],
                    "reason": payload["reason"],
                    "change_id": change["change_id"],
                },
            )
            self.queue_review_in_tx(db, "knowledge-revision", fresh["ref"], fresh["node_id"])
            fresh["change"] = change
            return fresh

        return self._mutate("knowledge.revise", payload, request_id, work)

    def dispose_impact(self, fields: dict, request_id: str) -> dict:
        """Record an explicit disposition for one impact (R21)."""
        payload = {
            "change_id": _require_text(fields.get("change_id"), "change_id"),
            "affected_version": _require_text(fields.get("affected_version"), "affected_version"),
            "kind": _require_text(fields.get("disposition_kind"), "disposition_kind"),
            "reason": _require_text(fields.get("reason"), "reason"),
            "evidence_refs": _json_value(fields.get("evidence_refs"), "evidence_refs", []),
            "replacement_ref": fields.get("replacement_ref"),
            "author": fields.get("author", "research-agent"),
        }
        parse_knowledge_ref(payload["affected_version"])

        def work(db: sqlite3.Connection) -> dict:
            self._validate_evidence(db, payload["evidence_refs"])
            if payload["replacement_ref"]:
                self._validate_evidence(db, [payload["replacement_ref"]])
            at = _now()
            value = _epistemic().write_disposition(
                db,
                disposition_id=self._next(db, "knowledge_dispositions", "DP"),
                change_id=payload["change_id"],
                affected_version=payload["affected_version"],
                kind=payload["kind"],
                reason=payload["reason"],
                evidence_refs=payload["evidence_refs"],
                replacement_ref=payload["replacement_ref"],
                author=payload["author"],
                operation_id=request_id,
                created_at=at,
                sequence=_epistemic().next_sequence(db),
            )
            self._event(db, "knowledge.disposed", value)
            return value

        return self._mutate("knowledge.dispose", payload, request_id, work)

    def impact_review_state(
        self, change_id: str, affected_version: str, state: str, request_id: str
    ) -> dict:
        """Review progress only. It never records a disposition and never
        clears a risk: a returned proposal means materials are ready (R24)."""
        if state not in {"pending", "running", "proposal_ready"}:
            raise ValidationError("Invalid impact review state")
        payload = {
            "change_id": change_id,
            "affected_version": affected_version,
            "state": state,
        }

        def work(db: sqlite3.Connection) -> dict:
            updated = db.execute(
                "UPDATE knowledge_impacts SET review_state=?"
                " WHERE change_id=? AND affected_version=?",
                (state, change_id, affected_version),
            ).rowcount
            if not updated:
                raise NotFoundError(f"Unknown impact: {change_id} {affected_version}")
            return dict(payload)

        return self._mutate("knowledge.impact_review", payload, request_id, work)

    def impact_next(self, node_id: str | None = None) -> dict | None:
        """Oldest valid, undisposed impact, optionally for one node.

        This replaces ``review_todos`` as consolidation's source: that table is
        keyed by the triggering revision and cannot address a
        ``(change_id, affected_version)`` pair at all.
        """
        with self._read() as db:
            epistemic = _epistemic()
            rows = epistemic.valid_impacts(db)
            for row in sorted(rows, key=lambda item: (item["detected_sequence"], item["impact_id"])):
                disposition = epistemic.current_disposition(
                    db, row["change_id"], row["affected_version"]
                )
                if (disposition["kind"] if disposition else None) not in (None, "unresolved"):
                    continue
                if row["review_state"] != "pending":
                    # A proposal already prepared waits for its owner to
                    # dispose of it; handing it out again starves everything
                    # behind it (R24, 2026-09-21).
                    continue
                if node_id is not None:
                    knowledge_id, _revision = parse_knowledge_ref(row["affected_version"])
                    owner = db.execute(
                        "SELECT node_id FROM knowledge_entries WHERE knowledge_id=?",
                        (knowledge_id,),
                    ).fetchone()
                    if not owner or owner[0] != node_id:
                        continue
                return dict(row)
            return None

    def knowledge_risk(self, versions: list[str], bound: int | None = None) -> dict:
        """The two predicates plus the separately displayed version notices."""
        with self._read() as db:
            epistemic = _epistemic()
            anchors = epistemic.anchor_set(db)
            limit = bound if bound is not None else epistemic.read_bound(db)
            return {
                "sequence_bound": limit,
                "versions": {
                    version: {
                        "needs_action": epistemic.needs_action(db, version, limit),
                        "residual_use_risk": epistemic.residual_use_risk(db, version, limit),
                        "version_notices": epistemic.version_notices(db, version, limit),
                        **epistemic.in_use(db, version, anchors=anchors),
                    }
                    for version in versions
                },
            }

    def narrow_impact_scope(self, fields: dict, request_id: str) -> dict:
        """Append an auditable narrowing of an unknown scope (R9)."""
        payload = {
            "change_id": _require_text(fields.get("change_id"), "change_id"),
            "affected_scope": _json_value(fields.get("affected_scope"), "affected_scope", []),
            "reason": _require_text(fields.get("reason"), "reason"),
        }

        def work(db: sqlite3.Connection) -> dict:
            epistemic = _epistemic()
            value = epistemic.narrow_scope(
                db,
                scope_revision_id=self._next(db, "knowledge_scope_revisions", "SR"),
                change_id=payload["change_id"],
                scope=payload["affected_scope"],
                reason=payload["reason"],
                operation_id=request_id,
                created_at=_now(),
                sequence=epistemic.next_sequence(db),
            )
            self._event(db, "knowledge.scope_narrowed", value)
            return value

        return self._mutate("knowledge.narrow_scope", payload, request_id, work)

    def knowledge_impacts(
        self,
        *,
        version: str | None = None,
        change_id: str | None = None,
        limit: int = 50,
        offset: int = 0,
        bound: int | None = None,
    ) -> dict:
        with self._read() as db:
            return _epistemic().impact_query(
                db,
                version=version,
                change_id=change_id,
                limit=limit,
                offset=offset,
                bound=bound,
            )

    def publication_check(self, publication_id: str, bound: int | None = None) -> dict:
        """Re-run the check, or replay a stored one by passing its bound."""
        with self._read() as db:
            return _epistemic().publication_check(db, publication_id, bound)

    def stored_publication_check(self, publication_id: str) -> dict | None:
        with self._read() as db:
            row = db.execute(
                "SELECT * FROM publication_checks WHERE publication_id=?"
                " ORDER BY sequence_bound DESC LIMIT 1",
                (publication_id,),
            ).fetchone()
            if row is None:
                return None
            value = dict(row)
            value["check_scope"] = json.loads(value["check_scope"])
            value["result"] = json.loads(value["result"])
            return value

    def checkpoint(self, fields: dict, request_id: str) -> dict:
        node_id = fields.get("node_id")
        payload = {
            "node_id": node_id,
            "state": _json_value(fields.get("state"), "state", {}),
            "expected_revision": fields.get("expected_revision"),
            "source_identity": _json_value(fields.get("source_identity"), "source_identity", {}),
        }

        def work(db: sqlite3.Connection) -> dict:
            if (
                node_id
                and not db.execute("SELECT 1 FROM nodes WHERE node_id=?", (node_id,)).fetchone()
            ):
                raise NotFoundError(f"Unknown node: {node_id}")
            row = db.execute(
                "SELECT MAX(revision) revision FROM node_checkpoints WHERE node_id IS ?", (node_id,)
            ).fetchone()
            current = int(row["revision"] or 0)
            if (
                payload["expected_revision"] is not None
                and int(payload["expected_revision"]) != current
            ):
                raise ConflictError(
                    f"checkpoint revision conflict: expected {payload['expected_revision']}, current {current}"
                )
            revision = current + 1
            cid = (
                "CP-" + hashlib.sha256((request_id + ":" + str(revision)).encode()).hexdigest()[:20]
            )
            at = _now()
            db.execute(
                "INSERT INTO node_checkpoints VALUES(?,?,?,?,?,?)",
                (
                    cid,
                    node_id,
                    revision,
                    _encoded(payload["state"]),
                    _encoded(payload["source_identity"]),
                    at,
                ),
            )
            value = {
                "checkpoint_id": cid,
                "node_id": node_id,
                "revision": revision,
                "state": payload["state"],
                "source_identity": payload["source_identity"],
                "created_at": at,
            }
            self._event(
                db,
                "memory.checkpointed",
                {"checkpoint_id": cid, "node_id": node_id, "revision": revision},
            )
            return value

        return self._mutate("memory.checkpoint", payload, request_id, work)

    def review_todo_next(self, node_id: str | None = None) -> dict | None:
        with self._read() as db:
            row = db.execute(
                "SELECT * FROM review_todos WHERE state='pending' AND (? IS NULL OR node_id IS ?) "
                "ORDER BY rowid LIMIT 1",
                (node_id, node_id),
            ).fetchone()
            return dict(row) if row else None

    def review_todo_state(self, todo_id: str, state: str, request_id: str) -> dict:
        # ``proposal_ready`` is additive (R24): consolidation now parks a todo
        # there instead of closing it, and ``review_todo_next`` still selects
        # only ``pending``, so the queue does not loop.
        if state not in {"pending", "running", "proposal_ready", "completed"}:
            raise ValidationError("Invalid review todo state")
        payload = {"todo_id": _require_text(todo_id, "todo_id"), "state": state}

        def work(db: sqlite3.Connection) -> dict:
            row = db.execute("SELECT * FROM review_todos WHERE todo_id=?", (todo_id,)).fetchone()
            if not row:
                raise NotFoundError("Unknown review todo")
            db.execute(
                "UPDATE review_todos SET state=?,updated_at=? WHERE todo_id=?",
                (state, _now(), todo_id),
            )
            value = dict(
                db.execute("SELECT * FROM review_todos WHERE todo_id=?", (todo_id,)).fetchone()
            )
            self._event(db, "review.todo", {"todo_id": todo_id, "state": state})
            return value

        return self._mutate("review.todo", payload, request_id, work)

    def knowledge_query(
        self,
        *,
        query: str | None = None,
        node_id: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        revision: int | None = None,
        conditions: dict | None = None,
        after: int = 0,
        limit: int = 50,
    ) -> dict:
        limit = max(1, min(int(limit), 200))
        after = max(0, int(after))
        if conditions is not None and not isinstance(conditions, dict):
            raise ValidationError("conditions filter must be an object")
        with self._read() as db:
            clauses = ["r.rowid>?"]
            args: list[Any] = [after]
            if revision is None:
                clauses.append(
                    "r.revision=(SELECT MAX(r2.revision) FROM knowledge_revisions r2 WHERE r2.knowledge_id=r.knowledge_id)"
                )
            else:
                clauses.append("r.revision=?")
                args.append(int(revision))
            if node_id:
                clauses.append("e.node_id=?")
                args.append(node_id)
            if kind:
                clauses.append("e.kind=?")
                args.append(kind)
            if status:
                clauses.append("r.status=?")
                args.append(status)
            for key, expected in sorted((conditions or {}).items()):
                if not isinstance(key, str) or not re.fullmatch(r"[\w\u3400-\u9fff.-]+", key):
                    raise ValidationError("condition filter keys contain unsupported characters")
                clauses.append("json_extract(r.conditions,?)=json_extract(?,'$')")
                args.extend((f'$."{key}"', _encoded(expected)))
            if query:
                bigrams, chars, terms = tokenize(query)
                candidates = []
                if bigrams:
                    candidates.append(
                        ("s.zh_bigrams LIKE ?", "%" + "%".join(bigrams.split()) + "%")
                    )
                if terms:
                    candidates.append(("s.terms LIKE ?", "%" + terms.split()[0] + "%"))
                if chars and len(query.strip()) == 1:
                    candidates.append(("s.zh_chars LIKE ?", f"%{query.strip()}%"))
                candidates.append(("s.original LIKE ?", f"%{query}%"))
                clauses.append("(" + " OR ".join(item[0] for item in candidates) + ")")
                args.extend(item[1] for item in candidates)
            rows = list(
                db.execute(
                    "SELECT r.rowid cursor,e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) JOIN knowledge_search s ON s.knowledge_ref=('knowledge/'||r.knowledge_id||'@'||r.revision) WHERE "
                    + " AND ".join(clauses)
                    + " ORDER BY r.rowid LIMIT ?",
                    (*args, limit + 1),
                )
            )
            reasons = [
                name
                for name, enabled in (
                    ("text", query),
                    ("node", node_id),
                    ("kind", kind),
                    ("status", status),
                    ("revision", revision),
                    ("conditions", conditions),
                )
                if enabled is not None and enabled != {}
            ]
            items = [
                {**self._decode_knowledge(row), "match_reason": reasons or ["latest"]}
                for row in rows[:limit]
            ]
            return {
                "items": items,
                "next_cursor": rows[limit - 1]["cursor"] if len(rows) > limit else None,
                "has_more": len(rows) > limit,
            }

    def context_view(self, host_id: str, session_id: str, max_chars: int = 12_000) -> dict:
        with self._read() as db:
            project = self._project(db)
            association = db.execute(
                "SELECT * FROM associations WHERE host_id=? AND session_id=? AND ended_at IS NULL",
                (host_id, session_id),
            ).fetchone()
            if not association:
                raise NotFoundError("Session is not associated with this project")
            session = db.execute(
                "SELECT * FROM workflow_sessions WHERE session_id=?", (session_id,)
            ).fetchone()
            attempt = db.execute(
                "SELECT * FROM attempts WHERE association_id=? AND ended_at IS NULL",
                (association["association_id"],),
            ).fetchone()
            task = db.execute(
                "SELECT * FROM exploration_tasks WHERE session_id=? AND state IN ('queued','starting','running','waiting','stopping','unverified')",
                (session_id,),
            ).fetchone()
            specialist = db.execute(
                "SELECT t.* FROM specialist_tasks t JOIN specialist_bindings b USING(task_id) "
                "WHERE b.session_id=? ORDER BY t.created_at DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if specialist and specialist["context_mode"] == "blind":
                body = _encoded({"role": "blind reviewer", "instructions": "Evaluate only the assigned frozen inputs. Use research_read_input; do not infer hidden labels or seek project background.", "inputs": [{"input_id": f"input-{i + 1}"} for i, _ in enumerate(json.loads(specialist["inputs"]))]})
                return {"context_mode": "blind", "text": body, "source_digest": hashlib.sha256(body.encode()).hexdigest(), "source_sequence": db.execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()[0], "dependencies": [f"specialist/{specialist['task_id']}"], "omitted": []}
            node_id = attempt["node_id"] if attempt else session["node_id"] if session else None
            node_row = (
                db.execute(
                    "SELECT node_id,question,plan,question_ref,inputs FROM nodes WHERE node_id=?",
                    (node_id,),
                ).fetchone()
                if node_id
                else None
            )
            checkpoint = db.execute(
                "SELECT * FROM node_checkpoints WHERE node_id IS ? ORDER BY revision DESC LIMIT 1",
                (node_id,),
            ).fetchone()
            review_rows = list(
                db.execute(
                    "SELECT rowid AS cursor,* FROM review_todos WHERE state='pending' "
                    "AND (? IS NULL OR node_id IS ? OR node_id IS NULL) ORDER BY rowid LIMIT 8",
                    (node_id, node_id),
                )
            )
            pending_total = int(
                db.execute(
                    "SELECT COUNT(*) FROM review_todos WHERE state='pending' "
                    "AND (? IS NULL OR node_id IS ? OR node_id IS NULL)",
                    (node_id, node_id),
                ).fetchone()[0]
            )
            review_todos = {
                "pending_total": pending_total,
                "shown_count": len(review_rows),
                "has_more": pending_total > len(review_rows),
                "cursor": review_rows[-1]["cursor"] if review_rows else None,
                "trigger_types": sorted({row["trigger_kind"] for row in review_rows}),
                "claim_owner": session_id if any(row["state"] == "running" for row in review_rows) else None,
                "items": [dict(row) for row in review_rows],
            }
            # The coordinator reads the same semantics the workbench and the
            # publication check do. Risks and version notices stay in separate
            # fields: a newer revision existing is a pointer, not a verdict.
            epistemic = _epistemic()
            # Select first, page second. Taking a page and then dropping the
            # settled rows reports a total nobody can reach: once the first
            # eight are disposed of, everything behind them disappears from the
            # default view (R18 supplement, CE-12v).
            bound = epistemic.read_bound(db)
            from .hints import structure_hints
            hints = structure_hints(db, bound)
            anchors = epistemic.anchor_set(db)
            def still_open(row: sqlite3.Row) -> bool:
                disposition = epistemic.current_disposition(
                    db, row["change_id"], row["affected_version"], bound
                )
                return (disposition["kind"] if disposition else None) in (None, "unresolved")

            open_rows = [row for row in epistemic.valid_impacts(db, bound) if still_open(row)]
            open_rows.sort(key=lambda row: (row["detected_sequence"], row["impact_id"]))
            affected_items = []
            for row in open_rows[:8]:
                version = row["affected_version"]
                roots = epistemic.change_roots(db, row["change_id"], bound)
                affected_items.append(
                    {
                        "change_id": row["change_id"],
                        "affected_version": version,
                        "hop": row["hop"],
                        "edge_source": row["edge_source"],
                        "review_state": row["review_state"],
                        "scope_unconfirmed": epistemic.effective_scope(
                            db, row["change_id"], bound
                        )["mode"]
                        == "unknown",
                        "uncovered_refs": epistemic.uncovered_refs(db, version),
                        "explanation": epistemic.explain_path(db, version, roots),
                        "in_use": epistemic.in_use(db, version, anchors=anchors)["in_use"],
                        "residual_use_risk": epistemic.residual_use_risk(db, version, bound),
                        "version_notices": epistemic.version_notices(db, version, bound),
                    }
                )
            affected_knowledge = {
                "total": len(open_rows),
                "shown_count": len(affected_items),
                "not_in_use_count": sum(
                    1 for item in affected_items if item["in_use"] is False
                ),
                "sequence_bound": bound,
                # On the whole open set, not on the eight shown: a ninth
                # pending row does not make the set undetermined (CE-12v-2).
                "targets_complete": epistemic.targets_are_complete(db, open_rows, bound),
                "targets_truncated": len(open_rows) > len(affected_items),
                "paths_truncated": any(
                    epistemic.has_alternate_path(
                        db,
                        item["affected_version"],
                        epistemic.change_roots(db, item["change_id"], bound),
                    )
                    for item in affected_items
                ),
                "items": affected_items,
            }
            # A version can carry RR1 with no impact row at all: nothing put it
            # in a scope, it simply cites a retracted version (CE-10b). A citer
            # that has itself been retracted is not an action item -- retraction
            # appends a revision inheriting the old references, so listing it
            # would ask the reader to fix something already withdrawn (CE-10b-2).
            retracted_rows = db.execute(
                "SELECT DISTINCT e.user_ref, e.used_ref FROM knowledge_support_edges e"
                " JOIN knowledge_revisions r"
                "  ON ('knowledge/' || r.knowledge_id || '@' || r.revision) = e.used_ref"
                " JOIN knowledge_revisions u"
                "  ON ('knowledge/' || u.knowledge_id || '@' || u.revision) = e.user_ref"
                " WHERE r.status='retracted' AND u.status<>'retracted'"
                " ORDER BY e.user_ref",
            ).fetchall()
            # Not in use is still listed, never silently dropped, and counted
            # the same way the affected set counts it.
            retracted_items = [
                {
                    "version": row["user_ref"],
                    "retracted_ref": row["used_ref"],
                    "in_use": epistemic.in_use(db, row["user_ref"], anchors=anchors)["in_use"],
                }
                for row in retracted_rows[:8]
            ]
            retracted_refs = {
                "total": len(retracted_rows),
                "shown_count": len(retracted_items),
                "not_in_use_count": sum(
                    1 for item in retracted_items if item["in_use"] is False
                ),
                "items": retracted_items,
            }

            # Fixed inputs determine provenance. Global question recency must never
            # crowd out an input's early negative results or later corrections.
            related_nodes = {node_id} if node_id else set()
            pinned = []
            inputs = json.loads(node_row["inputs"]) if node_row else []
            if specialist:
                inputs += json.loads(specialist["inputs"])
            for ref in inputs:
                if ref.startswith("knowledge/"):
                    pinned.append(ref)
                elif frozen_refs.parse(ref).get("publication_id"):
                    pid = frozen_refs.parse(ref)["publication_id"]
                    pub = db.execute(
                        "SELECT node_id FROM publications WHERE publication_id=?", (pid,)
                    ).fetchone()
                    if pub and pub["node_id"]:
                        related_nodes.add(pub["node_id"])
                    pinned.extend(
                        _knowledge_ref(r["knowledge_id"], r["revision"])
                        for r in db.execute(
                            "SELECT knowledge_id,revision FROM publication_knowledge WHERE publication_id=? ORDER BY knowledge_id",
                            (pid,),
                        )
                    )
                elif db.execute("SELECT 1 FROM nodes WHERE node_id=?", (ref,)).fetchone():
                    related_nodes.add(ref)
            selected = {}
            for ref in pinned[:16]:
                kid, rev = parse_knowledge_ref(ref)
                row = db.execute(
                    "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) WHERE r.knowledge_id=? AND r.revision=?",
                    (kid, rev),
                ).fetchone()
                if row:
                    selected[ref] = self._decode_knowledge(row)
                    # Keep the fixed version and its later correction side by side.
                    latest = self._decode_knowledge(self._latest_revision(db, kid))
                    selected[latest["ref"]] = latest
                    if row["node_id"]:
                        related_nodes.add(row["node_id"])
            related_json = _encoded(sorted(related_nodes))
            for row in db.execute(
                "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) "
                "WHERE r.revision=(SELECT MAX(r2.revision) FROM knowledge_revisions r2 WHERE r2.knowledge_id=r.knowledge_id) "
                "AND e.kind!='open_question' AND (e.node_id IS NULL OR e.node_id IN (SELECT value FROM json_each(?)) OR r.status='disputed') "
                "ORDER BY CASE WHEN r.status='disputed' THEN 0 WHEN r.revision>1 THEN 1 WHEN e.kind='lesson' THEN 2 ELSE 3 END,r.rowid DESC LIMIT 24",
                (related_json,),
            ):
                value = self._decode_knowledge(row)
                selected.setdefault(value["ref"], value)
            knowledge = list(selected.values())
            coordinator = session and session["role"] == "main" and node_id is None
            if coordinator:
                # Independent bounded selections prevent a busy agenda or a burst
                # of disputes from hiding the other coordination responsibilities.
                knowledge = {}
                for label, predicate, limit in (
                    ("disputed", "r.status='disputed'", 3),
                    ("revised", "r.revision>1 AND r.status!='disputed'", 3),
                    ("lessons", "e.kind='lesson' AND r.status!='disputed'", 4),
                    ("project", "e.node_id IS NULL AND r.status!='disputed'", 3),
                ):
                    knowledge[label] = [
                        self._decode_knowledge(row)
                        for row in db.execute(
                            "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) "
                            "WHERE r.revision=(SELECT MAX(r2.revision) FROM knowledge_revisions r2 WHERE r2.knowledge_id=r.knowledge_id) "
                            f"AND e.kind!='open_question' AND ({predicate}) ORDER BY r.rowid DESC LIMIT ?",
                            (limit,),
                        )
                    ]
            questions = [
                dict(r)
                for r in db.execute(
                    "SELECT node_id,question,question_ref FROM nodes WHERE node_id IN (SELECT value FROM json_each(?)) ORDER BY CASE WHEN node_id=? THEN 0 ELSE 1 END,rowid LIMIT 6",
                    (related_json, node_id),
                )
            ]
            if coordinator:
                questions = [
                    dict(row)
                    for row in db.execute(
                        "SELECT node_id,question,question_ref,status FROM nodes WHERE status IN ('proposed','open') ORDER BY rowid DESC LIMIT 8"
                    )
                ]
            notes = []
            if attempt:
                notes = [
                    dict(row)
                    for row in db.execute(
                        "SELECT * FROM notes WHERE attempt_id=? ORDER BY rowid DESC LIMIT 8",
                        (attempt["attempt_id"],),
                    )
                ]
            guidance = db.execute(
                "SELECT * FROM guidance_sources WHERE active=1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            guidance_cards = None
            if guidance:
                sections = json.loads(guidance["section_index"])
                task_text = " ".join(
                    filter(
                        None,
                        [
                            project["goal"],
                            node_row["question"] if node_row else "",
                            node_row["plan"] if node_row else "",
                            task["context"] if task else "",
                            specialist["prompt"] if specialist else "",
                        ],
                    )
                )
                task_bigrams, _, task_terms = tokenize(task_text)
                terms = set(f"{task_bigrams} {task_terms}".split())
                selected = []
                for section in sections:
                    heading_bigrams, _, heading_words = tokenize(section.get("heading", ""))
                    heading_terms = set(f"{heading_bigrams} {heading_words}".split())
                    if not selected or heading_terms & terms:
                        selected.append(section)
                    if sum(len(_encoded(item)) for item in selected) >= 4000:
                        break
                guidance_cards = {
                    "guidance_id": guidance["guidance_id"],
                    "version": guidance["version"],
                    "content_hash": guidance["content_hash"],
                    "sections": selected,
                }
            frozen_context = None
            if session and session["role"] in {"discussion", "handoff"}:
                frozen_context = json.loads(session["context"])
                if len(_encoded(frozen_context)) > 6000:
                    frozen_context = {
                        key: frozen_context[key]
                        for key in (
                            "role",
                            "goal",
                            "node",
                            "record_path",
                            "publication_ids",
                            "instruction",
                            "source_snapshot",
                            "source_attempt",
                        )
                        if key in frozen_context
                    }
                    if isinstance(frozen_context.get("goal"), str):
                        frozen_context["goal"] = frozen_context["goal"][:1000]
            blocks = [
                ("project", {"goal": project["goal"], "control": project["control"]}),
                ("role_instructions", {
                    "main": "Coordinate inventory, planning, dispatch, synthesis and continuation. Propose then dispatch node work; full coding and experiments belong to node_core. Do not use research_finish. In manual mode prepare the plan; /research auto enables node execution. If the user requests plan confirmation, submit the complete plan and wait; clarification answers alone do not approve it. Record acceptance criteria in plans/checkpoints and report evidence, gaps and stopping reasons. A successor must declare its actual predecessors and fixed inputs; a handoff failure does not make it a root. Already authorized execution needs no extra confirmation.",
                    "node_core": "Own this node's planning, coding, experiments and analysis. Delegate bounded read-only specialists, publish node findings and use research_finish to end the work segment. Put explicit evidence references, scope and known applicability limits into structured knowledge fields; do not invent missing conditions.",
                    "specialist": "Read only the assigned question and materials; return evidence and uncertainty. Do not change research records or delegate.",
                }.get(session["role"] if session else "", "Follow the assigned research role.")),
                ("control_instructions", "Use research tools to change the ledger. Never edit .research or issue SQL repairs. For stuck specialists use research_verify_specialist; for uncertain exploration creation use research_verify_task. Publication complete means a completed deliverable, not scientific success; pending reviews are not approval gates."),
                (
                    "identity",
                    {
                        "role": session["role"] if session else "legacy",
                        "node_id": node_id,
                        "permissions": "research-read-only"
                        if session and session["role"] in {"discussion", "handoff", "specialist"}
                        else "research-read-write",
                    },
                ),
                (
                    "task",
                    json.loads(task["context"])
                    if task
                    else (
                        {k: node_row[k] for k in ("node_id", "question", "plan", "question_ref")}
                        if node_row
                        else None
                    ),
                ),
                ("specialist_task", self._decode_specialist(specialist) if specialist else None),
                (
                    "attempt",
                    {k: attempt[k] for k in ("attempt_id", "node_id", "role", "mode", "state")}
                    if attempt
                    else None,
                ),
                # Queue pressure is control-plane state, not optional background
                # memory.  Put it ahead of the potentially large knowledge blocks
                # so the total and its bounded sample survive context pressure.
                ("pending_review", review_todos),
                ("affected_knowledge", affected_knowledge),
                ("structure_hints", hints),
                ("retracted_refs", retracted_refs),
                ("knowledge", knowledge),
                ("questions", questions),
                ("frozen_context", frozen_context),
                (
                    "checkpoint",
                    {
                        **dict(checkpoint),
                        "state": json.loads(checkpoint["state"]),
                        "source_identity": json.loads(checkpoint["source_identity"]),
                    }
                    if checkpoint
                    else None,
                ),
                ("method_guidance", guidance_cards),
                ("recent_node_notes", list(reversed(notes))),
                (
                    "index",
                    {
                        "query": "Use research_query with query/kind/node_id/cursor to expand project memory."
                    },
                ),
            ]
            rendered: list[str] = []
            omitted: list[str] = []
            used = 0
            included_refs = []
            max_chars = max(500, min(max_chars, 12000))
            for name, value in blocks:
                if (
                    value is None
                    or value == []
                    or (name == "pending_review" and not value["pending_total"])
                    or (name == "affected_knowledge" and not value["total"])
                    or (name == "retracted_refs" and not value["total"])
                    or (name == "structure_hints" and not value["total"])
                ):
                    continue
                allowance = min(
                    max_chars - used - 150,
                    {
                        "project": 1000,
                        "identity": 400,
                        "task": 2200,
                        "specialist_task": 1400,
                        "role_instructions": 1600,
                        "control_instructions": 800,
                        "attempt": 400,
                        "pending_review": 2200,
                        # Ahead of the large knowledge blocks on purpose: an
                        # invalidated basis is what the coordinator must not
                        # lose to context pressure.
                        "affected_knowledge": 2400,
                        "structure_hints": 2400,
                        "retracted_refs": 800,
                        "knowledge": 4000,
                        "questions": 1600 if coordinator else 800,
                        "frozen_context": 1600,
                    }.get(name, 1000),
                )
                if allowance < 100:
                    omitted.append(name)
                    continue
                if name == "knowledge" and coordinator:
                    groups = {}
                    for label, share in (
                        ("disputed", 0.23),
                        ("revised", 0.23),
                        ("lessons", 0.33),
                        ("project", 0.17),
                    ):
                        rows = value[label]
                        entries = []
                        budget = int(allowance * share)
                        for item in rows:
                            compact = {
                                key: item[key]
                                for key in (
                                    "ref",
                                    "node_id",
                                    "kind",
                                    "statement",
                                    "conditions",
                                    "status",
                                    "evidence_refs",
                                    "supersedes",
                                )
                            }
                            entry = bounded_value(
                                compact, min(420, budget - len(_encoded(entries)) - 80)
                            )
                            if len(_encoded(entries + [entry])) > budget - 60:
                                break
                            entries.append(entry)
                            included_refs.append(item["ref"])
                            if budget - len(_encoded(entries)) < 250:
                                break
                        groups[label] = {
                            "items": entries,
                            "omitted_from_selection": len(rows) - len(entries),
                        }
                    value = groups
                elif name == "knowledge":
                    entries = []
                    for item in value:
                        compact = {
                            key: item[key]
                            for key in (
                                "ref",
                                "kind",
                                "statement",
                                "scope",
                                "conditions",
                                "status",
                                "evidence_refs",
                                "dependencies",
                                "supersedes",
                            )
                        }
                        entry = bounded_value(
                            compact, min(1000, allowance - len(_encoded(entries)) - 100)
                        )
                        if len(_encoded(entries + [entry])) > allowance - 80:
                            break
                        entries.append(entry)
                        included_refs.append(item["ref"])
                        if allowance - len(_encoded(entries)) < 500:
                            break
                    value = {"items": entries, "omitted_count": len(knowledge) - len(entries)}
                else:
                    value = bounded_value(value, allowance)
                text = f"## {name}\n{_encoded(value)}"
                if used + len(text) + 2 <= max_chars:
                    rendered.append(text)
                    used += len(text) + 2
                else:
                    omitted.append(name)
            if omitted:
                rendered.append("## omitted_index\n" + _encoded(omitted))
            body = "\n\n".join(rendered)
            digest = hashlib.sha256(body.encode()).hexdigest()
            upper = int(db.execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()[0])
            dependencies = included_refs
            if coordinator:
                dependencies.extend(q["question_ref"] for q in questions if q["question_ref"])
            if node_row and node_row["question_ref"]:
                dependencies.append(node_row["question_ref"])
            if checkpoint:
                dependencies.append(
                    f"checkpoint/{checkpoint['checkpoint_id']}@{checkpoint['revision']}"
                )
            if guidance:
                dependencies.append(
                    f"guidance/{guidance['guidance_id']}@{guidance['content_hash']}"
                )
            if task:
                dependencies.append(f"exploration/{task['task_id']}")
            if specialist:
                dependencies.append(f"specialist/{specialist['task_id']}")
            if frozen_context:
                dependencies.append(
                    "frozen/" + hashlib.sha256(_encoded(frozen_context).encode()).hexdigest()
                )
            return {
                "text": body,
                "context_mode": specialist["context_mode"] if specialist else None,
                "source_digest": digest,
                "dependencies": sorted(set(dependencies)),
                "truncated": bool(omitted),
                "omitted": omitted,
                "source_sequence": upper,
            }

    def record_context_request(self, fields: dict, request_id: str) -> dict:
        body = fields["body"]
        pack_id = hashlib.sha256(
            (fields.get("policy_version", "memory-v2") + "\0" + body).encode()
        ).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT context_id,pack_id FROM context_requests WHERE operation_id=?",
                    (request_id,),
                ).fetchone()
                if previous:
                    db.commit()
                    return dict(previous)
                db.execute(
                    "INSERT OR IGNORE INTO context_packs VALUES(?,?,?,?,?)",
                    (
                        pack_id,
                        body,
                        _encoded(fields.get("dependencies", [])),
                        fields.get("policy_version", "memory-v2"),
                        _now(),
                    ),
                )
                db.execute(
                    "INSERT INTO context_requests(operation_id,host_id,session_id,turn,step,purpose,pack_id,source_sequence,selection,size_estimate,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        fields["host_id"],
                        fields["session_id"],
                        fields.get("turn"),
                        fields.get("step"),
                        fields.get("purpose"),
                        pack_id,
                        fields.get("source_sequence"),
                        _encoded(fields.get("selection", {})),
                        len(body),
                        _now(),
                    ),
                )
                context_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
                db.commit()
                return {"context_id": context_id, "pack_id": pack_id}
            except BaseException:
                db.rollback()
                raise

    def rebuild_memory_projection(self) -> None:
        root = self.meta / "memory"
        root.mkdir(parents=True, exist_ok=True)
        with self._read() as db:
            project = self._project(db)
            knowledge = [
                self._decode_knowledge(row)
                for row in db.execute(
                    "SELECT e.kind,e.node_id,r.* FROM knowledge_entries e JOIN knowledge_revisions r USING(knowledge_id) WHERE r.revision=(SELECT MAX(revision) FROM knowledge_revisions WHERE knowledge_id=e.knowledge_id) ORDER BY e.rowid"
                )
            ]
            sequence = int(db.execute("SELECT COALESCE(MAX(event_id),0) FROM events").fetchone()[0])
        lines = [
            "# Project memory",
            "",
            f"Goal: {project['goal']}",
            "",
            f"Source sequence: {sequence}",
            "",
            "## Knowledge",
        ]
        for item in knowledge:
            lines.extend(
                [
                    "",
                    f"- {item['ref']} · {item['kind']} · {item['status']}",
                    f"  {item['statement']}",
                ]
            )
        body = "\n".join(lines) + "\n"
        digest = hashlib.sha256(body.encode()).hexdigest()
        body = f"<!-- projection-sha256: {digest} -->\n" + body
        temporary = root / "PROJECT.md.tmp"
        if temporary.exists():
            temporary.chmod(0o644)
        temporary.write_text(body, encoding="utf-8")
        temporary.chmod(0o444)
        target = root / "PROJECT.md"
        if target.exists():
            previous = target.read_text(encoding="utf-8")
            first, _, remainder = previous.partition("\n")
            recorded = first.removeprefix("<!-- projection-sha256: ").removesuffix(" -->")
            actual = hashlib.sha256(remainder.encode()).hexdigest()
            if recorded != actual:
                drafts = root / "drafts"
                drafts.mkdir(exist_ok=True)
                draft = drafts / f"PROJECT-{hashlib.sha256(previous.encode()).hexdigest()[:20]}.md"
                if not draft.exists():
                    draft.write_text(previous, encoding="utf-8")
                    draft.chmod(0o444)
            target.chmod(0o644)
        temporary.replace(target)

    def specialist_create(self, fields: dict, request_id: str) -> dict:
        payload = {
            "parent_session_id": _require_text(
                fields.get("parent_session_id"), "parent_session_id"
            ),
            "node_id": fields.get("node_id"),
            "attempt_id": fields.get("attempt_id"),
            "purpose": _require_text(fields.get("purpose"), "purpose"),
            "label": _require_text(fields.get("label"), "label"),
            "prompt": _require_text(fields.get("prompt"), "prompt"),
            "inputs": _json_value(fields.get("inputs"), "inputs", []),
            "fanout_limit": int(fields.get("fanout_limit", 2)),
            "context_mode": fields.get("context_mode", "research"),
        }

        def work(db: sqlite3.Connection) -> dict:
            if payload["context_mode"] not in {"research", "blind"}:
                raise ValidationError("context_mode must be research or blind")
            if payload["purpose"] not in {"review", "domain"}:
                raise ValidationError("Specialist purpose must be review or domain")
            if (
                payload["node_id"]
                and not db.execute(
                    "SELECT 1 FROM nodes WHERE node_id=?", (payload["node_id"],)
                ).fetchone()
            ):
                raise NotFoundError("Unknown specialist node")
            if not isinstance(payload["inputs"], list) or not all(
                isinstance(ref, str) for ref in payload["inputs"]
            ):
                raise ValidationError("Specialist inputs must be research references")
            self._validate_evidence(db, payload["inputs"])
            existing = db.execute(
                "SELECT * FROM specialist_tasks WHERE operation_id=?", (request_id,)
            ).fetchone()
            if existing:
                return self._decode_specialist(existing)
            if not 1 <= payload["fanout_limit"] <= 8:
                raise ValidationError("Specialist fan-out limit must be between 1 and 8")
            active = db.execute(
                "SELECT COUNT(*) FROM specialist_tasks WHERE node_id IS ? AND state IN ('starting','running','unverified')",
                (payload["node_id"],),
            ).fetchone()[0]
            if active >= payload["fanout_limit"]:
                raise ConflictError(
                    f"The node specialist fan-out limit ({payload['fanout_limit']}) is reached"
                )
            task_id = "S-" + hashlib.sha256(request_id.encode()).hexdigest()[:20]
            at = _now()
            db.execute(
                "INSERT INTO specialist_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    task_id,
                    request_id,
                    payload["parent_session_id"],
                    None,
                    payload["node_id"],
                    payload["attempt_id"],
                    payload["purpose"],
                    payload["label"],
                    payload["prompt"],
                    _encoded(payload["inputs"]),
                    "starting",
                    None,
                    None,
                    0,
                    at,
                    at,
                    payload["context_mode"],
                ),
            )
            return self._decode_specialist(
                db.execute("SELECT * FROM specialist_tasks WHERE task_id=?", (task_id,)).fetchone()
            )

        return self._mutate("specialist.create", payload, request_id, work)

    @staticmethod
    def _decode_specialist(row: sqlite3.Row) -> dict:
        value = dict(row)
        value["inputs"] = json.loads(value["inputs"])
        value["result"] = json.loads(value["result"]) if value["result"] else None
        value["exit_verified"] = bool(value["exit_verified"])
        return value

    def specialist_bind(self, fields: dict, request_id: str) -> dict:
        payload = {
            "task_id": _require_text(fields.get("task_id"), "task_id"),
            "session_id": _require_text(fields.get("session_id"), "session_id"),
            "parent_session_id": _require_text(
                fields.get("parent_session_id"), "parent_session_id"
            ),
        }

        def work(db: sqlite3.Connection) -> dict:
            task = db.execute(
                "SELECT * FROM specialist_tasks WHERE task_id=?", (payload["task_id"],)
            ).fetchone()
            if not task or task["parent_session_id"] != payload["parent_session_id"]:
                raise ConflictError("Specialist lineage does not match the durable intent")
            existing = db.execute(
                "SELECT * FROM specialist_bindings WHERE session_id=?", (payload["session_id"],)
            ).fetchone()
            if existing and existing["task_id"] != payload["task_id"]:
                raise ConflictError("Specialist session is already bound")
            at = _now()
            db.execute(
                "INSERT OR IGNORE INTO specialist_bindings VALUES(?,?,?,?,?,?,?)",
                (
                    payload["session_id"],
                    payload["task_id"],
                    payload["parent_session_id"],
                    task["node_id"],
                    task["attempt_id"],
                    "read-only",
                    at,
                ),
            )
            db.execute(
                "UPDATE specialist_tasks SET child_session_id=?,state='running',updated_at=? WHERE task_id=?",
                (payload["session_id"], at, payload["task_id"]),
            )
            return {
                "task_id": payload["task_id"],
                "session_id": payload["session_id"],
                "permission": "read-only",
            }

        return self._mutate("specialist.bind", payload, request_id, work)

    def specialist_get(self, task_id: str) -> dict:
        with self._read() as db:
            row = db.execute(
                "SELECT * FROM specialist_tasks WHERE task_id=?", (task_id,)
            ).fetchone()
            if not row:
                raise NotFoundError("Unknown specialist task")
            return self._decode_specialist(row)

    def specialist_finish(self, fields: dict, request_id: str) -> dict:
        payload = {
            "task_id": _require_text(fields.get("task_id"), "task_id"),
            "parent_session_id": fields.get("parent_session_id"),
            "state": fields.get("state", "completed"),
            "result": _json_value(fields.get("result"), "result", {}),
            "error": fields.get("error"),
            "exit_verified": bool(fields.get("exit_verified")),
            "preserve_result": bool(fields.get("preserve_result")),
        }
        if payload["state"] not in {"completed", "incomplete", "cancelled", "unverified"}:
            raise ValidationError("Invalid specialist terminal state")

        def work(db: sqlite3.Connection) -> dict:
            task = db.execute(
                "SELECT * FROM specialist_tasks WHERE task_id=?", (payload["task_id"],)
            ).fetchone()
            if not task:
                raise NotFoundError("Unknown specialist task")
            if (
                payload["parent_session_id"]
                and task["parent_session_id"] != payload["parent_session_id"]
            ):
                raise ConflictError("Only the recorded parent can settle this specialist task")
            at = _now()
            db.execute(
                "UPDATE specialist_tasks SET state=?,result=?,error=?,exit_verified=?,updated_at=? WHERE task_id=?",
                (
                    payload["state"],
                    task["result"] if payload["preserve_result"] else _encoded(payload["result"]),
                    payload["error"],
                    int(payload["exit_verified"]),
                    at,
                    payload["task_id"],
                ),
            )
            value = self._decode_specialist(
                db.execute(
                    "SELECT * FROM specialist_tasks WHERE task_id=?", (payload["task_id"],)
                ).fetchone()
            )
            self._event(
                db,
                "specialist.finished",
                {
                    "task_id": value["task_id"],
                    "state": value["state"],
                    "exit_verified": value["exit_verified"],
                },
            )
            return value

        return self._mutate("specialist.finish", payload, request_id, work)

    def guidance_register(self, fields: dict, request_id: str) -> dict:
        content = _require_text(fields.get("content"), "content")
        path = _require_text(fields.get("path"), "path")
        version = _require_text(fields.get("version"), "version")
        payload = {
            "path": path,
            "version": version,
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
        }

        def work(db: sqlite3.Connection) -> dict:
            sections, heading, body = [], "Common principles", []
            for line in content.splitlines():
                if line.startswith("#"):
                    if body:
                        sections.append({"heading": heading, "body": "\n".join(body).strip()})
                    heading, body = line.lstrip("#").strip() or heading, []
                else:
                    body.append(line)
            if body or not sections:
                sections.append({"heading": heading, "body": "\n".join(body).strip()})
            guidance_id = "G-" + payload["content_hash"][:20]
            at = _now()
            db.execute("UPDATE guidance_sources SET active=0")
            db.execute(
                "INSERT INTO guidance_sources VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(guidance_id) DO UPDATE SET path=excluded.path,"
                "version=excluded.version,section_index=excluded.section_index,active=1",
                (guidance_id, path, version, payload["content_hash"], _encoded(sections), 1, at),
            )
            value = {
                "guidance_id": guidance_id,
                **payload,
                "sections": len(sections),
                "active": True,
            }
            self._event(db, "guidance.registered", value)
            return value

        return self._mutate("guidance.register", payload, request_id, work)

    def guidance_status(self) -> dict | None:
        with self._read() as db:
            row = db.execute(
                "SELECT * FROM guidance_sources WHERE active=1 ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            value = dict(row)
            value["section_index"] = json.loads(value["section_index"])
            value["active"] = bool(value["active"])
            return value
