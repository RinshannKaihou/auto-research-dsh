"""Recovery at durable boundaries between the backend, runtime, and state store.

Most tests replay actual on-disk job receipts around an injected interruption.
Two tests use a small local Python backend through the real supervisor; no model
or network service is involved.
"""

import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

from auto_research import cli
from auto_research.artifacts import ArtifactError
from auto_research.io import ProcessInspectionError, atomic_json, read_json
from auto_research.runtime import Runtime
from auto_research.store import Store


class AbruptInterruption(BaseException):
    """Do not let normal result validation turn an injected crash into failure."""


EMPTY_RESULT = {
    "close_reason": "The work stage ended without resolving the question",
    "limitations": "The main conjecture remains open",
    "products": [],
    "findings": [],
    "inputs": [],
    "next": [],
}


@pytest.fixture
def runtime(tmp_path):
    backend = tmp_path / "local_backend.py"
    backend.write_text(
        "import json,sys\nfrom pathlib import Path\n"
        "request=json.loads(Path(sys.argv[1]).read_text())\n"
        "workspace=Path(request['workspace'])\n"
        "(workspace/'received-context.json').write_text(json.dumps(request['context']))\n"
        "with (workspace/'launches.txt').open('a') as stream: stream.write('launch\\n')\n"
        "Path(sys.argv[2]).write_text(json.dumps("
        + repr({"result": EMPTY_RESULT, "usage": {"tokens": 1}, "session_id": "local-test"})
        + "))\n"
    )
    store = Store(tmp_path / "project")
    store.initialize(
        "Preserve an unfinished theoretical investigation",
        budget=100,
        config={
            "backend_command": [sys.executable, str(backend), "{request}", "{output}"],
            "worker_estimate": 4,
            "coordinator_estimate": 4,
            "concurrency": 2,
            "timeout_s": 5,
        },
    )
    runner = Runtime(store.root)
    yield runner
    # Real supervisors must not outlive a failed assertion in these tests.
    for process in runner.children.values():
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=7)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)


def proposal(label="Follow the missing argument", **updates):
    return {
        "question": "Q-001",
        "why_now": label,
        "plan": "Record partial work and its gaps",
        "inputs": [],
        "modifies_artifact": False,
        **updates,
    }


def durable_receipt(runtime, *, node=None, result=None, usage=2, terminal="completed"):
    """Write the same durable receipt a completed backend would leave behind."""
    attempt = runtime.store.reserve(
        node["id"] if node else None, role="worker" if node else "coordinator", estimate=4
    )
    workspace = runtime.artifacts.prepare_workspace(attempt["id"])
    job_dir = runtime.jobs / attempt["id"]
    job_dir.mkdir(parents=True)
    runtime.store.set_attempt(
        attempt["id"],
        state="running",
        dispatch_prepared=True,
        dispatch_at=0,
        workspace=str(workspace),
        job_dir=str(job_dir),
    )
    output = {"result": EMPTY_RESULT if result is None else result, "usage": {"tokens": usage}}
    atomic_json(job_dir / "output.json", output)
    state = {"terminal": True, "phase": terminal}
    atomic_json(job_dir / "status.json", state)
    return runtime.store.get_attempt(attempt["id"]), workspace, state


def wait_for_completion(runtime, attempt_id, timeout=8):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        runtime.reconcile()
        current = runtime.store.get_attempt(attempt_id)
        if current["state"] in {"completed", "failed", "interrupted"}:
            return current
        time.sleep(0.03)
    pytest.fail("Local supervisor did not reach an observable terminal state")


def archived_parent(runtime):
    node = runtime.store.propose(proposal("Initial partial lemma"))
    attempt = runtime.store.reserve(node["id"], estimate=0)
    source_dir = runtime.root / "manual-source"
    source_dir.mkdir()
    source = source_dir / "lemma.md"
    source.write_text("Partial proof. The compactness assumption is unresolved.")
    product = {
        "id": "proof",
        "interface": "A lemma draft",
        "status": "partial",
        "gaps": ["compactness"],
        **runtime.artifacts.freeze(source, source_dir),
    }
    closed = runtime.store.publish(
        node["id"],
        {**EMPTY_RESULT, "products": [product], "close_reason": "Ready for another approach",},
    )
    runtime.store.settle(attempt["id"], 0)
    return closed["result"]["products"][0]["ref"]


def test_worker_recollection_after_publication_crash_is_idempotent(runtime, monkeypatch):
    node = runtime.store.propose(proposal())
    attempt, workspace, state = durable_receipt(
        runtime,
        node=node,
        result={
            **EMPTY_RESULT,
            "products": [
                {
                    "id": "proof",
                    "path": "scratch/proof.md",
                    "status": "partial",
                    "interface": "An unfinished proof",
                    "gaps": ["H"],
                }
            ],
        },
    )
    (workspace / "scratch" / "proof.md").write_text("Conditional argument, with H still open")
    with monkeypatch.context() as patch:
        patch.setattr(
            runtime.store, "settle", lambda *a, **kw: (_ for _ in ()).throw(AbruptInterruption())
        )
        with pytest.raises(AbruptInterruption):
            runtime.collect(attempt, state)
    before_restart = runtime.store.get_node(node["id"])
    assert before_restart["status"] == "closed"
    assert runtime.store.get_attempt(attempt["id"])["state"] == "running"

    restored = Runtime(runtime.root)
    restored.reconcile()
    restored.collect(attempt, state)  # an at-least-once delivery is harmless
    assert restored.store.get_node(node["id"]) == before_restart
    assert restored.store.budget()["spent"] == 2
    assert restored.store.budget()["held"] == 0
    publications = [e for e in restored.store.events() if e["kind"] == "node.published"]
    assert len(publications) == 1
    assert restored.store.get_attempt(attempt["id"])["collected"] is True
    assert restored.store.resolve(f"{node['id']}/result#proof")["item"]["status"] == "partial"


@pytest.mark.parametrize("boundary", ["between_proposals", "after_notes"])
def test_coordinator_recollection_preserves_committed_proposals_and_notes(
    runtime, monkeypatch, boundary
):
    output = {
        "proposals": [proposal("Approach A"), proposal("Approach B")],
        "notes": "Two competitive approaches are worth exploring.",
        "pause_reason": "",
    }
    attempt, _, _ = durable_receipt(runtime, result=output)
    original_propose = runtime.store.propose
    calls = 0

    def interrupt_second_proposal(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise AbruptInterruption()
        return original_propose(*args, **kwargs)

    with monkeypatch.context() as patch:
        if boundary == "between_proposals":
            patch.setattr(runtime.store, "propose", interrupt_second_proposal)
        else:
            patch.setattr(
                runtime.store,
                "configure",
                lambda *a, **kw: (_ for _ in ()).throw(AbruptInterruption()),
            )
        with pytest.raises(AbruptInterruption):
            runtime.reconcile()
    committed_ids = [n["id"] for n in runtime.store.list_nodes()]
    assert len(committed_ids) == (1 if boundary == "between_proposals" else 2)

    restored = Runtime(runtime.root)
    restored.reconcile()
    restored.reconcile()
    nodes = restored.store.list_nodes()
    assert [n["why_now"] for n in nodes] == ["Approach A", "Approach B"]
    assert [n["id"] for n in nodes][: len(committed_ids)] == committed_ids
    assert restored.store.notes()["version"] == 2
    assert restored.store.notes()["text"] == output["notes"]
    assert restored.store.budget()["spent"] == 2
    assert len(restored.store.attempts()) == 1


def test_dispatch_context_uses_reserved_versions_during_operator_steering(runtime, monkeypatch):
    node = runtime.store.propose(proposal())
    runtime.store.notes("This is the note at dispatch.")
    original_prepare = runtime.artifacts.prepare_workspace

    def steer_after_reservation(*args, **kwargs):
        workspace = original_prepare(*args, **kwargs)
        runtime.store.agenda(questions=[{"id": "Q-002", "text": "The operator changed directions"}])
        runtime.store.notes("This was written after the attempt was reserved.")
        return workspace

    monkeypatch.setattr(runtime.artifacts, "prepare_workspace", steer_after_reservation)
    attempt = runtime.dispatch(node)
    terminal = wait_for_completion(runtime, attempt["id"])
    assert terminal["state"] == "completed", terminal
    context = read_json(Path(attempt["workspace"]) / "received-context.json")
    assert context["agenda"]["version"] == attempt["agenda_version"] == 1
    assert context["agenda"]["questions"][0]["id"] == node["question"]
    assert context["notes"]["version"] == attempt["notes_version"] == 2
    assert context["notes"]["text"] == "This is the note at dispatch."
    assert runtime.store.agenda()["version"] == 2
    assert runtime.store.notes()["version"] == 3


def test_same_reference_has_multiple_uses_but_one_materialized_copy_and_one_dispatch(
    runtime, monkeypatch
):
    reference = archived_parent(runtime)
    uses = [
        {"ref": reference, "use": "Continue the proof"},
        {"ref": reference, "use": "Check its compactness assumption"},
    ]
    node = runtime.store.propose(proposal(inputs=uses))
    materialized = []
    original_materialize = runtime.artifacts.materialize

    def count_materializations(product, destination, **kwargs):
        materialized.append(str(destination))
        return original_materialize(product, destination, **kwargs)

    monkeypatch.setattr(runtime.artifacts, "materialize", count_materializations)
    attempt = runtime.dispatch(node, request_id="one-worker-launch")
    replay = runtime.dispatch(node, request_id="one-worker-launch")
    assert replay["id"] == attempt["id"]
    terminal = wait_for_completion(runtime, attempt["id"])
    assert terminal["state"] == "completed", terminal
    again = runtime.dispatch(node, request_id="one-worker-launch")
    assert again["state"] == "completed"
    assert len(materialized) == 1
    assert Path(materialized[0]).read_text().startswith("Partial proof")
    context = read_json(Path(attempt["workspace"]) / "received-context.json")
    assert context["current_node"]["inputs"] == uses
    assert (Path(attempt["workspace"]) / "launches.txt").read_text() == "launch\n"
    assert len(runtime.store.attempts(node_id=node["id"])) == 1


def test_prepared_spawn_failure_becomes_unknown_and_retry_does_not_dispatch(runtime, monkeypatch):
    node = runtime.store.propose(proposal())
    monkeypatch.setattr("auto_research.runtime.process_identity", lambda pid: "a-queryable-process")
    launches = []

    def uncertain_spawn(*args, **kwargs):
        launches.append(args)
        raise OSError("Injected failure at the spawn boundary")

    monkeypatch.setattr("auto_research.runtime.subprocess.Popen", uncertain_spawn)
    with pytest.raises(OSError, match="spawn boundary"):
        runtime.dispatch(node, request_id="uncertain-launch")
    attempt = runtime.store.attempts()[0]
    assert attempt["state"] == "unknown"
    assert attempt["dispatch_prepared"] is True
    assert (Path(attempt["job_dir"]) / "request.json").is_file()
    assert runtime.store.budget()["held"] == 4
    assert runtime.dispatch(node, request_id="uncertain-launch")["state"] == "unknown"
    runtime.store.configure(control="running")
    assert runtime.tick()["control"] == "paused"
    assert len(runtime.store.attempts()) == 1
    assert len(launches) == 1
    assert runtime.store.get_node(node["id"])["status"] == "open"


def test_failure_before_prepare_consumes_no_model_budget_and_is_not_relaunched_on_receipt_retry(
    runtime, monkeypatch
):
    node = runtime.store.propose(proposal())
    monkeypatch.setattr("auto_research.runtime.process_identity", lambda pid: "a-queryable-process")
    monkeypatch.setattr(
        runtime.artifacts,
        "prepare_workspace",
        lambda *a, **kw: (_ for _ in ()).throw(ArtifactError("Input unavailable")),
    )
    launches = []
    monkeypatch.setattr(
        "auto_research.runtime.subprocess.Popen", lambda *a, **kw: launches.append(a)
    )
    with pytest.raises(ArtifactError, match="unavailable"):
        runtime.dispatch(node, request_id="unprepared")
    attempt = runtime.store.attempts()[0]
    assert attempt["state"] == "failed"
    assert attempt["cost"] == 0
    assert runtime.store.budget()["held"] == 0
    assert runtime.dispatch(node, request_id="unprepared")["state"] == "failed"
    assert launches == []
    assert runtime.store.get_node(node["id"])["status"] == "open"


def test_unavailable_process_inspection_never_authorizes_settlement_or_replay(
    runtime, monkeypatch, capsys
):
    node = runtime.store.propose(proposal())
    attempt = runtime.store.reserve(node["id"], estimate=4)
    job_dir = runtime.jobs / attempt["id"]
    atomic_json(
        job_dir / "status.json",
        {"terminal": False, "phase": "running", "pid": 12345, "identity": "saved-identity"},
    )
    runtime.store.set_attempt(attempt["id"], state="running", dispatch_prepared=True, dispatch_at=0)

    def unavailable(pid):
        raise ProcessInspectionError("The host cannot query processes")

    monkeypatch.setattr("auto_research.runtime.process_identity", unavailable)
    runtime.reconcile()
    current = runtime.store.get_attempt(attempt["id"])
    assert current["state"] == "unknown"
    assert current["cost"] is None
    assert runtime.store.budget()["held"] == 4
    with pytest.raises(SystemExit) as stopped:
        cli.main(
            [
                "-p",
                str(runtime.root),
                "reconcile",
                "--attempt",
                attempt["id"],
                "--confirm-stopped",
                "--cost",
                "0",
                "--cost-kind",
                "actual",
            ]
        )
    assert stopped.value.code == 2
    assert "cannot query" in json.loads(capsys.readouterr().err)["error"]
    assert runtime.store.get_attempt(attempt["id"])["state"] == "unknown"
    assert runtime.store.budget()["held"] == 4


def test_unavailable_process_inspection_blocks_before_any_reservation(runtime, monkeypatch):
    node = runtime.store.propose(proposal())
    monkeypatch.setattr(
        "auto_research.runtime.process_identity",
        lambda pid: (_ for _ in ()).throw(ProcessInspectionError("ps is denied")),
    )
    with pytest.raises(ProcessInspectionError):
        runtime.dispatch(node)
    assert runtime.store.attempts() == []
    assert runtime.store.get_node(node["id"])["status"] == "proposed"


def test_failed_archival_after_one_frozen_product_keeps_node_open(runtime):
    node = runtime.store.propose(proposal())
    result = {
        **EMPTY_RESULT,
        "products": [
            {
                "id": "present",
                "path": "scratch/present.md",
                "interface": "Draft",
                "status": "partial",
                "gaps": ["H"],
            },
            {
                "id": "missing",
                "path": "scratch/missing.md",
                "interface": "Draft",
                "status": "partial",
                "gaps": [],
            },
        ],
    }
    attempt, workspace, state = durable_receipt(runtime, node=node, result=result)
    (workspace / "scratch" / "present.md").write_text("Unfinished work must remain available")
    runtime.store.configure(control="running")
    runtime.collect(attempt, state)
    settled = runtime.store.get_attempt(attempt["id"])
    assert settled["state"] == "failed"
    assert "validation failed" in settled["error"]
    assert runtime.store.get_node(node["id"])["status"] == "open"
    assert runtime.store.get_node(node["id"])["result"] is None
    assert (workspace / "scratch" / "present.md").read_text().startswith("Unfinished work")
    assert runtime.store.budget()["spent"] == 2
    assert not [e for e in runtime.store.events() if e["kind"] == "node.published"]
    assert runtime.config()["control"] == "paused"


@pytest.mark.parametrize(
    "result",
    [
        [],
        {"proposals": []},
        {**EMPTY_RESULT, "products": [{"id": "escape", "path": "/tmp/absolute.md"}]},
    ],
)
def test_invalid_worker_handoff_is_execution_failure_not_a_scientific_negative(runtime, result):
    node = runtime.store.propose(proposal())
    attempt, _, state = durable_receipt(runtime, node=node, result=result)
    runtime.collect(attempt, state)
    assert runtime.store.get_attempt(attempt["id"])["state"] == "failed"
    assert runtime.store.get_node(node["id"])["status"] == "open"
    assert runtime.store.get_node(node["id"])["result"] is None


def test_excessive_coordinator_batch_is_rejected_before_any_proposal(runtime):
    attempt, _, state = durable_receipt(
        runtime,
        result={"proposals": [proposal(str(i)) for i in range(5)], "notes": "", "pause_reason": ""},
    )
    runtime.collect(attempt, state)
    assert runtime.store.list_nodes() == []
    assert runtime.store.get_attempt(attempt["id"])["state"] == "failed"
    assert "at most four" in runtime.store.get_attempt(attempt["id"])["error"]
    assert runtime.store.budget()["spent"] == 2


def test_operator_can_account_for_unknown_terminal_cost_without_rewriting_outcome(runtime):
    node = runtime.store.propose(proposal())
    attempt, _, state = durable_receipt(runtime, node=node, usage=None)
    runtime.collect(attempt, state)
    assert runtime.store.get_attempt(attempt["id"])["state"] == "completed"
    assert runtime.store.budget()["held"] == 4
    args = cli.parser().parse_args(
        ["-p", str(runtime.root), "settle", attempt["id"], "--cost", "3", "--cost-kind", "actual"]
    )
    result = cli.execute(args)
    assert result["state"] == "completed"
    assert result["cost"] == 3
    assert runtime.store.budget()["held"] == 0
    assert runtime.store.budget()["spent"] == 3
    active = runtime.store.reserve(role="coordinator", estimate=4)
    args.attempt = active["id"]
    with pytest.raises(ValueError, match="Only terminal"):
        cli.execute(args)
    assert runtime.store.get_attempt(active["id"])["cost"] is None
    assert runtime.store.budget()["held"] == 4


def test_call_limit_pauses_instead_of_closing_the_research(runtime):
    runtime.store.configure(control="running", max_coordinator_calls=0)
    state = runtime.tick()
    assert state["control"] == "paused"
    assert runtime.store.attempts() == []
    assert runtime.store.list_nodes() == []
    assert "call limit" in runtime.config()["idle_reason"]
    assert runtime.store.agenda()["questions"][0]["text"]
