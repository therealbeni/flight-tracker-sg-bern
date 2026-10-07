// A dialog the server opened (?bearbeiten=, a form with errors) shown as a
// real dialog before the page is first drawn - not first as a box at the
// bottom of the page that jumps to the middle a moment later. Loaded right
// after the dialogs and not deferred.
(function () {
    const dialog = document.querySelector("dialog[data-open-on-load]");
    if (!dialog) return;
    // Shown by the server (works without JavaScript): reopen it as a real
    // dialog. Not with close(), which would fire "close" and leave the page.
    dialog.removeAttribute("open");
    dialog.showModal();  // focuses the close button: no list or phone keyboard popping up by itself
})();
