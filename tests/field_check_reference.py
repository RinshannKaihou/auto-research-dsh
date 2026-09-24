"""Fable's reference reading of R42 (2026-09-23), used to compute CE-25.

Pure functions over an already-read file body; no ledger, no resolver. The
implementation (src/auto_research/field_checks.py) must agree with this on
every CE-25 row. usage: python3 field_check_reference.py
"""
import json
import math

NUMERIC_OPS = {"approx", "lt", "le", "gt", "ge"}


def pointer(document, text):
    """RFC 6901 evaluation. Returns (present, value)."""
    if text == "":
        return True, document
    current = document
    for raw in text[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return False, None
            current = current[token]
        elif isinstance(current, list):
            if token == "0" or (token[:1] in "123456789" and token.isdigit()):
                index = int(token)
                if index >= len(current):
                    return False, None
                current = current[index]
            else:
                return False, None
        else:
            return False, None
    return True, current


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def kind(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    return "container"


def evaluate(body: bytes, check: dict) -> dict:
    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return {"result": "not_checkable", "reason": "unsupported_format", "observed_text": None}
    present, observed = pointer(document, check["path"])
    if not present:
        return {"result": "not_checkable", "reason": "missing_value", "observed_text": None}
    text = json.dumps(observed, allow_nan=True, ensure_ascii=False, sort_keys=True)[:200]
    out = lambda result, reason=None: {"result": result, "reason": reason, "observed_text": text}
    if kind(observed) == "container":
        return out("not_checkable", "not_a_scalar")
    op, value = check["op"], check["value"]
    if op in NUMERIC_OPS or (op == "eq" and is_number(value)):
        if is_number(observed) and not math.isfinite(observed):
            return out("not_checkable", "invalid_number")
        if op in NUMERIC_OPS and not is_number(observed):
            return out("not_checkable", "invalid_number")
    if op == "eq":
        if kind(observed) != kind(value):
            return out("inconsistent", "type_mismatch")
        return out("consistent") if observed == value else out("inconsistent", "value_mismatch")
    holds = {
        "approx": lambda: abs(observed - value) <= check["tolerance"],
        "lt": lambda: observed < value, "le": lambda: observed <= value,
        "gt": lambda: observed > value, "ge": lambda: observed >= value,
    }[op]()
    return out("consistent") if holds else out("inconsistent", "value_mismatch")


FIXTURE = (
    '{"audit": {"fpr": 0.010401, "n": 12, "ok": true, "tag": "v2", "nan": NaN,'
    ' "list": [1, 2], "obj": {}}, "rows": [1.5, 2.5], "a/b": 7, "m~n": 8}'
).encode()

ROWS = [
    {"path": "/audit/fpr", "op": "approx", "value": 0.0104, "tolerance": 0.00005},
    {"path": "/audit/fpr", "op": "approx", "value": 0.0104, "tolerance": 0.0000005},
    {"path": "/audit/fpr", "op": "le", "value": 0.01},
    {"path": "/audit/fpr", "op": "le", "value": 0.0105},
    {"path": "/audit/n", "op": "eq", "value": 12},
    {"path": "/audit/n", "op": "eq", "value": 12.0},
    {"path": "/audit/n", "op": "eq", "value": "12"},
    {"path": "/audit/ok", "op": "eq", "value": True},
    {"path": "/audit/ok", "op": "eq", "value": 1},
    {"path": "/audit/ok", "op": "ge", "value": 0},
    {"path": "/audit/tag", "op": "eq", "value": "v2"},
    {"path": "/audit/tag", "op": "eq", "value": "v3"},
    {"path": "/audit/tag", "op": "gt", "value": 1},
    {"path": "/audit/nan", "op": "approx", "value": 0, "tolerance": 1},
    {"path": "/audit/nan", "op": "eq", "value": 0},
    {"path": "/audit/nan", "op": "eq", "value": "NaN"},
    {"path": "/audit/obj", "op": "eq", "value": "x"},
    {"path": "/rows", "op": "le", "value": 3},
    {"path": "/rows/1", "op": "lt", "value": 3},
    {"path": "/rows/2", "op": "lt", "value": 3},
    {"path": "/rows/01", "op": "eq", "value": 2.5},
    {"path": "/a~1b", "op": "eq", "value": 7},
    {"path": "/m~0n", "op": "eq", "value": 8},
    {"path": "/audit/missing", "op": "eq", "value": 1},
    {"path": "", "op": "eq", "value": 1},
]

if __name__ == "__main__":
    for index, row in enumerate(ROWS, 1):
        got = evaluate(FIXTURE, row)
        spec = f'{row["op"]} {row["path"]!r} {json.dumps(row["value"])}' + (f' tol={row["tolerance"]}' if "tolerance" in row else "")
        print(f"{index:2} {spec:42} -> {got['result']:13} {str(got['reason']):16} observed={got['observed_text']}")
    print("not JSON ->", evaluate(b"# report\n", {"path": "/x", "op": "eq", "value": 1}))
