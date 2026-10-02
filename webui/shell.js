"use strict";
/* ==========================================================================
   shell.js - the round-3 frame (REDESIGN 2026-09-26, docs/REDESIGN.md)
   --------------------------------------------------------------------------
   Loaded AFTER app.js. It composes the new frame at runtime by MOVING the
   nodes app.js already owns - never cloning or rebuilding them - so every
   handler, observer and `hidden` contract in app.js keeps working, and
   index.html keeps the canonical markup order the tests pin (and that the
   core-cutfill fork will keep editing; its changes merge cleanly here).

     header    wordmark | Projects / document . sheet | MODULE TABS | actions
     #ribbon   the canvas tools (the old icon rail), labelled, + the module's
               own actions (proxies that click the real Tools-menu buttons)
     main      left rail  = #sidebar: Sheets + the module's panels and cards
               centre     = #stage (the canvas)
               right rail = #railR: Sheet, Takeoff, Bid view, Questions,
                            and the approve footer
               or one full-width view: Plans / Summary / 3D

   Talks to app.js only through (a) ids it owns and their existing handlers
   (click the real button; open a panel only through its own button, close it
   only through its own close button - `panel.hidden` is app.js's one "open"
   flag), and (b) a few globals read-only (state, computeTakeoffRows, the
   check-queue functions, xsLoadApproved). Three app.js functions are
   wrapped to follow the page (selectPage, loadResult) and to bring the
   canvas forward before something scrolls to a row on it (revealStyleRow,
   ckGo). Nothing here measures anything or changes a number.
   ========================================================================== */
(() => {
  if (window.coreShell) return;
  const root = document.documentElement;
  const byId = (id) => document.getElementById(id);
  const F = (name) => (typeof window[name] === "function" ? window[name] : null);
  const call = (name, ...a) => { const f = F(name); return f ? f(...a) : undefined; };
  const S = () => window.state || {};
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: this session only */ } },
  };
  const fmtN = (v, d = 0) => (v == null || !isFinite(v) ? "" :
    Number(v).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d }));
  const money = (v) => (v == null || !isFinite(v) ? "" :
    "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
  const money0 = (v) => (v == null || !isFinite(v) ? "" :
    "$" + Math.round(Number(v)).toLocaleString("en-US"));
  const srcUrl = (p) => (typeof url === "function" ? url(p) : location.origin + p);

  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of kids.flat(3)) {
      if (c == null || c === false) continue;
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return n;
  }
  const SVGNS = "http://www.w3.org/2000/svg";
  function icon(inner, cls) {
    const s = document.createElementNS(SVGNS, "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    if (cls) s.setAttribute("class", cls);
    s.innerHTML = inner;
    return s;
  }
  const ICON = {
    // the wordmark's glyph: a sheet with two contour lines on it
    glyph: '<rect x="3" y="3" width="18" height="18" rx="4"/><path d="M7 15.5c2.2-2.6 4.1-2.6 6 0s3.6 2.2 4.5-.8"/><path d="M7 10.2c2.2-2.6 4.1-2.6 6 0"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    chev: '<path d="m6 9 6 6 6-6"/>',
    arrow: '<path d="M5 12h14M13 6l6 6-6 6"/>',
    cube: '<path d="M12 3 20 7.5v9L12 21l-8-4.5v-9z"/><path d="M4 7.5 12 12l8-4.5M12 12v9"/>',
    rooms: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M3 12h18M12 4v16"/>',
    doc: '<path d="M6 3h8l4 4v14H6z"/><path d="M14 3v4h4"/>',
    print: '<path d="M7 9V3h10v6"/><rect x="3" y="9" width="18" height="8" rx="1.5"/><path d="M7 14h10v7H7z"/>',
    csv: '<path d="M6 3h8l4 4v14H6z"/><path d="M9 13h6M9 17h6M9 9h2"/>',
    link: '<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>',
  };

  const header = document.querySelector("header");
  const main = document.querySelector("main");
  const stage = byId("stage");
  const sidebar = byId("sidebar");
  if (!header || !main || !stage || !sidebar) return;   // not the app page

  /* ------------------------------------------------------------ MODULES */
  const MODULES = [
    { id: "plans",     label: "Plans",     kind: "view" },
    { id: "lines",     label: "Lines",     kind: "canvas",
      acts: ["legendBtn", "coBtn", "aiBtn"],
      owns: [["coPanel", "coClose"]] },
    { id: "counts",    label: "Counts",    kind: "canvas",
      acts: ["symBtn", "rollBtn", "xsBtn"],
      primary: ["symBtn", "symPanel"], auto: "tab",
      owns: [["symPanel", "symClose"], ["rollPanel", "rollClose"], ["xsPanel", "xsClose"]] },
    { id: "areas",     label: "Areas",     kind: "canvas",
      acts: ["flBtn"],
      primary: ["flBtn", "flPanel"], auto: "always", needsResult: true,
      owns: [["flPanel", "flClose"]] },
    { id: "earthwork", label: "Earthwork", kind: "canvas",
      acts: ["cfBtn", "stBtn"],
      owns: [["stPanel", "stClose"]] },
    { id: "trenching", label: "Trenching", kind: "canvas",
      acts: ["trBtn"],
      primary: ["trBtn", "trPanel"], auto: "always", needsResult: true,
      owns: [["trPanel", "trClose"]] },
    { id: "summary",   label: "Summary",   kind: "view" },
    { id: "3d",        label: "3D",        kind: "view" },
  ];
  const MOD = Object.fromEntries(MODULES.map((m) => [m.id, m]));
  // which module a panel belongs to - a panel opened any other way (Tools
  // menu, a Questions row, the Analyze banner) brings its tab forward
  const PANEL_MOD = { coPanel: "lines", symPanel: "counts", rollPanel: "counts", xsPanel: "counts",
                      flPanel: "areas", stPanel: "earthwork", trPanel: "trenching" };
  // the Tools-menu button -> the thing it opens (TOOLS_MENU_ITEMS in app.js)
  const BTN_PANEL = { cfBtn: "cfStage", flBtn: "flPanel", trBtn: "trPanel", stBtn: "stPanel",
                      symBtn: "symPanel", rollBtn: "rollPanel", xsBtn: "xsPanel", coBtn: "coPanel",
                      aiBtn: "aiBanner", legendBtn: "legendModal" };
  let cur = null;
  let ewSub = LS.get("core-ew-sub", "cf");     // Earthwork: "cf" (engine) or "st" (structure dig)
  let pending = null;                          // a panel waiting for its button to appear

  /* -------------------------------------------------------------- HEADER */
  const brand = header.querySelector(".brand");
  if (brand) {
    brand.prepend(icon(ICON.glyph, "brand-glyph"));
    const sub = brand.querySelector(".brand-sub");
    if (sub) sub.textContent = "Takeoff";
    const sl = byId("settingsLink");
    if (sl) {
      sl.textContent = "";
      sl.append(icon(ICON.gear));
      sl.setAttribute("aria-label", "Settings");
      sl.classList.add("hdr-icon");
    }
  }
  const tabs = h("nav", { id: "modTabs", class: "mod-tabs", role: "tablist", "aria-label": "Modules" },
    MODULES.map((m) => h("button", {
      type: "button", class: "mod-tab", role: "tab", id: "modTab-" + m.id, "data-mod": m.id,
      "aria-selected": "false", tabindex: "-1",
      onclick: () => setModule(m.id, "tab"),
    }, m.label)));
  tabs.addEventListener("keydown", (e) => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    const list = [...tabs.querySelectorAll(".mod-tab")];
    let i = list.indexOf(document.activeElement);
    if (i < 0) return;
    e.preventDefault(); e.stopPropagation();
    i = e.key === "Home" ? 0 : e.key === "End" ? list.length - 1
      : (i + (e.key === "ArrowRight" ? 1 : -1) + list.length) % list.length;
    list[i].focus();
  });
  (byId("pageSel") || header.lastElementChild).after(tabs);

  /* -------------------------------------------------------------- RIBBON */
  const ribbon = h("div", { id: "ribbon", role: "toolbar", "aria-label": "Sheet tools" });
  header.after(ribbon);
  const toolkit = stage.querySelector(":scope > .toolkit");
  const TK_LABELS = {
    measureBtn: "Measure", splitBtn: "Split", extendBtn: "Extend", cutSpanBtn: "Cut",
    flattenBtn: "Flatten", drawBtn: "Draw", reclassBtn: "Move", mergeBtn: "Join",
    sepBtn: "Name", removeBtn: "Delete", msNameBtn: "Name these", msFamBtn: "Move all",
    msColorBtn: "Colour", msDelBtn: "Delete all", msClearBtn: "Clear",
    wzBtn: "Work area", wzClearBtn: "Clear area", trimBtn: "Trim",
  };
  if (toolkit) {
    for (const [id, lab] of Object.entries(TK_LABELS)) {
      const b = byId(id);
      if (b) b.setAttribute("data-label", lab);
    }
    ribbon.append(toolkit);
  }
  // THE RIBBON FITS ITS WINDOW, measured (2026-10-02: at 1280 dock.js's Panels button ran 12px past the edge; the
  // container-query widths in responsive.css were measured once, and the tools' words have grown since). Words step
  // out one group at a time until nothing is cut: first the Panels word, then the tools' words, then (a tablet) the
  // drawer toggles' words. Buttons, their order and their titles always stay. Where even that cannot fit (a phone),
  // the toggles keep their words and the icon strip scrolls between them, as responsive.css designs it.
  // RIBBON-FIT-BEGIN
  const RB_FIT = ["rb-fit-1", "rb-fit-2", "rb-fit-3"];
  let rbFitting = false;
  function ribbonFit() {
    if (rbFitting) return;
    rbFitting = true;
    try {
      ribbon.classList.remove(...RB_FIT);
      if (!ribbon.clientWidth) return;                  // hidden: Plans, Summary, 3D, no document
      const over = () => ribbon.scrollWidth > ribbon.clientWidth + 0.5;
      for (const c of RB_FIT) { if (!over()) break; ribbon.classList.add(c); }
      if (over()) ribbon.classList.remove("rb-fit-3");   // scrolling anyway: the toggles keep their words
      // ...and where the strip scrolls (a tablet or a phone), the Panels button is pinned beside the Takeoff toggle
      // instead of riding the strip off the edge or under that toggle (UI verify 2026-10-02 L1: 6-18px off at 768,
      // 104px under the toggle). responsive.css reads the toggle's width from here.
      const tog = ribbon.querySelector ? ribbon.querySelector("#rspTakeoffBtn") : null;
      if (ribbon.style) ribbon.style.setProperty("--rb-tog-w", (tog ? tog.offsetWidth : 0) + "px");
    } finally { rbFitting = false; }
  }
  // RIBBON-FIT-END
  if (window.ResizeObserver) {
    const rbRO = new ResizeObserver(() => ribbonFit());
    const watch = () => { for (const c of ribbon.children) rbRO.observe(c); };
    rbRO.observe(ribbon); watch();
    // dock.js adds its Panels button and responsive.js its toggles after this: a new child is measured too
    new MutationObserver(() => { watch(); ribbonFit(); }).observe(ribbon, { childList: true });
  }
  // the module's own tools live in a small card in the left rail (the ribbon
  // is the canvas tools only - it fits at 1440 with its labels that way)
  const rbMod = h("div", { class: "mc-acts", id: "rbMod", role: "group", "aria-label": "This module's tools" });
  const modCard = h("section", { id: "modCard", class: "rail-card mod-card", "aria-label": "Module tools" },
    h("div", { class: "rc-head" }, h("h2", { id: "modCardTitle" }, "")), rbMod);

  /* ------------------------------------------------------ THE TWO RAILS */
  // LEFT: the Sheets card sits in the frame of #sidebar, above #sideScroll,
  // so #toolDock stays the first thing in the scrollport (an opened panel is
  // what the operator just asked for - app.js's contract).
  const sideBody = sidebar.querySelector(".side-body");
  const sheetsCard = h("section", { id: "sheetsCard", class: "rail-card sheets-card", "aria-label": "Sheets" },
    h("div", { class: "rc-head" },
      h("h2", null, "Sheets ", h("span", { class: "card-n", id: "sheetsN" })),
      h("button", { type: "button", class: "mini", id: "sheetsDetectAll", hidden: true,
                    title: "Detect lines on every page, one after another",
                    onclick: () => { const b = byId("detectAllBtn"); if (b) b.click(); } }, "Detect all")),
    h("div", { class: "sheets-body", id: "sheetsBody" }));
  if (sideBody) { sideBody.prepend(sheetsCard); sheetsCard.after(modCard); }

  // RIGHT: a div, not an <aside> - style.css sizes every `aside` by --side-w
  const railScroll = h("div", { id: "railScroll" });
  const railFoot = h("div", { id: "railFoot" });
  const railR = h("div", { id: "railR", class: "rail-r", role: "complementary",
                           "aria-label": "Sheet, takeoff and questions" }, railScroll, railFoot);
  stage.after(railR);
  for (const id of ["cardSheet", "cardTakeoff", "cardBid", "cardCheck"]) {
    const n = byId(id);
    if (n) railScroll.append(n);
  }
  const approveBar = sidebar.querySelector(".approve-bar");
  const linksBar = sidebar.querySelector(".links-bar");
  if (approveBar) railFoot.append(approveBar);
  if (linksBar) railFoot.append(linksBar);
  // card titles in the mockups' words (text nodes only - the count spans
  // and the fold handlers on the <h2> stay exactly as app.js bound them)
  const retitle = (headId, text) => {
    const h2 = byId(headId) && byId(headId).querySelector("h2");
    if (!h2) return;
    const t = [...h2.childNodes].find((n) => n.nodeType === 3 && n.textContent.trim());
    if (t) t.textContent = text + " ";
  };
  retitle("autoHead", "Questions");
  retitle("takeoffHead", "Takeoff");

  /* --------------------------------------------------- FULL-WIDTH VIEWS */
  const plansView = h("section", { id: "plansView", class: "plans-view", "aria-label": "Sheets in this set" });
  const summaryView = h("section", { id: "summaryView", class: "summary-view", "aria-label": "Summary" });
  const view3d = h("section", { id: "view3dPick", class: "v3-view", "aria-label": "3D" });
  main.append(plansView, summaryView, view3d);
  // the triage bar (the tier-highlight toggle) belongs with the sheet grid
  const triageHome = h("div", { class: "pl-triage", id: "plTriage" });

  /* ---------------------------------------------------- CANVAS UPKEEP
     app.js sizes the canvas on window resize only. A tab switch, a rail
     fold or the ribbon wrapping changes #stage without one, and a canvas
     measured while display:none gets a zero fit it cannot zoom out of. */
  function syncCanvas() {
    if (!stage.clientWidth || !stage.clientHeight) return;
    call("syncBuffer");
    const s = S();
    if (s.img && !(s.view && s.view.k > 0)) { call("fitView"); call("draw"); }
  }
  if ("ResizeObserver" in window) new ResizeObserver(() => syncCanvas()).observe(stage);

  /* -------------------------------------------------- MODULE SWITCHING */
  function paintTabs() {
    for (const b of tabs.querySelectorAll(".mod-tab")) {
      const on = b.dataset.mod === cur;
      b.setAttribute("aria-selected", on ? "true" : "false");
      b.tabIndex = on ? 0 : -1;
    }
  }
  function isOpen(panelId) { const p = byId(panelId); return !!p && !p.hidden; }
  function closeOwned(m) {
    for (const [panel, closer] of m.owns || []) {
      if (isOpen(panel)) { const c = byId(closer); if (c) c.click(); }
    }
  }
  function closeStage() { if (isOpen("cfStage")) call("cfClose"); }
  function close3dOverlay() {
    const o = byId("fl3d");
    if (o && !o.hidden) { const x = byId("fl3dClose"); if (x) x.click(); }
  }
  // open a panel through its own button, once the button exists and only if
  // it is closed (the buttons TOGGLE - a click on an open one closes it)
  function openVia(btnId, panelId, forMod) {
    pending = null;
    const b = byId(btnId);
    if (isOpen(panelId)) return;
    if (b && !b.hidden) { b.click(); autoOpen = panelId; return; }
    pending = { btnId, panelId, forMod, until: Date.now() + 10000 };
    const tick = () => {
      const p = pending;
      if (!p || cur !== p.forMod || Date.now() > p.until) { if (p && Date.now() > p.until) pending = null; return; }
      const bb = byId(p.btnId);
      if (isOpen(p.panelId)) { pending = null; return; }
      if (bb && !bb.hidden) { pending = null; bb.click(); autoOpen = p.panelId; return; }
      setTimeout(tick, 250);
    };
    setTimeout(tick, 250);
  }
  // ONE CLICK OPENS A TOOL (UI verify 2026-10-02, defect 8: "Trench and Count do nothing on the first click after
  // switching tabs; the second click opens"). The tab had already opened its tool panel (openVia, above) - often out
  // of sight in a short rail or a closed drawer - so the operator's first press of that tool, in the module card or
  // the Tools menu, was the toggle that CLOSED it, and the second opened it again. A panel the tab opened by itself
  // is now shown by that first press instead (scrolled into view, its drawer opened, a brief ring); every press after
  // it toggles as before.
  let autoOpen = null;
  function revealPanel(panelId) {
    const p = byId(panelId);
    if (!p) return;
    const R = window.coreResponsive;
    const side = p.closest("#railR") ? "right" : p.closest("#sidebar") ? "left" : null;
    if (side && R && R.drawerMode && R.drawer !== side) R.open(side);
    const n = p.closest(".dk-panel.dk-auto");
    if (n && window.coreDock && window.coreDock.unfold) window.coreDock.unfold(n.id);
    requestAnimationFrame(() => {
      if (p.scrollIntoView) p.scrollIntoView({ block: "nearest" });
      p.classList.remove("shell-shown"); void p.offsetWidth; p.classList.add("shell-shown");
      setTimeout(() => p.classList.remove("shell-shown"), 1200);
    });
  }
  // true when this press is the first one on a panel the tab opened: shown, not closed
  function firstPressShows(panelId) {
    if (!panelId || autoOpen !== panelId || !isOpen(panelId)) return false;
    autoOpen = null;
    revealPanel(panelId);
    return true;
  }
  function openPrimary(m, reason) {
    if (!S().job) return;
    if (m.id === "earthwork") {
      if (reason === "tab") {
        if (ewSub === "st") { if (S().result) openVia("stBtn", "stPanel", "earthwork"); }
        else call("cfOpen");
      } else if (ewSub === "st" && S().result && (reason === "page" || reason === "result")) {
        openVia("stBtn", "stPanel", "earthwork");
      }
      return;
    }
    if (!m.primary) return;
    // flooring and trench work on detected lines; opening them over an
    // undetected sheet would only say so - the result hook brings them back
    if (m.needsResult && !S().result) return;
    if (m.auto === "always" && (reason === "tab" || reason === "page" || reason === "result"))
      openVia(m.primary[0], m.primary[1], m.id);
    else if (m.auto === "tab" && reason === "tab")
      openVia(m.primary[0], m.primary[1], m.id);
  }
  function setModule(id, reason) {
    const m = MOD[id] || MOD.lines;
    const prev = cur ? MOD[cur] : null;
    // "open": a project just opened on the tab Settings -> Workspace names.
    // It behaves like a tab press, but is not remembered as the last tab.
    const tabLike = reason === "tab" || reason === "open";
    if (prev && prev.id !== m.id && tabLike) {
      // the engine page and the rooms-3D overlay cover the frame; they step
      // down when a tab they do not belong to is picked
      if (m.id !== "earthwork" && m.id !== "3d") { closeStage(); close3dOverlay(); }
      if (m.id === "earthwork") close3dOverlay();
      // leaving one canvas module for another puts the old one's panels
      // away through their own close buttons (disarming their tools)
      if (prev.kind === "canvas" && m.kind === "canvas") closeOwned(prev);
    }
    cur = m.id;
    document.body.dataset.module = m.id;
    if (reason === "tab") LS.set("core-module", m.id);
    paintTabs();
    paintActs();
    if (m.id === "plans") renderPlans();
    else if (m.id === "summary") renderSummary();
    else if (m.id === "3d") render3d();
    if (m.kind === "canvas") openPrimary(m, tabLike ? "tab" : reason);
    if (m.id === "earthwork" && tabLike && isOpen("cfStage")) cfView("2d");
    // 3D with the engine already open: show its 3D view rather than the
    // chooser under it
    if (m.id === "3d" && tabLike && isOpen("cfStage")) { if (!cfView("3d")) openCf3d(); }
    requestAnimationFrame(syncCanvas);
  }

  /* ------------------------------------------- THE MODULE'S ACTIONS
     Proxies in the ribbon for the Tools-menu buttons this module is about.
     They click the real button (so its handler, its toggle and its hidden
     bookkeeping are the only truth), mirror its `hidden`, and show pressed
     while its panel is open. The Tools menu itself stays whole. */
  function actLabel(btn) {
    const nm = btn.querySelector(".tm-nm");
    return (nm ? nm.textContent : btn.textContent).trim();
  }
  function paintActs() {
    rbMod.innerHTML = "";
    const m = MOD[cur];
    modCard.hidden = !(m && m.acts);
    const t = byId("modCardTitle");
    if (t) t.textContent = m ? m.label : "";
    if (!m || !m.acts) return;
    for (const id of m.acts) {
      const b = byId(id);
      if (!b) continue;
      const ico = b.querySelector("svg.tm-ico");
      const p = BTN_PANEL[id];
      const on = p ? isOpen(p) : false;
      rbMod.append(h("button", {
        type: "button", class: "rb-btn", "data-for": id, hidden: b.hidden,
        "aria-pressed": on ? "true" : "false", title: b.getAttribute("title") || "",
        onclick: () => {
          if (firstPressShows(p)) return;
          if (id === "stBtn") { ewSub = isOpen("stPanel") ? "cf" : "st"; LS.set("core-ew-sub", ewSub); }
          if (id === "cfBtn") { ewSub = "cf"; LS.set("core-ew-sub", ewSub); }
          b.click();
        },
      }, ico ? ico.cloneNode(true) : null, h("span", null, actLabel(b))));
    }
  }
  // follow the real buttons and panels
  const watchIds = new Set([...Object.keys(BTN_PANEL), ...Object.values(BTN_PANEL)]);
  const actObs = new MutationObserver((recs) => {
    let repaint = false;
    for (const r of recs) {
      const t = r.target;
      repaint = true;
      if (t.id && t.id === autoOpen && t.hidden) autoOpen = null;
      // a panel opened some other way brings its module forward
      if (t.id && PANEL_MOD[t.id] && !t.hidden) {
        const want = PANEL_MOD[t.id];
        if (want === "earthwork") { ewSub = "st"; LS.set("core-ew-sub", ewSub); }
        if (cur !== want && MOD[cur] && MOD[cur].kind === "canvas") setModule(want, "follow");
      }
      if (t.id === "cfStage") {
        if (!t.hidden && cur !== "earthwork" && cur !== "3d") setModule("earthwork", "follow");
        if (!t.hidden) injectEmbedSkin();
      }
    }
    if (repaint) requestAnimationFrame(paintActs);
  });
  for (const id of watchIds) {
    const n = byId(id);
    if (n) actObs.observe(n, { attributes: true, attributeFilter: ["hidden"] });
  }

  /* ------------------------------------------ THE TOOLS MENU, IN COLUMNS
     app.js's menu is one long list - three groups, a paragraph per row -
     that ran off the bottom of a 900px screen. Each group's rows are MOVED
     into a column of their own: the same buttons in the same order (app.js's
     arrow keys walk querySelectorAll("button") in document order, which the
     columns keep), and its separators stand down. */
  const toolsMenu = byId("toolsMenu");
  // the Tools menu's row for a panel the tab already opened: the first press shows it (see firstPressShows); the
  // menu closes as after any row
  if (toolsMenu) toolsMenu.addEventListener("click", (e) => {
    const b = e.target.closest && e.target.closest("button");
    if (!b || !BTN_PANEL[b.id] || !firstPressShows(BTN_PANEL[b.id])) return;
    e.stopPropagation(); e.preventDefault();
    call("toolsMenuClose");
  }, true);
  if (toolsMenu && !toolsMenu.querySelector(".tmn-col")) {
    let col = null;
    for (const n of [...toolsMenu.children]) {
      if (n.classList.contains("tm-group")) {
        col = h("div", { class: "tmn-col" });
        n.before(col);
        col.append(n);
      } else if (col && !n.classList.contains("tm-sep")) col.append(n);
    }
  }

  /* --------------------------------------------- THE PANELS' HOW-TOs
     Every tool panel opens with its own explanation - Flooring's runs to
     ~250 words and pushed the controls below the fold. Folded, never
     deleted (the house rule for load-bearing copy): three lines show, and
     "How it works" opens the rest. The hint's own `hidden` stays app.js's
     (the panel folds write it); this only adds a class. */
  for (const hint of document.querySelectorAll(".tool-dock .ew-hint[id], #ckHint")) {
    if (hint.dataset.shellClamp) continue;
    hint.dataset.shellClamp = "1";
    hint.classList.add("hint-clamp");
    const b = h("button", { type: "button", class: "hint-more", "aria-expanded": "false",
      "aria-controls": hint.id,
      onclick: () => {
        const open = hint.classList.toggle("open");
        b.setAttribute("aria-expanded", open ? "true" : "false");
        b.textContent = open ? "Less" : "How it works";
      } }, "How it works");
    // before the hint, not after: calcNotePut inserts its note AFTER #trHint
    hint.before(b);
    // the toggle follows the hint when a panel fold hides it
    new MutationObserver(() => { b.hidden = hint.hidden; }).observe(hint, { attributes: true, attributeFilter: ["hidden"] });
    b.hidden = hint.hidden;
  }

  /* --------------------------------------------- PROJECTS: STATUS FILTER
     The mockup's left rail filters projects by state. The states are the
     server's own counts per project (pages / detected / approved, the same
     numbers each card prints) - not started, in progress, approved. Only
     on "Your projects": demo plans always open fresh. Filtering hides
     cards; it never refetches and never changes what app.js rendered. */
  const pvStatusOf = (r) => ((r.approved || 0) > 0 ? "approved" : (r.detected || 0) > 0 ? "progress" : "new");
  const pvStatusOfCard = (card) => {
    const t = (card.querySelector(".pv-meta") || {}).textContent || "";
    return /approved/.test(t) ? "approved" : /not detected/.test(t) ? "new" : /detected/.test(t) ? "progress" : "new";
  };
  let pvFilter = "all";
  const PV_FILTERS = [["all", "All projects"], ["new", "Not started"], ["progress", "In progress"], ["approved", "Approved"]];
  const pvFilterBox = h("div", { class: "pv-filters", id: "pvFilters", role: "group", "aria-label": "Filter by status" },
    h("div", { class: "pv-filters-t" }, "Status"));
  const pvView = byId("projectsView");
  if (pvView) pvView.append(pvFilterBox);
  function pvFilterPaint() {
    const mine = byId("pvTabMine") && byId("pvTabMine").getAttribute("aria-selected") === "true";
    pvFilterBox.hidden = !mine;
    const rows = (typeof pvRows !== "undefined" && Array.isArray(pvRows) ? pvRows : []).filter((r) => !r.demo);
    const count = { all: rows.length, new: 0, progress: 0, approved: 0 };
    for (const r of rows) count[pvStatusOf(r)]++;
    pvFilterBox.querySelectorAll(".pv-filter").forEach((b) => b.remove());
    for (const [k, label] of PV_FILTERS) {
      pvFilterBox.append(h("button", { type: "button", class: "pv-filter" + (pvFilter === k ? " on" : ""),
        "aria-pressed": pvFilter === k ? "true" : "false",
        onclick: () => { pvFilter = k; pvFilterPaint(); } },
        h("span", null, label), h("span", { class: "pv-tabn" }, String(count[k]))));
    }
    const grid = byId("pvGrid");
    if (!grid) return;
    for (const card of grid.querySelectorAll(".pv-card"))
      card.classList.toggle("pv-filter-out", mine && pvFilter !== "all" && pvStatusOfCard(card) !== pvFilter);
  }
  const pvGridEl = byId("pvGrid");
  if (pvGridEl) new MutationObserver(() => requestAnimationFrame(pvFilterPaint)).observe(pvGridEl, { childList: true });
  for (const id of ["pvTabMine", "pvTabDemo"]) {
    const t = byId(id);
    if (t) new MutationObserver(() => requestAnimationFrame(pvFilterPaint)).observe(t, { attributes: true, attributeFilter: ["aria-selected"] });
  }

  /* ------------------------------------------ THE SCOPE PANEL'S NOTE
     "Whose scope is this?" explains itself in a paragraph under its chips
     (app.js renderScopeBar, rebuilt on every change). Folded to its bold
     lead line with a Details toggle; the choice is remembered, the words
     are all still there. */
  // UI verify 2026-10-02 (defect 6): the chips and the note took 279px of a 490px Line styles panel and left its
  // list 51px. Folded, the bar is ONE line - its question (or "Counting for" and the trades that ARE declared, so a
  // filter that narrows the list is always on screen) and a button in its head: "Pick trades" / "Change", then "Less".
  const scopeBar = byId("scopeBar");
  let scopeOpen = LS.get("core-scope-note", "closed") === "open";
  function scopeFold() {
    if (!scopeBar) return;
    scopeBar.classList.toggle("note-open", scopeOpen);
    const head = scopeBar.querySelector(".scope-head");
    if (!head || head.querySelector(".scope-more")) return;
    const word = () => (scopeOpen ? "Less" : scopeBar.querySelector(".scope-chip.on") ? "Change" : "Pick trades");
    head.insertBefore(h("button", { type: "button", class: "scope-more", "aria-expanded": scopeOpen ? "true" : "false",
      title: "The trades to choose from and what the choice does",
      onclick: (e) => {
        scopeOpen = !scopeOpen;
        LS.set("core-scope-note", scopeOpen ? "open" : "closed");
        e.currentTarget.setAttribute("aria-expanded", scopeOpen ? "true" : "false");
        scopeBar.classList.toggle("note-open", scopeOpen);
        e.currentTarget.textContent = word();
      } }, word()), head.querySelector("#scopeClose"));
  }
  if (scopeBar) { new MutationObserver(scopeFold).observe(scopeBar, { childList: true }); scopeFold(); }

  /* ------------------------------------------- TAKEOFF BY TRADE (rail)
     #itemized is app.js's editing table: a trade head, its rows, the next
     head. A click on a head folds that trade (the mockup's accordion). The
     fold is remembered by trade name and re-applied after every rebuild;
     the rows stay in the DOM, so the counts and the CSV are untouched. */
  const tradeFolds = new Set(JSON.parse(LS.get("core-trade-folds", "[]") || "[]"));
  function tradeName(head) { const sp = head.querySelector("span"); return sp ? sp.textContent.trim() : ""; }
  function applyTradeFolds() {
    const box = byId("itemized");
    if (!box) return;
    let folded = false;
    for (const n of box.children) {
      if (n.classList.contains("trade-head")) {
        folded = tradeFolds.has(tradeName(n));
        n.classList.toggle("tf-folded", folded);
        n.setAttribute("role", "button");
        n.tabIndex = 0;
        n.setAttribute("aria-expanded", folded ? "false" : "true");
        continue;
      }
      if (n.classList.contains("total") || n.classList.contains("head")) { folded = false; n.classList.remove("tf-hide"); continue; }
      n.classList.toggle("tf-hide", folded);
    }
  }
  function toggleTrade(head) {
    const nm = tradeName(head);
    if (!nm) return;
    if (tradeFolds.has(nm)) tradeFolds.delete(nm); else tradeFolds.add(nm);
    LS.set("core-trade-folds", JSON.stringify([...tradeFolds]));
    applyTradeFolds();
  }
  const itemizedBox = byId("itemized");
  if (itemizedBox) {
    itemizedBox.addEventListener("click", (e) => {
      const hd = e.target.closest(".trade-head");
      if (hd && itemizedBox.contains(hd)) toggleTrade(hd);
    });
    itemizedBox.addEventListener("keydown", (e) => {
      const hd = e.target.closest && e.target.closest(".trade-head");
      if (hd && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); toggleTrade(hd); }
    });
  }

  /* ------------------------------------------------------------ SHEETS
     A second list over the same pages, NOT the page strip itself: app.js
     finds thumbs by their index in #thumbList, so the strip must stay one
     thumb per page in page order. This list mirrors it (status, current,
     sheet number) and clicks the real thumb, which sets #pageSel first. */
  const DISC = { G: "General", V: "Survey", B: "Geotechnical", C: "Civil", L: "Landscape",
    S: "Structural", A: "Architectural", I: "Interiors", Q: "Equipment", F: "Fire protection",
    P: "Plumbing", D: "Process", M: "Mechanical", E: "Electrical", W: "Distributed energy",
    T: "Telecom", R: "Resource", X: "Other disciplines", Z: "Shop drawings", O: "Operations" };
  const DISC_ORDER = "GVBCLSAIQFPDMEWTRXZO";
  const DISC_TITLE = "Grouped by the letter the sheet number starts with (the US National CAD " +
    "Standard's discipline letters: C civil, A architectural, S structural ...). An inference " +
    "from the number, not a read of the title block.";
  function discOf(no) {
    if (!no) return { key: "~", name: "No sheet number" };
    const m = /^([A-Za-z]{1,2})\s*[-.]?\s*\d/.exec(String(no).trim());
    if (!m) return { key: "}", name: "Other" };
    const k = m[1].toUpperCase()[0];
    return DISC[k] ? { key: k, name: DISC[k] } : { key: "}", name: "Other" };
  }
  const discRank = (k) => { const i = DISC_ORDER.indexOf(k); return i < 0 ? (k === "}" ? 90 : 99) : i; };
  function pageList() {
    const s = S();
    const n = (s.kinds && s.kinds.length) || 0;
    const tri = s.triage && Array.isArray(s.triage.pages) ? s.triage.pages : null;
    const thumbs = byId("thumbList") ? byId("thumbList").children : [];
    const out = [];
    for (let p = 0; p < n; p++) {
      const r = tri && tri[p];
      let no = r && r.tier !== "unknown" ? (r.sheet_no || "") : "";
      if (!no && p === s.page && s.result && s.result.sheet_no) no = s.result.sheet_no;
      // the triage paint app.js puts on the (hidden) strip - the "highlight"
      // dim and the evidence tooltip - carried over to this list
      const t = thumbs[p];
      out.push({ p, no, noText: !!(r && r.tier === "unknown"), kind: s.kinds[p] || "",
                 st: (s.pageStatus && s.pageStatus[p]) || "idle", cur: p === s.page,
                 dim: !!(t && t.classList.contains("tri-dim")), triTitle: t && r ? t.title : "" });
    }
    return out;
  }
  function groupPages(list) {
    const g = new Map();
    for (const it of list) {
      const d = discOf(it.no);
      if (!g.has(d.key)) g.set(d.key, { key: d.key, name: d.name, items: [] });
      g.get(d.key).items.push(it);
    }
    return [...g.values()].sort((a, b) => discRank(a.key) - discRank(b.key));
  }
  const ST_TEXT = { idle: "not detected", running: "detecting", done: "detected", error: "failed" };
  function goPage(p) {
    const s = S();
    if (p === s.page) return;
    const t = byId("thumbList") && byId("thumbList").children[p];
    if (t) t.click();
    else { const sel = byId("pageSel"); if (sel) sel.value = p; call("selectPage", p); }
  }
  function renderSheets() {
    const body = byId("sheetsBody");
    if (!body) return;
    const list = pageList();
    byId("sheetsN").textContent = list.length ? String(list.length) : "";
    const da = byId("detectAllBtn"), px = byId("sheetsDetectAll");
    if (px) { px.hidden = !(da && list.length > 1); if (da) px.textContent = da.textContent || "Detect all"; }
    body.innerHTML = "";
    if (!list.length) { body.append(h("div", { class: "rc-empty" }, "Open a plan set to see its sheets.")); return; }
    // Pages that print the SAME sheet number are the document's own doing
    // (a title block never updated - Belle Farms Structural prints A1.00 on
    // all three pages, checked against the PDF by the peer session). Said
    // once, so a repeated number does not read as a broken list.
    const seen = {};
    for (const it of list) if (it.no) seen[it.no] = (seen[it.no] || 0) + 1;
    const dup = Object.entries(seen).filter(([, n]) => n > 1);
    if (dup.length) body.append(h("div", { class: "sh-dup", role: "note" },
      dup.map(([no, n]) => `${n} pages print the same sheet number, ${no}`).join("; ") + " - it is on the drawings, not a read error."));
    for (const g of groupPages(list)) {
      body.append(h("div", { class: "sh-group", title: DISC_TITLE },
        h("span", null, g.name), h("span", { class: "sh-group-n" }, String(g.items.length))));
      for (const it of g.items) {
        body.append(h("button", {
          type: "button", class: "sh-row" + (it.cur ? " cur" : "") + (it.dim ? " dim" : ""), "data-p": it.p,
          "aria-current": it.cur ? "page" : null,
          title: (it.triTitle ? it.triTitle + "\n" : "") +
            `page ${it.p + 1}${it.kind ? " — " + it.kind : ""} · ${ST_TEXT[it.st] || it.st}`,
          onclick: () => goPage(it.p),
        },
          h("span", { class: "sh-no" }, it.no || (it.noText ? "no text" : "—")),
          h("span", { class: "sh-pg" }, `page ${it.p + 1}`),
          h("span", { class: "sh-dot st-" + it.st, "aria-label": ST_TEXT[it.st] || it.st })));
      }
    }
    const c = body.querySelector(".sh-row.cur");
    if (c && c.scrollIntoView) c.scrollIntoView({ block: "nearest" });
  }
  let sheetsRaf = 0;
  const sheetsSoon = () => { if (!sheetsRaf) sheetsRaf = requestAnimationFrame(() => { sheetsRaf = 0; renderSheets(); if (cur === "plans") renderPlanCards(); }); };
  const tl = byId("thumbList");
  if (tl) new MutationObserver(sheetsSoon).observe(tl, { childList: true, subtree: true, attributes: true,
                                                         attributeFilter: ["class"], characterData: true });
  const dab = byId("detectAllBtn");
  if (dab) new MutationObserver(sheetsSoon).observe(dab, { childList: true, characterData: true, subtree: true });

  /* ------------------------------------------------------------- PLANS */
  // `info`: the server's whole answer (GET /approved) - `pages`, `outdated` (OUT OF DATE / NOT IN THE BID, by page) and
  // `notes` (by page: "Earthwork from the old calculator was removed. Redo it in Cut / fill.", server.py
  // _ew_removed_pages) - the same words the fork's status bar, report and bid sheet carry. `job`: whose answer it is.
  const approvedCache = { job: null, at: 0, pages: [], info: null };
  async function approvedPages(force) {
    const s = S();
    if (!s.job) return [];
    const job = s.job;
    if (!force && approvedCache.job === job && Date.now() - approvedCache.at < 15000) return approvedCache.pages;
    try {
      const r = await fetch(`/api/job/${job}/approved`);
      const j = r.ok ? await r.json() : { pages: [] };
      approvedCache.job = job; approvedCache.at = Date.now(); approvedCache.pages = j.pages || [];
      approvedCache.info = r.ok ? j : null;
    } catch (e) { approvedCache.job = job; approvedCache.pages = []; approvedCache.info = null; }
    return approvedCache.pages;
  }
  // the server's answer for the job on screen, or null (never another job's)
  const approvedInfo = () => (approvedCache.job && approvedCache.job === S().job ? approvedCache.info : null);

  // SHELL-PURE-BEGIN (tests/test_redesign_frame.js evaluates this block on its own: no DOM, no closure)
  // The approvals' notes, from GET /approved. page null: the job's - every OUT OF DATE / NOT IN THE BID first (by
  // page), then the per-page notes (the old calculator's removed earthwork); a page number: that page's own.
  function approvalNotes(info, page) {
    if (!info) return [];
    const pick = (o) => (page == null
      ? Object.keys(o || {}).sort((a, b) => a - b).map((k) => o[k])
      : [(o || {})[String(page)]]);
    return [...pick(info.outdated), ...pick(info.notes)].map((n) => (n == null ? "" : String(n)))
      .filter((n, i, a) => n && a.indexOf(n) === i);
  }
  // how a note weighs in Bid readiness: every one of them means the bid does not hold what the screen shows
  function noteReadiness(note) {
    const n = String(note || "");
    if (/^OUT OF DATE/.test(n)) return { label: "Approval", value: "out of date" };
    if (/^NOT IN THE BID/.test(n)) return { label: "Approval", value: "not in the bid" };
    if (/old calculator was removed/i.test(n)) return { label: "Earthwork", value: "removed" };
    return { label: "Approval", value: "check" };
  }
  // THE JOB'S DOWNLOAD LINKS (app.js #dlLink / #reportLink / #bidsheetLink): what each must show and point at, from
  // the server's list of approved pages - never from what an Approve in this tab did, and never another job's. The
  // cross-sheet unticks (app.js xsKey: "<document> p<N> :: <label>") ride along only for THIS document.
  function linkPlan(job, page, pages, ignoreKeys, docName) {
    const ok = !!job && Array.isArray(pages);
    const any = ok && pages.length > 0;
    const mine = (docName || "?") + " p";
    const q = [];
    // parsed exactly as app.js bsIgnoreQuery does (fork 51c90b2), so its bsLinkSync and this write the same href
    for (const k of ignoreKeys || []) {
      const s = String(k);
      if (!s.startsWith(mine)) continue;
      const m = /^(\d+) :: ([\s\S]+)$/.exec(s.slice(mine.length));
      if (m) q.push("ignore=" + encodeURIComponent(m[1] + ":" + m[2]));
    }
    const qs = q.length ? "?" + q.join("&") : "";
    return {
      dl: { show: any && pages.includes(page), href: job ? `/api/job/${job}/page/${page}/elements` : "" },
      report: { show: any, href: job ? `/api/job/${job}/report.pdf${qs}` : "" },
      bid: { show: any, href: job ? `/api/job/${job}/bidsheet.csv${qs}` : "" },
    };
  }
  // SHELL-PURE-END

  function approvalNotesEl(notes) {
    if (!notes || !notes.length) return null;
    return h("div", { class: "sm-notes", role: "note" }, notes.map((n) =>
      h("div", { class: "rd-verdict missing" }, n)));
  }

  // F1 / F2 (live proof 2026-10-01): the links were shown by an Approve in this tab and never re-pointed, so after
  // opening another job "Bid sheet CSV" downloaded the PREVIOUS job's bid sheet, and on a job approved earlier they
  // stayed hidden (Summary's buttons read them). Now they follow the job on screen and the server's /approved.
  // ONE OWNER (fork 51c90b2, B3): app.js's applyOpened now hides the links itself (jobLinksReset) and asks
  // jobLinksSync, which shows #dlLink for the FIRST approved page when this one is not, and fetched /approved beside
  // this: two requests per open, and a link shown by one and hidden by the other. Its jobLinksSync is routed here
  // (below, beside the wraps); its reset, the Approve and the untick's bsLinkSync stay app.js's and are followed.
  const DL_LINKS = ["dlLink", "reportLink", "bidsheetLink"];
  let linksGen = 0;
  let linksObs = null;
  // this file's own writes to the links are not echoed back as "app.js changed them" (one request, not two)
  const linksQuiet = () => { if (linksObs) linksObs.takeRecords(); };
  function linkSet(a, p) {
    if (!a) return;
    if (!p.show) { if (!a.hidden) a.hidden = true; return; }
    const href = srcUrl(p.href);
    if (a.getAttribute("href") !== href) a.setAttribute("href", href);
    if (a.hidden) a.hidden = false;
  }
  async function linksSync() {
    const job = S().job || null;
    const my = ++linksGen;
    // a link that names another job goes AT ONCE, before any answer arrives
    for (const id of DL_LINKS) {
      const a = byId(id);
      if (a && !a.hidden && (!job || !String(a.getAttribute("href") || "").includes(`/api/job/${job}/`))) a.hidden = true;
    }
    linksQuiet();
    if (!job) return;
    const pages = await approvedPages(true);
    const s = S();
    if (my !== linksGen || s.job !== job) return;
    const plan = linkPlan(job, s.page, approvedCache.info ? pages : null, s.xsIgnore, s.docName);
    linkSet(byId("dlLink"), plan.dl);
    linkSet(byId("reportLink"), plan.report);
    linkSet(byId("bidsheetLink"), plan.bid);
    linksQuiet();
  }
  // app.js's own writers (the Approve, the cross-sheet untick's bsLinkSync) are followed and re-checked; such a
  // write also voids an answer still on its way (asked before an Approve, it would hide what the Approve showed)
  let linksT = 0;
  const linksSoon = () => { clearTimeout(linksT); linksT = setTimeout(() => { linksSync().then(() => { if (cur === "summary") summarySoon(); }); }, 120); };
  {
    const lo = new MutationObserver(() => { linksGen++; linksSoon(); });
    linksObs = lo;
    for (const id of DL_LINKS) { const a = byId(id); if (a) lo.observe(a, { attributes: true, attributeFilter: ["href", "hidden"] }); }
  }
  function renderPlans() {
    const s = S();
    plansView.innerHTML = "";
    if (!s.job) {
      plansView.append(h("div", { class: "view-empty" }, "Open a plan set from Projects to see its sheets."));
      return;
    }
    const list = pageList();
    const done = list.filter((x) => x.st === "done").length;
    const da = byId("detectAllBtn");
    const copyBtn = h("button", { type: "button", class: "btn", title: "Copy a link that opens this project (for anyone who can already see it)",
      onclick: async () => {
        const link = `${location.origin}/#job=${s.job}`;
        try { await navigator.clipboard.writeText(link); copyBtn.lastChild.textContent = "Link copied"; }
        catch (e) { copyBtn.lastChild.textContent = link; }
        setTimeout(() => { if (copyBtn.isConnected) copyBtn.lastChild.textContent = "Copy link"; }, 2500);
      } }, icon(ICON.link), h("span", null, "Copy link"));
    plansView.append(
      h("div", { class: "vw-head" },
        h("div", { class: "vw-title" },
          h("div", { class: "tm-crumb" }, "Project"),
          h("h1", null, s.docName || "This plan set"),
          h("div", { class: "pl-pills" },
            h("span", { class: "pl-pill" + (done && done === list.length ? " ok" : "") },
              `${done} of ${list.length} sheet${list.length === 1 ? "" : "s"} detected`),
            h("span", { class: "pl-pill", id: "plApproved" }, "approved: …"))),
        h("div", { class: "vw-acts" },
          da && list.length > 1 ? h("button", { type: "button", class: "btn", id: "plDetectAll",
            onclick: () => da.click() }, da.textContent || "Detect all") : null,
          copyBtn,
          h("button", { type: "button", class: "btn primary", onclick: () => setModule("lines", "tab") },
            "Open takeoff", icon(ICON.arrow)))),
      h("div", { class: "pl-layout" },
        h("div", { class: "pl-main" },
          h("section", { class: "vw-card pl-sheets-card" },
            h("div", { class: "vw-card-head" }, h("h2", null, "Sheets"),
              h("span", { class: "vw-note", id: "plMeta" }, `${list.length} in this set`)),
            triageHome,
            h("div", { class: "pl-groups", id: "plGroups" })),
          h("section", { class: "vw-card pl-result-card" },
            h("div", { class: "vw-card-head" }, h("h2", null, "Approved takeoff"),
              h("span", { class: "vw-note" }, "from approved sheets only")),
            h("div", { class: "pl-tiles", id: "plTiles" }))),
        h("div", { class: "pl-side" },
          h("section", { class: "vw-card pl-access" },
            h("div", { class: "vw-card-head" }, h("h2", null, "Access")),
            h("div", { id: "plAccess", class: "pl-access-body" }, h("div", { class: "rc-empty" }, "Reading…"))),
          h("section", { class: "vw-card pl-activity" },
            h("div", { class: "vw-card-head" }, h("h2", null, "Activity")),
            h("div", { id: "plActivity" }, h("div", { class: "rc-empty" }, "Reading…"))))));
    const tb = byId("triageBar");
    if (tb && tb.parentElement !== triageHome) triageHome.append(tb);
    renderPlanCards();
    approvedPages().then((pages) => {
      const p = byId("plApproved");
      if (p && cur === "plans") {
        p.textContent = `${pages.length} approved`;
        p.classList.toggle("ok", pages.length > 0);
      }
      renderPlanTiles(pages);
    });
    renderPlanAccess();
    renderPlanActivity();
  }
  // ACCESS: who can open this project - the job's owner (or "shared" when
  // it has none) and, for an admin, the admins who see every project.
  async function renderPlanAccess() {
    const box = byId("plAccess");
    const s = S();
    if (!box) return;
    let row = null, me = null, users = null;
    try { const r = await fetch("/api/projects"); if (r.ok) row = ((await r.json()).projects || []).find((x) => x.job_id === s.job) || null; } catch (e) { /* offline */ }
    try { const r = await fetch("/api/me"); if (r.ok) me = await r.json(); } catch (e) { /* offline */ }
    // the member list answers 409 with no accounts and 404 to a non-admin:
    // ask only an admin on a box that has accounts (no failed request logged)
    if (me && me.accounts && (me.staff || me.role === "admin")) {
      try { const r = await fetch("/api/users"); if (r.ok) users = (await r.json()).users || []; } catch (e) { /* not admin */ }
    }
    if (!box.isConnected) return;
    box.innerHTML = "";
    const person = (name, role, note) => h("div", { class: "pl-person" },
      h("span", { class: "tm-av", "aria-hidden": "true" }, String(name || "?").split("@")[0].slice(0, 2).toUpperCase()),
      h("div", { class: "pl-person-t" }, h("div", { class: "tm-name" }, name), note ? h("div", { class: "tm-status" }, note) : null),
      role ? h("span", { class: "tm-pill r-" + role }, { admin: "Admin", estimator: "Estimator", viewer: "Viewer", owner: "Owner" }[role] || role) : null);
    if (!me || !me.accounts) {
      box.append(h("div", { class: "rc-empty" }, "This server has no user accounts, so anyone who can reach it can open this project. ",
        h("a", { href: "#team", class: "tm-link", onclick: (e) => { e.preventDefault(); if (window.coreTeam) coreTeam.show(true); } }, "About accounts")));
      return;
    }
    if (row && row.owner) box.append(person(row.owner, "owner", row.mine ? "you" : "uploaded it"));
    else box.append(h("div", { class: "rc-empty" }, row && row.shared ? "Shared with everyone who can sign in to this server." : "Owner unknown."));
    if (users) {
      for (const u of users) {
        const role = u.role || (u.staff ? "admin" : "estimator");
        if (role === "admin" && (!row || u.user !== row.owner)) box.append(person(u.user, "admin", "sees every project"));
      }
    } else box.append(h("div", { class: "tm-dim pl-note" }, "Admins can open every project."));
  }
  // ACTIVITY: what happened to this project, newest first - the server's
  // audit and ledger records (GET /api/job/{id}/activity)
  const ACT_PHRASE = {
    project_upload: "Uploaded", project_open_path: "Opened", demo_open: "Opened (demo, fresh)",
    sheet_detect: "Detect run", sheet_approve: "Sheet approved", project_reset: "Reset",
    scale_set: "Scale set", approve_export: "Approved and exported", cutfill_rows: "Cut / fill rows added",
    trench_set: "Trench inputs set", trench_cleared: "Trench cleared", structure_set: "Structure inputs set",
    boundary_set: "Limit drawn", boundary_cleared: "Limit cleared", workzones_set: "Work area set",
    check_done: "Question checked",
    // the training ledger's own names for the same events (rows from before
    // the audit log, merged in by the server's job_activity)
    demo_opened: "Opened (demo, fresh)", job_reset: "Reset", job_deleted: "Deleted", project_delete: "Deleted",
    structure_calc: "Structure computed", check_undone: "Question reopened", scale_pick_retired: "Scale choice replaced",
    login: "Signed in",
  };
  // anything else reads as a sentence, not as an identifier
  const actWords = (a) => { const t = String(a || "").replace(/_/g, " "); return t.charAt(0).toUpperCase() + t.slice(1); };
  async function renderPlanActivity() {
    const box = byId("plActivity");
    const s = S();
    if (!box) return;
    let ev = null;
    try { const r = await fetch(`/api/job/${s.job}/activity?limit=12`); if (r.ok) ev = (await r.json()).events || []; } catch (e) { /* not there */ }
    if (!box.isConnected) return;
    box.innerHTML = "";
    if (ev == null) { box.append(h("div", { class: "rc-empty" }, "This server does not record project activity yet.")); return; }
    if (!ev.length) { box.append(h("div", { class: "rc-empty" }, "Nothing recorded for this project yet.")); return; }
    box.append(h("ol", { class: "pl-timeline" }, ev.map((e) => h("li", null,
      h("span", { class: "pl-dot", "aria-hidden": "true" }),
      h("div", { class: "pl-tl-t" },
        h("div", null, ACT_PHRASE[e.action] || actWords(e.action),
          e.page != null ? h("span", { class: "tm-dim" }, ` · page ${e.page + 1}`) : null),
        e.user || e.detail ? h("div", { class: "tm-status" }, [e.user, e.detail].filter(Boolean).join(" · ")) : null),
      h("time", { datetime: e.ts || "", title: e.ts || "" }, agoShort(e.ts))))));
  }
  function agoShort(iso) {
    const t = Date.parse(iso || "");
    if (!isFinite(t)) return "";
    const sec = (Date.now() - t) / 1000;
    if (sec < 90) return "just now";
    if (sec < 3600) return `${Math.round(sec / 60)} min`;
    if (sec < 86400) return `${Math.round(sec / 3600)} h`;
    return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric" });
  }
  // The mockup's trade tiles, from the APPROVED sheets only (the same
  // per-label/unit sums as the Summary and the report PDF). Nothing
  // approved = no numbers, and the tile row says why.
  async function renderPlanTiles(pages) {
    const box = byId("plTiles");
    if (!box) return;
    box.innerHTML = "";
    if (!pages || !pages.length) {
      box.append(h("div", { class: "pl-tiles-empty" },
        "Trade totals appear here once a sheet is approved (Approve & export) - they are the approved sheets' own numbers."));
      // NOT IN THE BID can stand with nothing approved (the job's cut / fill rows on a page not approved yet)
      const n0 = approvalNotesEl(approvalNotes(approvedInfo(), null));
      if (n0) box.append(n0);
      return;
    }
    const data = await approvedRows();
    if (cur !== "plans" || !data || data.error || !data.groups) return;
    // the approvals' notes under the tiles: the tiles add approved sheets, and these say which approvals do not stand
    const notesEl = approvalNotesEl(data.notes);
    const unitRank = (u) => ({ CY: 0, LF: 1, ft: 1, SF: 2, EA: 3 }[u] ?? 4);
    for (const g of data.groups) {
      const byUnit = new Map();
      for (const it of g.items) {
        if (it.unit === "pt") continue;
        // a CY row is keyed by what it is: cut / fill / net, a trench's dig / bedding / backfill,
        // a topsoil row by its name (a tile that adds them is a number that exists nowhere)
        const cfk = it.unit === "CY" && typeof bidCfKind === "function" ? bidCfKind(it.label) : null;
        const lab = String(it.label == null ? "" : it.label);
        const what = it.unit !== "CY" ? "" : cfk ? (cfk === "export" || cfk === "import" ? "net" : cfk)
          : (it.kind === "trench_dig" || /^Trench excavation \(/.test(lab)) ? "dig"
          : (it.kind === "trench_bed" || /^Bedding gravel \(/.test(lab)) ? "bedding"
          : (it.kind === "trench_backfill" || /^Backfill \(/.test(lab)) ? "backfill"
          : /topsoil/i.test(lab) ? lab.toLowerCase() : "";
        const u = showUnit(it.unit) + (what ? " " + what : "");
        byUnit.set(u, (byUnit.get(u) || 0) + (Number(it.qty) || 0));
      }
      // every figure the trade holds, none dropped: a trench tile keeps its footage beside its
      // yards, an earthwork tile its square feet and feet beside the cut / fill. One figure
      // stands large; more than one go one per line (two on a line were cut off at the edge)
      const rankOf = (u) => unitRank(String(u).split(" ")[0]);
      const units = [...byUnit.entries()].filter(([, q]) => q).sort((a, b) => rankOf(a[0]) - rankOf(b[0]));
      const many = units.length > 1;
      box.append(h("div", { class: "pl-tile" },
        h("div", { class: "pl-tile-t" }, g.trade),
        h("div", { class: "pl-tile-n" + (many ? " many" : "") },
          units.length ? units.map(([u, q], i) => many
            ? h("div", { class: "fig" }, fmtN(q, q < 100 && q % 1 ? 1 : 0), h("span", { class: "u" }, " " + u))
            : [i ? h("span", { class: "sep" }, " / ") : null,
               fmtN(q, q < 100 && q % 1 ? 1 : 0), h("span", { class: "u" }, " " + u)]) : "—"),
        h("div", { class: "pl-tile-s" }, g.sub ? money0(g.sub) : "not priced",
          ` · ${g.items.length} item${g.items.length === 1 ? "" : "s"}`)));
    }
    if (notesEl) box.append(notesEl);
  }
  function renderPlanCards() {
    const box = byId("plGroups");
    const s = S();
    if (!box || !s.job) return;
    box.innerHTML = "";
    for (const g of groupPages(pageList())) {
      box.append(h("h2", { class: "pl-group", title: DISC_TITLE }, g.name,
        h("span", { class: "pl-group-n" }, String(g.items.length))));
      const grid = h("div", { class: "pl-grid" });
      for (const it of g.items) {
        grid.append(h("button", {
          type: "button", class: "pl-card" + (it.cur ? " cur" : "") + (it.dim ? " dim" : ""),
          onclick: () => { goPage(it.p); setModule("lines", "tab"); },
          title: (it.triTitle ? it.triTitle + "\n" : "") + `Open page ${it.p + 1} in the takeoff`,
        },
          h("div", { class: "pl-thumb" },
            h("img", { loading: "lazy", alt: `page ${it.p + 1}`,
                       src: srcUrl(`/api/job/${s.job}/page/${it.p}/image?dpi=28`) })),
          h("div", { class: "pl-foot" },
            h("span", { class: "pl-no" }, it.no || (it.noText ? "no text" : `page ${it.p + 1}`)),
            it.no || it.noText ? h("span", { class: "pl-pg" }, `page ${it.p + 1}`) : null,
            h("span", { class: "pl-st st-" + it.st }, ST_TEXT[it.st] || it.st))));
      }
      box.append(grid);
    }
  }

  /* ----------------------------------------------------------- SUMMARY
     The takeoff by trade for THIS sheet (computeTakeoffRows - the same pass
     the right rail, the CSV and Approve use) or for EVERY APPROVED sheet
     (each page's approved export, summed per label and unit exactly as the
     report PDF does, with nothing de-duplicated across sheets). Read-only:
     quantities and prices are edited in the Takeoff panel beside the sheet. */
  let smScope = LS.get("core-sum-scope", "sheet");
  const UNIT_SHOW = { ft: "LF", pt: "pt" };
  const showUnit = (u) => UNIT_SHOW[u] || u || "";
  function calcSource(label) {
    if (typeof CF_ROW_RE !== "undefined" && CF_ROW_RE.test(label)) return "cut / fill engine";
    if (typeof FL_LABEL_RX !== "undefined" && FL_LABEL_RX.test(label)) return "flooring rooms";
    if (typeof ST_LABELS !== "undefined" && ST_LABELS.some((x) => label.startsWith(x))) return "structure dig";
    if (/\(run\)$|^Trench excavation \(|^Bedding gravel \(|^Backfill \(/.test(label)) return "pipe trench";
    return "calculator";
  }
  function rowSource(it) {
    if (it.isCalc) return calcSource(it.label);
    if (it.isObj) return "counted on the sheet";
    const n = parseInt(it.cnt, 10);
    return isFinite(n) && n > 0 ? `measured · ${fmtN(n)} run${n === 1 ? "" : "s"}` : "measured on the sheet";
  }
  function sheetRows() {
    const f = F("computeTakeoffRows");
    const r = f ? f() : null;
    if (!r) return null;
    return {
      groups: r.groups.filter((g) => g.items.length).map((g) => ({
        trade: g.trade, sub: g.sub,
        items: g.items.map((it) => ({
          // a family's feet minus what its trench runs carry (computeTakeoffRows sets `counted`)
          label: it.label, qty: it.counted != null ? it.counted : it.qty, unit: it.unit, price: it.price,
          ext: it.rowExt != null ? it.rowExt : it.ext,
          source: rowSource(it), edited: !!it.ov, priced: it.priced !== false,
        })),
      })),
      grand: r.grand,
    };
  }
  async function approvedRows() {
    const f = F("xsLoadApproved");
    if (!f) return null;
    let pages;
    try { pages = await f(); } catch (e) { return { error: String(e && e.message || e) }; }
    await approvedPages(true);
    const notes = approvalNotes(approvedInfo(), null);
    if (!pages || !pages.length) return { groups: [], grand: 0, pages: 0, notes };
    const order = typeof TRADES !== "undefined" ? TRADES : [];
    const tr = new Map();
    let grand = 0;
    const sheetName = (pg) => {
      const t = S().triage && S().triage.pages && S().triage.pages[pg];
      return t && t.sheet_no ? t.sheet_no : `page ${pg + 1}`;
    };
    // the feet a trench run carries are not counted on its family too (the bid sheet's rule):
    // a tagged run (kind trench_run, run_of) subtracts its own feet from the family named,
    // a legacy "(run)" row stands for the whole family
    const counted = (items) => {
      const runs = new Map();
      for (const it of items) {
        const lab = String(it.label == null ? "" : it.label);
        const legacy = lab.endsWith(" (run)");
        if (it.kind !== "trench_run" && !legacy) continue;
        const of = it.run_of || (legacy ? lab.slice(0, -6) : "");
        if (!of) continue;
        const r = runs.get(of) || { lf: 0, tagged: true };
        r.lf += Number(it.qty) || 0; r.tagged = r.tagged && it.kind === "trench_run" && !!it.run_of;
        runs.set(of, r);
      }
      const out = new Map();
      for (const it of items) {
        if (it.unit || it.length_ft == null) continue;
        const r = runs.get(String(it.label));
        if (!r) continue;
        const ft = Number(it.length_ft) || 0;
        out.set(it, Math.max(0, ft - (r.tagged ? Math.min(ft, r.lf) : ft)));
      }
      return out;
    };
    for (const { page, exp } of pages) {
      const items = (exp && exp.items) || [];
      const cnt = counted(items);
      for (const it of items) {
        const trade = it.trade || "General";
        const unit = it.unit || (it.length_ft != null ? "ft" : "");
        const qty = cnt.has(it) ? cnt.get(it) : (it.qty != null ? it.qty : it.length_ft);
        if (!tr.has(trade)) tr.set(trade, new Map());
        const rows = tr.get(trade);
        const key = it.label + "\u0000" + unit;
        if (!rows.has(key)) rows.set(key, { label: it.label, unit, qty: 0, ext: 0, prices: new Set(),
                                            sheets: [], edited: false, anyExt: false, kind: it.kind || null });
        const r = rows.get(key);
        if (isFinite(qty)) r.qty += Number(qty);
        if (isFinite(it.extended) && it.extended != null) { r.ext += Number(it.extended); r.anyExt = true; }
        if (it.unit_price != null) r.prices.add(Number(it.unit_price));
        if (it.qty_set_by) r.edited = true;
        const sn = sheetName(page);
        if (!r.sheets.includes(sn)) r.sheets.push(sn);
      }
    }
    const groups = [];
    const names = [...tr.keys()].sort((a, b) => {
      const ia = order.indexOf(a), ib = order.indexOf(b);
      return (ia < 0 ? 99 : ia) - (ib < 0 ? 99 : ib) || a.localeCompare(b);
    });
    for (const trade of names) {
      const items = [...tr.get(trade).values()].sort((a, b) => b.qty - a.qty).map((r) => ({
        label: r.label, qty: r.qty, unit: r.unit, kind: r.kind,
        price: r.prices.size === 1 ? [...r.prices][0] : null,
        mixed: r.prices.size > 1,
        ext: r.anyExt ? r.ext : null,
        source: (r.sheets.length === 1 ? "sheet " : "sheets ") + r.sheets.join(", "),
        edited: r.edited, priced: true,
      }));
      const sub = items.reduce((a, x) => a + (x.ext || 0), 0);
      grand += sub;
      groups.push({ trade, items, sub });
    }
    return { groups, grand, pages: pages.length, notes };
  }
  function tableFor(data) {
    const t = h("table", { class: "sm-table" },
      h("thead", null, h("tr", null,
        h("th", { class: "l" }, "Item"), h("th", null, "Qty"), h("th", { class: "c" }, "Unit"),
        h("th", null, "Unit price"), h("th", null, "Extended"), h("th", { class: "l" }, "Source"),
        h("th", { class: "c" }, h("span", { class: "sr-only" }, "Set by")))));
    const tb = h("tbody");
    for (const g of data.groups) {
      tb.append(h("tr", { class: "sm-group" },
        h("td", { class: "l", colspan: 4 }, g.trade),
        h("td", null, g.sub ? money0(g.sub) : "—"),
        h("td", { colspan: 2 })));
      for (const it of g.items) {
        const unpriced = it.price == null && !it.mixed && it.unit !== "pt";
        tb.append(h("tr", { class: "sm-item" + (unpriced ? " unpriced" : "") },
          h("td", { class: "l mono" }, it.label),
          h("td", { class: "mono" }, fmtN(it.qty, Math.abs(it.qty) < 100 && it.qty % 1 ? 1 : 0)),
          h("td", { class: "c mono" }, showUnit(it.unit)),
          h("td", { class: "mono" + (unpriced ? " np" : "") }, it.unit === "pt" ? "—" : it.mixed ? "mixed"
            : it.price != null ? money(it.price) : "not priced"),
          h("td", { class: "mono" }, it.ext != null && it.ext !== 0 ? money0(it.ext) : "—"),
          h("td", { class: "l src" }, it.source),
          h("td", { class: "c" }, it.edited
            ? h("span", { class: "chip warn", title: "A reviewer typed this quantity over the engine's" }, "qty edited")
            : null)));
      }
    }
    t.append(tb, h("tfoot", null, h("tr", { class: "sm-total" },
      h("td", { class: "l", colspan: 4 }, "Total"),
      h("td", { class: "mono" }, money0(data.grand || 0)),
      h("td", { colspan: 2 }))));
    return t;
  }
  // BID READINESS - the three-state contract the removed earthwork calculator
  // carried (supplied / assumed / missing; ready only when nothing is missing
  // or assumed), rebuilt over what this app ACTUALLY knows about the sheet.
  // Anything it cannot see is left off rather than called supplied.
  function readiness(rowsData) {
    const s = S();
    const items = [];
    const add = (label, state, value, why) => items.push({ label, state, value, why });
    const r = s.result;
    const sc = r && r.scale;
    if (!r) add("Scale", "missing", "sheet not detected", "Detect this sheet first.");
    else if (!sc || !sc.ft_per_paper_inch) add("Scale", "missing", "none", "No scale on this sheet - type it in the Sheet card.");
    // the page's scale and the sheet's review record part ways (app.js scaleRecordChip: "not approved yet: the review
    // says ...", or a saved scale dropped): the words beside the scale chip, never "supplied"
    else if (typeof scaleRecordChip === "function" && scaleRecordChip())
      add("Scale", "assumed", `1" = ${fmtN(sc.ft_per_paper_inch, sc.ft_per_paper_inch % 1 ? 1 : 0)}'`, scaleRecordChip());
    else if (sc.picked_by_reviewer || sc.typed_by_reviewer)
      add("Scale", "supplied", `1" = ${fmtN(sc.ft_per_paper_inch, sc.ft_per_paper_inch % 1 ? 1 : 0)}'`, "set by you");
    else if (sc.agrees === true)
      add("Scale", "supplied", `1" = ${fmtN(sc.ft_per_paper_inch, sc.ft_per_paper_inch % 1 ? 1 : 0)}'`, "printed and drawn agree");
    else add("Scale", "assumed", `1" = ${fmtN(sc.ft_per_paper_inch, sc.ft_per_paper_inch % 1 ? 1 : 0)}'`,
      "one reading (" + ({ text: "printed words", ocr: "OCR of the caption", "graphic-bar": "the drawn bar" }[sc.source]
        || sc.source || "engine") + ") - confirm it in the Sheet card");
    const wz = Array.isArray(s.workZones) ? s.workZones.length : 0;
    if (r) add("Work area", wz ? "supplied" : "info", wz ? `${wz} box${wz === 1 ? "" : "es"}` : "whole sheet",
      wz ? "only length inside your boxes counts" : "every line on the sheet counts");
    const extra = Array.isArray(s.extraItems) ? s.extraItems : [];
    const has = (rx) => extra.some((it) => rx.test(it.label || ""));
    const cf = typeof cfState !== "undefined" ? cfState : null;
    const cfs = cf && cf.summary;
    // the engine's rows only (app.js cfIsRow)
    const isCf = (it) => (typeof cfIsRow === "function" ? cfIsRow(it)
      : typeof CF_ROW_RE !== "undefined" && CF_ROW_RE.test(it.label || ""));
    // this page's approval notes from the server (GET /approved, fetched by renderSummary before this runs): OUT OF
    // DATE / NOT IN THE BID, and "Earthwork from the old calculator was removed. Redo it in Cut / fill." - each is
    // money the bid does not hold as the screen shows it, so each is "missing", in the server's own words
    for (const n of approvalNotes(approvedInfo(), s.page)) {
      const w = noteReadiness(n);
      add(w.label, "missing", w.value, n);
    }
    if (cfs || extra.some(isCf)) {
      if (!cfs) add("Cut / fill surfaces", "assumed", "in the takeoff", "open Earthwork to see the engine's current result");
      // the engine's refusal in its own words, whole: its second sentence is often the way out ("... is still
      // stopping. Try again in a minute.", "... Reload this page to keep working.")
      else if (cfs.error) add("Cut / fill surfaces", "missing", "refused", String(cfs.error));
      else if (cfs.stale) add("Cut / fill surfaces", "assumed", "stale", "an input changed - press Compute again");
      else add("Cut / fill surfaces", "supplied", `${fmtN(cfs.cut_cy)} / ${fmtN(cfs.fill_cy)} CY`, "computed by the engine");
      if (cfs && !cfs.error) {
        // the source the engine reports: a reading is not a confirmation
        const src = String(cfs.scale_source || "");
        const st = !src ? "assumed" : src === "read in review" ? "assumed" : "supplied";
        const why = !src ? "not confirmed in the cut / fill panel"
          : src === "read in review" ? "read off the sheet, not confirmed by anyone"
          : src === "confirmed in review" ? "confirmed on the sheet review"
          : src === "typed" ? "typed in the cut / fill panel" : "confirmed in the cut / fill panel";
        // two decimals at most in print (the host's cfScaleTxt); the number in force is not rounded
        const cfScale = typeof cfScaleTxt === "function" ? cfScaleTxt(cfs.scale_ft_per_in) : cfs.scale_ft_per_in;
        add("Cut / fill scale", st, cfs.scale_ft_per_in ? `1" = ${cfScale}'` : "not set", why);
        if (cfs.coverage != null) {
          const cov = Math.round(cfs.coverage * 100);
          add("Cut / fill coverage", cov >= 95 ? "supplied" : "assumed", `${cov} % of the limit`,
            cov >= 100 ? "both surfaces reach the whole limit" : `${100 - cov} % of the limit has no surface under it and is left out`);
        }
      }
    }
    if (has(/\(run\)$|^Trench excavation \(/))
      add("Trench depths", "assumed", "in the takeoff", "CORE cannot tell typed depths from the defaults - check the trench panel");
    if (typeof ST_LABELS !== "undefined" && extra.some((it) => ST_LABELS.some((x) => (it.label || "").startsWith(x))))
      add("Dig depth", "assumed", "in the takeoff", "the structure panel's depths - CORE cannot tell typed from the 8 ft default");
    if (s.symInfo) {
      const src = s.symInfo.schedule_source;
      add("Door / window schedule", src === "text" ? "supplied" : src === "ocr" ? "assumed" : "missing",
        src === "text" ? "read" : src === "ocr" ? "read by OCR" : "none found",
        src === "text" ? "counts checked against the printed schedule"
          : src === "ocr" ? "read off a picture of the schedule - check the marks"
          : "counts are not checked against a schedule");
    }
    if (rowsData && rowsData.groups) {
      let unp = 0, n = 0;
      for (const g of rowsData.groups) for (const it of g.items) {
        if (it.unit === "pt") continue;
        n++;
        if (it.price == null && !it.mixed) unp++;
      }
      if (n) add("Unit prices", unp ? "missing" : "supplied", unp ? `${unp} of ${n} rows unpriced` : `${n} rows priced`,
        unp ? "rows without a price add nothing to the total" : "from your price book");
    }
    return items;
  }
  const RD_TEXT = { supplied: "supplied", assumed: "assumed", missing: "missing", info: "noted" };
  function readinessEl(items) {
    const box = h("div", { class: "rd-list" });
    for (const it of items) {
      box.append(h("div", { class: "rd-row" },
        h("div", { class: "rd-main" },
          h("div", { class: "rd-label" }, it.label),
          h("div", { class: "rd-why" }, [it.value, it.why].filter(Boolean).join(" · "))),
        h("span", { class: "chip " + it.state }, RD_TEXT[it.state] || it.state)));
    }
    const miss = items.filter((x) => x.state === "missing").length;
    const ass = items.filter((x) => x.state === "assumed").length;
    box.append(h("div", { class: "rd-verdict " + (miss ? "missing" : ass ? "assumed" : "supplied") },
      miss ? `${miss} item${miss === 1 ? " is" : "s are"} missing. The total is not bid-ready until ${miss === 1 ? "it is" : "they are"} entered.`
        : ass ? `${ass} item${ass === 1 ? " is" : "s are"} assumed. Check ${ass === 1 ? "it" : "them"} before you bid.`
        : "Every input CORE uses on this sheet is supplied."));
    return box;
  }
  let smGen = 0;
  async function renderSummary() {
    const s = S();
    const gen = ++smGen;
    summaryView.innerHTML = "";
    if (!s.job) {
      summaryView.append(h("div", { class: "view-empty" }, "Open a plan set from Projects first."));
      return;
    }
    // the server's truth first: the job's approved pages (the Report / Bid sheet buttons follow them, F2) and the
    // approvals' notes (readiness and the tables below say them)
    await linksSync();
    if (gen !== smGen) return;
    const csv = byId("csvBtn"), rep = byId("reportLink"), bs = byId("bidsheetLink");
    const pageLabel = (() => {
      const t = s.triage && s.triage.pages && s.triage.pages[s.page];
      return (t && t.sheet_no) || (s.result && s.result.sheet_no) || `page ${s.page + 1}`;
    })();
    const scopeBtn = (id, text) => h("button", {
      type: "button", role: "tab", class: "seg" + (smScope === id ? " on" : ""),
      "aria-selected": smScope === id ? "true" : "false",
      onclick: () => { smScope = id; LS.set("core-sum-scope", id); renderSummary(); },
    }, text);
    const head = h("div", { class: "vw-head" },
      h("div", { class: "vw-title" },
        h("h1", null, "Summary"),
        h("div", { class: "vw-meta" }, s.docName || "")),
      h("div", { class: "vw-head-r" },
        h("div", { class: "seg-group", role: "tablist", "aria-label": "Which sheets" },
          scopeBtn("sheet", `This sheet · ${pageLabel}`),
          scopeBtn("approved", "All approved sheets"))));
    const tableCard = h("section", { class: "vw-card sm-card" },
      h("div", { class: "vw-card-head" }, h("h2", null, "Takeoff by trade"),
        h("span", { class: "vw-note", id: "smNote" })),
      h("div", { class: "sm-body", id: "smBody" }, h("div", { class: "rc-empty" }, "Reading the takeoff…")));
    // a <section>, never an <aside>: style.css sizes every aside by --side-w
    const readyCard = h("section", { class: "vw-card rd-card" },
      h("div", { class: "vw-card-head" }, h("h2", null, "Bid readiness"),
        h("span", { class: "vw-note" }, "this sheet")),
      h("div", { id: "smReady" }));
    const acts = h("div", { class: "vw-acts sm-acts" },
      h("button", { type: "button", class: "btn primary", disabled: !csv || csv.disabled,
        title: csv ? csv.title : "", onclick: () => csv && csv.click() }, icon(ICON.csv), "Export CSV"),
      h("button", { type: "button", class: "btn", disabled: !(rep && !rep.hidden && rep.href),
        title: rep && !rep.hidden ? "Every approved sheet, with the overlays and the grand summary"
          : "Approve a sheet first - the report prints approved sheets",
        onclick: () => { if (rep && rep.href) window.open(rep.href, "_blank", "noopener"); } }, icon(ICON.doc), "Report PDF"),
      h("button", { type: "button", class: "btn", disabled: !(bs && !bs.hidden && bs.href),
        title: bs && !bs.hidden ? bs.title : "Approve a sheet first",
        onclick: () => { if (bs && bs.href) window.open(bs.href, "_blank", "noopener"); } }, icon(ICON.csv), "Bid sheet CSV"),
      h("button", { type: "button", class: "btn", onclick: () => window.print() }, icon(ICON.print), "Print"));
    // the exports sit in the head, beside the scope - a long table must not
    // push them below the fold
    head.querySelector(".vw-head-r").prepend(acts);
    summaryView.append(head, h("div", { class: "sm-grid" }, tableCard, readyCard));

    const sheet = sheetRows();
    byId("smReady").append(readinessEl(readiness(sheet)));
    let data;
    if (smScope === "approved") {
      data = await approvedRows();
      if (gen !== smGen) return;
    } else data = sheet;
    const body = byId("smBody"), note = byId("smNote");
    body.innerHTML = "";
    if (!data) {
      body.append(h("div", { class: "rc-empty" }, smScope === "approved"
        ? "Could not read the approved sheets." : "Detect this sheet to see its takeoff."));
      return;
    }
    if (data.error) { body.append(h("div", { class: "rc-empty err" }, data.error)); return; }
    // the approvals' own notes above the sums, in the server's words: the job's (All approved sheets) or this page's
    // (This sheet) - OUT OF DATE, NOT IN THE BID, and "Earthwork from the old calculator was removed. Redo it in Cut /
    // fill." where the old calculator's rows were taken out of the numbers below
    const notesEl = approvalNotesEl(smScope === "approved" ? data.notes : approvalNotes(approvedInfo(), s.page));
    if (notesEl) body.append(notesEl);
    if (!data.groups.length) {
      body.append(h("div", { class: "rc-empty" }, smScope === "approved"
        ? (data.pages
          ? `${data.pages} approved sheet${data.pages === 1 ? "" : "s"}, and no row in ${data.pages === 1 ? "it" : "them"} counts now.`
          : "No sheet has been approved yet. Approve & export a sheet and it is added here.")
        : "Nothing is in this sheet's takeoff yet - tick line styles or add a calculator's rows."));
      return;
    }
    note.textContent = smScope === "approved"
      ? `${data.pages} approved sheet${data.pages === 1 ? "" : "s"} · summed per label and unit, not de-duplicated across sheets`
      : "live · quantities and prices are edited in the Takeoff panel, beside the sheet (Lines tab)";
    body.append(tableFor(data));
  }

  /* ----------------------------------------------------------------- 3D */
  let want3d = false;
  function cfView(mode) {
    const fr = byId("cfFrame");
    try {
      const w = fr && fr.contentWindow;
      if (w && typeof w.setViewMode === "function") { w.setViewMode(mode); return true; }
    } catch (e) { /* not ready */ }
    return false;
  }
  async function openCf3d() {
    want3d = true;
    await call("cfOpen");
    if (!isOpen("cfStage")) { want3d = false; return; }
    if (cfView("3d")) { want3d = false; return; }
    let n = 0;
    const t = setInterval(() => {
      if (!want3d || ++n > 80) { clearInterval(t); return; }
      const fr = byId("cfFrame");
      let ready = false;
      try { ready = fr.contentDocument && fr.contentDocument.readyState === "complete"; } catch (e) { /* cross-origin never */ }
      if (ready && cfView("3d")) { want3d = false; clearInterval(t); }
    }, 250);
  }
  window.addEventListener("message", (e) => {
    if (e.origin !== location.origin || !e.data || e.data.source !== "cutfill") return;
    if (e.data.type === "ready") { injectEmbedSkin(); if (want3d) setTimeout(() => { if (cfView("3d")) want3d = false; }, 300); }
  });
  function roomsCount() {
    const s = S(), fl = s.fl;
    // state.fl belongs to the result it was built for (flSync); a stale one
    // from another sheet has nothing to stand up
    return fl && fl.result === s.result && Array.isArray(fl.rooms) ? fl.rooms.length : 0;
  }
  function render3d() {
    view3d.innerHTML = "";
    const s = S();
    const rooms = roomsCount();
    view3d.append(
      h("div", { class: "vw-head" },
        h("div", { class: "vw-title" }, h("h1", null, "3D"),
          h("div", { class: "vw-meta" }, "Two ways to stand this sheet up. Both read what the 2D tools already hold; nothing here is estimated."))),
      h("div", { class: "v3-cards" },
        h("article", { class: "vw-card v3-card" },
          icon(ICON.cube, "v3-ico"),
          h("h2", null, "Cut / fill surfaces"),
          h("p", null, "Existing and proposed ground from the cut / fill engine, cut red and fill blue, with the volumes as solids and a vertical exaggeration slider. Hover a spot for its heights."),
          h("p", { class: "v3-need" }, "Needs a computed cut / fill (Earthwork)."),
          h("button", { type: "button", class: "btn primary", disabled: !s.job, onclick: openCf3d },
            "Open cut / fill in 3D")),
        h("article", { class: "vw-card v3-card" },
          icon(ICON.rooms, "v3-ico"),
          h("h2", null, "Rooms"),
          h("p", null, "The flooring rooms stood up in their floor colours, walls to the ceiling height."),
          h("p", { class: "v3-need" }, rooms ? `${rooms} room${rooms === 1 ? "" : "s"} on this sheet.` : "Pick or draw a room first (Areas)."),
          h("button", { type: "button", class: "btn", disabled: !rooms,
            onclick: () => call("flOpen3d") }, "Open rooms in 3D"))));
  }
  // the rooms overlay (fl3d.js appends #fl3d to <body> once, then toggles
  // its `hidden`) takes the 3D tab while it is up, and gives it back after
  let before3d = null;
  function fl3dSync() {
    const o = byId("fl3d");
    const up = !!(o && o.isConnected && !o.hidden);
    if (up && cur !== "3d") { before3d = cur; setModule("3d", "follow"); }
    else if (!up && cur === "3d" && before3d) { const b = before3d; before3d = null; setModule(b, "follow"); }
  }
  new MutationObserver(() => {
    const o = byId("fl3d");
    if (o && !o.__shellWatched) {
      o.__shellWatched = true;
      new MutationObserver(fl3dSync).observe(o, { attributes: true, attributeFilter: ["hidden"] });
      fl3dSync();
    }
  }).observe(document.body, { childList: true });

  /* -------------------------------------------- THE EMBEDDED ENGINE SKIN
     webui/cutfill/* are the cut/fill session's files, copied in unchanged;
     the host restyles its same-origin iframe from outside instead. */
  function injectEmbedSkin() {
    const fr = byId("cfFrame");
    let d = null;
    try { d = fr && fr.contentDocument; } catch (e) { return; }
    if (!d || !d.head || !/\/cutfill\//.test(d.location.pathname)) return;
    d.documentElement.dataset.palette = root.dataset.palette || "steel";
    d.documentElement.dataset.accent = root.dataset.accent || "green";
    if (!d.getElementById("coreSkin")) {
      const l = d.createElement("link");
      l.id = "coreSkin"; l.rel = "stylesheet"; l.href = "/cutfill-skin.css?v=1";
      d.head.append(l);
    }
  }
  const cfFrame = byId("cfFrame");
  if (cfFrame) cfFrame.addEventListener("load", injectEmbedSkin);

  /* ---------------------------------------------------- APPEARANCE
     Two switches beside app.js's own theme (dark / paper, which app.js owns
     with the ◐ button): the ACCENT - green (default) or yellow - and the
     GROUND - steel blue-black (default) or graphite. This browser only. */
  const LOOK = {
    accent:  { attr: "accent",  key: "core-accent",  dflt: "green", box: "svAccentChoice",
               opts: { green: ["Green", "CORE's own green"], yellow: ["Yellow", "equipment yellow"] } },
    palette: { attr: "palette", key: "core-palette", dflt: "steel", box: "svPaletteChoice",
               opts: { steel: ["Steel", "blue-black"], nightcrew: ["Graphite", "graphite, uppercase titles"] } },
  };
  function lookGet(which) {
    const L = LOOK[which], v = root.dataset[L.attr];
    return L.opts[v] ? v : L.dflt;
  }
  function lookSet(which, v) {
    const L = LOOK[which];
    v = L.opts[v] ? v : L.dflt;
    root.dataset[L.attr] = v;
    LS.set(L.key, v);
    injectEmbedSkin();
    paintLook();
  }
  function paintLook() {
    for (const which of Object.keys(LOOK)) {
      const L = LOOK[which], box = byId(L.box);
      if (!box) continue;
      const cv = lookGet(which);
      box.innerHTML = "";
      for (const [v, [name, desc]] of Object.entries(L.opts)) {
        const rb = h("input", { type: "radio", name: "sv-" + which, value: v });
        rb.checked = cv === v;
        rb.onchange = () => { if (rb.checked) lookSet(which, v); };
        box.append(h("label", { class: "sv-ch" + (cv === v ? " on" : "") }, rb,
          // the swatch rides INSIDE the name: .sv-ch-t is a flex column
          // (style.css), so as its own child it took a line of its own
          h("span", { class: "sv-ch-t" },
            h("b", null, h("span", { class: "sv-swatch sw-" + which + "-" + v, "aria-hidden": "true" }), name),
            h("span", { class: "sv-ch-d" }, desc))));
      }
    }
  }
  const themeBox = byId("svThemeChoice");
  if (themeBox) {
    themeBox.after(
      h("div", { class: "sv-sublabel" }, "Accent"),
      h("div", { class: "sv-choice", id: "svAccentChoice", role: "radiogroup", "aria-label": "Accent colour" }),
      h("div", { class: "sv-sublabel" }, "Ground"),
      h("div", { class: "sv-choice", id: "svPaletteChoice", role: "radiogroup", "aria-label": "Ground colour" }));
    paintLook();
  }
  for (const which of Object.keys(LOOK)) {
    const L = LOOK[which];
    if (!L.opts[root.dataset[L.attr]]) root.dataset[L.attr] = L.opts[LS.get(L.key, L.dflt)] ? LS.get(L.key, L.dflt) : L.dflt;
  }
  // (Settings -> "This machine" is app.js's own svHashTarget now, c7281e0.)

  /* ------------------------------------------ SETTINGS: THE LONG NOTES
     Settings explains itself at length - every note is load-bearing (where
     a value is stored, what it never changes). Folded to two lines with a
     "More", never deleted (the house rule). Only the static explanations:
     a note with an id is a live status line app.js rewrites, and stays
     whole. */
  for (const n of document.querySelectorAll(".settings-view .sv-section .sv-note:not([id])")) {
    if (n.dataset.fold || (n.textContent || "").trim().length < 180) continue;
    n.dataset.fold = "1";
    n.classList.add("sv-fold");
    const more = h("button", { type: "button", class: "sv-more", "aria-expanded": "false",
      onclick: () => {
        const open = n.classList.toggle("open");
        more.setAttribute("aria-expanded", open ? "true" : "false");
        more.textContent = open ? "Less" : "More";
      } }, "More");
    n.after(more);
  }

  /* ------------------------------------------ POPOVERS ON A RIBBON
     The tool-demo popover and the multi-select menus were placed to the
     RIGHT of a vertical rail. On a horizontal ribbon they go BELOW. */
  const inRibbon = (r) => !!(r && ribbon.getBoundingClientRect().bottom >= r.bottom - 1 &&
                             ribbon.getBoundingClientRect().top <= r.top + 1);
  const tip0 = F("tipvidPos");
  if (tip0) window.tipvidPos = function (btn, pop, vw, vh, gap) {
    if (!inRibbon(btn)) return tip0(btn, pop, vw, vh, gap);
    let x = btn.left + btn.width / 2 - pop.w / 2;
    x = Math.max(4, Math.min(x, vw - pop.w - 4));
    let y = btn.bottom + gap;
    if (y + pop.h > vh - 4) y = Math.max(4, btn.top - gap - pop.h);
    return { x, y };
  };
  const msp0 = F("msPopPlace");
  if (msp0) window.msPopPlace = function (pop, anchor) {
    const r = anchor.getBoundingClientRect();
    if (!inRibbon(r)) return msp0(pop, anchor);
    const w = pop.offsetWidth, hh = pop.offsetHeight;
    const x = Math.max(8, Math.min(r.left, innerWidth - w - 8));
    const y = Math.max(8, Math.min(r.bottom + 6, innerHeight - hh - 8));
    pop.style.left = x + "px";
    pop.style.top = y + "px";
  };

  /* ------------------------------------------- FOLLOW THE PAGE
     selectPage / loadResult rebuild the sheet (and pageReset closes the
     trench, count, whole-job, across-sheets and callout panels; flSync /
     stSync close flooring and structure on a new result). The module's own
     panel comes back afterwards. */
  function wrapAfter(name, after) {
    const orig = F(name);
    if (!orig || orig.__shell) return;
    const w = function (...a) {
      const r = orig.apply(this, a);
      Promise.resolve(r).then(() => { try { after(...a); } catch (e) { console.error(e); } }, () => {});
      return r;
    };
    w.__shell = true;
    window[name] = w;
  }
  function wrapBefore(name, before) {
    const orig = F(name);
    if (!orig || orig.__shell) return;
    const w = function (...a) { try { before(...a); } catch (e) { console.error(e); } return orig.apply(this, a); };
    w.__shell = true;
    window[name] = w;
  }
  function docSync() {
    document.body.classList.toggle("no-doc", !S().job);
  }
  // Settings -> Workspace -> "Open projects on": a project that opens (at
  // load or from Projects) lands on that tab; "last" keeps the old habit.
  let lastJob = null;
  // A RELOAD COMES BACK TO THE SHEET (UI verify 2026-10-02, M4: it landed on Projects). While a project is on screen
  // the address says #job=<id> - the address app.js already opens at boot (its bootJob), the one Copy link hands out -
  // and this tab remembers the page (sessionStorage); the tab comes back through "Open projects on" (the last tab by
  // default). Projects / Settings / Team keep their own address; closing them puts #job back.
  const OPEN_KEY = "core-open-page";
  let restorePage = null;
  try {
    const bm = /^#job=([0-9a-f]{6,32})$/.exec(typeof BOOT_HASH === "string" ? BOOT_HASH : "");
    const saved = JSON.parse(sessionStorage.getItem(OPEN_KEY) || "null");
    if (bm && saved && saved.job === bm[1] && Number.isInteger(saved.page)) restorePage = saved;
  } catch (e) { /* no storage: page 1 */ }
  function addrSync() {
    const s = S();
    if (!s.job || document.body.classList.contains("view-open")) return;
    const want = "#job=" + s.job;
    try {
      if (location.hash !== want) history.replaceState(null, "", location.pathname + location.search + want);
      sessionStorage.setItem(OPEN_KEY, JSON.stringify({ job: s.job, page: s.page }));
    } catch (e) { /* the address is a convenience */ }
  }
  new MutationObserver(() => addrSync()).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  function onPage(kind) {
    linksSync();
    const job = S().job || null;
    if (restorePage && job === restorePage.job) {
      const want = restorePage.page;
      restorePage = null;
      if (want !== S().page && want >= 0 && want < ((S().kinds || []).length)) { goPage(want); return; }
    } else if (job && restorePage) restorePage = null;
    addrSync();
    if (job && job !== lastJob) {
      lastJob = job;
      const pref = LS.get("core-start-module", "last");
      if (MOD[pref] && pref !== cur) { setModule(pref, "open"); docSync(); renderSheets(); return; }
    }
    docSync();
    renderSheets();
    const m = MOD[cur];
    if (!m) return;
    if (m.kind === "canvas") openPrimary(m, kind);
    else if (cur === "summary") renderSummary();
    else if (cur === "plans") renderPlans();
    else if (cur === "3d") render3d();
  }
  // SB-FOLD-BEGIN (tests/test_redesign_frame.js evaluates this block on its own)
  // THE STATUS BAR'S WARNINGS, WHOLE (F5, live proof 2026-10-01). app.js's two lines in the bar - its lasting note
  // #sbCf (OUT OF DATE, NOT IN THE BID, the old calculator's removed earthwork) and the transient #status (a refused
  // approve names both scales) - ellipsize, with the full text only in a hover title: cut even at 1,600px and out of
  // reach on a phone. Fold, never delete: a line that is cut becomes a button (click, tap, Enter or Space) that
  // opens the bar so every line wraps whole; a refusal (class "err") that is cut opens it by itself. The nodes stay
  // app.js's own (moved, never cloned); this only adds attributes and the bar's "sb-open" class.
  function installStatusFold(bar, opts) {
    const o = opts || {};
    const lines = () => Array.from(bar.querySelectorAll(".sb-cf, .status"));
    const cut = (el) => el.scrollWidth > el.clientWidth + 1;
    let lastErr = "";
    function setA(el, k, v) {
      if (v == null) { if (el.getAttribute(k) != null) el.removeAttribute(k); }
      else if (el.getAttribute(k) !== v) el.setAttribute(k, v);
    }
    function setOpen(open) {
      if (bar.classList.contains("sb-open") === open) return;
      bar.classList.toggle("sb-open", open);
      sync();
      if (o.onResize) o.onResize();
    }
    function sync() {
      const open = bar.classList.contains("sb-open");
      let anyText = false;
      for (const el of lines()) {
        const text = String(el.textContent || "").trim();
        if (text) anyText = true;
        const fold = !!text && (open || cut(el));
        setA(el, "data-fold", fold ? "" : null);
        setA(el, "tabindex", fold ? "0" : null);
        setA(el, "role", fold ? "button" : null);
        setA(el, "aria-expanded", fold ? (open ? "true" : "false") : null);
        // a refusal that does not fit opens the bar by itself, once per message
        const err = el.classList.contains("err") && el.classList.contains("status");
        if (err && text && text !== lastErr && !open && cut(el)) { lastErr = text; setOpen(true); return; }
        if (!err && el.classList.contains("status")) lastErr = "";
      }
      if (open && !anyText) setOpen(false);
    }
    const hit = (e) => (e.target && e.target.closest ? e.target.closest("[data-fold]") : null);
    bar.addEventListener("click", (e) => { if (hit(e)) setOpen(!bar.classList.contains("sb-open")); });
    bar.addEventListener("keydown", (e) => {
      if (!hit(e) || (e.key !== "Enter" && e.key !== " ")) return;
      e.preventDefault();
      setOpen(!bar.classList.contains("sb-open"));
    });
    if (o.observe) o.observe(sync);
    sync();
    return { sync, setOpen };
  }
  // SB-FOLD-END
  {
    const sbar = byId("statusBar");
    if (sbar) {
      let sbT = 0;
      const soon = (fn) => () => { cancelAnimationFrame(sbT); sbT = requestAnimationFrame(fn); };
      installStatusFold(sbar, {
        onResize: () => requestAnimationFrame(syncCanvas),
        observe: (fn) => {
          new MutationObserver(soon(fn)).observe(sbar, { childList: true, characterData: true, subtree: true,
            attributes: true, attributeFilter: ["class"] });
          addEventListener("resize", soon(fn));
        },
      });
    }
  }

  // a document opening: the previous job's download links go the moment state.job changes (app.js's jobLinksReset,
  // and linksSync hides any link that names another job before it asks the server anything). app.js's applyOpened
  // asks jobLinksSync(job) right after its reset: that call is this file's linksSync, so ONE request and ONE rule
  // decide the links (elements.json only when the page on screen is approved). An app.js without it: follow the open.
  if (F("jobLinksSync")) window.jobLinksSync = function () { return linksSync(); };
  else wrapAfter("applyOpened", () => { linksSync(); });
  wrapAfter("selectPage", () => onPage("page"));
  wrapAfter("loadResult", () => onPage("result"));
  wrapAfter("renderItemized", () => { applyTradeFolds(); if (cur === "summary") summarySoon(); });
  let smT = 0;
  const summarySoon = () => { clearTimeout(smT); smT = setTimeout(() => { if (cur === "summary") renderSummary(); }, 250); };
  // something is about to scroll to a row on the sheet: bring a canvas
  // module forward first (a row in a display:none rail cannot be shown)
  wrapBefore("revealStyleRow", () => { if (!MOD[cur] || MOD[cur].kind !== "canvas") setModule("lines", "follow"); });
  wrapBefore("ckGo", (e) => {
    const t = e && e.act && e.act.t;
    const to = t === "tr" ? "trenching" : t === "sym" ? "counts" : t === "ew" ? null
      : (!MOD[cur] || MOD[cur].kind !== "canvas") ? "lines" : null;
    if (to && to !== cur) setModule(to, "follow");
  });

  /* --------------------------------------------------------------- BOOT */
  // The left rail's first-run width: app.js defaults to 400 because the
  // five-column money table lived there; it lives in the right rail now, so
  // a rail nobody has resized starts near app.js's own minimum (320) - 336,
  // the 16px being the room the dock's panel controls take (dock.js). A
  // width the operator dragged to is theirs and is left alone.
  try { if (!localStorage.getItem("core-sidebar-w")) call("sideSetWidth", 336, false); } catch (e) { /* no storage */ }
  window.coreShell = { setModule, get module() { return cur; }, renderSheets, renderSummary, renderPlans, syncCanvas };
  const startPref = LS.get("core-start-module", "last"), lastTab = LS.get("core-module", "lines");
  const start = MOD[startPref] ? startPref : MOD[lastTab] ? lastTab : "lines";
  setModule(start, "boot");
  docSync();
  renderSheets();
  root.classList.add("shell-on");
  root.classList.remove("shell-boot");
  requestAnimationFrame(syncCanvas);
})();
