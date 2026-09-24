#!/usr/bin/env python3
"""Smoke-test the installed Research workbench in a real DSH web profile.

Pass the URL printed by run-workflow.py --blank --keep-web. The test creates
only a disposable empty project in that profile and never starts a model turn.
"""

import argparse
import hashlib
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError, sync_playwright


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--screenshots", type=Path, required=True)
    parser.add_argument("--sample", action="store_true", help="Use the pre-attached vLLM sample workspace")
    parser.add_argument("--ledger", type=Path, help="Compare this cloned ledger before and after browse-only interactions")
    args = parser.parse_args()
    args.screenshots.mkdir(parents=True, exist_ok=True)
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            executable_path="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        )
        page = browser.new_page(viewport={"width": 853, "height": 863})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="domcontentloaded", timeout=30000)
        try:
            page.get_by_role("button", name="继续", exact=True).wait_for(timeout=3000)
            page.get_by_role("button", name="继续", exact=True).click()
        except PlaywrightTimeoutError:
            pass
        page.get_by_role("button", name="稍后配置").wait_for(timeout=15000)
        page.get_by_role("button", name="稍后配置").click()
        if not args.sample:
            page.get_by_role("button", name="打开侧边栏").click()
            page.get_by_text("blank-research", exact=True).first.click()
            page.get_by_role("button", name="在“blank-research”中新建会话").click()
        page.get_by_role("button", name="打开研究工作台").click()
        if page.get_by_role("button", name="关联已有项目").is_visible():
            try:
                page.get_by_role("button", name="关联已有项目").click(timeout=3000)
            except PlaywrightTimeoutError:
                # DSH can replace the onboarding panel during a successful click.
                pass
        elif not page.get_by_role("heading", name="项目成果概览").is_visible():
            page.get_by_role("textbox", name="研究目标").fill("0.6.10 浏览器验收用空项目")
            page.get_by_role("button", name="新建并关联").click()
        page.get_by_role("heading", name="vLLM 稳定性检测优化" if args.sample else "项目成果概览").wait_for()

        if args.sample:
            text = page.locator(".ari-app").inner_text()
            for expected in ("gated_fusion_v2:core", "0.78669", "0.530", "0.2993", "目标已结束"):
                assert expected in text, expected
        ledger_before = hashlib.sha256(args.ledger.read_bytes()).hexdigest() if args.ledger else None

        for width, height in ((853, 863), (1440, 1000), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_role("heading", name="vLLM 稳定性检测优化" if args.sample else "项目成果概览").wait_for()
            geometry = page.evaluate("""() => ({
                viewport: innerWidth,
                page: document.documentElement.scrollWidth,
                workbench: document.querySelector('.ari-app')?.getBoundingClientRect().width,
            })""")
            assert geometry["page"] <= width + 1, geometry
            assert geometry["workbench"] <= width + 1, geometry
            page.screenshot(path=str(args.screenshots / f"overview-{width}.png"))

        page.set_viewport_size({"width": 853, "height": 863})
        if args.sample:
            page.get_by_role("button", name="阅读最终报告").click()
            try:
                page.locator(".ari-reader .ari-markdown h1").wait_for(timeout=5000)
            except PlaywrightTimeoutError:
                print("reader debug:", page.locator(".ari-reader").inner_text()[:900])
                print("reader tags:", page.locator(".ari-reader > div:nth-child(2)").inner_html()[:500])
                raise
            assert "FINAL REPORT" in page.locator(".ari-reader .ari-markdown h1").inner_text()
            assert page.locator(".ari-reader .ari-markdown table").count() >= 1
            page.screenshot(path=str(args.screenshots / "report-reader.png"))
            page.get_by_role("button", name="关闭材料").click()
        for nav in ("研究过程", "成果与知识", "材料", "运行与维护", "成果概览"):
            page.get_by_role("button", name=nav, exact=True).click()
            assert page.locator(".ari-app").is_visible()
            if args.sample and nav == "研究过程":
                page.locator(".ari-step").nth(6).wait_for()
                assert page.locator(".ari-step").count() == 7
                timeline = page.locator(".ari-timeline").inner_text()
                for expected in ("X-002", "已被 X-003 替代", "X-005", "默认冠军", "X-006", "新方案未采纳", "X-007", "未采纳"):
                    assert expected in timeline, expected
                page.locator(".ari-step").filter(has_text="X-007").click()
                detail = page.locator(".ari-detail")
                detail.wait_for()
                detail.get_by_text("v2.2 的 6 项 CV 标准只通过 3 项", exact=False).wait_for()
                assert page.evaluate("""() => {
                    const detail=document.querySelector('.ari-detail').getBoundingClientRect();
                    const main=document.querySelector('.ari-main').getBoundingClientRect();
                    return detail.top >= main.top-1 && detail.top < main.top+8 && detail.bottom <= main.bottom+1;
                }"""), "node detail must remain visible in the content viewport"
                page.screenshot(path=str(args.screenshots / "x007-detail.png"))
                detail.get_by_role("button", name="关闭").click()
                page.get_by_role("button", name="执行关系图").click()
                page.locator(".ari-node").first.wait_for()
                assert "100%" in page.locator(".ari-graph-tools").inner_text()
                assert page.locator(".ari-node-id").first.evaluate("el => parseFloat(getComputedStyle(el).fontSize)") >= 12
                page.screenshot(path=str(args.screenshots / "execution-graph.png"))
                page.get_by_role("button", name="时间线").click()
            if args.sample and nav == "成果与知识":
                page.locator(".ari-knowledge-row").first.wait_for()
                assert page.locator(".ari-knowledge-row").count() == 20
                page.get_by_role("textbox", name="搜索项目知识").fill("K-010")
                page.locator(".ari-knowledge-row").filter(has_text="K-010@2").wait_for()
                assert page.locator(".ari-knowledge-row").count() == 1
                page.locator(".ari-knowledge-row").click()
                page.get_by_role("button", name="knowledge/K-010@1", exact=True).wait_for()
                page.get_by_role("button", name="knowledge/K-010@1", exact=True).click()
                page.get_by_text("与现行版本", exact=False).wait_for()
                assert page.evaluate("""() => {
                    const detail=document.querySelector('.ari-detail').getBoundingClientRect();
                    const main=document.querySelector('.ari-main').getBoundingClientRect();
                    return detail.top >= main.top-1 && detail.top < main.top+8;
                }"""), "knowledge detail must remain visible in the content viewport"
                page.screenshot(path=str(args.screenshots / "knowledge-history.png"))
                page.locator(".ari-detail").get_by_role("button", name="关闭").click()
                page.get_by_role("textbox", name="搜索项目知识").fill("K-027")
                page.locator(".ari-knowledge-row").filter(has_text="K-027").wait_for()
                page.locator(".ari-knowledge-row").click()
                evidence = page.locator(".ari-detail .ari-ref").first
                evidence.wait_for()
                evidence.click()
                page.get_by_role("dialog", name="研究材料").wait_for()
                page.get_by_role("button", name="关闭材料").click()
                page.wait_for_function("document.activeElement?.classList.contains('ari-ref')", timeout=3000)
            if args.sample and nav == "材料":
                page.get_by_role("heading", name="报告与交付物").wait_for()
                page.get_by_text("P-007", exact=True).wait_for()
            if args.sample and nav == "运行与维护":
                page.get_by_role("button", name="历史与整理").click()
                page.get_by_text("整理队列是内部复核工作", exact=False).wait_for()
                page.locator(".ari-maintenance-row").first.wait_for()
            page.screenshot(path=str(args.screenshots / f"nav-{nav}.png"))
        if args.sample:
            page.get_by_role("button", name="关闭研究工作台").click()
            if page.get_by_role("button", name="打开侧边栏").is_visible():
                page.get_by_role("button", name="打开侧边栏").click()
            page.get_by_role("button", name="设置", exact=True).click()
            page.get_by_text("深色", exact=True).click()
            page.get_by_role("button", name="关闭", exact=True).click()
            page.get_by_role("button", name="打开研究工作台").click()
            page.get_by_role("heading", name="vLLM 稳定性检测优化").wait_for()
            assert "dark" in page.locator("html").get_attribute("style")
            for width, height in ((853, 863), (1440, 1000), (390, 844)):
                page.set_viewport_size({"width": width, "height": height})
                page.screenshot(path=str(args.screenshots / f"overview-dark-{width}.png"))
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
        assert not errors, errors
        if args.ledger:
            ledger_after = hashlib.sha256(args.ledger.read_bytes()).hexdigest()
            assert ledger_before == ledger_after, (ledger_before, ledger_after)
        browser.close()
    print(f"browser workbench smoke passed; screenshots: {args.screenshots}")


if __name__ == "__main__":
    main()
