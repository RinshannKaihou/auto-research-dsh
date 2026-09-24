"""Optional, publication-local display metadata; no inference or model calls."""
from __future__ import annotations

import json
from .errors import ValidationError

DISPLAY_ID = "__research_display"
DISPLAY_KIND = "research-display"
DISPLAY_SCHEMA = "publication-display/v1"


def normalize_display(value: dict, items: list[dict]) -> dict:
    if not isinstance(value, dict):
        raise ValidationError("display must be an object")

    def text(value, name, maximum, required=True):
        if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
            raise ValidationError(f"display.{name} must be text of at most {maximum} characters")
        return value.strip()

    output = {"schema": DISPLAY_SCHEMA,
              "title": text(value.get("title"), "title", 80),
              "overview": text(value.get("overview"), "overview", 400)}
    sections = value.get("sections", [])
    if not isinstance(sections, list) or len(sections) > 4:
        raise ValidationError("display.sections must contain at most 4 groups")
    output["sections"] = []
    for section in sections:
        if not isinstance(section, dict):
            raise ValidationError("display.sections entries must be objects")
        entries = section.get("items")
        if not isinstance(entries, list) or not 1 <= len(entries) <= 4:
            raise ValidationError("display section items must contain 1 to 4 entries")
        output["sections"].append({"heading": text(section.get("heading"), "heading", 80),
                                  "items": [text(v, "section item", 240) for v in entries]})
    primary = value.get("primary_item_id")
    if primary is not None:
        primary = text(primary, "primary_item_id", 128)
        item = next((v for v in items if v.get("item_id") == primary), None)
        if not item or item.get("kind") == DISPLAY_KIND or not item.get("source_path") or item.get("object_kind") == "directory":
            raise ValidationError("display.primary_item_id must name a file attachment in this publication")
        output["primary_item_id"] = primary
    if len(json.dumps(output, ensure_ascii=False).encode("utf-8")) > 16384:
        raise ValidationError("display must fit within 16 KiB")
    return output


def project_publication(db, record: dict) -> dict:
    """Bounded metadata projection; malformed display must not hide a publication."""
    rows = list(db.execute(
        "SELECT item_id,kind,CASE WHEN item_id='__research_display' THEN content END AS content,source_path,object_kind FROM publication_items "
        "WHERE publication_id=? ORDER BY item_id", (record["publication_id"],)))
    files = [dict(row) for row in rows if row["kind"] != DISPLAY_KIND and row["item_id"] != DISPLAY_ID]
    record["items"] = [{key: item[key] for key in ("item_id", "kind", "source_path", "object_kind")}
                       | {"ref": f"pub/{record['publication_id']}#{item['item_id']}"} for item in files]
    record["display"] = None
    displays = [row for row in rows if row["item_id"] == DISPLAY_ID and row["kind"] == DISPLAY_KIND]
    if len(displays) == 1:
        try:
            raw = displays[0]["content"]
            if len(raw.encode("utf-8")) > 16384:
                return record
            value = json.loads(raw)
            if value.get("schema") == DISPLAY_SCHEMA:
                record["display"] = normalize_display(value, files)
        except (ValueError, TypeError, AttributeError, ValidationError):
            pass
    return record
