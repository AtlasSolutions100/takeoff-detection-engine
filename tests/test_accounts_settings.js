// SETTINGS -> ACCOUNTS (2026-09-15): staff can add, reset, promote and
// remove accounts from the browser instead of the developer doing it over
// ssh. Pins the page's contract with index.html and the pure visibility rule.
//
// run: node tests/test_accounts_settings.js   (fails on aeae460)
"use strict";
const fs = require("fs");
const path = require("path");
const appSrc = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
const htmlSrc = fs.readFileSync(path.join(__dirname, "..", "webui", "index.html"), "utf8");
const cssSrc = fs.readFileSync(path.join(__dirname, "..", "webui", "style.css"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };

// ---- markup ------------------------------------------------------------------
check(/<a href="#accounts" id="svNavAccounts" hidden>Accounts<\/a>/.test(htmlSrc), "the Settings nav has a hidden Accounts link");
check(/id="svGroupAccounts" hidden/.test(htmlSrc) && /id="svAccountsSection" hidden/.test(htmlSrc), "the Accounts group and section start hidden");
for (const id of ["svUsersNote", "svUsersTable", "svNewUser", "svNewStaff", "svNewUserBtn", "svNewUserState", "svTempBox"])
  check(new RegExp('id="' + id + '"').test(htmlSrc), "index.html has #" + id);
check(/shown here once/.test(htmlSrc) && /cannot remove yourself/.test(htmlSrc), "the section explains the once-only password and the self/last-staff rules");
const av = (/app\.js\?v=(\d+)/.exec(htmlSrc) || [])[1], cv = (/style\.css\?v=(\d+)/.exec(htmlSrc) || [])[1];
check(+av >= 175 && +cv >= 86, "cache busters moved (app.js >= 175, style.css >= 86)");

// ---- pure rule ------------------------------------------------------------------
const a = appSrc.indexOf("// SETTINGS-PURE-BEGIN"), b = appSrc.indexOf("// SETTINGS-PURE-END");
check(a >= 0 && b > a, "SETTINGS-PURE markers present");
const S = new Function(appSrc.slice(a, b) + " return { svHashTarget, svAccountsVisible };")();
check(S.svHashTarget("#accounts") === "svGroupAccounts", "#accounts lands on the Accounts group");
check(S.svHashTarget("#account") === "svGroupAccount", "#account still lands on the Account group");
check(S.svAccountsVisible({ user: "boss", staff: true, accounts: true }) === true, "visible for a signed-in staff account on a box with accounts");
check(S.svAccountsVisible({ user: "alice", staff: false, accounts: true }) === false, "hidden for a client");
check(S.svAccountsVisible({ user: null, staff: true, accounts: false }) === false, "hidden on a single-password box");
check(S.svAccountsVisible(null) === false, "hidden when the server could not be asked");

// ---- wiring -------------------------------------------------------------------------
check(/function renderSvUsers\(\)/.test(appSrc) && /renderSvAccount\(\);\s*renderSvUsers\(\);/.test(appSrc), "renderSettings renders the accounts section");
check(/typeof renderSvUsers === "function" && !\$\("settingsView"\)\.hidden\)\s*renderSvUsers\(\);/.test(appSrc), "svMeSet re-renders the accounts section when who-am-I arrives");
check(/fetch\("\/api\/users"\)/.test(appSrc), "the section reads /api/users");
for (const p of ['"/api/users/" + encodeURIComponent(u.user) + "/staff"', '"/api/users/" + encodeURIComponent(u.user) + "/reset"', 'svUserAction("DELETE", "/api/users/" + encodeURIComponent(u.user)'])
  check(appSrc.includes(p), "calls " + p.replace(/"/g, ""));
const blk = appSrc.slice(appSrc.indexOf("// ---------- ACCOUNTS (staff)"), appSrc.indexOf('$("svNewUserBtn").onclick') + 600);
check(blk.length > 1000 && !/\bconfirm\(/.test(blk) && /Really remove\?/.test(blk), "Remove is two presses, not a confirm() dialog");
check(/if \(j\.temp_password\) svShowTemp\(j\.user, j\.temp_password\);/.test(appSrc), "a returned temporary password is shown once");
check(/rm\.disabled = isMe \|\| lastStaff;/.test(appSrc) && /rb\.disabled = lastStaff;/.test(appSrc), "self and last-staff rules are mirrored in the table");
check(/\.sv-temp-pw \{/.test(cssSrc) && /\.sv-fld-check \{/.test(cssSrc), "style.css styles the temp-password box and the staff checkbox row");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all accounts-settings checks passed");
