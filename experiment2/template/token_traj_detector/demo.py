#!/usr/bin/env python3
"""Load a token-id jsonl trajectory file and run the detector."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from metric import DetectResult, detect  # noqa: E402


def _load_decode(tokenizer_id: str) -> Callable[[Sequence[int]], str]:
    try:
        from transformers import AutoTokenizer
    except ImportError as exc:
        raise SystemExit("need transformers to use --tokenizer: pip install transformers") from exc

    tok = AutoTokenizer.from_pretrained(tokenizer_id)
    cache = {}
    backend = getattr(getattr(tok, "backend_tokenizer", None), "decoder", None)

    def per_token(tid: int) -> str:
        if tid in cache:
            return cache[tid]
        if backend is not None:
            raw = tok.convert_ids_to_tokens(int(tid))
            text = backend.decode([raw])
        else:
            text = tok.decode([int(tid)])
        cache[tid] = text if text is not None else ""
        return cache[tid]

    def decode(ids: Sequence[int]) -> str:
        ids = list(ids)
        if len(ids) == 1:
            return per_token(ids[0])
        return tok.decode(ids) or ""

    return decode


def _print_one(label: str, res: DetectResult, n_tokens: int) -> None:
    print(
        json.dumps(
            {
                "seq_id": label,
                "is_ill": res.is_ill,
                "ill_type": res.ill_type,
                "reason": res.reason,
                "n_tokens": n_tokens,
            },
            ensure_ascii=False,
        )
    )


def smoke() -> None:
    # Same 64-token block inserted 11 times with unique spacers so the tail
    # is not an exact loop; block_repeat should fire first among token-id rules.
    block = list(range(100, 164))
    token_ids = list(range(40))
    for i in range(11):
        token_ids.extend(block)
        token_ids.append(10_000 + i)
    token_ids.extend(range(200, 260))
    res = detect(token_ids)
    _print_one("smoke-block-repeat", res, len(token_ids))
    if not (res.is_ill and res.reason == "block_repeat"):
        raise SystemExit(f"smoke failed: expected block_repeat, got {res}")


def run_jsonl(
    path: str,
    decode: Optional[Callable[[Sequence[int]], str]],
    limit: int,
) -> None:
    flagged = total = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            token_ids = rec.get("token_ids")
            if not token_ids:
                continue
            total += 1
            res = detect(token_ids, decode=decode)
            if res.is_ill:
                flagged += 1
            _print_one(str(rec.get("seq_id", total - 1)), res, rec.get("n_tokens", len(token_ids)))
            if limit and total >= limit:
                break
    print(json.dumps({"flagged": flagged, "total": total}, ensure_ascii=False), file=sys.stderr)


def main() -> None:
    ap = argparse.ArgumentParser(description="Detect anomalies on token trajectories")
    ap.add_argument("--jsonl", type=str, help="jsonl with a token_ids field per line")
    ap.add_argument("--tokenizer", type=str, default=None, help="HF tokenizer id, e.g. Qwen/Qwen3-8B")
    ap.add_argument("--limit", type=int, default=0, help="max records to score (0 = all)")
    ap.add_argument("--smoke", action="store_true", help="run a synthetic block-repeat check")
    args = ap.parse_args()

    if args.smoke:
        smoke()
        return
    if not args.jsonl:
        ap.error("provide --jsonl, or --smoke")
    if not Path(args.jsonl).is_file():
        raise SystemExit(f"not a file: {args.jsonl}")

    decode = None
    if args.tokenizer:
        decode = _load_decode(args.tokenizer)
    else:
        print(
            "no --tokenizer: script / sentence rules skipped, token-id rules only",
            file=sys.stderr,
        )
    run_jsonl(args.jsonl, decode, args.limit)


if __name__ == "__main__":
    main()
