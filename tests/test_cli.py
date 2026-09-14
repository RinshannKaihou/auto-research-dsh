import json
from pathlib import Path

import pytest

from auto_research.cli import execute, main, parser
from auto_research.store import Store


def call(root, *args):
    return execute(parser().parse_args(["-p", str(root), *args]))


def test_cli_initialization_steer_propose_read_export(tmp_path, capsys):
    root = tmp_path / "study"
    result = call(root, "init", "--goal", "Investigate an open question", "--budget", "100")
    assert result["nodes"] == []
    spec = tmp_path / "node.json"
    spec.write_text(
        json.dumps(
            {"question": "Q-001", "why_now": "Test a premise", "plan": "Write partial derivation"}
        )
    )
    node = call(root, "propose", str(spec), "--request-id", "operator-1")
    assert call(root, "propose", str(spec), "--request-id", "operator-1")["id"] == node["id"]
    assert call(root, "show", node["id"])["status"] == "proposed"
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"id": "Q-001", "text": "Revised research scope"}]))
    notes = tmp_path / "notes.md"
    notes.write_text("Plan a counterexample search.")
    call(
        root,
        "steer",
        "--questions",
        str(questions),
        "--agenda",
        str(notes),
        "--notes",
        str(notes),
        "--budget",
        "150",
        "--concurrency",
        "3",
        "--coordinator-calls",
        "4",
        "--estimate",
        "10",
    )
    assert Store(root).agenda()["version"] == 2
    assert Store(root).project()["budget"] == 150
    exported = call(root, "export")
    assert Path(exported["path"]).is_dir()
    assert Path(exported["graph"]).is_file()
    assert "Test a premise" in Path(exported["graph"]).read_text()
    assert call(root, "check")["ok"]
    assert call(root, "pause")["project"]["config"]["control"] == "paused"
    assert call(root, "stop")["project"]["config"]["control"] == "stopped"
    main(["-p", str(root), "status"])
    assert "Test a premise" in capsys.readouterr().out
    main(["-p", str(root), "--json", "status"])
    assert json.loads(capsys.readouterr().out)["project"]["budget"] == 150


@pytest.mark.parametrize(
    "command",
    [
        ["init", "--goal", "x", "--concurrency", "0"],
        ["init", "--goal", "x", "--timeout", "0"],
        ["init", "--goal", "x", "--estimate", "-1"],
    ],
)
def test_invalid_init_limits(tmp_path, command):
    with pytest.raises(ValueError):
        call(tmp_path, *command)


@pytest.mark.parametrize(
    "command",
    [
        ["steer", "--concurrency", "0"],
        ["steer", "--coordinator-calls", "-1"],
        ["steer", "--estimate", "-1"],
        ["start", "--poll", "0"],
        ["resume", "--max-cycles", "0"],
    ],
)
def test_invalid_runtime_options(tmp_path, command):
    call(tmp_path, "init", "--goal", "x")
    with pytest.raises(ValueError):
        call(tmp_path, *command)


def test_cli_settlement_requires_stopped_execution(tmp_path):
    call(tmp_path, "init", "--goal", "x")
    store = Store(tmp_path)
    node = store.propose({"question": "Q-001", "why_now": "Continue", "plan": "Explore"})
    attempt = store.reserve(node["id"], estimate=3)
    with pytest.raises(ValueError):
        call(tmp_path, "settle", attempt["id"], "--cost", "2", "--cost-kind", "actual")
    with pytest.raises(ValueError):
        call(tmp_path, "reconcile", "--attempt", attempt["id"])
    call(tmp_path, "reconcile", "--attempt", attempt["id"], "--confirm-stopped")
    call(tmp_path, "settle", attempt["id"], "--cost", "2", "--cost-kind", "estimated")
    assert store.budget()["estimated"] == 2
    call(tmp_path, "reconcile")


def test_cli_errors_are_readable(tmp_path, capsys):
    with pytest.raises(SystemExit) as error:
        main(["-p", str(tmp_path / "missing"), "status"])
    assert error.value.code == 2
    assert "initialized" in json.loads(capsys.readouterr().err)["error"]
    call(tmp_path, "init", "--goal", "x")
    with pytest.raises(SystemExit):
        main(["-p", str(tmp_path), "init", "--goal", "x"])
    assert "error" in json.loads(capsys.readouterr().err)


def test_cli_view_forwards_options_without_starting_research(tmp_path, monkeypatch, capsys):
    call(tmp_path, "init", "--goal", "A viewable research question")
    seen = []
    monkeypatch.setattr(
        "auto_research.cli.serve_view", lambda store, **options: seen.append((store.root, options)),
    )
    before = Store(tmp_path).snapshot()
    assert call(tmp_path, "view", "--port", "8173", "--no-open") is None
    assert seen == [(tmp_path, {"port": 8173, "open_browser": False})]
    main(["-p", str(tmp_path), "view"])
    assert seen[-1][1] == {"port": 0, "open_browser": True}
    assert capsys.readouterr().out == ""
    assert Store(tmp_path).snapshot() == before


def test_gui_opens_without_initializing_current_directory(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(
        "auto_research.gui.serve_gui",
        lambda workspace, **options: seen.append((workspace, options)),
    )
    main(
        [
            "-p",
            str(tmp_path / "not-a-project"),
            "gui",
            "--workspace",
            str(tmp_path / "studies"),
            "--port",
            "8174",
            "--no-open",
        ]
    )
    assert seen == [(tmp_path / "studies", {"port": 8174, "open_browser": False})]
    assert not (tmp_path / "not-a-project").exists()
    assert not (tmp_path / "studies").exists()
    assert capsys.readouterr().out == ""
