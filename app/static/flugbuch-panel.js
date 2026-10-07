// The Flugdienst panel docked or put away before the page is first drawn:
// loaded right after the panel and not deferred, so it doesn't show below
// the flights for a moment and then jump into place. Same rule as in
// flugbuch.js (which does the rest).
(function () {
    const page = document.querySelector("[data-flugbuch]");
    if (!page) return;
    let remembered = null;
    try { remembered = localStorage.getItem("flugdienst-panel"); } catch (e) { /* private window */ }
    page.classList.add("panel-js");
    page.classList.toggle("panel-open", window.matchMedia("(min-width: 1800px)").matches && remembered !== "closed");
})();
