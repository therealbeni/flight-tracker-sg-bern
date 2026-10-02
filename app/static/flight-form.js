// The flight form (templates/flights/_fields.html):
//
//  - <select data-search>: a search field in front of long lists (pilots):
//    type a few letters of the name, pick from what matches. The <select>
//    stays in the form (hidden) and is what gets submitted, so without
//    JavaScript the plain dropdown still works.
//  - data-show-if="name=value": the element is only shown while the form
//    field `name` has that value (e.g. the guest's name field for "Gast").
//  - Schleppflugzeug: picking another tow aircraft suggests whoever is
//    checked in on it as tow pilot, unless a tow pilot was chosen by hand.
(function () {
    // "Zürcher" is found with "zurcher", "Müller" with "muller".
    function normalize(text) {
        return text.normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
    }

    let comboCount = 0;

    function makeSearchable(select) {
        const id = "combo-" + (++comboCount);
        const wrap = document.createElement("div");
        wrap.className = "combo";
        const input = document.createElement("input");
        input.type = "text";
        input.className = "combo-input";
        input.autocomplete = "off";
        input.spellcheck = false;
        input.placeholder = "Name eintippen oder wählen";
        input.setAttribute("role", "combobox");
        input.setAttribute("aria-autocomplete", "list");
        input.setAttribute("aria-expanded", "false");
        input.setAttribute("aria-controls", id);
        const list = document.createElement("ul");
        list.className = "combo-list";
        list.id = id;
        list.setAttribute("role", "listbox");
        list.hidden = true;

        select.parentNode.insertBefore(wrap, select);
        wrap.append(input, list, select);
        select.hidden = true;
        select.tabIndex = -1;
        // A hidden required field blocks submitting without saying why;
        // the server checks it and answers with a message instead.
        select.required = false;

        const options = Array.from(select.options).map((o) => ({ value: o.value, text: o.text, key: normalize(o.text) }));
        let shown = [];
        let active = -1;

        function selectedText() {
            const o = select.options[select.selectedIndex];
            return o ? o.text : "";
        }

        function render(filter) {
            const query = normalize(filter);
            const isMember = (o) => /^\d+$/.test(o.value);
            // Nobody matches (e.g. a guest's name typed): offer the special
            // entries (Unbekannt, Keiner, Gast) instead of an empty list.
            const matches = options.filter((o) => o.key.includes(query));
            shown = matches.length ? matches : options.filter((o) => !isMember(o));
            list.replaceChildren(...shown.map((o, i) => {
                const li = document.createElement("li");
                li.id = id + "-" + i;
                li.setAttribute("role", "option");
                li.textContent = o.text;
                if (o.value === select.value) li.setAttribute("aria-selected", "true");
                // mousedown, not click: must happen before the input's blur.
                li.addEventListener("mousedown", (e) => { e.preventDefault(); choose(o); });
                // The list sits inside the field's <label>: a click would
                // focus the input again and reopen the list.
                li.addEventListener("click", (e) => e.preventDefault());
                return li;
            }));
            highlight(query ? 0 : shown.findIndex((o) => o.value === select.value));
        }

        function highlight(index) {
            active = index;
            Array.from(list.children).forEach((li, i) => li.classList.toggle("active", i === index));
            const li = list.children[index];
            if (li) {
                input.setAttribute("aria-activedescendant", li.id);
                li.scrollIntoView({ block: "nearest" });
            } else {
                input.removeAttribute("aria-activedescendant");
            }
        }

        function open(filter) {
            render(filter);
            list.hidden = false;
            input.setAttribute("aria-expanded", "true");
        }

        function close() {
            list.hidden = true;
            input.setAttribute("aria-expanded", "false");
            input.value = selectedText();
        }

        function choose(option) {
            select.value = option.value;
            select.dispatchEvent(new Event("change", { bubbles: true }));
            close();
        }

        input.value = selectedText();
        input.addEventListener("focus", () => { input.select(); open(""); });
        input.addEventListener("click", () => { if (list.hidden) open(""); });
        input.addEventListener("input", () => open(input.value));
        input.addEventListener("blur", close);
        input.addEventListener("keydown", (e) => {
            if (e.key === "ArrowDown" || e.key === "ArrowUp") {
                e.preventDefault();
                if (list.hidden) return open("");
                const step = e.key === "ArrowDown" ? 1 : -1;
                highlight(Math.max(0, Math.min(shown.length - 1, active + step)));
            } else if (e.key === "Enter") {
                if (!list.hidden) {
                    e.preventDefault();  // pick, don't submit the form
                    if (shown[active]) choose(shown[active]);
                    else close();
                }
            } else if (e.key === "Escape") {
                close();
            }
        });
        // The form's reset button or other code changing the select.
        select.addEventListener("change", () => { if (list.hidden) input.value = selectedText(); });
    }

    function setUpShowIf(form) {
        const rules = Array.from(form.querySelectorAll("[data-show-if]")).map((el) => {
            const [name, value] = el.dataset.showIf.split("=");
            return { el, name, value };
        });
        if (!rules.length) return;
        function update() {
            for (const { el, name, value } of rules) {
                const field = form.elements[name];
                el.hidden = !field || field.value !== value;
            }
        }
        form.addEventListener("change", update);
        update();
    }

    function setUpTowPilot(form) {
        const aircraft = form.elements["tow_glider_id"];
        const pilot = form.elements["tow_pilot_id"];
        if (!aircraft || !pilot) return;
        const suggested = () => (aircraft.selectedOptions[0] && aircraft.selectedOptions[0].dataset.pilot) || "";
        let previous = suggested();
        aircraft.addEventListener("change", () => {
            if (pilot.value === "" || pilot.value === previous) {
                pilot.value = suggested();
                pilot.dispatchEvent(new Event("change", { bubbles: true }));
            }
            previous = suggested();
        });
    }

    // A double tap must not send a form twice (two flights added, two
    // check-ins). Once sent, further submits of that form are ignored until
    // the next page shows; coming back with the browser's Back button resets it.
    function sendOnce(form) {
        form.addEventListener("submit", (event) => {
            if (form.dataset.sent) event.preventDefault();
            else form.dataset.sent = "1";
        });
    }
    window.addEventListener("pageshow", () => {
        document.querySelectorAll("form[data-sent]").forEach((form) => delete form.dataset.sent);
    });

    document.querySelectorAll("form").forEach(sendOnce);
    document.querySelectorAll("select[data-search]").forEach(makeSearchable);
    document.querySelectorAll("form").forEach(setUpShowIf);
    document.querySelectorAll("form").forEach(setUpTowPilot);
})();
