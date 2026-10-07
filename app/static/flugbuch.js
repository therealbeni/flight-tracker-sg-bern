// The Flugbuch page (templates/flugbuch/day.html):
//
//  - Dialogs: the flight form, Einchecken and Auschecken open on top of the
//    flights ([data-dialog-open="id"]); the server opens one by itself after
//    a link like ?bearbeiten=12 or a form with errors (data-open-on-load).
//    Without JavaScript the links load the page with the dialog shown.
//  - The Flugdienst side panel ([data-panel-toggle]): docked next to the
//    flights on a wide screen, a drawer over them on a smaller one. Whether
//    it's open is remembered per browser.
//  - Kalender: changing the month swaps the calendar in place instead of
//    loading the page again (without JavaScript the links do that).
//  - Einchecken: "für den ganzen Tag" is preselected for aircraft usually
//    flown all day (tow plane, motor glider).
(function () {
    const page = document.querySelector("[data-flugbuch]");
    if (!page) return;

    // ---- dialogs

    function open(dialog) {
        if (dialog.matches(":modal")) return;
        if (dialog.open) dialog.removeAttribute("open");  // see flugbuch-dialog.js
        dialog.showModal();  // focuses the close button: no list or phone keyboard popping up by itself
    }

    // A dialog the server opened is a real one already (flugbuch-dialog.js).
    document.querySelectorAll("dialog").forEach((dialog) => {
        dialog.addEventListener("close", () => {
            // Leaving a flight being corrected: back to the plain day, so the
            // form is an empty "Flug hinzufügen" again.
            if (dialog.dataset.leaveTo) {
                location.assign(dialog.dataset.leaveTo);
                return;
            }
            const url = new URL(location.href);
            ["neu", "bearbeiten", "einchecken", "eingecheckt"].forEach((p) => url.searchParams.delete(p));
            history.replaceState(null, "", url);
        });
        // A click on the dimmed background closes it, like the close button.
        dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    });

    document.addEventListener("click", (event) => {
        const opener = event.target.closest("[data-dialog-open]");
        if (opener && !opener.hasAttribute("data-dialog-edit")) {
            const dialog = document.getElementById(opener.dataset.dialogOpen);
            if (dialog) {
                event.preventDefault();
                if (opener.dataset.glider) chooseAircraft(dialog, opener.dataset.glider);
                open(dialog);
            }
            return;
        }
        const closer = event.target.closest("[data-dialog-close]");
        if (closer && closer.closest("dialog")) {
            event.preventDefault();
            closer.closest("dialog").close();
        }
    });

    // ---- Flugdienst side panel

    const panel = document.getElementById("flugdienst");
    if (panel) {
        const wide = window.matchMedia("(min-width: 1800px)");  // as in flugbuch-panel.js
        function remembered() {
            try { return localStorage.getItem("flugdienst-panel"); } catch (e) { return null; }  // private window
        }

        // byHand: opened or closed by the user - only then it slides (style.css).
        function show(isOpen, remember, byHand = remember) {
            if (byHand) page.classList.add("panel-animate");
            page.classList.add("panel-js");
            page.classList.toggle("panel-open", isOpen);
            document.querySelectorAll("[data-panel-toggle][aria-controls]").forEach((b) => b.setAttribute("aria-expanded", isOpen));
            if (remember) {
                try { localStorage.setItem("flugdienst-panel", isOpen ? "open" : "closed"); } catch (e) { /* ignore */ }
            }
        }
        // Docked by default where there's room (unless closed there before);
        // a drawer stays closed until asked for. flugbuch-panel.js did this
        // already before the first paint; here for aria-expanded.
        show(wide.matches && remembered() !== "closed", false);
        document.querySelectorAll("[data-panel-toggle]").forEach((button) => {
            button.addEventListener("click", () => show(!page.classList.contains("panel-open"), true));
        });
        // A drawer over the flights closes with Escape or a click next to it.
        document.addEventListener("keydown", (event) => {
            if (event.key === "Escape" && !wide.matches && page.classList.contains("panel-open")) show(false, true);
        });
        document.addEventListener("click", (event) => {
            if (!wide.matches && page.classList.contains("panel-open") && !panel.contains(event.target)
                && !event.target.closest("[data-panel-toggle], dialog")) show(false, false, true);
        });
        wide.addEventListener("change", () => show(wide.matches && remembered() !== "closed", false));
    }

    // ---- Kalender

    document.addEventListener("click", async (event) => {
        const link = event.target.closest(".cal-pop .month-nav a[href]");
        if (!link || event.ctrlKey || event.metaKey || event.shiftKey) return;
        event.preventDefault();
        try {
            const response = await fetch(link.href, { credentials: "same-origin" });
            if (!response.ok || response.redirected) throw new Error(response.status);
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            document.querySelector(".cal-pop-body").replaceWith(page.querySelector(".cal-pop-body"));
            history.replaceState(null, "", link.href);
        } catch (error) {
            location.assign(link.href);  // offline for a moment, logged out ...: the plain way
        }
    });

    // ---- Einchecken

    function chooseAircraft(dialog, gliderId) {
        const select = dialog.querySelector("[data-checkin-aircraft]");
        if (!select) return;
        select.value = gliderId;
        select.dispatchEvent(new Event("change", { bubbles: true }));
    }

    const aircraft = document.querySelector("[data-checkin-aircraft]");
    if (aircraft) {
        const form = aircraft.form;
        const modes = Array.from(form.querySelectorAll("input[name=mode]"));
        let modeChosen = modes.some((m) => m.checked);
        modes.forEach((m) => m.addEventListener("change", () => { modeChosen = true; }));

        function suggestMode() {
            if (modeChosen) return;
            const option = aircraft.selectedOptions[0];
            const allDay = option && option.dataset.allDay === "1";
            modes.forEach((m) => { m.checked = m.value === (allDay ? "day" : "next"); });
        }

        aircraft.addEventListener("change", suggestMode);
        suggestMode();
    }
})();
