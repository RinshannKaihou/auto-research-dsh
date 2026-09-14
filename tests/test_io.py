import subprocess
from types import SimpleNamespace

import pytest

from auto_research.io import (
    ProcessInspectionError,
    atomic_json,
    atomic_text,
    process_identity,
    read_json,
)


def test_atomic_updates_preserve_previous_document_on_invalid_json(tmp_path):
    path = tmp_path / "document.json"
    atomic_json(path, {"version": 1})
    with pytest.raises(ValueError):
        atomic_json(path, {"number": float("nan")})
    assert read_json(path) == {"version": 1}
    assert list(tmp_path.iterdir()) == [path]
    atomic_text(path, "unfinished JSON")
    assert read_json(path, "fallback") == "fallback"
    assert read_json(tmp_path / "absent") is None


def test_exec_changes_image_without_changing_identity(monkeypatch):
    output = iter(
        [
            "Sun Sep 13 01:02:03 2026 S /usr/bin/python3 script.py",
            "Sun Sep 13 01:02:03 2026 S /Library/Python.app/Python script.py",
        ]
    )
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout=next(output), stderr=""),
    )
    assert process_identity(123) == process_identity(123)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(returncode=1, stdout="", stderr="Access denied"),
        SimpleNamespace(returncode=0, stdout="unexpected format", stderr=""),
        PermissionError("ps blocked"),
        subprocess.TimeoutExpired("ps", 3),
    ],
)
def test_process_inspection_failure_is_not_death(monkeypatch, response):
    def run(*args, **kwargs):
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(ProcessInspectionError):
        process_identity(123)


@pytest.mark.parametrize(
    "response",
    [
        SimpleNamespace(returncode=1, stdout="", stderr=""),
        SimpleNamespace(returncode=0, stdout="Sun Sep 13 01:02:03 2026 Z zombie", stderr=""),
    ],
)
def test_only_observed_termination_returns_none(monkeypatch, response):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: response)
    assert process_identity(123) is None
