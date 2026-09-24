"""Seed only the disposable native profile, using its installed plugin storage."""
import os
from pathlib import Path
import sys

from auto_research.native_store import NativeStore

root = Path(sys.argv[1]).resolve()
assert root.is_relative_to(Path(os.environ["DSH_HOME"]).resolve())
store = NativeStore(root)
store.focus("local", "ari-memory-main", None, "planner", "manual", "seed-focus")
note = store.note("local", "ari-memory-main", "Deterministic source", "condition", "seed-note")
lessons = []
for i in range(30):
    node = store.propose(f"OPEN_QUESTION_{i}", "seed", "plan", f"seed-node-{i}")
    lessons.append(
        store.record_knowledge(
            {
                "kind": "lesson",
                "statement": f"LESSON_{i}",
                "node_id": node["node_id"],
                "conditions": {"dataset": f"v{i}"},
                "evidence_refs": [note["note_id"]],
            },
            f"seed-lesson-{i}",
        )
    )
for i, changes in enumerate(
    ({"statement": "EARLY_DISPUTE", "status": "disputed"}, {"statement": "EARLY_CORRECTION"})
):
    store.revise_knowledge(
        {
            "ref": lessons[i]["ref"],
            "expected_revision": 1,
            "changes": changes,
            "reason": "fixture",
            # Required since 0.6.7: a revision has to say what it invalidates.
            "affected_scope_mode": "versions",
            "affected_scope": [lessons[i]["ref"]],
        },
        f"seed-revision-{i}",
    )
