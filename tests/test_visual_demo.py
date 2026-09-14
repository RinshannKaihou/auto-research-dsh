"""The viewer sample is a legal project with clear synthetic provenance."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

from auto_research.artifacts import ArtifactStore
from auto_research.runtime import check
from auto_research.store import Store


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_visual_demo.py"
SPEC = importlib.util.spec_from_file_location("create_visual_demo", SCRIPT)
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


def test_demo_is_legally_archived_with_branches_revision_and_partial_work(tmp_path):
    root = DEMO.create_demo(tmp_path / "demo")
    store, artifacts = Store(root), ArtifactStore(root)
    state = store.snapshot()
    assert check(root)["ok"]
    assert state["project"]["config"]["demo"] is True
    assert "不代表真实研究" in state["project"]["config"]["demo_notice"]
    assert "演示" in state["project"]["goal"]
    assert state["project"]["config"]["control"] == "paused"
    assert state["budget"]["total"] == 0
    assert state["budget"]["spent"] == 0
    assert {q["id"] for q in state["agenda"]["questions"]} == {
        "Q-001",
        "Q-002",
        "Q-003",
    }

    nodes = {node["id"]: node for node in state["nodes"]}
    assert len(nodes) == 8
    assert sum(node["status"] == "closed" for node in nodes.values()) == 6
    assert sum(node["status"] == "proposed" for node in nodes.values()) == 1
    assert sum(node["status"] == "open" for node in nodes.values()) == 1
    assert nodes["X-002"]["result"]["findings"] == []
    assert nodes["X-002"]["result"]["products"][0]["status"] == "partial"
    assert nodes["X-002"]["inputs"][0]["ref"] == nodes["X-003"]["inputs"][0]["ref"]
    assert nodes["X-002"]["question"] != nodes["X-003"]["question"]
    merged_from = {entry["ref"].split("/")[0] for entry in nodes["X-006"]["inputs"]}
    assert {"X-004", "X-005"} <= merged_from

    for node in nodes.values():
        assert node["why_now"] and node["plan"]
        for source in node["inputs"]:
            assert source["use"]
            assert store.resolve(source["ref"])["node_id"] in nodes
        for product in (node["result"] or {}).get("products", []):
            path = artifacts.verify(product)
            assert len(product["version"]) == 64
            assert "演示材料" in path.read_text()
        for finding in (node["result"] or {}).get("findings", []):
            assert finding["conditions"] and finding["evidence"]
            for ref in finding["evidence"]:
                store.resolve(ref)

    revision = nodes["X-005"]["result"]["findings"][0]
    assert revision["revises"] == "X-003/result#tentative-explanation"
    original = store.resolve(revision["revises"])["item"]
    assert "待检验解释" in original["text"]
    observation = store.resolve("X-003/result#weak-observation")["item"]
    assert "-3/4" in observation["text"]
    assert original["ref"] in {finding["ref"] for finding in nodes["X-003"]["result"]["findings"]}
    assert "superseded" not in original

    for attempt in state["attempts"]:
        assert attempt["demo"] is True and attempt["not_executed"] is True
        assert "pid" not in attempt and "session_id" not in attempt
        assert attempt["estimate"] == 0
        if attempt["state"] == "completed":
            assert attempt["cost"] == 0 and attempt["cost_kind"] == "estimated"
    open_attempt = next(attempt for attempt in state["attempts"] if attempt["node_id"] == "X-008")
    assert open_attempt["state"] == "reserved"
    assert not open_attempt.get("dispatch_prepared")
    assert not (root / ".research" / "jobs").exists()
    exported = json.loads((root / ".research" / "views" / "index.json").read_text())
    assert len(exported["nodes"]) == 8
    assert "演示" in exported["project"]["goal"]


@pytest.mark.parametrize(
    "existing_kind", ["empty-directory", "populated-directory", "file", "dangling-link"],
)
def test_demo_refuses_every_existing_target_without_overwriting(tmp_path, existing_kind):
    target = tmp_path / "existing"
    if existing_kind == "file":
        target.write_text("keep this file")
    elif existing_kind == "dangling-link":
        target.symlink_to(tmp_path / "must-not-be-created")
    else:
        target.mkdir()
        if existing_kind == "populated-directory":
            (target / "keep.txt").write_text("keep this project")
    with pytest.raises(FileExistsError):
        DEMO.create_demo(target)
    if existing_kind == "file":
        assert target.read_text() == "keep this file"
    elif existing_kind == "dangling-link":
        assert target.is_symlink() and not (tmp_path / "must-not-be-created").exists()
    elif existing_kind == "populated-directory":
        assert (target / "keep.txt").read_text() == "keep this project"
        assert not (target / ".research").exists()
    else:
        assert list(target.iterdir()) == []


def test_demo_script_prints_demo_notice_and_view_commands(tmp_path):
    target = tmp_path / "演示 project"
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "不代表真实研究记录" in completed.stdout
    assert "未调用模型" in completed.stdout
    assert "ari -p" in completed.stdout
    assert " view" in completed.stdout and " export" in completed.stdout
    assert Store(target).project()["config"]["demo"] is True
    refused = subprocess.run(
        [sys.executable, str(SCRIPT), "--project", str(target)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode == 2
    assert "不会覆盖" in refused.stderr
    assert len(Store(target).list_nodes()) == 8
