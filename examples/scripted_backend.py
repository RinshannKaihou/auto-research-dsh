"""Deterministic lifecycle fixture. This does not evaluate scientific effectiveness."""

import json
from pathlib import Path
import sys
import time

request = json.loads(Path(sys.argv[1]).read_text())
context = request["context"]
workspace = Path.cwd()
question = context["agenda"]["questions"][0]["id"]
nodes = context["nodes"]
closed = {n["why_now"]: n for n in nodes if n["status"] == "closed"}
labels = {n["why_now"] for n in nodes}


def proposal(label, inputs=()):
    return {
        "question": question,
        "why_now": label,
        "plan": f"Lifecycle fixture work stage {label}; partial work is allowed.",
        "modifies_artifact": False,
        "inputs": [
            {"ref": ref, "use": "Continue or compare this fixed material"} for ref in inputs
        ],
    }


def product_ref(label):
    return closed[label]["id"] + "/result#draft"


if request["role"] == "coordinator":
    proposals = []
    if not labels:
        proposals = [proposal("A")]
    elif "A" in closed and not {"B", "C"} & labels:
        proposals = [proposal("B", [product_ref("A")]), proposal("C", [product_ref("A")])]
    elif "B" in closed and "D" not in labels:
        proposals = [proposal("D", [product_ref("B")])]
    elif {"C", "D"} <= closed.keys() and "E" not in labels:
        proposals = [proposal("E", [product_ref("C"), product_ref("D")])]
    result = {
        "proposals": proposals,
        "notes": "Lifecycle fixture; no scientific verdict.",
        "pause_reason": "Fixture finished with an unresolved research question."
        if "E" in closed
        else "Waiting for useful published work.",
    }
else:
    node = context["current_node"]
    label = node["why_now"]
    progress = {
        "done": "Created a partial derivation",
        "pending": ["Unproved premise"],
        "files": ["output/draft.md"],
        "attempt": request["attempt_id"],
    }
    (workspace / "progress.json").write_text(json.dumps(progress))
    (workspace / "output").mkdir(exist_ok=True)
    (workspace / "output/draft.md").write_text(
        f"# Stage {label}\nA partial derivation; premise remains open.\n"
    )
    time.sleep(1.5 if label == "C" else 0.15)
    inputs = list(node["inputs"])
    findings = []
    if label == "B":
        findings = [
            {
                "id": "local",
                "text": "A provisional local step",
                "conditions": "Premise unverified",
                "evidence": ["#draft"],
            }
        ]
    if label == "C":
        history = json.loads((Path(request["history_dir"]) / "index.json").read_text())
        b = next(n for n in history["nodes"] if n["why_now"] == "B" and n["status"] == "closed")
        ref = b["id"] + "/result#local"
        inputs.append({"ref": ref, "use": "Inspect the provisional step's premise"})
        findings = [
            {
                "id": "correction",
                "text": "The provisional step needs a narrower condition",
                "conditions": "Counterexample in draft",
                "evidence": ["#draft"],
                "revises": ref,
            }
        ]
    result = {
        "close_reason": "Work stage ended with partial material",
        "limitations": "Premise remains open",
        "products": [
            {
                "id": "draft",
                "path": "output/draft.md",
                "interface": "Markdown derivation",
                "status": "partial",
                "gaps": ["Unproved premise"],
            }
        ],
        "findings": findings,
        "inputs": inputs,
        "next": ["Continue the unfinished argument"],
    }
Path(sys.argv[2]).write_text(
    json.dumps(
        {"result": result, "usage": {"tokens": 1}, "session_id": "fixture-" + request["attempt_id"]}
    )
)
