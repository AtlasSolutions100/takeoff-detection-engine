// THE CLIENT MARKER. Loaded FIRST, as the first element of <head>, on every page this app serves that writes (index.html,
// cutfill/index.html, login.html, change-password.html), so it is in place before any other script on the page - app.js,
// the cut / fill engine's own page (its own window or frame), and the redesign's frame scripts (shell.js, dock.js,
// team.js, responsive.js load at the end of <body>, after this). It puts "X-Core-Client: 3" (2 until scale verdict S4) on every state-changing
// request whatever transport sends it: fetch (a Request object too), XMLHttpRequest, and navigator.sendBeacon (which
// cannot carry a header, so it goes through fetch with keepalive). The server refuses a write without it (server.py
// _client_gate): a tab still running an older page's JavaScript - v1.8.5 left open across the deploy - cannot write
// and is told "CORE was updated. Reload this page to keep working." (critic legacy-3, WRONG-1 / RISK-1). Reads are
// untouched.
// SIGNED OUT ELSEWHERE (UI verify 2, 2026-10-02). A password change or a sign-out in another tab ends this page's
// session; from then on every request answers 401 (an /api/ path) or is sent to /login (any other path, e.g. the cut /
// fill page's own). The page used to show only "not signed in" in the status bar until a reload. Now, once a page that
// WAS signed in (it has had a good answer) meets that, it shows one line at the top - "You were signed out. Sign in
// again." with a link to the sign-in page - and sends no more writes (each answers 401 with the same words, without
// going out). It never reloads and never retries by itself. A wrong password on the sign-in page (POST /api/login) is
// not a sign-out; the 409 "CORE was updated. Reload this page to keep working." keeps its own words.
(function () {
  "use strict";
  var NAME = "X-Core-Client", VALUE = "3";
  var SAY = "You were signed out.", LINK = "Sign in again.";
  var signedIn = false, signedOut = false;
  var writes = function (m) {
    m = String(m || "GET").toUpperCase();
    return m !== "GET" && m !== "HEAD" && m !== "OPTIONS";
  };
  var pathOf = function (u) {
    u = (u && typeof u === "object" && u.url) ? u.url : u;
    try { return new URL(String(u), location.href).pathname; }
    catch (e) { return String(u || "").split(/[?#]/)[0]; }
  };
  var showSignedOut = function () {
    if (signedOut) return;
    signedOut = true;
    try {
      var bar = document.createElement("div");
      bar.id = "coreSignedOut";
      bar.setAttribute("role", "alert");
      bar.style.cssText = "position:fixed;top:0;left:0;right:0;z-index:2147483647;padding:10px 16px;"
        + "background:#3a1c17;color:#ffd9d3;border-bottom:2px solid #e0533d;"
        + "font:600 15px/1.4 system-ui,sans-serif;text-align:center";
      var a = document.createElement("a");
      var top = true;
      try { top = window.top === window; } catch (e) { top = false; }
      a.setAttribute("href", "/login?next=" + encodeURIComponent(top ? location.pathname + location.search : "/"));
      a.setAttribute("target", "_top");          // the cut / fill page can sit in a frame: sign in on the whole tab
      a.style.cssText = "color:#fff;text-decoration:underline;margin-left:6px";
      a.textContent = LINK;
      bar.appendChild(document.createTextNode(SAY));
      bar.appendChild(a);
      (document.body || document.documentElement).appendChild(bar);
    } catch (e) { /* no page to draw on: the writes are still held back */ }
  };
  // every answer this page gets: a good one means the page is signed in; a 401 (not the sign-in form's own) or a
  // bounce to /login after that means the session ended somewhere else
  var note = function (url, status, finalUrl, redirected) {
    var p = pathOf(url);
    var bounced = !!finalUrl && pathOf(finalUrl) === "/login" && p !== "/login" && (redirected !== false);
    if ((status === 401 && !/\/api\/login$/.test(p)) || bounced) {
      if (signedIn) showSignedOut();
      return;
    }
    if (status >= 200 && status < 300) signedIn = true;
  };
  var refused = function () {
    var body = JSON.stringify({ detail: SAY + " " + LINK });
    try { return new Response(body, { status: 401, headers: { "Content-Type": "application/json" } }); }
    catch (e) {
      return { ok: false, status: 401, json: function () { return Promise.resolve({ detail: SAY + " " + LINK }); },
               text: function () { return Promise.resolve(body); } };
    }
  };
  if (typeof window.fetch === "function" && !window.fetch.__coreClient) {
    var fetch0 = window.fetch;
    var wrapped = function (input, init) {
      var method = "GET";
      try {
        var req = (typeof Request !== "undefined" && input instanceof Request) ? input : null;
        method = (init && init.method) || (req && req.method) || "GET";
        if (writes(method)) {
          var h = new Headers((init && init.headers) || (req ? req.headers : undefined));
          h.set(NAME, VALUE);
          init = Object.assign({}, init || {}, { headers: h });
        }
      } catch (e) { /* never block a request because of the marker: the server says what is wrong */ }
      if (signedOut && writes(method)) return Promise.resolve(refused());
      var p = fetch0.call(this, input, init);
      if (p && typeof p.then === "function")
        p.then(function (r) { if (r) note(input, r.status, r.url, r.redirected); }, function () {});
      return p;
    };
    wrapped.__coreClient = true;
    window.fetch = wrapped;
  }
  if (typeof XMLHttpRequest !== "undefined" && !XMLHttpRequest.prototype.__coreClient) {
    var open0 = XMLHttpRequest.prototype.open, send0 = XMLHttpRequest.prototype.send;
    XMLHttpRequest.prototype.open = function (method, url) {
      this.__coreWrite = writes(method);
      this.__coreUrl = url;
      return open0.apply(this, arguments);
    };
    XMLHttpRequest.prototype.send = function () {
      var x = this;
      if (x.__coreWrite && signedOut) {
        // not sent: the page's own error path runs (an upload says it did not go), and the line on top says why
        setTimeout(function () {
          try { x.dispatchEvent(new Event("error")); }
          catch (e) { if (typeof x.onerror === "function") x.onerror({ type: "error" }); }
        }, 0);
        return;
      }
      if (x.__coreWrite) { try { x.setRequestHeader(NAME, VALUE); } catch (e) { /* not opened */ } }
      if (typeof x.addEventListener === "function")
        x.addEventListener("load", function () { note(x.__coreUrl, x.status, x.responseURL, undefined); });
      return send0.apply(x, arguments);
    };
    XMLHttpRequest.prototype.__coreClient = true;
  }
  if (typeof navigator !== "undefined" && typeof navigator.sendBeacon === "function" && !navigator.sendBeacon.__coreClient) {
    var beacon = function (url, data) {
      try { window.fetch(url, { method: "POST", body: data, keepalive: true, credentials: "same-origin" }); return true; }
      catch (e) { return false; }
    };
    beacon.__coreClient = true;
    try { navigator.sendBeacon = beacon; } catch (e) { /* read-only in this browser: nothing here uses it */ }
  }
})();
