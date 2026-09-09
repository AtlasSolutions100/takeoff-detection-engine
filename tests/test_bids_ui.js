// THE CRM HAND-OFF INTO CORE - "send to CORE for takeoff" (2026-09-07,
// narrowed 2026-09-09).
//
// Bid Sniper / the CRM (OptionallyWorking/bid_sniper, :8200; a separate,
// separately charged product) finds open solicitations and holds their plan
// sets. What core does is ACCEPT the hand-off: the CRM opens
// <core>/#bid=<listing id> in a new tab, and core pulls the plan set in as a
// project of whoever is signed in here, with this browser's own session.
//
// There is NO Open bids tab in core any more (removed at Koby's call on
// 2026-09-09): the listing lives in the CRM. This file pins what is left -
// the boot hook, bidPull, the file chooser, the "from bid:" note on a pulled
// project's card - and that the tab really is gone.
//
// Dependency-free:  node tests/test_bids_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const HTML = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const CSS = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
const LOGIN = fs.readFileSync(path.join(__dirname, "..", "webui", "login.html"), "utf8");

let fails = 0;
function check(cond, msg) {
  console.log((cond ? "  ok   " : "  FAIL ") + msg);
  if (!cond) fails++;
}
function grab(name) {
  const start = SRC.indexOf("function " + name + "(");
  if (start < 0) return null;
  let depth = 0;
  for (let j = SRC.indexOf("{", start); j < SRC.length; j++) {
    if (SRC[j] === "{") depth++;
    else if (SRC[j] === "}" && --depth === 0) return SRC.slice(start, j + 1);
  }
  return null;
}
function grabConst(name) {
  const m = new RegExp("const " + name + " = ([^;]+);").exec(SRC);
  return m ? m[1] : null;
}

// ---- the tab is gone; the hand-off is not --------------------------------
console.log("no tab");
const P = new Function("const PROJ_TABS = " + grabConst("PROJ_TABS") + ";\n"
  + grab("projTabNoun") + "\nreturn { PROJ_TABS, projTabNoun };")();
check(P.PROJ_TABS.join(",") === "demo,mine", "PROJ_TABS: demo, mine - no bids tab");
check(P.projTabNoun("bids").many === "projects", "projTabNoun no longer knows a bids population");
for (const gone of ["pvBidCard", "pvPullBid", "bidReachLine", "projEmptyBids",
                    "bidPlansLine", "bidCompText", "bidActionText"]) {
  check(!grab(gone), "removed with the tab: " + gone);
}
check(!/pvTabBids/.test(SRC) && !/id="pvTabBids"/.test(HTML), "no pvTabBids in app.js or index.html");
check(!/fetch\("\/api\/bids"\)/.test(SRC), "the Projects page does not fetch /api/bids");
check(!/\.pv-card\.pv-bid \{/.test(CSS) && !/\.pv-bid-act \{/.test(CSS), "style.css: the bid card rules are gone");

// ---- Send to Takeoff from the CRM: #bid=<id> on this site ------------------
console.log("hand-off");
const boot = grab("bootJob");
check(boot && /\/\^#bid=\(\.\+\)\$\/\.exec\(BOOT_HASH/.test(boot), "boot: #bid=<id> is recognised");
check(boot && /decodeURIComponent\(id\)/.test(boot), "boot: the id is decoded (the sniper encodes the colons)");
check(boot && /bidPull\(id, null\)/.test(boot), "boot: it runs bidPull");
check(boot && /pvTab = "mine"; loadProjects\(\);/.test(boot),
      "boot: a failed pull lands on Your projects (there is no bids tab to land on)");
check(boot && boot.indexOf("#bid=") < boot.indexOf("#job="), "boot: #bid= is tried before #job=");
check(/dest \+ \(location\.hash \|\| ""\)/.test(LOGIN), "login.html: the fragment survives the sign-in redirect");
check(/dest\.includes\("#"\) \? dest :/.test(LOGIN), "login.html: ...unless `next` carries its own");
check(/\/\^#job=\(\[0-9a-f\]\{6,32\}\)\$\/\.exec\(BOOT_HASH/.test(SRC),
      "#job=<id> at boot opens that job (what the sniper's dashboard links to)");

const pull = grab("bidPull");
check(!!pull, "bidPull exists");
check(pull && /const enc = encodeURIComponent\(id\);/.test(pull), "bidPull encodes the listing id");
check(pull && /fetch\("\/api\/bids\/" \+ enc \+ "\/plans"\)/.test(pull), "bidPull asks for the file list first");
check(pull && /method: "POST"/.test(pull), "...then pulls with a POST");
check(pull && /applyOpened\(first\)/.test(pull), "success goes through the same applyOpened as an upload");
check(pull && /j\.detail/.test(pull), "failure prints the server's own words");
check(pull && /files\.length === 1 \? \[files\[0\]\.i\] : await bidChoose\(plans\)/.test(pull),
      "one file opens at once; more than one asks");
check(pull && /return "";\s*\/\/ cancelled/.test(pull), "cancel is an empty string, not an error");
check(pull && /"\/pull\?file=" \+ picks\[k\]/.test(pull), "every ticked file is pulled by its index");
check(pull && /added under Your projects/.test(pull), "the rest are announced, not lost");

// ---- a pulled project says where it came from -------------------------------
const pc = grab("pvCard");
check(pc && /r\.bid/.test(pc) && /"from bid: "/.test(pc), "pvCard prints 'from bid:' for a pulled plan");
check(pc && /if \(r\.bid && \(r\.bid\.title \|\| r\.bid\.agency\)\)/.test(pc),
      "...and nothing at all for an upload");
const M = new Function(grab("bidClosesText") + "\n" + grab("bidMetaLine")
  + "\nreturn { bidClosesText, bidMetaLine };")();
const NOW = Date.UTC(2026, 8, 9, 12, 0, 0);
const iso = (days) => new Date(NOW + days * 86400000).toISOString().replace(/Z$/, "");
check(M.bidMetaLine({ agency: "CITIES - Lehi City", due: iso(3) }, NOW).startsWith("Lehi City · closes in 3 days"),
      "from bid: agency prefix stripped, close date in words");
check(M.bidClosesText("garbage", NOW) === "no close date read", "from bid: junk date is undated, not NaN");

// ---- the file chooser -------------------------------------------------------
console.log("chooser");
const C = new Function(grab("bidFileLine") + "\n" + grab("bidPickWhy")
  + "\nreturn { bidFileLine, bidPickWhy };")();
check(C.bidFileLine({ size: 6522825, pages: 4, sheet_pages: 4, text_pages: 0, hint: "drawings" }).includes("4 pages"),
      "file line: page count");
check(C.bidFileLine({ size: 10, pages: null, hint: "unknown" }).includes("could not be read"),
      "file line: an unreadable file says so");
check(C.bidPickWhy({ files: [{ hint: "drawings" }, { hint: "text" }] }).includes("is ticked"),
      "why line: says what is pre-ticked");
const bc = grab("bidChoose");
check(bc && /suggest\.has\(f\.i\)/.test(bc), "the chooser pre-ticks the server's suggestion");
check(bc && /e\.key === "Escape"/.test(bc) && /done\(null\)/.test(bc), "Esc cancels");
check(/id="bidPick" class="gz-modal" hidden/.test(HTML), "index.html: the chooser exists, hidden");
check(/id="bidPickGo"/.test(HTML) && /id="bidPickCancel"/.test(HTML), "index.html: Open selected and Cancel");
check(/\.bid-pick-row \{/.test(CSS), "style.css: chooser rows are styled");
const v = /app\.js\?v=(\d+)/.exec(HTML);
check(v && Number(v[1]) >= 169, "index.html: cache-buster moved with the removal (got " + (v && v[1]) + ")");

console.log(fails ? "\n" + fails + " FAILURE(S)" : "\nall green");
process.exit(fails ? 1 : 0);
