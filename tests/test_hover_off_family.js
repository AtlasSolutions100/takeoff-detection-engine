// HOVER HIGHLIGHTS A FAMILY WHATEVER ITS TICK SAYS.
//
// `state.include` was the only flag in the app and it meant two things at
// once: "this family is COUNTED in the takeoff" and "this family is PAINTED
// on the canvas". draw()'s chain loop derived visibility straight from it,
// and it computed `hot` on the line AFTER that guard - so every chain of an
// unticked family was already `continue`d past before the highlight pass
// could look at it. Hovering the row set state.hotStyle correctly and lit
// absolutely nothing.
//
// Measured on C4.0-SITE-GRADING-PLAN-Rev.1.pdf p0 (printed sheet C4.0),
// style 13 = CAD layer 24007215.01-X-SITE|P1-PVMT-HATCH, 21 chains: with the
// tick off and the row hovered, ZERO of the 82,972 pixels that separate the
// two frames came from that family.
//
// The AREA pass ~60 lines earlier in the same function already did it right
// (`const on = ...; const hot = ...; if (!on && !hot) continue;`), so
// hovering an unticked AREA lit up and hovering an unticked LINE FAMILY did
// not - two behaviours from one function.
//
// WHAT IS PINNED HERE
//
//   1. THE LOOP ORDER, behaviourally: hover beats the tick, deletion beats
//      hover, isolate precedence is unchanged in both directions. The whole
//      fix is an ordering, and only running the ordering tests it.
//   2. A GHOST DOES NOT READ AS COUNTED. This is the part that matters most:
//      the tick's entire value is that OFF means not in the bid, and this is
//      a tool where a confident wrong number reaches one. The tell is
//      STRUCTURAL - a ticked family gets the halo PLUS a crisp stroke at its
//      true weight and dash phase, a ghost gets the halo and NO measured
//      line - because structure survives a bad monitor, greyscale and
//      colour blindness where a colour difference does not. Asserted as
//      canvas calls against a recording stub, so "somebody added a stroke
//      back for ghosts" fails here rather than in a bid.
//   3. THE GHOST IS NOT THE FAMILY'S OWN COLOUR. CV_FAM_NEARWHITE /
//      CV_FAM_UNKNOWN exist because the engine sometimes measures no usable
//      colour; those families already halo almost invisibly when ticked ON,
//      so "same colour, lower alpha" would be a no-op on exactly the
//      families this was asked for.
//   4. THE WORDS. The owner is not an estimator and should not have to learn
//      a rendering convention: a pill on the canvas says "NOT counted in the
//      takeoff" in English. It must NOT use the shared status bar, which can
//      be carrying the earthwork read's busy line.
//   4b. THE WORDS COVER EVERY GHOSTING PATH (added 2026-08-31). The loop
//      ghosts on `hotStyle.has(sid) || hotChain === ch.chain_id`, and
//      ghostBarSync looked only at hotStyle - so the per-CHAIN hover (line
//      list, calculator rows, callout rows, earthwork rows) painted a ghost
//      with no disclosure at all. Before this feature that path painted
//      nothing, so the gap shipped WITH the feature, and an unlabelled
//      ghost is precisely the mistake the feature exists to prevent.
//      Behavioural: ghostBarSync run for real over each hover path.
//   4c. SELECTION IS A GHOSTING PATH TOO (added 2026-08-31). A line of an
//      unticked family could be SELECTED - the pill named it, the vertex
//      handles were drawn on it - and the line itself painted nothing at
//      all, not even while its own row was hovered: the halo was gated
//      `hot && !sel` and the measured stroke is skipped for every ghost, so
//      a selected ghost fell between the two passes. It now gets the same
//      slate ribbon and the same "NOT counted" pill, and still no measured
//      line and no selection magenta - being picked is not being counted.
//   5. THE OBJECT-MODE BOXES STAY OFF for a ghost, on purpose - a box IS a
//      count. Stated here so it is a decision on the record, not a
//      half-finished fix.
//   6. REPAINT DEDUPE on the per-chain hover writers. Those repaints were
//      free while an off family painted nothing; they are not free now.
//
// webui/app.js is not a module and touches the DOM at load, so the draw()
// suite runs the extracted chain loop against a recording 2-D context - the
// same technique as MULTISELECT / LINE-FAMILY / EW-BUSY-STATE.
//
// Dependency-free:  node tests/test_hover_off_family.js

"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.join(__dirname, "..");
const SRC = fs.readFileSync(path.join(ROOT, "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(ROOT, "webui", "index.html"), "utf8");
const CSS = fs.readFileSync(path.join(ROOT, "webui", "style.css"), "utf8");

let fails = 0;
const check = (ok, msg) => {
  if (ok) console.log("  ok: " + msg);
  else { fails++; console.error("  FAIL: " + msg); }
};
const eq = (got, want, msg) =>
  check(got === want, msg + "  (got " + JSON.stringify(got) + ")");

// ---------------------------------------------------------------------------
// The chain loop, lifted out of draw() and run for real.
// ---------------------------------------------------------------------------
// draw()'s chain loop specifically - several functions walk
// state.result.chains, so anchor on the one that computes `hot`, and brace
// match rather than trusting indentation.
const LOOP = (function () {
  const NEEDLE = "for (const ch of state.result.chains) {";
  for (let i = SRC.indexOf(NEEDLE); i >= 0; i = SRC.indexOf(NEEDLE, i + 1)) {
    let d = 0;
    for (let k = SRC.indexOf("{", i); k < SRC.length; k++) {
      if (SRC[k] === "{") d++;
      else if (SRC[k] === "}" && --d === 0) {
        const body = SRC.slice(i, k + 1);
        if (body.indexOf("const hot =") >= 0) return body;
        i = k;
        break;
      }
    }
  }
  return "";
})();
check(!!LOOP, "draw()'s chain loop is locatable");

const CV_GHOST = (SRC.match(/const CV_GHOST\s*=\s*"([^"]+)"/) || [])[1];
const CV_SELECT = (SRC.match(/const CV_SELECT\s*=\s*"([^"]+)"/) || [])[1];
check(!!CV_GHOST, "CV_GHOST is declared with the other canvas colours");

function recorder() {
  const calls = [];
  const c = {
    _dash: [], globalAlpha: 1, strokeStyle: "", lineWidth: 0,
    lineCap: "", lineJoin: "",
    // save/restore must really restore: the halo pass sets lineWidth and
    // strokeStyle inside one, and the crisp stroke that follows relies on
    // the real canvas putting them back.
    _stack: [],
    save() {
      calls.push(["save"]);
      this._stack.push({ dash: this._dash.slice(), alpha: this.globalAlpha,
                         stroke: this.strokeStyle, width: this.lineWidth,
                         cap: this.lineCap, join: this.lineJoin });
    },
    restore() {
      calls.push(["restore"]);
      const s = this._stack.pop();
      if (!s) return;
      this._dash = s.dash; this.globalAlpha = s.alpha;
      this.strokeStyle = s.stroke; this.lineWidth = s.width;
      this.lineCap = s.cap; this.lineJoin = s.join;
    },
    setLineDash(d) { this._dash = d; calls.push(["setLineDash", d.slice()]); },
    beginPath() { calls.push(["beginPath"]); },
    moveTo() {}, lineTo() {},
    stroke() {
      calls.push(["stroke", { color: this.strokeStyle, alpha: this.globalAlpha,
                              width: this.lineWidth, cap: this.lineCap,
                              dash: this._dash.slice() }]);
    },
  };
  return { ctx: c, calls };
}

// Two chains, two families. 13 is the hatch family the owner hovers.
function chains() {
  return [
    { chain_id: 1, style_id: 13, class: "annotation", width: 0.35,
      points: [[0, 0], [10, 0]] },
    { chain_id: 2, style_id: 5, class: "solid", width: 0.5,
      points: [[0, 5], [10, 5]] },
  ];
}

function runLoop(over) {
  const rec = recorder();
  const state = Object.assign({
    result: { chains: chains(),
              styles: [{ style_id: 13, texture: false, class: "solid" },
                       { style_id: 5, texture: false, class: "solid" }] },
    excluded: new Set(),
    isolate: null,
    include: new Map([[13, false], [5, true]]),
    hotStyle: null, hotChain: null,
    selected: null, multiSel: new Set(),
    faithful: true, view: { k: 1 },
  }, over || {});
  const scope = {
    ctx: rec.ctx, state, f: 1, k: 1,
    CV_SELECT, CV_GHOST,
    famShown: (sid) => state.isolate !== null ? sid === state.isolate
                                              : !!state.include.get(sid),
    chainCssColor: () => "#ff7700",
    dashArray: () => [4, 4],
    gcol: null,            // wall-group colours (structure panel, 2026-09-16): off here
  };
  const names = Object.keys(scope);
  new Function(...names, LOOP)(...names.map(n => scope[n]));
  // one stroke record per painting pass, in order
  return rec.calls.filter(c => c[0] === "stroke").map(c => c[1]);
}

// ---------------------------------------------------------------------------
console.log("1. hover beats the tick; deletion beats hover; isolate is unchanged");
// ---------------------------------------------------------------------------
{
  const s = runLoop({});
  eq(s.length, 1, "baseline: only the ticked family paints");
  eq(s[0].color, "#ff7700", "...in its own colour");
}
{
  // THE REPORTED BUG
  const s = runLoop({ hotStyle: new Set([13]) });
  check(s.length > 1, "hovering the UNTICKED family now paints something at all "
    + "- this lit nothing before");
  check(s.some(p => p.color === CV_GHOST),
    "...and what it paints is the ghost treatment");
}
{
  const s = runLoop({ hotChain: 1 });
  check(s.some(p => p.color === CV_GHOST),
    "per-CHAIN hover reaches an unticked family too (the line list's row hover)");
}
{
  // SELECTING IS POINTING AT IT TOO (2026-08-31). A line of an unticked
  // family could be selected - the pill named it, the vertex handles were
  // drawn on it - and the line itself painted NOTHING, not even while its
  // own row was hovered: the halo was gated `hot && !sel` and the measured
  // stroke is skipped for every ghost, so a selected ghost fell between the
  // two.
  const s = runLoop({ selected: 1 });
  check(s.some(p => p.color === CV_GHOST),
    "SELECTING a line of an unticked family paints it - it used to paint "
    + "nothing at all, while the selection pill described it");
  const h = runLoop({ selected: 1, hotChain: 1 });
  check(h.some(p => p.color === CV_GHOST),
    "...and hovering the selected line paints it too - the case the old "
    + "`hot && !sel` gate excluded from BOTH passes");
  const del = runLoop({ selected: 1, excluded: new Set([1]) });
  check(!del.some(p => p.color === CV_GHOST),
    "a DELETED line stays deleted even when it is the selection");
}
{
  const s = runLoop({ hotStyle: new Set([13]), excluded: new Set([1]) });
  check(!s.some(p => p.color === CV_GHOST),
    "a DELETED line stays deleted - hover does not resurrect it");
}
{
  // isolate precedence, both directions, exactly as before
  const s = runLoop({ isolate: 13 });
  eq(s.length, 1, "isolate on an UNTICKED family paints it (isolate beats the tick)");
  eq(s[0].color, "#ff7700", "...as a real, counted line, not as a ghost");
  const t = runLoop({ isolate: 5, include: new Map([[13, true], [5, true]]) });
  eq(t.length, 1, "isolate hides every OTHER family, ticked ones included");
}
{
  const s = runLoop({ isolate: 5, hotStyle: new Set([13]) });
  check(s.some(p => p.color === CV_GHOST),
    "hovering a family that isolate has hidden ghosts it - same rule as the tick");
}

// ---------------------------------------------------------------------------
console.log("2. a ghost does not read as counted");
// ---------------------------------------------------------------------------
{
  const ghost = runLoop({ hotStyle: new Set([13]) }).filter(p => p.color === CV_GHOST);
  const onFam = runLoop({ hotStyle: new Set([5]) }).filter(p => p.color !== CV_GHOST);

  eq(ghost.length, 1,
    "a GHOST paints exactly ONE pass - the halo - and no measured line");
  eq(onFam.length, 2,
    "a hovered TICKED family paints TWO - the halo AND its measured line");
  check(onFam[1].width < onFam[0].width,
    "...the second being the crisp stroke inside the soft ribbon");
  check(ghost[0].dash.length === 2 && ghost[0].dash[0] > 0,
    "the ghost ribbon carries a coarse uniform dash - it reads as provisional");
  // THE GAP HAS TO SURVIVE THE CAP. A round cap extends half the ribbon's
  // width past each dash end, and the ribbon is ~6x the plotted weight, so
  // round caps closed every gap and the ghost rendered SOLID - which threw
  // away the discriminator. Caught on a 400% capture, pinned here.
  eq(ghost[0].cap, "butt", "the ghost ribbon uses butt caps, or the dash closes up");
  eq(onFam[0].cap, "round", "...while the ON-state ribbon keeps its round caps");
  check(onFam[0].dash.length === 0,
    "the ON-state ribbon stays solid, so the two ribbons differ too");
  // the structural tell must not depend on colour at all
  check(ghost.length !== onFam.length,
    "on/off differ in the NUMBER of strokes, not only in their colour - the "
    + "tell survives greyscale, a bad monitor and colour blindness");
}
{
  // ...and SELECTION must not buy a ghost a measured line either. The
  // selection's magenta stroke is what says "counted and picked"; on an
  // unticked family it would say the opposite of the pill.
  const all = runLoop({ selected: 1 });
  const sel = all.filter(p => p.color === CV_GHOST);
  eq(sel.length, 1, "a SELECTED ghost paints exactly one pass, the halo");
  eq(all.length, 2, "...one ghost plus the one ticked family, nothing more");
  check(!all.some(p => p.color === CV_SELECT),
    "no selection magenta anywhere - a line that is not in the takeoff must "
    + "never wear the picked colour");
  const on = runLoop({ selected: 2 });
  check(on.some(p => p.color === CV_SELECT),
    "a selected line of a TICKED family still wears the selection colour");
}
{
  // the ghost dash lives on the halo, never on a measured line, so it can
  // never be mistaken for a detected dash pattern (CLAUDE.md §5)
  const all = runLoop({ hotStyle: new Set([13]) });
  check(all.every(p => p.color !== CV_GHOST || p.width >= 6),
    "every dashed ghost stroke is a wide ribbon, never a hairline at "
    + "measured weight");
}

// ---------------------------------------------------------------------------
console.log("3. the ghost is neutral, not the family's colour");
// ---------------------------------------------------------------------------
{
  const g = runLoop({ hotStyle: new Set([13]) }).find(p => p.color === CV_GHOST);
  check(!!g, "the ghost uses CV_GHOST");
  check(g.color !== "#ff7700", "...and not the family's own colour");
  const hex = (CV_GHOST || "").replace("#", "");
  const r = parseInt(hex.slice(0, 2), 16), gg = parseInt(hex.slice(2, 4), 16),
        b = parseInt(hex.slice(4, 6), 16);
  const mx = Math.max(r, gg, b), mn = Math.min(r, gg, b);
  check(mx - mn < 40, "CV_GHOST is a desaturated slate, not a hue that could "
    + "be read as a family colour  (spread " + (mx - mn) + ")");
  check(mx < 200 && mx > 60,
    "...and mid-toned, so it is visible where CV_FAM_NEARWHITE families are not");
  check(CV_GHOST !== CV_SELECT, "and it cannot be confused with the selection colour");
  check(CSS.indexOf(CV_GHOST) >= 0,
    "the readout pill wears the same slate as the linework");
}

// ---------------------------------------------------------------------------
console.log("4. the words: 'NOT counted', and not on the shared status bar");
// ---------------------------------------------------------------------------
{
  const gb = (SRC.match(/\nfunction ghostBarSync\(\) \{[\s\S]*?\n\}/) || [""])[0];
  check(!!gb, "ghostBarSync exists");
  check(/NOT counted in the takeoff/.test(gb),
    "it says NOT counted, in plain English");
  check(gb.indexOf("setStatus(") < 0,
    "it does NOT write the shared status bar - that bar can be carrying the "
    + "earthwork read's busy line, and a hover must not wipe it");
  check(/id="ghostInfo"/.test(HTML), "#ghostInfo exists in the markup");
  check(/\.ghost-info \{/.test(CSS), ".ghost-info is styled");
  check(/if \(key === ghostBarKey\) return;/.test(gb),
    "it no-ops when nothing moved - draw() calls it on every pan frame");
  const hf = (SRC.match(/\nfunction hotFam\(sids\) \{[\s\S]*?\n\}/) || [""])[0];
  check(/ghostBarSync\(\)/.test(hf), "the hover writer refreshes it");
  const hfo = (SRC.match(/\nfunction hotFamOff\(\) \{[\s\S]*?\n\}/) || [""])[0];
  check(/ghostBarSync\(\)/.test(hfo), "and mouseleave clears it");
  check(/renderZoomFact\(\);\s*\n(\s*\/\/[^\n]*\n)*\s*ghostBarSync\(\);/.test(SRC),
    "draw() re-derives it, so ticking the box while hovering updates the words");
}

// ---------------------------------------------------------------------------
console.log("4b. the words cover EVERY path that can ghost a line");
// ---------------------------------------------------------------------------
// THE REGRESSION ecd939f SHIPPED. The chain loop above ghosts on
// `hotStyle.has(sid) || hotChain === ch.chain_id`, and ghostBarSync only
// looked at hotStyle. So the per-CHAIN hover - the per-family line list,
// the calculator rows, the callout rows, the earthwork rows - painted a
// line of an unticked family with NO "NOT counted" pill anywhere. Before
// ecd939f that path painted nothing at all, so the disclosure gap arrived
// with the feature, and it is exactly the mistake the feature exists to
// prevent: a ghost that reads as a counted line.
{
  const GBS = (function () {
    const a = SRC.indexOf("let ghostBarKey = \"\";");
    const b = SRC.indexOf("\n}", SRC.indexOf("function ghostBarSync() {", a));
    return SRC.slice(a, b + 2);
  })();
  check(!!GBS && /function ghostBarSync/.test(GBS),
    "ghostBarSync and its dedupe key are locatable together");

  function runBar(over) {
    const el = { hidden: true, textContent: "", children: [],
                 appendChild(c) { this.children.push(c); return c; } };
    const state = Object.assign({
      result: { chains: chains(),
                styles: [{ style_id: 13, suggested_label: "hatch fam" },
                         { style_id: 5, suggested_label: "solid fam" }] },
      labels: new Map(), excluded: new Set(), isolate: null,
      include: new Map([[13, false], [5, true]]),
      hotStyle: null, hotChain: null,
    }, over || {});
    const scope = {
      $: () => el,
      state,
      document: { createElement: () => ({ textContent: "" }),
                  createTextNode: (t) => ({ textContent: t }) },
      famShown: (sid) => state.isolate !== null ? sid === state.isolate
                                                : !!state.include.get(sid),
    };
    const names = Object.keys(scope);
    new Function(...names, GBS + "\nghostBarSync();")(...names.map(n => scope[n]));
    const words = el.textContent
      + el.children.map(c => c.textContent || "").join("");
    return { hidden: el.hidden, words };
  }

  {
    const r = runBar({});
    check(r.hidden, "nothing hovered: no pill");
  }
  {
    const r = runBar({ hotStyle: new Set([13]) });
    check(!r.hidden && /NOT counted in the takeoff/.test(r.words),
      "hovering the unticked FAMILY still says NOT counted (unchanged)");
  }
  {
    // chain 1 belongs to style 13, whose tick is OFF - this is the ghost the
    // per-chain hover paints, and it used to carry no words at all
    const r = runBar({ hotChain: 1 });
    check(!r.hidden,
      "hovering one LINE of an unticked family now shows the pill - "
      + "THE REGRESSION: it painted a ghost and said nothing");
    check(/NOT counted in the takeoff/.test(r.words),
      "...and it says NOT counted, the same sentence, not a second wording");
    check(/line #1/.test(r.words),
      "...and names the LINE, because one line is ghosted and not the whole "
      + "family - the pill must not claim more of the sheet is showing");
  }
  {
    const r = runBar({ hotChain: 2 });
    check(r.hidden,
      "hovering a line of a TICKED family says nothing - it is counted, and "
      + "there is nothing to disclose");
  }
  {
    const r = runBar({ hotChain: 1, excluded: new Set([1]) });
    check(r.hidden,
      "a DELETED line paints no ghost, so it gets no pill either "
      + "(draw() continues past it before the hover test)");
  }
  {
    // family + one of its own lines hovered at once: one entry, and the
    // family name alone, because the WHOLE family is ghosted
    const r = runBar({ hotStyle: new Set([13]), hotChain: 1 });
    check(!r.hidden && !/line #/.test(r.words),
      "family and line hovered together names the family, not one line");
  }
  {
    const r = runBar({ hotStyle: new Set([13]), hotChain: 2,
                       include: new Map([[13, false], [5, false]]) });
    check(/2 families/.test(r.words),
      "two unticked families reached by two different hover paths count as two");
  }
  {
    // isolate hides a ticked family: the same rule, through the chain path
    const r = runBar({ isolate: 5, hotChain: 1 });
    check(!r.hidden && /NOT counted/.test(r.words),
      "a line hidden by isolate discloses through the chain path too");
  }
  {
    // SELECTION is a ghosting path now, so it is a disclosing path too
    const r = runBar({ selected: 1 });
    check(!r.hidden && /NOT counted in the takeoff/.test(r.words),
      "SELECTING a line of an unticked family discloses as well");
    check(/line #1/.test(r.words), "...and names the line");
    const t = runBar({ selected: 2 });
    check(t.hidden, "selecting a line of a TICKED family says nothing");
    const d = runBar({ selected: 1, excluded: new Set([1]) });
    check(d.hidden, "a deleted selection paints no ghost and gets no pill");
  }
  {
    // hovered line and selected line are DIFFERENT lines of the same
    // unticked family: two ghosts, so naming one of them would be a lie
    const r = runBar({ selected: 1, hotChain: 3,
                       result: { chains: chains().concat(
                                   [{ chain_id: 3, style_id: 13,
                                      points: [[0, 9], [10, 9]] }]),
                                 styles: [{ style_id: 13,
                                            suggested_label: "hatch fam" },
                                          { style_id: 5,
                                            suggested_label: "solid fam" }] } });
    check(!r.hidden && !/line #/.test(r.words),
      "two different ghosted lines: the family name alone, no line number");
  }
}

// ---------------------------------------------------------------------------
console.log("5. one visibility test, one definition");
// ---------------------------------------------------------------------------
check(/function famShown\(sid\) \{[\s\S]{0,300}state\.isolate !== null \? sid === state\.isolate/
      .test(SRC),
  "famShown is the single answer to 'is this family painted as itself'");
check(/const shown = famShown\(sid\);/.test(SRC),
  "the chain loop asks famShown, so the canvas and the readout cannot drift");
check(!/if \(state\.isolate === null && !state\.include\.get\(sid\)\) continue;/.test(SRC),
  "the old two-line include/isolate guard is gone from the chain loop");
// the object-mode boxes are a COUNT and stay off for a ghost, on purpose
{
  const at = SRC.indexOf('state.measureMode.get(s2.style_id) !== "object"');
  const blk = SRC.slice(at, at + 700);
  check(/famShown\(s2\.style_id\)/.test(blk),
    "the object-mode box pass uses the same visibility test");
  check(/DELIBERATELY NOT ghosted|box IS a count|boxes/i.test(blk),
    "...and says on the record why a ghost gets no boxes");
}

// ---------------------------------------------------------------------------
console.log("6. the per-chain hover writers dedupe their repaints");
// ---------------------------------------------------------------------------
{
  const hcs = (SRC.match(/\nfunction hotChainSet\(cid\) \{[\s\S]*?\n\}/) || [""])[0];
  check(/if \(state\.hotChain === cid\) return;/.test(hcs),
    "hotChainSet skips the repaint when nothing moved, like hotFam");
  let draws = 0;
  const st = { hotChain: null };
  const fn = new Function("state", "draw", hcs + "\nreturn hotChainSet;")
    (st, () => { draws++; });
  fn(7); fn(7); fn(7);
  eq(draws, 1, "three mouseenters on the same row cause ONE redraw");
  fn(null);
  eq(draws, 2, "and mouseleave causes one more");
}
check(!/onmouseenter = \(\) => \{ state\.hotChain = [a-z.]+\.chain_id; draw\(\); \}/.test(SRC),
  "the list-row hovers go through hotChainSet, not a bare assign+draw");

if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
console.log("\nall hover-off-family checks passed");
