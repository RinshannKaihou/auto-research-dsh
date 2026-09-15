"""Schema 3 workflow ledger. No models, threads, timers or task execution."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .errors import ConflictError, ValidationError, NotFoundError


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


DDL = """
CREATE TABLE IF NOT EXISTS workflow_project (
 id INTEGER PRIMARY KEY CHECK(id=1), main_session_id TEXT, state TEXT NOT NULL,
 generation INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_sessions (
 session_id TEXT PRIMARY KEY, host_id TEXT NOT NULL, role TEXT NOT NULL,
 node_id TEXT, cwd TEXT, pause_reason TEXT, waiting TEXT NOT NULL DEFAULT '[]',
 context TEXT NOT NULL DEFAULT '{}', goal_id TEXT, goal_revision INTEGER,
 detached INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_main_session ON workflow_sessions(role)
 WHERE role='main' AND detached=0;
CREATE TABLE IF NOT EXISTS exploration_tasks (
 task_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL,
 parent_session_id TEXT NOT NULL, node_id TEXT NOT NULL, session_id TEXT UNIQUE NOT NULL,
 state TEXT NOT NULL, context TEXT NOT NULL, cwd TEXT, error TEXT,
 generation INTEGER NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS unique_live_node_task ON exploration_tasks(node_id)
 WHERE state IN ('queued','starting','running','waiting','stopping','unverified');
CREATE TABLE IF NOT EXISTS workflow_notifications (
 notification_id TEXT PRIMARY KEY, task_id TEXT NOT NULL,
 recipient TEXT NOT NULL, kind TEXT NOT NULL, reference TEXT,
 payload TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS workflow_intents (
 intent_id TEXT PRIMARY KEY, kind TEXT NOT NULL, session_id TEXT,
 state TEXT NOT NULL, details TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS snapshot_handoffs (
 snapshot_id TEXT PRIMARY KEY, context TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS projection_cursors (
 session_id TEXT PRIMARY KEY, sequence INTEGER NOT NULL
);
"""


def migrate_schema3(path: Path):
    """Consistent backup before a transactional version upgrade; never arms execution."""
    with sqlite3.connect(path, isolation_level=None) as db:
        db.row_factory = sqlite3.Row
        version = db.execute("PRAGMA user_version").fetchone()[0]
        if version == 3:
            return
        if version != 2:
            raise ValidationError(f"Expected schema 2, found {version}")
        backup = path.parent / "schema-2-backup.sqlite3"
        if not backup.exists():
            temporary = backup.with_suffix(".tmp")
            with sqlite3.connect(temporary) as target:
                db.backup(target)
            temporary.replace(backup)
        db.execute("BEGIN IMMEDIATE")
        try:
            if db.execute("PRAGMA user_version").fetchone()[0] == 3:
                db.rollback()
                return
            for statement in DDL.split(";"):
                if statement.strip():
                    db.execute(statement)
            associations = (
                list(db.execute("SELECT * FROM associations ORDER BY started_at"))
                if db.execute("SELECT 1 FROM sqlite_master WHERE name='associations'").fetchone()
                else []
            )
            first = associations[0]["session_id"] if associations else None
            # Current control is deliberately retained; runtime activation is revoked.
            db.execute("INSERT INTO workflow_project VALUES(1,?,'cold',0,?)", (first, now()))
            seen = set()
            for row in associations:
                sid = row["session_id"]
                if sid in seen:
                    continue
                seen.add(sid)
                latest = [a for a in associations if a["session_id"] == sid][-1]
                attempt = db.execute(
                    "SELECT a.* FROM attempts a JOIN associations s USING(association_id) "
                    "WHERE s.session_id=? ORDER BY a.started_at DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                role = (
                    "main"
                    if sid == first
                    else "exploration"
                    if attempt and attempt["role"] == "branch"
                    else "legacy"
                )
                db.execute(
                    "INSERT INTO workflow_sessions(session_id,host_id,role,node_id,pause_reason,detached,created_at) VALUES(?,?,?,?,?,?,?)",
                    (
                        sid,
                        row["host_id"],
                        role,
                        attempt["node_id"] if attempt else None,
                        "legacy_history" if role == "exploration" else "cold",
                        int(latest["ended_at"] is not None),
                        now(),
                    ),
                )
                goal = db.execute(
                    "SELECT g.* FROM owned_goals g JOIN associations a USING(association_id) WHERE a.session_id=? ORDER BY g.updated_at DESC LIMIT 1",
                    (sid,),
                ).fetchone()
                if goal:
                    db.execute(
                        "UPDATE workflow_sessions SET goal_id=?,goal_revision=? WHERE session_id=?",
                        (goal["goal_id"], goal["revision"], sid),
                    )
            db.execute("PRAGMA user_version=3")
            db.commit()
        except BaseException:
            db.rollback()
            raise


class WorkflowStore:
    @staticmethod
    def notify_in_transaction(db, session_id, fields):
        task = db.execute(
            "SELECT * FROM exploration_tasks WHERE session_id=?", (session_id,)
        ).fetchone()
        if not task:
            return {"ignored": True}
        nid = (
            "notice-"
            + hashlib.sha256((task["task_id"] + ":" + fields["key"]).encode()).hexdigest()[:24]
        )
        body = {"task_id": task["task_id"], "node_id": task["node_id"], **fields}
        db.execute(
            "INSERT OR IGNORE INTO workflow_notifications VALUES(?,?,?,?,?,?,?,?)",
            (
                nid,
                task["task_id"],
                task["parent_session_id"],
                fields["kind"],
                fields.get("reference"),
                encoded(body),
                "pending",
                now(),
            ),
        )
        return {"notification_id": nid}

    def workflow_view(self, db, session_id=None):
        project = db.execute("SELECT * FROM workflow_project WHERE id=1").fetchone()
        sessions = []
        for row in db.execute("SELECT * FROM workflow_sessions ORDER BY created_at"):
            value = dict(row)
            value["waiting"] = json.loads(value["waiting"])
            value["context"] = json.loads(value["context"])
            sessions.append(value)
        tasks = []
        for row in db.execute("SELECT * FROM exploration_tasks ORDER BY created_at,task_id"):
            value = dict(row)
            value["context"] = json.loads(value["context"])
            tasks.append(value)
        notifications = []
        for row in db.execute("SELECT * FROM workflow_notifications ORDER BY created_at"):
            value = dict(row)
            value["payload"] = json.loads(value["payload"])
            notifications.append(value)
        return {
            "run": dict(project) if project else None,
            "sessions": sessions,
            "tasks": tasks,
            "intents": [
                {**dict(r), "details": json.loads(r["details"])}
                for r in db.execute("SELECT * FROM workflow_intents")
            ],
            "notifications": notifications,
            "session": next((s for s in sessions if s["session_id"] == session_id), None),
        }

    def workflow(self, host_id, session_id, action, fields, operation_id):
        payload = {"host_id": host_id, "session_id": session_id, "action": action, "fields": fields}

        def work(db):
            view = self.workflow_view(db, session_id)
            current = view["session"]
            at = now()
            if action == "register":
                role = fields.get(
                    "role",
                    "main" if not view["run"] or not view["run"]["main_session_id"] else "legacy",
                )
                if role not in {"main", "exploration", "discussion", "handoff", "specialist", "legacy"}:
                    raise ValidationError("Invalid session role")
                if current:
                    db.execute(
                        "UPDATE workflow_sessions SET detached=0,cwd=COALESCE(?,cwd) WHERE session_id=?",
                        (fields.get("cwd"), session_id),
                    )
                else:
                    db.execute(
                        "INSERT INTO workflow_sessions(session_id,host_id,role,node_id,cwd,context,created_at) VALUES(?,?,?,?,?,?,?)",
                        (
                            session_id,
                            host_id,
                            role,
                            fields.get("node_id"),
                            fields.get("cwd"),
                            encoded(fields.get("context", {})),
                            at,
                        ),
                    )
                if not view["run"]:
                    db.execute(
                        "INSERT INTO workflow_project VALUES(1,?,'manual',0,?)",
                        (session_id if role == "main" else None, at),
                    )
                elif role == "main":
                    db.execute(
                        "UPDATE workflow_project SET main_session_id=? WHERE id=1", (session_id,)
                    )
            elif action == "run":
                state = fields["state"]
                if state not in {
                    "manual",
                    "running",
                    "paused",
                    "stopping",
                    "stopped",
                    "complete",
                    "cold",
                    "unverified",
                }:
                    raise ValidationError("Invalid run state")
                generation = view["run"]["generation"] + (1 if fields.get("new_generation") else 0)
                db.execute(
                    "UPDATE workflow_project SET state=?,generation=?,updated_at=? WHERE id=1",
                    (state, generation, at),
                )
                control = (
                    "auto"
                    if state == "running"
                    else "stopped"
                    if state == "stopped"
                    else "manual"
                    if state == "manual"
                    else "paused"
                )
                db.execute("UPDATE project SET control=?", (control,))
                if state == "stopping":
                    db.execute(
                        "UPDATE exploration_tasks SET state='cancelled',updated_at=? WHERE state='queued'",
                        (at,),
                    )
            elif action == "session":
                if not current:
                    raise NotFoundError("Session role is missing")
                allowed = {"pause_reason", "waiting", "goal_id", "goal_revision", "detached", "cwd"}
                for key, value in fields.items():
                    if key not in allowed:
                        raise ValidationError(f"Unsupported session field {key}")
                    db.execute(
                        f"UPDATE workflow_sessions SET {key}=? WHERE session_id=?",
                        (encoded(value) if key == "waiting" else value, session_id),
                    )
            elif action == "task":
                if (
                    not current
                    or current["role"] not in {"main", "exploration"}
                    or current["detached"]
                ):
                    raise ConflictError("Only research agents may dispatch")
                if view["run"]["state"] != "running":
                    raise ConflictError("Autonomous research is not running")
                node = next(
                    (
                        dict(n)
                        for n in db.execute(
                            "SELECT * FROM nodes WHERE node_id=?", (fields["node_id"],)
                        )
                    ),
                    None,
                )
                if not node or node["status"] == "closed":
                    raise ConflictError("Unknown or closed node")
                if current["node_id"] == node["node_id"]:
                    raise ConflictError("Cannot dispatch the currently executing node to itself")
                live = db.execute(
                    "SELECT * FROM exploration_tasks WHERE node_id=? AND state IN ('queued','starting','running','waiting','stopping','unverified')",
                    (node["node_id"],),
                ).fetchone()
                if live:
                    return dict(live)
                task_id = "T-" + hashlib.sha256(operation_id.encode()).hexdigest()[:20]
                context = {**node, "inputs": json.loads(node["inputs"])}
                sid = "research-" + str(uuid4())
                db.execute(
                    "INSERT INTO exploration_tasks VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        task_id,
                        operation_id,
                        session_id,
                        node["node_id"],
                        sid,
                        "queued",
                        encoded(context),
                        None,
                        None,
                        view["run"]["generation"],
                        at,
                        at,
                    ),
                )
                return {
                    **dict(
                        db.execute(
                            "SELECT * FROM exploration_tasks WHERE task_id=?", (task_id,)
                        ).fetchone()
                    ),
                    "context": context,
                }
            elif action == "task_state":
                task = db.execute(
                    "SELECT * FROM exploration_tasks WHERE task_id=?", (fields["task_id"],)
                ).fetchone()
                if not task:
                    raise NotFoundError("Unknown task")
                allowed = {
                    "queued",
                    "starting",
                    "running",
                    "waiting",
                    "finished",
                    "failed",
                    "cancelled",
                    "stopping",
                    "unverified",
                }
                if fields["state"] not in allowed:
                    raise ValidationError("Invalid task state")
                db.execute(
                    "UPDATE exploration_tasks SET state=?,cwd=COALESCE(?,cwd),error=?,updated_at=? WHERE task_id=?",
                    (
                        fields["state"],
                        fields.get("cwd"),
                        fields.get("error"),
                        at,
                        fields["task_id"],
                    ),
                )
            elif action == "notify":
                notice = self.notify_in_transaction(db, session_id, fields)
                if notice.get("ignored"):
                    return notice
            elif action == "notification_state":
                if fields["state"] not in {"delivered", "claimed", "discarded"}:
                    raise ValidationError("Invalid notification state")
                db.execute(
                    "UPDATE workflow_notifications SET state=? WHERE notification_id=? AND state!='claimed'",
                    (fields["state"], fields["notification_id"]),
                )
            elif action == "intent":
                db.execute(
                    "INSERT INTO workflow_intents VALUES(?,?,?,?,?,?) ON CONFLICT(intent_id) DO UPDATE SET state=excluded.state,details=excluded.details,updated_at=excluded.updated_at",
                    (
                        fields["intent_id"],
                        fields["kind"],
                        session_id,
                        fields["state"],
                        encoded(fields.get("details", {})),
                        at,
                    ),
                )
            elif action == "cold":
                db.execute(
                    "UPDATE usage_observations SET details=? WHERE amount IS NULL AND json_extract(details,'$.phase')='started'",
                    (encoded({"phase": "missing", "reason": "host-restart"}),),
                )
                db.execute(
                    "UPDATE workflow_project SET state='cold',updated_at=? WHERE state NOT IN ('manual','stopped','complete')",
                    (at,),
                )
                db.execute(
                    "UPDATE workflow_sessions SET pause_reason='cold' WHERE role IN ('main','exploration') AND detached=0 AND (pause_reason IS NULL OR pause_reason IN ('wait','project','capacity'))"
                )
            else:
                raise ValidationError(f"Unknown workflow action {action}")
            self._event(db, "workflow." + action, {"session_id": session_id, **fields})
            return self.workflow_view(db, session_id)

        return self._mutate("workflow", payload, operation_id, work)

    def workflow_usage(self, db):
        rows = db.execute(
            "SELECT u.amount,u.completeness,u.details,COALESCE(w.role,'legacy') role FROM usage_observations u LEFT JOIN associations a USING(association_id) LEFT JOIN workflow_sessions w ON a.session_id=w.session_id"
        )
        result = {"actual": 0, "estimated": 0, "in_progress": 0, "missing": 0, "discussion": 0}
        for row in rows:
            if row["amount"] is not None:
                result["estimated" if row["completeness"] == "estimated" else "actual"] += row[
                    "amount"
                ]
                if row["role"] == "discussion":
                    result["discussion"] += row["amount"]
            elif json.loads(row["details"]).get("phase") == "started":
                result["in_progress"] += 1
            else:
                result["missing"] += 1
        return result
