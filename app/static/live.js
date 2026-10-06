// Keeps a page that someone watches all day (the Flugbuch on the club PC)
// current: every 30 s it fetches the page again and swaps in each part
// marked [data-live] (by its id) that changed. No full reload, so nothing
// flashes or jumps, and an open dialog or half-filled form stays as it is.
(() => {
    if (!document.querySelector("[data-live]")) return;

    async function refresh() {
        if (document.hidden) return;
        try {
            const response = await fetch(location.href, { credentials: "same-origin" });
            if (!response.ok || response.redirected) return;  // logged out: leave the page as it is
            const page = new DOMParser().parseFromString(await response.text(), "text/html");
            document.querySelectorAll("[data-live][id]").forEach((part) => {
                const fresh = page.getElementById(part.id);
                if (fresh && fresh.innerHTML !== part.innerHTML) part.innerHTML = fresh.innerHTML;
            });
        } catch (error) {
            // Offline for a moment (bad reception at the field): try again next time.
        }
    }

    setInterval(refresh, 30000);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
})();
