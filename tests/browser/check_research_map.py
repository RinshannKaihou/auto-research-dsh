"""Exercise the actual viewer in Chromium; no model calls or existing project edits.

PYTHONPATH=src python3 tests/browser/check_research_map.py --output-dir /tmp/ari-ui-check
Use --browser /path/to/chromium when the local Playwright cache differs.
"""

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import threading

from playwright.sync_api import expect, sync_playwright

from auto_research.store import Store
from auto_research.visualization import graph_snapshot, make_server, render_html

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("visual_demo", ROOT / "scripts/create_visual_demo.py")
DEMO = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEMO)


def exercise(browser, project: Path, url: str, output: Path, temporary: Path) -> dict:
    store = Store(project)
    initial = store.snapshot()
    context = browser.new_context(viewport={"width": 1440, "height": 960}, device_scale_factor=1)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    checks = []
    page.goto(url)
    page.wait_for_load_state("networkidle")
    expect(page.locator(".node")).to_have_count(8)
    expect(page.locator("#connection-text")).to_have_text("实时同步")
    expect(page.locator("#goal")).to_contain_text("演示")
    checks.append("live map renders the real eight-node Store snapshot")
    page.screenshot(path=str(output / "research-map-desktop.png"), full_page=True)

    page.locator('[data-node="X-005"]').click()
    expect(page.locator("#details-heading")).to_have_text("X-005")
    revision_ref = "X-003/result#tentative-explanation"
    page.locator(".revision-note .ref-button").filter(has_text=revision_ref).click()
    expect(page.locator("#details-heading")).to_have_text("X-003")
    expect(page.locator('[data-ref="X-003/result#weak-observation"]')).to_contain_text("-3/4")
    expect(page.locator('[data-ref="X-003/result#weak-observation"] .revision-note')).to_have_count(
        0
    )
    expect(page.locator('[data-ref="X-003/result#tentative-explanation"]')).to_contain_text(
        "此发现有后续修订"
    )
    assert page.locator('[data-ref="X-003/result#tentative-explanation"]').evaluate(
        "el => { const a = el.getBoundingClientRect(); "
        "const b = document.getElementById('details-body').getBoundingClientRect(); "
        "return a.top < b.bottom && a.bottom > b.top; }"
    )
    checks.append(
        "revision links preserve and distinguish the original observation and explanation"
    )
    paths = page.locator(".edge").evaluate_all("els => els.map(e => e.getAttribute('d'))")
    assert len(paths) == len(set(paths)), "Parallel input and revision edges overlap"
    checks.append("parallel input/revision relationships have separate visible paths")

    page.locator("#fit").click()
    page.locator('[data-node="X-006"]').click()
    page.get_by_role("button", name="追溯来源", exact=True).click()
    expect(page.locator('[data-node="X-004"]')).to_have_class("node related")
    expect(page.locator('[data-node="X-007"]')).to_have_class("node dim")
    page.get_by_role("button", name="后续探索", exact=True).click()
    expect(page.locator('[data-node="X-001"]')).to_have_class("node dim")
    expect(page.locator('[data-node="X-007"]')).to_have_class("node related")
    checks.append("upstream and downstream tracing follow the selected synthesis node")

    scale = page.locator("#zoom-label").inner_text()
    page.get_by_role("button", name="放大", exact=True).click()
    assert page.locator("#zoom-label").inner_text() != scale
    before_pan = page.locator("#canvas > g").get_attribute("transform")
    box = page.locator("#canvas").bounding_box()
    page.mouse.move(box["x"] + 80, box["y"] + 105)
    page.mouse.down()
    page.mouse.move(box["x"] + 130, box["y"] + 135, steps=5)
    page.mouse.up()
    assert page.locator("#canvas > g").get_attribute("transform") != before_pan
    page.locator("#fit").click()
    checks.append("zoom, pan and fit controls change the actual SVG viewport")

    page.locator("#search").fill("没有这个研究节点-unique")
    expect(page.locator(".node")).to_have_count(0)
    expect(page.locator("#empty-title")).to_have_text("没有匹配的探索")
    page.locator("#empty-clear").click()
    page.locator("#status-filter").select_option("proposed")
    expect(page.locator(".node")).to_have_count(1)
    expect(page.locator('[data-node="X-007"]')).to_be_visible()
    page.locator("#clear-filter").click()
    page.locator("#question-filter").select_option("Q-002")
    expect(page.locator(".node")).to_have_count(3)
    page.locator("#clear-filter").click()
    checks.append("search, question and status filters plus empty-result recovery work")

    page.locator('[data-node="X-002"]').focus()
    page.keyboard.press("Enter")
    expect(page.locator("#details-heading")).to_have_text("X-002")
    expect(page.locator(".product")).to_contain_text("半成品")
    expect(page.locator("#details-body")).to_contain_text("暂无发现")
    page.keyboard.press("ArrowRight")
    expect(page.locator("#details-heading")).to_have_text("X-004")
    page.locator('[data-node="X-002"]').click()
    page.locator(".artifact-version summary").click()
    expect(page.locator(".artifact-version")).to_have_attribute("open", "")
    product = initial["nodes"][1]["result"]["products"][0]
    expect(page.locator(".artifact-version code")).to_contain_text(product["version"])
    checks.append("keyboard navigation and partial-work details expose the fixed artifact version")
    assert store.snapshot() == initial, "Viewer interactions changed research state"
    checks.append("all viewer interactions leave Store state unchanged")

    transform = page.locator("#canvas > g").get_attribute("transform")
    page.locator("#details-body").evaluate("el => { el.scrollTop = 200; }")
    detail_scroll = page.locator("#details-body").evaluate("el => el.scrollTop")
    synthesis = initial["nodes"][5]["result"]["products"][0]["ref"]
    added = store.propose(
        {
            "question": "Q-001",
            "why_now": "浏览器测试：新增的接续节点",
            "plan": "只验证实时呈现，不启动研究",
            "inputs": [{"ref": synthesis, "use": "验证已有材料到新节点的连线"}],
        }
    )
    expect(page.locator(f'[data-node="{added["id"]}"]')).to_have_count(1, timeout=10000)
    expect(page.locator("#details-heading")).to_have_text("X-002")
    assert page.locator("#canvas > g").get_attribute("transform") == transform
    expect(page.locator(".artifact-version")).to_have_attribute("open", "")
    assert abs(page.locator("#details-body").evaluate("el => el.scrollTop") - detail_scroll) < 2
    checks.append("live changes retain node selection, viewport and expanded artifact metadata")

    page.route("**/api/graph", lambda route: route.fulfill(status=503, body="unavailable"))
    expect(page.locator("#connection-text")).to_contain_text("连接中断", timeout=10000)
    expect(page.locator(".node")).to_have_count(9)
    page.unroute("**/api/graph")
    expect(page.locator("#connection-text")).to_have_text("实时同步", timeout=10000)
    checks.append("a failed refresh preserves the graph and reconnects automatically")
    page.locator("#fit").click()
    page.locator('[data-node="X-005"]').click()
    assert page.locator("#details-body").evaluate("el => el.scrollTop") == 0
    checks.append("switching nodes resets details to the top while reference jumps reach the item")
    page.screenshot(path=str(output / "research-map-detail.png"), full_page=True)

    mobile = context.new_page()
    mobile.set_viewport_size({"width": 390, "height": 844})
    mobile.on("pageerror", lambda error: errors.append(str(error)))
    mobile.goto(url)
    mobile.wait_for_load_state("networkidle")
    assert mobile.evaluate("document.documentElement.scrollWidth <= innerWidth")
    mobile.locator("#status-filter").select_option("proposed")
    mobile.locator('[data-node="X-007"]').click()
    expect(mobile.locator("#details")).to_be_visible()
    expect(mobile.locator("#details-heading")).to_have_text("X-007")
    mobile.screenshot(path=str(output / "research-map-mobile.png"), full_page=True)
    mobile.locator("#close-details").click()
    expect(mobile.locator("#details")).to_be_hidden()
    checks.append("390px viewport has no horizontal overflow and opens/closes node details")
    mobile.close()

    offline = temporary / "offline.html"
    state = graph_snapshot(store)
    state["project"][
        "goal"
    ] = "</script><script>globalThis.__injected=1</script><img src=x onerror=alert(1)>"
    offline.write_text(render_html(state), encoding="utf-8")
    offline_requests = []
    page.on("request", lambda request: offline_requests.append(request.url))
    page.goto(offline.as_uri())
    page.wait_for_load_state("networkidle")
    expect(page.locator("#connection-text")).to_have_text("离线快照")
    assert page.evaluate("typeof globalThis.__injected") == "undefined"
    expect(page.locator("#goal")).to_have_text(state["project"]["goal"])
    expect(page.locator("img")).to_have_count(0)
    page.locator('[data-node="X-005"]').click()
    expect(page.locator("#details-heading")).to_have_text("X-005")
    assert not [url for url in offline_requests if url.startswith(("http:", "https:"))]
    checks.append("offline HTML is interactive without network and renders hostile text inertly")

    empty = copy.deepcopy(state)
    empty["nodes"], empty["attempts"] = [], []
    empty_path = temporary / "empty.html"
    empty_path.write_text(render_html(empty), encoding="utf-8")
    page.goto(empty_path.as_uri())
    expect(page.locator("#empty-title")).to_have_text("第一段探索，从这里开始")
    expect(page.locator(".node")).to_have_count(0)
    checks.append("an empty project has a usable explanation instead of a broken canvas")

    cycle = copy.deepcopy(state)
    cycle["nodes"] = [
        {
            "id": f"C-{i}",
            "question": "Q-001",
            "status": "closed",
            "agenda_version": 1,
            "why_now": "绘图健壮性夹具",
            "plan": "循环数据仅用于绘图测试",
            "result": None,
            "inputs": [{"ref": f"C-{(i-1)%3}/result#draft", "use": "循环布局"}],
        }
        for i in range(3)
    ]
    cycle["nodes"][0]["inputs"].append({"ref": "C-0/result#draft", "use": "自环"})
    cycle["attempts"] = []
    cycle_path = temporary / "cycle.html"
    cycle_path.write_text(render_html(cycle), encoding="utf-8")
    page.goto(cycle_path.as_uri())
    expect(page.locator(".node")).to_have_count(3)
    expect(page.locator(".edge")).to_have_count(4)
    page.locator('[data-node="C-0"]').click()
    expect(page.locator("#details-heading")).to_have_text("C-0")
    assert "NaN" not in page.locator("#canvas").inner_html()
    checks.append("cyclic and self-referencing layout fixtures remain finite and clickable")

    long_path = copy.deepcopy(cycle)
    long_path["nodes"] = [
        {
            **cycle["nodes"][0],
            "id": f"L-{i}",
            "inputs": [] if i == 0 else [{"ref": f"L-{i-1}/result#draft", "use": "长路径布局"}],
        }
        for i in range(80)
    ]
    long_file = temporary / "long.html"
    long_file.write_text(render_html(long_path), encoding="utf-8")
    page.goto(long_file.as_uri())
    expect(page.locator(".node")).to_have_count(80)
    page.locator("#fit").click()
    bounds = page.locator("#canvas").bounding_box()
    for node in page.locator(".node").all():
        b = node.bounding_box()
        assert (
            b["x"] >= bounds["x"] - 1 and b["x"] + b["width"] <= bounds["x"] + bounds["width"] + 1
        )
    checks.append("fit includes all nodes of an 80-stage path")

    assert errors == [], errors
    context.close()
    return {"checks_passed": len(checks), "checks": checks, "browser_errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--browser", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/ari-ui-check"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="ari-map-browser-") as directory:
        temporary = Path(directory)
        project = DEMO.create_demo(temporary / "study")
        server = make_server(Store(project))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with sync_playwright() as playwright:
                options = {"executable_path": str(args.browser)} if args.browser else {}
                browser = playwright.chromium.launch(headless=True, **options)
                try:
                    report = exercise(
                        browser,
                        project,
                        f"http://127.0.0.1:{server.server_port}/",
                        args.output_dir,
                        temporary,
                    )
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(3)
    (args.output_dir / "browser-summary.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
