"use strict";
/* ==========================================================================
   dock.js - the two rails as a DOCK (REDESIGN 2026-09-26, docs/REDESIGN.md)
   --------------------------------------------------------------------------
   Koby, 2026-09-26: "sheets should be closable, this lines tab should be
   closable, line styles I guess will need to take up most of the screen ...
   make all of this editable, so you can drag and drop [panels] to be on top
   of another or on the right or left side or customize your default loading
   screen".

   Every card in the two rails is a PANEL. You can:
     - close it with x. The Panels menu (the ribbon's far end) brings it back.
     - drag it by its head to either rail, above or below any other panel.
       The ... menu does the same from the keyboard.
     - let it fill: Line styles (left) and Takeoff (right) take whatever
       their rail has left. The others take their own height, up to a share
       of the rail, and scroll inside. Drag a panel's bottom edge to fix its
       height; double-click the edge to go back to its own height.
     - resize the right rail by its inner edge (the left one already has
       app.js's grip).
   The arrangement is saved in this browser (core-dock-v1), and every load
   starts from it. Settings -> Appearance -> Workspace resets it and picks
   the tab a project opens on.

   Loaded after shell.js. It MOVES the nodes shell.js placed: app.js's own
   cards, plus shell's Sheets card and module card. It never clones them,
   rebuilds them or writes `hidden`. A panel's `hidden` stays app.js's, and
   closing one here only adds a class (.dk-closed), so app.js's show/hide
   and this layout never fight. The module card carries #toolDock: the tool
   panels open beside the module's own tools, wherever that card lives.
   ========================================================================== */
(() => {
  if (window.coreDock) return;
  const shell = window.coreShell;
  const byId = (id) => document.getElementById(id);
  const root = document.documentElement;
  const body = document.body;
  const main = document.querySelector("main");
  const sidebar = byId("sidebar");
  const leftDock = byId("sideScroll");
  const rightDock = byId("railScroll");
  const railR = byId("railR");
  const ribbon = byId("ribbon");
  if (!shell || !main || !sidebar || !leftDock || !rightDock || !railR) return;

  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* private mode: this session only */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* nothing stored */ } },
  };
  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
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
    more: '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    x: '<path d="M6 6l12 12M18 6 6 18"/>',
    panels: '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M9 4v16M15 4v16"/>',
  };

  /* ------------------------------------------------------- THE PANELS
     side: where it starts. body: the part that scrolls when the panel is
     shorter than its content (none = the whole panel scrolls). fill: takes
     what its rail has left. carries: a node that travels right after it. */
  // fold: when a rail cannot hold every panel AND the filling panel's rows (rows: [list, row, how many]), the panels
  // fold to their head, lowest number first (UI verify 2026-10-02, defect 6: after Detect at 1440 x 900 the style list
  // was 51px - no row - and the Bid view 19px). compact: the Line styles panel's filters fold behind one button last.
  const PANELS = [
    { id: "sheetsCard",  name: "Sheets",              side: "L", body: "#sheetsBody", fold: 3, minBody: 74 },
    { id: "modCard",     name: "Module tools",        side: "L", carries: "toolDock", fold: 5 },
    { id: "cardLegend",  name: "Sheet legend",        side: "L", body: "#legendBox", fold: 1 },
    { id: "cardAreas",   name: "Areas (square feet)", side: "L", body: "#arList", where: "Areas tab", fold: 2 },
    { id: "cardStyles",  name: "Line styles",         side: "L", body: "#styleList", fill: true,
      rows: ["#styleList", ".style-row", 6], compact: 4 },
    { id: "cardSheet",   name: "Sheet",               side: "R", fold: 3 },
    { id: "cardTakeoff", name: "Takeoff",             side: "R", body: "#itemized", fill: true,
      rows: ["#itemized", ".item-row:not(.head):not(.total)", 5] },
    { id: "cardBid",     name: "Bid view",            side: "R", body: "#bidBox", fold: 1 },
    { id: "cardCheck",   name: "Questions",           side: "R", fold: 2 },
  ];
  const REG = Object.fromEntries(PANELS.map((p) => [p.id, p]));
  const KEY = "core-dock-v1";
  const RAIL_KEY = "core-rail-r";
  const DEFAULT = {
    v: 1,
    L: PANELS.filter((p) => p.side === "L").map((p) => p.id),
    R: PANELS.filter((p) => p.side === "R").map((p) => p.id),
    closed: [], size: {}, fill: PANELS.filter((p) => p.fill).map((p) => p.id),
  };
  let fillSet = new Set(DEFAULT.fill);
  const pEl = (id) => byId(id);
  // what to call a panel on screen: the module card is named by its tab
  const nameOf = (id) => {
    if (id !== "modCard") return REG[id].name;
    const t = (byId("modCardTitle") || {}).textContent;
    return t ? t + " tools" : "Module tools";
  };
  const docks = [leftDock, rightDock];
  const dockOf = (n) => (n && n.parentElement === rightDock ? rightDock : n && n.parentElement === leftDock ? leftDock : null);
  const sideName = (d) => (d === rightDock ? "right" : "left");
  const narrow = () => matchMedia("(hover: none), (max-width: 1023.98px)").matches;

  /* ------------------------------------------------------ FURNITURE
     Each panel gets: the class that sizes it, its scrolling body marked,
     a head that is the drag handle (the Sheet card had only a ::before,
     so it gets a real one), the options and close buttons in that head,
     and a bottom edge that sets its height. */
  function headOf(n) {
    return n.querySelector(":scope > .rc-head, :scope > .side-head, :scope > .takeoff-head, :scope > .dk-head");
  }
  function furnish(p) {
    const n = pEl(p.id);
    if (!n || n.dataset.dk) return;
    n.dataset.dk = "1";
    n.classList.add("dk-panel");
    const b = p.body ? n.querySelector(":scope > " + p.body) : null;
    if (b) b.classList.add("dk-body"); else n.classList.add("dk-nobody");
    let head = headOf(n);
    if (!head) {
      head = h("div", { class: "dk-head dk-aux" }, h("h2", null, p.name));
      n.prepend(head);
    }
    head.classList.add("dk-handle");
    head.title = head.title || "Drag to move this panel";
    head.prepend(h("span", { class: "dk-grip", "aria-hidden": "true" }));
    const more = h("button", { type: "button", class: "dk-b dk-more", title: "Panel options",
      "aria-label": p.name + " options", "aria-haspopup": "menu", "aria-expanded": "false" }, icon(ICON.more));
    const x = h("button", { type: "button", class: "dk-b dk-x", title: "Close panel (the Panels menu brings it back)",
      "aria-label": "Close " + p.name }, icon(ICON.x));
    more.addEventListener("click", (e) => { e.stopPropagation(); panelMenu(p.id, more); });
    x.addEventListener("click", (e) => { e.stopPropagation(); closePanel(p.id, true); });
    head.append(h("span", { class: "dk-ctl" }, more, x));
    const rs = h("div", { class: "dk-rs dk-aux", title: "Drag to set this panel's height - double-click for its own height",
      role: "separator", "aria-orientation": "horizontal" });
    rs.addEventListener("pointerdown", (e) => startResize(e, p.id, rs));
    rs.addEventListener("dblclick", () => { setSize(p.id, null); save(); refresh(); });
    n.append(rs);
    head.addEventListener("pointerdown", (e) => armDrag(e, p.id, head));
    // folded to fit: one click (or Enter / Space) on the head opens it. Captured before app.js's own fold on the
    // title, so the click that opens a panel the dock folded never also folds app.js's card.
    const openIt = (e) => {
      if (!n.classList.contains("dk-auto") || (e.target.closest && e.target.closest(".dk-ctl"))) return false;
      e.preventDefault(); e.stopImmediatePropagation();
      unfoldAuto(p.id);
      return true;
    };
    head.addEventListener("click", openIt, true);
    head.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") openIt(e); }, true);
  }

  /* --------------------------------------------------- LAYOUT: APPLY */
  function readLayout() {
    try {
      const v = JSON.parse(LS.get(KEY, "null"));
      return v && v.v === 1 && Array.isArray(v.L) && Array.isArray(v.R) ? v : null;
    } catch (e) { return null; }
  }
  function place(dock, id, before) {
    const n = pEl(id);
    if (!n) return;
    const p = REG[id];
    const carried = p && p.carries ? byId(p.carries) : null;
    dock.insertBefore(n, before || null);
    if (carried) n.after(carried);
  }
  function setSize(id, px) {
    const n = pEl(id);
    if (!n) return;
    if (px) { n.dataset.dkSize = String(Math.round(px)); n.style.setProperty("--dk-size", Math.round(px) + "px"); }
    else { delete n.dataset.dkSize; n.style.removeProperty("--dk-size"); }
  }
  function apply(lay) {
    lay = lay || DEFAULT;
    const seen = new Set();
    const put = (dock, list) => {
      for (const id of list) {
        if (!REG[id] || seen.has(id) || !pEl(id)) continue;
        seen.add(id);
        place(dock, id, null);
      }
    };
    put(leftDock, lay.L);
    put(rightDock, lay.R);
    // a panel the saved layout does not know (added since): its own side
    for (const p of PANELS) if (!seen.has(p.id)) put(p.side === "L" ? leftDock : rightDock, [p.id]);
    const closed = new Set(lay.closed || []);
    for (const p of PANELS) {
      const n = pEl(p.id);
      if (!n) continue;
      n.classList.toggle("dk-closed", closed.has(p.id));
      // a height saved before 2026-10-02 that leaves the panel a sliver (its head and a line) is not a layout
      // anyone chose to keep: it goes back to its own height
      const sz = lay.size && lay.size[p.id];
      setSize(p.id, sz && sz >= SLIVER ? sz : null);
    }
    fillSet = new Set(Array.isArray(lay.fill) ? lay.fill : DEFAULT.fill);
    refresh();
  }
  function current() {
    const ids = (d) => [...d.children].map((c) => c.id).filter((id) => REG[id]);
    const size = {};
    for (const p of PANELS) { const n = pEl(p.id); if (n && n.dataset.dkSize) size[p.id] = +n.dataset.dkSize; }
    return { v: 1, L: ids(leftDock), R: ids(rightDock),
             closed: PANELS.filter((p) => pEl(p.id) && pEl(p.id).classList.contains("dk-closed")).map((p) => p.id),
             size, fill: [...fillSet] };
  }
  function save() { LS.set(KEY, JSON.stringify(current())); }

  /* ------------------------------------------------ LAYOUT: REFRESH
     What is showing, which panel fills, whether the left rail is empty.
     Only writes classes when they change (so the observer below does not
     wake itself). */
  const shows = (n) => !!n && !n.classList.contains("dk-closed") && getComputedStyle(n).display !== "none";
  const folded = (n) => !!n.querySelector(":scope > .closed");
  const tog = (n, c, on) => { if (n.classList.contains(c) !== !!on) n.classList.toggle(c, !!on); };
  // The height a panel would take with nothing squeezing it: the sum of its
  // children, the scrolling body at its full content height. Never the
  // panel's own scrollHeight - that reads back the floor set below, and a
  // floor once raised (a module card with two rows of buttons) would stick.
  function naturalH(n) {
    const cs = getComputedStyle(n);
    let hgt = parseFloat(cs.paddingTop) + parseFloat(cs.paddingBottom)
      + parseFloat(cs.borderTopWidth) + parseFloat(cs.borderBottomWidth);
    for (const c of n.children) {
      if (c.classList.contains("dk-rs")) continue;
      const s = getComputedStyle(c);
      if (s.display === "none" || s.position === "absolute" || s.position === "fixed") continue;
      hgt += (c.classList.contains("dk-body") ? c.scrollHeight : c.offsetHeight)
        + parseFloat(s.marginTop) + parseFloat(s.marginBottom);
    }
    return hgt;
  }
  // Squeezed, a panel keeps all of a short content (one sheet, three
  // buttons) and at least its head plus a few rows of a long one (the Sheets
  // list two). An open tool panel keeps more; the Sheet card (the scale) and
  // the module's tools are never squeezed.
  const CAP = 104, TOOL_CAP = 240, MIN_BODY = 88, SLIVER = 90;
  // FIT (UI verify 2026-10-02, defect 6). A floor of 104px was the head plus nothing when the head itself is 100px
  // (Bid view: 19px of rows), and the filling panel's 150px floor was smaller than its own controls (Line styles:
  // 440px of buttons, scope chips and search over a 51px list). Now each rail is fitted from scratch on every
  // refresh: the filling panel needs its controls plus its first rows (Line styles 6, Takeoff 5), every other panel
  // its head plus a few rows; while that does not fit, panels fold to their head in a fixed order (fold:), then the
  // Line styles filters fold behind one button (compact:). One click on a folded head opens it, and that panel is
  // never folded again this session. Nothing is saved: a taller window gives every panel back by itself.
  const pinned = new Set();
  // a panel holding one of these is never folded to fit: it is saying something it must not lose (fork 152148c:
  // Areas' "Areas were measured again. ... N earlier ticks were not kept." stays until a tick or a dismiss)
  const KEEP_OPEN = ".ar-note";
  let filtersOpen = false;
  function bodyOf(c) { const p = REG[c.id]; return p && p.body ? c.querySelector(":scope > " + p.body) : null; }
  // the filling panel: its controls (everything but the list) plus the list down to its n-th showing row
  function rowsNeed(c, p, n) {
    n = n || p.rows[2];
    const nat = naturalH(c);
    const list = c.querySelector(p.rows[0]);
    if (!list) return nat;
    const rows = [...list.querySelectorAll(":scope > " + p.rows[1])].filter((r) => r.offsetHeight > 0);
    if (rows.length < n) return nat;
    const lr = list.getBoundingClientRect();
    const upto = rows[n - 1].getBoundingClientRect().bottom - lr.top + list.scrollTop + 2;
    return Math.min(nat, nat - list.scrollHeight + Math.ceil(upto));
  }
  function floorOf(c) {
    const p = REG[c.id];
    const nat = naturalH(c);
    if (!p) return Math.min(nat, TOOL_CAP);                                   // the tool dock
    if (c.classList.contains("dk-auto")) return nat;
    if (c.dataset.dkSize) return +c.dataset.dkSize;
    if (c.classList.contains("dk-fill")) return p.rows ? rowsNeed(c, p) : Math.min(nat, 150);
    if (c.id === "cardSheet" || c.id === "modCard") return nat;               // never squeezed
    const b = bodyOf(c), hd = headOf(c);
    const chrome = b ? nat - b.scrollHeight : (hd ? hd.offsetHeight : 40);
    return Math.min(nat, chrome + (p.minBody || MIN_BODY));
  }
  function setMin(c, px) {
    const want = px == null ? "" : Math.round(px) + "px";
    if (c.style.minHeight !== want) c.style.minHeight = want;
  }
  function fit(d) {
    const kids = [...d.children].filter((c) => REG[c.id] || c.id === "toolDock");
    // from scratch: nothing folded to fit, the filters showing
    for (const c of kids) if (REG[c.id]) { c.classList.remove("dk-auto"); c.classList.remove("dk-compact"); }
    const H = d.clientHeight;
    const shown = kids.filter(shows);
    const tool = d.classList.contains("dk-tool-open");
    // a tool panel open beside the fill: the tool is the work at hand, the fill keeps its controls and a few rows -
    // and folds to its head, last of all, rather than show a sliver of them
    const needOf = (c) => {
      if (!(tool && c.classList.contains("dk-fill")) || c.classList.contains("dk-auto")) return floorOf(c);
      const b = bodyOf(c), nat = naturalH(c);
      const p = REG[c.id];
      return p.rows ? rowsNeed(c, p, 2) : b ? Math.min(nat, nat - b.scrollHeight + MIN_BODY) : Math.min(nat, 150);
    };
    const need = new Map(shown.map((c) => [c, needOf(c)]));
    const gap = parseFloat(getComputedStyle(d).rowGap) || 0;
    const total = () => [...need.values()].reduce((a, b) => a + b, 0) + gap * Math.max(0, need.size - 1);
    let compactWanted = false;
    if (H && !d.classList.contains("dk-scroll")) {
      const steps = [];
      for (const c of shown) {
        const p = REG[c.id];
        if (!p || pinned.has(c.id) || c.querySelector(KEEP_OPEN)) continue;
        if (p.fold && !c.classList.contains("dk-fill")) steps.push({ c, rank: p.fold, kind: "fold" });
        if (p.compact && c.classList.contains("dk-fill")) steps.push({ c, rank: p.compact, kind: "compact" });
        if (tool && c.classList.contains("dk-fill")) steps.push({ c, rank: 9, kind: "fold" });
      }
      steps.sort((a, b) => a.rank - b.rank);
      for (const s of steps) {
        if (total() <= H + 0.5) break;
        if (s.kind === "compact") {
          compactWanted = true;
          if (filtersOpen) continue;
          s.c.classList.add("dk-compact");
        } else s.c.classList.add("dk-auto");
        need.set(s.c, needOf(s.c));
      }
    }
    for (const c of kids) {
      if (!shows(c) || c.classList.contains("dk-auto") || (c.dataset.dkSize && REG[c.id])) { setMin(c, null); continue; }
      setMin(c, need.has(c) ? need.get(c) : null);
    }
    const sc = d.querySelector(":scope > #cardStyles");
    if (sc) { tog(sc, "dk-filt-avail", compactWanted); tog(sc, "dk-filters-open", compactWanted && filtersOpen); }
    // a folded head says what it holds and opens with a click, Enter or Space
    for (const c of kids) {
      if (!REG[c.id]) continue;
      const hd = headOf(c), h2 = hd && hd.querySelector("h2");
      if (!h2) continue;
      const auto = c.classList.contains("dk-auto");
      if (auto && !h2.dataset.dkKb) {
        h2.dataset.dkKb = h2.hasAttribute("tabindex") ? "keep" : "1";
        if (h2.dataset.dkKb === "1") h2.tabIndex = 0;
        h2.setAttribute("role", "button");
        h2.setAttribute("aria-expanded", "false");
        h2.dataset.dkTitle = h2.getAttribute("title") || "";
        h2.title = "Folded to make room - click to open";
      } else if (!auto && h2.dataset.dkKb) {
        if (h2.dataset.dkKb === "1") h2.removeAttribute("tabindex");
        h2.removeAttribute("role"); h2.removeAttribute("aria-expanded");
        if (h2.dataset.dkTitle) h2.title = h2.dataset.dkTitle; else h2.removeAttribute("title");
        delete h2.dataset.dkKb; delete h2.dataset.dkTitle;
      }
      // the Sheet card folded still says the scale it holds
      if (c.id === "cardSheet") {
        const chip = auto ? [...c.querySelectorAll("#sheetHead .sh-chip")].find((x) => /=/.test(x.textContent)) : null;
        const sum = chip ? chip.textContent.trim() : "";
        if ((h2.dataset.dkSum || "") !== sum) { if (sum) h2.dataset.dkSum = sum; else delete h2.dataset.dkSum; }
      }
    }
  }
  function unfoldAuto(id) {
    const n = pEl(id);
    if (!n) return;
    pinned.add(id);
    n.classList.remove("dk-auto");
    refresh();
  }
  function refresh() {
    for (const d of docks) {
      let anyFill = false, anyShown = false, tool = false;
      for (const c of d.children) {
        if (!REG[c.id]) { if (c.id === "toolDock" && shows(c)) { anyShown = true; tool = true; } continue; }
        const on = shows(c);
        anyShown = anyShown || on;
        const fill = on && fillSet.has(c.id) && !folded(c) && !c.dataset.dkSize;
        tog(c, "dk-fill", fill);
        anyFill = anyFill || fill;
      }
      tog(d, "dk-scroll", !anyFill);
      tog(d, "dk-empty", !anyShown);
      // an open tool panel is the work at hand: the fill gives up its floor
      tog(d, "dk-tool-open", tool);
      fit(d);
    }
    tog(main, "dk-left-empty", leftDock.classList.contains("dk-empty") && !body.classList.contains("dk-dragging"));
    paintPanelsMenu();
    paintFilters();
    // the fit's own class writes are not news to the observer below (it would only measure again)
    obs.takeRecords();
  }
  let rq = 0;
  const refreshSoon = () => { if (!rq) rq = requestAnimationFrame(() => { rq = 0; refresh(); }); };
  // What can change the layout: content added or removed, anything shown or
  // hidden, and a class on a panel or its head (a fold). A class change deep
  // in a list (a row's hover or selection) cannot, and is not a reason to
  // measure the rails again.
  const layoutish = (t) => !!t && (docks.includes(t) || docks.includes(t.parentElement)
    || (t.parentElement && docks.includes(t.parentElement.parentElement)));
  const obs = new MutationObserver((recs) => {
    for (const r of recs) {
      if (r.type === "childList" || r.attributeName === "hidden" || layoutish(r.target)) { refreshSoon(); return; }
    }
  });
  for (const d of docks) obs.observe(d, { subtree: true, childList: true, attributes: true, attributeFilter: ["hidden", "class"] });
  obs.observe(body, { attributes: true, attributeFilter: ["data-module"] });
  addEventListener("resize", refreshSoon);

  /* ------------------------------------------------------ OPEN / CLOSE */
  let toastT = 0;
  const toast = h("div", { class: "dk-toast", role: "status", "aria-live": "polite", hidden: true });
  body.append(toast);
  function say(text, undo) {
    clearTimeout(toastT);
    toast.innerHTML = "";
    toast.append(h("span", null, text));
    if (undo) toast.append(h("button", { type: "button", onclick: () => { toast.hidden = true; undo(); } }, "Undo"));
    toast.hidden = false;
    toastT = setTimeout(() => { toast.hidden = true; }, 5000);
  }
  function closePanel(id, tell) {
    const n = pEl(id);
    if (!n) return;
    const had = n.contains(document.activeElement);
    n.classList.add("dk-closed");
    save(); refresh();
    if (had) { const b = byId("dkPanelsBtn"); if (b) b.focus(); }
    if (tell) say(`${nameOf(id)} closed. The Panels menu brings it back.`, () => openPanel(id));
  }
  function openPanel(id) {
    const n = pEl(id);
    if (!n) return;
    n.classList.remove("dk-closed");
    save(); refresh();
  }
  function reset() {
    LS.del(KEY);
    LS.del(RAIL_KEY);
    LS.del("core-minilegend-pos");      // the legend over the sheet goes home too
    root.style.removeProperty("--t-rail-r");
    apply(DEFAULT);
    if (typeof mlPlace === "function") mlPlace();
    say("Panels are back where they started.");
  }

  /* --------------------------------------------------------- MOVING */
  // the panels of a dock, in order, that are showing (the carried tool
  // dock is not a place of its own)
  const shownPanels = (d, except) => [...d.children].filter((c) => REG[c.id] && c !== except && shows(c));
  function moveTo(id, dock, before) {
    if (before && before.id === id) return;
    place(dock, id, before);
    save(); refresh();
    requestAnimationFrame(() => shell.syncCanvas && shell.syncCanvas());
  }
  function moveBy(id, step) {
    const n = pEl(id), d = dockOf(n);
    if (!d) return;
    const list = shownPanels(d);
    const i = list.indexOf(n);
    if (i < 0) return;
    const j = i + step;
    if (j < 0 || j >= list.length) return;
    // before the one above; or after the one below (= before the one after it)
    moveTo(id, d, step < 0 ? list[j] : (list[j + 1] || null));
  }

  /* ------------------------------------------------------- DRAGGING
     Press on a panel's head (not on one of its controls) and move 6px:
     the panel lifts, a line shows where it will land (either rail, above
     or below any panel), and letting go puts it there. Esc cancels. The
     click that ends a drag is swallowed, so a drag that started on a
     foldable title does not also fold it. */
  const line = h("div", { class: "dk-line", hidden: true });
  body.append(line);
  let drag = null;
  const INTERACTIVE = "button, a, input, select, textarea, label, summary, [contenteditable], .dk-ctl";
  function armDrag(e, id, head) {
    if (e.button !== 0 || narrow() || body.classList.contains("dk-locked")) return;
    if (e.target.closest(INTERACTIVE) && !e.target.closest("h2")) return;
    const sx = e.clientX, sy = e.clientY, pid = e.pointerId;
    const onMove = (ev) => {
      if (ev.pointerId !== pid) return;
      if (!drag) {
        if (Math.hypot(ev.clientX - sx, ev.clientY - sy) < 6) return;
        startDrag(id, head, pid);
      }
      dragMove(ev);
    };
    const end = (ev) => {
      if (ev.pointerId !== pid) return;
      removeEventListener("pointermove", onMove, true);
      removeEventListener("pointerup", end, true);
      removeEventListener("pointercancel", end, true);
      if (drag) finishDrag(ev.type === "pointerup");
    };
    addEventListener("pointermove", onMove, true);
    addEventListener("pointerup", end, true);
    addEventListener("pointercancel", end, true);
  }
  function startDrag(id, head, pid) {
    const n = pEl(id);
    drag = { id, n, ghost: h("div", { class: "dk-ghost" }, nameOf(id)), target: null, before: null };
    body.append(drag.ghost);
    body.classList.add("dk-dragging");
    tog(main, "dk-left-empty", false);
    n.classList.add("dk-lifted");
    try { head.setPointerCapture(pid); } catch (e) { /* capture is a nicety */ }
    addEventListener("keydown", dragKey, true);
  }
  function dragKey(e) {
    if (e.key === "Escape" && drag) { e.preventDefault(); e.stopImmediatePropagation(); finishDrag(false); }
  }
  function dockAt(x, y) {
    for (const d of docks) {
      const r = (d === leftDock ? sidebar : railR).getBoundingClientRect();
      if (r.width && x >= r.left - 12 && x <= r.right + 12 && y >= r.top && y <= r.bottom) return d;
    }
    return null;
  }
  function dragMove(ev) {
    if (!drag) return;
    drag.ghost.style.left = ev.clientX + "px";
    drag.ghost.style.top = ev.clientY + "px";
    const d = dockAt(ev.clientX, ev.clientY);
    for (const k of docks) tog(k, "dk-target", k === d);
    drag.target = d;
    if (!d) { line.hidden = true; return; }
    const list = shownPanels(d, drag.n);
    let before = null;
    for (const c of list) {
      const r = c.getBoundingClientRect();
      if (ev.clientY < r.top + r.height / 2) { before = c; break; }
    }
    drag.before = before;
    const dr = d.getBoundingClientRect();
    let y;
    if (before) y = before.getBoundingClientRect().top - 5;
    else if (list.length) y = list[list.length - 1].getBoundingClientRect().bottom + 3;
    else y = dr.top + 10;
    line.style.left = dr.left + "px";
    line.style.width = Math.max(40, dr.width - 2) + "px";
    line.style.top = y + "px";
    line.hidden = false;
  }
  function finishDrag(drop) {
    const dd = drag;
    drag = null;
    removeEventListener("keydown", dragKey, true);
    line.hidden = true;
    dd.ghost.remove();
    dd.n.classList.remove("dk-lifted");
    body.classList.remove("dk-dragging");
    for (const k of docks) tog(k, "dk-target", false);
    // the click that ends the gesture is not a fold
    const swallow = (e) => { e.stopPropagation(); e.preventDefault(); };
    addEventListener("click", swallow, { capture: true, once: true });
    setTimeout(() => removeEventListener("click", swallow, { capture: true }), 0);
    if (drop && dd.target) {
      const from = dockOf(dd.n);
      moveTo(dd.id, dd.target, dd.before);
      if (from !== dd.target) say(`${nameOf(dd.id)} moved to the ${sideName(dd.target)} rail.`);
    } else refresh();
  }

  /* ------------------------------------------------ PANEL HEIGHTS */
  function startResize(e, id, rs) {
    if (e.button !== 0) return;
    e.preventDefault(); e.stopPropagation();
    const n = pEl(id);
    const d = dockOf(n);
    if (!n || !d) return;
    const top = n.getBoundingClientRect().top;
    const maxH = d.getBoundingClientRect().height - 60;
    body.classList.add("dk-resizing");
    rs.classList.add("on");
    try { rs.setPointerCapture(e.pointerId); } catch (err) { /* nicety */ }
    const mv = (ev) => { setSize(id, Math.max(SLIVER, Math.min(maxH, ev.clientY - top))); refresh(); };
    const up = () => {
      rs.removeEventListener("pointermove", mv);
      rs.removeEventListener("pointerup", up);
      rs.removeEventListener("pointercancel", up);
      body.classList.remove("dk-resizing");
      rs.classList.remove("on");
      save(); refresh();
    };
    rs.addEventListener("pointermove", mv);
    rs.addEventListener("pointerup", up);
    rs.addEventListener("pointercancel", up);
  }

  /* ------------------------------------------ THE RIGHT RAIL'S WIDTH */
  const RMIN = 260, RMAX = 620;
  function railSet(px, keep) {
    const w = Math.max(RMIN, Math.min(RMAX, Math.round(px)));
    root.style.setProperty("--t-rail-r", w + "px");
    if (keep) LS.set(RAIL_KEY, String(w));
    shell.syncCanvas && shell.syncCanvas();
  }
  const rgrip = h("div", { class: "dk-rgrip", role: "separator", "aria-orientation": "vertical", tabindex: "0",
    "aria-label": "Right rail width", title: "Drag to widen the right rail - double-click to reset" });
  railR.append(rgrip);
  {
    const saved = parseFloat(LS.get(RAIL_KEY, ""));
    if (isFinite(saved)) railSet(saved, false);
  }
  rgrip.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    e.preventDefault();
    const right = railR.getBoundingClientRect().right;
    rgrip.classList.add("on");
    body.classList.add("dk-resizing");
    try { rgrip.setPointerCapture(e.pointerId); } catch (err) { /* nicety */ }
    const mv = (ev) => railSet(right - ev.clientX, false);
    const up = () => {
      rgrip.removeEventListener("pointermove", mv);
      rgrip.removeEventListener("pointerup", up);
      rgrip.removeEventListener("pointercancel", up);
      rgrip.classList.remove("on");
      body.classList.remove("dk-resizing");
      railSet(railR.getBoundingClientRect().width, true);
    };
    rgrip.addEventListener("pointermove", mv);
    rgrip.addEventListener("pointerup", up);
    rgrip.addEventListener("pointercancel", up);
  });
  rgrip.addEventListener("dblclick", () => { LS.del(RAIL_KEY); root.style.removeProperty("--t-rail-r"); shell.syncCanvas && shell.syncCanvas(); });
  rgrip.addEventListener("keydown", (e) => {
    const w = railR.getBoundingClientRect().width;
    if (e.key === "ArrowLeft") { e.preventDefault(); railSet(w + 16, true); }
    else if (e.key === "ArrowRight") { e.preventDefault(); railSet(w - 16, true); }
    else if (e.key === "Home") { e.preventDefault(); LS.del(RAIL_KEY); root.style.removeProperty("--t-rail-r"); }
  });

  /* ----------------------------------------------------------- MENUS */
  const menu = h("div", { class: "dk-menu", role: "menu", hidden: true });
  body.append(menu);
  let menuFor = null;          // the button that opened it
  function menuItems() { return [...menu.querySelectorAll(".dk-mi:not(:disabled)")]; }
  function openMenu(anchor, build) {
    if (menuFor === anchor && !menu.hidden) { closeMenu(); return; }
    closeMenu();
    menu.innerHTML = "";
    build(menu);
    menu.hidden = false;
    menuFor = anchor;
    anchor.setAttribute("aria-expanded", "true");
    const r = anchor.getBoundingClientRect();
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    let left = Math.min(r.right - mw, innerWidth - mw - 8);
    left = Math.max(8, left);
    let top = r.bottom + 6;
    if (top + mh > innerHeight - 8) top = Math.max(8, r.top - mh - 6);
    menu.style.left = left + "px";
    menu.style.top = top + "px";
    const first = menuItems()[0];
    if (first) first.focus();
  }
  function closeMenu(focusBack) {
    if (menu.hidden) return;
    menu.hidden = true;
    if (menuFor) {
      menuFor.setAttribute("aria-expanded", "false");
      if (focusBack) menuFor.focus();
    }
    menuFor = null;
  }
  menu.addEventListener("keydown", (e) => {
    const items = menuItems();
    const i = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); (items[(i + 1) % items.length] || items[0]).focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); (items[(i - 1 + items.length) % items.length] || items[0]).focus(); }
    else if (e.key === "Home") { e.preventDefault(); items[0] && items[0].focus(); }
    else if (e.key === "End") { e.preventDefault(); items[items.length - 1] && items[items.length - 1].focus(); }
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(true); }
    else if (e.key === "Tab") closeMenu(false);
  });
  addEventListener("pointerdown", (e) => {
    if (!menu.hidden && !menu.contains(e.target) && e.target !== menuFor && !(menuFor && menuFor.contains(e.target))) closeMenu(false);
  }, true);
  addEventListener("resize", () => closeMenu(false));
  const mi = (label, onPick, opts = {}) => h("button", {
    type: "button", class: "dk-mi", role: opts.check != null ? "menuitemcheckbox" : "menuitem",
    "aria-checked": opts.check != null ? String(!!opts.check) : null, disabled: opts.disabled || null,
    onclick: () => { closeMenu(opts.keepFocus !== false); onPick(); },
  }, opts.check != null ? h("span", { class: "dk-ck", "aria-hidden": "true" }) : null, h("span", null, label),
    opts.aside ? h("span", { class: opts.asideCls || "dk-side" }, opts.aside) : null);

  // one panel's own menu (its ... button)
  function panelMenu(id, anchor) {
    openMenu(anchor, (m) => {
      const n = pEl(id), d = dockOf(n);
      const list = d ? shownPanels(d) : [];
      const i = list.indexOf(n);
      const other = d === rightDock ? leftDock : rightDock;
      m.append(h("div", { class: "dk-mt" }, nameOf(id)));
      m.append(mi(`Move to the ${sideName(other)} rail`, () => moveTo(id, other, null)));
      m.append(mi("Move up", () => moveBy(id, -1), { disabled: i <= 0 }));
      m.append(mi("Move down", () => moveBy(id, +1), { disabled: i < 0 || i >= list.length - 1 }));
      m.append(h("div", { class: "dk-msep", role: "separator" }));
      m.append(mi("Fill the rail", () => { if (fillSet.has(id)) fillSet.delete(id); else { fillSet.add(id); setSize(id, null); } save(); refresh(); },
        { check: fillSet.has(id) }));
      if (n && n.dataset.dkSize) m.append(mi("Its own height again", () => { setSize(id, null); save(); refresh(); }));
      m.append(h("div", { class: "dk-msep", role: "separator" }));
      m.append(mi("Close panel", () => closePanel(id, true), { keepFocus: false }));
    });
  }

  // the Panels menu (the ribbon's far end): every panel, shown or not
  const panelsBtn = h("button", { type: "button", id: "dkPanelsBtn", class: "rb-btn", title: "Show, hide and rearrange the panels",
    "aria-haspopup": "menu", "aria-expanded": "false" }, icon(ICON.panels), h("span", null, "Panels"));
  if (ribbon) {
    const togR = byId("rspTakeoffBtn");
    if (togR && togR.parentElement === ribbon) ribbon.insertBefore(panelsBtn, togR); else ribbon.append(panelsBtn);
  }
  panelsBtn.addEventListener("click", () => openMenu(panelsBtn, buildPanelsMenu));
  function buildPanelsMenu(m) {
    m.append(h("div", { class: "dk-mt" }, "Panels"));
    for (const p of PANELS) {
      const n = pEl(p.id);
      if (!n) continue;
      const open = !n.classList.contains("dk-closed");
      const d = dockOf(n);
      const auto = open && shows(n) && n.classList.contains("dk-auto");
      const why = open && !shows(n) ? (p.where || "nothing to show here yet")
        : auto ? "folded to fit" : (d === rightDock ? "right" : "left");
      m.append(mi(nameOf(p.id),
        () => (open ? closePanel(p.id, false) : openPanel(p.id)),
        { check: open, aside: why, asideCls: (open && !shows(n)) || auto ? "dk-where" : "dk-side" }));
    }
    m.append(h("div", { class: "dk-msep", role: "separator" }));
    m.append(mi("Reset panels", reset));
    m.append(h("div", { class: "dk-mnote" }, "Drag a panel by its title to move it, to either side. Drag its bottom edge to size it. This browser remembers the layout."));
  }
  function paintPanelsMenu() {
    if (!menu.hidden && menuFor === panelsBtn) {
      const f = document.activeElement && menu.contains(document.activeElement) ? menuItems().indexOf(document.activeElement) : -1;
      menu.innerHTML = "";
      buildPanelsMenu(menu);
      if (f >= 0) { const it = menuItems()[f]; if (it) it.focus(); }
    }
  }

  /* ------------------------------------ THE LEGEND OVER THE SHEET
     The "On sheet" mini legend (app.js's #miniLegend) floats on the canvas,
     pinned bottom-left, over whatever part of the drawing is there. Drag it
     by its head to anywhere on the sheet; the spot is kept (as a share of
     the canvas, so it survives a resize) and a double-click on the head
     puts it back. app.js keeps its `hidden` and its collapse. */
  const ml = byId("miniLegend"), mlHead = ml && ml.querySelector(".ml-head"), stage = byId("stage");
  const ML_KEY = "core-minilegend-pos";
  function mlPlace() {
    if (!ml || !stage) return;
    let pos = null;
    try { pos = JSON.parse(LS.get(ML_KEY, "null")); } catch (e) { pos = null; }
    if (!pos || !isFinite(pos.x) || !isFinite(pos.y)) {
      ml.style.left = ""; ml.style.top = ""; ml.style.bottom = ""; ml.style.right = "";
      return;
    }
    const sw = stage.clientWidth, sh = stage.clientHeight;
    const w = ml.offsetWidth, hgt = ml.offsetHeight;
    ml.style.left = Math.max(0, Math.min(sw - w, pos.x * sw)) + "px";
    ml.style.top = Math.max(0, Math.min(sh - hgt, pos.y * sh)) + "px";
    ml.style.bottom = "auto"; ml.style.right = "auto";
  }
  if (ml && mlHead && stage) {
    mlHead.title = "Drag to move the legend over the sheet - double-click to put it back";
    mlHead.classList.add("dk-float-handle");
    mlHead.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || e.target.closest("button") || narrow()) return;
      e.preventDefault();
      const sr = stage.getBoundingClientRect(), mr = ml.getBoundingClientRect();
      const dx = e.clientX - mr.left, dy = e.clientY - mr.top;
      try { mlHead.setPointerCapture(e.pointerId); } catch (err) { /* nicety */ }
      body.classList.add("dk-resizing");
      const mv = (ev) => {
        const x = Math.max(0, Math.min(sr.width - mr.width, ev.clientX - sr.left - dx));
        const y = Math.max(0, Math.min(sr.height - mr.height, ev.clientY - sr.top - dy));
        ml.style.left = x + "px"; ml.style.top = y + "px"; ml.style.bottom = "auto"; ml.style.right = "auto";
      };
      const up = () => {
        mlHead.removeEventListener("pointermove", mv);
        mlHead.removeEventListener("pointerup", up);
        mlHead.removeEventListener("pointercancel", up);
        body.classList.remove("dk-resizing");
        const cur = ml.getBoundingClientRect(), s2 = stage.getBoundingClientRect();
        LS.set(ML_KEY, JSON.stringify({ x: (cur.left - s2.left) / (s2.width || 1), y: (cur.top - s2.top) / (s2.height || 1) }));
      };
      mlHead.addEventListener("pointermove", mv);
      mlHead.addEventListener("pointerup", up);
      mlHead.addEventListener("pointercancel", up);
    });
    mlHead.addEventListener("dblclick", (e) => { if (e.target.closest("button")) return; LS.del(ML_KEY); mlPlace(); });
    if ("ResizeObserver" in window) new ResizeObserver(() => mlPlace()).observe(stage);
    new MutationObserver(() => mlPlace()).observe(ml, { attributes: true, attributeFilter: ["hidden", "class"] });
    mlPlace();
  }

  /* ------------------------------------------ SETTINGS: WORKSPACE
     The tab a project opens on (shell.js reads core-start-module), and the
     panel layout's reset. In Appearance, after its own switches. */
  function settingsSection() {
    const grp = byId("svGroupAppearance");
    if (!grp || byId("svWorkspace")) return;
    let sec = grp.nextElementSibling;
    while (sec && !sec.classList.contains("sv-section")) sec = sec.nextElementSibling;
    if (!sec) return;
    const MODS = [["last", "The tab I used last"], ["plans", "Plans"], ["lines", "Lines"], ["counts", "Counts"],
                  ["areas", "Areas"], ["earthwork", "Earthwork"], ["trenching", "Trenching"], ["summary", "Summary"]];
    const cur = LS.get("core-start-module", "last");
    const sel = h("select", { id: "svStartModule" }, MODS.map(([v, t]) => {
      const o = h("option", { value: v }, t); if (v === cur) o.selected = true; return o; }));
    sel.addEventListener("change", () => { LS.set("core-start-module", sel.value); say("Saved: projects open on " + sel.options[sel.selectedIndex].text + "."); });
    const reBtn = h("button", { type: "button", class: "mini", id: "svDockReset", onclick: reset }, "Reset panels");
    sec.after(h("section", { class: "sv-section", id: "svWorkspace" },
      h("h2", null, "Workspace"),
      h("div", { class: "sv-note" }, "Where a project opens, and the panels around the sheet. Both are kept in this browser, like the theme."),
      h("div", { class: "sv-ws-row" }, h("label", { for: "svStartModule" }, "Open projects on"), sel),
      h("div", { class: "sv-ws-row" }, reBtn,
        h("span", { class: "sv-note", style: "margin:0" }, "Puts every panel back on its side and size, and the right rail at its width."))));
  }

  /* ------------------------------------------ LINE STYLES: FILTERS
     On a short rail (fit, compact) the colour chips, "Whose scope is this?" and the marks' key fold behind one
     button in the panel's head, so the rows keep the room. Folded, never gone: the button says how many trades are
     declared, and app.js's own "scope" button opens them too (it would otherwise hide a bar nobody can see). */
  const stylesActs = byId("stylesHead") && byId("stylesHead").querySelector(".side-actions");
  const filtBtn = h("button", { type: "button", class: "mini", id: "dkFiltersBtn", "aria-expanded": "false",
    title: "The colour chips, \"Whose scope is this?\" and the key to the two marks, folded so the list keeps its rows on this screen" },
  "Filters");
  if (stylesActs) stylesActs.append(filtBtn);
  function paintFilters() {
    const n = document.querySelectorAll("#scopeBar .scope-chip.on").length;
    const t = (filtersOpen ? "Hide filters" : "Filters") + (n && !filtersOpen ? ` (${n})` : "");
    if (filtBtn.textContent !== t) filtBtn.textContent = t;
    filtBtn.setAttribute("aria-expanded", filtersOpen ? "true" : "false");
  }
  function setFilters(open) { filtersOpen = !!open; paintFilters(); refresh(); }
  filtBtn.addEventListener("click", (e) => { e.stopPropagation(); setFilters(!filtersOpen); });
  const scopeTog = byId("scopeToggle");
  if (scopeTog) scopeTog.addEventListener("click", (e) => {
    const sc = byId("cardStyles");
    if (!sc || !sc.classList.contains("dk-compact") || scopeTog.getAttribute("aria-expanded") !== "true") return;
    e.stopImmediatePropagation(); e.preventDefault();
    setFilters(true);
  }, true);

  /* ------------------------------------------------------------ BOOT */
  for (const p of PANELS) furnish(p);
  // the module card and the Sheets card come into the left dock (shell.js
  // placed them above it); everything is then arranged by the saved layout
  leftDock.classList.add("dk-dock");
  rightDock.classList.add("dk-dock");
  apply(readLayout());
  settingsSection();
  window.coreDock = { reset, open: openPanel, close: closePanel, unfold: unfoldAuto, moveTo: (id, side, beforeId) =>
    moveTo(id, side === "right" ? rightDock : leftDock, beforeId ? pEl(beforeId) : null), layout: current, refresh };
})();
