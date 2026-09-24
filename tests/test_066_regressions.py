import json
import shutil
from pathlib import Path

import pytest

from auto_research.service import NativeService


def call(service, method, operation, session="main", **fields):
    return service.handle(
        dict(
            transport_id=operation,
            operation_id=operation,
            host_id="host",
            session_id=session,
            method=method,
            **fields,
        )
    )["value"]


def project(tmp_path):
    service = NativeService(tmp_path / "registry.sqlite3")
    root = tmp_path / "project"
    call(service, "open", "open", root=str(root), goal="fixture")
    call(service, "focus", "focus", role="planner", mode="manual")
    (root / "payload.txt").write_text("frozen bytes\n", encoding="utf-8")
    publication = call(
        service,
        "publish",
        "publish",
        status="partial",
        summary="fixture",
        items=[
            {"item_id": "file", "kind": "artifact", "source_path": "payload.txt"},
            {"item_id": "metadata", "kind": "finding", "content": {"value": 7}},
        ],
    )
    return service, root, publication


def test_whole_publication_expands_once_and_preserves_declared_inputs(tmp_path):
    service, root, publication = project(tmp_path)
    publication_ref = f"pub/{publication['publication_id']}"
    item_ref = f"{publication_ref}#file"
    node = call(
        service,
        "propose",
        "node",
        question="consume publication",
        why_now="fixture",
        plan="inspect inputs",
        root_reason="fixture root",
        inputs=[publication_ref, item_ref],
    )
    validated = call(
        service, "validate_branch_inputs", "validate", node_id=node["node_id"]
    )
    assert validated["declared_inputs"] == [publication_ref, item_ref]
    assert [item["ref"] for item in validated["inputs"]] == [
        item_ref,
        f"{publication_ref}#metadata",
    ]

    prepared = call(service, "prepare_branch", "prepare", node_id=node["node_id"])
    index = json.loads((Path(prepared["workspace"]) / "research-inputs.json").read_text())
    assert index["declared_inputs"] == [publication_ref, item_ref]
    materialized = next(item for item in index["inputs"] if item["ref"] == item_ref)
    assert (Path(prepared["workspace"]) / materialized["path"]).read_bytes() == b"frozen bytes\n"
    assert len(list((Path(prepared["workspace"]) / "inputs" / "input").iterdir())) == 1


def test_input_errors_distinguish_missing_unsupported_and_missing_frozen_object(tmp_path):
    service, root, publication = project(tmp_path)
    note = call(service, "note", "note", body="not an input material")
    unsupported = call(
        service,
        "propose",
        "unsupported-node",
        question="unsupported",
        why_now="fixture",
        plan="fixture",
        root_reason="fixture root",
        inputs=[note["note_id"]],
    )
    with pytest.raises(ValueError, match="cannot be used as an input"):
        call(
            service,
            "validate_branch_inputs",
            "unsupported-validate",
            node_id=unsupported["node_id"],
        )

    file_ref = f"pub/{publication['publication_id']}#file"
    item = call(service, "query", "item", ref=file_ref)["value"]
    frozen = root / ".research" / "objects" / item["object_version"]
    missing = call(
        service,
        "propose",
        "missing-node",
        question="missing frozen object",
        why_now="fixture",
        plan="fixture",
        root_reason="fixture root",
        inputs=[file_ref],
    )
    # R34 rejects new unavailable declarations, so lose the object AFTER registering.
    shutil.rmtree(frozen) if frozen.is_dir() else frozen.unlink()
    with pytest.raises(ValueError) as missing_error:
        call(
            service,
            "validate_branch_inputs",
            "missing-validate",
            node_id=missing["node_id"],
        )
    assert str(missing_error.value) == f"{file_ref}: object_missing"


def test_close_node_cancels_queued_task_but_blocks_unverified_creation(tmp_path):
    service, _, _ = project(tmp_path)
    first = call(
        service,
        "propose",
        "first-node",
        question="queued close",
        why_now="fixture",
        plan="fixture",
        root_reason="fixture root",
    )
    call(service, "workflow", "run", action="run", fields={"state": "running"})
    task = call(
        service,
        "workflow",
        "task",
        action="task",
        fields={"node_id": first["node_id"]},
    )
    call(service, "close_node", "close-first", node_id=first["node_id"])
    assert call(service, "query", "queued-task", ref=task["task_id"])["value"]["state"] == "cancelled"

    second = call(
        service,
        "propose",
        "second-node",
        question="unverified close",
        why_now="fixture",
        plan="fixture",
        root_reason="fixture root",
    )
    task2 = call(
        service,
        "workflow",
        "task-2",
        action="task",
        fields={"node_id": second["node_id"]},
    )
    call(
        service,
        "workflow",
        "task-2-state",
        action="task_state",
        fields={"task_id": task2["task_id"], "state": "unverified", "error": "unknown"},
    )
    with pytest.raises(Exception, match="research_verify_task"):
        call(service, "close_node", "close-second", node_id=second["node_id"])
