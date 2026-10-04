// THE REDESIGN'S FRAME AFTER THE MERGE OF 2026-10-04: three features built on the fork's classic screen come into the
// round-3 frame (shell.js / shell.css / responsive.css / team.js), and each needs hooks the fork could not build:
//   A. the takeoff starts empty   - Bid readiness says what the takeoff holds; an earlier version's approval is said as
//                                   information; Withdraw / Put back are reachable where Approve is pressed
//   B. bids by e-mail             - the "From e-mail" list has a place in the Projects grid; the ten activity actions
//                                   read as words; the list's link-buttons are not boxed by the frame
//   C. wall panels                - the Areas module owns the panel, which opens BEFORE Detect; Flooring never opens
//                                   over it; on-sheet walls are blue / amber / red, never the accent
// The pure blocks are evaluated on their own; the wiring and the layout rules are pinned as text (they were walked in
// Chrome at 1440 and 390, dark and paper: core-cutfill out/verify/2026-10-04/merge-redesign/VERIFY.md).
//
// Dependency-free:  node tests/test_redesign_three_features.js
"use strict";
const fs = require("fs");
const path = require("path");
const R = (...p) => fs.readFileSync(path.join(__dirname, "..", ...p), "utf8");
const W = (f) => R("webui", f);
const shell = W("shell.js"), shellCss = W("shell.css"), rspCss = W("responsive.css"), teamJs = W("team.js");
const index = W("index.html"), appJs = W("app.js"), wpJs = W("wallpanels.js"), wpCss = W("wallpanels.css");
const server = R("server.py"), inbound = R("takeoff_engine", "inbound.py");

let failures = 0;
function check(cond, msg, got) {
  if (!cond) { failures++; console.error("FAIL:", msg, got === undefined ? "" : JSON.stringify(got)); } else console.log("ok:", msg);
}
function block(src, a, b) {
  const i = src.indexOf(a), j = src.indexOf(b, i + a.length);
  if (i < 0 || j < i) throw new Error("markers " + a + " / " + b + " not found");
  return src.slice(i, j);
}
const fnSrc = (src, name) => {
  const m = new RegExp("\\n  function " + name + "\\([\\s\\S]*?\\n  \\}\\n").exec(src);
  return m ? m[0] : "";
};
// a group of checks that leans on something a frame without the hooks does not have: on such a frame the group counts
// as one failure with its reason, and the rest of the file still runs (so "fails before" is a count, not a crash)
function group(name, fn) {
  try { fn(); } catch (e) { failures++; console.error("FAIL:", name, "- could not run:", String(e && e.message || e).split("\n")[0]); }
}

// ================================================================ A. THE TAKEOFF STARTS EMPTY
const P = new Function(block(shell, "// SHELL-PURE-BEGIN", "// SHELL-PURE-END")
  + " return { approvalNotes, noteReadiness, linkPlan, earlierNotes: typeof earlierNotes === 'function' ? earlierNotes : null };")();
const HEAD = "Approved on an earlier version: its numbers are still in the bid.";
const DETAIL = "That approval holds 41 rows by length (1,204 lines, 44,793.4 ft) and 1 row counted or calculated. It stays in "
  + "the bid sheet and the report as it is until this page is approved again or its approval is withdrawn.";
const info = {
  pages: [0, 2], outdated: { "2": "OUT OF DATE: page 3 ..." }, notes: {},
  earlier: { "0": { head: HEAD, detail: DETAIL, holds: "41 rows by length (1,204 lines, 44,793.4 ft)", items: 42, lines: 1204, ft: 44793.4 } },
  kept: { "2": { at: "2026-10-04T05:00:00", how: "replaced", holds: "3 rows by length (12 lines, 310.0 ft)", items: 3, lines: 12, ft: 310 },
          "5": { at: "2026-10-04T05:01:00", how: "withdrawn", holds: "1 row counted or calculated", items: 1, lines: 0, ft: null } },
};
// A1: an earlier version's approval is never a warning
check(JSON.stringify(P.approvalNotes(info, null)) === JSON.stringify(["OUT OF DATE: page 3 ..."]),
      "A1: approvalNotes (the red notes) never carries `earlier` or `kept`: such a page is not out of date");
check(P.approvalNotes(info, 0).length === 0, "A1: ...nor for the page itself");
// A2: ...it is information, by page, with what it holds folded behind the line
check(typeof P.earlierNotes === "function", "A2: the frame reads `earlier` and `kept` from GET /approved (earlierNotes, in the pure block)");
group("A2: the earlier version's lines", () => {
  const job = P.earlierNotes(info, null);
  check(job.length === 3 && job[0].kind === "earlier" && job[0].page === 0 && job[0].text === "Page 1: " + HEAD
        && job[0].detail === DETAIL && job[0].value === "earlier version",
        "A2: the job's lines: the page approved on an earlier version, in the server's own words, named by its page", job[0]);
  check(job[1].kind === "kept" && job[1].page === 2
        && job[1].text === "Page 3: The approval made on an earlier version was replaced by an approve here. It is kept and can be put back."
        && job[1].detail === "It holds 3 rows by length (12 lines, 310.0 ft). Open the sheet and press Put back earlier approval, beside Approve.",
        "A2: an earlier approval replaced here: kept, and how to put it back", job[1]);
  check(job[2].page === 5 && /was withdrawn here\. It is kept and can be put back\.$/.test(job[2].text) && job[2].value === "earlier one kept",
        "A2: an earlier approval withdrawn here (the page is no longer approved) is said too", job[2]);
  const p0 = P.earlierNotes(info, 0);
  check(p0.length === 1 && p0[0].text === HEAD, "A2: a page's own line carries no page prefix");
  check(P.earlierNotes(info, 1).length === 0 && P.earlierNotes(null, 0).length === 0 && P.earlierNotes({ pages: [0] }, null).length === 0,
        "A2: no such approval, no answer, an older server (no `earlier` / `kept`): nothing said");
  check(P.earlierNotes({ kept: { "3": { how: "put back", holds: null } } }, 3)[0].text
        === "The approval made on an earlier version gave its place when another was put back. It is kept and can be put back."
        && P.earlierNotes({ kept: { "3": { how: "something new" } } }, 3)[0].text
        === "The approval made on an earlier version left the bid here. It is kept and can be put back.",
        "A2: every way the server says it left the bid has words; an unknown one still reads as a sentence");
  check(P.earlierNotes({ kept: { "3": { how: "withdrawn" } } }, 3)[0].detail === "Open the sheet and press Put back earlier approval, beside Approve.",
        "A2: with nothing said about what it holds, the detail is still how to put it back");
  const ens = fnSrc(shell, "earlierNotes");
  check(!!ens && !/"missing"|OUT OF DATE/.test(ens), "A2: nothing in it is ever 'missing' or OUT OF DATE");
});

// A3: Bid readiness: the takeoff row (app.js tkReadiness), the work area's words, the earlier approval as "noted"
const rd = fnSrc(shell, "readiness");
check(/const tkr = typeof tkReadiness === "function" \? tkReadiness\(\) : null;\s*if \(tkr\) add\(tkr\.label, tkr\.state, tkr\.value, tkr\.why\);/.test(rd),
      "A3: Bid readiness carries app.js's takeoff row (empty = missing, in app.js's own words)");
check(rd.indexOf("tkReadiness()") > rd.indexOf('add("Work area"') && rd.indexOf("tkReadiness()") < rd.indexOf("flJobFor"),
      "A3: ...right after the Work area row");
check(/wz \? "only length inside your boxes counts" : "every line you added to the takeoff counts"/.test(rd)
      && !/every line on the sheet counts/.test(shell),
      "A3: with no box drawn it is every line the person ADDED that counts (the old words were false)");
check(/for \(const e of earlierNotes\(approvedInfo\(\), s\.page\)\) add\("Approval", "info", e\.value, e\.text\);/.test(rd),
      "A3: an earlier version's approval of this page is a 'noted' row, never missing");
{
  const m = /\nfunction tkReadiness\(\) \{[\s\S]*?\n\}\n/.exec(appJs);
  check(!!m, "A3: app.js still has tkReadiness (the fork's TICKS block)");
  if (m) {
    const tk = (rows) => new Function("state", "computeTakeoffRows", "TK_EMPTY_APPROVE", m[0] + " return tkReadiness();");
    const empty = tk()({ result: {} }, () => ({ groups: [] }), "Nothing is in the takeoff. Tick the styles you want first.");
    const two = tk()({ result: {} }, () => ({ groups: [{ items: [1] }, { items: [2] }] }), "x");
    check(empty.label === "Takeoff" && empty.state === "missing" && empty.value === "empty" && /Tick the styles you want first/.test(empty.why),
          "A3: an empty takeoff reads Takeoff / empty / missing", empty);
    check(two.state === "supplied" && two.value === "2 items", "A3: two rows read Takeoff / 2 items / supplied", two);
    check(tk()({ result: null }, () => null, "x") === null, "A3: no detection, no row");
  }
}

// A4: the lines show with the numbers they explain: above the Summary's table and under the Plans tiles
const rs = block(shell, "async function renderSummary()", "/* ----------------------------------------------------------------- 3D */");
check(/const earlyEl = earlierNotesEl\(earlierNotes\(approvedInfo\(\), smScope === "approved" \? null : s\.page\)\);\s*if \(earlyEl\) body\.append\(earlyEl\);/.test(rs),
      "A4: Summary says them above its table: the job's (All approved sheets) or this page's (This sheet)");
const rt = fnSrc(shell, "renderPlanTiles") || block(shell, "async function renderPlanTiles(pages)", "function renderPlanCards()");
check((rt.match(/earlierNotesEl\(earlierNotes\(approvedInfo\(\), null\)\)/g) || []).length === 2,
      "A4: the Plans tiles say them too - also when nothing is approved (an earlier approval withdrawn here and kept)");
const en = fnSrc(shell, "earlierNotesEl");
check(/h\("details", \{ class: "rd-verdict info" \}, h\("summary", null, e\.text\), h\("div", \{ class: "sm-earlier-d" \}, e\.detail\)\)/.test(en)
      && !/missing/.test(en),
      "A4: the line is a <summary>, what the approval holds opens under it (folded, never deleted), never the red note");
check(/html\.shell-on \.rd-verdict\.info \{ border-color: color-mix\(in srgb, var\(--t-info\) 45%, transparent\);/.test(shellCss)
      && !/\.rd-verdict\.info \{[^}]*--t-(accent|stop)/.test(shellCss),
      "A4: in the info colour: not the stop red, not the accent");

// A5: Withdraw / Put back and the two notes travel with the bars the frame moves into the right rail's foot
{
  const bar = index.slice(index.indexOf('<div class="approve-bar">'), index.indexOf('<div class="links-bar">'));
  const links = index.slice(index.indexOf('<div class="links-bar">'), index.indexOf("</div>", index.indexOf('id="putBackBtn"')));
  check(/id="tkEarlierSlot"/.test(bar) && /id="tkUnsaved"/.test(bar) && bar.indexOf('id="tkEarlierSlot"') < bar.indexOf('id="tallyBar"')
        && bar.indexOf('id="tallyBar"') < bar.indexOf('id="approveBtn"'),
        "A5: the earlier-version line and the unsaved note are in the approve bar, above the tally and Approve");
  check(/id="withdrawBtn"/.test(links) && /id="putBackBtn"/.test(links) && /id="bidsheetLink"/.test(links),
        "A5: Withdraw approval and Put back earlier approval sit in the links bar, beside the downloads");
  check(/const approveBar = sidebar\.querySelector\("\.approve-bar"\);/.test(shell) && /if \(approveBar\) railFoot\.append\(approveBar\);/.test(shell)
        && /if \(linksBar\) railFoot\.append\(linksBar\);/.test(shell),
        "A5: shell.js moves both bars whole (never cloned) into the rail's foot, so app.js's own handlers and `hidden` stand");
}
check(/html\.shell-on #railFoot \.links-bar:not\(:has\(> :is\(a, button\):not\(\[hidden\]\)\)\) \{ display: none; \}/.test(shellCss)
      && !/\.links-bar:not\(:has\(> a:not\(\[hidden\]\)\)\)/.test(shellCss),
      "A5: the links bar shows for a BUTTON too: Put back stands with no link shown (was hidden with the bar: unreachable)");
check(/html\.shell-on #railFoot \.approve-bar :is\(\.tk-earlier-slot, \.tk-unsaved\) \{ max-width: 100% !important; max-height: 30vh;\s*overflow-y: auto; overflow-wrap: anywhere; \}/.test(shellCss),
      "A5: the two notes take the rail's width and scroll inside themselves: Approve is never pushed out of the foot");
// A6: ONE caller of approve - app.js approveRun, whole, with the takeoff's version and the one question
for (const [f, src] of [["shell.js", shell], ["team.js", teamJs], ["dock.js", W("dock.js")], ["responsive.js", W("responsive.js")]])
  check(!/\/approve[`"']/.test(src) && !/\/ticks[`"']|\/withdraw[`"']|\/putback[`"']/.test(src),
        `A6: ${f} posts no approve, tick, withdraw or put back of its own (app.js is the only caller)`);
{
  const ar = (/\nasync function approveRun\(\) \{[\s\S]*?\n\}\n/.exec(appJs) || [""])[0];
  check(/ticks_version/.test(ar) && /replace_earlier/.test(ar) && /tkFlush\(\)/.test(ar),
        "A6: app.js approveRun is the fork's: it sends every tick first, names the takeoff's version, and asks before replacing");
  check(/\/\/ TICKS-BEGIN/.test(appJs) && /\/\/ TICKS-END/.test(appJs) && /\nfunction scopeApply\(\) \{/.test(appJs)
        && /function tkEarlierShow\(el\)/.test(appJs) && /function tkUnsavedShow\(approve\)/.test(appJs) && /async function pbClick\(\)/.test(appJs),
        "A6: the fork's TICKS block, scopeApply, the two notes and Put back are all there");
}
// A7: the activity log says a withdrawal and a put back (server side: tests/test_team_roles.py 9b)
check(/_audit\(request, "sheet_withdraw", job=job_id, page=page, detail=_audit_n\(len\(items\), "item"\)\)/.test(server)
      && /_audit\(request, "sheet_putback", job=job_id, page=page, detail=\(_ea or \{\}\)\.get\("holds"\)\)/.test(server),
      "A7: the server logs an approval withdrawn and an earlier approval put back");
check(/sheet_withdraw: "Approval withdrawn", sheet_putback: "Earlier approval put back",/.test(shell)
      && /sheet_withdraw: \(e\) => \["withdrew the approval of", e\.name,/.test(teamJs)
      && /sheet_putback: \(e\) => \["put the earlier approval back on", e\.name,/.test(teamJs),
      "A7: ...and both read as words in the project's Activity card and in the team feed");

// ================================================================ B. BIDS BY E-MAIL
// B1: the fork's nodes are in the page, hidden until app.js shows them (the feature is OFF unless the server is set up)
{
  const pv = index.slice(index.indexOf('id="projectsView"'), index.indexOf('id="settingsView"'));
  check(/<section id="pvInbox" class="pv-inbox" aria-label="From e-mail" hidden><\/section>/.test(pv)
        && pv.indexOf('id="pvReach"') < pv.indexOf('id="pvInbox"') && pv.indexOf('id="pvInbox"') < pv.indexOf('id="pvSelBar"')
        && pv.indexOf('id="pvSelBar"') < pv.indexOf('id="pvGrid"'),
        "B1: #pvInbox sits between the reach line and the cards, hidden");
  check(/<a href="#bidaddress" id="svNavInbound" hidden>Bid address<\/a>/.test(index)
        && /<h2 class="sv-grouptitle" id="svGroupInbound" hidden>Bid address<\/h2>/.test(index)
        && /<section class="sv-section" id="svInboundSection" hidden>/.test(index)
        && /<section class="sv-section" id="svInbRulesSection" hidden>/.test(index),
        "B1: Settings has the Bid address link, group and its two sections, all hidden");
}
// B2: the Projects page is a named-area grid in the frame: the list has a row of its own, at every width
check(/grid-template-areas: "head head" "tabs controls" "filters reach" "filters inbox" "filters selbar" "filters grid" "filters empty";/.test(shellCss)
      && /html\.shell-on \.projects-view > \.pv-inbox \{ grid-area: inbox; max-width: none;/.test(shellCss),
      "B2: desk: the list has the grid's 'inbox' row, in the cards' column (unplaced, it fell into whichever cell was free)");
check(/grid-template-areas: "head" "tabs" "filters" "controls" "reach" "inbox" "selbar" "grid" "empty";/.test(rspCss),
      "B2: phone: the same row in the one column");
{
  // every child of #projectsView has a place: a child with no grid-area would be auto-placed
  const pvKids = [...index.slice(index.indexOf('id="projectsView"'), index.indexOf('id="settingsView"'))
    .matchAll(/\n    <(?:div|section)[^>]*class="(pv-[a-z]+)[ "]/g)].map((m) => m[1]);
  const placed = pvKids.filter((c) => new RegExp("\\.projects-view > \\." + c + " \\{ grid-area: ").test(shellCss));
  check(pvKids.length >= 7 && placed.length === pvKids.length, "B2: every child of the Projects page has a grid area in the frame",
        pvKids.filter((c) => !placed.includes(c)));
}
check(/html\.shell-on \.inb-open, html\.shell-on \.inb-page \{ padding: 0; border: 0; border-radius: 0; background: none;\s*color: var\(--t-accent-hi\); text-decoration: underline;/.test(shellCss),
      "B2: a file's name and a keyword's page read as links (the frame's button box is taken off them)");
check(/html\.shell-on \.inb-ok \.inb-what \{ color: var\(--t-ok\); \}/.test(shellCss)
      && /html\.shell-on \.inb-held \.inb-what, html\.shell-on \.inb-note, html\.shell-on \.inb-notext \{ color: var\(--t-warn\); \}/.test(shellCss),
      "B2: opened = the supplied teal (never the accent), held = amber");
check(/html\.shell-on \.inb-note, html\.shell-on \.inb-files, html\.shell-on \.inb-entry > \.inb-anyway \{ margin-left: 0; \}/.test(rspCss)
      && /html\.shell-on #svInbAddr \{ flex: 1 1 100%; max-width: none; \}/.test(rspCss)
      && /html\.shell-on #svInbAddr \{ flex: 1 1 610px; min-width: 0; max-width: 100%;/.test(shellCss)
      && /html\.shell-on #svInboundSection \.sv-addrow \{ flex-wrap: wrap; \}/.test(shellCss),
      "B2: the address shows whole on a desk and never makes its card wider; on a phone it takes a line and the list gives its 84px indent back");
// B3: every activity action the server writes for e-mail has words in the team feed
group("B3: the team feed's words for bids by e-mail", () => {
  const actions = new Set();
  for (const src of [server, inbound])
    for (const m of src.matchAll(/\baudit\(\s*"(inbound_[a-z_]+)"(?:\s+if[^,]*else\s+"(inbound_[a-z_]+)")?/g)) {
      actions.add(m[1]); if (m[2]) actions.add(m[2]);
    }
  check(actions.size === 10, "B3: the server writes ten inbound_* activity actions", [...actions].sort());
  const PH = new Function("ROLES", block(teamJs, "const PHRASE = {", "// -> [verb, what, extra, who]") + " return { PHRASE, MAILBOX };")({});
  const missing = [...actions].filter((a) => typeof PH.PHRASE[a] !== "function");
  check(missing.length === 0, "B3: team.js has words for every one of them", missing);
  for (const a of actions) {
    if (typeof PH.PHRASE[a] !== "function") continue;
    const withUser = PH.PHRASE[a]({ action: a, user: "est@example.test", name: "Invitation to bid", detail: "from x@gc.example; 1 file(s) attached" });
    const noUser = PH.PHRASE[a]({ action: a, user: null, name: null, detail: "nothing was read" });
    check(Array.isArray(withUser) && typeof withUser[0] === "string" && withUser[0].length > 8 && !/_/.test(withUser[0])
          && withUser[1] === "Invitation to bid" && withUser[2] === "from x@gc.example; 1 file(s) attached",
          `B3: ${a} reads as a sentence: verb, the subject or file, the server's detail`, withUser);
    if (a === "inbound_unknown_address" || a === "inbound_refused")
      check(noUser[3] === "The bid mailbox", `B3: ${a} with no account to name starts "The bid mailbox", never "Someone"`, noUser);
  }
  check(PH.PHRASE.inbound_refused({ user: "est@example.test", name: "s", detail: "d" })[3] === undefined,
        "B3: a refused message that names its account is about that account");
  check(/const \[verb, what, extra, who\] = phrase\(e\);/.test(teamJs) && /h\("b", null, who \|\| e\.user \|\| "Someone"\)/.test(teamJs),
        "B3: the feed prints that subject");
  check(/inbound_project: "Arrived by e-mail", inbound_file_same: "Sent again by e-mail",/.test(shell),
        "B3: the two that name a project read as words in its Activity card");
});
// B4: the door and the three settings are the fork's; the frame's viewer gate lets the mail service through
check(/if path == INBOUND_DOOR and request\.method == "POST":\s*# not a browser route[^\n]*\n\s*return await call_next\(request\)/.test(server),
      "B4: the mail door passes the session gate (its own credentials are checked by the route)");
check(/_sched_promote\(job_id, page\)[^\n]*\n\s*_audit\(request, "sheet_detect", job=job_id, page=page,\s*detail="already running"\)/.test(server),
      "B4: Detect on a page waiting in the e-mail line: the fork's promote, then the redesign's activity line (both kept)");

// ================================================================ C. WALL PANELS
const areas = (/\{ id: "areas",[\s\S]*?\},\n/.exec(shell) || [""])[0];
check(/acts: \["flBtn", "wpBtn"\]/.test(areas) && /owns: \[\["flPanel", "flClose"\], \["wpPanel", "wpClose"\]\]/.test(areas)
      && /primary: \["flBtn", "flPanel"\], auto: "always", needsResult: true, yields: \["wpPanel"\]/.test(areas),
      "C1: the Areas module carries Wall panels: its button, its panel and close button, and Flooring yields to it", areas);
check(/flPanel: "areas", wpPanel: "areas",/.test(shell) && /flBtn: "flPanel", wpBtn: "wpPanel",/.test(shell),
      "C1: PANEL_MOD.wpPanel = areas, BTN_PANEL.wpBtn = wpPanel");
// C2: before Detect. The button's `hidden` is wallpanels.js's (shown whenever a plan set is open), the frame mirrors it,
// and needsResult gates only the tab's own auto-open of Flooring
check(/\$\("wpBtn"\)\.hidden = !state\.job;/.test(wpJs) && !/\$\("wpBtn"\)\.hidden = !state\.result/.test(wpJs),
      "C2: wallpanels.js shows its button whenever a plan set is open (no detection needed)");
check(/"data-for": id, hidden: b\.hidden,/.test(shell), "C2: the module card's button mirrors the real button's `hidden`");
{
  const op = fnSrc(shell, "openPrimary");
  check(op.indexOf("(m.yields || []).some(isOpen)") > 0 && op.indexOf("(m.yields || []).some(isOpen)") < op.indexOf("m.needsResult && !S().result"),
        "C2: the tab never opens Flooring over an open Wall panels panel - on a tab press, a page change or a detection landing");
  check(/if \(!m\.primary\) return;/.test(op) && !/wpBtn|wpPanel/.test(op), "C2: ...by the module's own `yields`, no panel named in the rule");
}
// the same rule, run: a model of openPrimary's gate with the module table from shell.js
{
  const MODULES = new Function("return " + block(shell, "const MODULES = [", "const MOD = Object.fromEntries").replace("const MODULES = ", "").replace(/;\s*$/, ""))();
  const A = MODULES.find((m) => m.id === "areas"), T = MODULES.find((m) => m.id === "trenching"), C = MODULES.find((m) => m.id === "counts");
  const opens = (m, reason, open, result) => {
    if (!m.primary) return false;
    if ((m.yields || []).some((p) => open.includes(p))) return false;
    if (m.needsResult && !result) return false;
    return (m.auto === "always" && ["tab", "page", "result"].includes(reason)) || (m.auto === "tab" && reason === "tab");
  };
  check(opens(A, "tab", [], true) && opens(A, "page", [], true) && opens(A, "result", [], true),
        "C2: Areas with a detection and Wall panels closed: Flooring opens by itself, as before");
  check(!opens(A, "tab", [], false), "C2: Areas with no detection: nothing opens by itself (Wall panels is one press away)");
  check(!opens(A, "tab", ["wpPanel"], true) && !opens(A, "page", ["wpPanel"], true) && !opens(A, "result", ["wpPanel"], true),
        "C2: Wall panels open: Flooring stays down on a tab press, a page change and a detection landing");
  check(opens(T, "tab", [], true) && opens(T, "page", ["wpPanel"], true) && opens(C, "tab", ["wpPanel"], false) && !opens(C, "page", [], true),
        "C2: Trenching and Counts open exactly as they did (no module but Areas yields)");
  check(MODULES.map((m) => m.id).join() === "plans,lines,counts,areas,earthwork,trenching,summary,3d",
        "C2: the eight module tabs are the same eight, in the same order");
}
check(/if \(wm && wm\.primary && \(wm\.yields \|\| \[\]\)\.includes\(t\.id\) && autoOpen === wm\.primary\[1\] && isOpen\(autoOpen\)\) \{/.test(shell)
      && /const own = \(wm\.owns \|\| \[\]\)\.find\(\(\[p\]\) => p === autoOpen\);\s*const c = own && byId\(own\[1\]\);\s*if \(c\) c\.click\(\);/.test(shell),
      "C3: a Flooring the tab opened by itself steps down (through its own close button) when the person opens Wall panels");
check(/const opened = t;\s*requestAnimationFrame\(\(\) => requestAnimationFrame\(\(\) => \{\s*if \(!opened\.hidden && opened\.scrollIntoView\) opened\.scrollIntoView\(\{ block: "start" \}\);/.test(shell),
      "C3: ...and the panel the person opened then shows from its top (it had scrolled to where it sat under Flooring)");
check(/html\.shell-on \.mc-acts \.rb-btn\[aria-pressed="true"\]:hover:not\(:disabled\) \{ background: var\(--t-accent\);\s*border-color: var\(--t-accent\); filter: brightness\(1\.07\); \}/.test(shellCss),
      "C3: a pressed tool in the module card stays the accent under the pointer (its dark ink was on the dark hover ground: unreadable)");
// C4: the fork's page is whole: its nodes, its sheet and its script, after app.js
check(/<button id="wpBtn" hidden title="Wall panel estimate and proposal:/.test(index) && /<div id="wpPanel" class="ew-panel" hidden>/.test(index)
      && /<button id="wpClose" class="mini">close<\/button>/.test(index) && /<div class="ew-hint" id="wpHint">/.test(index),
      "C4: the Tools-menu row and the panel (with its how-to, which the frame folds) are in the page");
{
  const dock = index.slice(index.indexOf('id="toolDock"'), index.indexOf('<section class="card" id="cardSheet">'));
  check(dock.indexOf('id="flPanel"') > 0 && dock.indexOf('id="flPanel"') < dock.indexOf('id="wpPanel"') && dock.indexOf('id="wpPanel"') < dock.indexOf('id="stPanel"'),
        "C4: the panel is in the tool dock (the frame's dock and drawers carry every .ew-panel there)");
}
check(/<a href="#wallpanels">Wall panels<\/a>/.test(index) && /<h2 class="sv-grouptitle" id="svGroupWallpanels">Wall panels<\/h2>/.test(index)
      && /id="svWpSection"/.test(index), "C4: Settings has the Wall panels link and section");
check(index.indexOf('href="wallpanels.css?v=') > index.indexOf('href="style.css?v=')
      && index.indexOf('src="wallpanels.js?v=') > index.indexOf('src="app.js?v='), "C4: wallpanels.css after style.css, wallpanels.js after app.js");
check(/\["wpBtn", "wpPanel"\],/.test(appJs), "C4: app.js lists it in the Tools menu");
// C5: on the sheet a wall's colour says how its length stands: blue / amber / red. Never the accent green (or yellow).
{
  const cols = (/const WALL_CLEAN = "(#[0-9a-f]{6})", WALL_AMBER = "(#[0-9a-f]{6})", WALL_BAD = "(#[0-9a-f]{6})", WALL_DRAFT = "(#[0-9a-f]{6})";/.exec(wpJs) || []).slice(1);
  check(cols.length === 4, "C5: wallpanels.js names its four on-sheet colours", cols);
  const rgb = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const [clean, amber, bad, draft] = cols.map(rgb);
  check(clean && clean[2] > clean[0] && clean[2] > clean[1], "C5: a wall measured at a confirmed scale is BLUE", cols[0]);
  check(amber && amber[0] > amber[2] && amber[1] > amber[2] && amber[0] > amber[1], "C5: one whose scale is not confirmed is AMBER", cols[1]);
  check(bad && bad[0] > bad[1] * 1.8 && bad[0] > bad[2] * 1.8, "C5: one with no length in feet is RED", cols[2]);
  check(draft && draft[2] > draft[0], "C5: a wall being drawn is light blue", cols[3]);
  const accents = [...W("tokens.css").matchAll(/--t-accent(?:-hi)?:\s*(#[0-9a-fA-F]{6})/g)].map((m) => m[1].toLowerCase());
  check(accents.length >= 2 && !cols.some((c) => accents.includes(c.toLowerCase())),
        "C5: none of them is an accent of the frame (green or yellow, dark or paper)", accents);
  check(!/--t-accent|--accent\b|3fa873|f5c400/i.test(wpJs.slice(wpJs.indexOf("function wpOverlay()"), wpJs.indexOf("const _wpDrawBefore = draw;"))),
        "C5: the overlay that draws them reads no accent token");
  check(!/wp-wall|WALL_|wpOverlay/.test(shellCss + rspCss + shell), "C5: the frame does not restyle or redraw the walls on the sheet");
}

// ================================================================ the cache busters
// every file the frame changed for this merge is named anew; app.js is one past the fork's, as at every merge: the fork
// was at 311 when the three features came over and at 312 after its fix round (merged here 2026-10-04, the second
// merge: app.js changed again - an unsaved takeoff change waits for its page, the e-mail list is asked only when the
// feature is on - so a browser that holds the redesign's 312 must be handed the new file)
{
  const v = (f) => +((new RegExp(f.replace(/\./g, "\\.") + "\\?v=(\\d+)").exec(index) || [])[1] || 0);
  check(v("shell.js") >= 20 && v("shell.css") >= 15 && v("responsive.css") >= 8 && v("team.js") >= 7,
        "the frame's changed files carry new cache busters (shell.js 20, shell.css 15, responsive.css 8, team.js 7)",
        [v("shell.js"), v("shell.css"), v("responsive.css"), v("team.js")]);
  check(v("app.js") >= 313 && v("style.css") >= 122 && v("wallpanels.js") >= 2 && v("wallpanels.css") >= 2,
        "app.js is past the fork's 312 (the fix round changed it); style.css and the wall panel files are at the fork's",
        [v("app.js"), v("style.css")]);
}

// ================================================================ the fix round (the second merge of 2026-10-04)
// With bids by e-mail off the page no longer asks for the e-mail list (app.js inbAsk, from `inbound` in GET /api/config
// and GET /api/projects). That only holds here while the FRAME asks nothing of its own: its Projects page is app.js's,
// moved, and none of the four frame scripts may fetch the list (or the page would answer 404 on every Projects load again).
{
  const frame = shell + teamJs + W("dock.js") + W("responsive.js");
  check(!/\/api\/inbound/.test(frame), "the frame's scripts never ask for /api/inbound themselves: the e-mail list is app.js's to ask, and it asks only when the feature is on");
  check(/function inbAsk\(/.test(appJs) && /inbAsk\(d \? d\.inbound : undefined\)/.test(appJs) && /typeof cfg\.inbound === "boolean"/.test(appJs),
        "app.js (the fork's file) asks through inbAsk, told by `inbound` in the projects answer and in /api/config");
  check(/"inbound": _inbound\.config\(\) is not None/.test(server) && (server.match(/"inbound": _inbound\.config\(\) is not None/g) || []).length === 2,
        "the server says whether bids by e-mail is on in GET /api/config and in GET /api/projects (both came through the merge)");
}

console.log(failures ? `\n${failures} failure(s)` : "\nall redesign three-features checks passed");
process.exit(failures ? 1 : 0);
