"""Looks for flashes: records every frame the browser paints while a click
is handled (page loads, dialogs, the calendar, the side panel ...) and
reports frames that look like neither the page before nor the page after -
a cross-fade, something blinking, content jumping and back.
Run with dev/ui/run.sh flashes.py; the worst frame of each step is saved
to dev/ui/screens/flash-*.jpg.
"""

import base64
import sys

from playwright.sync_api import Page, sync_playwright

BASE = "http://localhost:8000"
PASSWORD = "testtest"
LAPTOP = {"viewport": {"width": 1366, "height": 768}}
WIDE = {"viewport": {"width": 1920, "height": 1080}}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
# Share of the screen (0..1) that may be in between before and after.
LIMIT = 0.01

# Compares two frames in the browser (no image library needed): the share
# of pixels whose brightness differs noticeably, on a 200 px wide copy.
DIFF = """async ([a, b]) => {
    const load = (src) => new Promise((ok) => { const i = new Image(); i.onload = () => ok(i); i.src = src; });
    const [ia, ib] = await Promise.all([load(a), load(b)]);
    const w = 200, h = Math.round(200 * ia.height / ia.width);
    const px = (img) => { const c = new OffscreenCanvas(w, h), x = c.getContext('2d');
        x.drawImage(img, 0, 0, w, h); return x.getImageData(0, 0, w, h).data; };
    const pa = px(ia), pb = px(ib);
    let n = 0;
    for (let i = 0; i < pa.length; i += 4) {
        const la = pa[i] * .3 + pa[i + 1] * .59 + pa[i + 2] * .11, lb = pb[i] * .3 + pb[i + 1] * .59 + pb[i + 2] * .11;
        if (Math.abs(la - lb) > 20) n++;
    }
    return n / (w * h);
}"""


class Recorder:
    def __init__(self, page: Page, judge: Page):
        self.page, self.judge, self.frames, self.worst, self.current = page, judge, [], 0.0, ""
        self.cdp = page.context.new_cdp_session(page)
        self.cdp.on("Page.screencastFrame", self._frame)

    def _frame(self, event) -> None:
        self.frames.append(event["data"])
        self.cdp.send("Page.screencastFrameAck", {"sessionId": event["sessionId"]})

    def reload(self, name: str) -> float:
        """Loads the page again (F5), also over a slow connection: the page
        then arrives in pieces, and the browser draws what it has so far."""
        self.step(name, None)
        self.cdp.send("Network.enable")
        self.cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 150,
                                                            "downloadThroughput": 40_000, "uploadThroughput": 40_000})
        try:
            return self.step(name + " (slow network)", None)
        finally:
            self.cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0,
                                                                "downloadThroughput": -1, "uploadThroughput": -1})

    def step(self, name: str, target) -> float:
        """Clicks `target` (a selector or locator), recording what's painted."""
        locator = self.page.locator(target).first if isinstance(target, str) else target
        self.current = name
        # Scrolled there first: Playwright would do it with the click, and
        # that scrolling would look like a flash.
        if locator is not None:
            locator.scroll_into_view_if_needed()
        self.page.wait_for_timeout(400)
        before = base64.b64encode(self.page.screenshot(type="jpeg", quality=70)).decode()
        self.frames = []
        self.cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 70, "everyNthFrame": 1})
        if locator is None:
            self.page.reload(wait_until="commit")
        else:
            locator.click()
        self.page.wait_for_load_state("load")
        self.page.wait_for_timeout(900)
        self.cdp.send("Page.stopScreencast")
        after = base64.b64encode(self.page.screenshot(type="jpeg", quality=70)).decode()
        url = lambda d: "data:image/jpeg;base64," + d  # noqa: E731
        worst, worst_frame = 0.0, None
        for frame in self.frames:
            score = min(self.judge.evaluate(DIFF, [url(frame), url(before)]),
                        self.judge.evaluate(DIFF, [url(frame), url(after)]))
            if score > worst:
                worst, worst_frame = score, frame
        self.worst = max(self.worst, worst)
        flag = "FLASH" if worst > LIMIT else "ok"
        print(f"{flag:5} {worst * 100:5.1f}%  {len(self.frames):3} frames  {name}")
        if worst > LIMIT and worst_frame:
            with open(f"/out/screens/flash-{name.replace(' ', '_').replace('/', '-')}.jpg", "wb") as f:
                f.write(base64.b64decode(worst_frame))
        return worst


def login(page: Page, email: str) -> None:
    page.goto(f"{BASE}/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_load_state()


def desk(r: Recorder, page: Page, prefix: str) -> None:
    page.goto(f"{BASE}/flugbuch")
    r.reload(f"{prefix} reload Flugbuch")
    r.step(f"{prefix} calendar open", ".cal-pop > summary")
    r.step(f"{prefix} calendar previous month", ".cal-pop [aria-label='Vorheriger Monat']")
    r.step(f"{prefix} calendar next month", ".cal-pop [aria-label='Nächster Monat']")
    r.step(f"{prefix} calendar pick a day", ".cal-pop a.cal-flown")
    r.step(f"{prefix} day arrow", ".day-nav a >> nth=-1")
    page.goto(f"{BASE}/flugbuch")
    r.step(f"{prefix} panel toggle 1", "button.panel-toggle")
    r.reload(f"{prefix} reload Flugbuch with the panel toggled")
    r.step(f"{prefix} panel toggle 2", "button.panel-toggle")
    # Docked: the same button again; a drawer covers it, so its close button.
    r.step(f"{prefix} panel toggle 3", "button.panel-toggle" if prefix == "wide" else ".fb-panel .sheet-close")
    r.reload(f"{prefix} reload Flugbuch again")
    page.goto(f"{BASE}/flugbuch")
    r.step(f"{prefix} dialog Flug hinzufuegen", ".fb-toolbar >> text=Flug hinzufügen")
    r.step(f"{prefix} dialog close", "#flight-dialog [data-dialog-close] >> nth=-1")
    r.step(f"{prefix} dialog Einchecken", ".fb-toolbar >> text=Einchecken")
    r.step(f"{prefix} dialog Einchecken close", "#checkin-dialog [data-dialog-close] >> nth=-1")
    r.step(f"{prefix} dialog Auschecken", ".fb-toolbar >> text=Auschecken")
    page.keyboard.press("Escape")
    r.step(f"{prefix} Bearbeiten (page load)", "tbody tr >> text=Bearbeiten")
    r.step(f"{prefix} Bearbeiten close", "#flight-dialog [data-dialog-close] >> nth=-1")
    for label in ("Statistik", "Karte", "Hilfe", "Flugbuch"):
        r.step(f"{prefix} nav {label}", f".topbar nav >> text={label}")
    r.step(f"{prefix} nav Konto", ".topbar nav >> text=Konto")


def pilot(r: Recorder, page: Page) -> None:
    page.goto(f"{BASE}/dashboard")
    for label in ("Einchecken", "Meine Flüge", "Karte", "Heute"):
        r.step(f"phone tab {label}", f".tabbar >> text={label}")
    page.goto(f"{BASE}/statistik")
    r.step("phone statistik Verein", ".segmented >> text=Verein")
    r.step("phone statistik Meine", ".segmented >> text=Meine")
    page.goto(f"{BASE}/logbook")
    r.step("phone logbook flight", "a.record")
    r.step("phone Korrigieren open", "text=Korrigieren")
    r.step("phone konto", ".topbar .account-link")
    page.goto(f"{BASE}/flugbuch")
    r.reload("phone reload Flugbuch")
    r.step("phone flugbuch calendar open", ".cal-pop > summary")
    r.step("phone flugbuch previous month", ".cal-pop [aria-label='Vorheriger Monat']")


def admin(r: Recorder, page: Page) -> None:
    page.goto(f"{BASE}/admin/pilots")
    for path in ("/admin/gliders", "/admin/finalize", "/admin/pilots"):
        r.step(f"admin {path}", f"a[href='{path}'] >> nth=0")


def main() -> None:
    worst, failed = 0.0, False
    with sync_playwright() as p:
        browser = p.chromium.launch()
        judge = browser.new_page()
        for email, screen, flow in [("desk@test.ch", LAPTOP, lambda r, pg: desk(r, pg, "laptop")),
                                    ("desk@test.ch", WIDE, lambda r, pg: desk(r, pg, "wide")),
                                    ("pilot@test.ch", PHONE, pilot), ("admin@test.ch", LAPTOP, admin)]:
            context = browser.new_context(**screen)
            page = context.new_page()
            login(page, email)
            r = Recorder(page, judge)
            try:
                flow(r, page)
            except Exception as exc:  # noqa: BLE001 - report and go on with the next flow
                print(f"step failed: {r.current}: {exc}".splitlines()[0])
                failed = True
            worst = max(worst, r.worst)
            context.close()
        browser.close()
    # The map pages load tiles bit by bit and a drawer slides: look at the
    # saved frames rather than going by the number alone.
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
