"""Research semantics and transactional failure boundaries of the state store."""

from concurrent.futures import ThreadPoolExecutor
import copy
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auto_research.artifacts import ArtifactStore
from auto_research.errors import BudgetError, ConflictError, NotFoundError, ValidationError
from auto_research.store import Store


def _race_reserve(root, node_id, ready, go, output):
    """Separate processes exercise SQLite, not merely a Python object lock."""
    try:
        store = Store(root)
        ready.put(True)
        if not go.wait(10):
            raise RuntimeError("Race did not start")
        attempt = store.reserve(node_id, estimate=4)
        output.put(("reserved", attempt["id"]))
    except (ConflictError, BudgetError) as exc:
        output.put(("rejected", type(exc).__name__))
    except Exception as exc:
        output.put(("error", repr(exc)))


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root)
        self.store.initialize("Understand the conjecture", budget=10)
        self.source = self.root / "source"
        self.source.mkdir()

    def spec(self, **updates):
        return {
            "question": "Q-001",
            "why_now": "The next missing step is explicit",
            "plan": "Work on the lemma; hand off the remaining gap",
            **updates,
        }

    def node(self, **updates):
        return self.store.propose(self.spec(**updates))

    def opened(self, **updates):
        node = self.node(**updates)
        attempt = self.store.reserve(node["id"], estimate=0)
        return node, attempt

    def product(self, identifier="proof", content="If H holds, the lemma follows."):
        source = self.source / f"{identifier}-{len(list(self.source.iterdir()))}.md"
        source.write_text(content)
        return {
            "id": identifier,
            "status": "partial",
            "gaps": ["H remains unproved"],
            **ArtifactStore(self.root).freeze(source, self.source),
        }

    def publish_proof(self):
        node, attempt = self.opened()
        product = self.product()
        result = {
            "products": [product],
            "findings": [
                {
                    "id": "lemma",
                    "text": "The implication H => L holds",
                    "conditions": "Assuming H",
                    "evidence": ["#proof"],
                }
            ],
            "close_reason": "Recorded the conditional proof",
            "limitations": "H is open",
        }
        closed = self.store.publish(node["id"], result)
        self.store.settle(attempt["id"], 0)
        return closed

    def test_initialize_is_idempotent_and_not_destructive(self):
        other = Store(self.root / "other")
        first = other.initialize("A goal", config={"parallel": 2}, request_id="init")
        self.assertEqual(
            first, other.initialize("A goal", config={"parallel": 2}, request_id="init")
        )
        self.assertEqual(first["config"], {"control": "paused", "parallel": 2})
        with self.assertRaises(ConflictError):
            other.initialize("Changed", request_id="init")
        with self.assertRaises(ConflictError):
            other.initialize("A goal")
        self.assertEqual(other.project()["goal"], "A goal")
        self.assertEqual(len(other.events()), 1)

    def test_uninitialized_project_reports_missing_state(self):
        other = Store(self.root / "empty")
        for read in (other.project, other.snapshot, other.budget, other.agenda, other.notes):
            with self.subTest(read=read.__name__), self.assertRaises(NotFoundError):
                read()

    def test_versions_are_retained_and_node_remembers_its_agenda(self):
        node = self.node()
        newer = self.store.agenda(
            "Revised agenda", [{"id": "Q-002", "text": "A different question"}]
        )
        self.assertEqual(newer["version"], 2)
        self.assertEqual(self.store.agenda(version=1)["questions"][0]["id"], "Q-001")
        self.assertEqual(self.store.get_node(node["id"])["agenda_version"], 1)
        with self.assertRaises(ValidationError):
            self.node()
        second = self.node(question="Q-002")
        self.assertEqual(second["agenda_version"], 2)
        self.store.agenda(text="Context changed")
        self.assertEqual(self.store.agenda()["questions"], newer["questions"])
        self.store.agenda(questions=[{"id": "Q-003", "text": "A third question"}])
        self.assertEqual(self.store.agenda()["text"], "Context changed")
        self.store.notes("An open question need not cite a finding", request_id="note")
        self.store.notes("")
        self.assertEqual(self.store.notes(version=1)["text"], "")
        self.assertIn("open question", self.store.notes(version=2)["text"])
        self.assertEqual(self.store.notes()["version"], 3)

    def test_dispatch_uses_current_agenda_and_stopped_questions_do_not_start(self):
        old = self.publish_proof()
        pending = self.node()
        self.store.agenda(
            questions=[
                {"id": "Q-001", "text": "Reconsider the question"},
                {"id": "Q-002", "text": "Another branch"},
            ]
        )
        self.store.notes("A changed plan")
        attempt = self.store.reserve(pending["id"])
        self.assertEqual(attempt["agenda_version"], 2)
        self.assertEqual(attempt["notes_version"], 2)
        self.assertEqual(self.store.get_node(pending["id"])["agenda_version"], 1)
        self.store.settle(attempt["id"], 0, state="interrupted")
        self.store.agenda(
            questions=[
                {"id": "Q-001", "text": "No more investment", "status": "stopped"},
                {"id": "Q-002", "text": "Another branch"},
            ]
        )
        with self.assertRaises(ConflictError):
            self.store.reserve(pending["id"])
        self.store.agenda(questions=[{"id": "Q-002", "text": "Another branch"}])
        with self.assertRaises(ConflictError):
            self.store.reserve(pending["id"])
        followup = self.node(
            question="Q-002",
            inputs=[{"ref": f"{old['id']}/result#proof", "use": "Reuse old material"}],
        )
        self.assertEqual(self.store.reserve(followup["id"])["agenda_version"], 4)
        self.assertEqual(self.store.reserve(role="coordinator")["agenda_version"], 4)
        self.assertEqual(self.store.get_node(old["id"]), old)

    def test_notes_validate_explicit_references_without_requiring_every_sentence_to_cite(self):
        old = self.publish_proof()
        ref = f"{old['id']}/result#lemma"
        self.store.notes(f"The conditional result is in `{ref}`. Next: examine H.")
        self.store.notes(f"The result is recorded at {ref}.")
        previous = self.store.notes()
        with self.assertRaises(NotFoundError):
            self.store.notes("Unsupported reference: X-999/result#missing", request_id="bad-note")
        with self.assertRaises(NotFoundError):
            self.store.notes("Unsupported reference: X-999/result#missing.")
        self.assertEqual(self.store.notes(), previous)
        self.store.notes("Question: is H necessary?", request_id="bad-note")

    def test_competing_proposals_are_not_deduplicated(self):
        first = self.store.propose(self.spec(), request_id="proposal-1")
        retry = self.store.propose(self.spec(), request_id="proposal-1")
        competitor = self.node()
        self.assertEqual(first, retry)
        self.assertNotEqual(first["id"], competitor["id"])
        self.assertEqual(len(self.store.list_nodes()), 2)
        with self.assertRaises(ConflictError):
            self.store.propose(self.spec(plan="Different work"), request_id="proposal-1")
        with self.assertRaises(ConflictError):
            self.store.event("other", request_id="proposal-1")

    def test_explicit_ids_do_not_collide_with_generated_ids(self):
        self.node(id="X-001")
        self.assertEqual(self.node()["id"], "X-002")
        with self.assertRaises(ConflictError):
            self.node(id="X-001")

    def test_zero_runs_empty_findings_can_publish_and_be_continued(self):
        node, attempt = self.opened()
        product = self.product()
        result = {
            "products": [product],
            "findings": [],
            "close_reason": "Budget exhausted before proof completed",
            "limitations": "H remains unresolved",
            "next": ["Find a counterexample to H"],
        }
        published = self.store.publish(node["id"], result, request_id="publish-empty")
        self.assertEqual(published["status"], "closed")
        self.assertEqual(published["result"]["findings"], [])
        self.assertEqual(self.store.get_attempt(attempt["id"])["state"], "reserved")
        self.assertEqual(
            self.store.publish(node["id"], result, request_id="publish-empty"), published
        )
        reference = f"{node['id']}/result#proof"
        resolved = self.store.resolve(reference)
        self.assertEqual(resolved["kind"], "product")
        self.assertEqual(resolved["item"]["status"], "partial")
        followup = self.node(inputs=[{"ref": reference, "use": "Continue the unresolved proof"}])
        self.assertEqual(followup["inputs"][0]["ref"], reference)
        self.store.settle(attempt["id"], 0)
        with self.assertRaises(ConflictError):
            self.store.reserve(node["id"])
        with self.assertRaises(ConflictError):
            self.store.publish(node["id"], result)

    def test_failed_work_need_not_invent_products_or_findings(self):
        node, attempt = self.opened()
        self.store.settle(attempt["id"], 0, state="failed")
        self.assertEqual(self.store.get_node(node["id"])["status"], "open")
        closed = self.store.publish(
            node["id"], {"close_reason": "Required material was unavailable"}
        )
        self.assertEqual(closed["result"]["products"], [])
        self.assertEqual(closed["result"]["findings"], [])

    def test_reference_requires_fixed_closed_results_and_use(self):
        node, _ = self.opened()
        with self.assertRaises(NotFoundError):
            self.store.resolve(f"{node['id']}/result#not-published")
        parent = self.publish_proof()
        reference = parent["result"]["products"][0]["ref"]
        for inputs in (
            "wrong",
            [1],
            [{"ref": reference, "use": ""}],
            [{"ref": "bad", "use": "test"}],
        ):
            with self.subTest(inputs=inputs), self.assertRaises(ValidationError):
                self.node(inputs=inputs)
        with self.assertRaises(NotFoundError):
            self.node(inputs=[{"ref": "X-999/result#proof", "use": "test"}])

    def test_revision_keeps_original_and_accepts_non_run_evidence(self):
        old = self.publish_proof()
        old_ref = f"{old['id']}/result#lemma"
        node, _ = self.opened()
        proof = self.product("counterexample", "A concrete counterexample to H")
        result = {
            "products": [proof],
            "findings": [
                {
                    "id": "correction",
                    "text": "H is not universally true",
                    "conditions": "The stated domain",
                    "evidence": ["#counterexample", old_ref],
                    "revises": old_ref,
                }
            ],
            "inputs": [{"ref": old_ref, "use": "Reconsider the original domain assumption"}],
            "close_reason": "Recorded an obstruction",
        }
        closed = self.store.publish(node["id"], result)
        finding = closed["result"]["findings"][0]
        self.assertEqual(finding["evidence"][0], f"{node['id']}/result#counterexample")
        self.assertEqual(finding["revises"], old_ref)
        self.assertEqual(self.store.get_node(old["id"]), old)
        self.assertEqual(self.store.resolve(old_ref)["kind"], "finding")
        self.assertEqual(closed["inputs"], result["inputs"])

    def test_handoff_may_repeat_inputs_but_different_uses_are_preserved(self):
        parent = self.publish_proof()
        reference = f"{parent['id']}/result#proof"
        source = {"ref": reference, "use": "Continue the proof"}
        node, _ = self.opened(inputs=[source, source])
        self.assertEqual(node["inputs"], [source])
        another_use = {"ref": reference, "use": "Check a premise"}
        closed = self.store.publish(
            node["id"], {"close_reason": "Reviewed the gap", "inputs": [source, another_use]}
        )
        self.assertEqual(closed["inputs"], [source, another_use])

    def test_product_and_evidence_validation_does_not_partially_publish(self):
        parent = self.publish_proof()
        node, _ = self.opened()
        product = self.product("draft")
        valid = {
            "products": [product],
            "findings": [{"id": "f", "text": "A conditional result", "evidence": ["#draft"]}],
            "close_reason": "A stopping point",
        }
        invalid = []
        for change in (
            {"evidence": []},
            {"evidence": ["#f"]},
            {"evidence": [12]},
            {"evidence": ["#missing"]},
            {"text": ""},
            {"revises": f"{parent['id']}/result#proof"},
        ):
            case = copy.deepcopy(valid)
            case["findings"][0].update(change)
            invalid.append(case)
        duplicate = copy.deepcopy(valid)
        duplicate["findings"][0]["id"] = "draft"
        invalid.append(duplicate)
        invalid.append({**valid, "products": [1]})
        invalid.append({**valid, "products": [{**product, "version": "0" * 64}]})
        invalid.append({**valid, "products": [{**product, "status": "scientifically-proven"}]})
        invalid.append({**valid, "products": [{**product, "interface": 4}]})
        invalid.append({**valid, "products": [{**product, "gaps": [4]}]})
        invalid.append({**valid, "findings": [{**valid["findings"][0], "conditions": ["H"]}]})
        invalid.append(
            {**valid, "findings": [{**valid["findings"][0], "evidence": ["X-999/result#gone"]}]}
        )
        before = len(self.store.events())
        for index, case in enumerate(invalid):
            with self.subTest(case=index), self.assertRaises((ValidationError, NotFoundError)):
                self.store.publish(node["id"], case)
            self.assertEqual(self.store.get_node(node["id"])["status"], "open")
            with self.assertRaises(NotFoundError):
                self.store.resolve(f"{node['id']}/result#draft")
        self.assertEqual(len(self.store.events()), before)
        closed = self.store.publish(node["id"], valid)
        self.assertEqual(closed["status"], "closed")

    def test_archive_corruption_is_detected_on_resolve(self):
        node = self.publish_proof()
        product = node["result"]["products"][0]
        path = self.root / product["path"]
        path.chmod(0o600)
        path.write_text("Corrupted after publication")
        with self.assertRaises(ValidationError):
            self.store.resolve(product["ref"])
        self.assertEqual(self.store.get_node(node["id"])["status"], "closed")

    def test_publication_failure_rolls_back_items_status_and_request(self):
        node, _ = self.opened()
        result = {"products": [self.product()], "findings": [], "close_reason": "Hand off"}
        original_record = self.store._record

        def fail(db, kind, data):
            if kind == "node.published":
                raise RuntimeError("Simulated crash before transaction commit")
            return original_record(db, kind, data)

        with patch.object(self.store, "_record", side_effect=fail), self.assertRaises(RuntimeError):
            self.store.publish(node["id"], result, request_id="retry-publish")
        restored = Store(self.root)
        self.assertEqual(restored.get_node(node["id"])["status"], "open")
        with self.assertRaises(NotFoundError):
            restored.resolve(f"{node['id']}/result#proof")
        closed = restored.publish(node["id"], result, request_id="retry-publish")
        self.assertEqual(closed["status"], "closed")
        self.assertEqual(sum(event["kind"] == "node.published" for event in restored.events()), 1)

    def test_reservation_failure_rolls_back_open_and_budget(self):
        node = self.node()
        with patch.object(
            self.store, "_record", side_effect=RuntimeError("Crash")
        ), self.assertRaises(RuntimeError):
            self.store.reserve(node["id"], estimate=7, request_id="retry-reserve")
        self.assertEqual(self.store.get_node(node["id"])["status"], "proposed")
        self.assertEqual(self.store.budget()["held"], 0)
        self.assertEqual(self.store.attempts(), [])
        attempt = self.store.reserve(node["id"], estimate=7, request_id="retry-reserve")
        self.assertEqual(attempt["id"], "A-001")

    def test_coordinator_and_worker_share_budget_and_actual_overrun_counts(self):
        first = self.store.reserve(None, role="coordinator", estimate=3)
        node = self.node()
        worker = self.store.reserve(node["id"], estimate=6)
        self.assertEqual(self.store.budget()["held"], 9)
        with self.assertRaises(BudgetError):
            self.store.reserve(None, role="coordinator", estimate=2)
        self.store.settle(first["id"], 5)
        self.assertTrue(self.store.budget()["over_budget"])
        self.assertEqual(self.store.budget()["actual"], 5)
        with self.assertRaises(BudgetError):
            self.store.reserve(None, role="coordinator", estimate=0)
        self.store.settle(worker["id"], 2)
        self.assertEqual(self.store.budget()["spent"], 7)
        self.assertEqual(self.store.budget()["available"], 3)

    def test_unknown_cost_survives_restart_and_reconciliation(self):
        node = self.node()
        attempt = self.store.reserve(node["id"], estimate=8)
        self.store.set_attempt(
            attempt["id"],
            state="running",
            pid=12345,
            workspace="/private/work",
            progress={"gap": "H"},
        )
        self.store.settle(attempt["id"], None, state="interrupted")
        restored = Store(self.root)
        self.assertEqual(restored.budget()["held"], 8)
        self.assertEqual(restored.budget()["unknown_count"], 1)
        self.assertIsNone(restored.get_attempt(attempt["id"])["cost"])
        self.assertEqual(restored.get_attempt(attempt["id"])["progress"], {"gap": "H"})
        self.assertEqual(restored.get_node(node["id"])["status"], "open")
        with self.assertRaises(BudgetError):
            restored.reserve(node["id"], estimate=3)
        reconciled = restored.settle(attempt["id"], 4, state="interrupted")
        self.assertEqual(reconciled["hold"], 0)
        retry = restored.reserve(node["id"], estimate=3)
        self.assertNotEqual(retry["id"], attempt["id"])
        self.assertEqual(restored.budget()["held"], 3)

    def test_uncertain_execution_blocks_duplicate_even_after_restart(self):
        node = self.node()
        attempt = self.store.reserve(node["id"], estimate=2)
        self.store.set_attempt(attempt["id"], state="unknown", job_id="backend-123")
        restored = Store(self.root)
        with self.assertRaises(ConflictError):
            restored.reserve(node["id"], estimate=2)
        self.assertEqual(restored.attempts(active_only=True)[0]["id"], attempt["id"])
        restored.set_attempt(attempt["id"], state="running")
        self.assertEqual(restored.get_attempt(attempt["id"])["state"], "running")

    def test_estimated_cost_can_be_reconciled_but_actual_cannot_be_erased(self):
        attempt = self.store.reserve(role="coordinator", estimate=4)
        self.store.settle(attempt["id"], 3, cost_kind="estimated")
        self.assertEqual(self.store.budget()["estimated"], 3)
        self.store.settle(attempt["id"], 2.5, cost_kind="actual")
        self.assertEqual(self.store.budget()["actual"], 2.5)
        self.assertEqual(self.store.budget()["estimated"], 0)
        self.store.settle(attempt["id"], 2.5, request_id="same-cost")
        before = len(self.store.events())
        self.store.settle(attempt["id"], 2.5, request_id="same-cost")
        self.assertEqual(len(self.store.events()), before)
        for kwargs in (
            {"cost": None},
            {"cost": 3},
            {"cost": 2.5, "cost_kind": "estimated"},
            {"cost": 2.5, "state": "failed"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ConflictError):
                self.store.settle(attempt["id"], **kwargs)

    def test_configuration_can_lower_budget_without_rewriting_accounting(self):
        self.store.reserve(role="coordinator", estimate=6)
        self.store.configure({"parallel": 2}, budget=4, control="running")
        self.assertEqual(self.store.project()["config"], {"control": "running", "parallel": 2})
        self.assertEqual(self.store.budget()["remaining"], -2)
        with self.assertRaises(BudgetError):
            self.store.reserve(estimate=0)
        self.store.configure(budget=8)
        self.assertEqual(self.store.budget()["available"], 2)

    def test_request_race_reserves_once(self):
        node = self.node()
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(
                pool.map(
                    lambda _: Store(self.root).reserve(
                        node["id"], estimate=4, request_id="same-reserve"
                    ),
                    range(4),
                )
            )
        self.assertEqual(len({result["id"] for result in results}), 1)
        self.assertEqual(self.store.budget()["held"], 4)
        self.assertEqual(len(self.store.attempts()), 1)

    def _process_race(self, node_ids):
        ctx = multiprocessing.get_context("spawn")
        ready, output, go = ctx.Queue(), ctx.Queue(), ctx.Event()
        processes = [
            ctx.Process(target=_race_reserve, args=(str(self.root), node_id, ready, go, output))
            for node_id in node_ids
        ]
        try:
            for process in processes:
                process.start()
            for _ in processes:
                self.assertTrue(ready.get(timeout=15))
            go.set()
            results = [output.get(timeout=15) for _ in processes]
            for process in processes:
                process.join(timeout=15)
                self.assertEqual(process.exitcode, 0)
            return results
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            ready.close()
            output.close()

    def test_two_processes_cannot_claim_the_same_node(self):
        node = self.node()
        results = self._process_race([node["id"], node["id"]])
        self.assertEqual(sorted(result[0] for result in results), ["rejected", "reserved"], results)
        self.assertEqual(self.store.budget()["held"], 4)
        self.assertEqual(len(self.store.attempts()), 1)

    def test_two_processes_cannot_overreserve_distinct_nodes(self):
        self.store.configure(budget=5)
        nodes = [self.node(), self.node()]
        results = self._process_race([node["id"] for node in nodes])
        self.assertEqual(sorted(result[0] for result in results), ["rejected", "reserved"], results)
        self.assertEqual(self.store.budget()["held"], 4)
        self.assertEqual(len(self.store.list_nodes(status="proposed")), 1)

    def test_snapshot_attempt_filters_and_events(self):
        node, attempt = self.opened()
        coordinator = self.store.reserve(role="coordinator", estimate=1)
        self.store.set_attempt(attempt["id"], state="running", pid=1, request_id="metadata")
        self.assertEqual(
            self.store.set_attempt(attempt["id"], state="running", pid=1, request_id="metadata")[
                "pid"
            ],
            1,
        )
        self.assertEqual(self.store.attempts(node_id=node["id"])[0]["id"], attempt["id"])
        self.assertEqual(
            self.store.attempts(role="coordinator", state="reserved")[0]["id"], coordinator["id"]
        )
        event = self.store.event("user.note", {"note": "Continue later"}, request_id="event")
        self.assertEqual(
            self.store.event("user.note", {"note": "Continue later"}, request_id="event"), event
        )
        self.assertEqual(self.store.events(after=event["id"]), [])
        self.assertEqual(len(self.store.events(limit=2)), 2)
        self.assertEqual(self.store.events(limit=0), [])
        snapshot = self.store.snapshot()
        self.assertEqual(snapshot["nodes"], self.store.list_nodes())
        self.assertEqual(snapshot["attempts"], self.store.attempts())
        self.assertEqual(snapshot["budget"], self.store.budget())

    def test_validation_and_terminal_execution_do_not_mutate_nodes(self):
        for bad in (
            {"question": "missing"},
            {"why_now": ""},
            {"plan": []},
            {"modifies_artifact": "yes"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                self.node(**bad)
        node = self.node(plan={"step": "A bounded derivation"})
        with self.assertRaises(ConflictError):
            self.store.publish(node["id"], {"close_reason": "Never opened"})
        attempt = self.store.reserve(node["id"])
        for fields in ({"hold": 0}, {"state": "invented"}, {"state": []}):
            with self.assertRaises(ValidationError):
                self.store.set_attempt(attempt["id"], **fields)
        self.store.set_attempt(attempt["id"], state="failed")
        self.assertEqual(self.store.get_node(node["id"])["status"], "open")
        with self.assertRaises(ConflictError):
            self.store.set_attempt(attempt["id"], state="running")
        for kwargs in ({"state": "running"}, {"state": []}, {"cost": 1, "cost_kind": "unknown"}):
            with self.assertRaises(ValidationError):
                self.store.settle(attempt["id"], **kwargs)
        for getter in (
            lambda: self.store.get_node("no-node"),
            lambda: self.store.get_attempt("no-attempt"),
            lambda: self.store.agenda(version=999),
            lambda: self.store.notes(version=999),
        ):
            with self.assertRaises(NotFoundError):
                getter()

    def test_non_finite_or_malformed_data_is_rejected(self):
        for value in (-1, True, "1", float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.store.reserve(estimate=value)
        operations = [
            lambda: self.store.initialize("", budget=1),
            lambda: self.store.configure(config=[]),
            lambda: self.store.configure(config={"x": float("nan")}),
            lambda: self.store.agenda(questions=[]),
            lambda: self.store.agenda(questions=[1]),
            lambda: self.store.agenda(
                questions=[{"id": "Q", "text": "a"}, {"id": "Q", "text": "b"}]
            ),
            lambda: self.store.agenda(questions=[{"id": "Q/1", "text": "a"}]),
            lambda: self.store.agenda(questions=[{"id": "Q", "text": "a", "status": []}]),
            lambda: self.store.agenda(text=12),
            lambda: self.store.agenda(text="old", version=1),
            lambda: self.store.notes(text=12),
            lambda: self.store.notes(text="old", version=1),
            lambda: self.store.propose([]),
            lambda: self.store.event("x", {"set": {1}}),
            lambda: self.store.event("x", request_id=""),
            lambda: self.store.events(after=-1),
            lambda: self.store.events(limit=-1),
            lambda: self.store.resolve("#unscoped"),
        ]
        for operation in operations:
            with self.subTest(operation=operation), self.assertRaises(ValidationError):
                operation()
        node, _ = self.opened()
        for result in (
            [],
            {},
            {"close_reason": "x", "findings": {}},
            {"close_reason": "x", "products": {}},
        ):
            with self.assertRaises(ValidationError):
                self.store.publish(node["id"], result)


if __name__ == "__main__":
    unittest.main()
