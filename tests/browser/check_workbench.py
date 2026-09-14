"""Exercise the actual GUI, Workbench and scheduler with a deterministic backend.

PYTHONPATH=src python3 tests/browser/check_workbench.py --browser /path/to/chromium
No model calls or existing project edits. All projects live in a random temp directory.
"""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import sys
import tempfile
import threading
import time
from urllib.parse import parse_qs, unquote, urlsplit

from playwright.sync_api import expect, sync_playwright

from auto_research.gui import make_gui_server
from auto_research.store import Store
from auto_research.workbench import Workbench


ROOT = Path(__file__).resolve().parents[2]


def wait_until(predicate, message, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.08)
    raise AssertionError(message)


def project_id(page):
    return unquote(urlsplit(page.url).fragment.removeprefix("project="))


def no_overflow(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")


def exercise(browser, wb, url, output, temporary, picker, report):
    checks = report["checks"]
    context = browser.new_context(viewport={"width": 1440, "height": 1000}, accept_downloads=True)
    page = context.new_page()
    page.set_default_timeout(12000)
    page.on("pageerror", lambda error: report["js_errors"].append(str(error)))
    requests = []
    page.on(
        "request",
        lambda request: requests.append(
            {
                "url": request.url,
                "method": request.method,
                "body": request.post_data_json if request.method == "POST" else None,
            }
        ),
    )
    try:
        page.goto(url)
        page.wait_for_load_state("networkidle")
        expect(page.locator("#empty-projects")).to_be_visible()
        expect(page.locator("#project-list .project-card")).to_have_count(0)
        assert wb.list_projects() == []
        checks.append("empty home renders without creating or launching a project")
        page.screenshot(path=str(output / "gui-home.png"), full_page=True)

        page.get_by_role("button", name="新建课题", exact=False).click()
        expect(page.locator("#create-dialog")).to_be_visible()
        name = "稳定性条件浏览器验收"
        goal = "研究耦合系统的稳定性条件，保留尚未证明的前提，并允许多个探索分支。"
        page.get_by_label("课题名称", exact=False).fill(name)
        page.get_by_label("研究目标", exact=False).fill(goal)
        page.locator("#create-budget").fill("100")
        page.locator("#create-concurrency").fill("2")
        page.get_by_label("新课题材料路径", exact=True).fill(str(temporary / "missing-material"))
        page.locator("#create-submit").click()
        expect(page.locator("#create-error")).to_be_visible()
        expect(page.locator("#create-name")).to_have_value(name)
        expect(page.locator("#create-goal")).to_have_value(goal)
        assert not (wb.workspace / name).exists()
        checks.append("invalid material shows an error, preserves fields and creates no project")
        picker["path"] = str(temporary / "中文材料.md")
        page.get_by_role("button", name="为此项材料选择文件", exact=True).click()
        expect(page.get_by_label("新课题材料路径", exact=True)).to_have_value(picker["path"])
        page.screenshot(path=str(output / "gui-create.png"), full_page=True)
        page.locator("#create-submit").click()
        expect(page.locator("#project-name")).to_have_text(name)
        first = project_id(page)
        first_store = wb.get_store(first)
        state = first_store.snapshot()
        assert state["nodes"] == [] and state["attempts"] == []
        assert state["project"]["goal"] == goal
        assert state["project"]["config"]["resources"] == [str((temporary / "中文材料.md").resolve())]
        assert state["project"]["config"]["concurrency"] == 2
        assert state["project"]["budget"] == 100
        frame = page.frame_locator("#research-map")
        expect(frame.locator(".node")).to_have_count(0)
        expect(frame.locator("#empty-title")).to_be_visible()
        checks.append(
            "Chinese goal/material/budget/concurrency are stored; create produces no attempt and iframe is empty"
        )

        # Two synchronous user-level click events hit the actual UI busy guard.
        page.locator("#action-start").evaluate("button => { button.click(); button.click(); }")
        wait_until(
            lambda: wb.project_state(first)["controller_running"],
            "Production controller did not start",
        )
        expect(page.locator("#execution-progress")).to_contain_text("第一批探索", timeout=12000)
        expect(page.locator("#execution-progress")).to_contain_text("规划探索方向")
        assert first_store.snapshot()["nodes"] == []
        page.screenshot(path=str(output / "gui-initial-planning.png"), full_page=True)
        checks.append("First coordinator activity is visible before any research node exists")
        wait_until(
            lambda: any(a["node_id"] for a in first_store.attempts()),
            "Scheduler did not dispatch a worker",
        )
        expect(page.locator("#action-pause")).to_be_visible(timeout=15000)
        assert first_store.project()["config"]["generation"] == 1
        starts = [
            request
            for request in requests
            if request["url"].endswith("/api/project/action")
            and request["body"].get("action") == "start"
        ]
        assert len(starts) == 1
        checks.append(
            "Start runs the real scheduler with fake subprocesses; rapid duplicate click sends one start and creates one controller generation"
        )
        page.locator("#action-pause").click()
        wait_until(lambda: wb.project_state(first)["control"] == "paused", "Pause was not applied")
        wait_until(
            lambda: not wb.project_state(first)["controller_running"]
            and not wb.project_state(first)["active_attempts"],
            "Pause did not drain existing work",
            timeout=30,
        )
        expect(page.locator("#action-start")).to_be_visible(timeout=15000)
        expect(page.locator("#action-start")).to_have_text("继续研究")
        assert len(first_store.snapshot()["nodes"]) >= 1
        checks.append(
            "Pause reaches the production control hook and drains existing fake work without discarding its node"
        )

        page.locator("#action-start").click()
        wait_until(
            lambda: wb.project_state(first)["controller_running"]
            and wb.project_state(first)["active_attempts"] > 0,
            "Resume did not dispatch more work",
        )
        expect(page.locator("#action-stop")).to_be_visible(timeout=15000)
        page.locator("#action-stop").click()
        wait_until(lambda: wb.project_state(first)["control"] == "stopped", "Stop was not applied")
        wait_until(
            lambda: not wb.project_state(first)["controller_running"]
            and not wb.project_state(first)["active_attempts"],
            "Stop did not settle active fake subprocesses",
            timeout=30,
        )
        assert any(
            a["state"] == "interrupted" for a in first_store.attempts()
        ), "Stop did not interrupt any real fake-backend attempt"
        expect(page.locator("#project-state")).to_have_text("已停止", timeout=15000)
        checks.append(
            "Resume starts a second real controller generation; Stop interrupts and settles active fake-backend processes"
        )

        page.locator("#toggle-settings").click()
        expect(page.locator("#settings")).to_be_visible()
        notes = "优先核对边界条件；未证明的前提继续保留。"
        page.locator("#settings-notes").fill(notes)
        page.locator("#settings-budget").fill("250")
        # Wait for a real periodic project response instead of assuming a timer interval.
        with page.expect_response(
            lambda response: "/api/project?id=" in response.url and response.status == 200,
            timeout=10000,
        ):
            page.wait_for_timeout(2200)
        expect(page.locator("#settings-notes")).to_have_value(notes)
        expect(page.locator("#settings-budget")).to_have_value("250")
        page.locator("#settings-save").click()
        expect(page.locator("#settings-status")).to_have_text("已保存")
        assert first_store.project()["budget"] == 250
        assert first_store.notes()["text"] == notes
        checks.append(
            "Polling preserves in-progress settings edits; saving updates persistent notes and budget"
        )
        page.locator("#close-settings").click()
        page.reload()
        page.wait_for_load_state("networkidle")
        assert project_id(page) == first
        expect(page.locator("#project-name")).to_have_text(name)
        assert parse_qs(urlsplit(page.locator("#research-map").get_attribute("src")).query)[
            "project"
        ] == [first]
        checks.append("Refresh preserves selected project hash and the correct iframe")
        page.screenshot(path=str(output / "gui-project.png"), full_page=True)

        page.locator("#back-home").click()
        page.locator("#new-project").click()
        page.locator("#create-name").fill(name)
        page.locator("#create-goal").fill("不能覆盖已存在的课题")
        before = first_store.snapshot()
        page.locator("#create-submit").click()
        expect(page.locator("#create-error")).to_be_visible()
        expect(page.locator("#create-name")).to_have_value(name)
        assert first_store.snapshot() == before
        checks.append(
            "Creating at an existing project path is rejected without changing that project"
        )
        second_name = "第二个未运行课题"
        page.locator("#create-name").fill(second_name)
        page.locator("#create-goal").fill("仅验证课题切换，不执行模型")
        page.locator("#create-submit").click()
        expect(page.locator("#project-name")).to_have_text(second_name)
        second = project_id(page)
        assert second != first and wb.get_store(second).attempts() == []
        page.locator("#back-home").click()
        page.locator(f'[data-project="{first}"]').click()
        expect(page.locator("#project-name")).to_have_text(name)
        assert parse_qs(urlsplit(page.locator("#research-map").get_attribute("src")).query)[
            "project"
        ] == [first]
        checks.append(
            "Switching between projects changes the iframe and leaves the second project unstarted"
        )

        external = temporary / "external-project"
        external_store = Store(external)
        external_store.initialize("已存在目录的浏览器打开验收", budget=40, config={"title": "外部已有课题"})
        external_before = external_store.snapshot()
        page.locator("#back-home").click()
        page.locator("#toggle-open").click()
        picker["path"] = str(external)
        page.locator("#browse-open").click()
        expect(page.locator("#open-path")).to_have_value(str(external))
        page.locator("#open-submit").click()
        expect(page.locator("#project-name")).to_have_text("外部已有课题")
        assert external_store.snapshot() == external_before
        checks.append(
            "Injected native-folder picker opens an existing external project without changing or starting it"
        )
        page.locator("#back-home").click()
        page.locator(f'[data-project="{first}"]').click()
        with page.expect_download() as pending:
            page.locator("#export-map").click()
        download = pending.value
        export = output / download.suggested_filename
        download.save_as(str(export))
        assert download.suggested_filename == "research-map.html"
        text = export.read_text(encoding="utf-8")
        assert "const LIVE = false" in text
        assert "backend_command" not in text and "PRIVATE_AGENT" not in text
        offline = context.new_page()
        offline.on("pageerror", lambda error: report["js_errors"].append(str(error)))
        offline.goto(export.as_uri())
        offline.wait_for_load_state("networkidle")
        expect(offline.locator(".node")).to_have_count(len(first_store.snapshot()["nodes"]))
        offline.close()
        checks.append(
            "Offline export downloads a self-contained graph and renders its real nodes without backend configuration"
        )

        page.set_viewport_size({"width": 390, "height": 844})
        assert no_overflow(page), "Project page overflows at 390 px"
        wait_until(
            lambda: frame.locator(".node").evaluate_all(
                "nodes => { const viewport = document.getElementById('canvas').getBoundingClientRect();"
                " return nodes.length > 0 && nodes.every(node => { const r = node.getBoundingClientRect();"
                " const x = (r.left+r.right)/2, y=(r.top+r.bottom)/2;"
                " return x >= viewport.left && x <= viewport.right && y >= viewport.top && y <= viewport.bottom; }); }"
            ),
            "Map nodes remained outside the SVG viewport after mobile resize",
            timeout=5,
        )
        page.screenshot(path=str(output / "gui-mobile.png"), full_page=True)
        page.locator("#back-home").click()
        assert no_overflow(page), "Home page overflows at 390 px"
        page.locator("#new-project").click()
        expect(page.locator("#create-dialog")).to_be_visible()
        assert no_overflow(page), "Create dialog overflows at 390 px"
        page.locator("#close-create").click()
        checks.append(
            "Home, create dialog and project view have no horizontal page overflow at 390 px; map nodes stay inside the resized viewport"
        )
        assert report["js_errors"] == [], report["js_errors"]
        external_requests = [
            request["url"] for request in requests if not request["url"].startswith(url)
        ]
        assert external_requests == [], external_requests
        checks.append("No JavaScript page exceptions or off-origin network requests")
        report.update(
            {
                "projects_created": 2,
                "existing_project_opened": True,
                "first_project_nodes": len(first_store.snapshot()["nodes"]),
                "first_project_attempt_states": [a["state"] for a in first_store.attempts()],
                "scheduler_generations": first_store.project()["config"]["generation"],
                "first_project_control": wb.project_state(first)["control"],
            }
        )
    except Exception:
        page.screenshot(path=str(output / "gui-failure.png"), full_page=True)
        raise
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--browser", help="Path to a local Chromium/headless-shell executable")
    parser.add_argument("--output-dir", type=Path, default=Path("/tmp/ari-v5-gui-browser"))
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    summary = output / "docs" / "validation" / "gui-browser-summary.json"
    summary.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "passed": False,
        "run_at": datetime.now(timezone.utc).isoformat(),
        "model_calls": 0,
        "real_gui": True,
        "real_workbench": True,
        "real_production_scheduler": True,
        "backend": "delayed deterministic scripted_backend.py",
        "native_picker": "injected paths; no operating-system dialog opened",
        "checks": [],
        "js_errors": [],
    }

    def expired(signum, frame):
        raise TimeoutError("Workbench browser test exceeded its overall timeout")

    previous = signal.signal(signal.SIGALRM, expired)
    signal.alarm(args.timeout)
    try:
        with tempfile.TemporaryDirectory(prefix="ari-gui-browser-") as directory:
            temporary = Path(directory)
            (temporary / "中文材料.md").write_text("测试材料：前提尚未证明。", encoding="utf-8")
            wrapper = temporary / "delayed_fixture.py"
            wrapper.write_text(
                "import json,pathlib,runpy,sys,time\n"
                "request=json.loads(pathlib.Path(sys.argv[1]).read_text())\n"
                "if request['role']=='coordinator': time.sleep(4)\n"
                "if request['role']=='worker':\n"
                " pathlib.Path('progress.json').write_text(json.dumps({'done':'Fake backend running','pending':['Test delay']}))\n"
                " time.sleep(4)\n"
                f"runpy.run_path({str(ROOT / 'examples' / 'scripted_backend.py')!r},run_name='__main__')\n"
            )
            wb = Workbench(
                temporary / "workspace",
                defaults={
                    "budget": 100,
                    "concurrency": 2,
                    "estimate": 1,
                    "timeout": 30,
                    "backend_command": [sys.executable, str(wrapper), "{request}", "{output}"],
                },
            )
            picker = {"path": str(temporary / "中文材料.md")}
            server = make_gui_server(wb, picker=lambda kind: picker["path"])
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                with sync_playwright() as playwright:
                    browser = playwright.chromium.launch(
                        headless=True, **({"executable_path": args.browser} if args.browser else {})
                    )
                    report["browser"] = browser.version
                    try:
                        exercise(
                            browser,
                            wb,
                            f"http://127.0.0.1:{server.server_port}/",
                            output,
                            temporary,
                            picker,
                            report,
                        )
                    finally:
                        browser.close()
                report["passed"] = True
            finally:
                for project in wb.list_projects():
                    if project.get("controller_running") or project.get("active_attempts"):
                        wb.action(project["id"], "stop")
                wait_until(
                    lambda: all(
                        not p.get("controller_running") and not p.get("active_attempts")
                        for p in wb.list_projects()
                    ),
                    "Temporary fake processes did not stop",
                    timeout=15,
                )
                server.shutdown()
                server.server_close()
                thread.join(2)
                wb.close()
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)
        summary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
