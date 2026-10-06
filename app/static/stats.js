// Statistik: the month chart's tooltip. Hovering, tapping or focusing a
// month (keyboard) shows its numbers above the chart.
(function () {
    document.querySelectorAll("figure.chart").forEach((figure) => {
        const tip = figure.querySelector(".chart-tip");
        function show(col) {
            tip.textContent = col.dataset.tip;
            tip.hidden = false;
            const box = col.getBoundingClientRect();
            const frame = figure.getBoundingClientRect();
            const x = box.left + box.width / 2 - frame.left;
            tip.style.left = Math.max(0, Math.min(frame.width - tip.offsetWidth, x - tip.offsetWidth / 2)) + "px";
            figure.querySelectorAll(".col.active").forEach((c) => c.classList.remove("active"));
            col.classList.add("active");
        }
        function hide() {
            tip.hidden = true;
            figure.querySelectorAll(".col.active").forEach((c) => c.classList.remove("active"));
        }
        figure.querySelectorAll(".col").forEach((col) => {
            col.addEventListener("pointerenter", () => show(col));
            col.addEventListener("focus", () => show(col));
            col.addEventListener("click", () => show(col));
            col.addEventListener("blur", hide);
        });
        figure.querySelector("svg").addEventListener("pointerleave", hide);
    });
})();
