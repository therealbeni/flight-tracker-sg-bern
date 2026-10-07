// Maps, drawn with Leaflet on the glider chart (Segelflugkarte, BAZL via
// swisstopo; switchable to the national map for detail):
//  - Karte (templates/karte/live.html, [data-karte-live]): the club's aircraft
//    in the air, asked for every 5 s while the page is visible.
//  - A flight's page (flights/detail.html, [data-track]): its track and
//    barogram, with a slider and playback.
// Data from routers/karte.py; each point is a row
// [unix time, lat, lon, altitude, ground (or null), km/h, m/s (or null)].
(function () {
    const T = 0, LAT = 1, LON = 2, ALT = 3, GND = 4, V = 5, VZ = 6;
    const COLORS = ["#d9480f", "#1971c2", "#2f9e44", "#ae3ec9", "#c2255c", "#0c8599", "#e67700", "#5c940d"];
    const TRAIL_COLOR = "#1d1d1f";  // the replay; stands out on the chart's coloured airspaces
    const LSZB = [46.9144, 7.499];
    const clock = new Intl.DateTimeFormat("de-CH", { timeZone: "Europe/Zurich", hour: "2-digit", minute: "2-digit" });
    const clockSeconds = new Intl.DateTimeFormat("de-CH", {
        timeZone: "Europe/Zurich", hour: "2-digit", minute: "2-digit", second: "2-digit",
    });
    const time = (t) => clock.format(new Date(t * 1000));
    const timeSeconds = (t) => clockSeconds.format(new Date(t * 1000));

    const SWISSTOPO = '© <a href="https://www.swisstopo.admin.ch/" target="_blank" rel="noopener">swisstopo</a>';
    const WMTS = "https://wmts.geo.admin.ch/1.0.0/{layer}/default/current/3857/{z}/{x}/{y}.{ext}";
    const BACKGROUNDS = {
        // The chart is made for 1:300 000: tiles up to zoom 12, enlarged beyond.
        "Segelflugkarte": { layer: "ch.bazl.segelflugkarte", ext: "png", maxNativeZoom: 12, maxZoom: 14 },
        "Landeskarte": { layer: "ch.swisstopo.pixelkarte-farbe", ext: "jpeg", maxZoom: 18 },
    };

    function makeMap(element) {
        const map = L.map(element).setView(LSZB, 10);
        map.attributionControl.setPrefix(false);
        const layers = {};
        Object.entries(BACKGROUNDS).forEach(([name, options]) => {
            layers[name] = L.tileLayer(WMTS, { ...options, attribution: SWISSTOPO });
        });
        // The background picked last time, in this browser.
        let chosen = null;
        try { chosen = localStorage.getItem("karte-hintergrund"); } catch (e) { /* private window */ }
        (layers[chosen] || layers.Segelflugkarte).addTo(map);
        L.control.layers(layers, null, { position: "topright" }).addTo(map);
        map.on("baselayerchange", (event) => {
            try { localStorage.setItem("karte-hintergrund", event.name); } catch (e) { /* ignore */ }
        });
        return map;
    }

    // "1850 m (1340 m ü. G.)": above sea level, and above the ground if known.
    function height(p) {
        const agl = p[GND] === null ? "" : ` (${Math.max(0, p[ALT] - p[GND])} m ü. G.)`;
        return `${p[ALT]} m${agl}`;
    }
    function vario(p) {
        if (p[VZ] === null) return "";
        return `${p[VZ] > 0 ? "+" : p[VZ] < 0 ? "−" : "±"}${Math.abs(p[VZ]).toFixed(1)} m/s`;
    }

    // An aircraft on the map: a dot with its registration (colour via CSS, the
    // page's security policy allows no inline styles).
    function aircraftMarker(label, color) {
        const icon = L.divIcon({ className: "ac-marker", html: '<span class="ac-dot"></span><span class="ac-label"></span>',
                                 iconSize: [14, 14], iconAnchor: [7, 7] });
        const marker = L.marker(LSZB, { icon, keyboard: false });
        marker.on("add", () => {
            const element = marker.getElement();
            element.style.setProperty("--ac", color);
            element.querySelector(".ac-label").textContent = label;
        });
        return marker;
    }

    // ---------------------------------------------------------------- live

    function live(root) {
        const map = makeMap(root.querySelector("[data-map]"));
        const list = root.querySelector("[data-karte-list]");
        const empty = root.querySelector("[data-karte-empty]");
        const status = root.querySelector("[data-karte-status]");
        const flights = new Map();  // flight id -> {points, line, marker, item}
        let since = null;  // ask for the positions from shortly before the last answer
        let fitted = false;
        let nextColor = 0;

        function add(a) {
            const color = COLORS[nextColor++ % COLORS.length];
            const f = { points: [], color };
            f.casing = L.polyline([], { color: "#fff", weight: 6, opacity: 0.8 }).addTo(map);
            f.line = L.polyline([], { color, weight: 3 }).addTo(map);
            f.marker = aircraftMarker(a.registration, color).addTo(map);
            f.item = document.createElement("li");
            f.item.innerHTML = '<button type="button" class="karte-item"><span class="ac-swatch"></span>'
                + '<span class="karte-item-text"><strong></strong> <span class="karte-pilot"></span>'
                + '<span class="karte-facts"></span></span></button>';
            f.item.querySelector(".ac-swatch").style.setProperty("--ac", color);
            f.item.querySelector("strong").textContent = a.registration;
            f.item.querySelector("button").addEventListener("click", () => {
                const last = f.points[f.points.length - 1];
                if (last) map.setView([last[LAT], last[LON]], Math.max(map.getZoom(), 12));
            });
            f.marker.on("click", () => { location.href = `/flights/${a.id}`; });
            list.append(f.item);
            flights.set(a.id, f);
            return f;
        }

        function update(f, a) {
            const last = f.points.length ? f.points[f.points.length - 1][T] : -Infinity;
            const fresh = a.points.filter((p) => p[T] > last);
            if (fresh.length) {
                f.points.push(...fresh);
                fresh.forEach((p) => { f.casing.addLatLng([p[LAT], p[LON]]); f.line.addLatLng([p[LAT], p[LON]]); });
            }
            const p = f.points[f.points.length - 1];
            if (!p) return;
            f.marker.setLatLng([p[LAT], p[LON]]);
            f.marker.getElement()?.querySelector(".ac-label")?.replaceChildren(`${a.registration} ${p[ALT]} m`);
            f.item.querySelector(".karte-pilot").textContent = a.pilot || "Pilot unbekannt";
            f.item.querySelector(".karte-facts").textContent =
                `Start ${a.takeoff || "?"} · ${height(p)} · ${vario(p)} · ${p[V]} km/h · ${time(p[T])}`;
        }

        async function refresh() {
            if (document.hidden) return;
            const url = new URL(root.dataset.karteLive, location.href);
            if (since !== null) {
                url.searchParams.set("seit", since);
                url.searchParams.set("bekannt", [...flights.keys()].join(","));
            }
            let data;
            try {
                const response = await fetch(url, { credentials: "same-origin" });
                if (!response.ok || response.redirected) {
                    status.textContent = "Nicht mehr angemeldet - Seite neu laden.";
                    return;
                }
                data = await response.json();
            } catch (error) {
                status.textContent = "Keine Verbindung - gleich nochmals.";
                return;
            }
            // Positions can reach the server out of order: overlap a little,
            // update() skips the ones we have.
            since = data.now - 120;
            const seen = new Set();
            data.aircraft.forEach((a) => {
                seen.add(a.id);
                update(flights.get(a.id) || add(a), a);
            });
            flights.forEach((f, id) => {
                if (seen.has(id)) return;
                f.casing.remove(); f.line.remove(); f.marker.remove(); f.item.remove();
                flights.delete(id);
            });
            const n = flights.size;
            empty.hidden = n > 0;
            status.textContent = `${n === 0 ? "Niemand" : n === 1 ? "1 Flugzeug" : n + " Flugzeuge"} in der Luft`
                + ` · Stand ${timeSeconds(data.now)}`;
            if (!fitted && n > 0) {
                fitted = true;
                const bounds = L.latLngBounds([...flights.values()].flatMap((f) => f.line.getLatLngs()));
                bounds.extend(LSZB);
                map.fitBounds(bounds, { padding: [30, 30], maxZoom: 12 });
            }
        }

        refresh();
        setInterval(refresh, 5000);
        document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
    }

    // -------------------------------------------------------------- replay

    async function replay(root) {
        const map = makeMap(root.querySelector("[data-map]"));
        const slider = root.querySelector("[data-replay-slider]");
        const playButton = root.querySelector("[data-replay-play]");
        const speedButton = root.querySelector("[data-replay-speed]");
        const readout = root.querySelector("[data-replay-readout]");
        const svg = root.querySelector("[data-barogram]");

        let data;
        try {
            const response = await fetch(root.dataset.track, { credentials: "same-origin" });
            data = await response.json();
        } catch (error) {
            readout.textContent = "Der Flugweg konnte nicht geladen werden.";
            return;
        }
        const pts = data.points;
        if (pts.length < 2) {
            readout.textContent = "Für diesen Flug gibt es keinen Flugweg.";
            return;
        }
        const latLngs = pts.map((p) => [p[LAT], p[LON]]);
        L.polyline(latLngs, { color: "#fff", weight: 6, opacity: 0.8 }).addTo(map);  // white casing
        const line = L.polyline(latLngs, { color: TRAIL_COLOR, weight: 3 }).addTo(map);
        L.circleMarker([pts[0][LAT], pts[0][LON]], { radius: 5, color: "#2f9e44", fillOpacity: 1 }).addTo(map)
            .bindTooltip(`Start ${time(pts[0][T])}`);
        const end = pts[pts.length - 1];
        L.circleMarker([end[LAT], end[LON]], { radius: 5, color: "#23272e", fillOpacity: 1 }).addTo(map)
            .bindTooltip(data.landed ? `Landung ${time(end[T])}` : `Zuletzt gesehen ${time(end[T])}`);
        const marker = aircraftMarker(data.registration, "#d9480f").addTo(map);
        map.fitBounds(line.getBounds(), { padding: [20, 20] });

        const barogram = drawBarogram(svg, pts, (i) => show(i, true));
        slider.max = pts.length - 1;

        let index = 0;
        function show(i, stop) {
            if (stop) pause();
            index = Math.max(0, Math.min(pts.length - 1, i));
            const p = pts[index];
            marker.setLatLng([p[LAT], p[LON]]);
            slider.value = index;
            readout.textContent = [timeSeconds(p[T]), height(p), vario(p), `${p[V]} km/h`].filter(Boolean).join(" · ");
            barogram.cursor(index);
        }
        slider.addEventListener("input", () => show(Number(slider.value), true));

        // Playback: flight time runs `speed` times faster than real time.
        const SPEEDS = [30, 60, 120, 300];
        let speed = 60, playing = false, flightTime = 0, lastFrame = 0;
        speedButton.addEventListener("click", () => {
            speed = SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length];
            speedButton.textContent = `${speed}×`;
        });
        function pause() {
            playing = false;
            playButton.classList.remove("is-playing");
            playButton.setAttribute("aria-label", "Abspielen");
        }
        function frame(now) {
            if (!playing) return;
            flightTime += ((now - lastFrame) / 1000) * speed;
            lastFrame = now;
            let i = index;
            while (i < pts.length - 1 && pts[i + 1][T] <= flightTime) i++;
            show(i, false);
            if (i >= pts.length - 1) pause();
            else requestAnimationFrame(frame);
        }
        playButton.addEventListener("click", () => {
            if (playing) { pause(); return; }
            if (index >= pts.length - 1) index = 0;
            playing = true;
            flightTime = pts[index][T];
            lastFrame = performance.now();
            playButton.classList.add("is-playing");
            playButton.setAttribute("aria-label", "Anhalten");
            requestAnimationFrame(frame);
        });
        show(0, false);
    }

    // Altitude over time (line) above the terrain below (filled), with a
    // cursor; clicking or dragging on it picks the moment. Redrawn on resize.
    function drawBarogram(svg, pts, pick) {
        const NS = "http://www.w3.org/2000/svg";
        const H = 150, PAD = { left: 44, right: 8, top: 8, bottom: 20 };
        const t0 = pts[0][T], t1 = pts[pts.length - 1][T];
        const grounds = pts.map((p) => p[GND]).filter((g) => g !== null);
        const low = Math.floor(Math.min(...pts.map((p) => p[ALT]), ...grounds) / 500) * 500;
        const high = Math.ceil(Math.max(...pts.map((p) => p[ALT])) / 500) * 500 || low + 500;
        let width = 0, cursorLine = null, current = 0;

        const el = (name, attrs, parent) => {
            const node = document.createElementNS(NS, name);
            Object.entries(attrs).forEach(([k, v]) => node.setAttribute(k, v));
            (parent || svg).append(node);
            return node;
        };
        const x = (t) => PAD.left + ((t - t0) / Math.max(1, t1 - t0)) * (width - PAD.left - PAD.right);
        const y = (alt) => PAD.top + (1 - (alt - low) / (high - low)) * (H - PAD.top - PAD.bottom);

        function draw() {
            width = svg.clientWidth;
            if (!width) return;
            svg.replaceChildren();
            svg.setAttribute("viewBox", `0 0 ${width} ${H}`);
            const step = high - low > 2500 ? 1000 : 500;
            for (let a = low; a <= high; a += step) {
                el("line", { x1: PAD.left, x2: width - PAD.right, y1: y(a), y2: y(a), class: "baro-grid" });
                el("text", { x: PAD.left - 6, y: y(a) + 4, class: "baro-label", "text-anchor": "end" }).textContent = `${a}`;
            }
            const hourStep = (t1 - t0) > 4 * 3600 ? 3600 : (t1 - t0) > 3600 ? 1800 : 600;
            for (let t = Math.ceil(t0 / hourStep) * hourStep; t <= t1; t += hourStep) {
                el("text", { x: x(t), y: H - 4, class: "baro-label", "text-anchor": "middle" }).textContent = time(t);
            }
            const withGround = pts.filter((p) => p[GND] !== null);
            if (withGround.length > 1) {
                const d = withGround.map((p, i) => `${i ? "L" : "M"}${x(p[T]).toFixed(1)},${y(p[GND]).toFixed(1)}`).join("");
                el("path", { d: `${d}L${x(withGround[withGround.length - 1][T]).toFixed(1)},${y(low)}`
                    + `L${x(withGround[0][T]).toFixed(1)},${y(low)}Z`, class: "baro-ground" });
            }
            el("path", { d: pts.map((p, i) => `${i ? "L" : "M"}${x(p[T]).toFixed(1)},${y(p[ALT]).toFixed(1)}`).join(""),
                         class: "baro-altitude" });
            cursorLine = el("line", { y1: PAD.top, y2: H - PAD.bottom, class: "baro-cursor" });
            cursor(current);
        }
        function cursor(i) {
            current = i;
            if (!cursorLine) return;
            const cx = x(pts[i][T]).toFixed(1);
            cursorLine.setAttribute("x1", cx);
            cursorLine.setAttribute("x2", cx);
        }
        function pickAt(event) {
            const box = svg.getBoundingClientRect();
            const t = t0 + ((event.clientX - box.left - PAD.left) / (width - PAD.left - PAD.right)) * (t1 - t0);
            let lo = 0, hi = pts.length - 1;
            while (lo < hi) { const mid = (lo + hi) >> 1; if (pts[mid][T] < t) lo = mid + 1; else hi = mid; }
            pick(lo);
        }
        svg.addEventListener("pointerdown", (event) => { svg.setPointerCapture(event.pointerId); pickAt(event); });
        svg.addEventListener("pointermove", (event) => { if (svg.hasPointerCapture(event.pointerId)) pickAt(event); });
        new ResizeObserver(draw).observe(svg);
        draw();
        return { cursor };
    }

    const liveRoot = document.querySelector("[data-karte-live]");
    if (liveRoot) live(liveRoot);
    const trackRoot = document.querySelector("[data-track]");
    if (trackRoot) replay(trackRoot);
})();
