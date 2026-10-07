// The Flugdienst panel docked or put away before anything of the Flugbuch
// is drawn: loaded right at its start and not deferred. Run later, the
// panel showed for a moment on every (re)load and then slid away. Same rule
// as in flugbuch.js, which does the rest.
(function () {
    const page = document.querySelector("[data-flugbuch]");
    if (!page || !page.classList.contains("has-panel")) return;
    let remembered = null;
    try { remembered = localStorage.getItem("flugdienst-panel"); } catch (e) { /* private window */ }
    page.classList.add("panel-js");
    page.classList.toggle("panel-open", window.matchMedia("(min-width: 1800px)").matches && remembered !== "closed");
})();
