"""Local GUI project registry and serialized, background runtime controls."""

from collections import deque
from dataclasses import dataclass, field
import copy
import hashlib
import json
import math
import os
from pathlib import Path
from datetime import datetime
import threading
import time

from .errors import NotFoundError, ValidationError
from .runtime import ACTIVE, Runtime, coordinator_lock, validate_config
from .store import Store


def execution_activity(root: Path, attempts: list) -> list:
    """Expose bounded lifecycle metadata, never raw logs, commands or credentials."""
    active = [a for a in attempts if a["state"] in ACTIVE]
    selected = active or attempts[-1:]
    output = []
    for attempt in selected:
        item = {key: attempt.get(key) for key in ("id", "role", "node_id", "state")}
        job = root / ".research" / "jobs" / attempt["id"]
        try:
            with (job / "stdout.log").open("rb") as stream:
                first = stream.read(4096)
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - 65536))
                tail = stream.read(65536)
            events = []
            for line in (first + b"\n" + tail).splitlines():
                try:
                    event = json.loads(line)
                    if isinstance(event, dict):
                        events.append(event)
                except (ValueError, UnicodeDecodeError):
                    pass
            for event in events:
                if event.get("type") == "ready":
                    for key in ("provider", "model"):
                        if isinstance(event.get(key), str):
                            item[key] = event[key][:160]
            valid = [
                e
                for e in events
                if e.get("type")
                in {
                    "ready",
                    "turn_start",
                    "turn_end",
                    "tool_started",
                    "tool_finished",
                    "tool_denied_or_failed",
                    "completed",
                    "failed",
                }
            ]
            if valid:
                event = valid[-1]
                item["event"] = {"type": event["type"], "at": event.get("t")}
                if event.get("name") in {"read", "write", "run", "submit_result"}:
                    item["event"]["tool"] = event["name"]
                if type(event.get("exit_code")) is int:
                    item["event"]["exit_code"] = event["exit_code"]
        except (OSError, ValueError):
            pass
        try:
            started = datetime.fromisoformat(attempt["created_at"]).timestamp()
            ended = (
                time.time()
                if attempt["state"] in ACTIVE
                else datetime.fromisoformat(attempt["updated_at"]).timestamp()
            )
            item["elapsed_seconds"] = max(0, int(ended - started))
        except (KeyError, TypeError, ValueError):
            pass
        output.append(item)
    return output


@dataclass
class _Project:
    path: Path
    lock: threading.RLock = field(default_factory=threading.RLock)
    commands: deque = field(default_factory=deque)
    thread: threading.Thread | None = None
    owns_controller: bool = False
    executing: str | None = None
    maintenance: str | None = None
    error: str | None = None


class Workbench:
    """One GUI session; project state remains in each project's existing Store.

    Runtime methods run on one background thread per project. Its control hook
    applies queued intents before the next tick; an already begun tick may finish.
    A GUI session never adopts another process's coordinator lock.
    """

    PUBLIC_DEFAULTS = {"budget": 100000, "concurrency": 2, "estimate": 5000, "timeout": 900}
    CREATE_FIELDS = {*PUBLIC_DEFAULTS, "name", "goal", "path", "resources", "dsh_config"}
    UPDATE_FIELDS = {*PUBLIC_DEFAULTS, "resources", "notes"}
    ACTIONS = {"start", "resume", "pause", "stop", "reconcile"}

    def __init__(self, workspace: Path, *, runtime_factory=Runtime, defaults: dict | None = None):
        self.workspace = Path(workspace).expanduser().resolve()
        if self.workspace.exists() and not self.workspace.is_dir():
            raise ValidationError("工作区必须是目录")
        self.runtime_factory = runtime_factory
        supplied = copy.deepcopy(defaults or {})
        if not isinstance(supplied, dict) or set(supplied) - {
            *self.PUBLIC_DEFAULTS,
            "backend_command",
        }:
            raise ValidationError("Unsupported workbench defaults")
        self.defaults = {
            **self.PUBLIC_DEFAULTS,
            **{k: v for k, v in supplied.items() if k in self.PUBLIC_DEFAULTS},
        }
        self._settings(self.defaults)
        self._backend_command = supplied.get("backend_command")
        if self._backend_command is not None and (
            not isinstance(self._backend_command, list)
            or not self._backend_command
            or not all(isinstance(arg, str) and arg for arg in self._backend_command)
        ):
            raise ValidationError("backend_command defaults must be a nonempty argv array")
        self._projects: dict[str, _Project] = {}
        self._lock = threading.RLock()
        self._closed = False

    @staticmethod
    def _id(path: Path) -> str:
        return hashlib.sha256(str(path).encode()).hexdigest()[:24]

    @staticmethod
    def _payload(payload: dict, allowed: set) -> dict:
        if not isinstance(payload, dict):
            raise ValidationError("Request must be an object")
        if set(payload) - allowed:
            raise ValidationError(
                "Unsupported fields: " + ", ".join(sorted(set(payload) - allowed))
            )
        return copy.deepcopy(payload)

    @staticmethod
    def _text(value, label: str) -> str:
        if not isinstance(value, str) or not value.strip() or "\x00" in value:
            raise ValidationError(f"请填写{label}")
        return value.strip()

    @staticmethod
    def _readable(value, *, file_only=False) -> str:
        if not isinstance(value, (str, Path)) or not str(value):
            raise ValidationError("请填写资源路径")
        try:
            path = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValidationError(f"资源不存在：{value}") from error
        if not (path.is_file() or (path.is_dir() and not file_only)):
            raise ValidationError(f"资源必须是可读{'文件' if file_only else '文件或目录'}：{path}")
        if not os.access(path, os.R_OK | (os.X_OK if path.is_dir() else 0)):
            raise ValidationError(f"无法读取资源：{path}")
        return str(path)

    @classmethod
    def _settings(cls, values: dict) -> dict:
        updates = {}
        if "budget" in values:
            value = values["budget"]
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValidationError("budget must be finite and nonnegative")
        for public, internal in (
            ("concurrency", "concurrency"),
            ("estimate", "worker_estimate"),
            ("timeout", "timeout_s"),
        ):
            if public in values:
                updates[internal] = values[public]
        if "estimate" in values:
            updates["coordinator_estimate"] = values["estimate"]
        validate_config(updates)
        if "resources" in values:
            resources = values["resources"]
            if not isinstance(resources, list):
                raise ValidationError("resources must be a list of paths")
            updates["resources"] = list(dict.fromkeys(cls._readable(path) for path in resources))
        return updates

    @staticmethod
    def _resource_boundary(project: Path, resources: list) -> None:
        for value in resources:
            path = Path(value)
            if project.is_relative_to(path):
                raise ValidationError("资源不能包含当前研究目录或其上级目录")
            if any(
                path.is_relative_to(project / protected)
                for protected in (".research", "workspaces")
            ):
                raise ValidationError("不能将研究控制文件或工作目录挂载为资源")

    @staticmethod
    def _require_project(path: Path) -> None:
        if not path.is_dir() or not (path / ".research" / "state.sqlite3").is_file():
            raise NotFoundError("研究目录或数据库已不存在；请重新打开正确目录")

    def _ensure_open(self):
        if self._closed:
            raise ValidationError("This workbench session is closed")

    def _register(self, path: Path) -> str:
        identifier = self._id(path)
        with self._lock:
            self._projects.setdefault(identifier, _Project(path))
        return identifier

    def _entry(self, identifier: str) -> _Project:
        with self._lock:
            entry = self._projects.get(identifier)
        if entry is None:
            raise NotFoundError("Unknown research project")
        return entry

    def get_store(self, identifier: str) -> Store:
        path = self._entry(identifier).path
        self._require_project(path)
        return Store(path)

    def list_projects(self) -> list:
        if self.workspace.is_dir():
            for path in sorted(self.workspace.iterdir()):
                if (
                    not path.is_symlink()
                    and path.is_dir()
                    and (path / ".research" / "state.sqlite3").is_file()
                ):
                    self._register(path.resolve())
        with self._lock:
            identifiers = list(self._projects)
        result = []
        for identifier in identifiers:
            try:
                result.append(self.project_state(identifier))
            except Exception as error:
                entry = self._entry(identifier)
                result.append(
                    {
                        "id": identifier,
                        "path": str(entry.path),
                        "name": entry.path.name,
                        "error": str(error),
                        "controller_running": False,
                    }
                )
        return result

    def create(self, payload: dict) -> dict:
        values = self._payload(payload, self.CREATE_FIELDS)
        name = self._text(values.get("name"), "研究名称")
        if name in {".", ".."} or any(c in name for c in "/\\") or any(ord(c) < 32 for c in name):
            raise ValidationError("研究名称不能包含路径或特殊控制字符")
        goal = self._text(values.get("goal"), "研究目标")
        settings = {**self.defaults, "resources": [], **values}
        config = self._settings(settings)
        config.update(title=name, control="paused")
        if self._backend_command is not None:
            config["backend_command"] = copy.deepcopy(self._backend_command)
        if "dsh_config" in values:
            config["dsh_config"] = self._readable(values["dsh_config"], file_only=True)
        value = values.get("path", str(self.workspace / name))
        if not isinstance(value, (str, Path)) or not str(value).strip():
            raise ValidationError("请填写研究保存目录")
        target = Path(value).expanduser()
        if not target.is_absolute():
            target = self.workspace / target
        # Do not resolve a dangling final symlink into a fresh destination.
        if os.path.lexists(target):
            raise ValidationError("保存目录已存在，请选择新目录")
        self._resource_boundary(target.resolve(), config["resources"])
        with self._lock:
            self._ensure_open()
            try:
                target.mkdir(parents=True, exist_ok=False)
            except FileExistsError as error:
                raise ValidationError("保存目录已存在，请选择新目录") from error
            path = target.resolve()
            store = Store(path)
            store.initialize(goal, budget=settings["budget"], config=config)
            identifier = self._register(path)
        return self.project_state(identifier)

    def open(self, path: Path | str) -> dict:
        try:
            canonical = Path(path).expanduser().resolve(strict=True)
        except (OSError, RuntimeError, ValueError) as error:
            raise ValidationError("研究目录不存在") from error
        if not canonical.is_dir() or not (canonical / ".research" / "state.sqlite3").is_file():
            raise ValidationError("此目录不是已初始化的研究项目")
        Store(canonical).project()
        with self._lock:
            self._ensure_open()
            identifier = self._register(canonical)
        return self.project_state(identifier)

    def project_state(self, identifier: str) -> dict:
        entry = self._entry(identifier)
        state = self.get_store(identifier).snapshot()
        project = state["project"]
        config = project.get("config", {})
        with entry.lock:
            running, error = entry.owns_controller, entry.error
            pending = entry.commands[-1] if entry.commands else entry.executing or entry.maintenance
        result = {
            "id": identifier,
            "path": str(entry.path),
            "name": config.get("title") or entry.path.name,
            "goal": project["goal"],
            "control": config.get("control", "paused"),
            "idle_reason": config.get("idle_reason", ""),
            "controller_running": running,
            "active_attempts": sum(a["state"] in ACTIVE for a in state["attempts"]),
            "node_count": len(state["nodes"]),
            "activity": execution_activity(entry.path, state["attempts"]),
            "budget": state["budget"],
            "settings": {
                "budget": project["budget"],
                "concurrency": config.get("concurrency", 2),
                "estimate": config.get("worker_estimate", 5000),
                "timeout": config.get("timeout_s", 900),
                "resources": config.get("resources", []),
            },
            "agenda": state["agenda"],
            "notes": state["notes"],
        }
        if pending:
            result["pending_action"] = pending
        if error:
            result["error"] = error
        return result

    def update(self, identifier: str, payload: dict) -> dict:
        values = self._payload(payload, self.UPDATE_FIELDS)
        updates = self._settings(values)
        if "notes" in values and not isinstance(values["notes"], str):
            raise ValidationError("notes must be text")
        entry = self._entry(identifier)
        self._resource_boundary(entry.path, updates.get("resources", []))
        with self._lock, entry.lock:
            self._ensure_open()
            store = self.get_store(identifier)
            previous = store.project()["config"].get("steer_revision", 0)
            previous = previous if isinstance(previous, int) else 0
            updates["steer_revision"] = max(time.time_ns(), previous + 1)
            # Notes validates all explicit references transactionally before settings change.
            if "notes" in values:
                store.notes(values["notes"])
            store.configure(budget=values.get("budget"), **updates)
            entry.error = None
        return self.project_state(identifier)

    def action(self, identifier: str, action: str) -> dict:
        if action not in self.ACTIONS:
            raise ValidationError("Unknown project action")
        entry = self._entry(identifier)
        with self._lock, entry.lock:
            self._ensure_open()
            self._require_project(entry.path)
            entry.error = None
            # Pausing an external CLI only updates its durable control flag.
            if action == "pause" and entry.thread is None:
                self.get_store(identifier).configure(
                    control="paused", idle_reason="Paused from GUI"
                )
            else:
                latest = entry.commands[-1] if entry.commands else entry.executing
                already_running = (
                    entry.thread is not None
                    and entry.owns_controller
                    and self.get_store(identifier).project()["config"].get("control") == "running"
                )
                if not (
                    (
                        action in {"start", "resume"}
                        and (latest in {"start", "resume"} or (latest is None and already_running))
                    )
                    or latest == action
                ):
                    entry.commands.append(action)
                if entry.thread is None:
                    entry.thread = threading.Thread(
                        target=self._run,
                        args=(entry,),
                        daemon=True,
                        name=f"ari-gui-{identifier[:8]}",
                    )
                    entry.thread.start()
        return self.project_state(identifier)

    @staticmethod
    def _apply(runtime, action: str) -> None:
        if action in {"start", "resume"}:
            runtime.store.configure(control="running", idle_reason="")
        elif action == "pause":
            runtime.store.configure(
                control="paused", idle_reason="Paused from GUI; existing work retained"
            )
        elif action == "stop":
            runtime.stop()
        elif action == "reconcile":
            runtime.reconcile()

    def _commands(self, entry: _Project, runtime, *, controller: bool) -> None:
        if controller:
            with entry.lock:
                entry.owns_controller = True
        while True:
            with entry.lock:
                if not entry.commands or (
                    not controller and entry.commands[0] in {"start", "resume"}
                ):
                    return
                action = entry.commands.popleft()
                entry.executing = action
            try:
                self._apply(runtime, action)
            finally:
                with entry.lock:
                    entry.executing = None

    def _run(self, entry: _Project) -> None:
        runtime = None
        try:
            self._require_project(entry.path)
            runtime = self.runtime_factory(entry.path)
            while True:
                with entry.lock:
                    if not entry.commands:
                        entry.owns_controller = False
                        entry.executing = None
                        entry.maintenance = None
                        entry.thread = None
                        return
                    controller = entry.commands[0] in {"start", "resume"}
                    entry.maintenance = None if controller else entry.commands[0]
                if controller:
                    runtime.start(
                        control_hook=lambda current: self._commands(entry, current, controller=True)
                    )
                else:
                    # A one-off reconcile or stop must not race a CLI collector.
                    with coordinator_lock(entry.path):
                        self._commands(entry, runtime, controller=False)
                        runtime.reconcile()
                        while runtime.store.project()["config"].get("control") == "stopped" and any(
                            a["state"] in {"reserved", "running"} for a in runtime.store.attempts()
                        ):
                            with entry.lock:
                                if entry.commands and entry.commands[0] in {"start", "resume"}:
                                    break
                            self._commands(entry, runtime, controller=False)
                            time.sleep(0.1)
                            runtime.reconcile()
                with entry.lock:
                    entry.owns_controller = False
                    entry.maintenance = None
                # Commands arriving after the final hook remain queued. In particular,
                # resume during draining is neither lost nor a second controller.
        except Exception as error:
            with entry.lock:
                owned = entry.owns_controller
                entry.error = (
                    "此研究已有其他协调器运行；请先暂停或退出对应 CLI，再重试"
                    if "already has a running coordinator" in str(error)
                    else str(error) or type(error).__name__
                )
                entry.commands.clear()
                if owned and runtime is not None:
                    try:
                        runtime.store.configure(
                            control="paused", idle_reason=f"GUI controller error: {error}"
                        )
                    except Exception:
                        pass
                entry.owns_controller = False
                entry.executing = None
                entry.maintenance = None
                entry.thread = None

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            for entry in self._projects.values():
                with entry.lock:
                    if entry.thread is not None:
                        # This is a request, not an assertion that workers have stopped.
                        entry.commands.append("pause")
