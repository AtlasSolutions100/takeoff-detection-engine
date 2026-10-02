// THE DOWNLOAD LINKS AND THE UNTICKS BELONG TO THE JOB ON SCREEN (live proof 2026-10-01, B3 / B4).
//
// B3: after an Approve the host sets and shows #dlLink / #reportLink / #bidsheetLink; opening ANOTHER
//     job in the same tab without a reload left them pointing at the first job, and "Bid sheet CSV"
//     downloaded the previous job's bid sheet. applyOpened now hides them and drops their href
//     (jobLinksReset), and shows them again only from the server's answer for the job now open
//     (jobLinksSync: GET /api/job/{id}/approved `pages`).
// B4: bsIgnoreQuery sent the cross-sheet unticks of EVERY document into the bid-sheet / report
//     request; a key "<doc A> p1 :: WATER MAIN" unticked sheet 2's water main of ANY job whose page 1
//     carried that label. Only the open document's keys go now.
//
// Dependency-free:  node tests/test_bid_links_job.js
"use strict";
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); } else console.log("ok:", msg);
}
const a = js.indexOf("// BIDVIEW-RULES-BEGIN"), b = js.indexOf("// BIDVIEW-RULES-END");
check(a >= 0 && b > a, "BIDVIEW-RULES markers present");

// a stub DOM holding the three links, and a server that answers /approved per job
const els = {};
for (const id of ["dlLink", "reportLink", "bidsheetLink"])
  els[id] = { id, hidden: true, attrs: {}, set href(v) { this.attrs.href = String(v); }, get href() { return this.attrs.href || ""; },
              removeAttribute(k) { delete this.attrs[k]; }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; } };
const approved = { jobA: [0, 2], jobB: [], jobC: [1] };
const asked = [];
const state = { job: null, docName: null, page: 0, gen: 0, xsIgnore: new Set() };
const fetchStub = (u) => {
  asked.push(u);
  const m = /^\/api\/job\/([^/]+)\/approved$/.exec(u);
  return Promise.resolve({ ok: !!m, json: () => Promise.resolve({ pages: m ? (approved[m[1]] || []) : [], outdated: {} }) });
};
const H = new Function("state", "url", "$", "fetch", "store", js.slice(a, b)
  + "\nreturn { bsIgnoreQuery, bsCsvUrl, bsReportUrl, bsLinkSync, jobLinksReset, jobLinksSync };")(
  state, (u) => u, (id) => els[id] || null, fetchStub, { getItem: () => null });

// ---- B4: only the open document's unticks travel ---------------------------------------------
state.job = "jobA"; state.docName = "Site A.pdf";
state.xsIgnore.add("Site A.pdf p1 :: PROPOSED 8\" WATER MAIN");
state.xsIgnore.add("Site B.pdf p1 :: GAS LINE");
state.xsIgnore.add("Site A.pdf p1x :: junk");
check(H.bsIgnoreQuery() === "?ignore=" + encodeURIComponent("1:PROPOSED 8\" WATER MAIN"),
  `document A's request carries only A's untick (${H.bsIgnoreQuery()})`);
state.job = "jobB"; state.docName = "Site B.pdf";
check(H.bsIgnoreQuery() === "?ignore=" + encodeURIComponent("1:GAS LINE") && !/WATER/.test(H.bsCsvUrl()) && !/WATER/.test(H.bsReportUrl()),
  `document B's CSV and report links carry only B's untick, never A's (${H.bsCsvUrl()})`);
state.docName = "Other.pdf";
check(H.bsIgnoreQuery() === "", "a document with no unticks of its own sends none");

// ---- B3: the links follow the job ------------------------------------------------------------
(async () => {
  // Approve in job A: the approve handler points the three links at A and shows them
  state.job = "jobA"; state.docName = "Site A.pdf";
  els.dlLink.href = "/api/job/jobA/page/0/elements"; els.dlLink.hidden = false;
  els.reportLink.href = H.bsReportUrl(); els.reportLink.hidden = false;
  els.bidsheetLink.href = H.bsCsvUrl(); els.bidsheetLink.hidden = false;
  // open job B in the same tab, no reload: what applyOpened does
  state.job = "jobB"; state.docName = "Site B.pdf"; state.gen++;
  H.jobLinksReset();
  check(Object.values(els).every((e) => e.hidden && e.getAttribute("href") === null),
    "on open the three links are hidden and point nowhere");
  await H.jobLinksSync("jobB");
  check(Object.values(els).every((e) => e.hidden && !/jobA/.test(e.href)),
    "job B has no approved page: the links stay hidden, never pointing at job A");
  // open job C, which has an approved page 1: the links come back for C
  state.job = "jobC"; state.docName = "Site C.pdf"; state.page = 0; state.gen++;
  H.jobLinksReset();
  await H.jobLinksSync("jobC");
  check(!els.reportLink.hidden && els.reportLink.href === "/api/job/jobC/report.pdf"
        && !els.bidsheetLink.hidden && els.bidsheetLink.href === "/api/job/jobC/bidsheet.csv"
        && !els.dlLink.hidden && els.dlLink.href === "/api/job/jobC/page/1/elements",
    `a job with approved pages shows its own links (${Object.values(els).map((e) => e.href).join(", ")})`);
  check(asked.includes("/api/job/jobC/approved"), "from the server's answer for that job (GET .../approved)");
  // an answer that arrives after the operator opened yet another job is dropped
  state.job = "jobC"; H.jobLinksReset();
  const late = H.jobLinksSync("jobA");
  await late;
  check(Object.values(els).every((e) => e.hidden), "a late answer for a job no longer open changes nothing");
  // applyOpened calls both, after the document's name is set (the unticks are keyed by it)
  const ao = js.slice(js.indexOf("function applyOpened("), js.indexOf("\n}\n", js.indexOf("function applyOpened(")));
  check(ao.indexOf("jobLinksReset()") > 0 && ao.indexOf("jobLinksSync(state.job)") > ao.indexOf("state.docName = j.name"),
    "applyOpened resets the links on every open and re-shows them from the server");
  if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
  console.log("\nall job-link checks passed");
})().catch((e) => { console.error("FAIL:", e && e.stack || e); process.exit(1); });
