"use strict";
/* ==========================================================================
   responsive.js - the frame from a phone to a 4K wall
   (REDESIGN 2026-09-26, docs/REDESIGN.md; layout in responsive.css)
   --------------------------------------------------------------------------
   Loaded after app.js, shell.js and team.js (dock.js loads after it and
   knows #rspTakeoffBtn). It adds only what CSS alone cannot:

     #rspSheetsBtn   ribbon, first  - opens the LEFT drawer  (#sidebar)
     #rspTakeoffBtn  ribbon, last   - opens the RIGHT drawer (#railR)
     #rspSideHead / #rspRailHead    - a title and a close button (#rspSideClose,
                                      #rspRailClose) atop each drawer
     #rspScrim                      - the scrim behind an open drawer
     #rspMenuBtn / #rspMenu         - the phone's menu (< 768px): PROXIES for
                                      what the top bar and the status bar fold
                                      away there (Projects, Tools, Team,
                                      Settings, Account, Feedback, Terms ...)
     touch on the sheet             - a finger's tap / drag / pinch reaches
                                      app.js as the mouse and wheel it answers
     the cut / fill frame           - a narrow-width layer for the embedded
                                      engine page (#cfFrame), like shell.js's skin

   The drawers are a tablet/phone (< 1024px) state kept in two classes on
   <html>: rsp-open-l / rsp-open-r. Nothing app.js or shell.js owns is
   cloned or rebuilt and no `hidden` attribute of theirs is written: the
   rails are the same nodes, restyled; the proxies click the real controls
   (shell.js's ribbon-proxy pattern), so every handler, title and hidden
   bookkeeping stays the only truth. window.coreResponsive = { open(side),
   close(), drawer, drawerMode } for anything that wants to drive them.
   ========================================================================== */
(() => {
  if (window.coreResponsive) return;
  const root = document.documentElement;
  const byId = (id) => document.getElementById(id);
  const F = (name) => (typeof window[name] === "function" ? window[name] : null);
  const header = document.querySelector("header");
  const main = document.querySelector("main");
  const sidebar = byId("sidebar");
  const ribbon = byId("ribbon");            // shell.js's
  const railR = byId("railR");              // shell.js's
  const statusBar = byId("statusBar");
  if (!header || !main || !sidebar || !ribbon) return;   // not the app page, or no frame

  // the same edges as responsive.css (the .98 keeps a zoomed 1023.5px
  // window from falling between two rules)
  const MQ_DRAWER = matchMedia("(max-width: 1023.98px)");
  const MQ_PHONE = matchMedia("(max-width: 767.98px)");
  const MQ_WIDE = matchMedia("(min-width: 2200px)");
  const onMq = (mq, fn) => { if (mq.addEventListener) mq.addEventListener("change", fn); else mq.addListener(fn); };

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
  function icon(inner) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24");
    s.setAttribute("aria-hidden", "true");
    s.innerHTML = inner;
    return s;
  }
  const ICON = {
    sheets: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M5.6 8.5h1.2M5.6 12h1.2M5.6 15.5h1.2"/>',
    takeoff: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M15 4v16M17.2 8.5h1.2M17.2 12h1.2M17.2 15.5h1.2"/>',
    menu: '<path d="M4 7h16M4 12h16M4 17h16"/>',
    close: '<path d="M6 6l12 12M18 6 6 18"/>',
    projects: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    tools: '<rect x="4" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="4" width="6.5" height="6.5" rx="1.2"/><rect x="4" y="13.5" width="6.5" height="6.5" rx="1.2"/><rect x="13.5" y="13.5" width="6.5" height="6.5" rx="1.2"/>',
    team: '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3.2 2.9-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2c2.4.1 4.3 1.7 4.9 4.8"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>',
    user: '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.6 7-5.6s6.2 2 7 5.6"/>',
    theme: '<circle cx="12" cy="12" r="8"/><path d="M12 4v16a8 8 0 0 0 0-16z" fill="currentColor" stroke="none"/>',
    feedback: '<path d="M4 5h16v11H9l-5 4z"/>',
    csv: '<path d="M6 3h8l4 4v14H6z"/><path d="M9 13h6M9 17h6M9 9h2"/>',
    saved: '<path d="M6 3h12v18l-6-4-6 4z"/>',
    remove: '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
  };

  /* ------------------------------------------------------------- STATE */
  const CANVAS = new Set(["lines", "counts", "areas", "earthwork", "trenching"]);
  const body = document.body;
  const moduleNow = () => body.dataset.module || "";
  const viewOpen = () => /(^|\s)(pv|sv|tv)-open(\s|$)/.test(main.className);
  const shown = (id) => { const n = byId(id); return !!(n && n.isConnected && !n.hidden); };
  const coverOpen = () => shown("cfStage") || shown("fl3d");     // full-frame overlays
  const drawerMode = () => MQ_DRAWER.matches && root.classList.contains("rsp-on");
  const drawerEl = (side) => (side === "l" ? sidebar : railR);
  // which drawer holds a node right now - dock.js lets the operator move
  // any panel (the tool dock, Line styles, the Sheet card ...) to either rail
  const sideOf = (n) => (!n ? null : sidebar.contains(n) ? "l" : railR && railR.contains(n) ? "r" : null);
  function visible(el) {
    if (!el || !el.isConnected || !el.getClientRects().length) return false;
    return getComputedStyle(el).visibility !== "hidden";
  }
  let openSide = null;          // "l" | "r" | null
  let opener = null;            // where focus goes back to

  /* ------------------------------------------------------- THE CONTROLS */
  const togL = h("button", { type: "button", id: "rspSheetsBtn", class: "rsp-tog", "aria-controls": "sidebar",
    "aria-expanded": "false", title: "Sheets, this module's tools and panels, line styles" },
    icon(ICON.sheets), h("span", { class: "rsp-tog-t" }, "Sheets", h("span", { class: "rsp-tog-x" }, " & tools")));
  const togR = railR ? h("button", { type: "button", id: "rspTakeoffBtn", class: "rsp-tog", "aria-controls": "railR",
    "aria-expanded": "false", title: "The sheet's scale, the takeoff by trade, questions, approve" },
    h("span", { class: "rsp-tog-t" }, "Takeoff"), icon(ICON.takeoff)) : null;
  ribbon.prepend(togL);
  if (togR) ribbon.append(togR);

  const closeL = h("button", { type: "button", class: "rsp-dclose", id: "rspSideClose",
    "aria-label": "Close sheets and tools", title: "Close (Esc)" }, icon(ICON.close));
  sidebar.prepend(h("div", { class: "rsp-dhead", id: "rspSideHead" }, h("h2", null, "Sheets & tools"), closeL));
  let closeR = null;
  if (railR) {
    closeR = h("button", { type: "button", class: "rsp-dclose", id: "rspRailClose",
      "aria-label": "Close the takeoff", title: "Close (Esc)" }, icon(ICON.close));
    railR.prepend(h("div", { class: "rsp-dhead", id: "rspRailHead" }, h("h2", null, "Takeoff"), closeR));
  }
  const scrim = h("div", { id: "rspScrim", "aria-hidden": "true" });
  body.append(scrim);

  const menuBtn = h("button", { type: "button", id: "rspMenuBtn", "aria-haspopup": "menu", "aria-expanded": "false",
    "aria-controls": "rspMenu", "aria-label": "Menu", title: "Menu" }, icon(ICON.menu));
  header.append(menuBtn);
  const menu = h("div", { id: "rspMenu", role: "menu", "aria-label": "Menu", tabindex: "-1", hidden: true });
  body.append(menu);

  /* ------------------------------------------------------------ DRAWERS */
  function canOpen(side) {
    if (!drawerMode() || viewOpen() || coverOpen() || body.classList.contains("no-doc")) return false;
    if (!CANVAS.has(moduleNow())) return false;
    // dock.js hides a left rail whose every panel was closed
    if (side === "l") return !main.classList.contains("dk-left-empty");
    return side === "r" && !!railR;
  }
  function focusInto(side) {
    const b = side === "l" ? closeL : closeR;
    if (b) b.focus({ preventScroll: true });
  }
  function setOpen(side, opts = {}) {
    if (side && !canOpen(side)) side = null;
    const prev = openSide;
    if (side === prev) { if (side && opts.focus !== false) focusInto(side); return; }
    openSide = side;
    root.classList.toggle("rsp-open-l", side === "l");
    root.classList.toggle("rsp-open-r", side === "r");
    togL.setAttribute("aria-expanded", side === "l" ? "true" : "false");
    if (togR) togR.setAttribute("aria-expanded", side === "r" ? "true" : "false");
    if (side) {
      if (!prev) {
        const a = document.activeElement;
        opener = opts.opener || (a && a !== body ? a : null);
      }
      if (opts.focus !== false) focusInto(side);
    } else if (prev) {
      const a = document.activeElement;
      const back = opener && visible(opener) ? opener : (prev === "l" ? togL : togR);
      opener = null;
      // give focus back only if it was in the drawer that is closing - never
      // take it from somewhere the operator has since moved it
      if (opts.restore !== false && back && visible(back) &&
          (!a || a === body || drawerEl(prev).contains(a))) back.focus({ preventScroll: true });
    }
  }
  togL.addEventListener("click", () => setOpen(openSide === "l" ? null : "l", { opener: togL }));
  if (togR) togR.addEventListener("click", () => setOpen(openSide === "r" ? null : "r", { opener: togR }));
  closeL.addEventListener("click", () => setOpen(null));
  if (closeR) closeR.addEventListener("click", () => setOpen(null));
  scrim.addEventListener("click", () => setOpen(null));

  // a view, the cut / fill page or the rooms-3D overlay takes the frame: an
  // open drawer steps down (its rail is display:none there anyway)
  function sync() {
    if (openSide && !canOpen(openSide)) setOpen(null, { restore: false });
    paintDot();
    tabsSoon();
  }
  new MutationObserver(sync).observe(body, { attributes: true, attributeFilter: ["data-module", "class"] });
  new MutationObserver(sync).observe(main, { attributes: true, attributeFilter: ["class"] });
  const cfStage = byId("cfStage");
  if (cfStage) new MutationObserver(sync).observe(cfStage, { attributes: true, attributeFilter: ["hidden"] });
  const watched = new WeakSet();
  new MutationObserver(() => {                 // fl3d.js appends #fl3d to <body> on first use
    const o = byId("fl3d");
    if (o && !watched.has(o)) {
      watched.add(o);
      new MutationObserver(sync).observe(o, { attributes: true, attributeFilter: ["hidden"] });
      sync();
    }
  }).observe(body, { childList: true });

  /* ---- a tool panel opened from the Tools menu lands in a drawer (the
     left one, unless the operator docked the module card on the right):
     open that drawer, so the thing just asked for is on screen. A panel a
     module tab opens by itself does not pull a drawer over the sheet; that
     drawer's toggle wears a dot instead. */
  const dock = byId("toolDock");
  let toolPickAt = 0;
  // a PERSON's pick only: shell.js opens a module's own panel by calling
  // .click() on the same Tools-menu buttons (untrusted), and a tab switch
  // must not pull the drawer over the sheet
  document.addEventListener("click", (e) => {
    const t = e.target;
    if (e.isTrusted && t && t.closest && t.closest("#toolsMenu")) toolPickAt = Date.now();
  }, true);
  function paintDot() {
    const on = !!dock && [...dock.children].some((p) => p.classList.contains("ew-panel") && !p.hidden);
    const side = on ? sideOf(dock) : null;
    togL.classList.toggle("rsp-has-panel", side === "l");
    if (togR) togR.classList.toggle("rsp-has-panel", side === "r");
  }
  if (dock) {
    const panelObs = new MutationObserver((recs) => {
      paintDot();
      if (!drawerMode() || Date.now() - toolPickAt > 2500) return;
      const side = sideOf(dock);
      if (side && openSide !== side && recs.some((r) => !r.target.hidden)) { toolPickAt = 0; setOpen(side); }
    });
    for (const p of dock.children) panelObs.observe(p, { attributes: true, attributeFilter: ["hidden"] });
  }

  /* ---- "show me that row": the sheet's line -> its row in the list, and a
     Questions row's Go. The rows live in a drawer here; wrapped the way
     shell.js wraps them (after the original, whose `this` and return pass
     through untouched). */
  function wrapAfter(name, after) {
    const orig = F(name);
    if (!orig || orig.__rsp) return;
    const w = function (...a) {
      const r = orig.apply(this, a);
      Promise.resolve(r).then((v) => { try { after(v, ...a); } catch (err) { console.error(err); } }, () => {});
      return r;
    };
    w.__rsp = true;
    window[name] = w;
  }
  wrapAfter("revealStyleRow", (ok) => {
    const side = sideOf(byId("styleList"));
    if (ok && drawerMode() && side) setOpen(side);
  });
  wrapAfter("ckGo", (_, e) => {
    if (!drawerMode()) return;
    const t = e && e.act && e.act.t;
    const at = { tr: "trPanel", sym: "symPanel", sheethead: "sheetHead" }[t];
    if (t === "style") setOpen(null, { restore: false });        // the isolated family is on the sheet
    else if (at && sideOf(byId(at))) setOpen(sideOf(byId(at)));  // its panel / the Sheet card, wherever docked
  });

  /* ---- keyboard: Esc closes (after any popup above the drawer has had
     it, and after a field in the drawer has used it); Tab stays inside an
     open drawer; focus never lands in the covered part of the frame */
  const isField = (t) => !!t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable);
  const ABOVE = ".fp-modal, .fb-back, .tm-modal, .ms-pop, .color-pop, .mem-menu, #drawFamModal, " +
    ".lg-modal:not([hidden]), .gz-modal:not([hidden]), #userMenu:not([hidden]), #toolsMenu:not([hidden]), " +
    ".dk-menu:not([hidden])";                 // dock.js's panel menus close first too
  // capture phase, registered after app.js's own capture handlers, which
  // therefore still consume Esc first for their menus and modals
  addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (!menu.hidden) { e.preventDefault(); e.stopImmediatePropagation(); closeMenu(true); return; }
    if (!openSide || document.querySelector(ABOVE)) return;
    if (isField(e.target) && drawerEl(openSide).contains(e.target)) return;   // the field's own Esc first
    e.preventDefault(); e.stopImmediatePropagation();
    setOpen(null);
  }, true);
  // bubble phase: a field in the drawer that did not use its Esc hands
  // focus to the drawer, so the next Esc closes it
  addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !openSide || e.defaultPrevented) return;
    if (isField(e.target) && drawerEl(openSide).contains(e.target)) { e.target.blur(); focusInto(openSide); }
  });
  const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Tab" || !openSide || e.altKey || e.ctrlKey || e.metaKey) return;
    const box = drawerEl(openSide), a = document.activeElement;
    if (!box.contains(a)) return;              // the top bar stays reachable
    const f = [...box.querySelectorAll(FOCUSABLE)].filter(visible);
    if (!f.length) return;
    if (e.shiftKey && a === f[0]) { e.preventDefault(); f[f.length - 1].focus(); }
    else if (!e.shiftKey && a === f[f.length - 1]) { e.preventDefault(); f[0].focus(); }
  });
  document.addEventListener("focusin", (e) => {
    if (!openSide) return;
    const t = e.target, box = drawerEl(openSide);
    if (box.contains(t)) return;
    const covered = ribbon.contains(t) || (statusBar && statusBar.contains(t)) || main.contains(t);
    if (covered) focusInto(openSide);
  });
  // the document itself never scrolls (body is 100vh, overflow hidden); a
  // scrollIntoView aimed at a closed drawer must not be able to shift it
  addEventListener("scroll", () => {
    if (MQ_DRAWER.matches && (scrollX || scrollY)) scrollTo(0, 0);
  }, { passive: true });

  /* -------------------------------------------------------- MODULE TABS
     Where the tabs do not fit (< 1440 when the bar is full, the tablet and
     phone strip) they scroll: the selected one is kept in view, the cut
     edge fades, and a mouse wheel scrolls the strip sideways. */
  const tabs = byId("modTabs");
  function tabsFade() {
    if (!tabs) return;
    const max = tabs.scrollWidth - tabs.clientWidth;
    tabs.classList.toggle("rsp-fade-l", max > 1 && tabs.scrollLeft > 2);
    tabs.classList.toggle("rsp-fade-r", max > 1 && tabs.scrollLeft < max - 2);
  }
  function tabIntoView() {
    if (!tabs || tabs.scrollWidth <= tabs.clientWidth + 1) return;
    const b = tabs.querySelector('.mod-tab[aria-selected="true"]');
    if (!b) return;
    const tr = tabs.getBoundingClientRect(), br = b.getBoundingClientRect();
    if (br.left < tr.left + 24) tabs.scrollLeft += br.left - tr.left - 24;
    else if (br.right > tr.right - 24) tabs.scrollLeft += br.right - tr.right + 24;
  }
  let tabsRaf = 0;
  function tabsSoon() {
    if (tabsRaf) return;
    tabsRaf = requestAnimationFrame(() => { tabsRaf = 0; tabIntoView(); tabsFade(); });
  }
  if (tabs) {
    tabs.addEventListener("scroll", tabsFade, { passive: true });
    tabs.addEventListener("wheel", (e) => {
      if (tabs.scrollWidth <= tabs.clientWidth + 1 || Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
      tabs.scrollLeft += e.deltaY;
      e.preventDefault();
    }, { passive: false });
    if ("ResizeObserver" in window) new ResizeObserver(tabsSoon).observe(tabs);
  }

  /* ---------------------------------------------------------- PHONE MENU
     < 768px the top bar keeps the glyph, the document, the accuracy flag, Detect and
     this button; the status bar keeps the sheet facts. Everything they
     fold away is here, as a proxy that clicks the real control - built at
     each open from what exists and is not hidden at that moment. */
  const bodyHas = (c) => body.classList.contains(c);
  const MENU = [
    { id: "projectsLink", label: "Projects", icon: ICON.projects },
    { id: "toolsBtn", label: "Tools", icon: ICON.tools, sub: "calculators, readers",
      show: () => !bodyHas("view-open") && !bodyHas("no-doc"), into: "toolsMenu" },
    { id: "teamLink", label: "Team", icon: ICON.team },
    { id: "settingsLink", label: "Settings", icon: ICON.gear },
    { id: "userBtn", label: "Account", icon: ICON.user, sub: "theme, shortcuts", into: "userMenu" },
    { id: "themeBtn", label: "Dark / paper theme", icon: ICON.theme, show: () => !byId("userBtn") },
    "sep",
    { id: "fbBtn", label: "Feedback", icon: ICON.feedback, own: true },
    { id: "fbExportBtn", label: "Feedback CSV", icon: ICON.csv, own: true },
    { id: "memBtn", label: "Saved work", icon: ICON.saved, own: true },
    { id: "docRemoveBtn", label: "Remove from server", icon: ICON.remove, danger: true, own: true,
      show: () => !bodyHas("view-open") },
  ];
  // `own`: the real button's words are live (the saved-work count, ...) and
  // are shown as they are; the others are icons or carry a chevron
  function labelOf(node, it) {
    const t = node.textContent.trim();
    return it.own && t ? t : it.label;
  }
  function activate(e, node, into) {
    e.stopPropagation();          // the real menus close on an outside click - this one included
    closeMenu(false);
    node.click();
    if (into) {                   // a menu the real button opened: put the keyboard in it
      const m = byId(into);
      const f = m && !m.hidden && m.querySelector("button:not([hidden]):not([disabled]), [role=menuitem]");
      if (f) f.focus({ preventScroll: true });
    }
  }
  function buildMenu() {
    menu.innerHTML = "";
    const doc = byId("docName");
    if (doc && doc.textContent.trim() && !bodyHas("view-open") && !bodyHas("no-doc")) {
      const ps = byId("pageSel");
      const pg = ps && !ps.hidden && ps.selectedIndex >= 0 ? ps.options[ps.selectedIndex].textContent : "";
      menu.append(h("div", { class: "rsp-mdoc" }, doc.textContent.trim(), pg ? h("span", null, " · " + pg) : null));
    }
    let lastSep = true;
    for (const it of MENU) {
      if (it === "sep") { if (!lastSep) { menu.append(h("div", { class: "rsp-msep", role: "separator" })); lastSep = true; } continue; }
      const node = byId(it.id);
      if (!node || node.hidden || node.disabled || (it.show && !it.show())) continue;
      const cur = node.getAttribute("aria-current");
      menu.append(h("button", { type: "button", role: "menuitem", class: "rsp-mi" + (it.danger ? " danger" : ""),
        "aria-current": cur || null, "data-for": it.id, title: node.getAttribute("title") || null,
        onclick: (e) => activate(e, node, it.into) },
        icon(it.icon), h("span", null, labelOf(node, it)), it.sub ? h("span", { class: "rsp-mi-sub" }, it.sub) : null));
      lastSep = false;
    }
    const legal = [...document.querySelectorAll(".status-bar a.legal-link")].filter((a) => !a.hidden);
    if (legal.length) {
      if (!lastSep) menu.append(h("div", { class: "rsp-msep", role: "separator" }));
      menu.append(h("div", { class: "rsp-mlegal" }, legal.map((a) => h("button", { type: "button", role: "menuitem",
        class: "rsp-mi", onclick: (e) => activate(e, a) }, a.textContent.trim()))));
    }
    // the accuracy flag rides every surface; its FULL line is here too: the
    // chip's face (the first sentence) + its title (the second), both set by
    // app.js from /api/beta-notice - "Automated detection can be wrong.
    // Double-check every quantity against the drawings."
    const beta = byId("betaBanner");
    const face = beta ? beta.textContent.trim().replace(/[.\s]+$/, "") : "";
    if (face) {
      const tip = (beta.getAttribute("title") || "").trim();
      menu.append(h("div", { class: "rsp-mnote", role: "note" }, tip ? face + ". " + tip : face + "."));
    }
  }
  // opened from the keyboard, the first item takes focus; from a tap, the
  // menu itself does (the arrow keys still work, and no item looks picked)
  function openMenu(byKeyboard) {
    if (!MQ_PHONE.matches) return;
    buildMenu();
    menu.hidden = false;
    root.classList.add("rsp-menu-open");
    menuBtn.setAttribute("aria-expanded", "true");
    const f = byKeyboard ? menu.querySelector(".rsp-mi") : menu;
    if (f) f.focus({ preventScroll: true });
  }
  function closeMenu(focusBack) {
    if (menu.hidden) return;
    menu.hidden = true;
    root.classList.remove("rsp-menu-open");
    menuBtn.setAttribute("aria-expanded", "false");
    if (focusBack) menuBtn.focus({ preventScroll: true });
  }
  menuBtn.addEventListener("click", (e) => { if (menu.hidden) openMenu(e.detail === 0); else closeMenu(false); });
  document.addEventListener("click", (e) => {
    if (menu.hidden || menu.contains(e.target) || menuBtn.contains(e.target)) return;
    closeMenu(false);
  });
  menu.addEventListener("keydown", (e) => {
    const items = [...menu.querySelectorAll(".rsp-mi")];
    const i = items.indexOf(document.activeElement);
    let to = null;
    if (e.key === "ArrowDown") to = items[i + 1] || items[0];
    else if (e.key === "ArrowUp") to = items[i - 1] || items[items.length - 1];
    else if (e.key === "Home") to = items[0];
    else if (e.key === "End") to = items[items.length - 1];
    else if (e.key === "Tab") { closeMenu(false); return; }
    else return;
    e.preventDefault(); e.stopPropagation();
    if (to) to.focus();
  });

  /* ------------------------------------------------ TOUCH ON THE SHEET
     app.js drives the canvas with a mouse: mousedown on it, mousemove and
     mouseup on the window, the wheel to zoom. A finger's drag never became
     a mouse drag, and a pinch zoomed the whole PAGE - measured on a phone
     emulation: one-finger drag, state.view unchanged; pinch, the sheet's
     view unchanged and visualViewport.scale 1 -> 5. So a phone or tablet
     could not pan or zoom the plan. This translates TOUCH only (a mouse and
     a pen keep their own events) into the events app.js already answers:
       tap              mousedown + mouseup where it landed = a click
                        (selects a line, or places the armed tool's point)
       double tap       dblclick (finishes a drawn line or outline)
       one-finger drag  mousedown at the start, mousemove, mouseup = the
                        mouse's drag (pans; drags a vertex or a work-area
                        box exactly as the mouse would)
       two-finger pinch wheel at the midpoint = zoom about it
     Nothing in app.js changes and nothing of its state is written. */
  const cv = byId("view");
  if (cv && window.PointerEvent) {
    const pts = new Map();              // pointerId -> {x, y}
    let g = null;                       // the gesture in progress
    let lastTap = null, lastDblAt = 0;
    const TAP = 8;                      // px a finger may wander and still tap
    const fire = (type, x, y, extra) => cv.dispatchEvent(new MouseEvent(type, Object.assign({
      bubbles: true, cancelable: true, view: window, clientX: x, clientY: y, button: 0,
      buttons: type === "mousedown" || type === "mousemove" ? 1 : 0 }, extra)));
    cv.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "touch") return;
      e.preventDefault();               // no compatibility mouse events: this sends its own
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      try { cv.setPointerCapture(e.pointerId); } catch (err) { /* capture is a nicety */ }
      if (pts.size === 1) g = { kind: "one", id: e.pointerId, sx: e.clientX, sy: e.clientY, down: false };
      else if (pts.size === 2) {
        // a second finger: a drag already under way ends here (it moved
        // more than a tap, so app.js does not read it as a click)
        if (g && g.kind === "one" && g.down) { const p = pts.get(g.id); fire("mouseup", p.x, p.y); }
        const [a, b] = [...pts.values()];
        g = { kind: "pinch", d: Math.hypot(a.x - b.x, a.y - b.y) || 1 };
      } else g = { kind: "none" };
    });
    cv.addEventListener("pointermove", (e) => {
      if (e.pointerType !== "touch" || !pts.has(e.pointerId)) return;
      pts.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (!g) return;
      if (g.kind === "one" && e.pointerId === g.id) {
        if (!g.down) {
          if (Math.hypot(e.clientX - g.sx, e.clientY - g.sy) < TAP) return;
          fire("mousedown", g.sx, g.sy);
          g.down = true;
        }
        fire("mousemove", e.clientX, e.clientY);
      } else if (g.kind === "pinch" && pts.size === 2) {
        const [a, b] = [...pts.values()];
        const d = Math.hypot(a.x - b.x, a.y - b.y) || 1;
        const z = d / g.d;
        if (Math.abs(z - 1) < 0.004) return;
        g.d = d;
        // app.js zooms by exp(-deltaY * 0.0015) about the event's point
        cv.dispatchEvent(new WheelEvent("wheel", { bubbles: true, cancelable: true, view: window,
          clientX: (a.x + b.x) / 2, clientY: (a.y + b.y) / 2, deltaY: -Math.log(z) / 0.0015, deltaMode: 0 }));
      }
    });
    const lift = (e) => {
      if (e.pointerType !== "touch" || !pts.has(e.pointerId)) return;
      const p = pts.get(e.pointerId);
      pts.delete(e.pointerId);
      if (g && g.kind === "one" && e.pointerId === g.id) {
        if (g.down) fire("mouseup", p.x, p.y);
        else if (e.type === "pointerup") {          // a tap
          fire("mousedown", g.sx, g.sy);
          fire("mouseup", g.sx, g.sy);
          const now = Date.now();
          if (lastTap && now - lastTap.t < 350 && Math.hypot(g.sx - lastTap.x, g.sy - lastTap.y) < 24) {
            lastDblAt = now;
            fire("dblclick", g.sx, g.sy, { detail: 2 });
            lastTap = null;
          } else lastTap = { t: now, x: g.sx, y: g.sy };
        }
        g = null;
      }
      // after a pinch the fingers leave one by one: the last one up ends
      // it, and the one left behind never turns into a jumping pan
      if (!pts.size) g = null;
    };
    cv.addEventListener("pointerup", lift);
    cv.addEventListener("pointercancel", lift);
    // some browsers add their own dblclick after a double tap (Chromium
    // does, measured: two per double tap); app.js must see one, so the
    // browser's is dropped when the bridge has just sent it
    addEventListener("dblclick", (e) => {
      if (e.isTrusted && e.target === cv && lastDblAt && Date.now() - lastDblAt < 700) {
        e.stopImmediatePropagation(); e.preventDefault();
      }
    }, true);
  }

  /* ------------------------------------- THE EMBEDDED CUT / FILL PAGE
     Earthwork and 3D show the cut / fill engine in #cfFrame. Its page is
     three fixed columns (330 | map | 320): a tablet's map was 170px wide
     and a phone's was off the screen. Like shell.js's skin, a layer is
     added to the same-origin frame from outside - webui/cutfill/* stays
     untouched - and it only acts below 1024px OF THE FRAME: the map first,
     then the result, then the inputs, one column, the page scrolling. The
     engine refits its canvas by its own ResizeObserver on #stage. */
  // "html body[.embed]" outranks both the engine's own rules and the
  // host skin's body.embed ones (cutfill-skin.css fixes #top and main's heights)
  const EMBED_CSS = `@media (max-width: 1023.98px) {
  html, html body { height: auto; min-height: 100%; }
  html body #top, html body.embed #top { height: auto; min-height: 48px; flex-wrap: wrap; row-gap: 6px;
    column-gap: 10px; padding: 6px 10px; }
  html body #top .grp.right { margin-left: 0; flex-wrap: wrap; row-gap: 6px; }
  html body #top .tabs { flex: 1 1 100%; }
  html body main, html body.embed main { display: flex; flex-direction: column; height: auto; }
  html body #stage, html body.embed #stage { order: 0; flex: none; height: 62vh; min-height: 300px; }
  html body #right, html body.embed #right { order: 1; overflow: visible; }
  html body #left, html body.embed #left { order: 2; overflow: visible; }
  html body #legend { max-width: calc(100% - 20px); flex-wrap: wrap; }
}`;
  function injectEmbedLayer() {
    const fr = byId("cfFrame");
    let d = null;
    try { d = fr && fr.contentDocument; } catch (e) { return; }
    if (!d || !d.head || !/\/cutfill\//.test(d.location.pathname) || d.getElementById("rspEmbed")) return;
    const st = d.createElement("style");
    st.id = "rspEmbed";
    st.textContent = EMBED_CSS;
    d.head.append(st);
  }
  const cfFrame = byId("cfFrame");
  if (cfFrame) cfFrame.addEventListener("load", injectEmbedLayer);
  addEventListener("message", (e) => {
    if (e.origin === location.origin && e.data && e.data.source === "cutfill" && e.data.type === "ready") injectEmbedLayer();
  });
  if (cfStage) new MutationObserver(() => { if (!cfStage.hidden) injectEmbedLayer(); })
    .observe(cfStage, { attributes: true, attributeFilter: ["hidden"] });

  /* ---------------------------------------------------- WIDE (>= 2200)
     The left rail's first-run width grows a little on a wall-sized
     window - through app.js's own setter, never saved, and only while the
     operator has not dragged it (a dragged width is theirs). */
  function wideRail() {
    const set = F("sideSetWidth");
    let stored = null;
    try { stored = localStorage.getItem("core-sidebar-w"); } catch (e) { /* no storage */ }
    if (!set || stored) return;
    const want = MQ_WIDE.matches ? 360 : 320;
    if (parseFloat(sidebar.style.getPropertyValue("--side-w")) !== want) set(want, false);
  }

  /* ------------------------------------------------------------ LAYOUT */
  function layout() {
    if (!MQ_DRAWER.matches) setOpen(null, { restore: false });
    if (!MQ_PHONE.matches) closeMenu(false);
    wideRail();
    tabsSoon();
  }
  onMq(MQ_DRAWER, layout);
  onMq(MQ_PHONE, layout);
  onMq(MQ_WIDE, layout);

  window.coreResponsive = {
    open(side) { setOpen(side === "left" || side === "l" ? "l" : side === "right" || side === "r" ? "r" : null); },
    close() { setOpen(null); },
    get drawer() { return openSide === "l" ? "left" : openSide === "r" ? "right" : null; },
    get drawerMode() { return drawerMode(); },
  };
  root.classList.add("rsp-on");
  layout();
  paintDot();
})();
