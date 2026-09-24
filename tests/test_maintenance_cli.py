import json

import pytest

from auto_research.maintenance_cli import execute, main, parser, validate_project
from auto_research.native_store import NativeStore


def test_default_cli_does_not_expose_legacy_runtime_commands():
    with pytest.raises(SystemExit):
        parser().parse_args(["start"])


def test_validate_and_export_schema_five_project(tmp_path):
    root = tmp_path / "project?one"
    store = NativeStore(root)
    store.initialize("offline export", "initialize")

    report = validate_project(root)
    assert report["ok"] is True
    assert report["schema_version"] == 9

    output = tmp_path / "state.json"
    args = parser().parse_args(["-p", str(root), "export", "--output", str(output)])
    assert execute(args) == {"output": str(output), "schema_version": 9}
    assert json.loads(output.read_text())["project"]["goal"] == "offline export"


def test_invalid_project_exits_without_starting_execution(tmp_path, capsys):
    with pytest.raises(SystemExit) as raised:
        main(["-p", str(tmp_path / "missing"), "validate"])
    assert raised.value.code == 2
    assert "does not exist" in capsys.readouterr().err
