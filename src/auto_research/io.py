"""Durable JSON and process identity helpers, shared with the supervisor."""

import json
import os
import subprocess
import tempfile
from pathlib import Path


class ProcessInspectionError(RuntimeError):
    """The host cannot establish process identity; this is not proof of termination."""


def atomic_json(path: Path, value: object) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def process_identity(pid: int) -> str | None:
    try:
        result = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart=", "-o", "stat=", "-o", "args="],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        parts = result.stdout.strip().split(maxsplit=6)
        if result.returncode and result.stderr.strip():
            raise ProcessInspectionError(
                "Process inspection is unavailable: " + result.stderr.strip()
            )
        if result.returncode or not result.stdout.strip():
            return None
        if len(parts) < 7:
            raise ProcessInspectionError("Unrecognized process identity response")
        if parts[5].startswith("Z"):
            return None
        # An exec (including macOS's Python launcher) can change argv without
        # changing process ownership. Use birth time and PID, never the image name.
        return str(pid) + " " + " ".join(parts[:5])
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProcessInspectionError(
            "Process inspection is unavailable; no execution was confirmed stopped"
        ) from error
