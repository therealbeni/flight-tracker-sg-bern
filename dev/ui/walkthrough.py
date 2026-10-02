"""Walks through every flow in a real browser on a phone-sized screen, like a
pilot would, and fails on anything broken: errors, English leftovers, pages
wider than the phone, JavaScript errors. Screenshots go to dev/ui/screens/.

Run with dev/ui/run.sh (starts the app with test data from serve.py).
"""

import re
import sys

from playwright.sync_api import Page, expect, sync_playwright

# The browser shares the app container's network, so the app is on localhost -
# a secure origin, which the camera API requires.
BASE = "http://localhost:8000"
OUT = "/out/screens"
PASSWORD = "testtest"
DESKTOP = {"viewport": {"width": 1366, "height": 768}}
PHONE = {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True}
# English words that must not show up on a German page (whole words only).
ENGLISH = re.compile(r"\b(Log in|Log out|Sign up|Claim|Flight|Logbook|Glider|Today|Save|Verify|Pending|Unknown)\b")

problems: list[str] = []


def check_page(page: Page, name: str) -> None:
    page.screenshot(path=f"{OUT}/{name}.png", full_page=True)
    text = page.inner_text("body")
    if match := ENGLISH.search(text):
        problems.append(f"{name}: English text '{match.group(0)}'")
    overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
    if overflow > 1:
        problems.append(f"{name}: page is {overflow}px wider than the phone screen")
    covered = page.evaluate("""() => {
        const bar = document.querySelector('.tabbar');
        if (!bar || getComputedStyle(bar).display === 'none') return false;
        window.scrollTo(0, document.documentElement.scrollHeight);
        const last = document.querySelector('.content').lastElementChild;
        return last && last.getBoundingClientRect().bottom > bar.getBoundingClientRect().top + 1;
    }""")
    if covered:
        problems.append(f"{name}: end of the page is hidden under the tab bar")
    if not page.context.pages[0].evaluate("matchMedia('(pointer: coarse)').matches"):
        return  # tap target sizes matter on touch screens only
    small = page.evaluate("""() => [...document.querySelectorAll('button, a.btn, .glider-pick, .tabbar a')]
        .filter(e => e.offsetParent !== null)
        .filter(e => { const r = e.getBoundingClientRect(); return r.height < 40 || r.width < 40; })
        .map(e => e.innerText.trim() || e.getAttribute('aria-label') || e.className).slice(0, 5)""")
    if small:
        problems.append(f"{name}: tap targets smaller than 40px: {small}")


def login(page: Page, email: str) -> None:
    page.goto(f"{BASE}/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/(dashboard|flugbuch)$"))


def pick(form, name: str, typed: str, enter: bool = True) -> None:
    """Chooses in a searchable dropdown like a person: type, then Enter."""
    combo = form.locator(f".combo:has(select[name={name}]) .combo-input")
    combo.click()
    combo.fill(typed)
    expect(form.locator(f".combo:has(select[name={name}]) .combo-list")).to_be_visible()
    if enter:
        combo.press("Enter")


def pilot_flow(page: Page) -> None:
    page.goto(f"{BASE}/login")
    check_page(page, "01-login")
    login(page, "pilot@test.ch")
    expect(page.locator("body")).to_contain_text("Hallo Pia")
    expect(page.locator("body")).to_contain_text("Schlepp D-EDUY")
    check_page(page, "02-heute")

    page.click(".tabbar >> text=Einchecken")
    check_page(page, "03-einchecken")
    page.click("#scan-start")
    # The fake camera shows HB-1811's QR code: the scanner must open its page.
    page.wait_for_url(re.compile(r"/claim/11111111-2222-3333-4444-555555555555$"), timeout=15000)
    check_page(page, "04-scan-treffer")
    page.click("text=Für den nächsten Start einchecken")
    expect(page.locator("h1")).to_contain_text("HB-1811 ist eingecheckt")
    check_page(page, "05-eingecheckt")

    page.goto(f"{BASE}/dashboard")
    expect(page.locator(".claim-card")).to_contain_text("HB-1811")
    page.click(".claim-card >> text=Freigeben")
    expect(page).to_have_url(f"{BASE}/dashboard")
    expect(page.locator(".claim-card")).to_have_count(0)

    page.click(".content a[href='/hilfe']")
    page.click("text=Ich habe vergessen einzuchecken.")
    expect(page.locator("details[open]")).to_contain_text("wähle dich als Pilot")
    check_page(page, "05b-hilfe")

    page.click(".tabbar >> text=Meine Flüge")
    check_page(page, "06-meine-fluege")
    page.click(".record >> text=HB-1811")
    expect(page.locator("body")).to_contain_text("F-Schlepp")
    check_page(page, "07-flug")
    page.click("text=Korrigieren")
    expect(page.locator("fieldset.tow-fields")).to_be_visible()
    check_page(page, "07b-flug-korrigieren")


def tow_pilot_flow(page: Page) -> None:
    login(page, "tow@test.ch")
    expect(page.locator(".claim-card")).to_contain_text("D-EDUY")
    expect(page.locator(".claim-card")).to_contain_text("ganzen Tag")
    page.goto(f"{BASE}/claim")
    expect(page.locator(".glider-pick", has_text="D-EDUY")).to_contain_text("Du bist eingecheckt")
    page.click(".glider-pick >> text=D-EDUY")
    buttons = page.locator("form.stack button")
    expect(buttons.first).to_have_text("Für den ganzen Tag einchecken")
    check_page(page, "10-schlepp-einchecken")


def pilot_checkout_flow(page: Page) -> None:
    login(page, "pilot@test.ch")
    page.click("text=Auschecken")
    expect(page.locator("h1")).to_contain_text("Auschecken: Pia Pilot")
    check_page(page, "08-auschecken")
    page.click("text=Flüge bestätigen und auschecken")
    expect(page).to_have_url(re.compile(r"/flugbuch"))
    check_page(page, "09-flugbuch-handy")


def club_pc_flow(page: Page) -> None:
    login(page, "desk@test.ch")  # the Flugdienstleiter's overview of the day
    expect(page.locator("h1")).to_contain_text("Flugdienst")
    expect(page.locator(".strip-air")).to_contain_text("HB-3131")
    expect(page.locator(".strip-claimed")).to_contain_text("Toni Schlepp")
    expect(page.locator(".topbar nav")).not_to_contain_text("Einchecken")
    check_page(page, "29-fdl-uebersicht")
    page.click(".topbar nav >> text=Flugbuch")
    expect(page.locator("h1")).to_contain_text("Flugbuch")
    check_page(page, "30-flugbuch")

    # A guest flight the tracker couldn't see.
    form = page.locator("form#erfassen")
    pick(form, "glider_id", "3131")
    form.locator("select[name=launch_method]").select_option("F")
    # F-Schlepp: tow plane and the tow pilot checked in on it are filled in.
    tow = form.locator("fieldset.tow-fields")
    expect(tow).to_be_visible()
    expect(tow.locator("select[name=tow_glider_id]")).to_have_value(re.compile(r"\d+"))
    expect(tow.locator(".combo-input")).to_have_value("Toni Schlepp")
    # The motor glider can tow too; nobody is checked in on it, so no tow pilot is suggested.
    tow.locator("select[name=tow_glider_id]").select_option(label="HB-2377 (H36 Dimona)")
    expect(tow.locator(".combo-input")).to_have_value("-- unbekannt --")
    tow.locator("select[name=tow_glider_id]").select_option(label="D-EDUY (Robin DR400)")
    expect(tow.locator(".combo-input")).to_have_value("Toni Schlepp")
    form.locator("select[name=launch_method]").select_option("W")
    expect(tow).to_be_hidden()
    form.locator("select[name=launch_method]").select_option("F")
    form.locator("select[name=flight_type]").select_option("SF")
    # Pilot "Unbekannt" shows the name field; a guest Begleiter too, "Keiner" hides it.
    expect(form.locator("input[name=pilot_name]")).to_be_visible()
    form.locator("input[name=pilot_name]").fill("Gast Hans Muster")
    expect(form.locator("input[name=companion_name]")).to_be_hidden()
    pick(form, "companion_id", "gast", enter=False)
    check_page(page, "34-suche")
    page.keyboard.press("Enter")
    expect(form.locator("input[name=companion_name]")).to_be_visible()
    pick(form, "companion_id", "pia")
    expect(form.locator("input[name=companion_name]")).to_be_hidden()
    expect(form.locator("select[name=companion_id]")).to_have_value(re.compile(r"\d+"))
    form.locator("select[name=billing]").select_option("guest")
    form.locator("input[name=takeoff_time]").fill("0915")  # no colon on a number pad
    form.locator("input[name=landing_time]").fill("09.05")  # typo: before takeoff
    form.locator("button[type=submit]").click()
    expect(page.locator(".error")).to_contain_text("Die Landung muss nach dem Start sein.")
    expect(form.locator("input[name=pilot_name]")).to_have_value("Gast Hans Muster")  # nothing lost
    expect(form.locator("fieldset.tow-fields")).to_be_visible()
    check_page(page, "31-fehler")
    page.locator("form#erfassen input[name=landing_time]").fill("09:55")
    page.locator("form#erfassen button[type=submit]").click()
    row = page.locator("tbody tr", has_text="Gast Hans Muster")
    expect(row).to_contain_text("Pia Pilot")
    expect(row).to_contain_text("SF")
    expect(row).to_contain_text("Gastflug")
    expect(row).to_contain_text("D-EDUY, Toni Schlepp")
    expect(row.locator("td.num").nth(2)).to_have_text("40")

    # Fill in the missing pilot of HB-3131's tracked flight.
    page.locator("tbody tr", has_text="in der Luft").locator("text=Bearbeiten").click()
    expect(page.locator("form#erfassen h2")).to_contain_text("Flug bearbeiten")
    expect(page.locator("form#erfassen select[name=glider_id]")).to_have_count(0)  # the aircraft is fixed
    pick(page.locator("form#erfassen"), "pilot_id", "toni")
    page.locator("form#erfassen button[type=submit]").click()
    expect(page.locator("tbody tr", has_text="in der Luft")).to_contain_text("Toni Schlepp")
    check_page(page, "32-flugbuch-bearbeitet")

    page.locator(".checkout-names >> text=Pia Pilot").click()
    expect(page.locator("h1")).to_contain_text("Auschecken: Pia Pilot")
    check_page(page, "33-auschecken-pc")


def admin_flow(page: Page) -> None:
    login(page, "admin@test.ch")
    page.click(".tabbar >> text=Verwaltung")
    check_page(page, "20-piloten")
    page.locator("tr", has_text="Pia Pilot").locator("text=Als Pilot ansehen").click()
    expect(page.locator(".view-as-banner")).to_contain_text("Pia Pilot")
    check_page(page, "21-als-pilot")
    page.click("text=Zurück zu meinem Konto")
    expect(page).to_have_url(f"{BASE}/admin/pilots")
    for path, name in [("/admin/gliders", "22-flugzeuge"), ("/admin/airfields", "23-flugplaetze"),
                       ("/admin/finalize", "24-abschliessen")]:
        page.goto(BASE + path)
        check_page(page, name)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.launch(args=[
            "--use-fake-ui-for-media-stream",
            "--use-fake-device-for-media-stream",
            "--use-file-for-fake-video-capture=/out/qr-camera.y4m",
        ])
        for flow, screen in ((pilot_flow, PHONE), (pilot_checkout_flow, PHONE), (tow_pilot_flow, PHONE),
                             (admin_flow, PHONE), (club_pc_flow, DESKTOP)):
            # Reduced motion: no page cross-fade, so screenshots show the finished page.
            context = browser.new_context(**screen, locale="de-CH", timezone_id="Europe/Zurich",
                                          reduced_motion="reduce")
            context.grant_permissions(["camera"], origin=BASE)
            page = context.new_page()
            page.on("pageerror", lambda e, f=flow.__name__: problems.append(f"{f}: JavaScript error {e}"))
            # 400 = a form with a validation message, which the flows provoke on purpose.
            page.on("console", lambda m, f=flow.__name__: m.type == "error" and "status of 400" not in m.text
                    and problems.append(f"{f}: console {m.text}"))
            try:
                flow(page)
            except Exception as exc:  # noqa: BLE001 - report every flow, not just the first failure
                page.screenshot(path=f"{OUT}/FAILED-{flow.__name__}.png", full_page=True)
                problems.append(f"{flow.__name__}: {exc}")
            context.close()
        browser.close()
    print("\n".join(problems) if problems else "All flows OK.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
