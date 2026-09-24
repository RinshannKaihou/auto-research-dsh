import base64
import json
import pytest
from auto_research import workbench_read
from auto_research.errors import ValidationError
from auto_research.publication_display import DISPLAY_ID
from test_workbench_read import initialized, published, service_call


def display(**changes):
    return {"title": "文献调研完成", "overview": "梳理机制并提出待验证假设。",
            "sections": [{"heading": "待验证假设", "items": ["需要实验检验"]}],
            "primary_item_id": "report", **changes}


def test_display_publish_roundtrip_and_immutable_retry(tmp_path):
    root = tmp_path / "research"
    root.mkdir()
    (root / "REPORT.md").write_text("# Grounding\nEvidence and limits.")
    service, store, _, _, _, old = published(tmp_path, items=[{"item_id": "report", "source_path": "REPORT.md"}])
    args = dict(status="complete", summary="Original detailed publication", gaps=["Not validated"],
                items=[{"item_id": "report", "kind": "report", "source_path": "REPORT.md"}], display=display())
    result = service_call(service, "publish", "with-display", **args)
    assert service_call(service, "publish", "with-display", **args) == result
    full = store.reference_query(f"pub/{result['publication_id']}", full=True)["value"]
    assert full["summary"] == args["summary"]
    meta = next(item for item in full["items"] if item["item_id"] == DISPLAY_ID)
    assert meta["content"]["schema"] == "publication-display/v1"
    assert meta["content"]["title"] == args["display"]["title"]
    with store._read() as db:
        before = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
    page = workbench_read.page(store, "publications")
    assert page["items"][0]["display"] is None
    assert page["items"][1]["display"]["title"] == "文献调研完成"
    assert [item["item_id"] for item in page["items"][1]["items"]] == ["report"]
    assert workbench_read.summary(store, "host", "main")["final_publication"]["display"]["primary_item_id"] == "report"
    with store._read() as db:
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == before
        assert db.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 2
    assert store.reference_query(f"pub/{old['publication_id']}", full=True)["value"]["summary"] == "Documented outcome"


@pytest.mark.parametrize("metadata", [display(primary_item_id="absent"), display(title="x"*81),
                                     display(sections=[{"heading":"H", "items":[]}]), display(overview=None)])
def test_invalid_display_rejected_before_publication(tmp_path, metadata):
    store, _, _ = initialized(tmp_path)
    store.focus("host", "main", None, "planner", "manual", "focus")
    with pytest.raises(ValidationError):
        store.publish_metadata("host", "main", "partial", "summary", [],
                               [{"item_id":"report", "source_path":"report.md", "object_kind":"file"}],
                               "publish", display=metadata)
    assert workbench_read.page(store, "publications")["total"] == 0


def test_bad_optional_metadata_does_not_hide_existing_results(tmp_path):
    store, _, _ = initialized(tmp_path)
    store.focus("host", "main", None, "planner", "manual", "focus")
    pub = store.publish_metadata("host", "main", "partial", "keep original", [],
                                 [{"item_id":DISPLAY_ID, "kind":"research-display", "content":{"schema":"bad"}}], "legacy")
    projected = workbench_read.page(store, "publications")["items"][0]
    assert projected["publication_id"] == pub["publication_id"]
    assert projected["summary"] == "keep original" and projected["display"] is None
    assert projected["items"] == []


def test_reference_chunks_preserve_long_publication_text(tmp_path):
    store, _, _ = initialized(tmp_path)
    store.focus("host", "main", None, "planner", "manual", "focus")
    original = "### " + "长文本🙂" * 6000
    pub = store.publish_metadata("host", "main", "partial", original, [], [], "long")
    ref = f"pub/{pub['publication_id']}"
    assert store.reference_query(ref)["value"]["summary"]["truncated"]
    chunks = []; offset = 0
    while offset is not None:
        part = store.reference_chunk(ref, offset=offset, limit=8192)
        chunks.append(base64.b64decode(part["chunk"]))
        offset = part["next_offset"]
    assert json.loads(b"".join(chunks))["value"]["summary"] == original
