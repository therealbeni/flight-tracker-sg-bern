"""Measures how fast each page loads and whether its layout jumps while
loading (cumulative layout shift), on a phone and on the FDL's laptop.
Run with dev/ui/run.sh perf.py (same test data as the walk-through)."""

import sys

from playwright.sync_api import sync_playwright

BASE = "http://localhost:8000"
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
LAPTOP = {"viewport": {"width": 1366, "height": 768}}
PAGES = {
    "pilot@test.ch": (PHONE, ["/dashboard", "/claim", "/claim/11111111-2222-3333-4444-555555555555", "/logbook",
                              "/flights/1", "/flugbuch", "/hilfe"]),
    "desk@test.ch": (LAPTOP, ["/dashboard", "/flugbuch", "/flugbuch?bearbeiten=1"]),
    "admin@test.ch": (PHONE, ["/admin/pilots", "/admin/gliders", "/admin/finalize"]),
}
# Collects layout shifts from the very start of the page (buffered).
CLS_SCRIPT = """() => new Promise(resolve => {
    let cls = 0; const shifts = [];
    new PerformanceObserver(list => { for (const e of list.getEntries()) if (!e.hadRecentInput) {
        cls += e.value; shifts.push(e.sources.map(s => s.node ? (s.node.className || s.node.nodeName) : '?').join('|'));
    } }).observe({type: 'layout-shift', buffered: true});
    setTimeout(() => resolve({cls, shifts}), 800);
})"""
TIMING = """() => { const n = performance.getEntriesByType('navigation')[0];
    const res = performance.getEntriesByType('resource');
    return {ttfb: n.responseStart - n.requestStart, dcl: n.domContentLoadedEventEnd, load: n.loadEventEnd,
            html_kb: n.transferSize / 1024, requests: res.length + 1,
            total_kb: (n.transferSize + res.reduce((a, r) => a + r.transferSize, 0)) / 1024}; }"""


def main() -> None:
    worst = 0.0
    with sync_playwright() as p:
        browser = p.chromium.launch()
        print(f"{'page':48} {'server':>7} {'ready':>7} {'load':>7} {'req':>4} {'kB':>7} {'CLS':>6}")
        for email, (screen, paths) in PAGES.items():
            context = browser.new_context(**screen)
            page = context.new_page()
            page.goto(f"{BASE}/login")
            page.fill("input[name=email]", email)
            page.fill("input[name=password]", "testtest")
            page.click("button[type=submit]")
            page.wait_for_load_state()
            for path in paths:
                for visit in ("first", "again"):  # again: with the browser cache
                    page.goto(BASE + path, wait_until="load")
                    t = page.evaluate(TIMING)
                    c = page.evaluate(CLS_SCRIPT)
                    worst = max(worst, c["cls"])
                    print(f"{email.split('@')[0] + ' ' + path + ' (' + visit + ')':48} {t['ttfb']:6.0f}ms "
                          f"{t['dcl']:6.0f}ms {t['load']:6.0f}ms {t['requests']:4} {t['total_kb']:6.1f} "
                          f"{c['cls']:6.3f} {' '.join(c['shifts'][:3])}")
            context.close()
        browser.close()
    sys.exit(1 if worst > 0.01 else 0)


if __name__ == "__main__":
    main()
