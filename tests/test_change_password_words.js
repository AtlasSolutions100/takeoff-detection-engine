// The Change password page says only what is true (UI verify 2, 2026-10-02). It used to tell everyone "You are
// signed in with a temporary password that somebody else chose for you" - untrue for a user changing their own.
// The server has two cases: a forced first change after staff issued a one-time password (/api/me must_change true)
// and a change the user chose (must_change false). Before /api/me answers, the words must be true in both.
// Runs the page's own inline script against a stub document.  Dependency-free:  node tests/test_change_password_words.js

"use strict";
const fs = require("fs");
const path = require("path");

const html = fs.readFileSync(path.join(__dirname, "..", "webui", "change-password.html"), "utf8");
let failures = 0;
function check(cond, msg) {
  if (!cond) { failures++; console.error("FAIL:", msg); }
  else console.log("ok:", msg);
}
const textOf = (s) => s.replace(/<!--[\s\S]*?-->/g, "").replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();

const why0 = textOf((html.match(/<p class="why" id="why">([\s\S]*?)<\/p>/) || [])[1] || "");
check(why0 === "Pick a new password for your account. Use at least 8 characters.",
  `before /api/me answers, the words are true in both cases ("${why0}")`);
check(!/somebody else|temporary password/i.test(textOf(html.slice(html.indexOf("<body>"), html.indexOf("<script>", html.indexOf("<body>"))))),
  "the page no longer claims somebody else chose the password");
check(/<div class="beta">[^<]*Double-check every quantity/.test(html), "the accuracy flag bar stays on the page");

const script = html.slice(html.lastIndexOf("<script>") + 8, html.lastIndexOf("</script>"));
async function run(me) {
  const els = {};
  const get = (id) => els[id] || (els[id] = {
    id, textContent: id === "why" ? why0 : "", value: "", disabled: false,
    classList: { add() {}, remove() {} }, focus() {},
  });
  const document = { getElementById: get };
  const fetch = () => Promise.resolve({ ok: !!me, json: () => Promise.resolve(me) });
  new Function("document", "fetch", "location", script)(document, fetch, { href: "" });
  for (let i = 0; i < 5; i++) await new Promise((r) => setImmediate(r));
  return get("why").textContent;
}

(async () => {
  const forced = await run({ user: "est2@x.test", must_change: true });
  check(/one-time password that was given to you/.test(forced) && /Pick your own/.test(forced),
    `forced first change: says the password was given to them ("${forced}")`);
  const own = await run({ user: "est1@x.test", must_change: false });
  check(!/given to you|somebody else|one-time|temporary/i.test(own) && /Pick a new password/.test(own),
    `a change the user chose: no claim that someone else picked it ("${own}")`);
  check(/signed out everywhere else/.test(own), "it says what saving does to the other places they are signed in");
  const none = await run(null);
  check(none === why0, "no answer from /api/me: the words true in both cases stay");
  if (failures) { console.error(`\n${failures} FAILURE(S)`); process.exit(1); }
  console.log("\nall change-password word checks passed");
})();
