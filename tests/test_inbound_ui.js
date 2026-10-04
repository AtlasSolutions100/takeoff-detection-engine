// BIDS BY E-MAIL, ON SCREEN (rulings 8 and 13, 2026-10-03).
//
// The "From e-mail" list on the Projects page, the project card's e-mail note and
// Settings -> Bid address (the address, Copy / New address, how to forward, a mail
// provider's confirmation, the keyword rules). What is pinned:
//
//   1  the markup: the list, the Settings group and its nav link are in the page and
//      START HIDDEN; nothing shows while the server's feature is off
//   2  the words (INBOUND-PURE, run under node): what became of each file, how many
//      pages Detect ran on by itself and how to do the rest, which keywords were found
//      on which pages, how many pages had no text to search
//   3  the list itself, drawn into a stand-in DOM from a reply shaped like the
//      server's: newest first - when, sender, subject, each file with its project link
//      or its reason; a keyword's page opens the project on that page; Open anyway
//      posts the message and the file; OFF (the route answers 404) draws nothing and
//      never asks again
//   4  Settings: hidden when off and for a view-only account; the address with Copy and
//      New address; the confirmation text when one arrived; the two word lists
//
// Every name and word here is made up. run: node tests/test_inbound_ui.js
"use strict";
const fs = require("fs");
const path = require("path");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const htmlSrc = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
let fails = 0, checks = 0;
const check = (ok, msg, got) => {
  checks++;
  if (ok) console.log("ok: " + msg);
  else { fails++; console.error("FAIL: " + msg + (got === undefined ? "" : "  (got " + JSON.stringify(got).slice(0, 400) + ")")); }
};
const attempt = (name, fn) => { try { fn(); } catch (e) { check(false, name + ": raised " + (e && e.message)); } };
const cut = (from, to) => {
  const a = appSrc.indexOf(from), b = appSrc.indexOf(to, a + 1);
  if (a < 0 || b < 0) throw new Error("app.js has no block " + from);
  return appSrc.slice(a, b);
};

// ---- 1. markup ---------------------------------------------------------------------------------
attempt("markup", () => {
  const pv = htmlSrc.slice(htmlSrc.indexOf('id="projectsView"'), htmlSrc.indexOf('id="settingsView"'));
  check(/<section id="pvInbox" class="pv-inbox" aria-label="From e-mail" hidden><\/section>/.test(pv)
    && pv.indexOf('id="pvInbox"') > pv.indexOf('id="pvReach"') && pv.indexOf('id="pvInbox"') < pv.indexOf('id="pvGrid"'),
    "1: the From e-mail list sits on the Projects page above the cards, and starts hidden");
  check(/<a href="#bidaddress" id="svNavInbound" hidden>Bid address<\/a>/.test(htmlSrc)
    && /id="svGroupInbound" hidden/.test(htmlSrc) && /id="svInboundSection" hidden/.test(htmlSrc)
    && /id="svInbRulesSection" hidden/.test(htmlSrc),
    "1: the Settings group, both its sections and its nav link start hidden");
  for (const id of ["svInbNote", "svInbAddr", "svInbCopy", "svInbNew", "svInbState", "svInbHow", "svInbConfirm",
                    "svInbRulesNote", "svInbKeywords", "svInbExclude", "svInbRulesSave", "svInbRulesState", "svInbRulesFoot"])
    check(new RegExp('id="' + id + '"').test(htmlSrc), "1: index.html has #" + id);
  check(/id="svInbAddr" type="text" readonly/.test(htmlSrc), "1: the address box cannot be typed in");
  const sv = htmlSrc.slice(htmlSrc.indexOf('id="settingsView"'));
  check(sv.indexOf('id="svGroupInbound"') > sv.indexOf('id="svGroupBidsheet"') && sv.indexOf('id="svGroupInbound"') < sv.indexOf('id="svGroupAccount"'),
    "1: the group sits between Bid sheet and Account");
  check(/\.pv-inbox\[hidden\] \{ display: none; \}/.test(cssSrc) && /\.inb-entry \{/.test(cssSrc), "1: the list is styled, and hidden means hidden");
  const S = new Function(cut("// SETTINGS-PURE-BEGIN", "// SETTINGS-PURE-END") + " return { svHashTarget };")();
  check(S.svHashTarget("#bidaddress") === "svGroupInbound" && S.svHashTarget("#bidsheet") === "svGroupBidsheet",
    "1: #bidaddress lands on the group");
});

// ---- 2. the words ------------------------------------------------------------------------------
let P = null;
attempt("pure block", () => {
  P = new Function(cut("// INBOUND-PURE-BEGIN", "// INBOUND-PURE-END")
    + " return { INB_SHOWN, INB_HOW, inbPages, inbAutoText, inbMatch, inbFileLine, inbEntryNotes, inbSplitFiles, inbCardNote, inbLimitsLine, inbWords,"
    + " inbPageShown, inbLineSays, inbUnlisted: typeof inbUnlisted === 'function' ? inbUnlisted : null };")();
});
attempt("auto text", () => {
  const A = P.inbAutoText;
  check(A({ queued: 3, done: 1, running: true, failed: 0, wanted: 3, total: 3, cap: 25 })
    === "Detect is running by itself: 1 of 3 pages done.", "2: while it runs: how many are done so far", A({ queued: 3, done: 1, running: true, wanted: 3, total: 3, cap: 25 }));
  check(A({ queued: 1, done: 0, running: true, waiting: true, failed: 0, wanted: 1, total: 1, cap: 25 })
    === "Waiting its turn to detect by itself: 0 of 1 page done.",
    "2: a set still WAITING in the line is not said to be running", A({ queued: 1, done: 0, running: true, waiting: true, wanted: 1, total: 1, cap: 25 }));
  check(P.inbPageShown({ status: "running", queued: true, email: true }) === "idle"
    && P.inbPageShown({ status: "running", email: true }) === "running" && P.inbPageShown({ status: "running", queued: true }) === "running"
    && P.inbPageShown({ status: "done" }) === "done" && P.inbPageShown({ status: "idle" }) === "idle"
    && P.inbPageShown({ status: "error: x" }) === "idle" && P.inbPageShown(null) === "idle",
    "2: a sheet waiting in the e-mail line reads as not detected, never 'detecting'; every other status reads as it did");
  check(P.inbLineSays({ status: "running", queued: true, email: true })
    === "This sheet is waiting its turn: a set that came in by e-mail detects one sheet at a time. Press Detect to do it now."
    && P.inbLineSays({ status: "running", email: true }) === "This sheet is being detected by itself right now. It opens here when it is done.",
    "2: the sheet says which: waiting (and that Detect does it now), or being detected by itself");
  check(A({ queued: 3, done: 3, running: false, failed: 0, wanted: 3, total: 3, cap: 25 }) === "Detect ran by itself on all 3 pages.",
    "2: finished, every page", A({ queued: 3, done: 3, running: false, wanted: 3, total: 3, cap: 25 }));
  check(A({ queued: 25, done: 25, running: false, failed: 0, wanted: 60, total: 60, cap: 25 })
    === "Detect ran by itself on 25 of 60 pages. E-mail detects up to 25 pages by itself: open it and press Detect all to do the other 35.",
    "2: a set over the cap: how many were done, and how to do the rest", A({ queued: 25, done: 25, running: false, wanted: 60, total: 60, cap: 25 }));
  check(A({ queued: 4, done: 4, running: false, failed: 0, wanted: 4, total: 9, cap: 25 })
    === "Detect ran by itself on 4 of 9 pages. The other 5 did not read as plans: open it and press Detect on any you need.",
    "2: pages that did not read as plans are left to the person, and it says so");
  check(A({ queued: 0, done: 0, running: false, failed: 0, wanted: 0, total: 6, cap: 25 })
    === "Detect did not start by itself: no sheet read as a plan. Open it and press Detect.", "2: nothing queued: said, with what to do");
  check(A({ queued: 5, done: 2, running: false, failed: 1, wanted: 5, total: 5, cap: 25 })
    === "Detect ran by itself on 2 of the 5 pages it started on (1 failed). Open it and press Detect all to do the rest.",
    "2: stopped short (a restart, a failure): never reads as finished");
  check(A(null) === "", "2: no record, no sentence");
});
attempt("match text", () => {
  const m = P.inbMatch({ found: [{ word: "quartzline", pages: [0, 3], count: 2 }, { word: "blue marrow pipe", pages: [1], count: 7 }],
                         searched: 4, no_text: 0, pages: 4 });
  check(JSON.stringify(m) === JSON.stringify({ found: [{ word: "quartzline", pages: [0, 3], more: 0 },
    { word: "blue marrow pipe", pages: [1], more: 6 }], note: "" }), "2: each keyword with its pages; pages past the listed ones are counted", m);
  check(P.inbMatch({ found: [], searched: 3, no_text: 3, pages: 3 }).note
    === "none of the 3 pages has text to search (a scan, or lettering drawn as lines), so a keyword cannot be found in it"
    && P.inbMatch({ found: [], searched: 1, no_text: 1, pages: 1 }).note
    === "the page has no text to search (a scan, or lettering drawn as lines), so a keyword cannot be found in it",
    "2: no text to search: said plainly (a one-page file too), and not called a scan when it may be lettering drawn as lines",
    [P.inbMatch({ found: [], searched: 3, no_text: 3, pages: 3 }).note, P.inbMatch({ found: [], searched: 1, no_text: 1, pages: 1 }).note]);
  check(P.inbMatch({ found: [], searched: 12, no_text: 5, pages: 12 }).note
    === "5 of 12 pages have no text to search, so a keyword there cannot be found", "2: some pages with no text: counted");
  check(P.inbMatch({ found: [], searched: 300, no_text: 0, pages: 420 }).note === "only the first 300 of 420 pages were searched",
    "2: a file longer than the search reads: said");
  check(P.inbMatch(null) === null && P.inbMatch(undefined) === null, "2: no keywords set: no match line at all");
});
attempt("file lines", () => {
  const F = P.inbFileLine;
  const o = F({ name: "plans.pdf", zip: "bundle.zip", state: "opened", pages: 12, job: "abc", auto: { queued: 12, done: 12, running: false, wanted: 12, total: 12, cap: 25 } });
  check(o.name === "plans.pdf (in bundle.zip)" && o.text === "opened · 12 pages." && o.open === true && o.anyway === false
    && o.auto === "Detect ran by itself on all 12 pages.", "2: an opened plan: its name (and zip), its pages, a link, what Detect did", o);
  check(F({ name: "p.pdf", state: "opened", pages: 1, anyway: true, job: "abc" }).text === "opened by you · 1 page.", "2: opened anyway reads as the person's act");
  const g = F({ name: "p.pdf", state: "opened", pages: 2, gone: true });
  check(g.open === false && g.auto === "" && / Its project has been deleted since\.$/.test(g.text), "2: a deleted project: no link, and it says so", g);
  const s = F({ name: "p.pdf", state: "same", job: "abc", reason: "the same file came in less than a day ago; nothing new was opened" });
  check(s.text === "not opened again: the same file came in less than a day ago; nothing new was opened." && s.open === true, "2: the same file again: said, with the link to the first", s);
  const h = F({ name: "p.pdf", state: "held", why: "keywords", kept: true, reason: "none of your keywords found" }, 7);
  check(h.text === "not opened: none of your keywords found." && h.anyway === true && h.open === false, "2: a held plan offers Open anyway", h);
  const hx = F({ name: "p.pdf", state: "held", why: "exclude", kept: false, reason: "skipped by your exclusion rule" }, 7);
  check(hx.anyway === false && /no longer kept \(a plan that was not opened is kept 7 days\)/.test(hx.text), "2: ... until its days run out, and then says why not", hx);
  check(F({ name: "spec.pdf", state: "refused", reason: "reads as a text document, not drawings (40 pages, none a drawing sheet)" }).text
    === "not opened: reads as a text document, not drawings (40 pages, none a drawing sheet)", "2: a refused file: its reason");
  const sp = P.inbSplitFiles([{ name: "a.pdf", state: "opened" }, { name: "logo.png", state: "refused", reason: "not a PDF" },
    { name: "b.pdf", state: "refused", reason: "the PDF has no pages" }, { name: "n.txt", state: "refused", reason: "not a PDF" }]);
  check(sp.own.map(f => f.name).join() === "a.pdf,b.pdf" && sp.notPdf.map(f => f.name).join() === "logo.png,n.txt", "2: files that are simply not PDFs fold into one line");
});
attempt("message notes", () => {
  const N = P.inbEntryNotes;
  check(N({ status: "done", again: 0, more: 0 }).length === 0, "2: an ordinary message has no note");
  check(N({ status: "received" })[0] === "Reading the attachments…", "2: a message still being read says so");
  check(N({ status: "skipped", excluded: "mudlark paving" })[0] === "Skipped by your exclusion rule (“mudlark paving”): nothing was opened.", "2: skipped by the exclusion rule, naming the word");
  check(N({ status: "over", reason: "Over the limit of 50 messages a day, so this one was not read." })[0].startsWith("Over the limit of 50"), "2: over a limit: the server's reason is shown");
  check(N({ status: "done", again: 1 })[0] === "The mail service delivered this message 2 times. It was read once."
    && N({ status: "over", more: 3 })[0] === "And 3 more messages over the daily limit were not read."
    && N({ status: "over", more: 1 })[0] === "And 1 more message over the daily limit was not read.", "2: delivered twice, and a counted flood: both said");
  check(/looks like your mail provider's forwarding confirmation\. Its code and link are in Settings/.test(N({ status: "done", confirm: true })[0]),
    "2: a forwarding confirmation is called what it looks like (anybody can send one), with where its code and link are");
  // after the first outside review: only a message's first attachments are looked at, and a long file list is counted
  const L = { pdfs: 20, attachments: 50, unpacked_mb: 200 };
  check(N({ status: "done", attached: 20001, unread: 19951 }, L)[0]
    === "This message had 20,001 attachments. Only the first 50 of a message are looked at: the other 19,951 were not read.",
    "2: a message with more attachments than are looked at says how many it had and how many were not read",
    N({ status: "done", attached: 20001, unread: 19951 }, L));
  check(P.inbUnlisted && P.inbUnlisted({ notpdf: 4321, pdfs: 12, unpack: 3, other: 1 }, L)
    === "And 4,337 more files were not opened: 4,321 not PDFs, 12 past the limit of 20 PDFs, 3 past the unpacking limit (200 MB), 1 not read."
    && P.inbUnlisted({ notpdf: 1 }, L) === "And 1 more file was not opened: 1 not PDF."
    && P.inbUnlisted({}, L) === "" && P.inbUnlisted(null, L) === "" && P.inbUnlisted({ notpdf: 0, junk: 9, other: "x" }, L) === "",
    "2: files that are not listed one by one are counted in one line, by why they were not opened",
    P.inbUnlisted && P.inbUnlisted({ notpdf: 4321, pdfs: 12, unpack: 3, other: 1 }, L));
  check(N({ status: "done", unlisted: { notpdf: 29 } }, L)[0] === "And 29 more files were not opened: 29 not PDFs."
    && N({ status: "done", unlisted: {} }, L).length === 0 && N({ status: "done", attached: 3, unread: 0 }, L).length === 0,
    "2: ... under the message; an ordinary message still has no note");
});
attempt("card note", () => {
  const C = P.inbCardNote;
  check(C(null) === null && C(undefined) === null, "2: a project that did not come by e-mail has no note");
  const all = C({ subject: "Invitation to bid: Elm Street", queued: 12, wanted: 12, pages: 12, cap: 25, found: [] });
  check(all.text === "from e-mail: Invitation to bid: Elm Street" && all.more === "", "2: the card carries 'from e-mail: <subject>'", all);
  const cap = C({ subject: "Big set", queued: 25, wanted: 60, pages: 60, cap: 25, found: ["quartzline"] });
  check(cap.more === "E-mail detects up to 25 pages by itself: 25 of 60 here. Open it and press Detect all for the rest."
    && cap.title === "Big set\n" + cap.more + "\nYour keywords found in it: quartzline.", "2: a capped set: the card says how many and how to do the rest", cap);
  check(C({ subject: "s", queued: 4, wanted: 4, pages: 9, cap: 25 }).more
    === "Detect started by itself on 4 of 9 pages, the ones that read as plans. Open it and press Detect on any other you need.", "2: some pages not plans: said");
  check(C({ subject: "", queued: 0, wanted: 0, pages: 3, cap: 25 }).text === "from e-mail: (no subject)"
    && C({ subject: "", queued: 0, wanted: 0, pages: 3, cap: 25 }).more === "Detect did not start by itself. Open it and press Detect.", "2: no subject, nothing queued: both said");
});
attempt("limits and lists", () => {
  const L = P.inbLimitsLine({ message_mb: 35, pdfs: 20, pdf_pages: 300, per_day: 50, auto_pages: 25, held_days: 7 });
  check(L === "One message can carry up to 35 MB and 20 PDFs; a PDF up to 300 pages; 50 messages a day. Detect runs by itself on up to 25 pages of a set. For anything bigger, upload the PDF with + New project.",
    "2: the limits are said out loud, from the server's own numbers", L);
  check(/Only PDF plans are kept: the message text is not stored\.$/.test(P.INB_HOW) && /Forward an invitation to bid to this address/.test(P.INB_HOW),
    "2: one line on how to forward, and what is kept");
  check(JSON.stringify(P.inbWords(" quartzline \r\n\r\nblue marrow pipe\n  \nvellumgrade")) === '["quartzline","blue marrow pipe","vellumgrade"]',
    "2: a word box is one keyword or phrase per line");
});

// ---- a stand-in DOM, enough for the two renderers -----------------------------------------------
class El {
  constructor(tag) { this.tag = tag; this.kids = []; this._t = ""; this.hidden = false; this.className = ""; this.value = ""; this.disabled = false; }
  append(...xs) { for (const x of xs) this.kids.push(typeof x === "string" ? { text: x } : x); }
  appendChild(x) { this.kids.push(x); return x; }
  set textContent(v) { this._t = String(v); this.kids = []; }
  get textContent() { return this._t + this.kids.map(k => k.text !== undefined ? k.text : k.textContent).join(""); }
  set innerHTML(v) { this._t = ""; this.kids = []; }
  get innerHTML() { return ""; }
  setAttribute() {}
  focus() {} select() {}
  all(pred, out = []) { for (const k of this.kids) if (k instanceof El) { if (pred(k)) out.push(k); k.all(pred, out); } return out; }
}
function world(reply, status = 200) {
  const els = {}, posts = [], timers = [], opened = [], goto_ = [];
  const w = {
    els, posts, timers, opened, goto_, reply, status, pvTab: "mine", cards: 0,
    $: (id) => els[id] || (els[id] = new El("#" + id)),
    document: { createElement: (t) => new El(t), visibilityState: "visible", activeElement: null },
    fetch: async (url, opt) => {
      if (opt && opt.method === "POST") { posts.push([url, JSON.parse(opt.body)]); return { ok: true, status: 200, json: async () => w.postReply || {} }; }
      w.gets = (w.gets || 0) + 1;
      if (url === "/api/projects") { w.cards++; return { ok: true, status: 200, json: async () => ({ projects: [] }) }; }
      const m = /^\/api\/job\/([^/]+)\/open$/.exec(url);
      if (m) return { ok: true, status: 200, json: async () => ({ job_id: m[1] }) };
      return { ok: w.status === 200, status: w.status, json: async () => w.reply };
    },
  };
  return w;
}
function listCode(w) {
  const src = cut("// INBOUND-PURE-BEGIN", "// INBOUND-PURE-END") + cut("// ---------- FROM E-MAIL: the list on the Projects page", "// THE PULL ITSELF");
  return new Function("$", "document", "fetch", "W",
    "const projAgo = (now, at) => 'T' + Math.round(now - at); const nWord = (n, a, b) => n + ' ' + (n === 1 ? a : (b || a + 's'));"
    + "const state = { job: null, page: 0, kinds: [] }; const pvIsOpen = () => true; let pvTab = W.pvTab;"
    + "const setTimeout = (fn, ms) => { W.timers.push(ms); return 1; }; const clearTimeout = () => {};"
    + "const setStatus = (t) => { W.said = t; }; const svGoto = (h) => { W.goto_.push(h); };"
    + "const showProjects = () => {}; const selectPage = () => {}; const renderProjects = () => {};"
    + "const applyOpened = (j, p) => { W.opened.push([j.job_id, p]); };"
    + src + "; return { inbLoad, inbRender, inbOpenPage, inbOpenAnyway, setTab: (t) => { pvTab = t; }, data: () => inbData };")(w.$, w.document, w.fetch, w);
}
const REPLY = {
  enabled: true, can_address: true, address: "0123456789abcdef0123456789abcdef+aaaaaaaaaaaaaaaaaaaaaaaaaa@inbound.postmarkapp.com",
  rules: { keywords: ["quartzline"], exclude: ["mudlark paving"], v: 3 },
  limits: { message_mb: 35, pdf_pages: 300, per_day: 50, pdfs: 20, auto_pages: 25, held_days: 7, words: 40, word_max: 60 },
  confirm: null, total: 5, busy: true,
  entries: [
    { id: "e5", at: Date.now() / 1000 - 60, from: "Pat Sender <pat@sender.example>", subject: "Invitation to bid: Elm Street", status: "done",
      reason: null, excluded: null, again: 0, more: 0, confirm: false, pending: [],
      files: [
        { n: 0, name: "logo.png", zip: null, state: "refused", reason: "not a PDF", pages: null, anyway: false },
        { n: 1, name: "site-plans.pdf", zip: null, state: "opened", reason: null, pages: 12, anyway: false, job: "job111111111",
          auto: { queued: 12, done: 5, running: true, failed: 0, wanted: 12, total: 12, cap: 25 },
          match: { found: [{ word: "quartzline", pages: [0, 3], count: 2 }], searched: 12, no_text: 2, pages: 12 } },
        { n: 2, name: "specs.pdf", zip: null, state: "refused", reason: "reads as a text document, not drawings (40 pages, none a drawing sheet)", pages: 40, anyway: false,
          match: { found: [{ word: "quartzline", pages: [8], count: 1 }], searched: 40, no_text: 0, pages: 40 } },
        { n: 3, name: "details.pdf", zip: "bundle.zip", state: "held", why: "keywords", kept: true, reason: "none of your keywords found", pages: 3, anyway: false,
          match: { found: [], searched: 3, no_text: 3, pages: 3 } }] },
    { id: "e4", at: Date.now() / 1000 - 600, from: "bids@rival-gc.example", subject: "ITB from Mudlark Paving", status: "skipped",
      reason: null, excluded: "mudlark paving", again: 1, more: 0, confirm: false, pending: [],
      files: [{ n: 0, name: "a.pdf", zip: null, state: "held", why: "exclude", kept: true, reason: "skipped by your exclusion rule", pages: 1, anyway: false },
              { n: 1, name: "b.pdf", zip: null, state: "held", why: "exclude", kept: true, reason: "skipped by your exclusion rule", pages: 1, anyway: false }] },
    { id: "e3", at: Date.now() / 1000 - 6000, from: "x@y.example", subject: "third", status: "received", reason: null, excluded: null, again: 0, more: 0,
      confirm: false, pending: ["waiting.pdf"], files: [] },
    { id: "e2", at: Date.now() / 1000 - 60000, from: "x@y.example", subject: "fourth, not shown until asked", status: "done", reason: "No PDF was attached.", excluded: null,
      again: 0, more: 0, confirm: false, pending: [], files: [] }],
};

// ---- 3. the list ---------------------------------------------------------------------------------
(async () => {
  try {
    // OFF: the route answers 404
    let w = world(null, 404);
    let L = listCode(w);
    await L.inbLoad();
    check(L.data() === null && w.$("pvInbox").hidden === true && w.$("pvInbox").kids.length === 0 && w.timers.length === 0 && w.gets === 1,
      "3: feature off (the route answers 404): nothing is drawn and the page never asks again", { hidden: w.$("pvInbox").hidden, timers: w.timers, gets: w.gets });
    // a view-only account with no messages: nothing either
    w = world({ enabled: true, can_address: false, address: null, rules: null, limits: REPLY.limits, confirm: null, entries: [], total: 0, busy: false });
    L = listCode(w);
    await L.inbLoad();
    check(w.$("pvInbox").hidden === true, "3: an account that may not have an address and has no messages sees nothing");
    // an account that has not asked for an address yet: one line that says how
    w = world(Object.assign({}, REPLY, { address: null, entries: [], total: 0, busy: false }));
    L = listCode(w);
    await L.inbLoad();
    const hint = w.$("pvInbox");
    check(hint.hidden === false && /forward an invitation to bid to your own bid address/.test(hint.textContent) && /Get your bid address/.test(hint.textContent),
      "3: no address yet: one line saying plans can come by e-mail, with the way to Settings", hint.textContent);
    hint.all(k => k.tag === "a")[0].onclick({ preventDefault() {} });
    check(w.goto_.join() === "#bidaddress", "3: ... which opens Settings on Bid address", w.goto_);
    // the list
    w = world(REPLY);
    L = listCode(w);
    await L.inbLoad();
    const box = w.$("pvInbox");
    const rows = box.all(k => /\binb-entry\b/.test(k.className));
    check(box.hidden === false && rows.length === 3 && /^From e-mail/.test(box.textContent) && /5 messages · newest first/.test(box.textContent),
      "3: the list shows the newest three, under 'From e-mail', and says how many there are", box.textContent.slice(0, 120));
    const t0 = rows[0].textContent;
    check(/^T\d+Pat Sender <pat@sender\.example>Invitation to bid: Elm Street/.test(t0), "3: each message: when, the sender, the subject", t0.slice(0, 90));
    const order = rows.map(r => r.all(k => k.className === "inb-subj")[0].textContent);
    check(order.join("|") === "Invitation to bid: Elm Street|ITB from Mudlark Paving|third", "3: newest first", order);
    const files = rows[0].all(k => /\binb-file\b/.test(k.className));
    check(files.length === 3 && /site-plans\.pdf.*opened · 12 pages\..*Detect is running by itself: 5 of 12 pages done\./.test(files[0].textContent),
      "3: an opened file: its name, what became of it, and Detect's progress", files[0].textContent);
    check(/Your keywords in it: quartzline page 1, page 4\. 2 of 12 pages have no text to search, so a keyword there cannot be found\./.test(files[0].textContent),
      "3: its keywords with their pages (as the page strip numbers them), and how many pages could not be searched", files[0].textContent);
    check(/specs\.pdf.*not opened: reads as a text document/.test(files[1].textContent) && /quartzline page 9/.test(files[1].textContent)
      && files[1].all(k => k.tag === "button").length === 0, "3: a file that was not opened: its reason; its keyword pages are not links", files[1].textContent);
    check(/details\.pdf \(in bundle\.zip\).*not opened: none of your keywords found\./.test(files[2].textContent)
      && /None of the 3 pages has text to search \(a scan, or lettering drawn as lines\)/.test(files[2].textContent),
      "3: a held scan says 'none of your keywords found' AND that it had no text to search", files[2].textContent);
    check(/Not opened, not a PDF: logo\.png\./.test(rows[0].textContent), "3: files that are not PDFs are listed by name in one line");
    const open = files[0].all(k => k.className === "inb-open")[0];
    await open.onclick();
    const pages = files[0].all(k => k.className === "inb-page");
    await pages[1].onclick();
    check(JSON.stringify(w.opened) === '[["job111111111",null],["job111111111",3]]',
      "3: the file's name opens its project; a keyword's page opens it ON that page", w.opened);
    const anyway = files[2].all(k => /inb-anyway/.test(k.className))[0];
    w.reply = REPLY;
    await anyway.onclick();
    check(anyway.textContent === "Opening…" && JSON.stringify(w.posts[0]) === '["/api/inbound/open",{"entry":"e5","n":3}]',
      "3: Open anyway posts the message and the file's number", w.posts);
    check(/Skipped by your exclusion rule \(“mudlark paving”\): nothing was opened\./.test(rows[1].textContent)
      && /delivered this message 2 times/.test(rows[1].textContent), "3: a skipped message says which rule, and that it came twice", rows[1].textContent);
    const all = rows[1].all(k => /inb-anyway/.test(k.className));
    check(all.length === 3 && all[0].textContent === "Open all 2 anyway", "3: a skipped message offers Open anyway for all its plans and for each", all.map(a => a.textContent));
    await all[0].onclick();
    check(JSON.stringify(w.posts[w.posts.length - 1]) === '["/api/inbound/open",{"entry":"e4"}]', "3: ... the whole message in one post", w.posts);
    check(/Reading the attachments…/.test(rows[2].textContent) && /waiting\.pdf — being read…/.test(rows[2].textContent), "3: a message still being read lists what is waiting");
    check(w.timers.length >= 1 && w.timers[0] === 3000, "3: while something is still being read or detected the list looks again every 3 s", w.timers);
    const more = box.all(k => /inb-more/.test(k.className))[0];
    check(more && more.textContent === "Show all 4 (the newest of 5)", "3: older messages are one press away, and the count is honest", more && more.textContent);
    more.onclick();
    check(w.$("pvInbox").all(k => /\binb-entry\b/.test(k.className)).length === 4 && /No PDF was attached\./.test(w.$("pvInbox").textContent),
      "3: ... and a message with no PDF says so");
    L.setTab("demo");
    L.inbRender();
    check(w.$("pvInbox").hidden === true, "3: the list belongs to Your projects: hidden on the Demo tab");

    // ---- 4. Settings -----------------------------------------------------------------------------
    const svCode = (w2, data) => new Function("$", "document", "fetch", "W", "inbData0", "window", "navigator",
      cut("// INBOUND-PURE-BEGIN", "// INBOUND-PURE-END")
      + "const projAgo = (now, at) => 'T' + Math.round(now - at); const nWord = (n, a, b) => n + ' ' + (n === 1 ? a : (b || a + 's'));"
      // `inbOn`: what the server said about the feature (GET /api/config, GET /api/projects); these worlds are ON
      // unless one says otherwise
      + "let inbData = inbData0; const inbOn = W.inbOn === undefined ? true : W.inbOn;"
      + "const inbLoad = () => { W.asked = (W.asked || 0) + 1; }; const inbRender = () => {};"
      + cut("// ---------- BID ADDRESS ---", "// ---------- COMPANY ---")
      + "; return { renderSvInbound, data: () => inbData };")(w2.$, w2.document, w2.fetch, w2, data, { confirm: (q) => { w2.asked_q = q; return w2.yes !== false; } },
        { clipboard: { writeText: async (t) => { w2.copied = t; } } });
    let s = world(null);
    let V = svCode(s, null);
    V.renderSvInbound();
    check(["svGroupInbound", "svInboundSection", "svInbRulesSection", "svNavInbound"].every(id => s.$(id).hidden === true),
      "4: feature off: the Settings group, both sections and the nav link are hidden");
    s = world(null);
    V = svCode(s, { enabled: true, can_address: false, address: null, rules: null, limits: REPLY.limits, confirm: null, entries: [] });
    V.renderSvInbound();
    check(["svGroupInbound", "svInboundSection", "svInbRulesSection", "svNavInbound"].every(id => s.$(id).hidden === true),
      "4: a view-only account: hidden too");
    s = world(null);
    V = svCode(s, undefined);
    V.renderSvInbound(); V.renderSvInbound();
    check(s.asked === 1 && s.$("svInboundSection").hidden === true, "4: Settings opened first: asks the server once, shows nothing until it answers");
    // ... and only a server that takes bids by e-mail is asked (CRITIC-merge-1: the route answers 404 while it is off)
    s = world(null);
    s.inbOn = false;
    V = svCode(s, undefined);
    V.renderSvInbound(); V.renderSvInbound();
    check(!s.asked && s.$("svInboundSection").hidden === true, "4: Settings opened first on a server with the feature OFF: nothing is asked, nothing shows");
    s = world(null);
    V = svCode(s, Object.assign({}, REPLY, { address: null, rules: { keywords: [], exclude: [], v: 0 } }));
    V.renderSvInbound();
    check(s.$("svInboundSection").hidden === false && s.$("svInbAddr").hidden === true && s.$("svInbCopy").hidden === true
      && s.$("svInbNew").textContent === "Get my address" && /You have no bid address yet/.test(s.$("svInbNote").textContent),
      "4: no address yet: one button, 'Get my address'", s.$("svInbNote").textContent);
    check(/No keywords are set, so every plan that comes in by e-mail opens\./.test(s.$("svInbRulesNote").textContent),
      "4: no keywords: the page says every plan opens", s.$("svInbRulesNote").textContent);
    s.postReply = { address: "0123456789abcdef0123456789abcdef+bbbbbbbbbbbbbbbbbbbbbbbbbb@inbound.postmarkapp.com", replaced: false };
    await s.$("svInbNew").onclick();
    check(JSON.stringify(s.posts[0]) === '["/api/inbound/address",{}]' && s.asked_q === undefined
      && s.$("svInbAddr").value === s.postReply.address && s.$("svInbNew").textContent === "New address" && s.$("svInbCopy").hidden === false,
      "4: getting the first address asks nothing; the address appears with Copy and New address", [s.posts, s.$("svInbAddr").value]);
    await s.$("svInbCopy").onclick();
    check(s.copied === s.postReply.address && s.$("svInbState").textContent === "copied", "4: Copy puts the address on the clipboard and says so");
    s.yes = false;
    await s.$("svInbNew").onclick();
    check(/The one you have now stops working right away/.test(s.asked_q) && s.posts.length === 1, "4: New address asks first, in plain words; No changes nothing", s.asked_q);
    s.yes = true;
    s.postReply = { address: "0123456789abcdef0123456789abcdef+cccccccccccccccccccccccccc@inbound.postmarkapp.com", replaced: true };
    await s.$("svInbNew").onclick();
    check(s.posts.length === 2 && s.$("svInbAddr").value === s.postReply.address && /the old one no longer works/.test(s.$("svInbState").textContent),
      "4: Yes makes the new one and says the old one no longer works", s.$("svInbState").textContent);
    check(/Forward an invitation to bid to this address/.test(s.$("svInbHow").textContent) && /up to 35 MB and 20 PDFs/.test(s.$("svInbHow").textContent)
      && /the message text is not stored/.test(s.$("svInbHow").textContent), "4: one line on how to forward, what is kept, and the limits", s.$("svInbHow").textContent);
    // a mail provider's forwarding confirmation: its code and its link, named for the provider - and none of its
    // words, even if a reply carried some (the server keeps none since the first outside review)
    s = world(null);
    V = svCode(s, Object.assign({}, REPLY, { confirm: { provider: "Gmail", text: "WORDS-OF-THE-MESSAGE please click the link", code: "716599842",
      link: "https://mail-settings.google.com/mail/vf-made-up", at: Date.now() / 1000 - 120 } }));
    V.renderSvInbound();
    const c = s.$("svInbConfirm");
    check(c.hidden === false && /^A Gmail forwarding confirmation came in T\d+\. If you just added this address as a forwarding address in Gmail, use the code or the link there to finish\. If you did not, ignore it\./.test(c.textContent)
      && /Gmail confirmation code: 716599842/.test(c.textContent) && /Gmail confirmation link$/.test(c.textContent),
      "4: a mail provider's confirmation: a confirmation code and a confirmation link, with the provider's name, and what to do if you did not ask for it", c.textContent);
    check(!/WORDS-OF-THE-MESSAGE/.test(c.textContent) && c.all(k => k.tag === "pre").length === 0 && !/sv-inb-text/.test(appSrc) && !/sv-inb-text/.test(cssSrc),
      "4: none of the message's words are shown: the box has no place for text", c.textContent);
    const a = c.all(k => k.tag === "a")[0];
    check(a && a.href === "https://mail-settings.google.com/mail/vf-made-up" && a.rel === "noopener noreferrer" && a.target === "_blank"
      && a.textContent === "Gmail confirmation link",
      "4: its link opens in a new tab, with no way back into this page");
    const only = (conf) => { const x = world(null); svCode(x, Object.assign({}, REPLY, { confirm: conf })).renderSvInbound(); return x.$("svInbConfirm"); };
    check(only({ provider: "Gmail", code: "716599842", link: null, at: 1 }).all(k => k.tag === "a").length === 0
      && /Gmail confirmation code: 716599842$/.test(only({ provider: "Gmail", code: "716599842", link: null, at: 1 }).textContent)
      && only({ provider: "Gmail", code: null, link: null, text: "only words", at: 1 }).hidden === true,
      "4: a code with no link shows the code alone; neither a code nor a link shows nothing at all");
    check(svCode(world(null), REPLY) && (() => { const x = world(null); svCode(x, REPLY).renderSvInbound(); return x.$("svInbConfirm").hidden === true; })(),
      "4: no confirmation on file: the box is hidden");
    check(/A message's first 50 attachments are looked at, and its zips may unpack to 200 MB in all\./.test(
      P.inbLimitsLine({ message_mb: 35, pdfs: 20, pdf_pages: 300, per_day: 50, auto_pages: 25, held_days: 7, attachments: 50, unpacked_mb: 200 })),
      "4: the two caps on what one message may carry are said with the other limits",
      P.inbLimitsLine({ message_mb: 35, pdfs: 20, pdf_pages: 300, per_day: 50, auto_pages: 25, held_days: 7, attachments: 50, unpacked_mb: 200 }));
    // the keyword rules
    check(s.$("svInbKeywords").value === "quartzline" && s.$("svInbExclude").value === "mudlark paving"
      && /opens only when one of your 1 keyword is found in its text/.test(s.$("svInbRulesNote").textContent)
      && /kept 7 days/.test(s.$("svInbRulesNote").textContent), "4: the two lists are shown, and what they do is said", s.$("svInbRulesNote").textContent);
    check(/a scan, or lettering drawn as lines, has none/.test(s.$("svInbRulesFoot").textContent) && /Whole words or phrases; capitals do not matter/.test(s.$("svInbRulesFoot").textContent),
      "4: the page says what a search cannot see", s.$("svInbRulesFoot").textContent);
    s.$("svInbKeywords").value = "quartzline\nGranite Wren Tile\n";
    s.$("svInbKeywords").oninput();
    check(s.$("svInbRulesState").textContent === "unsaved", "4: typing in a list says unsaved");
    V.renderSvInbound();
    check(s.$("svInbKeywords").value === "quartzline\nGranite Wren Tile\n", "4: a re-render never refills a list that is being typed in");
    s.postReply = { rules: { keywords: ["quartzline", "Granite Wren Tile"], exclude: ["mudlark paving"], v: 4 }, left_out: 1 };
    await s.$("svInbRulesSave").onclick();
    check(JSON.stringify(s.posts[0]) === '["/api/inbound/rules",{"keywords":["quartzline","Granite Wren Tile"],"exclude":["mudlark paving"],"v":3}]'
      && /^saved · 2 keywords, 1 exclusion word · 1 line left out/.test(s.$("svInbRulesState").textContent) && V.data().rules.v === 4,
      "4: Save sends both lists with the version the page read, and says what was kept and what was left out", [s.posts, s.$("svInbRulesState").textContent]);
  } catch (e) {
    check(false, "the list / Settings renderers raised: " + (e && e.stack || e));
  }

  // ---- wiring ------------------------------------------------------------------------------------
  attempt("wiring", () => {
    // (since CRITIC-merge-1: through inbAsk, which asks only when GET /api/projects says the feature is on)
    check(/async function loadProjects\(\) \{[\s\S]{0,1100}inbAsk\(d \? d\.inbound : undefined\);/.test(appSrc)
      && /function inbAsk\(on\) \{[\s\S]{0,200}if \(inbOn\) return inbLoad\(\);/.test(appSrc),
      "5: opening the Projects page asks for the list, on a server that takes bids by e-mail");
    check(/pvSyncTabs\([^)]*\);\s*inbRender\(\);/.test(appSrc), "5: switching tabs redraws (and hides) the list");
    check(/if \(r\.inbound\) \{\s*const nb = inbCardNote\(r\.inbound\);/.test(appSrc) && /pv-from-mail/.test(appSrc) && /\.pv-from-mail \{/.test(cssSrc),
      "5: the project card prints the e-mail note the way it prints a bid's");
    check(/renderSvFoot\(\);\s*renderSvInbound\(\);\s*renderSvAccount\(\);/.test(appSrc), "5: Settings renders the Bid address group");
    const wait = cut("async function emailLineWait(", "// RUN-STATE-BEGIN");
    check(/else if \(st\.status === "running" && st\.email\) emailLineWait\(gen, st\);/.test(appSrc)
      && /setPageStatus\(state\.page, inbPageShown\(j\)\);\s*setStatus\(inbLineSays\(j\), "busy"\);/.test(wait)
      && /if \(!j\.email\) \{[\s\S]{0,400}return;\s*\}/.test(wait) && /if \(j\.status === "done"\) \{[\s\S]{0,160}loadResult\(gen\);/.test(wait),
      "5: a sheet in the e-mail line says which state it is in, loads its result when it lands, and stops once a person presses Detect");
    check(/res\.forEach\(\(j, p\) => setPageStatus\(p, inbPageShown\(j\)\)\);/.test(cut("async function refreshPageStatuses()", "// ---------- sheet triage")),
      "5: the page strip's badges use the same rule: waiting in the e-mail line is not 'detecting'");
    const fromMail = cut("// INBOUND-PURE-BEGIN", "// INBOUND-PURE-END") + cut("// ---------- FROM E-MAIL: the list on the Projects page", "// THE PULL ITSELF")
      + cut("// ---------- BID ADDRESS ---", "// ---------- COMPANY ---");
    check(!/innerHTML\s*=\s*[^";]*[a-zA-Z_$][\w$.]*\s*[+;]/.test(fromMail.replace(/innerHTML = "";/g, "")),
      "5: nothing from a message is ever written as HTML (every innerHTML here is an emptying)");
    const av = +(/app\.js\?v=(\d+)/.exec(htmlSrc) || [])[1], cv = +(/style\.css\?v=(\d+)/.exec(htmlSrc) || [])[1];
    check(av >= 311 && cv >= 122, "5: cache busters moved (app.js >= 311, style.css >= 122)", [av, cv]);
  });

  console.log(`\n${checks} checks, ${fails} failure(s)`);
  process.exit(fails ? 1 : 0);
})();
