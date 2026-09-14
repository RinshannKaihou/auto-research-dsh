#!/usr/bin/env python3
"""build_samples.py -- construct length-controlled answer variants for the comparison plan P of node X-1.

Run from the campaign root:
  python3 memory/nodes/X-2/build_samples.py --mode pad  --src answers.json --target-tokens 281 --out scratch/variants/base_pad281.json
  python3 memory/nodes/X-2/build_samples.py --mode trim --src scratch/history/answers.rewrite.json --target-tokens 138 --out scratch/variants/rewrite_trim138.json

Status at close of X-2:
  pad()  implemented; hand-checked on answers.json (token count reaches the target exactly; filler adds no key facts as far as checked by eye).
  trim() NOT implemented (raises NotImplementedError): the content-preserving rule is not decided.
  Token counting is whitespace split (str.split); whether the evaluator counts tokens the same way is unverified.
Output format: JSON {"answers": {qid: text}} written to --out as a flat file (one file per variant).
"""
import argparse
import json
from pathlib import Path

FILLER = ("To expand on this point, the explanation above is offered for completeness, and the reasoning "
          "has been reviewed carefully so that the response is thorough, well organized, and easy to follow.").split()


def load(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return dict(data["answers"]) if isinstance(data, dict) and "answers" in data else dict(data)


def dump(answers, path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"answers": answers}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def token_count(answers):
    return sum(len(v.split()) for v in answers.values())


def pad(answers, target_tokens):
    """Append neutral filler words round-robin over the answers until token_count == target_tokens.
    The filler adds no key facts. Raises if the set is already longer than the target."""
    out = dict(answers)
    n = token_count(out)
    if n > target_tokens:
        raise ValueError(f"{n} tokens already exceed target {target_tokens}; padding cannot shorten")
    keys = list(out)
    i = 0
    while n < target_tokens:
        k = keys[i % len(keys)]
        out[k] = out[k] + " " + FILLER[i % len(FILLER)]
        i += 1
        n += 1
    return out


def trim(answers, target_tokens):
    """Reduce token_count to target_tokens without dropping key facts.

    Not implemented. The rule is the open problem: truncating from the end can delete the fact itself
    (e.g. 'The Mona Lisa was painted by Leonardo da Vinci' -> 'The Mona Lisa was painted by').
    Whatever rule is chosen must be checked against the score, not only against the token count.
    """
    raise NotImplementedError("trim(): content-preserving rule not decided (X-2, gap 1)")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["pad", "trim"], required=True)
    ap.add_argument("--src", required=True)
    ap.add_argument("--target-tokens", type=int, required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    answers = load(args.src)
    before = token_count(answers)
    result = (pad if args.mode == "pad" else trim)(answers, args.target_tokens)
    dump(result, args.out)
    print(json.dumps({"mode": args.mode, "src": args.src, "out": args.out,
                      "tokens_before": before, "tokens_after": token_count(result)}))


if __name__ == "__main__":
    main()
