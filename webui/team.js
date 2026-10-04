"use strict";
/* ==========================================================================
   team.js - the Team page (REDESIGN, docs/REDESIGN.md)
   --------------------------------------------------------------------------
   Workspace -> Team: who can sign in, what each person may do, and what
   they did. Built on the server's account API (the same one Settings ->
   Accounts has always used, extended with roles, last-active and an audit
   log). Every number on the page is the server's; nothing is invented:

     GET  /api/me                      who I am, my role
     GET  /api/users                   the members (admins only; 404 else,
                                       409 when the server has no accounts)
     POST /api/users                   add a member -> a one-time password
     POST /api/users/{u}/role          admin / estimator / viewer
     POST /api/users/{u}/reset         a fresh one-time password
     DELETE /api/users/{u}             remove
     GET  /api/team/activity           the audit log (admins only)
     GET  /api/branding                the company the reports carry

   The page is a full view like Projects and Settings (main.tv-open), and
   the three are exclusive. Loaded after app.js and shell.js.
   ========================================================================== */
(() => {
  if (window.coreTeam) return;
  const byId = (id) => document.getElementById(id);
  const F = (n) => (typeof window[n] === "function" ? window[n] : null);
  const main = document.querySelector("main");
  const header = document.querySelector("header");
  if (!main || !header) return;

  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === "class") n.className = v;
      else if (k.startsWith("on") && typeof v === "function") n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v === true ? "" : String(v));
    }
    for (const c of kids.flat(3)) {
      if (c == null || c === false) continue;
      n.append(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return n;
  }
  function icon(inner, cls) {
    const s = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
    if (cls) s.setAttribute("class", cls);
    s.innerHTML = inner;
    return s;
  }
  const ICON_TEAM = '<circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3.2 2.9-5 5.5-5s4.9 1.8 5.5 5"/><circle cx="17" cy="9" r="2.5"/><path d="M15.5 14.2c2.4.1 4.3 1.7 4.9 4.8"/>';
  const ICON_PLUS = '<path d="M12 5v14M5 12h14"/>';
  const ICON_COPY = '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3"/>';

  /* ------------------------------------------------------------- facts */
  const ROLES = {
    admin: { name: "Admin", short: "the whole team and every project",
             does: "Everything an estimator does, plus the team: add people, change roles, reset passwords. Sees every project on this server." },
    estimator: { name: "Estimator", short: "uploads, detects, prices and approves",
                 does: "Uploads plan sets, runs Detect, measures, prices and approves takeoffs on their own and shared projects." },
    viewer: { name: "Viewer", short: "opens and exports - read-only",
              does: "Opens and exports the projects they can see. Read-only: cannot upload, edit or approve." },
  };
  const roleOf = (u) => (u && ROLES[u.role]) ? u.role : (u && u.staff ? "admin" : "estimator");
  function initials(user) {
    const local = String(user || "?").split("@")[0];
    const parts = local.split(/[._\-\s]+/).filter(Boolean);
    const s = parts.length > 1 ? parts[0][0] + parts[1][0] : local.slice(0, 2);
    return s.toUpperCase();
  }
  function ago(iso) {
    if (!iso) return "never";
    const t = Date.parse(iso);
    if (!isFinite(t)) return "—";
    const s = Math.max(0, (Date.now() - t) / 1000);
    if (s < 90) return "just now";
    if (s < 3600) return `${Math.round(s / 60)} min ago`;
    if (s < 86400) return `${Math.round(s / 3600)} h ago`;
    if (s < 2 * 86400) return "yesterday";
    if (s < 14 * 86400) return `${Math.round(s / 86400)} days ago`;
    return new Date(t).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  }
  const PHRASE = {
    project_upload: (e) => ["uploaded", e.name],
    project_open_path: (e) => ["opened", e.name],
    demo_open: (e) => ["opened the demo", e.name],
    sheet_detect: (e) => ["ran Detect on", e.name, e.page != null ? `page ${e.page + 1}` : null],
    sheet_approve: (e) => ["approved", e.name, e.page != null ? `page ${e.page + 1}` : null],
    project_reset: (e) => ["reset", e.name],
    project_delete: (e) => ["removed", e.name],
    user_add: (e) => ["added", e.name, e.detail ? `as ${(ROLES[e.detail] || {}).name || e.detail}` : null],
    user_remove: (e) => ["removed the account", e.name],
    user_reset_password: (e) => ["reset the password for", e.name],
    user_role: (e) => ["changed the role of", e.name, e.detail ? String(e.detail).replace("->", " → ") : null],
    login: () => ["signed in"],
    // the fork's sign-in limit (92c8e40) logs each failed sign-in with user null and the account tried, or
    // "(no such account)" - never what was typed: "Someone failed to sign in as x@company.com"
    login_failed: (e) => (e.name && e.name !== "(no such account)"
      ? ["failed to sign in as", e.name] : ["failed to sign in with a name that is no account"]),
    // an approval taken out of the bid, and an earlier version's approval put back in it (the takeoff starts empty,
    // merged 2026-10-04; server.py withdraw_approval / put_back_approval)
    sheet_withdraw: (e) => ["withdrew the approval of", e.name, e.page != null ? `page ${e.page + 1}` : null],
    sheet_putback: (e) => ["put the earlier approval back on", e.name, e.page != null ? `page ${e.page + 1}` : null],
    // BIDS BY E-MAIL (takeoff_engine/inbound.py, merged 2026-10-04): ten actions. Nobody is signed in when mail
    // arrives, so `user` is the account whose bid address the message came to (null when no account could be named:
    // the line then starts "The bid mailbox", MAILBOX below); `name` is the message's subject or a file's name; `detail`
    // is the server's own sentence for what happened. A message's text is never in the log.
    inbound_message: (e) => ["got an e-mail at their bid address", e.name, e.detail],
    inbound_project: (e) => ["got a project from e-mail", e.name, e.detail],
    inbound_file_refused: (e) => ["was sent a file that was not opened", e.name, e.detail],
    inbound_file_held: (e) => ["was sent a plan their keyword rules held", e.name, e.detail],
    inbound_file_same: (e) => ["was sent a plan they already had", e.name, e.detail],
    inbound_again: (e) => ["was sent the same e-mail again", e.name, e.detail],
    inbound_refused: (e) => (e.user ? ["was sent an e-mail that was not read", e.name, e.detail]
      : ["did not read a message", e.name, e.detail, MAILBOX]),
    inbound_unknown_address: (e) => ["got an e-mail for an address no account has", e.name, e.detail, MAILBOX],
    inbound_address_new: (e) => ["made a bid address", e.name, e.detail],
    inbound_rules: (e) => ["saved their e-mail keyword rules", e.name, e.detail],
  };
  // who a line is about when no account can be named (an e-mail turned away at the door, or sent to no account's address)
  const MAILBOX = "The bid mailbox";
  // -> [verb, what, extra, who]: `who` only when the line is not about the account in `user`
  function phrase(e) {
    const f = PHRASE[e.action];
    if (f) return f(e);
    return [String(e.action || "").replace(/_/g, " "), e.name, e.detail];
  }

  /* -------------------------------------------------------- the view */
  const view = h("section", { id: "teamView", class: "team-view", hidden: true, "aria-label": "Team" });
  main.append(view);
  const link = h("a", { id: "teamLink", class: "hdr-icon team-link", href: "#team", title: "Team - who can sign in and what they may do",
    "aria-label": "Team" }, icon(ICON_TEAM));
  const settingsLink = byId("settingsLink");
  if (settingsLink) settingsLink.before(link); else header.append(link);

  const isOpen = () => !view.hidden;
  let backTo = null;           // what to return to on close: "sheet" | "projects"
  function show(on) {
    on = !!on;
    if (on === isOpen()) { if (on) render(); return; }
    if (on) {
      backTo = (window.state && window.state.job) ? "sheet" : "projects";
      // exclusive with Projects and Settings (their raw toggles)
      if (F("pvIsOpen") && pvIsOpen() && F("showProjects")) showProjects(false);
      if (F("svIsOpen") && svIsOpen() && F("showSettings")) showSettings(false);
      if (F("toolsMenuClose")) toolsMenuClose();
    }
    view.hidden = !on;
    main.classList.toggle("tv-open", on);
    // recompute body.view-open through app.js's own function (wrapped
    // below to count Team as a view) - opening AND closing
    if (F("viewChromeSync")) viewChromeSync(); else paintLink();
    if (on) {
      render();
      try { history.replaceState(null, "", "#team"); } catch (e) { /* convenience only */ }
    } else {
      try { if (location.hash === "#team") history.replaceState(null, "", location.pathname + location.search); } catch (e) {}
      if (F("syncBuffer")) syncBuffer();
    }
  }
  function paintLink() {
    const on = isOpen();
    if (on) document.body.classList.add("view-open");
    if (on) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  }
  // app.js recomputes body.view-open from Projects / Settings only - keep
  // Team in it; and opening either of those (or a document) closes Team
  const wrap = (name, fn) => {
    const orig = F(name);
    if (!orig || orig.__team) return;
    const w = function (...a) { return fn(orig, this, a); };
    w.__team = true;
    window[name] = w;
  };
  // (paintLink never calls back into viewChromeSync - no loop)
  wrap("viewChromeSync", (orig, self, a) => { const r = orig.apply(self, a); paintLink(); return r; });
  wrap("showProjects", (orig, self, a) => { if (a[0] && isOpen()) show(false); return orig.apply(self, a); });
  wrap("showSettings", (orig, self, a) => { if (a[0] && isOpen()) show(false); return orig.apply(self, a); });
  wrap("applyOpened", (orig, self, a) => { if (isOpen()) show(false); return orig.apply(self, a); });
  link.addEventListener("click", (e) => { e.preventDefault(); show(!isOpen()); if (!isOpen()) back(); });
  function back() {
    if (backTo === "projects" && F("showProjects")) showProjects(true);
  }
  addEventListener("hashchange", () => { if (location.hash === "#team") show(true); });
  // The sheet is hidden behind this page, so its single-key tools, Delete
  // and undo must not reach it - the same swallow app.js does for Projects
  // and Settings (SV_SWALLOW_KEYS). Found by the peer session's critic.
  const SWALLOW = new Set(["m", "f", "l", "s", "x", "e", "d", "i", "j", "r", "t", "?", "Delete", "Backspace"]);
  addEventListener("keydown", (e) => {
    if (!isOpen()) return;
    const t = e.target;
    if (t && (t.tagName === "INPUT" || t.tagName === "SELECT" || t.tagName === "TEXTAREA" || t.isContentEditable)) return;
    if (e.key !== "Escape") {
      if (SWALLOW.has(e.key) || SWALLOW.has(e.key.toLowerCase())
          || ((e.ctrlKey || e.metaKey) && (e.key.toLowerCase() === "z" || e.key.toLowerCase() === "y")))
        e.stopImmediatePropagation();
      return;
    }
    if (byId("teamModal")) return;                 // the dialog closes first
    const um = byId("userMenu");
    if (um && !um.hidden) return;                  // ...and the account menu
    e.preventDefault(); e.stopImmediatePropagation();
    show(false); back();
  }, true);
  // the Settings page links to it (its Accounts section stays, unchanged)
  const svNav = byId("svNav");
  if (svNav) svNav.prepend(h("a", { href: "#team", class: "sv-team-link", onclick: (e) => { e.preventDefault(); show(true); } }, "Team"));
  const accSection = byId("svAccountsSection");
  if (accSection) accSection.prepend(h("div", { class: "sv-note sv-team-note" },
    "The ", h("a", { href: "#team", onclick: (e) => { e.preventDefault(); show(true); } }, "Team page"),
    " shows the same accounts with roles, last activity and an activity log."));

  /* ------------------------------------------------------ data + render */
  async function getJSON(url, opts) {
    const r = await fetch(url, opts);
    let body = null;
    try { body = await r.json(); } catch (e) { /* not JSON */ }
    return { ok: r.ok, status: r.status, body };
  }
  // a refused team action in words: a 401 is the session ended elsewhere - client.js's own sentence, not the
  // server's bare "not signed in" (the first write after a sign-out still reaches the server and gets that)
  const failWords = (r, dflt) => (r.status === 401 ? "You were signed out. Sign in again."
    : (r.body && r.body.detail) || dflt);
  let gen = 0;
  async function render() {
    const my = ++gen;
    view.innerHTML = "";
    const [me, brand] = await Promise.all([getJSON("/api/me"), getJSON("/api/branding")]);
    // /api/users answers 409 on a server with no accounts (and 404 to a
    // non-admin) - ask only when there is a team to list, so a laptop with
    // no accounts does not log a failed request on every visit.
    const mayList = !!(me.body && me.body.accounts && (me.body.staff || me.body.role === "admin"));
    const users = mayList ? await getJSON("/api/users") : { ok: false, status: 0, body: null };
    if (my !== gen) return;
    // the session ended (a sign-out or password change elsewhere: fork 92c8e40 / client.js v2). Not "no accounts on
    // this server": the same words as client.js's line on top, and nothing else to act on
    if (me.status === 401) {
      view.append(h("div", { class: "vw-head tm-head" }, h("div", { class: "vw-title" },
        h("div", { class: "tm-crumb" }, "Workspace"), h("h1", null, "Team"))),
        h("div", { class: "tm-empty", role: "note" }, "You were signed out. ",
          h("a", { href: "/login?next=" + encodeURIComponent("/#team") }, "Sign in again.")));
      return;
    }
    const meB = me.body || {};
    const company = brand.body && brand.body.company ? brand.body.company : "";
    const accounts = !!meB.accounts;
    const isAdmin = users.ok;
    const list = isAdmin ? ((users.body && users.body.users) || []) : [];
    const myRole = roleOf({ role: meB.role, staff: meB.staff });

    const head = h("div", { class: "vw-head tm-head" },
      h("div", { class: "vw-title" },
        h("div", { class: "tm-crumb" }, "Workspace"),
        h("h1", null, "Team"),
        h("div", { class: "vw-meta" }, [company || "This workspace",
          accounts ? (isAdmin ? `${list.length} member${list.length === 1 ? "" : "s"}` : `signed in as ${meB.user}`)
                   : "no accounts on this server"].join(" · "))),
      h("div", { class: "vw-acts" },
        isAdmin ? h("button", { type: "button", class: "btn primary", id: "tmAdd", onclick: addDialog },
          icon(ICON_PLUS), "Add member") : null));
    const grid = h("div", { class: "tm-grid" });
    view.append(head, grid);

    const left = h("div", { class: "tm-col" }), right = h("div", { class: "tm-col" });
    grid.append(left, right);

    // MEMBERS
    const members = h("section", { class: "vw-card tm-members" },
      h("div", { class: "vw-card-head" }, h("h2", null, "Members"),
        isAdmin ? h("span", { class: "vw-note" }, "roles take effect on the next request") : null));
    left.append(members);
    if (!accounts) {
      members.append(h("div", { class: "tm-empty" },
        h("p", null, h("b", null, "No user accounts yet."),
          " Everyone who can reach this server signs in with one shared password (a local install may have none). Accounts give each person their own sign-in, a role and an activity trail."),
        h("p", null, "The first admin is created on the server itself, so the shared password alone can never make one:"),
        h("code", { class: "tm-cmd" }, "python -m takeoff_engine.users add you@company.com --role admin"),
        h("p", { class: "tm-dim" }, "Admins then add everyone else from this page.")));
    } else if (!isAdmin) {
      members.append(h("div", { class: "tm-table-wrap" }, h("table", { class: "tm-table" },
        h("thead", null, h("tr", null, h("th", null, "Name"), h("th", null, "Role"), h("th", null, "What it can do"))),
        h("tbody", null, memberRow({ user: meB.user, role: meB.role, staff: meB.staff, must_change: meB.must_change }, false, meB.user)))),
        h("p", { class: "tm-dim tm-pad" }, "Only admins see the whole team. Ask an admin to change your role."));
    } else {
      const tbl = h("table", { class: "tm-table" },
        h("thead", null, h("tr", null,
          h("th", null, "Name"), h("th", null, "Role"), h("th", { class: "num" }, "Projects"),
          h("th", null, "Last active"), h("th", null, h("span", { class: "sr-only" }, "Actions")))),
        h("tbody", null, list.map((u) => memberRow(u, true, users.body.me))));
      members.append(h("div", { class: "tm-table-wrap" }, tbl));
    }

    // ROLES
    right.append(h("section", { class: "vw-card tm-roles" },
      h("div", { class: "vw-card-head" }, h("h2", null, "Roles"),
        h("span", { class: "vw-note" }, "enforced by the server")),
      h("dl", { class: "tm-dl" }, Object.entries(ROLES).map(([k, r]) => [
        h("dt", null, h("span", { class: "tm-pill r-" + k }, r.name), k === myRole && accounts ? h("span", { class: "tm-you" }, "you") : null),
        h("dd", null, r.does)]))));

    // WORKSPACE
    right.append(h("section", { class: "vw-card tm-ws" },
      h("div", { class: "vw-card-head" }, h("h2", null, "Workspace")),
      h("dl", { class: "tm-kv" },
        h("dt", null, "Company on reports"), h("dd", null, company || h("span", { class: "tm-dim" }, "not set")),
        h("dt", null, "Sign-in"), h("dd", null, accounts ? "one account per person" : "shared password / none"),
        h("dt", null, "Unit prices"), h("dd", null, "kept in each browser (Settings → Pricing)")),
      h("div", { class: "tm-ws-acts" },
        h("a", { href: "#company", class: "tm-link", onclick: (e) => { e.preventDefault(); goSettings("#company"); } }, "Company settings"),
        h("a", { href: "#pricing", class: "tm-link", onclick: (e) => { e.preventDefault(); goSettings("#pricing"); } }, "Price book"),
        h("a", { href: "#bidsheet", class: "tm-link", onclick: (e) => { e.preventDefault(); goSettings("#bidsheet"); } }, "Bid sheet"))));

    // ACTIVITY
    const act = h("section", { class: "vw-card tm-activity" },
      h("div", { class: "vw-card-head" }, h("h2", null, "Recent activity"),
        h("span", { class: "vw-note" }, isAdmin ? "uploads, detects, approvals, account changes" : "")));
    left.append(act);
    if (!isAdmin) {
      act.append(h("div", { class: "tm-empty tm-dim" }, accounts ? "The team's activity is visible to admins." : "Activity is recorded per person once the server has accounts."));
    } else {
      const a = await getJSON("/api/team/activity?limit=40");
      if (my !== gen) return;
      const ev = (a.body && a.body.events) || [];
      if (!a.ok) act.append(h("div", { class: "tm-empty tm-dim" }, "The activity log is not available on this server yet."));
      else if (!ev.length) act.append(h("div", { class: "tm-empty tm-dim" }, "Nothing recorded yet."));
      else act.append(h("ol", { class: "tm-feed" }, ev.map((e) => {
        const [verb, what, extra, who] = phrase(e);
        return h("li", null,
          h("span", { class: "tm-av sm", "aria-hidden": "true" }, initials(e.user || "·")),
          h("span", { class: "tm-feed-t" },
            h("b", null, who || e.user || "Someone"), " ", verb, what ? [" ", h("span", { class: "tm-what" }, what)] : null,
            extra ? h("span", { class: "tm-dim" }, " · " + extra) : null),
          h("time", { datetime: e.ts || "", title: e.ts || "" }, ago(e.ts)));
      })));
    }
  }
  function goSettings(hash) {
    show(false);
    try { history.replaceState(null, "", hash); } catch (e) {}
    if (F("svGoto")) svGoto(hash); else if (F("showSettings")) showSettings(true);
  }
  function memberRow(u, admin, me) {
    const role = roleOf(u);
    const self = u.user === me;
    const status = u.must_change ? h("span", { class: "tm-status pending" }, "must set a password")
      : h("span", { class: "tm-status" }, "active");
    const cells = [
      h("td", null, h("div", { class: "tm-person" },
        h("span", { class: "tm-av" + (u.must_change ? " pending" : ""), "aria-hidden": "true" }, initials(u.user)),
        h("div", null, h("div", { class: "tm-name" }, u.user, self ? h("span", { class: "tm-you" }, "you") : null),
          status))),
    ];
    if (admin) {
      // your own role is not yours to change (the server refuses it; Settings -> Accounts disables it the same way)
      const sel = h("select", { class: "tm-role-sel r-" + role, "aria-label": `Role of ${u.user}`, disabled: self,
        title: self ? "You cannot change your own role. Ask another admin to do it." : null },
        Object.entries(ROLES).map(([k, r]) => { const o = h("option", { value: k }, r.name); if (k === role) o.selected = true; return o; }));
      sel.addEventListener("change", () => changeRole(u.user, sel.value, role, sel));
      cells.push(h("td", null, sel));
      cells.push(h("td", { class: "num" }, u.projects != null ? String(u.projects) : "—"));
      cells.push(h("td", null, h("time", { datetime: u.last_active || "", title: u.last_active || "" }, ago(u.last_active))));
      const reset = h("button", { type: "button", class: "mini", onclick: () => resetPw(u.user) }, "Reset password");
      const rm = h("button", { type: "button", class: "mini danger", disabled: self,
        title: self ? "You cannot remove yourself" : `Remove ${u.user} from this server`,
        onclick: () => removeUser(u.user, rm) }, "Remove");
      cells.push(h("td", { class: "tm-acts" }, reset, rm));
    } else {
      // the status already sits under the name; the third column is the
      // role's one-line meaning, so a non-admin sees what they may do
      cells.push(h("td", null, h("span", { class: "tm-pill r-" + role }, ROLES[role].name)));
      cells.push(h("td", { class: "tm-dim" }, ROLES[role].short || ""));
    }
    return h("tr", null, cells);
  }

  /* --------------------------------------------------------- actions */
  function toast(msg, bad) {
    const t = h("div", { class: "tm-toast" + (bad ? " bad" : ""), role: "status" }, msg);
    view.append(t);
    setTimeout(() => t.remove(), 4200);
  }
  // ONE rule with Settings -> Accounts (fork 92c8e40, app.js svRoleChange in SETTINGS-PURE): the same route, the same
  // question before making an admin ("An admin can see every company's documents. Make <name> an admin?") and the
  // same words after; a pick that changes nothing sends nothing. An app.js without it: the route as before.
  async function changeRole(user, role, was, sel) {
    const ch = F("svRoleChange") ? svRoleChange({ user, role: was }, role) : null;
    if (F("svRoleChange") && !ch) { sel.value = was; return; }
    if (ch && ch.ask && !window.confirm(ch.ask)) { sel.value = was; return; }
    const r = await getJSON(ch ? ch.url : `/api/users/${encodeURIComponent(user)}/role`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(ch ? ch.body : { role }) });
    if (!r.ok) {
      sel.value = was;
      return toast(failWords(r, "The role could not be changed."), true);
    }
    toast(ch ? ch.done : `${user} is now ${ROLES[role].name}.`);
    render();
  }
  async function resetPw(user) {
    const r = await getJSON(`/api/users/${encodeURIComponent(user)}/reset`, { method: "POST" });
    if (!r.ok || !r.body || !r.body.temp_password)
      return toast(failWords(r, "The password could not be reset."), true);
    secretDialog(`New temporary password for ${user}`, r.body.temp_password,
      "It is shown once. Send it privately; they choose their own password when they next sign in.");
    render();
  }
  async function removeUser(user, btn) {
    // two presses, no browser dialog (the Settings page's rule)
    if (btn.dataset.armed !== "1") {
      btn.dataset.armed = "1"; btn.textContent = "Confirm remove";
      setTimeout(() => { if (btn.isConnected) { btn.dataset.armed = ""; btn.textContent = "Remove"; } }, 4000);
      return;
    }
    const r = await getJSON(`/api/users/${encodeURIComponent(user)}`, { method: "DELETE" });
    if (!r.ok) return toast(failWords(r, "The account could not be removed."), true);
    toast(`${user} was removed. Their projects stay on the server, visible to admins.`);
    render();
  }
  function modal(title, body) {
    const close = () => { back.remove(); };
    const back = h("div", { class: "tm-modal", id: "teamModal", role: "dialog", "aria-modal": "true", "aria-label": title,
      onclick: (e) => { if (e.target === back) close(); } },
      h("div", { class: "tm-modal-card" },
        h("div", { class: "tm-modal-head" }, h("h2", null, title),
          h("button", { type: "button", class: "mini", "aria-label": "Close", onclick: close }, "✕")),
        body));
    back.addEventListener("keydown", (e) => { if (e.key === "Escape") { e.stopPropagation(); close(); } });
    document.body.append(back);
    const f = back.querySelector("input, select, button.btn");
    if (f) f.focus();
    return close;
  }
  function addDialog() {
    const email = h("input", { type: "email", id: "tmNewEmail", placeholder: "name@company.com", autocomplete: "off", spellcheck: "false" });
    const role = h("select", { id: "tmNewRole" }, Object.entries(ROLES).map(([k, r]) => {
      const o = h("option", { value: k }, r.name); if (k === "estimator") o.selected = true; return o; }));
    const msg = h("div", { class: "tm-msg", role: "status" });
    const go = h("button", { type: "button", class: "btn primary" }, "Add member");
    const body = h("div", { class: "tm-form" },
      h("label", null, h("span", null, "Email - their sign-in name"), email),
      h("label", null, h("span", null, "Role"), role),
      h("p", { class: "tm-dim" }, "They get a temporary password, shown to you once. Nothing is emailed: send it to them privately. They choose their own password at their first sign-in."),
      msg, h("div", { class: "tm-form-acts" }, go));
    const close = modal("Add a member", body);
    go.addEventListener("click", async () => {
      const u = email.value.trim();
      if (!u) { msg.textContent = "Enter their email address."; return; }
      go.disabled = true;
      const r = await getJSON("/api/users", { method: "POST", headers: { "Content-Type": "application/json" },
        // the route reads `username` (server.py api_users_add); `role` wins over `staff`
        body: JSON.stringify({ username: u, role: role.value, staff: role.value === "admin" }) });
      go.disabled = false;
      if (!r.ok || !r.body || !r.body.temp_password) { msg.textContent = failWords(r, "The account could not be created."); return; }
      close();
      secretDialog(`${r.body.user || u} added as ${ROLES[role.value].name}`, r.body.temp_password,
        "It is shown once. Send it privately; they choose their own password at their first sign-in.");
      render();
    });
  }
  function secretDialog(title, secret, note) {
    const code = h("code", { class: "tm-secret" }, secret);
    const copy = h("button", { type: "button", class: "btn", onclick: async () => {
      try { await navigator.clipboard.writeText(secret); copy.lastChild.textContent = "Copied"; } catch (e) { copy.lastChild.textContent = "Select and copy"; }
    } }, icon(ICON_COPY), h("span", null, "Copy"));
    modal(title, h("div", { class: "tm-form" }, h("div", { class: "tm-secret-row" }, code, copy),
      h("p", { class: "tm-dim" }, note)));
  }

  /* ---------------------------------------------------- ACCOUNT MENU
     Top right: who I am, my role, and the account's verbs - theme,
     keyboard shortcuts, change password, sign out. The ◐ theme button
     MOVES in here (same node, same handler); sign-out clicks app.js's own
     #signOutBtn (it owns the POST and what follows). */
  const ICON_USER = '<circle cx="12" cy="8.5" r="3.5"/><path d="M5 20c.8-3.6 3.6-5.6 7-5.6s6.2 2 7 5.6"/>';
  const userBtn = h("button", { type: "button", id: "userBtn", class: "hdr-avatar", "aria-haspopup": "menu",
    "aria-expanded": "false", title: "Account" }, icon(ICON_USER));
  const userMenu = h("div", { id: "userMenu", class: "user-menu", role: "menu", hidden: true });
  const userWrap = h("div", { class: "user-wrap" }, userBtn, userMenu);
  const themeBtn = byId("themeBtn");
  (themeBtn || settingsLink || link).after(userWrap);
  let menuMe = null;
  // A viewer is told once, in the header, that this seat is read-only - the
  // server refuses its writes whatever the page offers (server.py's viewer
  // gate), so the page stops offering the ones that only fail: a new
  // project (upload), and opening a demo (it resets the demo's run).
  const roChip = h("span", { class: "hdr-ro", id: "roChip", hidden: true,
    title: "Your role is Viewer: you can open and export the projects you can see. Ask an admin for Estimator access to upload, detect, price or approve." },
    "Read-only");
  userWrap.before(roChip);
  async function paintUser() {
    try { const r = await fetch("/api/me"); menuMe = r.ok ? await r.json() : null; } catch (e) { menuMe = null; }
    userBtn.textContent = "";
    if (menuMe && menuMe.user) {
      userBtn.append(h("span", { class: "hdr-avatar-t" }, initials(menuMe.user)));
      userBtn.title = `Signed in as ${menuMe.user}`;
    } else userBtn.append(icon(ICON_USER));
    const viewer = !!(menuMe && menuMe.role === "viewer");
    roChip.hidden = !viewer;
    document.body.classList.toggle("role-viewer", viewer);
    viewerPaint();
  }
  // A VIEWER'S PROJECTS PAGE (UI verify 2026-10-02, defect 10). The empty page told a viewer to upload, drop a PDF or
  // open a demo - all three refused ("Read-only: your role is Viewer ..."), and the Demo plans tab offered five cards
  // that each refuse (a demo opens fresh, which is a write). Now a viewer gets the Your projects tab only and, while it
  // is empty, one plain note in place of app.js's: what this account can do, and who can change that. (The server
  // shows a viewer only its own projects and the shared demo set - there is no way yet to give a viewer someone
  // else's project; that is the server's to add, not this page's to promise.)
  const pvEmptyEl = byId("pvEmpty");
  const viewerNote = h("div", { id: "pvViewerNote", class: "pv-viewer-note", role: "note", hidden: true },
    h("div", { class: "pv-vn-t" }, "Read-only account"),
    h("p", null, "A Viewer can open and export projects, but cannot upload, detect, price or approve. There is no project this account can open yet."),
    h("p", null, "To upload your plans, ask an admin on your team to change your role to Estimator."));
  if (pvEmptyEl) pvEmptyEl.after(viewerNote);
  function viewerPaint() {
    const viewer = document.body.classList.contains("role-viewer");
    const demoTab = byId("pvTabDemo"), mineTab = byId("pvTabMine");
    // the Demo plans tab is closed to a viewer: go to Your projects (app.js's own tab button)
    if (viewer && demoTab && demoTab.getAttribute("aria-selected") === "true" && mineTab) mineTab.click();
    const mine = !!mineTab && mineTab.getAttribute("aria-selected") === "true";
    const grid = byId("pvGrid");
    const empty = !!pvEmptyEl && !pvEmptyEl.hidden && !(grid && grid.querySelector(".pv-card"))
      && !/could not/i.test(pvEmptyEl.textContent || "");
    const show = viewer && mine && empty;
    if (viewerNote.hidden === show) viewerNote.hidden = !show;
    document.body.classList.toggle("viewer-empty", show);
  }
  {
    const vo = new MutationObserver(() => viewerPaint());
    for (const id of ["pvEmpty", "pvTabDemo", "pvTabMine"]) {
      const n = byId(id);
      if (n) vo.observe(n, { attributes: true, attributeFilter: ["hidden", "aria-selected"], childList: true, characterData: true, subtree: true });
    }
    const g = byId("pvGrid");
    if (g) vo.observe(g, { childList: true });
  }
  function menuItem(label, onclick, opts = {}) {
    return h("button", { type: "button", role: "menuitem", class: "um-item" + (opts.danger ? " danger" : ""), onclick: (e) => { closeMenu(); onclick(e); } },
      opts.icon ? opts.icon : null, h("span", null, label), opts.hint ? h("span", { class: "um-hint" }, opts.hint) : null);
  }
  function buildMenu() {
    userMenu.innerHTML = "";
    const me = menuMe || {};
    const role = roleOf({ role: me.role, staff: me.staff });
    userMenu.append(h("div", { class: "um-who" },
      me.user ? [h("div", { class: "um-name" }, me.user), h("div", { class: "um-role" }, `${ROLES[role].name} · signed in`)]
        : [h("div", { class: "um-name" }, "No account"), h("div", { class: "um-role" }, me.accounts === false ? "this server runs without accounts" : "not signed in")]));
    userMenu.append(h("div", { class: "um-sep" }));
    userMenu.append(menuItem("Team", () => show(true), { hint: "members & roles" }));
    userMenu.append(menuItem("Settings", () => { if (F("showSettings")) showSettings(true); }));
    const dark = document.documentElement.dataset.theme !== "light";
    userMenu.append(menuItem(dark ? "Paper theme" : "Dark theme", () => { if (themeBtn) themeBtn.click(); }, { hint: "◐" }));
    userMenu.append(menuItem("Keyboard shortcuts", () => { const k = byId("kbdBtn"); if (k) k.click(); }, { hint: "?" }));
    if (me.user) {
      userMenu.append(h("div", { class: "um-sep" }));
      userMenu.append(menuItem("Change password", () => { location.href = "/change-password"; }));
      const so = byId("signOutBtn");
      userMenu.append(menuItem("Sign out", () => { if (so) so.click(); else fetch("/api/logout", { method: "POST" }).then(() => { location.href = "/login"; }); }, { danger: true }));
    }
  }
  function openMenu() { buildMenu(); userMenu.hidden = false; userBtn.setAttribute("aria-expanded", "true");
    const f = userMenu.querySelector(".um-item"); if (f) f.focus(); }
  function closeMenu() { userMenu.hidden = true; userBtn.setAttribute("aria-expanded", "false"); }
  userBtn.addEventListener("click", (e) => { e.stopPropagation(); if (userMenu.hidden) openMenu(); else closeMenu(); });
  addEventListener("click", (e) => { if (!userMenu.hidden && !userWrap.contains(e.target)) closeMenu(); });
  userMenu.addEventListener("keydown", (e) => {
    const items = [...userMenu.querySelectorAll(".um-item")];
    const i = items.indexOf(document.activeElement);
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeMenu(); userBtn.focus(); }
    else if (e.key === "ArrowDown") { e.preventDefault(); e.stopPropagation(); (items[i + 1] || items[0]).focus(); }
    else if (e.key === "ArrowUp") { e.preventDefault(); e.stopPropagation(); (items[i - 1] || items[items.length - 1]).focus(); }
  });
  paintUser();

  window.coreTeam = { show, render };
  // app.js rewrites the hash to #projects at boot, before this file runs;
  // it keeps what the address bar said in BOOT_HASH
  const bootHash = typeof BOOT_HASH === "string" ? BOOT_HASH : location.hash;
  if (bootHash === "#team" || location.hash === "#team") show(true);
})();
