"""A1/A2 acceptance inside the disposable profile, against its installed plugin.

Exercises the packaged code, not the repository source: the traceback path in
any failure names ``node_modules/auto-research-v5/python``. Prints one JSON
report that the probe turns into separate A1 and A2 checks.
"""
import json
import os
from pathlib import Path
import sys

from auto_research.native_store import NativeStore

root = Path(sys.argv[1]).resolve()
assert root.is_relative_to(Path(os.environ["DSH_HOME"]).resolve())
store = NativeStore(root)
store.initialize("0.6.7 acceptance", "init-067")
store.associate("local", "ari-067", "assoc-067")
store.workflow("local", "ari-067", "register", {"role": "main", "cwd": str(root)}, "role-067")


def record(name, dependencies=None):
    return store.record_knowledge(
        {"kind": "decision", "statement": name, "dependencies": dependencies or []},
        f"rec-{name}",
    )["ref"]


findings = {}

a = record("basis")
b = record("uses-basis", [a])
c = record("downstream", [b])

node = store.propose("acceptance", "now", "plan", "node-067")
store.focus("local", "ari-067", node["node_id"], "planner", "manual", "focus-067")
publication = store.publish_metadata(
    "local", "ari-067", "complete", "acceptance", [],
    [{"item_id": "finding", "kind": "finding", "content": {"value": 1}}],
    "publish-067", knowledge_refs=[b],
)
findings["publish_receipt_carries_check"] = (
    isinstance(publication.get("check"), dict)
    and "sequence_bound" in publication["check"]
    and publication["check"]["status"] == "no impact found"
)

store.revise_knowledge(
    {
        "ref": a, "expected_revision": 1, "changes": {"status": "retracted"},
        "reason": "acceptance retraction", "affected_scope_mode": "versions",
        "affected_scope": [a], "change_kind": "retract",
    },
    "retract-067",
)

text = store.context_view("local", "ari-067")["text"]
section = None
if "## affected_knowledge\n" in text:
    body = text.split("## affected_knowledge\n", 1)[1]
    end = body.find("\n## ")
    section = json.loads(body if end < 0 else body[:end])
findings["context_shows_affected_entries"] = bool(
    section and section["total"] >= 3 and {item["affected_version"] for item in section["items"]} >= {a, b, c}
)
findings["context_shows_an_explanation_path"] = bool(
    section
    and any(item["explanation"] and item["explanation"][-1]["to"] == a for item in section["items"])
)

before = store.knowledge_risk([c])["versions"][c]
picked = store.impact_next()
findings["queue_offers_a_pending_item"] = picked is not None
store.impact_review_state(picked["change_id"], picked["affected_version"], "proposal_ready", "prep-067")
after_prepare = store.knowledge_risk([picked["affected_version"]])["versions"][picked["affected_version"]]
findings["a_prepared_proposal_clears_no_risk"] = after_prepare["needs_action"] is True
again = store.impact_next()
findings["queue_does_not_reoffer_a_prepared_item"] = (
    again is None or again["affected_version"] != picked["affected_version"]
)

store.dispose_impact(
    {
        "change_id": "CH-001", "affected_version": b, "disposition_kind": "revised",
        "reason": "acceptance disposition", "replacement_ref": b,
    },
    "dispose-067",
)
after = store.knowledge_risk([b, c])["versions"]
findings["disposition_closes_its_own_key"] = after[b]["needs_action"] is False
findings["disposition_leaves_citers_at_risk"] = "disposed_old_ref" in {
    item["reason"] for item in after[c]["residual_use_risk"]
}
check = store.publication_check(publication["publication_id"])
findings["publication_reports_the_reference_risk"] = (
    check["result"]["status"] == "attention" and check["result"]["flagged"] == [b]
)
findings["before_disposition_c_needed_action"] = before["needs_action"] is True

# A2 has its own project and goes through the installed service, not offline writes.
from auto_research.service import NativeService
import auto_research.service as service_module

home = Path(os.environ["DSH_HOME"]).resolve()
installed_python = Path(os.environ["ARI_PLUGIN_PYTHON"]).resolve()
assert installed_python.is_relative_to(home)
assert Path(service_module.__file__).resolve().is_relative_to(installed_python)
a2_root = home / "accept-a2-068"
assert a2_root != root and not a2_root.exists()
service = NativeService(home / "accept-a2-registry.sqlite3")
a2_session = "ari-a2-068"


def call(method, operation, **fields):
    return service.handle({
        "host_id": "local", "session_id": a2_session, "turn": 7,
        "transport_id": operation, "operation_id": operation, "method": method, **fields,
    })["value"]


call("open", "a2-open", root=str(a2_root), goal="A2 packaged acceptance")
parent = call("propose", "a2-root", question="independent root", why_now="now",
              plan="plain plan", root_reason="independent")
call("focus", "a2-focus", node_id=parent["node_id"], role="core", mode="manual")
(a2_root / "REPORT.md").write_text("frozen A2 acceptance evidence\n", encoding="utf-8")
snapshot = call("snapshot", "a2-snapshot", paths=["REPORT.md"])
snapshot_ref = snapshot["snapshot_id"]
resolved = call("query", "a2-resolve", ref=f"{snapshot_ref}#REPORT.md")

claim = call("memory_write", "a2-record", action="record", fields={
    "kind": "claim", "statement": "plain observation", "visibility": "project",
    "evidence_refs": [snapshot_ref],
})
revised = call("memory_write", "a2-revise", action="revise", fields={
    "ref": claim["ref"], "changes": {"statement": "reworded observation"},
    "reason": "wording", "affected_scope_mode": "none", "change_kind": "reword",
})
saved = call("query", "a2-read-revision", ref=revised["ref"])["value"]
branch = call("propose", "a2-branch", question="follow-up", why_now="now", plan="plain plan",
              predecessors=[{"node_id": parent["node_id"], "relation_type": "branches_from",
                             "rationale": "follows the question", "input_refs": []}])
branch_saved = call("query", "a2-read-branch", ref=branch["node_id"])["value"]
context = call("memory_context", "a2-context")["text"]
marker = "## structure_hints\n"
hints = json.loads(context.split(marker, 1)[1].split("\n## ", 1)[0]) if marker in context else None
expected_source = dict(host_id="local", session_id=a2_session, turn=7, operation_id="a2-revise")
a2_findings = {
    "snapshot_path_resolves_as_snapshot_entry": (
        resolved["kind"] == "snapshot-entry"
        and resolved["resolution"]["outcome"] == "resolved"
        and resolved["resolution"]["object"]["kind"] == "file"
    ),
    "branches_from_accepts_empty_inputs": (
        branch_saved["origin_kind"] == "derived" and branch_saved["inputs"] == []
    ),
    "revision_provenance_matches_service_session": (
        saved["asserted_at"] == revised["asserted_at"] == expected_source
        and all(saved["asserted_at"].values())
    ),
    "context_contains_whole_snapshot_candidate": bool(
        hints and any(item["class"] == "whole_snapshot_evidence"
                      and item["target"] == revised["ref"]
                      and item["evidence"]["ref"] == snapshot_ref for item in hints["items"])
    ),
}
# B: a separate project, the same installed service, no real provider or ledger.
b_root = home / "accept-b-069"
assert not b_root.exists()
b_service = NativeService(home / "accept-b-registry.sqlite3")


def b_call(method, operation, **fields):
    return b_service.handle({
        "host_id": "local", "session_id": "ari-b-069", "turn": 8,
        "transport_id": operation, "operation_id": operation, "method": method, **fields,
    })["value"]


b_call("open", "b-open", root=str(b_root), goal="B packaged acceptance")
b_call("focus", "b-focus", role="planner", mode="manual")
(b_root / "metrics.json").write_text('{"fpr":0.010401}', encoding="utf-8")
b_snap = b_call("snapshot", "b-snapshot", paths=["metrics.json"])
b_spec = dict(ref=b_snap["snapshot_id"] + "#metrics.json", path="/fpr", op="approx",
              value=0.0104, tolerance=0.00005)
b_value = b_call("memory_write", "b-record", action="record", fields={
    "kind": "claim", "visibility": "project", "statement": "declared measurement",
    "evidence_refs": [b_spec["ref"]], "checks": [b_spec],
})
b_saved = b_call("query", "b-query", ref=b_value["ref"])["value"]
b_rows = b_saved["field_checks"]
b_findings = {
    "declaration_preserved": b_saved["checks"] == [b_spec],
    "single_consistent_result": len(b_rows) == 1 and b_rows[0]["result"] == "consistent"
                                and b_rows[0]["reason"] is None,
    "result_has_input_and_checker": bool(b_rows and b_rows[0]["input_sha256"]
                                         and b_rows[0]["checker_version"] == "field-check/1"),
}
print(json.dumps({
    "b": {"passed": all(b_findings.values()), "findings": b_findings,
          "project_root": str(b_root), "field_checks": b_rows},
    "passed": all(findings.values()), "findings": findings,
    "a2": {"passed": all(a2_findings.values()), "findings": a2_findings,
           "project_root": str(a2_root), "service_module": service_module.__file__,
           "session_id": a2_session, "asserted_at": saved["asserted_at"],
           "snapshot_ref": snapshot_ref, "revision_ref": revised["ref"]},
}, ensure_ascii=False))
