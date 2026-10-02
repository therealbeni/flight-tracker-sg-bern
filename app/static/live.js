// Keeps a page that someone watches all day (the FDL overview) current:
// every 30 s it fetches the page again and swaps in the [data-live] part if
// anything changed. No full reload, so nothing flashes or jumps.
(() => {
    const board = document.querySelector("[data-live]");
    if (!board) return;

    async function refresh() {
        if (document.hidden) return;
        try {
            const response = await fetch(location.href, { credentials: "same-origin" });
            if (!response.ok || response.redirected) return;  // logged out: leave the page as it is
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            const fresh = page.querySelector("[data-live]");
            if (fresh && fresh.innerHTML !== board.innerHTML) board.innerHTML = fresh.innerHTML;
        } catch (error) {
            // Offline for a moment (bad reception at the field): try again next time.
        }
    }

    setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();
