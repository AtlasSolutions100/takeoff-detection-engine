// THE WALL PANEL CALCULATOR'S PAGE (2026-10-03): Tools -> Calculators -> Wall panels, and
// Settings -> Wall panels. Loaded after app.js and built on its globals (state, $, setStatus, draw,
// ptFromEvent, flChainAt, handleCanvasClick, showSettings).
//
// THIS PAGE OWNS NO NUMBER. The job's wall panels are one document per job and per account on the
// server (takeoff_engine/wp_job.py); GET /api/job/<id>/wallpanels answers it WITH every figure -
// each wall's feet, each room's wall length and suggested count, every line's quantity, rate and
// amount, the total, the sentence, what holds the proposal - and this file shows those strings as
// they came. A change is ONE ACT (POST .../wallpanels/act) naming what the person did and the version
// this tab read: a typed value, a choice, a line picked, a wall's two ends. No act carries a length,
// an amount or a total, and nothing here adds, multiplies or rounds one. A stale tab is refused and
// stops writing until it is reloaded.
(function () {
  "use strict";
  if (typeof state === "undefined" || !document.getElementById("wpPanel")) return;

  const WP_TOOLS = ["wp-pick", "wp-draw"];
  // a click places a point with either: the sheet shows the same cursor as the other point tools (app.js syncCursor)
  if (typeof POINT_TOOLS !== "undefined") for (const t of WP_TOOLS) POINT_TOOLS.add(t);
  const WP_STALE_TEXT = "This wall panel job was changed on another computer. Reload to see it.";
  // a wall's colour on the sheet says how its length stands, never more: blue when its sheet's scale is in force and
  // confirmed, amber when it is in force but not confirmed - or when the wall is measured at the scale of its own
  // view on a sheet that prints several (the view is told by its title: an inference) - red when it has no length
  // in feet. No green: nothing here is a second, agreeing reading.
  const WALL_CLEAN = "#2f81f7", WALL_AMBER = "#f2a33a", WALL_BAD = "#e0533d", WALL_DRAFT = "#58b7e6";
  const wp = { job: null, d: null, stale: false, queue: Promise.resolve(), room: null, draft: null, hover: null,
               held: null, loading: false, toolPage: null, chainIdx: null, chainRes: null };
  window.wpPanelState = wp;

  const mk = (tag, cls, txt) => {
    const el = document.createElement(tag);
    if (cls) el.className = cls;
    if (txt !== undefined && txt !== null) el.textContent = txt;
    return el;
  };
  const wpFor = () => (wp.d && wp.job === state.job ? wp : null);
  const readOnly = () => !!(wp.stale || (wp.d && wp.d.view_only));

  // ---- the document: read, and one act at a time ------------------------------------------
  async function wpLoad() {
    if (!state.job || wp.loading) return;
    const job = state.job;
    wp.loading = true;
    try {
      const r = await fetch(`/api/job/${job}/wallpanels`);
      if (!r.ok) { setStatus("the job's wall panels did not load (" + r.status + ")", "err"); return; }
      const d = await r.json();
      if (state.job !== job) return;
      wp.job = job; wp.d = d; wp.stale = false; wp.held = null;
      if (!d.rooms.some(x => x.id === wp.room)) wp.room = null;
    } catch (e) {
      setStatus("the job's wall panels did not load - the server did not answer", "err");
    } finally {
      wp.loading = false;
    }
    wpRender();
    draw();
  }
  function wpAct(act, opts) {
    if (!wpFor()) { setStatus("the job's wall panels are still loading - try again in a moment", "err"); return Promise.resolve(null); }
    if (wp.stale) { setStatus(WP_STALE_TEXT, "err"); return Promise.resolve(null); }
    const run = async () => {
      if (!wpFor() || wp.stale) return null;
      let r, d;
      try {
        r = await fetch(`/api/job/${wp.job}/wallpanels/act`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ version: wp.d.version, act }) });
        d = await r.json().catch(() => ({}));
      } catch (e) { setStatus("not saved - the server did not answer", "err"); return null; }
      if (r.status === 409 && d.client !== "old") { wp.stale = true; wpRender(); setStatus(d.detail || WP_STALE_TEXT, "err"); return null; }
      if (!r.ok) { wpRender(); setStatus(d.detail || `not saved (${r.status})`, "err"); return null; }
      wp.d = d; wp.held = null;
      wpRender();
      draw();
      if (d.ok === false) setStatus(d.why || "refused", "err");
      else if (opts && opts.status) { const m = opts.status(d); if (m) setStatus(m, "ok"); }
      else if (d.said) setStatus(d.said, "ok");
      return d;
    };
    wp.queue = (wp.queue || Promise.resolve()).then(run, run);
    return wp.queue;
  }

  // ---- small builders -----------------------------------------------------------------------
  // (a remove button, the small x, is clicked and never tabbed to: Tab goes from one box to the next box)
  // A box a person types in. It shows what the server holds and sends what was typed when the box is left or Enter is
  // pressed - once, and only when it differs. Every act redraws the panel, so each box carries a key (where it sits:
  // its room, row or section, and its place there): the box being typed in when an answer arrives keeps the caret and
  // whatever was typed since (wpRender puts both back), and what it holds is still sent when it is left.
  let KCTX = "", KN = 0;
  const ctx_ = (name) => { KCTX = name; KN = 0; };
  function input(value, placeholder, cls, onchange, title) {
    const i = document.createElement("input");
    i.className = cls || "fl-spec";
    i.value = value === null || value === undefined ? "" : String(value);
    i.dataset.k = KCTX + ":" + (KN++);
    i.dataset.v0 = i.value;
    if (placeholder) i.placeholder = placeholder;
    if (title) i.title = title;
    i.spellcheck = false;
    i.disabled = readOnly();
    const send = () => {
      if (wp.painting) return;              // a box taken off the page by a redraw is not a box that was left
      const v = i.value.trim();
      if (v === i.dataset.v0) return;
      i.dataset.v0 = v;
      onchange(v);
    };
    i.onchange = send;
    i.onblur = send;
    return i;
  }
  function field(label, el) {
    const l = mk("label", "wp-fld");
    l.append(mk("span", "wp-lab", label), el);
    return l;
  }
  function section(title, note) {
    const s = mk("div", "wp-sec");
    s.appendChild(mk("div", "wp-sec-h", title));
    if (note) s.appendChild(mk("div", "ew-meta", note));
    return s;
  }
  function linesTable(lines, total, words) {
    const t = mk("table", "fl-est wp-lines");
    t.innerHTML = "<tr><th>Line</th><th>Qty</th><th>Rate</th><th>Amount</th></tr>";
    for (const l of lines) {
      const tr = mk("tr");
      const what = mk("td");
      what.appendChild(mk("div", null, l.what));
      const how = [l.how].concat(l.missing || []).filter(Boolean).join(" · ");
      if (how) what.appendChild(mk("div", (l.missing || []).length ? "wp-miss" : "ew-meta", how));
      tr.append(what, mk("td", "n", l.qty ? l.qty + (l.unit ? " " + l.unit : "") : "—"), mk("td", "n", l.rate || "—"),
                mk("td", "n", l.amount || "—"));
      t.appendChild(tr);
    }
    const tr = mk("tr", "wp-total");
    tr.append(mk("td", null, "Total"), mk("td"), mk("td"), mk("td", "n", total));
    t.appendChild(tr);
    const box = mk("div");
    box.appendChild(t);
    if (words) box.appendChild(mk("div", "ew-meta wp-words", words));
    return box;
  }

  // ---- the panel ----------------------------------------------------------------------------
  function wpRender() {
    const body = $("wpBody");
    if (!body || $("wpPanel").hidden) return;
    // the box being typed in right now: its key, what it holds and where the caret is (put back after the redraw)
    const act0 = document.activeElement;
    const keep = act0 && act0.dataset && act0.dataset.k && body.contains(act0)
      ? { k: act0.dataset.k, v: act0.value, dirty: act0.value.trim() !== act0.dataset.v0, a: act0.selectionStart, b: act0.selectionEnd } : null;
    wp.painting = true;
    try { wpPaint(body); } finally {
      wp.painting = false;
      if (keep) {
        const el = [...body.querySelectorAll("input[data-k]")].find(q => q.dataset.k === keep.k);
        if (el && !el.disabled) {
          if (keep.dirty) el.value = keep.v;
          el.focus();
          try { el.setSelectionRange(keep.a, keep.b); } catch (e) { /* not a text box */ }
        }
      }
    }
  }
  function wpPaint(body) {
    body.innerHTML = "";
    if (!state.job) { body.appendChild(mk("div", "ew-meta", "open a plan set first")); return; }
    if (!wpFor()) { body.appendChild(mk("div", "ew-meta", "loading this job's wall panels…")); wpLoad(); return; }
    const d = wp.d, c = d.computed, ro = readOnly();
    if (wp.stale) {
      const w = mk("div", "fl-check", WP_STALE_TEXT + " ");
      const b = mk("button", "mini", "Reload");
      b.onclick = () => { wp.d = null; wp.stale = false; wpLoad(); };
      w.appendChild(b);
      body.appendChild(w);
    }
    if (d.view_only) body.appendChild(mk("div", "fl-check", "This sign-in can look, not change."));
    // the account's settings could not be read: said, never shown as "nothing set" (the server holds the proposal)
    if (d.settings_problem) body.appendChild(mk("div", "fl-check wp-differ wp-problem", d.settings_problem));
    // WHICH IS WHICH: what this job holds as its own (it never moves by itself) and what it follows the settings for
    if ((d.rates_follow || []).length) {
      const w = mk("div", "fl-check wp-differ wp-follow", "This job was started before these were set in your Wall panel rates, so it holds "
        + "none of its own: it follows your settings for them, and takes them as its own at its next change. Everything else is the job's own "
        + "copy" + (d.rates_at && d.rates_at.at ? " (taken " + d.rates_at.at + ")" : "") + " and does not move by itself.");
      for (const x of d.rates_follow) w.appendChild(mk("div", "fl-check-line", x));
      body.appendChild(w);
    }
    const priceNotes = c.price_notes || [];
    if ((d.rates_differ || []).length || priceNotes.length) {
      const w = mk("div", "fl-check wp-differ", "Your wall panel settings changed since this job took them"
        + (d.rates_at && d.rates_at.at ? " (" + d.rates_at.at + ")" : "") + ". The job stands on what it took:");
      for (const x of d.rates_differ || []) w.appendChild(mk("div", "fl-check-line", x));
      for (const x of priceNotes) w.appendChild(mk("div", "fl-check-line", x));
      if ((d.rates_differ || []).length) {
        const parts = d.differ_parts || {};
        const b = mk("button", "mini", parts.words && !parts.rates ? "Use my sentence and notes as they are now" : "Use my rates as they are now");
        b.disabled = ro;
        b.onclick = () => wpAct({ kind: "rates_take" });
        w.appendChild(b);
        // one part without the other: a corrected note or sentence into a priced job, its rates left as they are
        if (parts.rates && parts.words) {
          const br = mk("button", "mini", "Only the rates, thresholds and sizes");
          br.disabled = ro;
          br.onclick = () => wpAct({ kind: "rates_take", part: "rates" });
          const bw = mk("button", "mini", "Only the sentence and notes");
          bw.disabled = ro;
          bw.onclick = () => wpAct({ kind: "rates_take", part: "words" });
          w.append(" ", br, " ", bw);
        }
      }
      body.appendChild(w);
    }

    // ROOMS
    const rs = section("Rooms", "Wall lengths: type them joined by + (8 + 8 + 12.5: the sum is the wall length), or pick them on the sheet - "
      + "click a wall line, or draw a wall with two clicks; each is measured at the scale of the view it is drawn in.");
    d.rooms.forEach((r) => {
      const f = c.rooms[r.id] || { walls: [], suggestion: { options: [] }, flags: [] };
      const card = mk("div", "wp-room" + (wp.room === r.id ? " on" : ""));
      card.dataset.room = r.id;
      ctx_("room:" + r.id);
      const set = (fld) => (v) => wpAct({ kind: "room_set", id: r.id, field: fld, value: v });
      const top = mk("div", "wp-row");
      const x = mk("button", "mini", "×"); x.tabIndex = -1;
      x.title = "remove this room"; x.disabled = ro;
      x.onclick = () => wpAct({ kind: "room_del", id: r.id });
      top.append(field("Room", input(r.name, "room name", "fl-spec wp-name", set("name"))), field("No.", input(r.number, "—", "fl-num", set("number"))), x);
      const two = mk("div", "wp-row");
      two.append(field("Panel height", input(r.height, "8' or 96\"", "fl-num wp-w", set("height"), "With its unit: 8', 8 ft, 96\", 8'-6\"")),
                 field("Thickness", input(r.thickness, "—", "fl-num wp-w", set("thickness"))),
                 field("Fire rating", input(r.fire, "—", "fl-num wp-w", set("fire"))));
      const three = mk("div", "wp-row");
      const mol = input(r.molding, "—", "fl-num wp-w", set("molding"));
      mol.setAttribute("list", "wpMoldings");
      three.append(field("Manufacturer", input(r.mfr, "—", "fl-spec", set("mfr"))), field("Colour", input(r.colour, "—", "fl-spec", set("colour"))),
                   field("Product code", input(r.code, "—", "fl-spec", set("code"))), field("Molding", mol));
      const four = mk("div", "wp-row");
      four.append(field("Wall lengths, feet", input(r.typed, "8 + 8 + 12.5", "fl-spec wp-typed", set("typed"),
                        "Lengths joined by + (a comma followed by a space also separates two lengths). A plain number is feet, written without a "
                        + "thousands comma (1250, not 1,250); a length with its marks (8'-6\") works too. The sum is the wall length.")));
      card.append(top, two, three, four);
      if (f.typed_lf) card.appendChild(mk("div", "ew-meta", "typed: " + f.typed_lf + " LF"));
      for (const w of f.walls) {
        const ln = mk("div", "wp-wall" + (w.lf ? "" : " wp-miss"), w.words + " ");
        // a picked line erased on this screen afterwards still counts here: an erasure lives in this browser until
        // Approve and the server holds none - so it is said, and the person takes the wall off
        if (w.src === "line" && w.page === state.page && state.excluded && state.excluded.has(w.chain))
          ln.appendChild(mk("span", "wp-erased", "this line is erased on this screen - it still counts in this room: take it off (×) if it should not "));
        const wx = mk("button", "mini", "×"); wx.tabIndex = -1;
        wx.title = "take this wall off the room"; wx.disabled = ro;
        wx.onclick = () => wpAct({ kind: "wall_del", room: r.id, wall: w.id });
        if (w.page !== state.page) {
          const go = mk("button", "mini", "sheet " + (w.page + 1));
          go.onclick = () => { const ps = $("pageSel"); if (ps) { ps.value = String(w.page); ps.dispatchEvent(new Event("change")); } };
          ln.appendChild(go);
        }
        ln.appendChild(wx);
        card.appendChild(ln);
      }
      const tools = mk("div", "wp-row st-modes");
      const pick = mk("button", "mini" + (wp.room === r.id && state.tool === "wp-pick" ? " active" : ""), "Pick wall lines");
      const drw = mk("button", "mini" + (wp.room === r.id && state.tool === "wp-draw" ? " active" : ""), "Draw a wall");
      const noRes = !state.result;
      pick.disabled = drw.disabled = ro || noRes;
      pick.title = drw.title = noRes ? "Detect this sheet first: walls are picked on its detected lines and measured at its scale"
        : "Walls picked on the sheet are measured by the server at the scale of the view they are drawn in "
          + "(the sheet's scale in force where the sheet prints one scale)";
      pick.onclick = () => wpArm("wp-pick", r.id);
      drw.onclick = () => wpArm("wp-draw", r.id);
      tools.append(pick, drw);
      card.appendChild(tools);
      card.appendChild(mk("div", "wp-len", f.wall_lf ? "Wall length " + f.wall_lf + " LF" + (f.wall_words ? " — " + f.wall_words : "")
        : (f.wall_words || "no wall length yet")));
      for (const fl of f.flags || []) card.appendChild(mk("div", "wp-miss", "check: " + fl));
      const sg = f.suggestion || { options: [] };
      for (const o of sg.options) {
        // sizes that share the length this room needs are alternatives: each with its own count, never added together
        const s = mk("div", "wp-sugg" + (o.alt ? " wp-alt" : ""));
        s.append(mk("b", null, (o.alt ? "Or instead: " : "Suggested: ") + o.count + " of " + o.size), mk("span", null, " — " + o.arith + " — "), mk("i", null, sg.words));
        s.title = "Offered only: type the count in a sheet row. " + (o.alt ? "An alternative to the size above, not as well as it. " : "") + (sg.inference || "");
        card.appendChild(s);
      }
      if (sg.why) card.appendChild(mk("div", "ew-meta", sg.why));
      card.onmouseenter = () => { if (wp.hot !== r.id) { wp.hot = r.id; draw(); } };
      card.onmouseleave = () => { if (wp.hot === r.id) { wp.hot = null; draw(); } };
      rs.appendChild(card);
    });
    const addRoom = mk("button", "mini", "+ room");
    addRoom.disabled = ro;
    addRoom.onclick = () => wpAct({ kind: "room_add" });
    rs.appendChild(addRoom);
    body.appendChild(rs);
    let dl = $("wpMoldings");
    if (!dl) { dl = document.createElement("datalist"); dl.id = "wpMoldings"; document.body.appendChild(dl); }
    dl.innerHTML = "";
    for (const m of (d.rates.molding_types || [])) { const o = document.createElement("option"); o.value = m; dl.appendChild(o); }

    // SHEETS
    const ss = section("Sheets", "A row per size: the count and the price a sheet. A price is typed, or taken from one of your price items.");
    d.sheets.forEach((s) => {
      const row = mk("div", "wp-row wp-sheet");
      ctx_("sheet:" + s.id);
      const set = (fld) => (v) => wpAct({ kind: "sheet_set", id: s.id, field: fld, value: v });
      const sel = document.createElement("select");
      sel.className = "fl-sel"; sel.disabled = ro;
      const sizes = (d.rates.sizes || []).slice();
      if (s.size && !sizes.includes(s.size)) sizes.push(s.size);
      if (!s.size) { const o = document.createElement("option"); o.value = ""; o.textContent = "size…"; sel.appendChild(o); }
      for (const z of sizes) { const o = document.createElement("option"); o.value = z; o.textContent = z; if (z === s.size) o.selected = true; sel.appendChild(o); }
      sel.onchange = () => { if (sel.value) set("size")(sel.value); };
      const items = document.createElement("select");
      items.className = "fl-sel wp-items"; items.disabled = ro || !(d.price_items || []).length;
      const o0 = document.createElement("option"); o0.value = ""; o0.textContent = (d.price_items || []).length ? "from a price item…" : "no price items";
      items.appendChild(o0);
      for (const it of d.price_items || []) {
        const o = document.createElement("option");
        o.value = it.id; o.textContent = `${it.name}${it.code ? " (" + it.code + ")" : ""}: ${it.price} a ${it.per === "sqft" ? "square foot" : "sheet"}`;
        items.appendChild(o);
      }
      items.onchange = () => { if (items.value) wpAct({ kind: "sheet_price_item", id: s.id, item: items.value }); };
      const x = mk("button", "mini", "×"); x.tabIndex = -1;
      x.disabled = ro; x.title = "remove this row";
      x.onclick = () => wpAct({ kind: "sheet_del", id: s.id });
      const ln = (c.lines || []).find(l => l.id === "sheet:" + s.id) || {};
      row.append(field("Sheets", input(s.count, "count", "fl-num", set("count"), "A whole number of sheets")), field("Size", sel),
                 field("Price a sheet", input(d.typed.sheets[s.id], "$", "fl-num wp-w", set("price"), "Dollars and cents; blank = no price")),
                 field("Price item", items), mk("span", "wp-amt", ln.amount ? "= " + ln.amount : ""), x);
      ss.appendChild(row);
      const under = [ln.how].concat(ln.missing || []).filter(Boolean).join(" · ");
      if (under) ss.appendChild(mk("div", (ln.missing || []).length ? "wp-miss" : "ew-meta", under));
    });
    const addSheet = mk("button", "mini", "+ sheet row");
    addSheet.disabled = ro;
    addSheet.onclick = () => wpAct({ kind: "sheet_add", size: (d.rates.sizes || [])[0] || "" });
    ss.appendChild(addSheet);
    if (!(d.rates.sizes || []).length) ss.appendChild(mk("div", "wp-miss", "Your sheet sizes are not set: set them in Settings, Wall panels."));
    ss.appendChild(mk("div", "ew-meta", (c.sheets_total === null ? c.sheets_so_far + " sheets so far (a row has no count)" : c.sheets_total + " sheets on the job")
      + ((c.suggested || []).length ? " · suggested by the rooms: " + c.suggested.map(x => x.count + " of " + x.size).join(", ") + " (" + c.suggested_words + ")" : "")));
    body.appendChild(ss);

    // TYPED LINES
    const ls = section("Moldings, adhesive, other lines", "Typed: what, how many, the unit, the price each. No rule fills these.");
    d.lines.forEach((l) => {
      const row = mk("div", "wp-row wp-line");
      ctx_("line:" + l.id);
      const set = (fld) => (v) => wpAct({ kind: "line_set", id: l.id, field: fld, value: v });
      const x = mk("button", "mini", "×"); x.tabIndex = -1;
      x.disabled = ro;
      x.onclick = () => wpAct({ kind: "line_del", id: l.id });
      const ln = (c.lines || []).find(q => q.id === "line:" + l.id) || {};
      row.append(input(l.what, "what (division bar, adhesive…)", "fl-spec", set("what")), input(l.qty, "qty", "fl-num", set("qty")),
                 input(l.unit, "unit", "fl-num", set("unit")), input(d.typed.lines[l.id], "price", "fl-num wp-w", set("price")),
                 mk("span", "wp-amt", ln.amount ? "= " + ln.amount : ""), x);
      ls.appendChild(row);
    });
    const addLine = mk("button", "mini", "+ line");
    addLine.disabled = ro;
    addLine.onclick = () => wpAct({ kind: "line_add" });
    ls.appendChild(addLine);
    body.appendChild(ls);

    // COSTS
    const cs = section("Costs", "The job's own numbers. Every rate comes from your Wall panel rates in Settings; a rate that is not set says so.");
    const jset = (fld) => (v) => wpAct({ kind: "job_set", field: fld, value: v });
    const j = d.job;
    ctx_("job");
    const r1 = mk("div", "wp-row");
    r1.append(field("Days", input(j.days, "—", "fl-num", jset("days"), "How many days of labor; 0 for none. Blank holds the proposal.")), field("Hours a day", input(j.hours, "—", "fl-num", jset("hours"), "Hours worked a day; blank holds the proposal.")),
              field("Labor $/hour", input(d.typed.labor_rate, d.rates.labor_rate ? d.rates.labor_rate + " (yours)" : "not set", "fl-num wp-w", jset("labor_rate"),
                    "Blank = your own labor rate from Settings; a rate typed here is for this job only")));
    const r2 = mk("div", "wp-row");
    r2.append(field("Travel miles", input(j.miles, "—", "fl-num", jset("miles"), "0 for none. Blank holds the proposal.")), field("Hotel nights", input(j.nights, "—", "fl-num", jset("nights"), "0 for none. Blank holds the proposal.")),
              field("Freight $", input(d.typed.freight, "—", "fl-num wp-w", jset("freight"), "The freight amount; 0 for none. Blank holds the proposal.")),
              field("Profit $ (typed)", input(d.typed.profit, "by the rule", "fl-num wp-w", jset("profit"),
                    "Blank = the rule: the flat amount under your sheet threshold, otherwise the amount a day x days. An amount typed here replaces it and is shown as typed.")));
    cs.append(r1, r2, linesTable(c.lines || [], c.total, c.total_words));
    body.appendChild(cs);

    // PROPOSAL
    const ps = section("Proposal", "For the customer: your company block, the scope sentence, the price in figures and in words, your notes. No rate, no days, no profit.");
    ctx_("proposal");
    const r3 = mk("div", "wp-row");
    // the date the proposal will print, shown before issuing: the one typed for this job, else the server's today
    const dt = c.date || {};
    r3.append(field("Project", input(j.project, "project name", "fl-spec", jset("project"))), field("Location", input(j.location, "address or city", "fl-spec", jset("location"))),
              field("Date", input(dt.typed ? dt.text : "", dt.text ? dt.text + " (today)" : "", "fl-spec wp-date", jset("date"),
                    "The date the proposal prints. Blank = today, by the server's clock. Type another like October 6, 2026 or 10/6/2026 (month/day/year).")));
    ps.appendChild(r3);
    ps.appendChild(mk("div", "ew-meta wp-date-said", "Date on the proposal: " + (dt.text || "—") + (dt.by ? " (" + dt.by + ")" : "")));
    ps.appendChild(mk("div", "ew-meta", d.company ? "Company block: " + d.company : "Company block: not set (Settings, Company)"));
    const sc = c.scope || { blanks: [] };
    ps.appendChild(mk("div", "wp-scope", sc.text || "The scope sentence is not set: set it in Settings, Wall panels."));
    for (const b of sc.blanks || []) {
      const row = mk("div", "wp-row wp-blank");
      row.appendChild(mk("span", "wp-lab", b.name));
      if (b.kind === "typed") {
        // a blank no part of the job holds: only a person knows it, so it is typed for this job
        row.append(input(b.typed || "", "type it for this job", "fl-spec", (v) => wpAct({ kind: "blank_set", name: b.name, value: v }),
                         "Only you know this one: it is typed for this job"),
                   mk("span", b.value ? "ew-meta" : "wp-miss", b.value ? b.by : b.why));
      } else {
        // THE JOB FILLS IT, each time, from its sheet rows or its rooms: there is no box to type over it, so the
        // sentence can not say what the job no longer says
        row.append(mk("span", "wp-filled" + (b.value ? "" : " wp-miss"), b.value || "—"),
                   mk("span", b.value ? "ew-meta" : "wp-miss",
                      b.value ? b.by + " — the job fills it; change it there"
                        : b.why + (b.kind === "sheets" ? " — give the sheet rows their counts" : b.kind === "job" ? " — type it in its box above" : " — type it on a room")));
      }
      ps.appendChild(row);
    }
    for (const n of c.notes || []) ps.appendChild(mk("div", "ew-meta wp-note", "• " + n));
    const held = wp.held || (c.issue ? null : c.hold);
    if (held && held.length) {
      const hb = mk("div", "fl-check wp-held", "The proposal is held until these are set:");
      for (const h of held) hb.appendChild(mk("div", "fl-check-line", h));
      ps.appendChild(hb);
    } else ps.appendChild(mk("div", "ew-meta wp-ready", "Nothing is missing: the proposal can be issued for " + c.total + "."));
    const acts = mk("div", "wp-row");
    const pdf = mk("button", "mini", "Proposal PDF");
    pdf.id = "wpPdf";
    pdf.onclick = () => wpDownload("wallpanels_proposal.pdf");
    const ws = mk("button", "mini", "Worksheet CSV (internal)");
    ws.id = "wpCsv";
    ws.onclick = () => wpDownload("wallpanels_worksheet.csv");
    acts.append(pdf, ws);
    ps.appendChild(acts);
    body.appendChild(ps);
    $("wpResult").textContent = d.rooms.length + " room" + (d.rooms.length === 1 ? "" : "s") + " · total " + c.total
      + (c.issue ? "" : " · proposal held (" + c.hold.length + ")");
  }

  // a document of the job. The proposal is asked for first: while anything is missing the server refuses it with its
  // list, and the list is shown here; otherwise the browser takes the file from the same address (the server names it).
  async function wpDownload(name) {
    if (!state.job) return;
    const at = `/api/job/${state.job}/${name}`;
    if (name === "wallpanels_proposal.pdf") {
      let r;
      try { r = await fetch(at); } catch (e) { return setStatus("the server did not answer", "err"); }
      if (r.status === 409) {
        const d = await r.json().catch(() => ({}));
        wp.held = d.missing || [];
        wpRender();
        const hb = document.querySelector("#wpBody .wp-held");
        if (hb) hb.scrollIntoView({ block: "center" });
        return setStatus((d.detail || "The proposal is held.") + " " + wp.held.length + " missing - listed in the panel", "err");
      }
      if (!r.ok) return setStatus("could not make that document (" + r.status + ")", "err");
    }
    const a = document.createElement("a");
    a.href = url(at);
    a.click();
    setStatus(name === "wallpanels_proposal.pdf" ? "the proposal is downloading" : "the worksheet is downloading", "ok");
  }

  // ---- the two wall tools on the sheet ------------------------------------------------------
  function wpRoom() { return wpFor() ? wp.d.rooms.find(r => r.id === wp.room) || null : null; }
  function wpArm(tool, roomId) {
    if (state.tool === tool && wp.room === roomId) return wpDisarm("wall panels: tool released");
    if (state.tool && !WP_TOOLS.includes(state.tool)) return setStatus("finish or Esc the current tool first", "err");
    if (!state.result) return setStatus("Detect this sheet first: walls are picked on its detected lines and measured at its scale", "err");
    wp.room = roomId; wp.draft = null; wp.hover = null; wp.toolPage = state.page;
    state.tool = tool;
    const r = wpRoom();
    setStatus(tool === "wp-pick"
      ? `${r ? r.name : "room"}: click each wall LINE on the sheet - the server measures it at the sheet's scale (Esc to stop)`
      : `${r ? r.name : "room"}: draw a wall - click one end, then the other (Esc to stop)`, "busy");
    wpRender();
    draw();
  }
  function wpDisarm(msg) {
    if (WP_TOOLS.includes(state.tool)) state.tool = null;
    wp.draft = null; wp.hover = null;
    if (msg !== undefined) setStatus(msg);
    wpRender();
    draw();
  }
  function wallSaid(d, roomId) {
    const f = d.computed.rooms[roomId];
    const w = f && f.walls[f.walls.length - 1];
    return w ? w.words + (f.wall_lf ? " — the room is " + f.wall_lf + " LF" : "") : d.said;
  }
  function wpCanvasClick(e) {
    const r = wpRoom();
    if (!r) return wpDisarm("that room is not on the job any more");
    const [px, py] = ptFromEvent(e);
    if (state.tool === "wp-pick") {
      const ch = typeof flChainAt === "function" ? flChainAt(px, py) : null;
      if (!ch) return setStatus("no line under that click - zoom in and click ON the wall line, or draw the wall", "err");
      wpAct({ kind: "wall_pick", room: r.id, page: state.page, chain: ch.chain_id }, { status: (d) => wallSaid(d, r.id) });
      return;
    }
    if (!wp.draft) {
      wp.draft = [px, py];
      setStatus("draw a wall: now click its other end", "busy");
      draw();
      return;
    }
    const seg = [wp.draft[0], wp.draft[1], px, py];
    wp.draft = null; wp.hover = null;
    wpAct({ kind: "wall_draw", room: r.id, page: state.page, seg }, { status: (d) => wallSaid(d, r.id) });
  }
  const _wpClickBefore = handleCanvasClick;
  handleCanvasClick = function (e) {
    if (WP_TOOLS.includes(state.tool)) return wpCanvasClick(e);
    return _wpClickBefore(e);
  };
  addEventListener("mousemove", (e) => {
    if (state.tool !== "wp-draw" || !wp.draft || (typeof panning !== "undefined" && panning)) return;
    wp.hover = ptFromEvent(e);
    draw();
  });
  addEventListener("keydown", (e) => {
    if (e.key !== "Escape" || !WP_TOOLS.includes(state.tool)) return;
    if (typeof typing === "function" && typing()) return;
    e.preventDefault(); e.stopImmediatePropagation();
    if (wp.draft) { wp.draft = null; wp.hover = null; setStatus("first end cleared - Esc again releases the tool", "busy"); draw(); }
    else wpDisarm("wall panels: tool released");
  }, true);

  // the picked and drawn walls of this job on the sheet in view, over the drawing
  function chainOf(id) {
    if (wp.chainRes !== state.result) {
      wp.chainRes = state.result;
      wp.chainIdx = new Map(((state.result && state.result.chains) || []).map(ch => [ch.chain_id, ch]));
    }
    return wp.chainIdx.get(id);
  }
  function wpOverlay() {
    $("wpBtn").hidden = !state.job;
    if (wp.job && wp.job !== state.job) {            // another plan set is open: this panel is that job's now
      wp.job = null; wp.d = null; wp.room = null; wp.draft = null; wp.stale = false;
      if (WP_TOOLS.includes(state.tool)) state.tool = null;
      if (!$("wpPanel").hidden) wpRender();
    }
    if (WP_TOOLS.includes(state.tool) && wp.toolPage !== state.page) { wp.draft = null; wp.hover = null; wp.toolPage = state.page; }
    // the sheet in view was detected (again): its walls are measured again by the server - ask for them
    if (wp.resSeen !== state.result) {
      const first = wp.resSeen === undefined;
      wp.resSeen = state.result;
      if (!first && wpFor() && !$("wpPanel").hidden) wpLoad();
    }
    if (!state.img || !wpFor() || ($("wpPanel").hidden && !WP_TOOLS.includes(state.tool))) return;
    const f = IMG_DPI / 72, k = state.view.k;
    ctx.save();
    ctx.setTransform(devicePixelRatio, 0, 0, devicePixelRatio, 0, 0);
    ctx.translate(state.view.x, state.view.y);
    ctx.scale(k, k);
    viewApplyRot(ctx);
    ctx.lineCap = "round"; ctx.lineJoin = "round"; ctx.setLineDash([]);
    for (const r of wp.d.rooms) {
      const fig = (wp.d.computed.rooms[r.id] || {}).walls || [];
      const on = r.id === wp.room || r.id === wp.hot;
      for (const w of r.walls) {
        if (w.page !== state.page) continue;
        const cw = fig.find(q => q.id === w.id) || {};
        let pts = null;
        if (w.src === "drawn") pts = [[w.seg[0], w.seg[1]], [w.seg[2], w.seg[3]]];
        else { const ch = chainOf(w.chain); pts = ch && ch.points && ch.points.length > 1 ? ch.points : [w.a, w.b]; }
        ctx.beginPath();
        ctx.moveTo(pts[0][0] * f, pts[0][1] * f);
        for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i][0] * f, pts[i][1] * f);
        ctx.lineWidth = (on ? 6 : 4) / k;
        ctx.strokeStyle = !cw.lf ? WALL_BAD : cw.state === "clean" ? WALL_CLEAN : WALL_AMBER;
        ctx.globalAlpha = on ? 0.95 : 0.55;
        ctx.stroke();
        ctx.globalAlpha = 1;
        // its length, as the server printed it, beside the middle of its first run
        const a = pts[0], b = pts[1];
        ctx.font = `600 ${12 / k}px sans-serif`;
        ctx.fillStyle = ctx.strokeStyle;
        // under the line, so a dimension the sheet prints over that line stays readable beside it
        // (why a wall has no feet is the server's word: no scale in force, or its view's scale is not known)
        ctx.fillText(cw.lf ? cw.lf + " LF" : cw.pt ? cw.pt + " pt (" + (cw.short || "no scale in force") + ")" : "not measured", (a[0] + b[0]) / 2 * f + 6 / k, (a[1] + b[1]) / 2 * f + 18 / k);
      }
    }
    if (state.tool === "wp-draw" && wp.draft) {
      ctx.beginPath(); ctx.arc(wp.draft[0] * f, wp.draft[1] * f, 4 / k, 0, Math.PI * 2);
      ctx.fillStyle = WALL_DRAFT; ctx.fill();
      if (wp.hover) {
        ctx.beginPath(); ctx.moveTo(wp.draft[0] * f, wp.draft[1] * f); ctx.lineTo(wp.hover[0] * f, wp.hover[1] * f);
        ctx.lineWidth = 2 / k; ctx.setLineDash([6 / k, 4 / k]); ctx.strokeStyle = WALL_DRAFT; ctx.stroke(); ctx.setLineDash([]);
      }
    }
    ctx.restore();
  }
  const _wpDrawBefore = draw;
  draw = function () {
    _wpDrawBefore();
    try { wpOverlay(); } catch (e) { /* the overlay never costs the sheet its paint */ }
  };
  // THE WALLS FOLLOW THE SHEET'S SCALE. A scale a person chose, typed or measured is recorded on the server first and
  // the page then repaints everything that stands on it (app.js scaleRepaint): the walls picked on that sheet are
  // measured again by the server at the scale now in force, so the panel asks for them again.
  if (typeof scaleRepaint === "function") {
    const _wpRepaintBefore = scaleRepaint;
    scaleRepaint = function () {
      _wpRepaintBefore();
      if (wpFor() && !$("wpPanel").hidden) wpLoad();
    };
  }

  // ---- the panel's button ---------------------------------------------------------------------
  $("wpBtn").onclick = () => {
    const panel = $("wpPanel");
    if (!panel.hidden) { panel.hidden = true; wpDisarm(); return; }
    if (!state.job) return setStatus("open a plan set first", "err");
    panel.hidden = false;
    panel.scrollIntoView({ block: "start", behavior: "smooth" });
    if (wpFor()) { wpRender(); wpLoad(); } else { wpRender(); }
    draw();
  };
  $("wpClose").onclick = () => { $("wpPanel").hidden = true; wpDisarm(); };
  {
    const head = $("wpHead");
    head.querySelector("h2").onclick = () => {
      const closed = head.classList.toggle("closed");
      for (const id of ["wpHint", "wpBody", "wpActions"]) $(id).hidden = closed;
    };
  }
  $("wpBtn").hidden = !state.job;

  // ---- SETTINGS -> WALL PANELS: the account's own rates -------------------------------------
  // One record per account on the server (takeoff_engine/wp_settings.py). Saved whole; every problem
  // the server finds is listed and nothing is saved. Staff may choose another account, and may load a
  // record from a JSON file: the file's contents are posted as they are and held to the same checks.
  const sv = { p: null, account: null, busy: false };
  const SV_MONEY = ["labor_rate", "overtime_rate", "travel_per_mile", "hotel_nightly", "profit_flat", "profit_per_day"];
  const SV_ORDER = ["labor_rate", "overtime_after_hours", "overtime_rate", "travel_per_mile", "hotel_nightly",
                    "profit_flat_under_sheets", "profit_flat", "profit_per_day"];
  async function svLoad(account) {
    const box = $("svWpBody");
    if (!box) return;
    let r;
    try {
      r = account ? await fetch("/api/wallpanels/settings/of", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ account }) })
                  : await fetch("/api/wallpanels/settings");
    } catch (e) { $("svWpNote").textContent = "The server did not answer."; return; }
    if (!r.ok) { $("svWpNote").textContent = "Could not read the wall panel settings (" + r.status + ")."; return; }
    sv.p = await r.json();
    sv.account = sv.p.account;
    svRender();
  }
  function svRender(problems, said) {
    const p = sv.p, box = $("svWpBody");
    if (!p || !box) return;
    const s = p.settings, ro = !!p.view_only;
    box.innerHTML = "";
    if (p.problem) {
      // THE STORE CAN NOT BE READ: said in the server's words. No form is drawn - blank boxes would read as "not set" -
      // and nothing can be saved over the file until it is mended.
      $("svWpNote").textContent = p.problem;
      box.appendChild(mk("div", "fl-check wp-sv-problems", "No rate is shown and nothing can be saved here until the settings store is mended."));
      $("svWpSave").disabled = true;
      $("svWpLoad").hidden = !p.staff;
      $("svWpLoad").disabled = true;
      $("svWpState").textContent = said || "";
      return;
    }
    const whose = p.account && p.account !== p.me ? p.account + "'s" : (p.me ? "your account's" : "this computer's");
    $("svWpNote").textContent = `These are ${whose} wall panel rates. Nothing here has a starting value: a rate left blank is not set, `
      + "and a wall panel proposal is held until every rate its lines use is set (0 is allowed: it means you never charge it). "
      + (s.saved && s.saved.at ? `Last saved ${s.saved.at}${s.saved.by ? " by " + s.saved.by : ""}${s.saved.how ? " (" + s.saved.how + ")" : ""}.` : "Never saved.");
    if (p.staff && p.accounts) {
      const row = mk("div", "wp-sv-row");
      const sel = document.createElement("select");
      sel.id = "svWpAccount";
      for (const a of p.accounts) { const o = document.createElement("option"); o.value = a; o.textContent = a + (a === p.me ? " (you)" : ""); if (a === p.account) o.selected = true; sel.appendChild(o); }
      sel.onchange = () => svLoad(sel.value === p.me ? null : sel.value);
      row.append(mk("span", "wp-lab", "Account"), sel, mk("span", "sv-note", "Staff: see, change or load any account's wall panel settings."));
      box.appendChild(row);
    }
    const grid = mk("div", "wp-sv-grid");
    for (const k of SV_ORDER) {
      const i = document.createElement("input");
      i.id = "svWp_" + k; i.type = "text"; i.inputMode = "decimal"; i.spellcheck = false; i.disabled = ro;
      i.value = s[k] === null || s[k] === undefined ? "" : String(s[k]);
      i.placeholder = "not set";
      const l = mk("label", "wp-sv-fld");
      l.append(mk("span", null, p.words[k] || k), i);
      grid.appendChild(l);
    }
    box.appendChild(grid);
    if ((s.unreadable || []).length) {
      // a value the file holds and the server can not read (the file was changed by hand): never passed off as never set
      const ub = mk("div", "fl-check wp-sv-problems", "These saved values could not be read and show as not set here. Type them again and save:");
      for (const x of s.unreadable) ub.appendChild(mk("div", "fl-check-line", x));
      box.appendChild(ub);
    }
    const txt = (id, label, value, ph, rows) => {
      const el = document.createElement(rows ? "textarea" : "input");
      el.id = id; el.value = value || ""; el.placeholder = ph; el.spellcheck = false; el.disabled = ro;
      if (rows) el.rows = rows; else el.type = "text";
      const l = mk("label", "wp-sv-fld wp-sv-wide");
      l.append(mk("span", null, label), el);
      box.appendChild(l);
      return el;
    };
    txt("svWp_sizes", "Sheet sizes you buy, width x length in feet, separated by commas", (s.sizes || []).join(", "), "4x8, 4x10");
    txt("svWp_molding_types", "Molding types, separated by commas", (s.molding_types || []).join(", "), "the names you use");
    txt("svWp_scope_template", "The proposal's scope sentence. A blank is a name in braces.", s.scope_template, "… {thickness} … {colour} … {sheets} sheets of {sizes} …", 3);
    box.appendChild(mk("div", "sv-note", "Blanks a job fills by itself, each time it is read, and that can not be typed over: from its rooms (every different value "
      + "the rooms give, in their order) " + (p.room_blanks || []).map(b => "{" + b + "}").join(", ")
      + "; from its sheet rows " + (p.job_blanks || []).map(b => "{" + b + "}").join(" and ")
      + "; from its own boxes " + (p.field_blanks || []).map(b => "{" + b + "}").join(", ")
      + ". Any other name is typed on each job - but not a name for a figure the job works out (its total, its days, a rate): a figure typed by hand "
      + "would not follow the job, and the proposal prints its price by itself. A blank left open holds the proposal. A name is read whatever its case."));
    txt("svWp_notes", "The proposal's standard notes, one a line", (s.notes || []).join("\n"), "one note a line", 5);
    txt("svWp_about", "A note about these settings (not printed anywhere)", s.about, "where these numbers came from");
    // price items
    box.appendChild(mk("div", "wp-sec-h", "Price items"));
    box.appendChild(mk("div", "sv-note", "A price for one sheet, or for one square foot (then a sheet's price is that x the sheet's area, rounded to the cent)."));
    const tbl = mk("div", "wp-sv-items");
    tbl.id = "svWpItems";
    const addItemRow = (it) => {
      const row = mk("div", "wp-sv-item");
      row.dataset.id = (it && it.id) || "";
      const nm = document.createElement("input"); nm.placeholder = "name"; nm.value = (it && it.name) || ""; nm.className = "wp-i-name";
      const cd = document.createElement("input"); cd.placeholder = "code"; cd.value = (it && it.code) || ""; cd.className = "wp-i-code";
      const per = document.createElement("select"); per.className = "wp-i-per";
      for (const [v, t] of [["sheet", "a sheet"], ["sqft", "a square foot"]]) { const o = document.createElement("option"); o.value = v; o.textContent = t; if (it && it.per === v) o.selected = true; per.appendChild(o); }
      const pr = document.createElement("input"); pr.placeholder = "price"; pr.value = (it && it.price) || ""; pr.className = "wp-i-price"; pr.inputMode = "decimal";
      const x = mk("button", "mini", "×"); x.tabIndex = -1; x.onclick = () => row.remove();
      for (const el of [nm, cd, per, pr, x]) el.disabled = ro;
      row.append(nm, cd, per, pr, x);
      tbl.appendChild(row);
    };
    for (const it of s.price_items || []) addItemRow(it);
    box.appendChild(tbl);
    const add = mk("button", "mini", "+ price item");
    add.disabled = ro;
    add.onclick = () => addItemRow(null);
    box.appendChild(add);
    if (problems && problems.length) {
      const pb = mk("div", "fl-check wp-sv-problems", "Nothing was saved:");
      for (const x of problems) pb.appendChild(mk("div", "fl-check-line", x));
      box.appendChild(pb);
    }
    $("svWpSave").disabled = ro;
    $("svWpLoad").hidden = !p.staff;
    $("svWpLoad").disabled = ro;
    $("svWpState").textContent = said || (ro ? "This sign-in can look, not change." : "");
  }
  function svCollect() {
    const v = (id) => ($(id) ? $(id).value.trim() : "");
    const list = (id, sep) => v(id).split(sep).map(x => x.trim()).filter(Boolean);
    const out = {};
    for (const k of SV_ORDER) out[k] = v("svWp_" + k) === "" ? null : v("svWp_" + k);
    out.sizes = list("svWp_sizes", ",");
    out.molding_types = list("svWp_molding_types", ",");
    out.scope_template = v("svWp_scope_template");
    out.notes = list("svWp_notes", "\n");
    out.about = v("svWp_about");
    out.price_items = [...document.querySelectorAll("#svWpItems .wp-sv-item")].map(row => ({
      id: row.dataset.id || undefined, name: row.querySelector(".wp-i-name").value.trim(), code: row.querySelector(".wp-i-code").value.trim(),
      per: row.querySelector(".wp-i-per").value, price: row.querySelector(".wp-i-price").value.trim() }))
      .filter(it => it.name || it.price || it.code);
    for (const it of out.price_items) if (it.id === undefined) delete it.id;
    return out;
  }
  async function svSave(settings, how) {
    if (!sv.p || sv.busy) return;
    sv.busy = true;
    $("svWpState").textContent = "Saving…";
    let r, d;
    try {
      r = await fetch("/api/wallpanels/settings", { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ settings, rev: sv.p.settings.rev || 0, account: sv.account && sv.account !== sv.p.me ? sv.account : null, how: how || null }) });
      d = await r.json().catch(() => ({}));
    } catch (e) { sv.busy = false; $("svWpState").textContent = "Not saved: the server did not answer."; return; }
    sv.busy = false;
    if (r.status === 422) return svRender(d.problems || [d.detail || "refused"], "Not saved.");
    if (!r.ok) {
      // (a store that can not be read refuses the save: the page asks again and shows the server's sentence)
      if (d.damaged) await svLoad(sv.account && sv.account !== sv.p.me ? sv.account : null);
      $("svWpState").textContent = d.detail || "Not saved (" + r.status + ").";
      return;
    }
    sv.p = d;
    svRender(null, how === "file" ? "Loaded from the file and saved." : "Saved.");
    if (wpFor()) wpLoad();          // an open job says what now differs from the rates it took
  }
  if ($("svWpSave")) {
    $("svWpSave").onclick = () => svSave(svCollect());
    $("svWpLoad").onclick = () => $("svWpFile").click();
    $("svWpFile").onchange = () => {
      const f = $("svWpFile").files && $("svWpFile").files[0];
      $("svWpFile").value = "";
      if (!f) return;
      const rd = new FileReader();
      rd.onload = () => {
        let parsed;
        try { parsed = JSON.parse(String(rd.result).replace(/^﻿/, "")); }
        catch (e) { return svRender(["that file is not JSON: " + e.message], "Not loaded."); }
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return svRender(["that file is not one JSON object of named settings"], "Not loaded.");
        const who = sv.account || "this computer";
        if (!confirm(`Replace ${who}'s wall panel settings with the contents of ${f.name}?`)) return;
        svSave(parsed, "file");
      };
      rd.readAsText(f);
    };
    const _wpShowSettings = showSettings;
    showSettings = function (on) {
      const was = typeof svIsOpen === "function" && svIsOpen();
      _wpShowSettings(on);
      if (on && !was) svLoad(sv.account && sv.p && sv.account !== sv.p.me ? sv.account : null);
      // back on the sheet: the company name or a rate may have changed in Settings - the job is asked again
      if (!on && was && wpFor() && !$("wpPanel").hidden) wpLoad();
    };
    if (typeof svIsOpen === "function" && svIsOpen()) svLoad(null);
  }
})();
