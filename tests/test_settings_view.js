// The SETTINGS page's pure logic, pinned.
//
// Only the block between the SETTINGS-PURE markers is evaluated (app.js is
// not a module and touches the DOM at load) - same technique as
// test_projects_view.js. Structure checks at the bottom pin the view's
// contract with index.html: the view exists inside <main>, the brand block
// carries the Settings link, the CSS toggle hides the same three workbench
// columns the projects view does, and the key swallow includes Ctrl+Z/Y.
//
// The page grew from "the price book" to "Settings" on 2026-09-01. The price
// book's own checks below are UNCHANGED - that was the requirement - and the
// four new groups add their own: scopeSeed (standing default vs per-document
// declaration), the theme store (one key, two controls), svAccountState (a
// box with no accounts must SAY so), and svHashTarget (#pricing still lands
// on the price book).
//
// Dependency-free:  node tests/test_settings_view.js

"use strict";
const fs = require("fs");
const path = require("path");

const appSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "app.js"), "utf8");
const htmlSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "index.html"), "utf8");
const cssSrc = fs.readFileSync(
  path.join(__dirname, "..", "webui", "style.css"), "utf8");

let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}

const a = appSrc.indexOf("// SETTINGS-PURE-BEGIN");
const b = appSrc.indexOf("// SETTINGS-PURE-END");
check(a >= 0 && b > a, "SETTINGS-PURE markers present in webui/app.js");
const S = new Function(appSrc.slice(a, b) +
  " return { svBookRows, svSplitCsvLine, svCsvField, svSerializeCSV,"
  + " svParseCSV, svImportPlan, svPreviewLine,"
  + " scopeSeed, themeNormalize, themeToggleNext, CORE_THEMES,"
  + " svAccountState, svHashTarget };")();

// ---- the book: stored keys merged with the live takeoff --------------------
{
  const rows = S.svBookRows(
    ["Silt Fence", "Water Main"],                       // stored prices
    [{ label: "Silt Fence", unit: "ft" },               // live rows
     { label: "Curb", unit: "ft" },
     { label: "Manholes", unit: "EA" }]);
  check(rows.length === 4, "book: stored + live dedupes to the union");
  const byLab = Object.fromEntries(rows.map(r => [r.label, r]));
  check(byLab["Silt Fence"].inUse && byLab["Silt Fence"].stored,
    "book: a label both stored and on the sheet carries both flags");
  check(byLab["Silt Fence"].unit === "ft",
    "book: the unit comes from the live row when known");
  check(byLab["Water Main"].inUse === false
    && byLab["Water Main"].stored === true
    && byLab["Water Main"].unit === "",
    "book: stored-only label has no unit and is not in use");
  check(byLab["Curb"].stored === false && byLab["Curb"].inUse === true,
    "book: live unpriced label is in-use, not stored");
  check(rows.map(r => r.label).join("|") === "Curb|Manholes|Silt Fence|Water Main",
    "book: rows sort case-insensitively by label");
}
{
  const rows = S.svBookRows(["b"], [{ label: "A", unit: "ft" },
                                    { label: "A", unit: "" }]);
  check(rows.length === 2 && rows[0].label === "A" && rows[0].unit === "ft",
    "book: duplicate live labels collapse and keep the known unit");
  check(S.svBookRows([], []).length === 0, "book: empty in, empty out");
}

// ---- CSV round trip ---------------------------------------------------------
{
  const rows = [
    { label: "Silt Fence", unit: "ft", price: 4.5 },
    { label: 'Pipe, 12" RCP', unit: "ft", price: 62 },  // comma AND quote
    { label: "Manholes", unit: "EA", price: 5200 },
  ];
  const csv = S.svSerializeCSV(rows);
  check(csv.startsWith("label,unit,price\n"), "csv: header row present");
  const back = S.svParseCSV(csv);
  check(back.bad.length === 0, "csv: round trip has no unparseable lines");
  check(back.rows.length === 3
    && back.rows[1].label === 'Pipe, 12" RCP'
    && back.rows[1].price === 62
    && back.rows[0].unit === "ft",
    "csv: labels with commas and quotes survive the round trip");
}

// ---- CSV parse: forgiving where safe, listed where not ----------------------
{
  // Excel quotes a currency cell ("$1,250.50"); the parser strips $ and the
  // thousands separators. Unquoted it would honestly be four fields.
  const r = S.svParseCSV('label,unit,price\nCurb,ft,"$1,250.50"\nGrate,EA,80\n');
  check(r.rows.length === 2 && r.rows[0].price === 1250.5,
    "csv: quoted $ and thousands separators in the price are accepted");
}
{
  const r = S.svParseCSV("Curb,12.5\n");
  check(r.rows.length === 1 && r.rows[0].label === "Curb"
    && r.rows[0].unit === "" && r.rows[0].price === 12.5,
    "csv: two-field label,price rows are accepted with a blank unit");
}
{
  const text = [
    "label,unit,price",
    "Good Row,ft,10",
    "No Price Here,ft,abc",       // bad price
    ",ft,5",                      // empty label
    "Too,many,fields,here,9",     // wrong field count
    '"Unclosed quote,ft,2',       // unclosed quote
    "Zero,ft,0",                  // not > 0
  ].join("\n");
  const r = S.svParseCSV(text);
  check(r.rows.length === 1 && r.rows[0].label === "Good Row",
    "csv: only the good row parses");
  check(r.bad.length === 5, "csv: every bad line is listed, none dropped");
  check(r.bad.every(x => x.line >= 3 && x.text && x.reason),
    "csv: bad lines carry 1-based line number, text and reason");
  check(r.bad.some(x => x.reason === "unclosed quote"),
    "csv: an unclosed quote is named, not guessed at");
}
{
  const r = S.svParseCSV("");
  check(r.rows.length === 0 && r.bad.length === 0, "csv: empty text is empty");
}

// ---- dry-run plan ------------------------------------------------------------
{
  const plan = S.svImportPlan(
    [{ label: "A", unit: "", price: 1 },
     { label: "B", unit: "", price: 2 },
     { label: "B", unit: "", price: 3 },   // duplicate: last one wins, once
     { label: "C", unit: "", price: 4 }],
    ["B", "Z"]);
  check(plan.adds.length === 2 && plan.overwrites.length === 1,
    "plan: new vs overwrite counted against the stored labels");
  check(plan.overwrites[0].price === 3,
    "plan: duplicate labels in the file collapse to the last occurrence");
}
check(S.svPreviewLine(12, 3, 2) === "12 new, 3 overwrite, 2 unparseable — shown below",
  "plan: the preview line says it shows the unparseable lines");
check(S.svPreviewLine(12, 3, 0) === "12 new, 3 overwrite, 0 unparseable",
  "plan: nothing unparseable, nothing promised");

// ---- structure -----------------------------------------------------------
check(/<main>[\s\S]*id="settingsView"/.test(htmlSrc),
  "#settingsView lives inside <main>");
const brand = htmlSrc.slice(htmlSrc.indexOf('class="brand"'),
                            htmlSrc.indexOf("</div>", htmlSrc.indexOf('class="brand"')));
check(brand.includes('id="settingsLink"'),
  "the Pricing link rides the brand block");
check(brand.includes('id="projectsLink"'),
  "…next to the Projects link that was already there");
check(!htmlSrc.slice(htmlSrc.indexOf('id="toolsMenu"'),
                     htmlSrc.indexOf("</div>", htmlSrc.indexOf('id="toolsMenu"')))
  .includes("settingsLink"),
  "no settings row inside the Tools dropdown (tools are sheet tools)");
check(cssSrc.includes("main.sv-open #pageStrip")
  && cssSrc.includes("main.sv-open #sidebar")
  && cssSrc.includes("main.sv-open #stage"),
  "the CSS toggle hides exactly the three workbench columns");
for (const id of ["svBack", "svBookNote", "svBookTable", "svAddLabel",
                  "svAddPrice", "svAddBtn", "svExportBtn",
                  "svImportBtn", "svImportFile", "svImportPreview", "svFoot"]) {
  check(htmlSrc.includes(`id="${id}"`), `#${id} exists in index.html`);
}

// ---- the redesigned price book (fix 10) --------------------------------------
// A real <table> in its own scroller so the head can stick and the rows can
// zebra. What must NOT change: the rows come from svBookRows, the writes go
// through priceKey/store, and the honesty footer keeps its wording.
const book = appSrc.slice(appSrc.indexOf("function renderSvBook()"),
                          appSrc.indexOf('$("svAddBtn").onclick'));
check(book.includes('createElement("table")')
  && book.includes('className = "sv-book-table"'),
  "book: rendered as a real table, not a div grid");
check(book.includes('className = "sv-book-scroll"'),
  "book: the table sits in its own scroller (so thead can stick)");
check(/\.sv-book-table thead th\s*{[^}]*position:\s*sticky/.test(cssSrc),
  "book: the table head is sticky");
check(/\.sv-book-table tbody tr:nth-child\(even\) td/.test(cssSrc),
  "book: rows zebra");
check(book.includes('head: "On this sheet"') && book.includes('head: "Stored only"'),
  "book: grouped on-this-sheet vs stored-only");
check(book.includes("rows.filter(r => r.inUse)")
  && book.includes("rows.filter(r => !r.inUse)"),
  "book: the two groups partition svBookRows' own inUse flag");
// the price cell reads as editable even when empty - "set", not a bare dash
check(book.includes('pin.placeholder = "set"'),
  "book: an unpriced row shows an editable affordance, not a bare dash");
check(book.includes('className = "sv-cur"'), "book: the $ sits beside the field");
// the store contract is untouched: same key helper, same three writes
check(book.includes("store.setItem(priceKey(r.label), pin.value)")
  && book.includes("store.removeItem(priceKey(r.label))"),
  "book: writes through priceKey/store exactly as before");
check(!book.includes('"price:"'), "book: no hand-built price: key");
check(book.includes("pin.onchange = () => { renderSvBook(); renderSvFoot(); }"),
  "book: re-render on blur, not on keystroke (the field stays attached)");
check(book.includes('ev.preventDefault()') && book.includes('"wheel"'),
  "book: the wheel guard on a focused number field survives");

// the swallow block: the projects view's list, Ctrl+Z/Y included
const sv = appSrc.slice(appSrc.indexOf("SV_SWALLOW_KEYS"),
                        appSrc.indexOf("end SETTINGS"));
check(sv.includes('"Delete"') && sv.includes('"Backspace"'),
  "swallow: Delete and Backspace are swallowed while Settings is up");
check(/e\.key\.toLowerCase\(\) === "z"/.test(sv)
  && /e\.key\.toLowerCase\(\) === "y"/.test(sv),
  "swallow: Ctrl+Z and Ctrl+Y never reach the hidden sheet");
check(sv.includes('e.key === "Escape"'),
  "swallow: Esc is handled (back to the sheet, or to Projects)");
// T (trim) needs no selection on the sheet, so it must be swallowed with the
// other single-key tools - it was missing, and armed trim on the hidden sheet
check(sv.includes('"t"'),
  "swallow: T (trim) never reaches the hidden sheet while Settings is up");

// the settings page writes through the SAME key scheme the itemized panel
// uses - priceKey - and never invents a second one
const svAll = appSrc.slice(appSrc.indexOf("SETTINGS: one page, five groups"));
check(svAll.includes("priceKey(") && !/"price:"\s*\+/.test(svAll),
  "store: settings goes through priceKey(), no hand-built price: keys");
// persistence honesty: the footer says where prices actually live
check(appSrc.includes("Prices live in this browser"),
  "footer: names the browser as where prices live");
// the two views are exclusive, both ways
check(/if \(on && svIsOpen\(\)\) svHide\(\);/.test(appSrc),
  "exclusive: opening Projects closes Settings");
check(/if \(on && pvIsOpen\(\)\) showProjects\(false\);/.test(appSrc),
  "exclusive: opening Settings closes Projects");

// ============================================================================
// SCOPE: the standing default vs the per-document declaration
// ============================================================================
// The one rule this must never break: a standing default may SEED a document
// that has never been declared on, and may never override a declaration the
// operator has already made on a sheet - "show everything" (an empty list
// that was CHOSEN) included.
const VALID = { "Wet Utilities": 1, "Earthwork & Grading": 1, "Structures": 1 };
{
  const r = S.scopeSeed(null, ["Wet Utilities"], VALID);
  check(r.trades.join() === "Wet Utilities" && r.from === "default",
    "scope seed: a document with NO record starts from the standing default");
  check(r.showAside === false, "scope seed: a seeded record shows nothing aside");
}
{
  const r = S.scopeSeed(null, [], VALID);
  check(r.trades.length === 0 && r.from === "none",
    "scope seed: no record and no default is 'none' - nothing declared");
}
{
  // THE CRITICAL CASE. "show everything" writes {trades: []}. Re-seeding it
  // would be the Settings preference silently undoing a click on the sheet.
  const r = S.scopeSeed('{"trades":[],"showAside":false}', ["Wet Utilities"], VALID);
  check(r.trades.length === 0 && r.from === "document",
    "scope seed: an EMPTY declaration the operator chose is NOT re-seeded");
}
{
  const r = S.scopeSeed('{"trades":["Structures"]}', ["Wet Utilities"], VALID);
  check(r.trades.join() === "Structures" && r.from === "document",
    "scope seed: a real declaration outranks the default");
}
{
  const r = S.scopeSeed('{"trades":["Structures"],"showAside":true}', [], VALID);
  check(r.showAside === true, "scope seed: showAside survives the read");
}
{
  const r = S.scopeSeed("{ not json", ["Wet Utilities"], VALID);
  check(r.from === "default" && r.trades.join() === "Wet Utilities",
    "scope seed: an unparseable record falls back to the default, never throws");
}
{
  const r = S.scopeSeed(null, ["Wet Utilities", "Retired Trade"], VALID);
  check(r.trades.join() === "Wet Utilities",
    "scope seed: a default naming a trade this build no longer has is dropped");
  const d = S.scopeSeed('{"trades":["Structures","Ghost"]}', [], VALID);
  check(d.trades.join() === "Structures",
    "scope seed: ...and so is one inside a stored declaration");
}
{
  check(S.scopeSeed(null, null, VALID).trades.length === 0,
    "scope seed: a missing default list is empty, not a crash");
  check(S.scopeSeed(null, ["Anything"], null).trades.join() === "Anything",
    "scope seed: with no validity map every token is kept");
}
// the app must actually USE it, and must not persist the provenance field
check(/function scopeRec\(\)\s*{\s*return scopeSeed\(/.test(appSrc),
  "scope: scopeRec() is scopeSeed over the stored record and the default");
check(/scopeSave[\s\S]{0,400}?trades: r\.trades \|\| \[\], showAside: !!r\.showAside/
  .test(appSrc),
  "scope: scopeSave writes only trades+showAside - `from` is never persisted");
check(appSrc.includes('const SCOPE_DEFAULT_KEY = "core-scope-default"'),
  "scope: the standing default has its own key, separate from the document record");
check(appSrc.includes('localStorage.getItem(SCOPE_DEFAULT_KEY)'),
  "scope: the standing default is raw localStorage, not the tk1:: saved work");
check(/scopeKey\(\) { return docKey\(\) \+ "::scope"; }/.test(appSrc),
  "scope: the per-document key is unchanged");
// the bar has to SAY which of the two scopes is talking
check(appSrc.includes('rec.from === "default"')
  && appSrc.includes("from your Settings default"),
  "scope bar: a seeded declaration is labelled as coming from Settings");

// ============================================================================
// APPEARANCE: one stored key, two controls
// ============================================================================
check(S.themeNormalize("light") === "light"
  && S.themeNormalize("dark") === "dark"
  && S.themeNormalize(null) === "dark"
  && S.themeNormalize("banana") === "dark",
  "theme: anything that is not 'light' normalises to the shipped dark default");
check(S.themeToggleNext("dark") === "light"
  && S.themeToggleNext("light") === "dark"
  && S.themeToggleNext(undefined) === "light",
  "theme: the toggle round-trips and an unset value toggles to light");
check(S.CORE_THEMES.join() === "dark,light",
  "theme: the radio group renders exactly the two shipped themes");
check(appSrc.includes('const CORE_THEME_KEY = "core-theme"'),
  "theme: the key is the one that shipped - core-theme");
{
  // ONE writer. The masthead button must not set the value itself.
  const themeBlock = appSrc.slice(appSrc.indexOf("// ---------- theme ----------"),
                                  appSrc.indexOf("// ---------- canvas ----------"));
  check(/\$\("themeBtn"\)\.onclick = \(\) => coreThemeSet\(/.test(themeBlock),
    "theme: #themeBtn goes through coreThemeSet - it does not write the store");
  check((appSrc.match(/localStorage\.setItem\(CORE_THEME_KEY/g) || []).length === 1,
    "theme: exactly ONE place writes core-theme");
  check(!/localStorage\.setItem\("core-theme"/.test(appSrc),
    "theme: no hand-built core-theme write survives anywhere in the file");
}
check(/rb\.onchange = \(\) => \{ if \(rb\.checked\) coreThemeSet\(/.test(appSrc),
  "theme: the Settings radios go through the same single writer");

// ============================================================================
// ACCOUNT: a box with no accounts must say so, not render an empty panel
// ============================================================================
{
  const st = S.svAccountState(null);
  check(st.kind === "unreachable" && st.controls === false
    && /Nothing about\s+this session has changed/.test(st.text.replace(/\s+/g, " ")),
    "account: an unreachable /api/me says so and claims nothing about the session");
}
{
  const st = S.svAccountState({ user: null, staff: false, accounts: false });
  check(st.kind === "noaccounts" && st.controls === false,
    "account: NO ACCOUNTS CONFIGURED hides the sign-out row");
  check(/no accounts are configured/i.test(st.text)
    && /nothing to sign out of/i.test(st.text),
    "account: ...and says in plain words that there is nobody to be signed in as");
  check(/single-password mode/i.test(st.foot)
    && /every\s+document/i.test(st.foot.replace(/\s+/g, " ")),
    "account: ...and does NOT soften what single-password mode exposes");
}
{
  const st = S.svAccountState({ user: null, staff: false, accounts: true });
  check(st.kind === "anon" && st.controls === false,
    "account: accounts exist but this session is not one of them");
}
{
  const st = S.svAccountState({ user: "bill", staff: false, accounts: true });
  check(st.kind === "user" && st.controls === true
    && st.text === "Signed in as bill.",
    "account: a signed-in account is named and gets the controls", st.text);
  check(/this browser only/i.test(st.foot),
    "account: sign-out is scoped honestly to this browser");
}
{
  const st = S.svAccountState({ user: "koby", staff: true, accounts: true });
  check(st.text === "Signed in as koby (staff).",
    "account: staff is stated on the line, not hidden", st.text);
  check(/EVERY\s+document/.test(st.foot.replace(/\s+/g, " "))
    && /delete/i.test(st.foot),
    "account: what STAFF can reach is disclosed in full, not softened");
}
// the two nodes MOVED - they were not rebuilt
check(!/<span id="whoami"[\s\S]{0,400}?<\/div>\s*<\/div>\s*<!-- THE ZONE MODAL/
  .test(htmlSrc), "account: #whoami is no longer the last thing in the status bar");
{
  const sb = htmlSrc.slice(htmlSrc.indexOf('id="statusBar"'),
                           htmlSrc.indexOf("THE ZONE MODAL"));
  check(!sb.includes('id="whoami"') && !sb.includes('id="signOutBtn"'),
    "account: neither node is left behind in #statusBar");
  const sv = htmlSrc.slice(htmlSrc.indexOf('id="settingsView"'),
                           htmlSrc.indexOf('id="pageStrip"'));
  check(sv.includes('id="whoami"') && sv.includes('id="signOutBtn"'),
    "account: both nodes live in #settingsView now");
  check(sv.includes('id="signOutBtn" class="ghost" hidden title="End this '
    + 'session on this browser"'),
    "account: #signOutBtn moved with its class, its hidden and its title intact");
}
check(/loadWhoami\(\)[\s\S]*?svMeSet\(me\)/.test(appSrc),
  "account: loadWhoami still owns the nodes and now also publishes the payload");
check(/catch \(e\) \{ svMeSet\(null\); return; \}/.test(appSrc),
  "account: a failed /api/me exits through svMeSet, so the panel never hangs "
  + "on 'asking the server'");
check(appSrc.includes('$("svAccountRow").hidden = !st.controls'),
  "account: with no account to show, the row is hidden rather than empty");

// ============================================================================
// HASHES: #pricing still lands on the price book
// ============================================================================
check(S.svHashTarget("#pricing") === "svGroupPricing",
  "hash: the OLD #pricing lands on the price book group");
check(S.svHashTarget("#settings") === "",
  "hash: #settings opens the page at the top");
check(S.svHashTarget("#company") === "svGroupCompany"
  && S.svHashTarget("#appearance") === "svGroupAppearance"
  && S.svHashTarget("#scope") === "svGroupScope"
  && S.svHashTarget("#account") === "svGroupAccount",
  "hash: every group has its own hash");
// the nav's "This machine" entry is href="#machine"; without a mapping here
// svGoto answered "not ours" and the click did nothing (fixed 2026-09-25)
check(S.svHashTarget("#machine") === "svGroupMachine",
  "hash: #machine lands on the This-machine group");
check(S.svHashTarget("#PRICING") === "svGroupPricing"
  && S.svHashTarget("pricing") === "svGroupPricing",
  "hash: case and a missing # are both tolerated");
check(S.svHashTarget("#projects") === null
  && S.svHashTarget("") === null
  && S.svHashTarget(null) === null,
  "hash: a hash that is not this page's is left alone");
check(appSrc.includes('on ? "#settings" : location.pathname'),
  "hash: opening the page writes the new #settings, not the old #pricing");
for (const id of ["svGroupCompany", "svGroupAppearance", "svGroupScope",
                  "svGroupPricing", "svGroupAccount"]) {
  check(htmlSrc.includes(`id="${id}"`), `#${id} is a real scroll target`);
}

// ============================================================================
// COMPANY: the only setting that leaves the browser
// ============================================================================
for (const id of ["svBrandCompany", "svBrandContact", "svBrandSave",
                  "svBrandNote", "svBrandState", "svThemeChoice",
                  "svScopeChips", "svScopeNote", "svAccountNote",
                  "svAccountRow", "svAccountFoot", "svNav"]) {
  check(htmlSrc.includes(`id="${id}"`), `#${id} exists in index.html`);
}
check(appSrc.includes('fetch("/api/branding")')
  && /fetch\("\/api\/branding", \{\s*method: "POST"/.test(appSrc),
  "company: read and written over /api/branding, not localStorage");
check(!/store\.setItem\([^)]*brand/i.test(appSrc)
  && !/localStorage\.setItem\("core-(company|brand)/.test(appSrc),
  "company: it is NOT quietly mirrored into a browser store as well");
{
  // every account sees this example; until 2026-09-28 it was a client's
  // company name (same names as tests/test_projects_api.py's demo check)
  const ph = (htmlSrc.match(/id="svBrandCompany"[^>]*?placeholder="([^"]*)"/) || [])[1];
  check(typeof ph === "string" && ph.length > 0
    && !/ryno|stice|liberty|beaumont|kayden|ellis|klayton|thielen|aqua vista|barn|swr/i.test(ph),
    `company: the example name is not a client's ("${ph}")`);
}
{
  const co = htmlSrc.slice(htmlSrc.indexOf('id="svGroupCompany"'),
                           htmlSrc.indexOf('id="svGroupAppearance"'));
  check(/Stored on <b>the server<\/b>|the server/.test(
          appSrc.slice(appSrc.indexOf("function renderSvBrand"),
                       appSrc.indexOf("$(\"svBrandCompany\").oninput"))),
    "company: the page says the value is stored on the server");
  check(/never displace\s+the accuracy notice/i.test(co) && !/\bbeta\b/i.test(co),
    "company: the page says the letterhead does not displace the accuracy notice (no BETA)");
  check(/nothing here changes a\s+quantity/i.test(co.replace(/\s+/g, " ")),
    "company: ...and that branding cannot move a number");
}
// the standing-vs-document distinction has to be ON THE PAGE, in words
{
  const sc = appSrc.slice(appSrc.indexOf("function renderSvScope"),
                          appSrc.indexOf("// ---------- ACCOUNT"));
  check(/starting value, not a filter/i.test(sc),
    "scope page: says the default is a starting value, not a filter");
  check(/always wins/.test(sc) && /on that job/.test(sc),
    "scope page: says the per-document declaration always wins");
  check(/ever deletes a row or changes a measured length/.test(sc),
    "scope page: repeats the guarantee the sheet panel makes");
}

// ============================================================================
// the price book did not move
// ============================================================================
{
  const sv = htmlSrc.slice(htmlSrc.indexOf('id="settingsView"'),
                           htmlSrc.indexOf('id="pageStrip"'));
  check(sv.indexOf('id="svGroupPricing"') < sv.indexOf('id="svBookTable"')
    && sv.indexOf('id="svGroupPricing"') < sv.indexOf('id="svExportBtn"'),
    "pricing: the book and CSV both sit under the "
    + "PRICING group head");
  check(sv.indexOf('id="svGroupAccount"') > sv.indexOf('id="svFoot"'),
    "pricing: the honesty footer still follows the price book");
}
// Every group's renderer runs, in page order, and the price book keeps its
// place in the middle. Deliberately NOT an exact-adjacency regex: this landed
// the same day as the line-colour convention book, which legitimately inserts
// renderSvColors() into this sequence, and an assertion that breaks when a new
// group is added correctly is an assertion that gets deleted rather than read.
{
  const _i = appSrc.indexOf("function renderSettings()");
  const _NL = String.fromCharCode(10);
  const body = _i < 0 ? "" : appSrc.slice(_i, appSrc.indexOf(_NL + "}", _i));
  const order = ["renderSvBrand", "renderSvTheme", "renderSvScope", "renderSvBook",
                 "renderSvFoot", "renderSvAccount"];
  const at = order.map(f => body.indexOf(f + "("));
  check(at.every(i => i >= 0),
    "renderSettings calls every group renderer ("
      + order.filter((f, i) => at[i] < 0).join(", ") + " missing)");
  check(at.every((v, i) => i === 0 || v > at[i - 1]),
    "renderSettings paints the groups in page order, price book in the middle");
}

// ============================================================================
// tokens only
// ============================================================================
{
  const newCss = cssSrc.slice(cssSrc.indexOf(
    "SETTINGS: the four groups that joined the price book"));
  check(newCss.length > 500, "the new settings CSS block is present");
  // Comments are stripped first. The block DOCUMENTS two token values by
  // number (#1f5c3e, #06210f) to explain why one of them cannot be used as
  // a fill in both themes, and a rule that forbids naming a colour in prose
  // would only get the prose deleted. What must not contain a raw hex is a
  // declaration.
  const decls = newCss.replace(/\/\*[\s\S]*?\*\//g, "");
  const hex = decls.match(/#[0-9a-fA-F]{3,8}\b/g) || [];
  check(hex.length === 0,
    "css: every DECLARATION in the new block uses var(--t-*), no raw hex"
      + (hex.length ? " (found " + hex.join(" ") + ")" : ""));
  check(/\.sv-grouptitle\s*\{/.test(newCss) && /\.sv-nav a\s*\{/.test(newCss)
    && /\.sv-ch\s*\{/.test(newCss) && /\.scope-src\s*\{/.test(newCss),
    "css: every new class the JS/HTML emits is styled");
}

// ============================================================================
// cache busters: NOT bumped here, and that is a thing to say out loud
// ============================================================================
// EVERY asset carries a cache buster. Deliberately NOT pinned to specific
// numbers any more: this assertion was written by a builder as a reminder to
// bump at integration, and it then failed on three consecutive landings for
// the only reason it ever could - somebody HAD bumped. A check whose green
// state means "nobody has shipped since I was written" teaches integrators to
// edit tests to make them pass, which is the opposite of what it is for. What
// is actually testable is that no asset lost its buster; remembering to turn
// the number belongs to the landing checklist, not here.
{
  const b = (f) => {
    const at = htmlSrc.indexOf(f + "?v=");
    if (at < 0) return null;
    const tail = htmlSrc.slice(at + f.length + 3);
    const n = tail.slice(0, tail.search(/[^0-9]/));
    return n.length ? n : null;
  };
  const seen = {"tokens.css": b("tokens.css"), "style.css": b("style.css"),
                "app.js": b("app.js")};
  for (const [f, v] of Object.entries(seen))
    check(!!v, "cache buster present on " + f + " (v=" + v + ")");
}

if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
console.log("\nall settings view checks passed");
