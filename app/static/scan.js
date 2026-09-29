// QR scanner for checking in: opens the camera, and as soon as it sees the QR
// code of one of our aircraft, goes to that aircraft's check-in page.
//
// Uses the browser's BarcodeDetector where it exists (Android/Chrome) and the
// bundled jsQR library otherwise (iPhone/Safari). Needs HTTPS for the camera.
(function () {
    const button = document.getElementById("scan-start");
    const overlay = document.getElementById("scan-overlay");
    if (!button || !overlay) return;

    const video = overlay.querySelector("video");
    const message = overlay.querySelector(".scan-message");
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("2d", { willReadFrequently: true });
    let stream = null;
    let detector = null;
    let running = false;

    // Only our own check-in links count; the token is all we take from the
    // code, and we always stay on this site (a QR code could point anywhere).
    function checkInPath(text) {
        try {
            const match = new URL(text).pathname.match(/^\/claim\/([A-Za-z0-9-]{8,64})$/);
            return match ? "/claim/" + match[1] : null;
        } catch (e) {
            return null;
        }
    }

    function show(text) {
        message.textContent = text;
    }

    async function start() {
        overlay.hidden = false;
        document.body.classList.add("scanning");
        show("Halte die Kamera auf den QR-Code am Flugzeug.");
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            show("Dein Browser kann die Kamera hier nicht öffnen. Scanne den Code mit der Kamera-App oder wähle das Flugzeug in der Liste.");
            return;
        }
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
        } catch (e) {
            show("Kein Zugriff auf die Kamera. Erlaube den Zugriff in den Browser-Einstellungen, oder wähle das Flugzeug in der Liste.");
            return;
        }
        video.srcObject = stream;
        await video.play();
        if ("BarcodeDetector" in window) {
            try {
                detector = new BarcodeDetector({ formats: ["qr_code"] });
            } catch (e) {
                detector = null;
            }
        }
        running = true;
        requestAnimationFrame(scan);
    }

    function stop() {
        running = false;
        if (stream) stream.getTracks().forEach((track) => track.stop());
        stream = null;
        overlay.hidden = true;
        document.body.classList.remove("scanning");
    }

    async function decode() {
        if (detector) {
            const codes = await detector.detect(video);
            return codes.length ? codes[0].rawValue : null;
        }
        const width = video.videoWidth;
        const height = video.videoHeight;
        if (!width || !height) return null;
        canvas.width = width;
        canvas.height = height;
        context.drawImage(video, 0, 0, width, height);
        const code = jsQR(context.getImageData(0, 0, width, height).data, width, height, { inversionAttempts: "dontInvert" });
        return code ? code.data : null;
    }

    let lastForeign = null;

    async function scan() {
        if (!running) return;
        try {
            const text = await decode();
            if (text) {
                const path = checkInPath(text);
                if (path) {
                    show("Gefunden!");
                    if (navigator.vibrate) navigator.vibrate(80);
                    stop();
                    window.location.href = path;
                    return;
                }
                if (text !== lastForeign) {
                    lastForeign = text;
                    show("Das ist kein QR-Code von einem Vereinsflugzeug.");
                }
            }
        } catch (e) {
            // A single bad frame is not a problem; keep scanning.
        }
        setTimeout(() => requestAnimationFrame(scan), 150);
    }

    button.addEventListener("click", start);
    overlay.querySelector(".scan-close").addEventListener("click", stop);
    document.addEventListener("keydown", (event) => { if (event.key === "Escape" && running) stop(); });
})();
