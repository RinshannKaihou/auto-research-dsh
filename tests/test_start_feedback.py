import json
from pathlib import Path

from auto_research.prompts import make_prompt
from auto_research.runtime import Runtime
from auto_research.store import Store
from auto_research.workbench import Workbench, execution_activity


def test_resources_reach_both_role_prompts_without_backend_credentials(tmp_path):
    materials = tmp_path / "材料"
    materials.mkdir()
    store = Store(tmp_path / "research")
    store.initialize(
        "Read my PDF",
        budget=10000,
        config={
            "resources": [str(materials)],
            "dsh_config": "/private/settings.yaml",
            "backend_command": ["SECRET_TEST_SENTINEL"],
        },
    )
    runtime = Runtime(store.root)
    for role in ("worker", "coordinator"):
        context = runtime.context(None)
        assert context["resources"] == [str(materials)]
        prompt = make_prompt(role, context)
        assert str(materials) in prompt
        assert "PDF text" in prompt
        assert "SECRET_TEST_SENTINEL" not in prompt
        assert "/private/settings.yaml" not in prompt


def test_activity_exists_before_first_node_and_exposes_only_safe_metadata(tmp_path):
    wb = Workbench(tmp_path)
    project = wb.create({"name": "paper", "goal": "Read paper"})
    store = wb.get_store(project["id"])
    attempt = store.reserve(role="coordinator", estimate=5000)
    folder = store.root / ".research/jobs" / attempt["id"]
    folder.mkdir(parents=True)
    log = folder / "stdout.log"
    events = [
        {"type": "ready", "provider": "test", "model": "research-model", "api_key": "SECRET"},
        {
            "type": "tool_started",
            "name": "read",
            "t": "2026-09-14T01:02:03Z",
            "arguments": {"path": "SECRET"},
        },
    ]
    log.write_text("\n".join(json.dumps(e) for e in events) + "\n{partial")
    result = wb.project_state(project["id"])
    assert result["node_count"] == 0
    active = result["activity"][0]
    assert active["role"] == "coordinator"
    assert active["provider"] == "test"
    assert active["event"]["tool"] == "read"
    assert "SECRET" not in json.dumps(result)
    assert active["elapsed_seconds"] >= 0
    # Large and partially written logs remain bounded; unknown events/args stay private.
    with log.open("a") as stream:
        stream.write("\n" + ("x" * 70000) + "\n")
        stream.write(json.dumps({"type": "tool_finished", "name": "run", "exit_code": 1}) + "\n")
    final = wb.project_state(project["id"])["activity"][0]
    assert final["model"] == "research-model"
    assert final["event"]["exit_code"] == 1
    log.unlink()
    assert "event" not in wb.project_state(project["id"])["activity"][0]
    wb.close()


def test_finished_activity_does_not_keep_increasing_and_invalid_date_is_tolerated(tmp_path):
    attempts = [
        {
            "id": "A-001",
            "role": "coordinator",
            "node_id": None,
            "state": "interrupted",
            "created_at": "2026-09-14T01:00:00+00:00",
            "updated_at": "2026-09-14T01:00:05+00:00",
        }
    ]
    assert execution_activity(tmp_path, attempts)[0]["elapsed_seconds"] == 5
    attempts[0]["created_at"] = "invalid"
    assert "elapsed_seconds" not in execution_activity(tmp_path, attempts)[0]
    assert execution_activity(tmp_path, []) == []
