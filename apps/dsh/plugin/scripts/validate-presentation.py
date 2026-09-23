"""Validate a project's optional research.presentation.json without ledger writes."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

PLUGIN = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN / "python"))

from auto_research.native_store import NativeStore  # noqa: E402
from auto_research.workbench_read import presentation  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_root", type=Path)
    args = parser.parse_args()
    root = args.project_root.resolve()
    result = presentation(NativeStore(root, readonly=True), root)
    if result["status"] != "ready":
        print(f"presentation: {result['status']}: {result.get('message', '')}", file=sys.stderr)
        return 1
    value = result["value"]
    failed = [metric["id"] for metric in value["metrics"] if any(
        metric[side]["value"] is None for side in ("baseline", "current")
    )]
    if failed or value["link_warnings"] or value["stale"]:
        print(f"presentation: invalid metrics={failed} links={value['link_warnings']} stale={value['stale']}", file=sys.stderr)
        return 1
    print(f"presentation: valid; {len(value['nodes'])} nodes, {len(value['metrics'])} verified comparisons")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
