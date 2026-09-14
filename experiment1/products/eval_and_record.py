#!/usr/bin/env python3
"""eval_and_record.py -- score every variant under a directory through the recorder and tabulate deltas.

Run from the campaign root (the recorder resolves runs/ relative to the cwd):
  python3 memory/nodes/X-3/eval_and_record.py --variants scratch/variants --baseline R-001 [--note-prefix variant]

Expected layout:  <variants>/<name>/answers.json      (one sub-directory per variant)
For each variant: python3 tools/run_record.py run --candidate <variants>/<name>/answers.json --note "<prefix>:<name>"
then the result line is parsed and a table is printed: name, run id, status, score, delta vs the baseline run's score.

Status at close of X-3:
  smoke-tested once on scratch/variants/base/answers.json (a copy of answers.json) -> R-003; table printed fine.
  Not yet wired to the variant builder of X-2, which writes flat files <variants>/<name>.json rather than sub-directories.
  --baseline is a plain run id; which run should be the baseline for each delta of plan P is not decided here.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

RECORDER = ["python3", "tools/run_record.py"]


def results():
    out = {}
    p = Path("runs/events.jsonl")
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            e = json.loads(line)
            if e.get("ev") == "result":
                out[e["id"]] = e
    return out


def run_one(path, note):
    proc = subprocess.run(RECORDER + ["run", "--candidate", str(path), "--note", note], capture_output=True, text=True)
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if proc.returncode != 0 or not lines:
        raise SystemExit(f"recorder failed for {path}: rc={proc.returncode} stderr={proc.stderr[-300:]}")
    return json.loads(lines[-1])


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variants", required=True)
    ap.add_argument("--baseline", required=True, help="run id whose score is the reference for delta")
    ap.add_argument("--note-prefix", default="variant")
    args = ap.parse_args()
    base = results().get(args.baseline)
    if base is None or base.get("score") is None:
        raise SystemExit(f"baseline {args.baseline} has no scored result in runs/events.jsonl")
    rows = []
    for d in sorted(Path(args.variants).iterdir()):
        f = d / "answers.json"
        if d.is_dir() and f.exists():
            r = run_one(f, f"{args.note_prefix}:{d.name}")
            delta = None if r.get("score") is None else round(r["score"] - base["score"], 4)
            rows.append((d.name, r["id"], r["status"], r.get("score"), delta))
    if not rows:
        raise SystemExit(f"no <name>/answers.json found under {args.variants}")
    print(f"{'name':24s} {'run':7s} {'status':11s} {'score':>8s} {'delta':>8s}   (baseline {args.baseline} = {base['score']})")
    for name, rid, st, sc, dl in rows:
        sc_s = "-" if sc is None else f"{sc:.4f}"
        dl_s = "-" if dl is None else f"{dl:+.4f}"
        print(f"{name:24s} {rid:7s} {st:11s} {sc_s:>8s} {dl_s:>8s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
