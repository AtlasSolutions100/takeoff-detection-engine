// A session ended somewhere else (UI verify 2, 2026-10-02): webui/client.js, loaded first on every page, watches
// every answer. Once a page that WAS signed in meets a 401 (or a bounce to /login), it shows one line on top - "You
// were signed out. Sign in again." with a link to the sign-in page - and sends no more writes. It never reloads. A
// wrong password on the sign-in form is not a sign-out, and the 409 "CORE was updated" keeps its own words.
//
// Drives the real client.js against a stubbed window / document / fetch / XMLHttpRequest.
// Dependency-free:  node tests/test_signed_out.js

"use strict";
const fs = require("fs");
const path = require("path");

const SRC = fs.readFileSync(path.join(__dirname, "..", "webui", "client.js"), "utf8");
let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}
const tick = () => new Promise((r) => setImmediate(r));

function page({ pathname = "/", search = "", framed = false } = {}) {
  const sent = [];            // what reached the network
  const answers = [];         // the next answers, in order: { status, url, redirected }
  const nav = [];             // any reload / navigation the script tries
  function el(tag) {
    return {
      tag, children: [], attrs: {}, style: {}, textContent: "", id: "",
      setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return this.attrs[k]; },
      appendChild(x) { this.children.push(x); return x; },
      get text() { return this.children.map((c) => c.text !== undefined ? c.text : c.textContent || "").join("") || this.textContent; },
    };
  }
  const body = el("body");
  const document = {
    body, documentElement: el("html"),
    createElement: (t) => el(t),
    createTextNode: (s) => ({ textContent: s, text: s }),
  };
  const location = {
    href: "http://core.test" + pathname + search, pathname, search,
    reload() { nav.push("reload"); }, assign(u) { nav.push("assign " + u); }, replace(u) { nav.push("replace " + u); },
  };
  const window = {
    fetch: (input, init) => {
      const url = typeof input === "string" ? input : input.url;
      sent.push([(init && init.method) || "GET", url]);
      const a = answers.shift() || { status: 200 };
      return Promise.resolve({ status: a.status, ok: a.status >= 200 && a.status < 300,
                               url: a.url || "http://core.test" + url, redirected: !!a.redirected });
    },
  };
  window.top = framed ? {} : window;
  const xhrEvents = [];
  function XMLHttpRequest() { this.listeners = {}; }
  XMLHttpRequest.prototype.open = function (m, u) { this.m = m; this.u = u; };
  XMLHttpRequest.prototype.setRequestHeader = function () {};
  XMLHttpRequest.prototype.addEventListener = function (ev, fn) { (this.listeners[ev] = this.listeners[ev] || []).push(fn); };
  XMLHttpRequest.prototype.dispatchEvent = function (e) {
    xhrEvents.push(e.type);
    if (typeof this["on" + e.type] === "function") this["on" + e.type](e);
  };
  XMLHttpRequest.prototype.send = function () {
    sent.push(["xhr " + this.m, this.u]);
    const a = answers.shift() || { status: 200 };
    this.status = a.status; this.responseURL = a.url || "http://core.test" + this.u;
    for (const fn of this.listeners.load || []) fn();
  };
  class Headers { constructor() { this.m = {}; } set(k, v) { this.m[k] = v; } }
  class Response {
    constructor(b, o) { this.body = b; this.status = o.status; this.ok = false; }
    json() { return Promise.resolve(JSON.parse(this.body)); }
  }
  const Event = function (type) { this.type = type; };
  new Function("window", "document", "location", "navigator", "XMLHttpRequest", "Request", "Headers", "Response",
               "Event", "setTimeout", SRC)(
    window, document, location, {}, XMLHttpRequest, function Request() {}, Headers, Response, Event,
    (fn) => setImmediate(fn));
  const bar = () => body.children.filter((c) => c.id === "coreSignedOut");
  return { window, XMLHttpRequest, sent, answers, nav, bar, xhrEvents };
}

(async () => {
  // 1. signed in, then a 401: one line on top, a link to sign in, no more writes, reads still go, no reload
  {
    const P = page({ pathname: "/", search: "?x=1" });
    P.answers.push({ status: 200 }, { status: 401 });
    await P.window.fetch("/api/me"); await tick();
    check(P.bar().length === 0, "a good answer shows nothing");
    const r = await P.window.fetch("/api/jobs/j1/edits", { method: "POST" }); await tick();
    check(r.status === 401, "the 401 itself reaches the page's own code unchanged");
    const b = P.bar();
    check(b.length === 1 && b[0].attrs.role === "alert", "a stubbed 401 shows one line on top, as an alert");
    check(b.length === 1 && b[0].text === "You were signed out.Sign in again.",
      `the line reads "You were signed out." + "Sign in again." (${b.length && b[0].text})`);
    const a = b.length && b[0].children.find((c) => c.tag === "a");
    check(a && a.attrs.href === "/login?next=" + encodeURIComponent("/?x=1") && a.attrs.target === "_top",
      `the link goes to the sign-in page and back here (${a && a.attrs.href})`);
    const n = P.sent.length;
    const w = await P.window.fetch("/api/jobs/j1/edits", { method: "POST" });
    const j = await w.json();
    check(P.sent.length === n && w.status === 401 && j.detail === "You were signed out. Sign in again.",
      `a later write is not sent; it answers 401 in the same words (${JSON.stringify(j)})`);
    await P.window.fetch("/api/me"); await tick();
    check(P.sent.length === n + 1, "reads still go out (nothing loops: one read, one request)");
    P.answers.push({ status: 401 });
    await P.window.fetch("/api/recent"); await tick();
    check(P.bar().length === 1, "a second 401 does not add a second line");
    const x = new P.XMLHttpRequest();
    let err = 0; x.onerror = () => { err++; };
    x.open("POST", "/api/upload"); x.send("pdf"); await tick();
    check(P.sent.length === n + 2 && err === 1, "an upload after the sign-out is not sent; its error path runs once");
    check(P.nav.length === 0, `no reload, no navigation by itself (${P.nav.join(", ")})`);
  }
  // 2. the sign-in form's own wrong password is not a sign-out, even after a good answer
  {
    const P = page({ pathname: "/login" });
    P.answers.push({ status: 200 }, { status: 401 }, { status: 401 });
    await P.window.fetch("/api/me"); await tick();
    await P.window.fetch("/api/login", { method: "POST" }); await tick();
    await P.window.fetch("/api/login", { method: "POST" }); await tick();
    check(P.bar().length === 0 && P.sent.length === 3, "a wrong password on the sign-in form: no line, the next try still goes");
  }
  // 3. a page that never got a good answer is not told it "was" signed out
  {
    const P = page();
    P.answers.push({ status: 401 });
    await P.window.fetch("/api/me"); await tick();
    check(P.bar().length === 0, "a 401 before any good answer shows no line");
  }
  // 4. the 409 client gate keeps its own words and blocks nothing
  {
    const P = page();
    P.answers.push({ status: 200 }, { status: 409 }, { status: 200 });
    await P.window.fetch("/api/me"); await tick();
    await P.window.fetch("/api/x", { method: "POST" }); await tick();
    await P.window.fetch("/api/x", { method: "POST" }); await tick();
    check(P.bar().length === 0 && P.sent.length === 3, "a 409 shows no sign-out line and the next write still goes");
  }
  // 5. the cut / fill page: its paths are sent to /login instead of a 401; inside a frame the link signs in the tab
  {
    const P = page({ pathname: "/cutfill/", framed: true });
    P.answers.push({ status: 200 }, { status: 200, redirected: true, url: "http://core.test/login?next=%2Fcutfill%2Fapi%2Fp" });
    await P.window.fetch("api/projects"); await tick();
    await P.window.fetch("api/projects/p1/zones", { method: "PUT" }); await tick();
    const b = P.bar();
    const a = b.length && b[0].children.find((c) => c.tag === "a");
    check(b.length === 1, "a write bounced to /login shows the line");
    check(a && a.attrs.href === "/login?next=%2F" && a.attrs.target === "_top",
      `in a frame the link opens sign-in on the whole tab (${a && a.attrs.href})`);
  }
  // 6. an upload (XHR) that meets the 401 shows the line too
  {
    const P = page();
    P.answers.push({ status: 200 }, { status: 401 });
    await P.window.fetch("/api/me"); await tick();
    const x = new P.XMLHttpRequest();
    x.open("POST", "/api/upload"); x.send("pdf"); await tick();
    check(P.bar().length === 1, "an upload answered 401 shows the line");
  }
  if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
  console.log("\nall signed-out checks passed");
})();
