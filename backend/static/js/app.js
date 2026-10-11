/* RecruitAI: the small amount of script the pages share. Nothing here decides anything or changes what a form sends;
   every page works with scripts turned off, only less comfortably. */
(function () {
  "use strict";
  var doc = document, body = doc.body;
  function all(sel, root) { return Array.prototype.slice.call((root || doc).querySelectorAll(sel)); }
  function icon(name) { return '<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-' + name + '"></use></svg>'; }

  /* --- confirm dialog ------------------------------------------------------
     A form or a submit button marked data-confirm asks first, saying what will happen. Saying yes sends the very
     same form with the very same button; nothing about the request changes. */
  var dialog = doc.getElementById("confirm");
  function ask(opts) {
    return new Promise(function (resolve) {
      if (!dialog || typeof dialog.showModal !== "function") { resolve(window.confirm(opts.title + "\n\n" + opts.text)); return; }
      dialog.querySelector("[data-title]").textContent = opts.title;
      dialog.querySelector("[data-text]").textContent = opts.text;
      var yes = dialog.querySelector("[data-yes]");
      yes.textContent = opts.action;
      yes.className = opts.danger ? "danger solid" : "";
      dialog.returnValue = "";
      dialog.addEventListener("close", function done() {
        dialog.removeEventListener("close", done);
        resolve(dialog.returnValue === "yes");
      });
      dialog.showModal();
      dialog.querySelector("[data-no]").focus();  // the safe answer is the one under the finger
    });
  }

  /* --- forms: ask where asked to, send once, show that it is on its way ---- */
  doc.addEventListener("submit", function (e) {
    var form = e.target, by = e.submitter || null;
    var source = by && by.dataset.confirm ? by : (form.dataset.confirm ? form : null);
    if (source && !form._agreed) {
      e.preventDefault();
      ask({
        title: source.dataset.confirmTitle || "Are you sure?",
        text: source.dataset.confirm,
        action: source.dataset.confirmAction || (by ? by.textContent.trim() : "Yes, go ahead"),
        danger: source.dataset.confirmTone !== "plain"
      }).then(function (ok) {
        if (!ok) { return; }
        form._agreed = true;
        if (form.requestSubmit) { form.requestSubmit(by || undefined); } else { form.submit(); }
      });
      return;
    }
    if (form.method === "dialog") { return; }
    if (form.dataset.sent) { e.preventDefault(); return; }
    form.dataset.sent = "1";
    form.setAttribute("aria-busy", "true");
    if (by) { by.classList.add("is-loading"); }
  });

  window.addEventListener("pageshow", function (e) {
    // Back and Forward can bring a page back exactly as it was, from the browser's memory, without asking the
    // server (some browsers do this even for pages sent "no-store"). A page shown to someone signed in must never
    // come back that way: they may have signed out since. So it is hidden and asked for again, and the server
    // answers with the page if they are still signed in and with the sign-in page if they are not.
    if (e.persisted && body.hasAttribute("data-signed-in")) { doc.documentElement.style.visibility = "hidden"; location.reload(); return; }
    all("form[data-sent]").forEach(function (f) { delete f.dataset.sent; f.removeAttribute("aria-busy"); f._agreed = false; });
    all(".is-loading").forEach(function (b) { b.classList.remove("is-loading"); });
  });

  // A message shown once belongs to the action just taken, not to the address: refreshing or sharing the page
  // must not show it again.
  (function () {
    var here = new URL(location.href), had = false;
    ["msg", "sent", "expired", "said"].forEach(function (k) { if (here.searchParams.has(k)) { here.searchParams.delete(k); had = true; } });
    if (had) { history.replaceState(null, "", here.pathname + here.search + here.hash); }
  })();

  // While resumes are being read the page keeps itself up to date, but never under someone who is typing
  // or answering a question.
  if (body.hasAttribute("data-reading")) {
    setInterval(function () {
      var at = doc.activeElement;
      if (doc.querySelector("dialog[open]")) { return; }
      if (!at || !/^(INPUT|TEXTAREA|SELECT)$/.test(at.tagName)) { location.reload(); }
    }, 5000);
  }

  /* --- toasts: what the last action did ------------------------------------ */
  var toasts = doc.getElementById("toasts");
  function toast(node) {
    if (!toasts) { return; }
    node.classList.add("toast");
    var close = doc.createElement("button");
    close.type = "button"; close.className = "toast-x"; close.setAttribute("aria-label", "Dismiss this message");
    close.innerHTML = icon("x");
    close.addEventListener("click", function () { node.remove(); });
    node.appendChild(close);
    toasts.appendChild(node);
    // A short confirmation goes away by itself; anything longer is something to read, and stays until dismissed.
    if (node.textContent.trim().length <= 90) {
      var timer = setTimeout(function () { node.remove(); }, 7000);
      node.addEventListener("mouseenter", function () { clearTimeout(timer); });
      node.addEventListener("focusin", function () { clearTimeout(timer); });
    }
  }
  all("[data-toast]").forEach(toast);

  /* --- header: account menu and the small-screen menu ---------------------- */
  var navToggle = doc.querySelector(".navtoggle"), nav = doc.getElementById("mainnav");
  if (navToggle && nav) {
    navToggle.addEventListener("click", function () {
      var open = nav.classList.toggle("open");
      navToggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }
  doc.addEventListener("click", function (e) {
    all("details.menu[open]").forEach(function (m) { if (!m.contains(e.target)) { m.removeAttribute("open"); } });
    if (nav && nav.classList.contains("open") && !nav.contains(e.target) && !navToggle.contains(e.target)) {
      nav.classList.remove("open"); navToggle.setAttribute("aria-expanded", "false");
    }
  });
  doc.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") { return; }
    all("details.menu[open]").forEach(function (m) { m.removeAttribute("open"); var s = m.querySelector("summary"); if (s) { s.focus(); } });
    if (nav && nav.classList.contains("open")) { nav.classList.remove("open"); navToggle.setAttribute("aria-expanded", "false"); navToggle.focus(); }
  });

  /* --- password boxes: an eye inside the field ------------------------------
     It changes what is displayed on this screen and nothing else; the box is a password box again when the form is sent. */
  all("input[type=password]").forEach(function (box) {
    var wrap = doc.createElement("span");
    wrap.className = "pw";
    box.parentNode.insertBefore(wrap, box);
    wrap.appendChild(box);
    var eye = doc.createElement("button");
    eye.type = "button"; eye.className = "pw-eye";
    eye.setAttribute("aria-label", "Show password"); eye.setAttribute("aria-pressed", "false"); eye.title = "Show password";
    eye.innerHTML = icon("eye");
    eye.addEventListener("click", function (e) {
      e.preventDefault();
      var show = box.type === "password";
      box.type = show ? "text" : "password";
      eye.setAttribute("aria-pressed", show ? "true" : "false");
      eye.setAttribute("aria-label", show ? "Hide password" : "Show password"); eye.title = show ? "Hide password" : "Show password";
      eye.innerHTML = icon(show ? "eye-off" : "eye");
    });
    wrap.appendChild(eye);
    if (box.form) { box.form.addEventListener("submit", function () { box.type = "password"; }); }
    if (box.hasAttribute("autofocus")) { box.focus(); }
  });

  /* --- file drop zones: the same <input type="file">, easier to hit -------- */
  all(".dropzone").forEach(function (zone) {
    var input = zone.querySelector("input[type=file]"), list = zone.querySelector("[data-files]");
    if (!input) { return; }
    function show() {
      if (!list) { return; }
      var names = Array.prototype.map.call(input.files, function (f) { return f.name; });
      list.textContent = names.length === 0 ? "" : names.length === 1 ? names[0] : names.length + " files: " + names.join(", ");
      zone.classList.toggle("has-files", names.length > 0);
    }
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.add("over"); }); });
    ["dragleave", "drop"].forEach(function (n) { zone.addEventListener(n, function (e) { e.preventDefault(); zone.classList.remove("over"); }); });
    zone.addEventListener("drop", function (e) {
      if (!e.dataTransfer || !e.dataTransfer.files.length) { return; }
      try { input.files = e.dataTransfer.files; } catch (err) { return; }
      input.dispatchEvent(new Event("change", { bubbles: true }));
    });
  });

  /* --- copy to the clipboard ------------------------------------------------ */
  all("[data-copy]").forEach(function (button) {
    button.addEventListener("click", function () {
      var from = doc.querySelector(button.dataset.copy);
      if (!from) { return; }
      var text = "value" in from ? from.value : from.textContent;
      function done() {
        var was = button.innerHTML;
        button.innerHTML = icon("check") + "<span>Copied</span>";
        setTimeout(function () { button.innerHTML = was; }, 1800);
      }
      function byHand() { if (from.select) { from.select(); try { if (doc.execCommand("copy")) { done(); } } catch (err) { /* the field stays selected for Ctrl+C */ } } }
      if (navigator.clipboard && window.isSecureContext) { navigator.clipboard.writeText(text).then(done, byHand); } else { byHand(); }
    });
  });

  /* --- tables: search, filter tabs, sort ------------------------------------
     All of it works on the rows already on the page; nothing is asked of the server. */
  function visibleCount(table) {
    var rows = all("tbody tr:not(.no-match)", table), shown = rows.filter(function (r) { return !r.hidden; }).length;
    var none = table.querySelector("tr.no-match");
    if (none) { none.hidden = shown !== 0; }
    all('[data-count-for="#' + table.id + '"]').forEach(function (n) { n.textContent = shown === rows.length ? rows.length : shown + " of " + rows.length; });
  }
  function applyFilters(table) {
    var q = (table._q || "").toLowerCase(), group = table._group || "";
    all("tbody tr:not(.no-match)", table).forEach(function (row) {
      var okText = !q || row.textContent.toLowerCase().indexOf(q) !== -1;
      var okGroup = !group || (" " + (row.dataset.group || "") + " ").indexOf(" " + group + " ") !== -1;
      row.hidden = !(okText && okGroup);
    });
    visibleCount(table);
  }
  all("input[data-filter]").forEach(function (input) {
    var tables = all(input.dataset.filter);  // one table, or every table of a grouped list
    if (!tables.length) { return; }
    input.addEventListener("input", function () {
      tables.forEach(function (table) {
        table._q = input.value.trim(); applyFilters(table);
        // a group with nothing left to show steps aside while the search is on
        var group = table.closest("[data-group-of]");
        if (group) { group.hidden = !all("tbody tr:not(.no-match)", table).some(function (r) { return !r.hidden; }); }
      });
      var none = doc.querySelector('[data-none-for="' + input.dataset.filter + '"]');
      if (none) { none.hidden = tables.some(function (t) { return all("tbody tr:not(.no-match)", t).some(function (r) { return !r.hidden; }); }); }
    });
  });
  all("[data-filter-tabs]").forEach(function (bar) {
    var table = doc.querySelector(bar.dataset.filterTabs);
    if (!table) { return; }
    all("button", bar).forEach(function (tab) {
      tab.addEventListener("click", function () {
        all("button", bar).forEach(function (t) { t.setAttribute("aria-pressed", t === tab ? "true" : "false"); });
        table._group = tab.dataset.group || ""; applyFilters(table);
      });
    });
  });
  function cellValue(row, index, kind) {
    var cell = row.cells[index];
    if (!cell) { return kind === "text" ? "" : -Infinity; }
    var raw = cell.dataset.value !== undefined ? cell.dataset.value : cell.textContent.trim();
    if (kind === "num") { var n = parseFloat(raw.replace(/[^0-9.\-]/g, "")); return isNaN(n) ? -Infinity : n; }
    if (kind === "date") { var m = raw.match(/(\d{2})-(\d{2})-(\d{4})(?:\s+(\d{2}):(\d{2}))?/); return m ? Number(m[3] + m[2] + m[1] + (m[4] || "00") + (m[5] || "00")) : -Infinity; }
    return raw.toLowerCase();
  }
  all("table[data-sortable]").forEach(function (table) {
    all("thead th[data-sort]", table).forEach(function (th) {
      var button = doc.createElement("button");
      button.type = "button"; button.className = "sort";
      button.innerHTML = "<span>" + th.innerHTML + "</span>" + icon("sort");
      th.innerHTML = ""; th.appendChild(button);
      button.addEventListener("click", function () {
        var up = th.getAttribute("aria-sort") !== "ascending", kind = th.dataset.sort, index = th.cellIndex, tbody = table.tBodies[0];
        all("thead th[aria-sort]", table).forEach(function (o) { o.removeAttribute("aria-sort"); });
        th.setAttribute("aria-sort", up ? "ascending" : "descending");
        all("tr:not(.no-match)", tbody).map(function (row, i) { return { row: row, i: i, v: cellValue(row, index, kind) }; })
          .sort(function (a, b) { return (a.v < b.v ? -1 : a.v > b.v ? 1 : a.i - b.i) * (up ? 1 : -1); })
          .forEach(function (x) { tbody.appendChild(x.row); });
        var none = tbody.querySelector("tr.no-match"); if (none) { tbody.appendChild(none); }
      });
    });
  });

  /* --- tabs: sections of one long page ---------------------------------------
     Without scripts every section is simply on the page, one under the other. */
  all("[role=tablist]").forEach(function (list) {
    var tabs = all("[role=tab]", list);
    function pick(tab, focus) {
      tabs.forEach(function (t) {
        var on = t === tab, panel = doc.getElementById(t.getAttribute("aria-controls"));
        t.setAttribute("aria-selected", on ? "true" : "false"); t.tabIndex = on ? 0 : -1;
        if (panel) { panel.hidden = !on; }
      });
      if (focus) { tab.focus(); }
    }
    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { pick(tab); if (history.replaceState) { history.replaceState(null, "", "#" + tab.getAttribute("aria-controls")); } });
      tab.addEventListener("keydown", function (e) {
        if (e.key === "ArrowRight") { pick(tabs[(i + 1) % tabs.length], true); }
        if (e.key === "ArrowLeft") { pick(tabs[(i - 1 + tabs.length) % tabs.length], true); }
      });
    });
    function fromAddress() {
      var id = location.hash.slice(1), target = id && doc.getElementById(id), found = null;
      if (target) { tabs.forEach(function (t) { var p = doc.getElementById(t.getAttribute("aria-controls")); if (p && (p === target || p.contains(target))) { found = t; } }); }
      return found;
    }
    var asked = fromAddress();
    pick(asked || tabs.filter(function (t) { return t.getAttribute("aria-selected") === "true"; })[0] || tabs[0]);
    // The address named something inside a part that was not showing when the page arrived: go to it now that it is.
    if (asked) { var there = doc.getElementById(location.hash.slice(1)); if (there) { setTimeout(function () { there.scrollIntoView(); }, 0); } }
    window.addEventListener("hashchange", function () { var t = fromAddress(); if (t) { pick(t); var el = doc.getElementById(location.hash.slice(1)); if (el) { el.scrollIntoView(); } } });
  });

  /* --- print: the page as it stands, through the browser's own dialog (which also saves a PDF) --- */
  all("[data-print]").forEach(function (button) { button.hidden = false; button.addEventListener("click", function () { window.print(); }); });

  /* --- a new password: how it stands against the one rule there is, its length. Only a hint; the server decides. --- */
  all("input[data-min-length]").forEach(function (box) {
    var hint = doc.getElementById(box.getAttribute("aria-describedby") || ""), min = Number(box.dataset.minLength);
    if (!hint || !min) { return; }
    var rule = hint.textContent;
    box.addEventListener("input", function () {
      var n = box.value.length;
      hint.textContent = n === 0 ? rule : n < min ? n + " of at least " + min + " characters" : n + " characters: long enough" + (n >= min + 4 ? ", and longer is stronger" : "");
      hint.classList.toggle("ok", n >= min);
    });
  });

  /* --- dialogs opened by a button (a form in a side drawer, say) ------------ */
  all("[data-open]").forEach(function (button) {
    var target = doc.querySelector(button.dataset.open);
    if (!target || typeof target.showModal !== "function") { return; }
    button.hidden = false;
    button.addEventListener("click", function () { target.showModal(); });
  });
  all("dialog").forEach(function (d) {
    d.addEventListener("click", function (e) { if (e.target === d) { d.close(); } });  // a click on the dimmed page closes it
  });
})();
