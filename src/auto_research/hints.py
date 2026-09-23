"""Read-only structural candidates (R38), evaluated against current ledger state.

No hint writes a relation or changes a historical revision. A read boundary is
metadata, not a promise to replay these candidates at a historical boundary.
"""
from __future__ import annotations

import json
import re
import sqlite3

from . import epistemic, frozen_refs

# Copied from docs/067/E1/e1_query.py:19-22. Keep the same mention vocabulary.
KNOWLEDGE_MENTION = re.compile(r"\b(K-\d{3})(?:@(\d+))?")
NODE_MENTION = re.compile(r"\b(X-\d{3})\b")
TEXT_FIELDS = ("statement", "scope", "conditions")
CLASSES = (
    "prose_mention_without_relation",
    "whole_snapshot_evidence",
    "lineage_mention_without_predecessor",
    "complete_publication_cites_risk",
)


def _hint(klass: str, target: str, evidence: dict, repairs: list | None = None) -> dict:
    return {
        "class": klass, "target": target, "evidence": evidence,
        "candidate": True, "confidence": "candidate", "repair_evidence": repairs or [],
    }


def _entry(ref: str) -> str | None:
    match = re.fullmatch(r"knowledge/(K-[\w-]+)(?:@\d+)?", ref)
    return match.group(1) if match else None


def _basis(row: dict) -> set[str]:
    return {entry for ref in row["dependencies"] + row["evidence_refs"]
            if (entry := _entry(ref)) is not None}


def _revisions(db: sqlite3.Connection) -> tuple[list[dict], dict[str, list[dict]]]:
    rows, by_entry = [], {}
    for raw in db.execute(
        "SELECT r.*,e.kind,e.node_id FROM knowledge_revisions r"
        " JOIN knowledge_entries e USING(knowledge_id) ORDER BY r.knowledge_id,r.revision"
    ):
        row = dict(raw)
        for key in ("dependencies", "evidence_refs"):
            row[key] = json.loads(row[key])
        row["ref"] = f"knowledge/{row['knowledge_id']}@{row['revision']}"
        rows.append(row)
        by_entry.setdefault(row["knowledge_id"], []).append(row)
    return rows, by_entry


def prose_hints(rows: list[dict], by_entry: dict, relations: list[dict]) -> list[dict]:
    result = []
    for row in rows:
        kid, owner = row["knowledge_id"], row["node_id"]
        basis = _basis(row)
        for field in TEXT_FIELDS:
            text = row[field] or ""
            seen = set()
            for mention in KNOWLEDGE_MENTION.finditer(text):
                target, explicit = mention.groups()
                if target == kid or target in basis or mention.group(0) in seen:
                    continue
                seen.add(mention.group(0))
                if explicit:
                    candidates = [f"knowledge/{target}@{explicit}"]
                else:
                    existing = [r for r in by_entry.get(target, [])
                                if (r["source_sequence"] or 0) <= (row["source_sequence"] or 0)]
                    candidates = [existing[-1]["ref"]] if existing else []
                repairs = [later["ref"] for later in by_entry[kid]
                           if later["revision"] > row["revision"] and target in _basis(later)]
                result.append(_hint(CLASSES[0], row["ref"], {
                    "mention": mention.group(0), "field": field, "candidate_versions": candidates,
                }, repairs))
            seen = set()
            for mention in NODE_MENTION.finditer(text):
                target = mention.group(1)
                if target == owner or target in seen:
                    continue
                seen.add(target)
                def owns(ref: str) -> bool:
                    return _entry(ref) == kid or (owner is not None and ref == owner)
                connected = any(
                    (owns(rel["source_ref"]) and rel["target_ref"] == target)
                    or (owns(rel["target_ref"]) and rel["source_ref"] == target)
                    for rel in relations
                )
                if not connected:
                    result.append(_hint(CLASSES[0], row["ref"], {
                        "mention": target, "field": field,
                    }))
    return result


def _whole(ref: str) -> bool:
    return re.fullmatch(r"S-\d+", ref) is not None


def _entry_evidence(ref: str) -> bool:
    return frozen_refs.parse(ref).get("form") in {"snapshot_entry", "publication_item", "object_subpath"}


def snapshot_hints(rows: list[dict], by_entry: dict) -> list[dict]:
    result = []
    for row in rows:
        if row["kind"] not in {"claim", "observation"}:
            continue
        repairs = [later["ref"] for later in by_entry[row["knowledge_id"]]
                   if later["revision"] > row["revision"]
                   and not any(_whole(ref) for ref in later["evidence_refs"])
                   and any(_entry_evidence(ref) for ref in later["evidence_refs"])]
        for ref in sorted(set(row["evidence_refs"])):
            if _whole(ref):
                result.append(_hint(CLASSES[1], row["ref"], {"ref": ref}, repairs))
    return result


def lineage_hints(db: sqlite3.Connection, relations: list[dict], node_id: str | None) -> list[dict]:
    result = []
    for node in db.execute(
        "SELECT * FROM nodes n WHERE NOT EXISTS"
        " (SELECT 1 FROM node_dependencies d WHERE d.successor_node_id=n.node_id)"
        " ORDER BY node_id"
    ):
        if node_id is not None and node["node_id"] != node_id:
            continue
        repairs = [rel["relation_id"] for rel in relations
                   if rel["label"] == "lineage_correction" and rel["target_ref"] == node["node_id"]]
        for field in ("question", "root_reason", "plan"):
            seen = set()
            for mention in NODE_MENTION.finditer(node[field] or ""):
                target = mention.group(1)
                if target == node["node_id"] or target in seen:
                    continue
                seen.add(target)
                result.append(_hint(CLASSES[2], node["node_id"], {
                    "mention": target, "field": field,
                }, repairs))
    return result


def publication_hints(db: sqlite3.Connection, bound: int, node_id: str | None) -> list[dict]:
    result = []
    for pub in db.execute("SELECT publication_id,node_id FROM publications WHERE status='complete' ORDER BY publication_id"):
        if node_id is not None and pub["node_id"] != node_id:
            continue
        check = epistemic.publication_check(db, pub["publication_id"], bound)
        if check["result"]["status"] == "attention":
            result.append(_hint(CLASSES[3], f"pub/{pub['publication_id']}", {
                "status": check["result"]["status"], "flagged": check["result"]["flagged"],
            }))
    return result


def structure_hints(
    db: sqlite3.Connection, bound: int | None = None, *, limit: int | None = 8,
    offset: int = 0, node_id: str | None = None, klass: str | None = None,
) -> dict:
    bound = epistemic.read_bound(db) if bound is None else bound
    rows, by_entry = _revisions(db)
    selected = [row for row in rows if node_id is None or row["node_id"] == node_id]
    relations = [dict(row) for row in db.execute("SELECT * FROM relations ORDER BY relation_id")]
    items = (prose_hints(selected, by_entry, relations) + snapshot_hints(selected, by_entry)
             + lineage_hints(db, relations, node_id) + publication_hints(db, bound, node_id))
    if klass is not None:
        items = [item for item in items if item["class"] == klass]
    items.sort(key=lambda item: (CLASSES.index(item["class"]), item["target"],
                                 item["evidence"].get("mention", "")))
    offset = max(0, int(offset))
    page = items[offset:] if limit is None else items[offset:offset + max(1, int(limit))]
    return {
        "items": page, "total": len(items), "shown_count": len(page), "sequence_bound": bound,
        "offset": offset, "next_offset": offset + len(page) if offset + len(page) < len(items) else None,
    }
