// THE SCALE VERDICT ON SCREEN, IN PLAIN WORDS (scale verdict S3, 2026-10-02).
//
// The server decides a page's scale state (takeoff_engine/scale_verdict.py; GET .../scale `verdict`); the review page
// says it. Behavioural: the SCALE-VERDICT-UI block of webui/app.js (svChipModel, svStatusWords, svBannerLine,
// svGroups) and the CHECK-QUEUE block (ckBuild), each evaluated standalone, one case per state, on verdicts shaped
// exactly as the server sends them (the live proof's JSON, out/verify/2026-10-02/scale-verdict/s1-s2/json). Source
// checks (stated as such) for the wiring: every page asks for the dimension check, every person's act goes through
// POST .../scale/confirm with its `how`, the Measure readout names itself and offers Confirm, the cache busters moved.
//
//     node tests/test_scale_verdict_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const R = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const js = R("webui", "app.js");
const html = R("webui", "index.html");
const css = R("webui", "style.css");

let fails = 0;
const check = (ok, msg, got) => {
  if (ok) console.log("ok: " + msg);
  else { fails++; console.error("FAIL: " + msg + (got === undefined ? "" : "  [" + JSON.stringify(got) + "]")); }
};
const block = (b, e) => {
  const a = js.indexOf(b), z = js.indexOf(e);
  if (a < 0 || z < a) throw new Error("block not found: " + b);
  return js.slice(a, z);
};
const SV = new Function(block("// SCALE-VERDICT-UI-BEGIN", "// SCALE-VERDICT-UI-END")
  + "\nreturn { svChipModel, svStatusWords, svBannerLine, svGroups, svAgree, svScale, SV_HELP, svPlainWords, svWaiting };")();
const CK = new Function(block("// CHECK-QUEUE-BEGIN", "// CHECK-QUEUE-END") + "\nreturn { ckBuild };")();

const W = (kind, value, note) => ({ kind, value, source: "", note: note || "" });
// shapes from the live proof
const V_CONFIRMED = { state: "CONFIRMED", value: 20, agree: [W("caption", 20, '1" = 20\''), W("bar", 20)], disagree: [],
                      choices: [], other_printed: [], offered: [], person: null, dimensions: "checked" };
const V_PENDING = { state: "UNCHECKED", would_be: "CONFIRMED", value: 4, agree: [W("caption", 4), W("bar", 3.965)],
                    disagree: [], choices: [], other_printed: [], offered: [], person: null, dimensions: "pending" };
const V_ONE = { state: "UNCHECKED", value: 20, agree: [W("bar", 20)], disagree: [], choices: [], other_printed: [],
                offered: [], person: null, dimensions: "checked" };
const V_ONE_MULTI = { state: "UNCHECKED", value: 20, agree: [W("caption", 20)], disagree: [], choices: [],
                      other_printed: [W("caption", 5)], offered: [], person: null, dimensions: "checked" };
const V_DISAGREE = { state: "CONFLICT", value: null, agree: [], person: null, dimensions: "checked",
                  disagree: [W("caption", 4, '1/4" - 1\'-0"'), W("bar", 3.964838529563876), W("dimensions", 7.93)],
                  choices: [W("caption", 4, '1/4" - 1\'-0"'), W("bar", 3.964838529563876), W("dimensions", 7.93)],
                  other_printed: [W("caption", 8)], offered: [] };
const V_NONE = { state: "NONE", value: null, agree: [], disagree: [], choices: [], other_printed: [], offered: [],
                 person: null, dimensions: "checked" };
const V_NONE_DIM = Object.assign({}, V_NONE, { offered: [W("dimensions", 3.692)] });
const V_PERSON = { state: "CONFIRMED", value: 8, agree: [W("dimensions", 7.93)],
                   disagree: [W("caption", 4), W("bar", 3.965)], choices: [], other_printed: [], offered: [],
                   person: { value: 8, how: "picked", who: null, when: "2026-10-02T12:41:21" }, dimensions: "checked" };
const SC = (v) => ({ ft_per_paper_inch: v, text: `1" = ${v}'`, source: "text" });

// ---- the chip, one case per state
let m = SV.svChipModel(V_CONFIRMED, SC(20));
check(m.look === "ok" && m.text === `1" = 20' ✓ caption and drawn bar agree` && !m.buttons.length,
      "confirmed by witnesses: green, the scale, and which kinds agree", m);
m = SV.svChipModel(V_ONE, SC(20));
check(m.look === "warn" && m.text === `1" = 20' · not confirmed (one reading: the drawn bar)`
      && m.buttons.map(b => b.label).join("|") === "Measure to check|Accept as read"
      && m.help === "Check it first: measure the scale bar or a length you know. If your measure agrees, confirm it there."
      && m.buttons[0].title === m.help,
      "one reading: amber, not confirmed, Measure to check FIRST then Accept as read; no Confirm (CRITIC-scale-2 M1)", m);
m = SV.svChipModel(V_ONE_MULTI, SC(20));
check(m.subs.length === 1 && m.subs[0].text === `the sheet also prints 1" = 5'`
      && m.buttons.some(b => b.act === "pick" && b.value === 5 && b.label === `Use 1" = 5' (caption)`),
      "one caption on a sheet printing two scales: the other printed scale is named, and offered (the old picker folded "
      + "in, MINOR 7)", [m.subs, m.buttons]);
m = SV.svChipModel(V_PENDING, SC(4));
check(m.look === "wait" && m.text === "Checking the scale's readings…" && !m.buttons.length && !/\d/.test(m.text),
      "pending (caption + bar agree, the dimensions not heard): grey 'checking', no value, no green (CRITIC-scale-2 B1)", m);
m = SV.svChipModel(null, SC(20));
check(m && m.look === "wait" && m.text === "Checking the scale's readings…",
      "before the server has answered: checking, not green", m);
check(SV.svChipModel(null, null).look === "wait", "nothing heard and nothing in force: checking too");
m = SV.svChipModel(V_PENDING, null, { slow: true });
check(m.text === "Still checking the printed dimensions" && m.buttons.map(b => b.act).join() === "retry",
      "after 15 s without an answer: 'still checking the printed dimensions' and a retry (CRITIC-scale-2 M2)", m);
check(SV.svChipModel({ state: null, why: "could not read" }, SC(20)) === null,
      "a verdict the server could not work out: the old chip, never an error");
m = SV.svChipModel(V_DISAGREE, null);
const labs = m.buttons.map(b => b.label);
check(m.look === "bad" && m.text === "Scale readings disagree"
      && labs[0] === `1" = 4' (caption, drawn bar)` && labs[1] === `1" = 7.93' (printed dimensions)`
      && labs[2] === "Measure something you know",
      "readings disagree: red, each reading a button (caption and bar agreeing are ONE choice), measure", labs);
check(m.buttons[0].act === "pick" && m.buttons[0].value === 4 && m.buttons[0].text === `1/4" - 1'-0"`
      && m.buttons[1].value === 7.93 && m.buttons[1].text === `1" = 7.93'`,
      "a choice records the caption's printed words, else the reading as measured (never snapped)", m.buttons);
m = SV.svChipModel(V_NONE, null);
check(m.look === "warn" && m.text === "No scale. Type it, or measure something you know."
      && m.buttons.map(b => b.label).join("|") === "Measure something you know", "none: amber, type or measure", m);
m = SV.svChipModel(V_NONE_DIM, null);
check(m.buttons[0].label === `Use 1" = 3.69' (printed dimensions)` && m.buttons[0].act === "pick"
      && m.text === "No scale. The printed dimensions suggest 1 in = 3.69 ft: choose it, type one, or measure."
      && SV.svStatusWords(V_NONE_DIM) === m.text && SV.svBannerLine(V_NONE_DIM).html === `<b>${m.text}</b>`,
      "none with the dimension-only offer: chip, status and banner say one sentence (MINOR 6); the reading a button",
      [m, SV.svStatusWords(V_NONE_DIM), SV.svBannerLine(V_NONE_DIM)]);
m = SV.svChipModel(V_PERSON, SC(8));
check(m.look === "ok" && m.text === `1" = 8' ✓ set by hand` && m.subs[0].text === "chosen on 2026-10-02"
      && m.subs[1].look === "warn" && m.subs[1].text === `the sheet reads 1" = 4' (caption, drawn bar)`
      && !m.buttons.length,
      "confirmed by a person: green, how in plain words and the date; what the sheet reads, in amber; no refusal", m.subs);
m = SV.svChipModel(Object.assign({}, V_PERSON, { person: { value: 20, how: "calibrated", who: "alice",
                                                             when: "2026-10-02T09:00:00" }, disagree: [] }), SC(20));
check(m.subs[0].text === "measured on the sheet on 2026-10-02 by alice" && m.subs.length === 1,
      "the name only when the server sends it; calibrated reads 'measured on the sheet'", m.subs);
for (const [how, word] of [["typed", "typed"], ["confirmed", "checked by measuring"]]) {
  m = SV.svChipModel(Object.assign({}, V_PERSON, { disagree: [], person: { value: 8, how, when: "" } }), SC(8));
  check(m.subs[0].text === word, `how ${how} reads '${word}'`, m.subs);
}
check(!/CONFIRMED|UNCHECKED|CONFLICT|NONE/.test(JSON.stringify([V_CONFIRMED, V_ONE, V_PENDING, V_DISAGREE, V_NONE, V_PERSON]
      .map(v => SV.svChipModel(v, SC(20))).map(x => [x.text, x.subs, x.buttons.map(b => b.label)]))),
      "the state names never reach the chip");

// ---- the status bar, the banner
check(SV.svStatusWords(V_CONFIRMED) === "confirmed: caption and drawn bar agree"
      && SV.svStatusWords(V_ONE) === "not confirmed" && SV.svStatusWords(V_PENDING) === "checking the scale's readings…"
      && SV.svStatusWords(V_DISAGREE) === "scale readings disagree - none in force until you choose one"
      && SV.svStatusWords(V_PERSON) === "set by hand" && SV.svStatusWords(V_NONE) === null,
      "status bar: one phrase per state; none keeps its own words");
let b = SV.svBannerLine(V_ONE);
check(b.level === "warn" && b.html.includes("Scale not confirmed") && b.html.includes("one reading (the drawn bar)")
      && b.html.includes("measure the scale bar or a length you know"), "banner, one reading: warn, one sentence", b);
b = SV.svBannerLine(V_DISAGREE);
check(b.level === "stop" && b.html.includes("Scale readings disagree")
      && b.html.includes(`1" = 4' (caption, drawn bar) or 1" = 7.93' (printed dimensions)`)
      && b.html.includes("No scale is in force until you choose one"), "banner, disagree: stop", b);
b = SV.svBannerLine(V_CONFIRMED);
check(b.level === "ok" && b.corr === true && b.html.includes("Scale confirmed: 1\" = 20'"),
      "banner, confirmed by two kinds: ok, the one line that may be called corroborated", b);
b = SV.svBannerLine(V_PERSON);
check(b.level === "warn" && b.html.includes("Scale set by hand") && b.html.includes("The sheet reads"),
      "banner, person whose value the sheet's readings dispute: warn, says what the sheet reads", b);
check(SV.svBannerLine(V_NONE) === null, "banner, none: the existing no-scale sentences say it");

// ---- CRITIC-scale-2: waiting, accepted, confirmed by measuring, a disagreeing measure, a failed check
const P = (how, extra) => Object.assign({}, V_ONE, { person: Object.assign({ value: 20, how, who: null,
                                                                              when: "2026-10-02T10:00:00" }, extra || {}) });
b = SV.svBannerLine(V_PENDING);
check(b.level === "warn" && !b.corr && /Checking the scale's readings/.test(b.html) && !/confirmed|matches|corroborat/i.test(b.html),
      "banner while the verdict waits: no 'confirmed', 'matches' or 'corroborated', not green", b);
check(SV.svWaiting(V_PENDING) && SV.svWaiting(null) && !SV.svWaiting(V_ONE)
      && !SV.svWaiting(Object.assign({}, V_PENDING, { person: { value: 4, how: "picked" } }))
      && !SV.svWaiting(Object.assign({}, V_ONE, { dimensions: "failed" })),
      "waiting = nothing heard or the dimensions pending, never with a person's record or after a failed check");
m = SV.svChipModel(P("accepted"), SC(20));
check(m.look === "accepted" && m.text === `1" = 20' · accepted without measuring (one reading: the drawn bar)`
      && m.buttons.map(b => b.label).join("|") === "Measure to check" && m.subs[0].text === "accepted on 2026-10-02",
      "accepted: amber outline (not green), 'accepted without measuring', Measure to check still offered", m);
check(SV.svStatusWords(P("accepted")) === "accepted without measuring"
      && SV.svBannerLine(P("accepted")).level === "warn" && !SV.svBannerLine(P("accepted")).corr
      && /accepted without measuring/.test(SV.svBannerLine(P("accepted")).html),
      "accepted: status and banner say the same, amber, never corroborated");
check(SV.svPlainWords(P("accepted"), SC(20)) === "1 in = 20 ft, accepted by the reviewer without measuring on 2026-10-02",
      "accepted: the takeoff CSV line", SV.svPlainWords(P("accepted"), SC(20)));
m = SV.svChipModel(P("accepted", { measure_disagreed: { ft: 30, at: "2026-10-02T10:05:00" } }), SC(20));
check(m.look === "accepted" && m.text === `1" = 20' · accepted; a measure on 2026-10-02 said 1 in = 30 ft`,
      "Keep 20 after a measure said 30: the chip itself says 'accepted; a measure on <date> said 1 in = 30 ft'", m);
// CRITIC-scale-3 M1: the person's own disagreeing measure outweighs two kinds that agree - never green
const V_TWO = { state: "UNCHECKED", value: 20, agree: [W("caption", 20), W("bar", 20)], disagree: [], choices: [],
                other_printed: [], offered: [], dimensions: "checked",
                person: { value: 20, how: "accepted", who: null, when: "2026-10-02T10:00:00",
                          measure: { ft: 30, at: "2026-10-02T10:05:00" }, measure_agrees: false } };
m = SV.svChipModel(V_TWO, SC(20));
b = SV.svBannerLine(V_TWO);
check(m.look === "accepted" && /a measure on 2026-10-02 said 1 in = 30 ft/.test(m.text) && b.level === "warn" && !b.corr
      && /Scale accepted: 1" = 20'; a measure on 2026-10-02 said 1" = 30'/.test(b.html)
      && SV.svStatusWords(V_TWO) === "accepted; a measure said 1 in = 30 ft"
      && SV.svPlainWords(V_TWO, SC(20)) === "1 in = 20 ft, accepted by the reviewer on 2026-10-02; a measure on 2026-10-02 said 1 in = 30 ft",
      "M1 (critic 3): caption and bar agree, the person's measure disagrees: amber chip, warn banner, never corroborated, "
      + "the measure named on every surface", [m.text, b, SV.svStatusWords(V_TWO), SV.svPlainWords(V_TWO, SC(20))]);
const V_TWO_OK = Object.assign({}, V_TWO, { state: "CONFIRMED", person: Object.assign({}, V_TWO.person,
  { measure: { ft: 20.2, at: "2026-10-02" }, measure_agrees: true }) });
m = SV.svChipModel(V_TWO_OK, SC(20));
check(m.look === "ok" && /checked by measuring/.test(m.text), "M1 (critic 3): accepted with a measure that agrees: green, checked", m);
m = SV.svChipModel(P("confirmed", { measured: 20.1 }), SC(20));
check(m.look === "ok" && m.subs[0].text === "checked by measuring on 2026-10-02" && !m.buttons.length,
      "confirmed (a measure agreed): green, 'checked by measuring on <date>'", m);
b = SV.svBannerLine(P("confirmed"));
check(b.level === "ok" && b.corr === false && /checked by measuring/.test(b.html),
      "confirmed by measuring: green banner line, but not 'corroborated' (a person's word, not two readings)", b);
m = SV.svChipModel(V_ONE, SC(20), { measure: { ft: 30, at: "2026-10-02" } });
check(m.subs.some(x => x.text === "Your measure says 1 in = 30 ft; the sheet reads 20.")
      && m.buttons[0].label === "Use 30 (measured)" && m.buttons[1].label === "Keep 20",
      "a measure this session that disagrees: the inline line, Use 30 (measured) and Keep 20", m);
m = SV.svChipModel(V_CONFIRMED, SC(20), { measure: { ft: 30, at: "2026-10-02" } });
check(m.look === "warn" && /your measure does not/.test(m.text) && m.buttons.map(b => b.label).join("|") === "Use 30 (measured)|Keep 20",
      "M1 (critic 3): caption and bar agree, a measure this session says 30: not green, Use / Keep offered", m);
m = SV.svChipModel(V_ONE, SC(20), { measure: { ft: 20.2 } });
check(!m.subs.length && m.buttons[0].label === "Measure to check", "a measure that agrees adds no disagreement line", m);
const V_FAIL = Object.assign({}, V_PENDING, { dimensions: "failed", would_be: "CONFIRMED" });
m = SV.svChipModel(V_FAIL, SC(4));
check(m.look === "warn" && /not confirmed/.test(m.text) && m.subs.some(x => x.text === "the printed dimensions could not be checked")
      && m.buttons.some(b => b.act === "retry"),
      "a failed dimension check: not confirmed, 'the printed dimensions could not be checked', a retry (M2)", m);
check(!/✓/.test(m.text) && SV.svStatusWords(V_FAIL) === "not confirmed: the printed dimensions could not be checked",
      "a failed check never reads as confirmed, though caption and bar agree", SV.svStatusWords(V_FAIL));
check(![V_ONE, V_PENDING, V_FAIL, P("accepted")].some(v =>
        (SV.svChipModel(v, SC(20)).buttons || []).some(b => b.label === "Confirm")),
      "no chip button is labelled Confirm (a Confirm is offered only where a measure agrees, in the readout)");

// ---- the check queue
const q = (scale) => CK.ckBuild({ totalPt: 1000, styles: [], scale }).entries.filter(e => e.id === "scale");
let e = q({ present: true, text: "x", ftIn: 20, source: "text", operator: false, verdict: "UNCHECKED", kinds: "caption" });
check(e.length === 1 && e[0].sev === "warn" && e[0].head === "Scale not confirmed."
      && e[0].text.includes("(the caption)"), "queue: a not-confirmed scale is one warn entry", e);
e = q({ present: false, nsug: 0, verdict: "CONFLICT", choices: [`1" = 4' (caption, drawn bar)`] });
check(e.length === 1 && e[0].sev === "stop" && e[0].head === "Scale readings disagree."
      && e[0].text.includes(`1" = 4' (caption, drawn bar)`), "queue: readings that disagree are a STOP", e);
e = q({ present: false, nsug: 0, verdict: "NONE" });
check(e.length === 1 && e[0].sev === "stop" && e[0].head === "The engine did not read a scale on this sheet.",
      "queue: no scale stays the existing STOP", e);
e = q({ present: true, text: "x", ftIn: 20, source: "text", operator: false, verdict: "CONFIRMED", corroborated: true });
check(e.length === 0, "queue: a confirmed scale queues nothing", e);
e = q({ present: true, text: "x", ftIn: 8, source: "text", operator: false, verdict: "CONFIRMED", person: true });
check(e.length === 0, "queue: a person's scale queues nothing", e);

// ---- the wiring (source checks)
const fnSrc = (name) => {
  const a = js.search(new RegExp(`(?:async )?function ${name}\\(`));
  const z = js.indexOf("\n}\n", a);
  return a < 0 ? "" : js.slice(a, z + 3);
};
check(/if \(typeof svTake === "function"\) svTake\(j, gen, job, page\);/.test(fnSrc("scaleRestore")),
      "scaleRestore hands the verdict to svTake (guarded, so an older harness still runs it)");
const take = fnSrc("svTake"), dims = fnSrc("svDimsLoad");
check(/svDimsLoad\(gen, job, page\)/.test(take) && !/scale/.test(take.split("svDimsLoad")[0].replace(/_verdict|j\.verdict/g, ""))
      && /\/scale\/dimensions/.test(dims) && !/ft_per_paper_inch/.test(dims),
      "every page asks for the dimension check, with or without a scale in force (rule 2)");
const rec = fnSrc("svRecord");
check(/\/scale\/confirm/.test(rec) && /scale_seen: state\.result\._scaleSeen/.test(rec) && /how: how/.test(rec)
      && /approveScaleRecorded\(/.test(rec), "svRecord posts how, value and the stamp, and takes the new stamp");
check(/svRecord\("confirmed", ft, sc\.text \|\| null, measure\)/.test(fnSrc("svConfirm"))
      && /svRecord\("accepted", ft/.test(fnSrc("svAccept")) && /svRecord\("picked", value/.test(fnSrc("svPick"))
      && /measure: measure && Array\.isArray\(measure\.points\)/.test(rec) && !/measured_ft_per_paper_inch/.test(rec),
      "Confirm sends confirmed WITH the measure; Accept as read sends accepted; a choice sends picked");
check(/if \(typeof svHold === "function"\) svHold\(\);\s*\/\/ \.\.\.and the rows/.test(js)
      && /r\._heldScale = r\.scale; r\.scale = null;/.test(fnSrc("svHold")),
      "the engine's scale is held off screen before the first paint until the verdict is heard (CRITIC-scale-2 B1)");
check(/state\.result\._verdictWaiting\) \{\s*setStatus\("Approve waits for the scale/.test(fnSrc("approveRun")),
      "Approve waits while the verdict is pending");
check(/measure:\s+\[SNAP_PX, SNAP_VERTEX_PX\]/.test(js) && /const mh = snapToChain\(px, py, null\);/.test(js),
      "the ruler snaps to drawn geometry within the usual radius (MINOR 8)");
check(/scalePersonSet\("typed", p\.ft, p\.text\)/.test(js) && /scalePersonSet\("calibrated", ft/.test(js)
      && (js.match(/svRecord\("picked", \+c\.ft_per_paper_inch, c\.text\)/g) || []).length === 2,
      "the typed box (typed), the Measure tool (calibrated), the picker and the suggestions (picked) record first");
const mi = fnSrc("updateMeasureInfo");
check(/set the scale by a length you know/.test(mi) && /cal-confirm/.test(mi) && /svAgree\(meas, inF\)/.test(mi)
      && /svConfirm\(\{ ft: trueFt \/ \(pt \/ 72\), true_ft: trueFt, points: state\.measurePts/.test(mi)
      && /in on paper/.test(mi) && /svNoteMeasure\(/.test(mi),
      "the Measure readout names itself, shows the paper distance, offers Confirm (with the measure) only when it agrees");
check(/r\._conflictScale = sc;\s*r\.scale = null;/.test(fnSrc("svApply")),
      "readings that disagree put no scale in force (the no-scale path on every surface)");
check(/Scale not confirmed on this page/.test(fnSrc("renderItemized"))
      && (fnSrc("renderItemized").match(/Scale not confirmed on this page/g) || []).length === 1,
      "the takeoff panel says it once, at the top");
// (the redesign's frame takes them past the fork's own: at least 312 / 122)
const _bv = (re) => +((html.match(re) || [])[1] || 0);
check(_bv(/app\.js\?v=(\d+)/) >= 312 && _bv(/style\.css\?v=(\d+)/) >= 122,
      "cache busters at or past the fork's (312 / 122: what the merge critic found)", [_bv(/app\.js\?v=(\d+)/), _bv(/style\.css\?v=(\d+)/)]);
// every page that loads the client marker loads it at the marker's own version (3), so no cached 2 can linger
for (const f of [["webui", "index.html"], ["webui", "login.html"], ["webui", "change-password.html"], ["webui", "cutfill", "index.html"]])
  check(/\/client\.js\?v=3"/.test(R(...f)) && !/\/client\.js\?v=2"/.test(R(...f)), `${f.join("/")} loads /client.js?v=3`);
// a measure the person already kept beside the record is not offered again when the readout's input blurs
{
  const src = fnSrc("svNoteMeasure");
  const st = { result: { scale: { ft_per_paper_inch: 20 }, _svMeasure: null,
    _verdict: { person: { value: 20, how: "accepted", measure: { ft: 30, at: "2026-10-02" }, measure_agrees: false } } } };
  let painted = 0;
  const run = new Function("state", "svAgree", "renderSheetHead", src + "\nreturn svNoteMeasure;")(st, SV.svAgree, () => { painted++; });
  run(30, "x", [[0, 0], [72, 0]], 30);
  check(st.result._svMeasure === null, "the kept measure (30) on blur: no Use / Keep line again", st.result._svMeasure);
  run(40, "x", [[0, 0], [72, 0]], 40);
  check(st.result._svMeasure && st.result._svMeasure.ft === 40, "a NEW disagreeing measure (40) is still offered", st.result._svMeasure);
}
check(/\.sh-chip\.accepted \{[^}]*border: 1px solid var\(--t-warn\)/.test(css) && /\.sh-chip\.wait \{/.test(css),
      "accepted is an amber outline; waiting is grey");
check(/\.sh-sv-btns \{[^}]*flex-wrap: wrap/.test(css) && /\.sh-sv-btns button \{[^}]*max-width: 100%/.test(css),
      "the chip's buttons wrap and never run off a narrow screen");
check(/\.sh-chip\.amber \{[^}]*--t-warn/.test(css), "amber is the warn colour, not the stop colour");

console.log(fails ? `\n${fails} FAILED` : "\nall passed");
process.exit(fails ? 1 : 0);
