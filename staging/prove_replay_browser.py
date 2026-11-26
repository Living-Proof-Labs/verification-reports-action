import argparse
import functools
import http.server
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright

parser = argparse.ArgumentParser()
parser.add_argument("--replay", type=Path, required=True)
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--browser", default="/usr/bin/chromium")
args = parser.parse_args()
root = args.replay
handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(root))
server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
art = args.output
art.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch(executable_path=args.browser)
    try:
        for js in (True, False):
            for width in (390, 430, 1280):
                context = b.new_context(
                    viewport={"width": width, "height": 900}, java_script_enabled=js
                )
                page = context.new_page()
                page.goto(
                    f"http://127.0.0.1:{server.server_port}/on/site/unitree-g1/stock-mujoco-hallway/runs/45760910690/index.html"
                )
                row = page.locator(".has-presentations > .eval-row")
                assert (
                    page.locator(".summary-hero").first.locator(".integrity-status").inner_text()
                    == "Run Integrity: PASS"
                )
                assert row.locator(".eval-name-full").all_text_contents() == [
                    "Scenario",
                    "Run Integrity",
                    "Health",
                ]
                assert row.evaluate("e=>e.scrollWidth<=e.clientWidth")
                tab = page.locator("#evaluator-livingproof-health > summary")
                assert tab.locator(".pip").count() == 0
                box = tab.bounding_box()
                assert box["x"] >= 0 and box["x"] + box["width"] <= width
                page.locator(".summary-card").scroll_into_view_if_needed()
                page.screenshot(path=str(art / f"peers-{width}-js-{js}.png"))
                tab.focus()
                tab.press("Enter")
                panel = page.locator("#" + tab.get_attribute("aria-controls"))
                assert panel.is_visible()
                assert "574 recorded samples" in panel.inner_text()
                assert "0.74814 m at 8.46 s" in panel.inner_text()
                assert "No robot health telemetry" in panel.inner_text()
                page.screenshot(path=str(art / f"health-open-{width}-js-{js}.png"))
                if js:
                    tab.press("Home")
                    page.keyboard.press("ArrowRight")
                    page.keyboard.press("Enter")
                    assert (
                        page.locator("#evaluator-livingproof-run-integrity").get_attribute("open")
                        is not None
                    )
                print(
                    width,
                    "JS",
                    js,
                    "three peer tiles, no overflow, neutral Health, accessible details PASS",
                )
                off = context.new_page()
                off.goto(
                    f"http://127.0.0.1:{server.server_port}/off/site/unitree-g1/stock-mujoco-hallway/runs/45760910690/index.html"
                )
                assert (
                    page.locator(".summary-hero").all_inner_texts()
                    == off.locator(".summary-hero").all_inner_texts()
                )
                assert page.locator(".eval-tabpanels > .eval-tabpanel > .eval-panel").evaluate_all(
                    "es=>es.slice(0,2).map(e=>e.innerHTML)"
                ) == off.locator(".eval-tabpanels > .eval-tabpanel > .eval-panel").evaluate_all(
                    "es=>es.slice(0,2).map(e=>e.innerHTML)"
                )

                def evidence(p):
                    return p.locator('a[href^="evidence/"]').evaluate_all(
                        "es=>es.map(e=>e.getAttribute('href')).sort()"
                    )

                assert evidence(page) == evidence(off)
                assert "11.46 s" in page.content()
                assert "evaluator-livingproof-health" not in off.content()
                if width == 390 and js:
                    tab.focus()
                    tab.press("Enter") if not panel.is_visible() else None
                    page.locator(".summary-card").screenshot(
                        path=str(art / "health-peer-mobile.png")
                    )
                print(
                    "Hero, Scenario/Integrity peer details, evidence links and duration parity PASS"
                )
                context.close()
    finally:
        b.close()
        server.shutdown()
