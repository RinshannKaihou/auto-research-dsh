"""Operator controls run at the scheduler boundary, not in HTTP worker threads."""

from types import SimpleNamespace
import subprocess

import pytest

from auto_research import gui
from auto_research.runtime import Runtime, coordinator_lock
from auto_research.store import Store


def test_control_hook_can_pause_before_any_dispatch_while_owning_lock(tmp_path):
    store = Store(tmp_path)
    store.initialize("Do not dispatch before operator control is applied", budget=10000)
    store.propose(
        {"question": "Q-001", "why_now": "Continue a derivation", "plan": "Save partial work"}
    )
    seen = []

    def control(runtime):
        with pytest.raises(RuntimeError, match="coordinator"):
            with coordinator_lock(tmp_path):
                pass
        seen.append(runtime)
        runtime.store.configure(control="paused")

    runtime = Runtime(tmp_path)
    result = runtime.start(control_hook=control)
    assert seen == [runtime]
    assert result["project"]["config"]["control"] == "paused"
    assert result["attempts"] == []
    assert result["nodes"][0]["status"] == "proposed"


def test_native_picker_only_executes_fixed_choices(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "darwin")
    calls = []

    def run(command, **options):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="/tmp/中文材料\n", stderr="")

    monkeypatch.setattr(gui.subprocess, "run", run)
    assert gui.pick_path("folder") == "/tmp/中文材料"
    assert "choose folder" in calls[0][2]
    assert gui.pick_path("file") == "/tmp/中文材料"
    assert "choose file" in calls[1][2]
    with pytest.raises(ValueError):
        gui.pick_path('file); do shell script "anything"')
    assert len(calls) == 2
    monkeypatch.setattr(
        gui.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="User cancelled (-128)"),
    )
    assert gui.pick_path("file") is None
    monkeypatch.setattr(
        gui.subprocess,
        "run",
        lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="PRIVATE_BACKEND_DETAIL"),
    )
    with pytest.raises(ValueError, match="选择窗口"):
        gui.pick_path("file")
    monkeypatch.setattr(gui.sys, "platform", "linux")
    with pytest.raises(ValueError, match="本地路径"):
        gui.pick_path("file")


def test_picker_timeout_is_actionable(monkeypatch):
    monkeypatch.setattr(gui.sys, "platform", "darwin")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("osascript", 180)

    monkeypatch.setattr(gui.subprocess, "run", timeout)
    with pytest.raises(ValueError, match="超时"):
        gui.pick_path("folder")


@pytest.mark.parametrize("launch", ["browser", "manual", "browser_failure", "server_failure"])
def test_server_exit_always_closes_server_and_requests_pause(monkeypatch, tmp_path, capsys, launch):
    from auto_research import workbench

    events = []

    class Session:
        def __init__(self, path):
            assert path == tmp_path

        def close(self):
            events.append("pause")

    class Server:
        server_port = 8174

        def serve_forever(self, **kwargs):
            events.append("serve")
            if launch == "server_failure":
                raise RuntimeError("server failed")
            raise KeyboardInterrupt

        def server_close(self):
            events.append("close")

    def browser(url):
        assert url == "http://127.0.0.1:8174/"
        events.append("browser")
        if launch == "browser_failure":
            raise OSError("no default browser")

    monkeypatch.setattr(workbench, "Workbench", Session)
    monkeypatch.setattr(gui, "make_gui_server", lambda session, port: Server())
    monkeypatch.setattr(gui.webbrowser, "open", browser)
    if launch == "server_failure":
        with pytest.raises(RuntimeError, match="server failed"):
            gui.serve_gui(tmp_path, open_browser=False)
    else:
        gui.serve_gui(tmp_path, open_browser=launch != "manual")
    assert events[-3:] == ["serve", "close", "pause"]
    assert ("browser" in events) == (launch in {"browser", "browser_failure"})
    output = capsys.readouterr()
    assert "http://127.0.0.1:8174/" in output.out
    if launch == "browser_failure":
        assert "浏览器" in output.err
