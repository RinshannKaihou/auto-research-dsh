"""R29–R34: frozen reference syntax, cheap resolution, verified object reads.

Only this module interprets publication/snapshot reference syntax. Resolution
never hashes an object or falls back to its original workspace source.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import stat
from urllib.parse import unquote

from .artifacts import ArtifactError, ArtifactStore
from .errors import NotFoundError, ValidationError


def is_frozen(ref) -> bool:
    """Dispatch only; parsing and validation remain in parse/resolve."""
    return isinstance(ref, str) and ref.startswith(("pub/", "S-"))


def _path(text):
    while text.startswith("./"):
        text = text[2:]
    if text.endswith("/"):
        text = text[:-1]
    if not text or any(part in ("", "..") for part in text.split("/")) or "\0" in text:
        raise ValueError("path_escape")
    return text


def parse(ref):
    bad = {"outcome": "unsupported", "reason": "bad_syntax"}
    if not isinstance(ref, str):
        return bad
    head, separator, tail = ref.partition("#")
    if separator and not tail:
        return bad
    try:
        if head.startswith("pub/"):
            pid = head[4:]
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", pid):
                return bad
            value = {"form": "publication", "publication_id": pid}
            if separator:
                tail = _path(tail)
                item, slash, subpath = tail.partition("/")
                value.update(form="publication_item", item_id=item)
                if slash:
                    value["subpath"] = subpath
            return value
        if re.fullmatch(r"S-\d+", head):
            value = {"form": "snapshot", "snapshot_id": head}
            if separator:
                value.update(form="snapshot_entry", path=_path(unquote(tail)))
            return value
    except ValueError as exc:
        return {"outcome": "unsupported", "reason": str(exc)}
    return bad


def _fail(result, outcome, reason, detail=None):
    result.update(outcome=outcome, reason=reason, integrity=None,
                  message=f"{result['ref']}: {reason}" + (f"\n{detail}" if detail else ""))
    return result


def resolve(db, root, ref):
    """Locate the ledger record and inspect types only (never read file bytes)."""
    result = dict(ref=ref, outcome="resolved", reason=None, kind=None, object=None,
                  integrity="unverified", message=f"{ref}: resolved")
    parsed = parse(ref)
    if "outcome" in parsed:
        return _fail(result, parsed["outcome"], parsed["reason"])
    result["kind"] = parsed["form"]
    subpath = parsed.get("subpath")
    if "publication_id" in parsed:
        pid = result["publication_id"] = parsed["publication_id"]
        row = db.execute("SELECT * FROM publications WHERE publication_id=?", (pid,)).fetchone()
        if row is None:
            return _fail(result, "not_found", "publication_missing")
        if parsed["form"] == "publication":
            return result
        item_id = result["item_id"] = parsed["item_id"]
        row = db.execute("SELECT * FROM publication_items WHERE publication_id=? AND item_id=?",
                         (pid, item_id)).fetchone()
        if row is None:
            return _fail(result, "not_found", "item_missing")
        item = dict(row)
        result["source_path"] = item.get("source_path")
        version, kind = item.get("object_version"), item.get("object_kind")
        if version is None:
            if subpath:
                return _fail(result, "not_found", "subpath_missing")
            return result
    else:
        sid = result["snapshot_id"] = parsed["snapshot_id"]
        row = db.execute("SELECT * FROM snapshots WHERE snapshot_id=?", (sid,)).fetchone()
        if row is None:
            return _fail(result, "not_found", "snapshot_missing")
        if parsed["form"] == "snapshot":
            return result
        requested = parsed["path"]
        entries = []
        for entry in json.loads(row["manifest"]):
            try:
                path = _path(entry["source_path"])
            except (ValueError, KeyError, TypeError):
                continue
            entries.append((path, entry))
        matching_paths = {
            path for path, entry in entries
            if requested == path or (
                entry.get("kind") == "directory" and requested.startswith(path + "/")
            )
        }
        # All rows for a selected path participate in ambiguity, including a
        # duplicate incomplete row; availability must not silently pick a winner.
        candidates = [(path, entry) for path, entry in entries if path in matching_paths]
        if not candidates:
            return _fail(result, "not_found", "entry_missing")
        longest = max(len(path) for path, _ in candidates)
        candidates = [(path, entry) for path, entry in candidates if len(path) == longest]
        if len(candidates) != 1:
            return _fail(result, "ambiguous", "duplicate_path")
        path, entry = candidates[0]
        result["entry"] = entry
        subpath = requested[len(path) + 1:] or None
        if entry.get("status") != "fixed":
            return _fail(result, "unavailable", "entry_incomplete", entry.get("error"))
        version, kind = entry.get("version"), entry.get("kind")
    if not isinstance(version, str) or not re.fullmatch(r"[0-9a-f]{64}", version):
        return _fail(result, "unavailable", "object_missing")
    result["object"] = {"version": version, "kind": kind}
    if subpath:
        result.update(kind="object_subpath", subpath=subpath)
    archive = Path(root) / ".research" / "objects" / version
    # Reject redirected object stores without traversing or reading their targets.
    for parent in (archive.parent.parent, archive.parent):
        if parent.is_symlink():
            return _fail(result, "unsupported", "path_escape")
    try:
        mode = archive.lstat().st_mode
    except FileNotFoundError:
        return _fail(result, "unavailable", "object_missing")
    actual = "file" if stat.S_ISREG(mode) else "directory" if stat.S_ISDIR(mode) else None
    if actual != kind or actual is None:
        return _fail(result, "unavailable", "object_kind_mismatch")
    if subpath:
        if kind != "directory":
            return _fail(result, "not_found", "subpath_missing")
        current = archive
        for part in subpath.split("/"):
            current = current / part
            if current.is_symlink():
                return _fail(result, "unsupported", "path_escape")
            if not current.exists():
                return _fail(result, "not_found", "subpath_missing")
    return result


def open(db, root, ref):
    """Return (Resolution, frozen Path or None); verify the entire owning object."""
    result = resolve(db, root, ref)
    if result["outcome"] != "resolved":
        return result, None
    if result["object"] is None:
        return _fail(result, "unavailable", "not_an_object"), None
    obj = result["object"]
    try:
        path = ArtifactStore(root).verify({**obj, "path": f".research/objects/{obj['version']}"})
    except (ArtifactError, OSError) as exc:
        return _fail(result, "unavailable", "object_corrupted", str(exc)), None
    result["integrity"] = "verified"
    return result, path / result["subpath"] if result.get("subpath") else path


def require(db, root, ref):
    """Registration policy: validate without hashing, preserving the original ref."""
    result = resolve(db, root, ref)
    if result["outcome"] == "not_found":
        raise NotFoundError(result["message"])
    if result["outcome"] != "resolved":
        raise ValidationError(result["message"])
    return result
