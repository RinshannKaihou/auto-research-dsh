"""Run the entire deterministic lifecycle through the production scheduler."""

import argparse
import json
from pathlib import Path
import sys
import tempfile

from auto_research.runtime import Runtime, check
from auto_research.store import Store

parser = argparse.ArgumentParser()
parser.add_argument("--project", type=Path)
args = parser.parse_args()
root = (args.project or Path(tempfile.mkdtemp(prefix="ari-v5-demo-"))).resolve()
source = Path(__file__).resolve().parents[1]
store = Store(root)
store.initialize(
    "Demonstrate partial work, parallel continuation and synthesis; no scientific verdict",
    budget=100,
    config={
        "backend_command": [
            sys.executable,
            str(source / "examples/scripted_backend.py"),
            "{request}",
            "{output}",
        ],
        "worker_estimate": 2,
        "coordinator_estimate": 2,
        "concurrency": 2,
        "timeout_s": 10,
    },
)
result = Runtime(root).start(poll_interval=0.05, max_cycles=500)
checks = check(root)
assert checks["ok"], checks
assert len(result["nodes"]) == 5 and all(n["status"] == "closed" for n in result["nodes"]), result
print(
    json.dumps(
        {
            "project": str(root),
            "nodes": [
                {"id": n["id"], "stage": n["why_now"], "status": n["status"], "inputs": n["inputs"]}
                for n in result["nodes"]
            ],
            "budget": result["budget"],
            "check": checks["ok"],
        },
        indent=2,
    )
)
