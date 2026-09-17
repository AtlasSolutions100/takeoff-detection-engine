"""Detection service + classify-and-approve review UI.

Run:  python3 -m uvicorn server:app --port 8100

Workflow (the estimator's 5-minute loop):
  open a plan -> pick a page -> Detect -> review discovered line styles
  (overlay, counts, lengths, suggested labels) -> relabel / include ->
  Approve -> elements JSON in TakeoffDraft2 format is saved + downloadable.

The service holds detection results per (document, page) on disk under
out/jobs/, so re-opening a document reuses finished work.
"""

import hashlib
import requests
import json
import math
import multiprocessing
import os
import re
import sys
import urllib.parse
import textwrap
import threading
import time
import uuid
from collections import deque
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from takeoff_engine import bids as _bids
from takeoff_engine import branding as _branding
from takeoff_engine import bidsheet as _bidsheet
from takeoff_engine import ledger
from takeoff_engine.chain import build_chains
from takeoff_engine.classify_page import classify_page
from takeoff_engine.detect_page import (error_path, read_sheet_number,
                                        result_path, run_detect)
from takeoff_engine.extract import extract_strokes, page_words
from takeoff_engine.marks import read_mark_schedule
from takeoff_engine.scale_detect import detect_scale

ROOT = Path(__file__).parent
# Where every artifact lives: detection caches, page renders, approved
# exports, report PDFs. Overridable because in a container this has to be
# a mounted volume - losing it means every demo is cold again, 90s+ per
# sheet on a big set. Defaults to out/jobs beside the source, which is
# what a laptop wants.
JOBS_DIR = Path(os.environ.get("TAKEOFF_JOBS_DIR")
                or (ROOT / "out" / "jobs")).expanduser()
JOBS_DIR.mkdir(parents=True, exist_ok=True)
SAMPLES_DIR = (ROOT / ".." / "TakeoffPro AI").resolve()

# Filesystem exposure. /api/open-path and /api/legend-sheet take a path
# from the client, and /api/samples lists plans on disk - fine on a laptop,
# an arbitrary-file read on a hosted box. Both are confined to PLAN_ROOTS:
#   TAKEOFF_PLAN_ROOTS  os.pathsep-separated directories (default: the
#                       local plan corpus + client_meet next to this repo,
#                       and our own upload dir)
#   TAKEOFF_HOSTED=1    hosted mode - open-path/legend-sheet paths are
#                       refused outright (uploads only) and /api/samples
#                       is empty, so one tenant never sees another's files
HOSTED = os.environ.get("TAKEOFF_HOSTED", "").strip().lower() in ("1", "true", "yes")
_roots_env = os.environ.get("TAKEOFF_PLAN_ROOTS", "").strip()
PLAN_ROOTS = [Path(r).expanduser().resolve()
              for r in (_roots_env.split(os.pathsep) if _roots_env else [])
              if r.strip()] or [
    SAMPLES_DIR,
    (ROOT / ".." / "client_meet").resolve(),
    JOBS_DIR.resolve(),
]


def _under_roots(p: Path) -> bool:
    """True when the RESOLVED path sits inside one of PLAN_ROOTS. Resolving
    first defeats ../ traversal and symlink tricks; a non-existent path is
    never allowed."""
    try:
        rp = p.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return False
    for root in PLAN_ROOTS:
        try:
            rp.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def _axis_len_pt(points, ratio: float, length_pt: float) -> float:
    """`length_pt` re-measured as if the two axes carried different scales.

    Mirrors chainLenPt() in webui/app.js exactly, including the reason it
    SCALES rather than recomputes: length_pt is not the polyline length of
    `points`. A chain that picks up across a gap adds that gap to its length
    without adding a leg, and on Kayden Springs p39 that makes 515 of 682
    chains differ, one by 17.8%. Rebuilding the length from points would drop
    every one of those gaps the moment a vertical scale was applied.

    So take the ratio the axes change the geometry by, and apply it to the
    length the engine already measured. ratio == 1.0 returns length_pt exactly.
    """
    if ratio == 1.0 or len(points) < 2:
        return length_pt
    plain = axis = 0.0
    for i in range(1, len(points)):
        dx = points[i][0] - points[i - 1][0]
        dy = points[i][1] - points[i - 1][1]
        plain += math.hypot(dx, dy)
        axis += math.hypot(dx, dy * ratio)
    if plain <= 0:
        return length_pt
    return length_pt * (axis / plain)


def _wz_norm(zones) -> list:
    """Work-area rectangles off the wire: junk rows dropped, corners
    sorted, zero-area boxes refused. Mirrors wzNorm() in webui/app.js."""
    out = []
    for z in zones or []:
        try:
            x0, y0, x1, y1 = float(z[0]), float(z[1]), float(z[2]), float(z[3])
        except (TypeError, ValueError, IndexError):
            continue
        if not all(math.isfinite(v) for v in (x0, y0, x1, y1)):
            continue
        if x1 < x0:
            x0, x1 = x1, x0
        if y1 < y0:
            y0, y1 = y1, y0
        if x1 - x0 <= 0 or y1 - y0 <= 0:
            continue
        out.append((x0, y0, x1, y1))
    return out


def _wz_pt_in(x, y, zones) -> bool:
    return any(z[0] <= x <= z[2] and z[1] <= y <= z[3] for z in zones)


def _wz_seg_t(ax, ay, bx, by, zones) -> float:
    """Fraction of segment a->b inside the UNION of rects, as merged
    t-intervals in [0,1]. Mirrors wzSegT() in webui/app.js exactly: the
    export must clip precisely as the panel the operator approved from,
    or the approved number and the exported number silently diverge.
    Exact axis-aligned slab clipping - never midpoint voting."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return 1.0 if _wz_pt_in(ax, ay, zones) else 0.0
    iv = []
    for z in zones:
        t0, t1 = 0.0, 1.0
        if dx == 0:
            if ax < z[0] or ax > z[2]:
                continue
        else:
            ta, tb = (z[0] - ax) / dx, (z[2] - ax) / dx
            if ta > tb:
                ta, tb = tb, ta
            t0, t1 = max(t0, ta), min(t1, tb)
        if dy == 0:
            if ay < z[1] or ay > z[3]:
                continue
        else:
            ta, tb = (z[1] - ay) / dy, (z[3] - ay) / dy
            if ta > tb:
                ta, tb = tb, ta
            t0, t1 = max(t0, ta), min(t1, tb)
        if t1 > t0:
            iv.append((t0, t1))
    if not iv:
        return 0.0
    iv.sort()
    total, (s, e) = 0.0, iv[0]
    for a2, b2 in iv[1:]:
        if a2 <= e:
            e = max(e, b2)
        else:
            total += e - s
            s, e = a2, b2
    total += e - s
    return total


def _wz_frac(points, zones, ratio: float = 1.0) -> float:
    """Fraction of a chain's (axis-weighted) polyline inside the work-area
    union. Mirrors wzChainFrac()/wzPolyInsideLen() in webui/app.js: the
    inside PORTION is decided in true page space, the LENGTH each piece
    contributes is axis-weighted, and the exported quantity is the engine
    length scaled by this fraction - welded pickup gaps are apportioned,
    never re-measured, so a zone covering the whole sheet returns the
    engine's own number exactly."""
    if not zones:
        return 1.0
    if not points:
        return 0.0
    if len(points) < 2:
        return 1.0 if _wz_pt_in(points[0][0], points[0][1], zones) else 0.0
    inside = total = 0.0
    for i in range(1, len(points)):
        ax, ay = points[i - 1][0], points[i - 1][1]
        bx, by = points[i][0], points[i][1]
        seg = math.hypot(bx - ax, (by - ay) * ratio)
        if seg <= 0:
            continue
        total += seg
        t = _wz_seg_t(ax, ay, bx, by, zones)
        if t > 0:
            inside += t * seg
    if total <= 0:
        return 1.0 if _wz_pt_in(points[0][0], points[0][1], zones) else 0.0
    return inside / total


def _client_pdf(raw: str) -> Path:
    """Validate a client-supplied PDF path or raise the right HTTP error."""
    if HOSTED:
        raise HTTPException(403, "path access is disabled on the hosted "
                                 "service - upload the PDF instead")
    p = Path(raw).expanduser()
    if not _under_roots(p) or p.suffix.lower() != ".pdf" or not p.is_file():
        # one message for missing and forbidden: a probe must not learn
        # which paths exist outside the roots
        raise HTTPException(404, "not an accessible PDF")
    return p.resolve()

app = FastAPI(title="Takeoff Detection Service")

# AUTHENTICATION. A real login page and a signed session cookie, NOT HTTP
# Basic. Basic auth was removed on 2026-08-25 for a concrete reason: the
# browser caches the credentials it was given and keeps replaying them, so
# once someone has typed a password that later stops working - which is
# exactly what happens the moment per-user logins are switched on and the old
# shared password dies - they get 401 forever with no way to correct it. There
# is no "sign out" for a Basic prompt. That cost the owner an evening.
#
# Two modes, unchanged in substance:
#   users.json present - each person signs in with their own email/password
#   users.json absent  - any username plus TAKEOFF_UI_PASSWORD (dev, and the
#                        state a fresh box boots in)
# With TAKEOFF_UI_PASSWORD unset there is no auth at all, which is what a
# laptop wants.
_UI_PASSWORD = os.environ.get("TAKEOFF_UI_PASSWORD")

# The password every account is issued with. Safe only because the service
# refuses to do anything except change it (see _RESET_OK), and because
# /api/change-password will not accept it as the new one.
DEFAULT_PASSWORD = "WorkOptional"

SESSION_COOKIE = "tk_session"
SESSION_DAYS = 7


def _session_secret() -> bytes:
    """Signing key, persisted beside the job store.

    On the volume, not in the image or the environment: it has to survive a
    redeploy or every session dies on each release, and it must NOT be derived
    from TAKEOFF_UI_PASSWORD, or changing that password would silently
    invalidate every cookie.
    """
    import secrets as _s
    f = JOBS_DIR / "session.key"
    try:
        if f.exists():
            b = f.read_bytes().strip()
            if len(b) >= 32:
                return b
    except OSError:
        pass
    b = _s.token_hex(32).encode()
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b)
        os.chmod(f, 0o600)
    except OSError:
        pass                        # read-only volume: sessions last this boot
    return b


def _sign_session(username: str | None) -> str:
    import base64 as _b
    import hmac as _h
    import time as _t
    body = json.dumps({"u": username, "exp": int(_t.time()) + SESSION_DAYS * 86400},
                      separators=(",", ":")).encode()
    raw = _b.urlsafe_b64encode(body).rstrip(b"=")
    sig = _h.new(_session_secret(), raw, hashlib.sha256).hexdigest()[:32]
    return raw.decode() + "." + sig


def _read_session(token: str):
    """(ok, username). Fails closed on anything malformed or expired."""
    import base64 as _b
    import hmac as _h
    import time as _t
    if not token or "." not in token:
        return False, None
    raw, _, sig = token.rpartition(".")
    want = _h.new(_session_secret(), raw.encode(), hashlib.sha256).hexdigest()[:32]
    if not _h.compare_digest(sig, want):
        return False, None
    try:
        pad = "=" * (-len(raw) % 4)
        d = json.loads(_b.urlsafe_b64decode(raw + pad))
        if int(d.get("exp", 0)) < _t.time():
            return False, None
        return True, d.get("u")
    except (ValueError, TypeError):
        return False, None


# Added ALWAYS, not only when a password is set. If it were conditional,
# deleting TAKEOFF_UI_PASSWORD from .env - which is exactly what you do once
# real accounts exist - would silently remove authentication from a public box
# rather than switch it over. This decides per request instead.
if True:
    import secrets as _secrets

    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import JSONResponse as _StarJSON
    from starlette.responses import RedirectResponse as _StarRedirect
    from starlette.responses import Response as _StarResponse

    from takeoff_engine import users as _users

    # Reachable before sign-in. Deliberately tiny - every entry here is a path
    # nobody has authenticated to reach.
    # Terms and Privacy must be readable BEFORE anyone signs in - a policy you
    # can only see once you have already accepted it is not a policy. They pull
    # legal.css and the logo, so those are public too, and nothing else is.
    # The site mark: /favicon.ico was already public, but the SVG and the
    # apple-touch PNG were not, so the login page - the one page every user
    # sees before any of this matters - fell back to a blank tab icon.
    _PUBLIC = {"/login", "/login.html", "/api/login", "/favicon.ico",
               "/favicon.svg", "/favicon-32.png", "/favicon-180.png",
               "/terms", "/terms.html", "/privacy", "/privacy.html",
               "/legal.css", "/lockup-animated.svg"}

    # Reachable while signed in but still on the issued password. Everything
    # else is refused until it is changed - that gate is the entire reason a
    # shared starting password is acceptable rather than reckless, because
    # until then the account is only as private as one word that 20 people
    # were told.
    _RESET_OK = {"/change-password", "/change-password.html",
                 "/api/change-password", "/api/me", "/api/logout",
                 "/api/beta-notice", "/favicon.ico", "/favicon.svg",
                 "/favicon-32.png", "/favicon-180.png"}

    # Crude per-IP throttle. Twenty known accounts on one box does not warrant
    # a real rate limiter, but it should not be a free password oracle either.
    _FAILS: dict = {}

    def _throttled(ip: str) -> bool:
        import time as _t
        now = _t.time()
        hits = [t for t in _FAILS.get(ip, []) if now - t < 300]
        _FAILS[ip] = hits
        return len(hits) >= 12

    def _note_fail(ip: str) -> None:
        import time as _t
        _FAILS.setdefault(ip, []).append(_t.time())

    # Ownership is enforced HERE rather than in each route. There are twenty-odd
    # /api/job/{id}/... routes and more will be added; a check that has to be
    # remembered per route is a check that eventually is not.
    # ANY path segment, not only a well-formed hex id: the first question
    # below is "does the registry hold this row", and an id that could never
    # have one is exactly the kind that must not fall through to the router.
    _JOB_PATH = re.compile(r"^/api/job/([^/]+)(?:/|$)")

    def _forbidden_job(path: str, user, staff: bool = False):
        """404 when the job named in `path` has no registry row, or when
        `user` may not touch it; else None.

        FAILS CLOSED ON A MISSING ROW, FOR EVERY CALLER - staff and
        single-password boxes included, which is why that check runs before
        the `user is None or staff` return. A row can vanish while the job's
        directory stays on disk: delete_job unlinks each file inside a
        try/except and then drops the row regardless, and on Windows an
        unlink that hits an open file fails silently. What is left is an
        ORPHAN - one client's detection, pricing and approved deliverable,
        with no owner - and the old "let the route answer its own 404"
        branch handed it to the first signed-in account that asked. 24 of
        the 27 job routes take no Request and go straight to _job_dir(),
        which returns (or creates) the directory; they cannot answer
        anything. Reproduced 2026-08-31: account B rewrote A's calcstate
        and approved export, two 200s. tests/test_orphan_job.py sweeps every
        job route against an orphan and is the reason a new route cannot
        reopen this.

        404 and not 403: a 403 confirms the job exists and belongs to somebody
        else, which on a box holding several clients' bid documents leaks that
        the other client is here at all.
        """
        m = _JOB_PATH.match(path)
        if not m:
            return None
        with JOBS_LOCK:
            j = JOBS.get(m.group(1))
        if j is None:
            return _StarJSON({"detail": "unknown job"}, status_code=404)
        if user is None or staff:
            return None                 # single-password mode, or our own team
        owner = j.get("owner")
        if owner is None or owner == user:
            return None                 # shared demo set, or theirs
        return _StarJSON({"detail": "unknown job"}, status_code=404)

    class _SessionAuth(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            path = request.url.path
            if path in _PUBLIC:
                return await call_next(request)

            # FAIL CLOSED. No accounts and no site password on a reachable box
            # is a misconfiguration, not permission to serve clients' bid
            # documents to the internet. A laptop (HOSTED unset) is left alone.
            if not _users.load() and not _UI_PASSWORD:
                if HOSTED:
                    return _StarJSON(
                        {"detail": "this server has no accounts configured; "
                                   "run takeoff_engine.users to add one"},
                        status_code=503)
                # A laptop has nobody to keep apart, but it still must not
                # serve a job the registry has forgotten (see _forbidden_job).
                blocked = _forbidden_job(path, None, True)
                if blocked is not None:
                    return blocked
                return await call_next(request)

            ok, user = _read_session(request.cookies.get(SESSION_COOKIE, ""))
            # AN OWNERLESS SESSION IS NOT SIGNED IN ONCE ACCOUNTS EXIST.
            # Single-password mode issues cookies whose payload is {"u": null}
            # - the only signed-in state that mode has. They live seven days,
            # and session.key survives the switch to accounts, so such a
            # cookie stays VALID after users.json appears. Everything below
            # then reads user=None as "single-password mode": _forbidden_job
            # lets None through every job route and _is_staff reports True.
            # Demonstrated live on b528e74 with three accounts: /api/me
            # answered {user: null}, /api/recent listed a client's private
            # upload, her job opened and her page rendered - a signed-in
            # nobody who outranked everybody. This is the ONE place the
            # request's user is derived, so this is where it fails closed:
            # with accounts on the box, no name means no session, and a
            # name the box no longer knows (an account deleted while its
            # 7-day cookie was still out) means no session either; the
            # caller is treated exactly like one with no cookie at all.
            _accounts = _users.load()
            if ok and _accounts and (user is None or user not in _accounts):
                ok = False
            if not ok:
                # An API caller wants a status code; a person wants the login
                # page, and to land back where they were pointed.
                if path.startswith("/api/"):
                    return _StarJSON({"detail": "not signed in"}, status_code=401)
                nxt = path + (("?" + request.url.query) if request.url.query else "")
                return _StarRedirect(
                    "/login?next=" + urllib.parse.quote(nxt, safe=""),
                    status_code=303)

            request.scope["tk_user"] = user
            request.scope["tk_staff"] = bool(user) and _users.is_staff(user)

            if user and _users.must_change(user) and path not in _RESET_OK:
                if path.startswith("/api/"):
                    return _StarJSON(
                        {"detail": "choose a new password before continuing"},
                        status_code=403)
                return _StarRedirect("/change-password", status_code=303)

            blocked = _forbidden_job(path, user, request.scope["tk_staff"])
            if blocked is not None:
                return blocked
            return await call_next(request)

    app.add_middleware(_SessionAuth)

    @app.get("/terms")
    def terms_page():
        return FileResponse(ROOT / "webui" / "terms.html", media_type="text/html")

    @app.get("/privacy")
    def privacy_page():
        return FileResponse(ROOT / "webui" / "privacy.html", media_type="text/html")

    @app.get("/login")
    def login_page():
        return FileResponse(ROOT / "webui" / "login.html",
                            media_type="text/html")

    @app.post("/api/login")
    async def login(request: Request):
        body = await request.json()
        name = str(body.get("username") or "").strip()
        pw = str(body.get("password") or "")
        ip = (request.client.host if request.client else "?")
        if _throttled(ip):
            raise HTTPException(
                429, "Too many attempts. Wait five minutes and try again.")

        who = None
        if _users.load():
            who = _users.check(name, pw)          # per-user mode
            good = who is not None
        else:
            # No accounts defined: any username, the shared site password.
            good = bool(name) and _secrets.compare_digest(pw, _UI_PASSWORD)
        if not good:
            _note_fail(ip)
            # One message for both cases, so this cannot be used to find out
            # which of these twenty addresses has an account.
            raise HTTPException(401, "That email and password do not match.")

        r = JSONResponse({"ok": True, "user": who})
        # Secure only when the request actually arrived over TLS, or a laptop
        # on plain http could never hold a session.
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        r.set_cookie(SESSION_COOKIE, _sign_session(who),
                     max_age=SESSION_DAYS * 86400, httponly=True,
                     samesite="lax", secure=(proto == "https"), path="/")
        return r

    @app.post("/api/logout")
    def logout():
        r = JSONResponse({"ok": True})
        r.delete_cookie(SESSION_COOKIE, path="/")
        return r

    @app.get("/change-password")
    def change_password_page():
        return FileResponse(ROOT / "webui" / "change-password.html",
                            media_type="text/html")

    @app.post("/api/change-password")
    async def change_password(request: Request):
        u = request.scope.get("tk_user")
        if not u:
            raise HTTPException(401, "not signed in")
        body = await request.json()
        pw = str(body.get("password") or "")
        if len(pw) < 8:
            raise HTTPException(400, "Use at least 8 characters.")
        # Refuse to "change" it to the word everyone was handed. Without this
        # the whole gate is theatre: you could satisfy it by retyping the
        # default and stay exactly as exposed as before.
        if pw.strip().lower() == DEFAULT_PASSWORD.lower():
            raise HTTPException(
                400, "That is the temporary password. Choose a different one.")
        if not _users.set_password(u, pw):
            raise HTTPException(404, "unknown account")
        # Re-issue the cookie so the session outlives the change cleanly.
        r = JSONResponse({"ok": True})
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        r.set_cookie(SESSION_COOKIE, _sign_session(u),
                     max_age=SESSION_DAYS * 86400, httponly=True,
                     samesite="lax", secure=(proto == "https"), path="/")
        return r

    @app.get("/api/me")
    def whoami(request: Request):
        """Who the UI is signed in as, so it can show it and offer sign-out."""
        u = request.scope.get("tk_user")
        return {"user": u, "staff": bool(request.scope.get("tk_staff")),
                "must_change": bool(u and _users.must_change(u)),
                "accounts": bool(_users.load())}


def _who(request: Request):
    """Username for this request, or None in single-password mode."""
    return request.scope.get("tk_user")


def _is_staff(request: Request) -> bool:
    """Our own team: sees every document, may delete any of them. True in
    single-password mode too, where there is nothing to separate.

    "No user" only means single-password mode while the box HAS no accounts.
    The middleware now refuses an ownerless session once accounts exist, so
    a route should never see user None on a per-user box - but this is the
    function every staff gate in the file calls, and it must not be the
    thing that turns a nameless caller into staff if some future path lets
    one through. Both conditions, or it is not staff."""
    return bool(request.scope.get("tk_staff")) or (
        _who(request) is None and not _users.load())


# ---------------------------------------------------------------------------
# ACCOUNTS FROM INSIDE THE APP (2026-09-15). Until now every account on the
# hosted box was created over ssh: `docker compose exec app python -m
# takeoff_engine.users add <email> --staff`. The boss could not hand a client
# a login without the developer at a terminal. These routes give a STAFF
# account the CLI's verbs - list, add, remove, reset - plus the staff toggle,
# from Settings -> Accounts.
#
# Rules the CLI never had to enforce, because a person at a shell is assumed
# to know what they are doing:
#   * staff only, and 404 rather than 403 for anyone else, the same way
#     /api/feedback hides that it exists;
#   * the FIRST account on a box is still made at the terminal: creating it
#     here would flip the box into per-user mode and sign the creator out
#     mid-request (the middleware refuses an ownerless cookie once accounts
#     exist - see _SessionAuth), so the gate answers 409 with the command;
#   * you cannot remove yourself, and nobody can remove or demote the LAST
#     staff account: a box with accounts and no staff has nobody left who can
#     open every document, delete a client's set or add the next account,
#     and the only way back is ssh;
#   * a password issued here is shown ONCE, in the response, and the account
#     must change it at first sign-in (users.must_change) - so whoever created
#     the account does not keep a working password for it.
# Documents keep their owner's name when the account is removed: they do not
# vanish, they become visible to staff only (ownership is by name in the
# registry; an account re-created with the same spelling gets them back).

_USERNAME_MAX = 120


def _account_gate(request: Request) -> None:
    if not _is_staff(request):
        raise HTTPException(404, "not found")
    if not _users.load():
        raise HTTPException(
            409, "This server has no accounts yet, so it is in single-password "
                 "mode. The first account is created on the server itself: "
                 "python -m takeoff_engine.users add <email> --staff")


def _staff_count(exclude: str | None = None) -> int:
    return sum(1 for r in _users.list_users()
               if r["staff"] and r["user"] != exclude)


@app.get("/api/users")
def api_users(request: Request):
    _account_gate(request)
    return {"me": _who(request), "users": _users.list_users()}


@app.post("/api/users")
async def api_users_add(request: Request):
    _account_gate(request)
    body = await request.json()
    name = str(body.get("username") or "").strip()
    if not name or len(name) > _USERNAME_MAX or any(c.isspace() for c in name):
        raise HTTPException(
            400, "Enter the sign-in name as one word, usually their email address.")
    have = _users.find(name)
    if have is not None:
        raise HTTPException(
            409, f"{have} already has an account. Use Reset password to issue "
                 "a new one.")
    pw = str(body.get("password") or "")
    if not pw:
        pw = _users.temp_password()
    elif len(pw) < 8:
        raise HTTPException(400, "Use at least 8 characters.")
    elif pw.strip().lower() == DEFAULT_PASSWORD.lower():
        raise HTTPException(
            400, "That is the shared temporary password. Choose a different "
                 "one, or leave it blank to have one generated.")
    # must_change is not optional: the creator chose (or saw) this password,
    # so the account is not private until its owner replaces it.
    rec = _users.add_user(name, pw, staff=bool(body.get("staff")),
                          must_change=True)
    return {"ok": True, "user": rec["user"], "staff": rec["staff"],
            "must_change": True, "temp_password": pw,
            "created_by": _who(request)}


@app.delete("/api/users/{username}")
def api_users_remove(username: str, request: Request):
    _account_gate(request)
    name = _users.find(username)
    if name is None:
        raise HTTPException(404, "no such account")
    if name == _who(request):
        raise HTTPException(
            400, "You cannot remove the account you are signed in as. Ask "
                 "another staff member to do it.")
    if _users.is_staff(name) and _staff_count(exclude=name) == 0:
        raise HTTPException(
            400, f"{name} is the last staff account on this server. Make "
                 "someone else staff first.")
    _users.remove_user(name)
    return {"ok": True, "removed": name, "left": len(_users.load())}


@app.post("/api/users/{username}/reset")
def api_users_reset(username: str, request: Request):
    _account_gate(request)
    name = _users.find(username)
    if name is None:
        raise HTTPException(404, "no such account")
    pw = _users.temp_password()
    rec = _users.add_user(name, pw, must_change=True)     # keeps staff
    return {"ok": True, "user": rec["user"], "staff": rec["staff"],
            "must_change": True, "temp_password": pw}


@app.post("/api/users/{username}/staff")
async def api_users_staff(username: str, request: Request):
    _account_gate(request)
    body = await request.json()
    flag = bool(body.get("staff"))
    name = _users.find(username)
    if name is None:
        raise HTTPException(404, "no such account")
    # Your own role is off limits: demoting yourself drops the Accounts page
    # from under you mid-click and nobody but another staff member can put
    # it back (it happened to the owner on the first day it was live).
    if name == _who(request):
        raise HTTPException(
            400, "You cannot change your own role. Ask another staff member "
                 "to do it.")
    if not flag and _users.is_staff(name) and _staff_count(exclude=name) == 0:
        raise HTTPException(
            400, f"{name} is the last staff account on this server. Make "
                 "someone else staff first.")
    _users.set_staff(name, flag)
    return {"ok": True, "user": name, "staff": flag}


# ---------------------------------------------------------------------------
# BRANDING. The one Settings value that is not a browser preference, because
# the thing it brands (report_pdf, below) is generated on the server. See
# takeoff_engine/branding.py for why it is stored per account rather than
# once per install.
#
# Both fields are OPTIONAL with a safe default, so a client that predates
# this endpoint - or one that only knows about `company` - still works: it
# posts what it has, the missing field reads as "" and the record is
# rewritten without it. Nothing here can 404 or 500 a box that has never
# been branded; the blank record is a legitimate answer.
class BrandingBody(BaseModel):
    company: str | None = None
    contact: str | None = None


@app.get("/api/branding")
def get_branding(request: Request):
    """The company this seat's reports are stamped with.

    `scope` says where the record came from and `seat` says where a save
    would go, so the UI can tell the operator which of the two they are
    looking at instead of showing a name with no provenance.
    """
    rec, scope = _branding.resolve(_who(request))
    return {"company": rec["company"], "contact": rec["contact"],
            "scope": scope,
            "seat": "account" if _who(request) else "install",
            "company_max": _branding.COMPANY_MAX,
            "contact_max": _branding.CONTACT_MAX}


@app.post("/api/branding")
def post_branding(body: BrandingBody, request: Request):
    """Set this seat's company. Writing a blank company is a decision, not a
    reset: it means "stamp nothing", and it outranks the install default."""
    rec, scope = _branding.save(
        _who(request), {"company": body.company, "contact": body.contact})
    return {"company": rec["company"], "contact": rec["contact"],
            "scope": scope, "seat": scope,
            "company_max": _branding.COMPANY_MAX,
            "contact_max": _branding.CONTACT_MAX}


class BidsheetBody(BaseModel):
    lines: list | None = None


def _bidsheet_payload(lines, scope, request: Request) -> dict:
    return {"lines": lines, "scope": scope,
            "seat": "account" if _who(request) else "install",
            "units": list(_bidsheet.UNITS), "default": _bidsheet.default_lines(),
            "line_max": _bidsheet.LINE_MAX}


@app.get("/api/bidsheet")
def get_bidsheet(request: Request):
    """This seat's bid sheet: ordered lines with units and the words that
    file a takeoff row onto them (Beaumont's printed sheet until the seat
    saves its own). `scope` says where it came from, like the letterhead."""
    lines, scope = _bidsheet.resolve(_who(request))
    return _bidsheet_payload(lines, scope, request)


@app.post("/api/bidsheet")
def post_bidsheet(body: BidsheetBody, request: Request):
    """Save this seat's sheet (a list, cleaned and renumbered) or, with
    `lines` null, remove it and go back to the printed default."""
    lines, scope = _bidsheet.save(_who(request), body.lines)
    return _bidsheet_payload(lines, scope, request)


# BETA NOTICE. The whole point of this product is a number an estimator puts
# in a bid, so the caveat has to travel WITH the number, not sit on a login
# page nobody re-reads. It is stamped on every page of the report PDF, tailed
# onto the CSV, and shown as a banner the UI cannot dismiss.
BETA_SHORT = ("BETA - CORE Takeoff is still being refined. Automated de"
              "tection can be wrong, so check every quantity against the drawings.")
BETA_LONG = (
    "BETA SOFTWARE. CORE Takeoff is in active development. The quanti"
    "ties here are produced by automated line detection and scale rea"
    "ding and can be incomplete or wrong. Check every figure against "
    "the source drawings before relying on it.")

# job_id -> {"path": str, "pages": int, "status": {page: "running"|"done"|"error:..."}}
JOBS: dict = {}
JOBS_LOCK = threading.Lock()
REGISTRY = JOBS_DIR / "registry.json"


def _save_registry() -> None:
    """Jobs live in memory, but detection results live on disk - so without
    this a service restart answers 'unknown job' for work that is sitting
    right there, and the page has to be detected again."""
    try:
        REGISTRY.write_text(json.dumps(
            {jid: {"path": j["path"], "pages": j["pages"],
                   "mtime": j.get("mtime"), "kinds": j.get("kinds"),
                   "sizes": j.get("sizes"), "owner": j.get("owner"),
                   # where the plan came from when Open bids pulled it
                   # (/api/bids/{id}/pull); None for an upload
                   "bid": j.get("bid")}
             for jid, j in JOBS.items()}, indent=1))
    except OSError:
        pass


def _load_registry() -> None:
    if not REGISTRY.exists():
        return
    try:
        saved = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        return
    for jid, j in saved.items():
        if not Path(j.get("path", "")).exists():
            continue  # the PDF moved; a stale entry would fail confusingly
        status = {}
        d = JOBS_DIR / jid
        if d.is_dir():
            for f in d.glob("p*_result.json"):
                try:
                    status[int(f.name[1:].split("_")[0])] = "done"
                except ValueError:
                    continue
        # owner=None on rows written before per-user mode existed. That is
        # the SHARED marker, so the demo corpus already on a box stays visible
        # to everyone after logins are switched on - which is what you want
        # for plans that are ours, and why new uploads get a real owner.
        JOBS[jid] = {"path": j["path"], "pages": j.get("pages", 0),
                     "mtime": j.get("mtime"), "kinds": j.get("kinds"),
                     "sizes": j.get("sizes"), "owner": j.get("owner"),
                     "bid": j.get("bid"), "status": status}


_load_registry()


def _job_dir(job_id: str) -> Path:
    d = JOBS_DIR / job_id
    d.mkdir(parents=True, exist_ok=True)
    return d


class OpenPathBody(BaseModel):
    path: str


@app.get("/api/recent")
def recent_documents(request: Request):
    """Plan sets already on this box, newest first.

    On a hosted box filesystem paths are refused and /api/samples is empty, so
    without this there is no way to reopen a document that is already here -
    the operator would re-upload a 40 MB set, and on a large one wait out a
    detection the server had already done. Nothing here exposes a path: the
    only handle is the job id, which is all the reopen route takes.
    """
    me = _who(request)
    staff = _is_staff(request)
    out = []
    with JOBS_LOCK:
        items = list(JOBS.items())
    for jid, j in items:
        owner = j.get("owner")
        # In per-user mode you see your own plus the shared set (owner None,
        # which is our demo corpus and anything registered before logins were
        # switched on). In single-password mode `me` is None and nothing is
        # filtered, which is exactly how this behaved before.
        if not staff and owner is not None and owner != me:
            continue
        src = Path(j.get("path", ""))
        try:
            if not src.exists():
                continue            # the file moved; a dead row helps nobody
            mtime = src.stat().st_mtime
        except OSError:
            continue
        d = JOBS_DIR / jid
        done = 0
        try:
            done = len(list(d.glob("p*_result.json")))
        except OSError:
            pass
        out.append({
            "job_id": jid,
            "name": _display_name(src),
            "pages": int(j.get("pages") or 0),
            # how much of it is already detected, so the operator can tell a
            # set that will open instantly from one that will not
            "detected": done,
            "uploaded": bool(_UPLOAD_PREFIX.match(src.name)),
            "when": mtime,
            # The UI groups on these rather than re-deriving ownership, and
            # `deletable` keeps that judgement on the server where it is
            # enforced instead of in JavaScript where it is only cosmetic.
            "owner": owner,
            "mine": owner is not None and owner == me,
            "shared": owner is None,
            "deletable": staff or (owner == me),
            "bid": j.get("bid"),
        })
    out.sort(key=lambda r: -r["when"])
    return out[:60]

# ---- OPEN BIDS (Bid Sniper) --------------------------------------------------
# The landing page's third tab. Bid Sniper (:8200 on the operator's own
# machine) watches the Utah portals and holds the plan sets; the engine PULLS
# from it so the job belongs to whoever clicked, and the sniper never needs
# an engine password. takeoff_engine/bids.py has the reasoning and the pure
# parts; these two routes are the glue.
BID_SNIPER_URL = _bids.sniper_url(HOSTED)


def _bid_job_visible(jid, me, staff) -> bool:
    """A linked job counts as 'here' only when this account could open it -
    the same rule /api/recent applies - and its PDF is still on disk."""
    if not jid:
        return False
    j = JOBS.get(jid)
    if not j or not Path(j.get("path", "")).exists():
        return False
    owner = j.get("owner")
    return staff or owner is None or owner == me


@app.get("/api/bids")
def list_bids(request: Request):
    """Open, relevant, undismissed bids from Bid Sniper, best first.

    Never raises for a sniper that is down: the reply says enabled/ok and
    an error line, and the tab prints that instead of an empty grid. On a
    box with no BID_SNIPER_URL (every hosted box) enabled is false and the
    UI does not draw the tab at all."""
    if not BID_SNIPER_URL:
        return {"enabled": False, "ok": False, "url": "", "bids": []}
    try:
        with _bids.Sniper(BID_SNIPER_URL) as s:
            rows = s.list_open()
    except Exception as e:
        return {"enabled": True, "ok": False, "url": BID_SNIPER_URL,
                "error": "%s: %s" % (e.__class__.__name__, str(e)[:160]),
                "bids": []}
    me = _who(request)
    staff = _is_staff(request)
    out = _bids.open_bids(rows)
    with JOBS_LOCK:
        for b in out:
            b["job_here"] = _bid_job_visible(b.get("takeoff_job"), me, staff)
    return {"enabled": True, "ok": True, "url": BID_SNIPER_URL, "bids": out}


def _bid_detect_core(job_id: str) -> None:
    """After a pull: triage the set (text layer only, seconds) and queue
    detection on the pages it calls core. Runs on its own thread so the
    pull answers as soon as the job exists and the page opens while the
    sheets are still being read - the page strip shows them running."""
    try:
        tri = job_triage(job_id)
    except Exception:
        return
    for pg in tri.get("pages") or []:
        if pg.get("tier") == "core":
            try:
                _enqueue_detect(job_id, int(pg["page"]))
            except Exception:
                pass


@app.get("/api/bids/{lid}/plans")
def bid_plans(lid: str):
    """The PDFs the sniper holds for a bid, fetched first (a Bonfire bundle
    is a zip of the plan set, the RFB text, the agreement template...), each
    with page count and a drawings/text/mixed profile from the sniper. The
    chooser in the UI is built from this; /pull?file=<i> takes one."""
    if not BID_SNIPER_URL:
        raise HTTPException(404, "Bid Sniper is not configured on this server")
    try:
        with _bids.Sniper(BID_SNIPER_URL) as s:
            plan = s.plans(lid, fetch=True)
    except requests.HTTPError as e:
        raise HTTPException(502, "Bid Sniper answered %s for %s"
                            % (getattr(e.response, "status_code", "?"), lid))
    except Exception as e:
        raise HTTPException(502, "Bid Sniper did not answer: %s: %s"
                            % (e.__class__.__name__, str(e)[:160]))
    files = [f for f in (plan.get("files") or []) if isinstance(f, dict)]
    return {"id": lid, "title": plan.get("title"), "agency": plan.get("agency"),
            "documents": int(plan.get("documents") or 0), "files": files,
            "suggest": _bids.pick_files(files)}


@app.post("/api/bids/{lid}/pull")
def pull_bid(lid: str, request: Request, detect: int = 1, file: int = -1):
    """Make a job from a bid: have the sniper download the plan set, stream
    the drawing PDF here, register it exactly as an upload would, remember
    which bid it came from, and tell the sniper the job id.

    Same content-addressed path as /api/upload, so pulling the same bid
    twice lands on the same job with its detections and saved decisions.
    `file` picks one of the sniper's PDFs by index when the biggest is not
    the drawing set; `detect=0` skips the triage-and-detect kick-off."""
    if not BID_SNIPER_URL:
        raise HTTPException(404, "Bid Sniper is not configured on this server")
    try:
        with _bids.Sniper(BID_SNIPER_URL) as s:
            plan = s.plans(lid, fetch=True)
            f = _bids.pick_plan(plan.get("files"), file)
            if f is None:
                raise HTTPException(
                    409, "Bid Sniper could not fetch a plan PDF for this bid "
                    "(%d document link(s) listed). Download it from the "
                    "portal in your browser and upload it here."
                    % int(plan.get("documents") or 0))
            raw = s.file_bytes(lid, int(f["i"]))
    except HTTPException:
        raise
    except requests.HTTPError as e:
        raise HTTPException(502, "Bid Sniper answered %s for %s"
                            % (getattr(e.response, "status_code", "?"),
                               getattr(e.request, "path_url", lid)))
    except Exception as e:
        raise HTTPException(502, "Bid Sniper did not answer: %s: %s"
                            % (e.__class__.__name__, str(e)[:160]))
    if raw[:4] != b"%PDF":
        raise HTTPException(502, "the file Bid Sniper sent is not a PDF")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    stem = Path(f.get("name") or "plans.pdf").name
    dest = JOBS_DIR / f"upload_{digest}_{stem}"
    if not dest.exists() or dest.stat().st_size != len(raw):
        dest.write_bytes(raw)
    reg = _register(dest, classify=True, owner=_who(request))
    jid = reg["job_id"]
    meta = _bids.bid_meta(plan, lid)
    with JOBS_LOCK:
        if jid in JOBS:
            JOBS[jid]["bid"] = meta
        _save_registry()
    base = str(request.base_url).rstrip("/")
    with _bids.Sniper(BID_SNIPER_URL) as s:
        linked = s.link(lid, jid, f"{base}/#job={jid}")
    if detect:
        threading.Thread(target=_bid_detect_core, args=(jid,),
                         name="bid-detect", daemon=True).start()
    reg.update({"bid": meta, "file": stem, "size": len(raw), "linked": linked,
                "detect": "core pages, after triage" if detect else "off"})
    return reg


@app.get("/api/job/{job_id}/open")
def reopen_job(job_id: str):
    """Reopen a document already on this box, by job id.

    Same reply shape as /api/open-path and /api/upload so the UI has one code
    path for all three. By id, never by path - a hosted box must not take a
    filesystem location from the client.
    """
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(404, "unknown job")
        src = Path(j.get("path", ""))
        pages = int(j.get("pages") or 0)
        kinds = j.get("kinds")
        sizes = j.get("sizes")
    if not src.exists():
        raise HTTPException(410, "that plan set is no longer on this server")
    if not sizes:
        # registry rows predate sizes, or classification never finished.
        # THE ROW'S OWNER TRAVELS WITH IT. _register's default owner is
        # None, and None is the SHARED marker: every signed-in account may
        # list, open, render and detect an owner-less row. Until 2026-09-01
        # this call passed no owner, so one reopen of a client's un-sized
        # row - by the client, or by staff on their behalf - minted a
        # second row for the SAME PDF with owner None and the document was
        # on every other client's landing page. _register never rewrites
        # an existing row's owner (its reuse key includes the owner, and
        # reuse repairs mtime only), so passing the owner through is the
        # whole fix; a genuinely shared row (owner None) stays shared.
        # tests/test_reopen_owner.py pins both.
        return _register(src, owner=j.get("owner"))
    return {"job_id": job_id, "pages": pages, "kinds": kinds,
            "sizes": sizes, "name": _display_name(src)}


@app.get("/api/projects")
def list_projects(request: Request):
    """The landing page's card grid: every plan set this account may see,
    enriched with what a card needs at a glance.

    Same visibility rule as /api/recent (yours plus the shared set; staff and
    single-password mode see everything) and the same handle - the job id,
    never a filesystem path, so it is safe on a hosted box.

    Per job: `detected` counts p<N>_result.json in the job dir exactly as
    /api/recent does; `approved` counts p<N>_elements.json, which only an
    approve writes, so counting them IS the approved-page count with no new
    bookkeeping; `when` is the job DIRECTORY's mtime (last engine activity:
    a detect, a render, an approve), falling back to the PDF's own mtime for
    a job that was registered but never opened; `has_thumb` says whether the
    page-0 strip thumbnail (p0_28.png, the page-strip's own 28-dpi cache) is
    already on disk - the UI only puts an <img> on cards that have one, so
    listing 60 jobs never triggers 60 fresh page renders.

    The reply says its own coverage ({total, projects}): if the list is ever
    longer than the cap, the landing page can say "showing N of M" instead of
    silently reading as "my plans are gone".

    DEMO ROWS ARE NEVER CAPPED. The landing page tabs "Demo plans" against
    "Your projects", and a demo that fell off the end of the newest-200 slice
    would make the demo tab quietly wrong - on this laptop the demo sheets sit
    at position 300-odd of 420. So the slice applies to the OTHER rows only
    and every demo row is sent, which is also what lets the demo tab claim
    full search reach honestly.

    `demo` reports the set's health: how many the manifest expects, how many
    are actually on this box, and which are missing - so the UI can say "the
    demo plans are not on this server" instead of drawing an empty tab.
    """
    me = _who(request)
    staff = _is_staff(request)
    out = []
    demos = []
    with JOBS_LOCK:
        items = list(JOBS.items())
    for jid, j in items:
        owner = j.get("owner")
        if not staff and owner is not None and owner != me:
            continue
        src = Path(j.get("path", ""))
        try:
            if not src.exists():
                continue        # the file moved; a dead card helps nobody
            when = src.stat().st_mtime
        except OSError:
            continue
        name = _display_name(src)
        dm = _demo_meta(name, owner)
        # A demo card pictures the sheet its card describes, not the cover
        # sheet in front of it: Oncor page 0 is a title block and page 2 is
        # the grading plan the demo is about. Everything else still pictures
        # page 0, which is what an operator recognises their own upload by.
        thumb_page = int(dm.get("page") or 0) if dm else 0
        d = JOBS_DIR / jid
        detected = 0
        approved = 0
        has_thumb = False
        try:
            if d.is_dir():
                when = max(when, d.stat().st_mtime)
                detected = len(list(d.glob("p*_result.json")))
                approved = len(list(d.glob("p*_elements.json")))
                has_thumb = (d / ("p%d_28.png" % thumb_page)).exists()
                if not has_thumb and thumb_page:
                    # the demo page has never been rendered but page 0 has:
                    # a picture of the wrong page beats "not opened yet"
                    thumb_page = 0
                    has_thumb = (d / "p0_28.png").exists()
        except OSError:
            pass
        row = {
            "job_id": jid,
            "name": name,
            "pages": int(j.get("pages") or 0),
            "detected": detected,
            "approved": approved,
            "when": when,
            "has_thumb": has_thumb,
            # which page has_thumb is talking about (0 for everything that is
            # not a demo, so no existing reader changes behaviour)
            "thumb_page": thumb_page,
            "uploaded": bool(_UPLOAD_PREFIX.match(src.name)),
            "owner": owner,
            "mine": owner is not None and owner == me,
            "shared": owner is None,
            # A demo is a SHARED row whose name is in demo-plans.json. It is
            # the "shared demo" the badge has always named, now said in the
            # data instead of guessed at in JavaScript.
            "demo": dm is not None,
            # one line for the card: what this sheet demonstrates
            "demo_note": (dm or {}).get("shows") or None,
            # the sheet a visitor should land on (engine page index)
            "demo_page": (dm or {}).get("page") if dm else None,
            # UNCHANGED. Deleting a demo is still the server's call and still
            # the rule it already had (DELETE /api/job refuses an owner-less
            # row to anyone who is not staff), so a hosted client sees no
            # delete on a demo card and our own box still can clean one off.
            "deletable": staff or (owner == me),
            # the solicitation this plan set was pulled from, when Open bids
            # made the job: {id, title, agency, due, url}. The card prints
            # it under the name; an upload has none.
            "bid": j.get("bid"),
        }
        (demos if dm is not None else out).append(row)

    demos.sort(key=lambda r: -r["when"])
    out.sort(key=lambda r: -r["when"])
    have = {r["name"] for r in demos}
    return {
        # unchanged meaning: every row this account may see
        "total": len(out) + len(demos),
        # every demo, then the newest 200 of everything else
        "projects": demos + out[:200],
        "demo": {"expected": len(DEMO_PLANS),
                 "present": len(have),
                 "missing": sorted(n for n in DEMO_PLANS if n not in have)},
    }


# FEEDBACK. Replaces a phone call. Stored as JSONL beside the job store so it
# survives a redeploy the way the detection cache does, with no new service and
# no new credential; staff pull it as CSV and triage in a spreadsheet.
#
# The free text is the least useful part. "The lines are wrong" cannot be acted
# on - which sheet, which style, whose account, which build? So the CONTEXT is
# captured automatically and the reporter does not have to know any of it.
FEEDBACK_LOG = JOBS_DIR / "feedback.jsonl"

# Plain language, because the people choosing are estimators mid-task, not
# triagers. Ordered worst-first so the list reads as a scale.
SEVERITIES = ["blocked", "wrong-number", "annoying", "idea"]
CATEGORIES = ["wrong-quantity", "missed-lines", "extra-lines", "error",
              "confusing", "feature", "other"]
MAX_MSG = 4000


class FeedbackBody(BaseModel):
    message: str
    severity: str = "annoying"
    category: str = "other"
    context: dict = {}
    shot: str | None = None          # optional canvas PNG, as a data URL


@app.post("/api/feedback")
def submit_feedback(body: FeedbackBody, request: Request):
    msg = (body.message or "").strip()
    if not msg:
        raise HTTPException(400, "say what happened")
    if len(msg) > MAX_MSG:
        msg = msg[:MAX_MSG] + " [truncated]"
    # An unknown value is recorded as-is rather than rejected: losing a report
    # because a future build sent a new category would be the worst trade here.
    sev = body.severity if body.severity in SEVERITIES else "annoying"
    cat = body.category if body.category in CATEGORIES else "other"

    fid = uuid.uuid4().hex[:10]
    shot_name = None
    if body.shot and body.shot.startswith("data:image/png;base64,"):
        import base64 as _b64
        try:
            raw = _b64.b64decode(body.shot.split(",", 1)[1], validate=True)
            # A screenshot is worth more than the words on a drawing tool, but
            # it must not be able to fill the disk one report at a time.
            if len(raw) <= 4_000_000:
                shot_name = f"feedback_{fid}.png"
                (JOBS_DIR / shot_name).write_bytes(raw)
        except Exception:
            shot_name = None

    ctx = body.context if isinstance(body.context, dict) else {}
    row = {
        "id": fid,
        "when": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "user": _who(request) or "(no accounts configured)",
        "severity": sev,
        "category": cat,
        "message": msg,
        "status": "new",             # staff triage; the client never sets this
        "document": str(ctx.get("document") or "")[:200],
        "page": ctx.get("page"),
        "style": str(ctx.get("style") or "")[:120],
        "scale": str(ctx.get("scale") or "")[:80],
        "chains": ctx.get("chains"),
        "app": str(ctx.get("app") or "")[:40],
        "url": str(ctx.get("url") or "")[:300],
        "browser": (request.headers.get("user-agent") or "")[:200],
        "shot": shot_name,
    }
    try:
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        raise HTTPException(500, f"could not save that: {e}")
    return {"ok": True, "id": fid}


def _feedback_rows() -> list:
    if not FEEDBACK_LOG.exists():
        return []
    out = []
    for line in FEEDBACK_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except ValueError:
            continue          # one bad line must not hide every other report
    # worst first, then newest - the order you actually want to triage in
    order = {s: i for i, s in enumerate(SEVERITIES)}
    out.sort(key=lambda r: (order.get(r.get("severity"), 9),
                            r.get("when", "")), reverse=False)
    return out


@app.get("/api/feedback")
def list_feedback(request: Request):
    if not _is_staff(request):
        raise HTTPException(404, "not found")
    rows = _feedback_rows()
    counts: dict = {}
    for r in rows:
        counts[r.get("severity", "?")] = counts.get(r.get("severity", "?"), 0) + 1
    return {"total": len(rows), "by_severity": counts, "rows": rows}


@app.get("/api/feedback.csv")
def feedback_csv(request: Request):
    """Excel-ready. Staff only, and 404 rather than 403 for a client, so the
    endpoint does not advertise that other people's reports exist."""
    if not _is_staff(request):
        raise HTTPException(404, "not found")
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    cols = ["when", "severity", "category", "status", "user", "message",
            "document", "page", "style", "scale", "chains", "app", "url",
            "browser", "shot", "id"]
    w = _csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore",
                        lineterminator="\r\n")
    w.writeheader()
    for r in _feedback_rows():
        w.writerow(r)
    # utf-8-sig: Excel reads a plain UTF-8 CSV as the system codepage and
    # mangles anything non-ASCII in a comment. The BOM is what makes it open
    # correctly by double-click, which is the whole delivery mechanism here.
    data = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=data, media_type="text/csv",
        headers={"Content-Disposition":
                 'attachment; filename="core-takeoff-feedback.csv"'})


@app.get("/api/beta-notice")
def beta_notice():
    """The UI renders this rather than hard-coding it, so the banner on screen
    and the stamp on a printed report are always the same words."""
    return {"short": BETA_SHORT, "long": BETA_LONG}


@app.get("/api/config")
def ui_config():
    """What the UI needs to know about THIS box before it draws itself.

    Hosted mode refuses filesystem paths (they would be an arbitrary file
    read on a server), so on a hosted box uploading is the ONLY way in and
    the path box has to go. The UI cannot infer that from an empty sample
    list - an empty list is also what a local box with no plans looks
    like."""
    return {"hosted": HOSTED,
            "auth": bool(_UI_PASSWORD),
            "workers": TAKEOFF_WORKERS,
            "page_budget_s": TAKEOFF_PAGE_BUDGET_S}


@app.get("/api/samples")
def list_samples():
    """Convenience: sample plans on this machine, for quick opening.
    Confined to PLAN_ROOTS (minus our own upload dir); empty when hosted."""
    out = []
    if HOSTED:
        return out
    for root in PLAN_ROOTS:
        if root == JOBS_DIR.resolve() or not root.is_dir():
            continue
        for p in sorted(root.rglob("*.pdf")):
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > 50_000:
                out.append({"path": str(p), "name": p.name, "mb": round(size / 1e6, 1)})
    return out[:200]


_UPLOAD_PREFIX = re.compile(r"^upload_[0-9a-f]{8,12}_")


def _display_name(path) -> str:
    """The document name the operator sees - and the key their saved work
    hangs on (webui/app.js docKey). Our own "upload_<hash>_" prefix is
    plumbing and must not appear in it, or the same plan set would look like
    a different document every time it arrived."""
    return _UPLOAD_PREFIX.sub("", Path(path).name)


# THE DEMO SET. demo-plans.json is the one list of plans that are OURS and may
# be shown to anyone; see the _readme inside it for the provenance rule.
#
# This is NOT a second visibility notion. A row is a demo only when it is
# already SHARED under the rule /api/recent and /api/projects have always
# used - owner is None - and its display name is in the manifest. So a client
# who uploads a file called "C8.0-STORM-LAYOUT-Rev.1.pdf" gets their own
# private job (owner = them), exactly as before, and it is not a demo.
#
# Read once at import. A box that ships without the file simply has no demo
# set, which the landing page then says out loud rather than drawing an empty
# tab (/api/projects reports expected/present/missing).
DEMO_MANIFEST = ROOT / "demo-plans.json"


def _load_demo_plans() -> dict:
    """{display name -> {"page": int, "shows": str}}. Never raises: a broken
    or absent manifest costs the demo tab, it must not cost the service."""
    try:
        raw = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    plans = raw.get("plans")
    if not isinstance(plans, dict):
        return {}
    out = {}
    for name, meta in plans.items():
        if not isinstance(name, str) or not name.strip():
            continue
        meta = meta if isinstance(meta, dict) else {}
        try:
            page = int(meta.get("page") or 0)
        except (TypeError, ValueError):
            page = 0
        out[name] = {"page": max(0, page),
                     "shows": str(meta.get("shows") or "")}
    return out


DEMO_PLANS = _load_demo_plans()


def _demo_meta(name: str, owner) -> dict | None:
    """The manifest entry for a job row, or None when it is not a demo.
    Shared-ness is the gate; the name only selects WHICH demo."""
    if owner is not None:
        return None
    return DEMO_PLANS.get(name)


def _classify_async(job_id: str, src: str, n: int) -> None:
    """Per-page vector/raster classification, off the request thread.

    Lifted verbatim out of _register so a second caller can use it: when a
    demo re-points its one job at a source whose bytes moved, the kinds have
    to be recomputed for the SAME job rather than by registering a new one.
    """
    def _classify_later():
        try:
            d2 = pymupdf.open(src)
            got = []
            for i in range(n):
                got.append(classify_page(d2[i]).kind)
                with JOBS_LOCK:
                    j2 = JOBS.get(job_id)
                    if j2 is None:
                        d2.close()
                        return          # job went away; stop working
                    j2["kinds_progress"] = len(got)
            d2.close()
        except Exception:
            # A classification failure must not cost the operator the job:
            # every page falls back to "unknown", which the UI renders as
            # no kind label at all rather than as an error.
            got = ["unknown"] * n
        with JOBS_LOCK:
            j2 = JOBS.get(job_id)
            if j2 is not None:
                j2["kinds"] = got
                j2["kinds_progress"] = n
                _save_registry()
    threading.Thread(target=_classify_later, daemon=True).start()


# ---- THE DEMO TAB IS THE MANIFEST, NOT THIS BOX'S HISTORY ------------------
# It used to be built from JOB ROWS whose display name matched the manifest,
# and two things were wrong with that.
#
#   * It showed the box's history rather than the demo set. This laptop holds
#     15 registered jobs for the Oncor sheet and 14 for Belle Farms
#     Structural - one for every time the corpus PDF's mtime moved under
#     _register's (path, mtime, owner) reuse key - so the tab read "Demo
#     plans 140" for a manifest of seven drawings.
#   * Every one of those rows carried a DETECTION computed weeks ago, so a
#     visitor clicking a demo card was handed a finished takeoff instantly.
#     That is a pre-warmed cache presented as a live run, which is the one
#     thing this repo's demo rules forbid - the same reasoning that put
#     POST /reset in the service in the first place.
#
# So /api/demo-plans below answers from demo-plans.json alone: exactly the
# manifest's plans, one entry each, on a box with zero jobs for them or with
# sixty-three. Jobs are an implementation detail of OPENING one, and
# POST /api/demo/{name}/open guarantees the visitor gets an undetected
# document. /api/projects is UNCHANGED - it still flags demo rows, which is
# what keeps them off "Your projects".

# Page rasters for the demo cards. Deliberately NOT inside a job directory:
# the card's picture used to be the job's own p<N>_28.png page-strip cache,
# and the fresh-open sweep deletes that - so a demo card would have lost its
# thumbnail the first time anybody opened it. A demo thumbnail is a picture
# of the PDF and outlives every run of the engine over it.
DEMO_THUMBS = JOBS_DIR / "_demo_thumbs"

# name -> resolved source path. Re-validated on read, and a full plan-root
# scan is rate-limited: an unseeded box must not walk the corpus on every
# paint of the landing page. A client's file NEVER enters this cache - see
# _demo_scan_refused.
_DEMO_SRC: dict = {}
_DEMO_SCAN_AT = 0.0
_DEMO_SCAN_EVERY_S = 30.0
_DEMO_PAGES: dict = {}
# resolved paths the scan refused as a client's file, so the collision is
# logged once per file rather than once per 30 s rescan
_DEMO_REFUSED: set = set()


def _demo_scan_refused(p: Path, name: str, jobs_root: Path, owned: set) -> bool:
    """True when a plan-root scan candidate is a CLIENT'S file and must not
    become the demo, whatever it is called. Two belts, either one refuses:
      (a) it lies under JOBS_DIR. A staff seed there is a shared row and the
          row loop in _demo_source has already found it, so a PDF the scan
          meets under JOBS_DIR is somebody's upload;
      (b) its path is registered to a row with an owner, under any root.
    Both compare RESOLVED paths, so another spelling of the same file (../,
    a symlink, drive-letter case) is the same file, and a path that cannot
    be resolved is refused too - it cannot be shown to be the corpus.
    The collision is logged once so staff can see that a client set is
    wearing a demo name; nothing is cached for it."""
    try:
        rp = p.resolve(strict=True)
    except (OSError, RuntimeError):
        return True
    key = os.path.normcase(str(rp))
    why = None
    try:
        rp.relative_to(jobs_root)
        why = "it sits inside the jobs directory, so it is an upload"
    except ValueError:
        if key in owned:
            why = "it is registered to a client account"
    if why is None:
        return False
    if key not in _DEMO_REFUSED:
        _DEMO_REFUSED.add(key)
        print("[demo] a client file collides with the demo name %r: not "
              "serving %s as the demo because %s. Only a STAFF upload of "
              "our own copy publishes that name." % (name, rp, why),
              file=sys.stderr)
    return True


def _demo_rows_split() -> tuple:
    """(shared, owned): the resolved, case-folded paths of the owner-less
    rows and of the owned rows, read under JOBS_LOCK. A path that cannot
    be resolved is in neither set - it cannot be shown to be anything."""
    with JOBS_LOCK:
        raw = [(j.get("path", ""), j.get("owner")) for j in JOBS.values()]
    shared, owned = set(), set()
    for path, owner in raw:
        try:
            key = os.path.normcase(str(Path(path).resolve()))
        except (OSError, RuntimeError):
            continue
        (shared if owner is None else owned).add(key)
    return shared, owned


def _demo_cache_ok(p: Path, name: str) -> bool:
    """True while a cached demo source is STILL one we may serve: the path
    of a SHARED row (how a seeded box has it - a staff upload under
    JOBS_DIR, or a row from before per-user mode), or an unowned corpus
    file the scan would accept right now.

    The writers into _DEMO_SRC apply _demo_scan_refused; the cache hit did
    not. So a path that entered the cache by any other route, or a row
    whose owner changed underneath it, was served as the demo until the
    process restarted. Re-checked on every hit: the row read is one lock
    acquisition and a few path resolves, nothing that walks the disk."""
    try:
        key = os.path.normcase(str(p.resolve(strict=True)))
    except (OSError, RuntimeError):
        return False
    shared, owned = _demo_rows_split()
    if key in shared:
        return True
    return not _demo_scan_refused(p, name, JOBS_DIR.resolve(), owned)


def _demo_source(name: str):
    """The PDF behind a manifest entry, or None when this box does not have it.

    Two places it can legitimately be:
      * already registered as a SHARED job (owner None) - which is how a
        HOSTED box has it: /api/upload writes JOBS_DIR/upload_<hash>_<name>.pdf
        and makes the row shared only when STAFF uploaded a manifest name;
      * loose under PLAN_ROOTS - which is how this laptop has it, straight out
        of the TakeoffPro AI corpus.
    Registered rows are checked first, so a seeded box never walks its plan
    roots at all.

    THE SCAN IS FOR THE CORPUS, NEVER FOR A CLIENT'S FILE. The default
    PLAN_ROOTS ends with JOBS_DIR, and on a box without the corpus beside the
    repo - the hosted box - it degrades to JOBS_DIR alone, where the only
    PDFs are client uploads. Before this was pinned, an unseeded hosted box
    scanned those, found a client's own "Belle Farms Structural Plans.pdf",
    and _demo_job registered a SHARED row over it: every account on the box
    could open, render and detect that client's set through the demo card,
    and it appeared on their /api/recent. So a candidate under JOBS_DIR, or
    registered to any owner, is refused by _demo_scan_refused and is never
    written into _DEMO_SRC. A client set that collides with a demo name stays
    private until staff upload OUR copy (tests/test_demo_source_scan.py).

    Never raises and never invents: a name it cannot place returns None, and
    the card then says so in words rather than erroring or vanishing.
    """
    global _DEMO_SCAN_AT
    if name not in DEMO_PLANS:
        return None
    hit = _DEMO_SRC.get(name)
    if hit:
        p = Path(hit)
        if p.exists() and _demo_cache_ok(p, name):
            return p
        # the file went away, or it is a client's: do not serve it
        _DEMO_SRC.pop(name, None)
    with JOBS_LOCK:
        rows = sorted((jid, j.get("path", "")) for jid, j in JOBS.items()
                      if j.get("owner") is None)
    for _jid, raw in rows:
        p = Path(raw)
        try:
            if _display_name(p) == name and p.exists():
                _DEMO_SRC[name] = str(p)
                return p
        except OSError:
            continue
    now = time.time()
    if now - _DEMO_SCAN_AT < _DEMO_SCAN_EVERY_S:
        return None                     # scanned recently and it was not there
    _DEMO_SCAN_AT = now
    want = set(DEMO_PLANS) - set(_DEMO_SRC)
    jobs_root = JOBS_DIR.resolve()
    _shared, owned = _demo_rows_split()
    for root in PLAN_ROOTS:
        if not want:
            break
        if not root.is_dir():
            continue
        try:
            for p in sorted(root.rglob("*.pdf")):
                n = _display_name(p)
                if n not in want:
                    continue
                if _demo_scan_refused(p, n, jobs_root, owned):
                    continue
                _DEMO_SRC[n] = str(p)
                want.discard(n)
                if not want:
                    break
        except OSError:
            continue
    hit = _DEMO_SRC.get(name)
    return Path(hit) if hit else None


def _demo_pages(src: Path):
    """Page count for a card, or None. Cheap - pymupdf.open plus page_count,
    no classification and no drawing read - and memoised on (path, mtime,
    size) so a page of cards costs one open per plan per edit of the file."""
    try:
        st = src.stat()
    except OSError:
        return None
    key = (str(src), round(st.st_mtime, 2), st.st_size)
    if key in _DEMO_PAGES:
        return _DEMO_PAGES[key]
    try:
        doc = pymupdf.open(src)
        n = int(doc.page_count)
        doc.close()
    except Exception:
        return None                     # a card without a page count, not a 500
    _DEMO_PAGES[key] = n
    return n


def _sweep_job_dir(job_id: str):
    """Unlink every file in a job's directory and forget its page statuses.

    The shared body of POST /reset and of a demo's fresh open: both mean the
    same thing by "cold", and two copies of it would drift apart. The guard
    re-derives the directory from JOBS_DIR and refuses anything that is not a
    direct child of it, so a crafted job id cannot walk out of the jobs tree.
    Returns (removed, failed) - a file another process holds open is reported,
    never silently counted as swept.
    """
    d = (JOBS_DIR / job_id).resolve()
    if d.parent != JOBS_DIR.resolve() or not d.is_dir():
        return (0, 0)
    removed = failed = 0
    for f in sorted(d.iterdir()):
        if not f.is_file():
            continue
        try:
            f.unlink()
            removed += 1
        except OSError:
            failed += 1                 # held open elsewhere; report the rest
    # The in-memory status map is what makes the UI think a page is done.
    # Leaving it behind would show "detected" over an empty directory.
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is not None:
            j["status"] = {}
    return (removed, failed)


def _demo_job(name: str, src: Path) -> str:
    """The ONE job a demo plan is ever opened as.

    BOUNDED BY CONSTRUCTION. A demo open never registers a second job for a
    plan that already has one, so three opens of the same demo leave the
    registry exactly as they found it. That is the whole point: unbounded
    fresh jobs are how this box reached 15 rows for Oncor and 14 for Belle
    Farms Structural. _register's reuse key is (path, mtime, owner), the
    corpus PDF's mtime moves whenever OneDrive touches it, and every open
    after a sync therefore registered a NEW job and orphaned the last one's
    cache. Keying the demo's job on the manifest NAME instead of on mtime is
    what closes that hole.

    Deterministic: the lowest job id among the shared rows for this name wins,
    preferring one whose recorded mtime still matches the file, so the same
    plan resolves to the same job on every open and across restarts.
    """
    try:
        mtime = round(src.stat().st_mtime, 2)
    except OSError:
        mtime = None
    with JOBS_LOCK:
        cands = sorted(jid for jid, j in JOBS.items()
                       if j.get("owner") is None
                       and _display_name(Path(j.get("path", ""))) == name)
        fresh = [jid for jid in cands
                 if JOBS[jid].get("path") == str(src)
                 and JOBS[jid].get("mtime") == mtime
                 and JOBS[jid].get("sizes")]
        jid = fresh[0] if fresh else (cands[0] if cands else None)
    if jid is None:
        # nothing registered for it yet: the ordinary path, owner-less so the
        # row stays SHARED and keeps satisfying _demo_meta's sharedness gate
        return _register(src, owner=None)["job_id"]
    if not fresh:
        # The row exists but points at bytes that have moved. Re-derive what
        # _register would have derived and write it back into the SAME row,
        # rather than letting _register mint a new one.
        n, sizes = 0, []
        try:
            doc = pymupdf.open(src)
            n = doc.page_count
            for p in range(n):
                r = doc[p].rect
                sizes.append([round(r.width, 2), round(r.height, 2)])
            doc.close()
        except Exception:
            return _register(src, owner=None)["job_id"]
        # DECIDE under the lock, CALL _register after it. JOBS_LOCK is a
        # plain Lock, and _register takes it: the old branch called
        # _register from inside this block when the row had vanished, and
        # the demo open then deadlocked the web process - every request
        # after it queued on the lock forever. Reproduced in
        # tests/test_demo_job_lock.py by popping the row while the PDF is
        # being re-read.
        with JOBS_LOCK:
            j = JOBS.get(jid)
            if j is not None:
                j["path"] = str(src)
                j["mtime"] = mtime
                j["pages"] = n
                j["sizes"] = sizes
                j["kinds"] = None
                j["kinds_progress"] = 0
                j["status"] = {}
                _save_registry()
        if j is None:
            return _register(src, owner=None)["job_id"]
        _classify_async(jid, str(src), n)
    return jid


@app.get("/api/demo-plans")
def demo_plans():
    """The demo tab, straight off demo-plans.json.

    Exactly the manifest's plans, one entry each, ALWAYS - this box's job rows
    do not add an entry and do not remove one. `available` is the honest
    degradation: a hosted box that has not been seeded still gets every card,
    each saying in words that its drawing is not here, instead of an empty tab
    or a 500.

    Nothing here is a filesystem path. The only handle is the manifest name,
    which is already public in the shipped file.
    """
    plans = []
    present = 0
    for name, meta in DEMO_PLANS.items():
        src = _demo_source(name)
        if src is not None:
            present += 1
        plans.append({
            "name": name,
            "shows": meta.get("shows") or "",
            # the sheet a visitor lands on (engine page index, 0-based)
            "page": int(meta.get("page") or 0),
            "available": src is not None,
            # cheaply known, or null - a card prints what it has
            "pages": _demo_pages(src) if src is not None else None,
        })
    return {
        "version": 1,
        "plans": plans,
        "expected": len(DEMO_PLANS),
        "present": present,
        "missing": sorted(p["name"] for p in plans if not p["available"]),
    }


@app.get("/api/demo/{name}/thumb")
def demo_thumb(name: str):
    """A page RASTER for a demo card - never a detection.

    Rendering the sheet ahead of time is honest: it is a picture of the PDF,
    the same thing any reader would draw, and it says nothing at all about
    what the engine found. The dishonest one is a cached DETECTION, which is
    exactly what a demo open throws away.
    """
    if name not in DEMO_PLANS:
        raise HTTPException(404, "not a demo plan")
    src = _demo_source(name)
    if src is None:
        raise HTTPException(404, "that demo plan is not on this server")
    page = int(DEMO_PLANS[name].get("page") or 0)
    try:
        st = src.stat()
    except OSError:
        raise HTTPException(404, "that demo plan is not on this server")
    tag = "%s_%d_%d_p%d" % (
        hashlib.sha1(name.encode("utf-8")).hexdigest()[:12],
        int(st.st_mtime), st.st_size, page)
    DEMO_THUMBS.mkdir(parents=True, exist_ok=True)
    cache = DEMO_THUMBS / (tag + ".png")
    if not cache.exists():
        try:
            doc = pymupdf.open(src)
            p = page if 0 <= page < doc.page_count else 0
            pix = doc[p].get_pixmap(dpi=28)
            tmp = cache.with_name("%s.%s.tmp"
                                  % (cache.name, uuid.uuid4().hex[:8]))
            # tobytes, not pix.save: save() infers the format from the
            # extension and the temp name ends ".tmp"
            tmp.write_bytes(pix.tobytes("png"))
            doc.close()
            os.replace(tmp, cache)      # atomic: no half-PNG for the next GET
        except Exception:
            raise HTTPException(500, "could not render that page")
    return Response(cache.read_bytes(), media_type="image/png",
                    headers={"Cache-Control": "public, max-age=86400"})


@app.post("/api/demo/{name}/open")
def demo_open(name: str):
    """Open a demo plan FRESH. The visitor presses Detect and watches it work.

    THE RULE, and it is the whole reason this route exists rather than the
    card reusing /api/job/{id}/open: a demo arrives UNDETECTED, every time,
    for every visitor. A demo that opens instantly because somebody detected
    it months ago is a staged demo, and this product's own demo-integrity rule
    says a visitor watches the engine run.

    So the open sweeps everything DERIVED from the PDF first - detections,
    page renders, earthwork maps, callouts, the triage sidecar, and any
    approved export or report a previous demo run left behind. What survives
    is the PDF (never inside the job directory) and the registry row.

    WHAT HAPPENS TO THE JOB. Nothing is created and nothing accumulates: see
    _demo_job - one job per demo plan, reused forever, so three opens of a
    demo leave the registry exactly the size they found it. That job is
    owner-less, so /api/projects flags it demo:true and it therefore appears
    on NEITHER tab: not on "Your projects" (it is not the operator's upload)
    and not on "Demo plans" (that tab is the manifest now). It exists only as
    the handle the workbench holds the open document by.

    Concurrency is the honest caveat: two visitors opening the same demo at
    once share that job, so the second one's sweep resets the first one's run.
    Acceptable for a demo box with one visitor in front of it; named here so
    nobody discovers it in front of a client.
    """
    meta = DEMO_PLANS.get(name)
    if meta is None:
        raise HTTPException(404, "not a demo plan")
    src = _demo_source(name)
    if src is None:
        raise HTTPException(
            410, "\"%s\" is one of our demo plans, but its PDF is not on this "
                 "server. A box gets the demo library by running seed-demo.sh "
                 "against it." % name)
    job_id = _demo_job(name, src)
    removed, failed = _sweep_job_dir(job_id)
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(500, "the demo job went away mid-open")
        pages = int(j.get("pages") or 0)
        kinds = j.get("kinds")
        sizes = j.get("sizes")
    page = int(meta.get("page") or 0)
    # training ledger (observation only): everything after this point for this
    # job is a FRESH detection, and a trainer reading the ledger later must be
    # able to see where that boundary was.
    ledger.record("demo_opened", job_id, None, None,
                  {"name": name, "removed": removed, "failed": failed},
                  None, pdf_path=str(src))
    return {"job_id": job_id, "pages": pages, "kinds": kinds, "sizes": sizes,
            "name": name,
            # the sheet the card described, clamped to the set as it is now
            "demo_page": page if 0 <= page < pages else 0,
            # what the sweep actually did. A reset that half-failed and then
            # replayed a cached sheet during a demo is why this is reported
            # rather than assumed.
            "fresh": {"removed": removed, "failed": failed},
            "demo": True}


def _register(path: Path, classify: bool = True, owner=None) -> dict:
    """classify=False skips per-page vector/raster classification, which
    reads every drawing on every page. Rasterisation callers do not need it,
    and on a large package it dominates the cost of simply opening the file.

    Re-opening the SAME file (path + mtime) reuses its job: detect results,
    images, and the pinned AI verdict all live in the job dir, and a takeoff
    must come back identical when the same plan is opened twice."""
    doc = pymupdf.open(path)
    mtime = round(path.stat().st_mtime, 2)
    with JOBS_LOCK:
        for jid, j in JOBS.items():
            # Owner is part of the reuse key. Uploads are content-addressed,
            # so two people uploading the same PDF land on the same path - and
            # without this the second one would be handed the first one's job
            # and then be refused access to it by the ownership check.
            # RECLAIM AN INTERRUPTED OPEN. mtime is written when a job is
            # registered, but an open that dies before it finishes leaves a row
            # with mtime None - and None can never equal a real timestamp, so
            # that row could never be matched and the next open registered
            # ANOTHER one. Measured on this box: 420 registry rows, 250 of them
            # carrying mtime None, including 63 rows for one Belle Farms sheet
            # and 54 for one Oncor sheet - months of restarts and killed dev
            # servers, each leaving a permanent orphan. A null mtime means "we
            # never finished recording this", not "this is a different file",
            # so it is reclaimed by path+owner and its mtime repaired below.
            _mt = j.get("mtime")
            if (j.get("path") == str(path) and j.get("owner") == owner
                    and (_mt == mtime or _mt is None)):
                if _mt is None:
                    j["mtime"] = mtime
                kinds0 = j.get("kinds")
                sizes0 = j.get("sizes")
                # Gated on SIZES only. It used to require kinds too, so a job
                # whose classification had not finished (or was interrupted by
                # a restart) failed the reuse test and was registered again
                # under a NEW id - orphaning every cached detection in the old
                # job directory. Sizes are computed synchronously and are
                # always present; kinds may legitimately still be null.
                if sizes0:
                    doc.close()
                    return {"job_id": jid, "pages": j["pages"],
                            "kinds": kinds0, "sizes": sizes0,
                            "name": _display_name(path)}
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {"path": str(path), "pages": doc.page_count,
                        "mtime": mtime, "owner": owner, "status": {}}
        _save_registry()
    # Page RECTS are free; classification is not. classify_page reads every
    # drawing on every page, and on a dense package that dominated the cost of
    # simply opening the file: measured 16.3 s for the 20-page stice.pdf and
    # 38.0 s for the 15-page Barn Civil, all of it inside the POST /open-path
    # request thread, with nothing on screen to say the app had not hung.
    # Sizes are returned at once and the kinds arrive on their own thread; the
    # UI polls /kinds and fills the page labels in when they land.
    n_pages = doc.page_count
    sizes = []
    for p in range(n_pages):
        r = doc[p].rect
        sizes.append([round(r.width, 2), round(r.height, 2)])
    doc.close()
    kinds = None if classify else ["unknown"] * n_pages
    if classify:
        _classify_async(job_id, str(path), n_pages)
    with JOBS_LOCK:
        JOBS[job_id]["kinds"] = kinds
        JOBS[job_id]["kinds_progress"] = 0 if kinds is None else len(kinds)
        JOBS[job_id]["sizes"] = sizes
        _save_registry()
    # sizes let a caller compute exact raster pixel dimensions without
    # decoding an image (a 36x24in sheet at 300dpi is 78 megapixels)
    return {"job_id": job_id, "pages": n_pages, "kinds": kinds,
            "sizes": sizes, "name": _display_name(path)}


@app.post("/api/open-path")
def open_path(body: OpenPathBody, request: Request):
    return _register(_client_pdf(body.path), owner=_who(request))


@app.post("/api/upload")
async def upload(file: UploadFile, request: Request, classify: int = 1):
    """Take a plan set from the browser. The only way in on a hosted box.

    Named by CONTENT, not by a random id. Two things broke when it was random:
      * Re-uploading the same set wrote a new path, so _register saw a new
        file and re-detected everything - an hour of compute on a 54-sheet set
        the server had already read.
      * The review UI keys the reviewer's memory on the document NAME (see
        docKey in webui/app.js), so every upload of the same plans started
        with no labels, no prices and no scale picks. The work was there,
        filed under a name nobody could reach again.
    Same bytes now resolve to the same path, so _register's reuse test hits
    and both the cached detections and the saved decisions come back. Written
    only when genuinely new: rewriting would move the mtime reuse depends on.
    """
    raw = await file.read()
    digest = hashlib.sha256(raw).hexdigest()[:12]
    stem = Path(file.filename or "plans.pdf").name
    dest = JOBS_DIR / f"upload_{digest}_{stem}"
    if not dest.exists() or dest.stat().st_size != len(raw):
        dest.write_bytes(raw)
    # Seeding the demo set is a staff upload of a plan the manifest already
    # names, so that is what makes a SHARED row. Without this the seeded rows
    # belong to whoever ran the script, _demo_source skips every owned row,
    # and all seven cards read "not on this box" on a box that just received
    # them - which is the state this shipped in.
    #
    # Both halves of the test carry weight. Staff-only, because a contractor
    # must not be able to publish to every other account by naming a file
    # after a demo. Exact manifest name only, because that file is already the
    # public list of what any visitor may open. A client set that happens to
    # collide with a demo name still needs staff hands to become shared - and
    # _demo_source's plan-root scan refuses it too (never a file under
    # JOBS_DIR, never a path registered to an owner), so an UNSEEDED box
    # does not fall back to the client's upload either.
    owner = _who(request)
    if owner is not None and _is_staff(request) and _display_name(dest) in DEMO_PLANS:
        owner = None
    return _register(dest, classify=bool(classify), owner=owner)


@app.get("/api/job/{job_id}/page/{page}/image")
def page_image(job_id: str, page: int, dpi: int = 130):
    job = JOBS.get(job_id) or _404()
    cache = _job_dir(job_id) / f"p{page}_{dpi}.png"
    if not cache.exists():
        doc = pymupdf.open(job["path"])
        pix = doc[page].get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72))
        pix.save(cache)
        doc.close()
    return FileResponse(cache, media_type="image/png")


def _404():
    raise HTTPException(404, "unknown job")


# ---------- detection scheduler: one child PROCESS per page ----------
# Detection used to run in a thread inside the web process. One heavy sheet
# pinned the CPU and the GIL (the extract/chain hot loops are C calls that
# never release it), so page images, samples and status stalled for seconds,
# nothing capped concurrency, and nothing could kill a runaway page
# (python-level SIGALRM budgets never fire inside a C call - FINDINGS F23).
# Each page now runs in a spawned child process (takeoff_engine.detect_page
# .run_detect, which writes the same p{page}_result.json): the web process
# stays responsive, at most TAKEOFF_WORKERS children run at once (FIFO
# beyond that), and a child over TAKEOFF_PAGE_BUDGET_S is terminated.
# Default cap is 4, not cpu_count-1: a heavy sheet (Barn civil p3, 412k
# paths) peaks at multiple GB, so 15 concurrent children on a 16-core box
# would swap long before they ran out of cores. Raise TAKEOFF_WORKERS on a
# box sized for it.
TAKEOFF_WORKERS = max(1, int(os.environ.get("TAKEOFF_WORKERS")
                             or min(4, max(1, (os.cpu_count() or 2) - 1))))
TAKEOFF_PAGE_BUDGET_S = float(os.environ.get("TAKEOFF_PAGE_BUDGET_S") or 900)
_MP = multiprocessing.get_context("spawn")
_SCHED = threading.Condition()          # guards the two containers below
_SCHED_QUEUE: deque = deque()           # (job_id, page) FIFO, not started yet
_SCHED_RUNNING: dict = {}               # (job_id, page) -> {"proc", "t0"}
_SCHED_THREAD: list = []                # [Thread] once the watcher is up


def _sched_start_locked(job_id: str, page: int) -> None:
    job = JOBS[job_id]
    proc = _MP.Process(
        target=run_detect, args=(job["path"], page, str(_job_dir(job_id))),
        name=f"detect-{job_id}-p{page}", daemon=True)
    proc.start()
    _SCHED_RUNNING[(job_id, page)] = {"proc": proc, "t0": time.time()}


def _sched_fill_locked() -> None:
    while _SCHED_QUEUE and len(_SCHED_RUNNING) < TAKEOFF_WORKERS:
        job_id, page = _SCHED_QUEUE.popleft()
        try:
            _sched_start_locked(job_id, page)
        except Exception as e:  # spawn itself failed; surface like any error
            JOBS[job_id]["status"][page] = f"error: could not start worker: {e}"


def _sched_finish_locked(key: tuple, reason: str | None = None) -> None:
    """Record the outcome of a child that has exited (reason=None: read it
    off the disk) or that we killed (reason given: clean partial files)."""
    job_id, page = key
    info = _SCHED_RUNNING.pop(key)
    proc = info["proc"]
    d = _job_dir(job_id)
    rf, ef = result_path(d, page), error_path(d, page)
    if reason is None:
        # Success means the child WROTE a result - not that one exists. A
        # stale file from an older engine sits at the same path, and "does
        # rf exist" read a child that died on startup (OOM during numpy
        # import, measured 2026-08-30 on ellis p42) as done: the status
        # then reported idle-stale and the UI polled "detecting..."
        # forever. Freshness = mtime at-or-after the moment we spawned.
        fresh = False
        if rf.exists():
            try:
                fresh = rf.stat().st_mtime >= info["t0"] - 2.0
            except OSError:
                fresh = False
        if fresh:
            st = "done"
        elif ef.exists():
            try:
                st = f"error: {ef.read_text()}"
            except OSError:
                st = "error: detection failed"
        else:
            st = f"error: worker exited with code {proc.exitcode}"
    else:
        st = reason
        rf.unlink(missing_ok=True)  # a kill mid-write leaves a truncated file
    ef.unlink(missing_ok=True)
    try:
        proc.close()
    except Exception:
        pass
    job = JOBS.get(job_id)
    if job is not None:
        job["status"][page] = st


def _sched_loop() -> None:
    """Watcher: reap finished children, kill over-budget ones, start queued."""
    while True:
        with _SCHED:
            _SCHED.wait(timeout=0.25)
            now = time.time()
            for key, info in list(_SCHED_RUNNING.items()):
                proc = info["proc"]
                if not proc.is_alive():
                    proc.join()
                    _sched_finish_locked(key)
                elif now - info["t0"] > TAKEOFF_PAGE_BUDGET_S:
                    proc.terminate()
                    proc.join(10)
                    _sched_finish_locked(
                        key, f"error: timeout after {TAKEOFF_PAGE_BUDGET_S:g}s")
            _sched_fill_locked()


def _enqueue_detect(job_id: str, page: int) -> bool:
    """Queue a page for detection; returns True if it was already queued or
    running (so a double-click / a detect-all pass never runs a page twice)."""
    key = (job_id, page)
    with _SCHED:
        if key in _SCHED_RUNNING or key in _SCHED_QUEUE:
            return True
        JOBS[job_id]["status"][page] = "running"
        _SCHED_QUEUE.append(key)
        if not _SCHED_THREAD:
            t = threading.Thread(target=_sched_loop, name="detect-scheduler",
                                 daemon=True)
            t.start()
            _SCHED_THREAD.append(t)
        _sched_fill_locked()
        _SCHED.notify()
        return False


def _is_queued(job_id: str, page: int) -> bool:
    with _SCHED:
        return (job_id, page) in _SCHED_QUEUE


def _detect_worker(job_id: str, page: int):
    """Synchronous in-process detection (scripts / tests). The service itself
    goes through _enqueue_detect; this keeps the old entry point callable."""
    job = JOBS[job_id]
    try:
        run_detect(job["path"], page, str(_job_dir(job_id)))
        job["status"][page] = "done"
    except Exception as e:  # surfaced to the UI
        job["status"][page] = f"error: {e}"
        raise
    finally:
        error_path(_job_dir(job_id), page).unlink(missing_ok=True)


def _result_stale(job_id: str, page: int) -> bool:
    """True when a stored result was produced by DIFFERENT engine code.

    A result is cached on (pdf path, mtime), which says nothing about whether
    the code changed. See takeoff_engine.detect_page.engine_sig - a scale fix
    landed on 2026-08-22 while the browser kept serving the old wrong value
    out of this cache.
    """
    from takeoff_engine.detect_page import engine_sig

    f = _job_dir(job_id) / f"p{page}_result.json"
    if not f.exists():
        return False
    try:
        sig = json.loads(f.read_text()).get("engine_sig")
    except (OSError, ValueError):
        return True
    return sig != engine_sig()


@app.post("/api/job/{job_id}/page/{page}/detect")
def detect(job_id: str, page: int):
    job = JOBS.get(job_id) or _404()
    if job["status"].get(page) == "running":
        return {"status": "running", "queued": _is_queued(job_id, page)}
    if (_job_dir(job_id) / f"p{page}_result.json").exists() \
            and not _result_stale(job_id, page):
        job["status"][page] = "done"
        return {"status": "done"}
    _enqueue_detect(job_id, page)
    return {"status": "running", "queued": _is_queued(job_id, page)}


@app.get("/api/job/{job_id}/page/{page}/status")
def status(job_id: str, page: int):
    job = JOBS.get(job_id) or _404()
    st = job["status"].get(page)
    if st is None and (_job_dir(job_id) / f"p{page}_result.json").exists():
        st = "done"
    # A result from older engine code is not "done" as far as the UI is
    # concerned: auto-loading it puts a number on screen that the current
    # engine would not produce, and the reviewer has no way to tell. Report
    # it as idle-with-a-reason so the page shows "not detected" and one
    # click on Detect recomputes it.
    if st == "done" and _result_stale(job_id, page):
        return {"status": "idle", "stale": True}
    out = {"status": st or "idle"}
    if st == "running" and _is_queued(job_id, page):
        out["queued"] = True
    return out


@app.get("/api/job/{job_id}/kinds")
def job_kinds(job_id: str):
    """Per-page vector/hybrid/raster labels, once the background pass has them.

    Opening a document no longer waits for this (see _register): the UI shows
    the page list immediately and fills the kind in when it arrives.
    """
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(404, "unknown job")
        kinds = j.get("kinds")
        return {"kinds": kinds,
                "done": kinds is not None,
                "progress": int(j.get("kinds_progress") or 0),
                "pages": int(j.get("pages") or 0)}


# ---------------------------------------------------------------------------
# SHEET TRIAGE - which pages of a set matter to excavation (EXCAVATION-PLAN
# section 7 item 4: "48-page set, the operator knows in 30 seconds which 12
# matter"). CLASSIFICATION ONLY: this paints attention in the page strip and
# never detects, excludes, or produces a quantity. Text-layer only and cheap
# on purpose - no detection, no AI call, no render: one get_text() pass per
# page plus the title-block probe the engine already has (read_sheet_number),
# cached to a triage.json sidecar in the job dir.
#
# The score is a transparent keyword tally so the UI can show WHY a page was
# tiered: the `hits` list IS the whole computation, term by term. Weights and
# caps were calibrated against the measured text of three real sets on
# 2026-08-30 (Barn civil 15p, Kayden Springs 48p, ellis 54p; the synthetic
# edge of each rule is pinned in tests/test_triage.py):
#   * counts are CAPPED per family (min(count, cap)) so one repeated word
#     cannot carry a page: ellis SD1, a structural-detail sheet, says FILL
#     five times in compaction notes and must stay "other";
#   * bare GRADE, RIM, INV and ELEVATION are deliberately NOT signals -
#     measured ambiguity: "GRADE 60" is rebar steel on ellis SD1, RIM is a
#     rim joist on ellis SD1/SD3 (x11/x26), and ELEVATION is a building
#     facade on every ellis A3.x sheet;
#   * DRAIN scores +1 where DRAINAGE scores +2: roof drains put DRAINx3 on
#     ellis A4.10, an architectural wall-section sheet.

TRIAGE_VERSION = 1          # bump when the table below changes - cached
                            # triage.json sidecars key on it

# (label, regex over the UPPERCASED whitespace-normalized page text,
#  points per occurrence, cap on occurrences counted)
_TRIAGE_KW = [
    # --- excavation / civil signals (positive) ---------------------------
    # weight 3: words that in this corpus only appear on earth-moving sheets
    ("GRADING",     re.compile(r"\b(?:RE)?GRADING\b"),             3, 5),
    ("EARTHWORK",   re.compile(r"\bEARTHWORKS?\b"),                3, 5),
    ("EXCAVATION",  re.compile(r"\bEXCAVAT\w*"),                   3, 5),
    # weight 2: civil utility / sitework vocabulary
    ("CONTOUR",     re.compile(r"\bCONTOURS?\b"),                  2, 5),
    ("TOPSOIL",     re.compile(r"\bTOPSOIL\b"),                    2, 5),
    ("EROSION",     re.compile(r"\bEROSION\b"),                    2, 5),
    ("STORM",       re.compile(r"\bSTORM\b"),                      2, 5),
    ("SANITARY",    re.compile(r"\bSANITARY\b"),                   2, 5),
    ("SEWER",       re.compile(r"\bSEWERS?\b"),                    2, 5),
    ("WATER LINE",  re.compile(r"\bWATER\s?(?:LINES?|MAINS?)\b"),  2, 5),
    ("UTILITY",     re.compile(r"\bUTILIT(?:Y|IES)\b"),            2, 5),
    ("DRAINAGE",    re.compile(r"\bDRAINAGE\b"),                   2, 5),
    ("TRENCH",      re.compile(r"\bTRENCH\w*"),                    2, 5),
    ("MANHOLE",     re.compile(r"\bMANHOLES?\b"),                  2, 5),
    ("SWALE",       re.compile(r"\bSWALES?\b"),                    2, 5),
    ("SILT FENCE",  re.compile(r"\bSILT\s+FENCE\b"),               2, 5),
    ("CULVERT",     re.compile(r"\bCULVERTS?\b"),                  2, 5),
    ("GRUBBING",    re.compile(r"\bGRUB(?:BING)?\b"),              2, 5),
    # weight 1: civil-leaning but shared with other trades
    ("DEMO",        re.compile(r"\bDEMO\w*"),                      1, 5),
    ("SITE",        re.compile(r"\bSITE\b"),                       1, 5),
    ("HYDRANT",     re.compile(r"\bHYDRANTS?\b"),                  1, 5),
    ("DRAIN",       re.compile(r"\bDRAINS?\b"),                    1, 5),
    ("CUT",         re.compile(r"\bCUT\b"),                        1, 3),
    ("FILL",        re.compile(r"\bFILL\b"),                       1, 3),
    # --- other-trade signals (negative) -----------------------------------
    ("ELECTRICAL",  re.compile(r"\bELECTRICAL\b"),                -2, 5),
    ("MECHANICAL",  re.compile(r"\bMECHANICAL\b"),                -2, 5),
    ("PLUMBING",    re.compile(r"\bPLUMBING\b"),                  -2, 5),
    ("HVAC",        re.compile(r"\bHVAC\b"),                      -2, 5),
    ("REFLECTED CEILING", re.compile(r"\bREFLECTED\s+CEILING\b"), -3, 3),
    ("DOOR SCHEDULE",     re.compile(r"\bDOOR\s+SCHEDULES?\b"),   -3, 3),
    ("WINDOW SCHEDULE",   re.compile(r"\bWINDOW\s+SCHEDULES?\b"), -3, 3),
]

# Printed sheet-number prefix, when read_sheet_number found one. C is the
# civil series on every set measured; A/S/M/E/P are architectural,
# structural, mechanical, electrical, plumbing. -6 rather than -4 because
# ellis A1.02 - an architectural index sheet, 3,413 words - name-drops
# GRADING, EXCAVATION and DRAINAGE and needs the prefix to outweigh the
# borrowed vocabulary. Every other letter (V survey, G general, T title...)
# is neutral: no evidence either way.
_TRIAGE_CIVIL_PREFIX = 4
_TRIAGE_OTHER_PREFIX = -6

# Tier thresholds. A bare C prefix (+4) lands in "maybe", not "core": a C
# detail sheet with no excavation words on it is plausibly relevant, not
# certainly - core demands keyword evidence. Calibrated so that on ellis
# (54 pages, 3 civil) exactly the civil sheets are core, and on Barn
# (15 pages, all civil) all 15 are.
_TRIAGE_CORE_MIN = 6    # score >= 6  -> "core"
_TRIAGE_MAYBE_MIN = 2   # score >= 2  -> "maybe"; below -> "other"


def triage_score(text: str, sheet_no: "str | None") -> dict:
    """Score one page's TEXT LAYER for excavation relevance. Pure - no I/O,
    nothing but the constant table above - so tests/test_triage.py can run
    it on synthetic word lists with no PDF and no server.

    Returns {"tier", "score", "words", "hits"} where hits is the full
    term-by-term computation [[label, count, points], ...] sorted by
    |points| descending, so a tooltip can lead with the strongest evidence.
    Never a quantity: the only outputs are a tier and the words that earned
    it.

    A page with NO text abstains: tier "unknown". A raster/scanned sheet is
    invisible to a text probe, and calling it "other" would dim a page this
    code knows nothing about.
    """
    T = " ".join((text or "").upper().split())
    if not T:
        return {"tier": "unknown", "score": 0, "words": 0, "hits": []}
    words = len(T.split())
    score = 0
    hits = []
    for label, rx, pts, cap in _TRIAGE_KW:
        n = len(rx.findall(T))
        if not n:
            continue
        p = pts * min(n, cap)
        score += p
        hits.append([label, n, p])
    pre = (sheet_no or "").strip().upper()
    m = re.match(r"[A-Z]+", pre)
    if m:
        letter = m.group(0)[0]
        if letter == "C":
            score += _TRIAGE_CIVIL_PREFIX
            hits.append([f"sheet {pre} (C = civil)", 1, _TRIAGE_CIVIL_PREFIX])
        elif letter in "ASMEP":
            score += _TRIAGE_OTHER_PREFIX
            hits.append([f"sheet {pre} ({letter} = other trade)", 1,
                         _TRIAGE_OTHER_PREFIX])
    hits.sort(key=lambda h: (-abs(h[2]), h[0]))
    tier = ("core" if score >= _TRIAGE_CORE_MIN
            else "maybe" if score >= _TRIAGE_MAYBE_MIN else "other")
    return {"tier": tier, "score": score, "words": words, "hits": hits}


@app.get("/api/job/{job_id}/triage")
def job_triage(job_id: str):
    """Per-page excavation-relevance tiers for the whole set, with evidence.

    Cheap by construction - text layer plus the title-block sheet-number
    probe, page by page; no detection, no AI, no render. Measured first-call
    cost on this box: 5.0 s for 15 pages (Barn), 12.0 s for 48 (Kayden),
    5.7 s for 54 (ellis); afterwards served from the triage.json sidecar,
    which keys on TRIAGE_VERSION + the PDF mtime the registry recorded.
    /reset sweeps the sidecar with the other derived files, so a reset demo
    recomputes it live.
    """
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(404, "unknown job")
        src = Path(j.get("path", ""))
        mtime = j.get("mtime")
    side = _job_dir(job_id) / "triage.json"
    if side.exists():
        try:
            d = json.loads(side.read_text())
            if d.get("version") == TRIAGE_VERSION and d.get("mtime") == mtime:
                return d
        except (OSError, ValueError):
            pass                     # corrupt cache; recompute below
    if not src.exists():
        raise HTTPException(410, "that plan set is no longer on this server")
    pages = []
    doc = pymupdf.open(src)
    try:
        for i in range(doc.page_count):
            pg = doc[i]
            try:
                txt = pg.get_text("text")
            except Exception:
                txt = ""             # a broken page abstains as "unknown"
            try:
                sn = read_sheet_number(pg)
            except Exception:
                sn = None
            row = triage_score(txt, sn)
            row["page"] = i
            row["sheet_no"] = sn
            pages.append(row)
    finally:
        doc.close()
    counts = {"core": 0, "maybe": 0, "other": 0, "unknown": 0}
    for r in pages:
        counts[r["tier"]] += 1
    d = {"version": TRIAGE_VERSION, "mtime": mtime,
         "pages": pages, "counts": counts}
    # atomic like the calcstate sidecar: a crash mid-write must leave no
    # half-file for the next GET to trip over
    tmp = side.with_name(f"{side.name}.{uuid.uuid4().hex[:8]}.tmp")
    try:
        tmp.write_text(json.dumps(d, indent=1))
        os.replace(tmp, side)
    except OSError:
        pass                         # cache write is best-effort; still reply
    return d


@app.get("/api/job/{job_id}/page/{page}/result")
def result(job_id: str, page: int):
    f = _job_dir(job_id) / f"p{page}_result.json"
    if not f.exists():
        raise HTTPException(404, "no result yet")
    return Response(f.read_text(), media_type="application/json")


@app.post("/api/job/{job_id}/reset")
def reset_job(job_id: str, preview: bool = False):
    """Throw away everything DERIVED from this PDF, so the next detect is cold.

    This exists for demos. The review UI already forgets a document's saved
    decisions on request, but that only clears the browser: the service keeps
    every page's detection in p<N>_result.json and serves it straight back, so
    a "fresh" run in front of a client would replay a result computed hours
    earlier. That is a pre-warmed cache presented as a live run, which is the
    one thing a demo is not allowed to be.

    What goes: every derived artifact in this job's own directory - detection
    results, page renders, earthwork maps, callouts, symbols, schedules, AND
    the two that are operator work rather than cache: the approved sheet
    exports (p<N>_elements.json) and any report PDF already built. Those two
    are counted separately and named in the confirmation dialog, because
    sweeping a deliverable away under the word "cached" is not consent.
    Pass preview=1 to get the counts back without deleting anything.

    What stays: the source PDF. It is never inside the job directory - a
    sample is opened by path from wherever it lives, and an upload is written
    to JOBS_DIR/upload_<hex>_<name>.pdf, a SIBLING of this directory. The
    guard below re-derives the directory from JOBS_DIR and refuses anything
    that does not resolve to a direct child of it, so a crafted job_id cannot
    walk out of the jobs tree. The registry entry also stays, so the document
    stays open in the UI and simply needs detecting again.
    """
    d = (JOBS_DIR / job_id).resolve()
    if d.parent != JOBS_DIR.resolve() or not d.is_dir():
        raise HTTPException(404, "unknown job")

    # Classify before touching anything. Two of these are OPERATOR WORK, not
    # cache: p<N>_elements.json is an approved sheet export, and the report PDF
    # is the only deliverable this product has. The first version of this route
    # swept them away with the renders under the word "cached", which is not
    # informed consent - so the UI asks for a preview first and names them.
    def kind(name: str) -> str:
        if name.endswith("_elements.json"):
            return "approved"
        if name.endswith(".pdf"):
            return "report"
        if name.endswith("_result.json"):
            return "detection"
        if name.endswith(".png"):
            return "render"
        return "other"

    counts = {"approved": 0, "report": 0, "detection": 0, "render": 0, "other": 0}
    freed = 0
    files = [f for f in sorted(d.iterdir()) if f.is_file()]
    for f in files:
        counts[kind(f.name)] += 1
        try:
            freed += f.stat().st_size
        except OSError:
            pass

    out = {"ok": True, "counts": counts, "total": len(files),
           "freed_mb": round(freed / 1048576, 2)}
    if preview:
        out["preview"] = True
        return out

    # Same sweep a demo's fresh open performs - one definition of "cold", not
    # two that can drift apart. It unlinks the files and clears the in-memory
    # status map (leaving that behind would show "detected" over a directory
    # with nothing in it - the screen disagreeing with the record, again).
    removed, _failed = _sweep_job_dir(job_id)

    out["removed"] = removed
    # training ledger (observation only): later events for this job refer
    # to a FRESH detection - the provenance boundary matters at training time
    ledger.record("job_reset", job_id, None, None,
                  {"counts": counts, "removed": removed},
                  None, pdf_path=(JOBS.get(job_id) or {}).get("path"))
    return out


@app.delete("/api/job/{job_id}")
def delete_job(job_id: str, request: Request):
    """Remove a document from this box entirely.

    `reset` throws away everything DERIVED and deliberately keeps the source
    PDF, so a plan set reset in front of a client is still sitting on the box
    afterwards, still listed, still openable by anyone who can log in. This is
    the other half, and the one you want when a client's live bid set should
    not outlive the meeting.

    Deletes the job directory, the registry row, and the uploaded PDF. It will
    NOT unlink a source that lives outside the job store - a sample opened by
    path is somebody's actual file - and it will not unlink a content-addressed
    upload that another job still points at, because two people uploading
    identical bytes share one file on disk.
    """
    me = _who(request)
    d = (JOBS_DIR / job_id).resolve()
    if d.parent != JOBS_DIR.resolve():
        raise HTTPException(404, "unknown job")
    with JOBS_LOCK:
        j = JOBS.get(job_id)
        if j is None:
            raise HTTPException(404, "unknown job")
        owner = j.get("owner")
    # A user may delete their own. The shared demo corpus is deliberately not
    # deletable through the UI: it is the thing every demo depends on, and one
    # mis-click should not cost an hour of re-detection for everybody.
    if owner is None and not _is_staff(request):
        raise HTTPException(
            403, "shared demo plans are managed on the server, not from here")
    if owner is not None and owner != me and not _is_staff(request):
        raise HTTPException(404, "unknown job")

    src = Path(j.get("path", ""))
    name = _display_name(src)
    removed = 0
    if d.is_dir():
        for f in sorted(d.iterdir()):
            if f.is_file():
                try:
                    f.unlink()
                    removed += 1
                except OSError:
                    pass
        try:
            d.rmdir()
        except OSError:
            pass

    with JOBS_LOCK:
        JOBS.pop(job_id, None)
        shared_source = any(x.get("path") == str(src) for x in JOBS.values())
        _save_registry()

    pdf_removed = False
    if (not shared_source and _UPLOAD_PREFIX.match(src.name)
            and src.parent.resolve() == JOBS_DIR.resolve() and src.exists()):
        try:
            src.unlink()
            pdf_removed = True
        except OSError:
            pass
    # training ledger (observation only): the job's events end here, and a
    # trainer reading the ledger later should know the source went away
    ledger.record("job_deleted", job_id, None, None,
                  {"files_removed": removed, "pdf_removed": pdf_removed},
                  None)
    return {"ok": True, "name": name, "files_removed": removed,
            "pdf_removed": pdf_removed}


@app.get("/api/job/{job_id}/page/{page}/confidence")
def page_confidence(job_id: str, page: int):
    """Page-level trust signals for the review banner. Read-only.

    Two facts the detect result does not already carry, and both decide
    whether an operator should believe this sheet's numbers:

      * NOT TO SCALE when the sheet prints no ratio at all. ScaleResult
        .to_dict() returns None when there is no `best`, so `has_nts` is
        dropped in exactly the case where it matters most.
      * where each spot elevation's base elevation came from - the sheet's
        own legend note (the drafter telling us) or a consensus guess off
        other numbers on the page. Barn Civil p3 is the cautionary case:
        the guess landed a clean 200 ft high on all 272 grades and every
        value still read as a plausible elevation (earthwork.read_spot_datum).

    Text layer only - no OCR, no rasterising - and nothing here feeds a
    quantity. It costs one page parse (0.4-2.3 s measured), so the UI
    fetches it once per page and upgrades the banner when it lands, rather
    than holding up detect. Deliberately NOT folded into the detect result:
    that would change `detect_page.engine_sig` and stale every cached page
    in the corpus for a reporting change.
    """
    from takeoff_engine.earthwork import read_spot_datum, read_spot_grades
    from takeoff_engine.extract import page_lines as _page_lines
    from takeoff_engine.scale_detect import _NTS

    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    try:
        if page < 0 or page >= doc.page_count:
            raise HTTPException(404, "no such page")
        pg = doc[page]
        has_nts = bool(_NTS.search(pg.get_text()))
        words = page_words(pg)
        lines = _page_lines(pg)
    finally:
        doc.close()
    dinfo = {}
    try:
        spots = read_spot_grades(words, lines=lines, datum_info=dinfo)
    except Exception:
        spots, dinfo = [], {}
    declared = dinfo.get("declared")
    # Which branch resolved each grade, reconstructed with the SAME rule the
    # reader now applies: the note only wins where read_spot_grades let it
    # win (dinfo["chosen"]). The old reconstruction assumed the note always
    # wins - the exact assumption that shipped 171 grades 100 ft low on Barn
    # C3.3 under "from_sheet_note: 171".
    # Count the reader's OWN resolution stamps (g["res"]) instead of
    # re-deriving with a copied threshold. The old >= 1000.0 copy here
    # drifted from the reader's corroborated >= 100 rule and called 210
    # printed pad elevations "a guess" (defect shape: one semantic
    # threshold duplicated in two files - now impossible to re-arm).
    n_printed = n_note = n_inferred = n_unresolved = 0
    for g in spots:
        r = g.get("res")
        if not g.get("resolved"):
            n_unresolved += 1
        elif r == "printed":
            n_printed += 1
        elif r == "note":
            n_note += 1
        else:
            n_inferred += 1
    return {
        "has_nts": has_nts,
        "spots": {
            "n": len(spots),
            "printed_in_full": n_printed,
            "from_sheet_note": n_note,
            "inferred": n_inferred,
            "unresolved": n_unresolved,
            # legend sample rows the reader dropped (text-layer fallback
            # rule here - this endpoint has no detect result to hand the
            # reader legend boxes from)
            "legend_excluded": dinfo.get("legend_excluded", 0),
            "declared_datum": declared,
            # Both candidates and the disagreement, so no banner ever again
            # asserts "the drafter's statement" while the sheet's own
            # contours read 100 ft higher.
            "datum": dinfo,
        },
    }


# ---------- the scale a human chose --------------------------------------
# Every foot, square foot and cubic yard on a sheet is one multiplication away
# from `ft_per_paper_inch`, and on a quarter of the pages that return a scale
# at all the sheet prints more than one - a plan and its details. The review
# UI lets the estimator pick which one governs the drawing and redraws every
# number on screen from it (webui/app.js renderScalePick). Until this file
# learned to read that pick, `approve` recomputed every length from the
# ENGINE's choice, so the screen and the exported takeoff disagreed with no
# symptom - Barn Landscaping L604 exported 4x low - and the export still
# stamped the number `(auto, text)` with the estimator's approval on it.
#
# The pick is stored BESIDE the detect result, never inside it, for two
# reasons that both matter:
#   * `_result_stale` throws the cached result away whenever engine code
#     changes (detect_page.engine_sig). A human's reading of which caption
#     governs the drawing is not invalidated by an engine edit.
#   * a re-detect rewrites p<N>_result.json wholesale.
# It is a judgement about the DRAWING, not an engine output, so it outlives
# both. `set_by` is the provenance, and it travels with the number all the
# way into the elements JSON and onto the face of the report PDF.

_BY_REVIEWER = "reviewer"

# The band a reviewer's scale has to land in to be usable at all.
#
# A number a person types is reachable from the review UI (webui/app.js
# renderScaleTyped) and from anything that POSTs `scale.ft_per_paper_inch`,
# and until this band existed neither had a floor or a ceiling: Gate 1
# posted 1e9 and the export came back quoting 1,967,692,500,000.1 ft with
# a straight face.
#
# It REFUSES, it does not clamp. A clamp turns a typo into a confident
# wrong number - the most expensive thing this engine can produce,
# because it reaches a bid - while a refusal costs one retype and says
# what it wanted. Measured, not assumed:
#
#   * 6,714 corpus pages read text-only through takeoff_engine.scale_detect
#     (out/verify/2026-08-23/scalebox/work/band_corpus.py; the per-page rows
#     are in band_corpus.json). 279 return a scale, and every one lands
#     between 0.3333 and 250.0 ft per paper inch, over 26 distinct values.
#   * FLOOR 0.0833 ft/in is 1/12 - FULL SIZE, one paper inch to one real
#     inch. Three separate things put it there and none of them is a
#     round number picked for looks:
#       - the smallest reading anywhere in that scan is 0.3333, and the
#         smallest from an UNAMBIGUOUS imperial caption is 0.4 (`5" = 2'`,
#         GAOA Star Meadow 007^S-01 New Foundation.pdf page 0). The 0.3333
#         is the engine reading `Scale 3:1` as if it were 3" = 1'-0"; read
#         as a ratio that caption means a 3x ENLARGEMENT (0.0278 ft/in),
#         so it is quoted here as the observed extreme, not as a scale we
#         vouch for.
#       - CONVENTION: the largest scale an architect's rule carries is
#         3" = 1'-0" = 0.3333 ft/in (see scale_detect._scan, which measured
#         it against drawn geometry on Barn Landscaping L804).
#       - full size is one step finer again, so the floor sits 4x below the
#         smallest thing this corpus reads and still admits a full-size
#         detail. Under it the drawing is ENLARGED past life size (2:1,
#         3:1, 4:1 shop detailing), which is not geometry anyone takes
#         quantities off.
#     It is 1/12 rounded DOWN to four places, so the number this module
#     PRINTS in its refusal is itself inside the band when typed back.
#   * CEILING 1000 ft/in is 4x the corpus maximum (1" = 250' on
#     client_meet/beaumont/SWR_Guide_Residence_-_Civil_5.8.26.pdf, engine
#     page index 8) and 2.5x scale_detect._scan's own text-caption accept
#     ceiling of 400. At 1000 ft/in a 36-inch-wide sheet spans 6.8 miles.
#   * scale_detect's own FLOOR of 0.33 is deliberately not reused: it is
#     there to stop a dimension string (`6" - 2'` = 0.3333) reading as a
#     scale, an ambiguity a number typed on purpose does not have.
#
# The band cannot catch a plausible-but-wrong number (20 typed as 200 is
# inside it). Nothing here can. That is what the amber banner and the
# `set by the reviewer` provenance on the export and the PDF are for.
SCALE_FT_MIN = 0.0833
SCALE_FT_MAX = 1000.0


def _sane_ft(v) -> float | None:
    """A usable feet-per-paper-inch, or None - which means REFUSE.

    Never substitutes a default. Every caller says out loud that it
    refused; silently falling back to another number is how a scale a
    person set stops being the scale the deliverable used.
    """
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f):
        return None
    return f if SCALE_FT_MIN <= f <= SCALE_FT_MAX else None


def _refuse_scale(v) -> HTTPException:
    """Why this number was refused, in the words the reviewer needs."""
    return HTTPException(400, (
        f"scale {v!r} is not a usable number of feet per paper inch. "
        f"This app accepts {SCALE_FT_MIN:.4f} to {SCALE_FT_MAX:g} - "
        f"from full size (1 inch of paper = 1 real inch) to 1 inch = "
        f"{SCALE_FT_MAX:g} ft, which already covers every scale this app "
        f"has read anywhere in the plan corpus (0.33 to 250). Nothing was "
        f"changed; retype the scale."))


def _scale_pick_path(job_id: str, page: int) -> Path:
    return _job_dir(job_id) / f"p{page}_scale.json"


def _stored_scale_pick(job_id: str, page: int) -> dict | None:
    f = _scale_pick_path(job_id, page)
    if not f.exists():
        return None
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return None
    # An out-of-band sidecar can now only come from a hand edit or an
    # older build - the approve route refuses to write one. Ignoring it
    # drops the page back to the engine's reading, which the chip, the
    # banner and the export all state as `auto`: visible, not silent.
    return d if _sane_ft(d.get("ft_per_paper_inch")) is not None else None


# What a reviewer's number was BASED ON, when it was not their own arithmetic.
# Absent = they typed it or picked a caption the engine had already promoted;
# the record never guesses between the two.
_BASIS_SUGGESTION = "engine-suggestion"


def _scale_suggestion_for(sugg: dict | None, ft: float) -> dict | None:
    """The unpromoted reading this number IS, or None.

    Matched on the value, to 1e-6 ft per paper inch - the same tolerance
    webui/app.js uses everywhere it matches a scale against a candidate. A
    number that is merely close to a suggestion is not that suggestion.
    """
    for c in ((sugg or {}).get("candidates") or []):
        try:
            v = float(c.get("ft_per_paper_inch"))
        except (TypeError, ValueError):
            continue
        if abs(v - float(ft)) < 1e-6:
            return c
    return None


def _scale_offered(sugg: dict | None) -> list:
    """Every reading the operator was shown, for the training ledger."""
    out = []
    for c in ((sugg or {}).get("candidates") or []):
        out.append({"ft_per_paper_inch": c.get("ft_per_paper_inch"),
                    "text": c.get("text"), "origin": c.get("origin")})
    return out


def _page_scale(job_id: str, page: int, res: dict,
                sent: "ApproveScale | None" = None) -> dict | None:
    """The scale this page's quantities are computed from, and who set it.

    Returns the engine's scale dict with `set_by` added ("auto" or
    "reviewer"), plus `engine_*` recording what the engine had thought when a
    human overruled it - or None when the sheet has no scale at all.

    `sent` is what the review UI had ON SCREEN when Approve was pressed, and
    it wins: the deliverable must never disagree with the screen the human
    approved (CLAUDE.md 1). A pick is written to disk so a reload, a
    re-detect and a later re-approve all agree with it; approving the
    engine's own reading retires an earlier pick rather than letting it
    outlive the screen.

    A SUGGESTION the reviewer accepted is a third case, and it is neither of
    the other two. `res["scale_suggestions"]` holds readings the engine
    recovered off a picture of the caption and refused to promote (see
    detect_page's scale boundary): the engine reads NO scale on that page, so
    `eng` is None here exactly as it is for a blank sheet, and without the
    distinction the export would say "the engine read no scale here" about a
    page where it read one and distrusted it. So when the accepted number
    matches a suggestion, the record says `basis = "engine-suggestion"` and
    carries which reading was accepted. It is still a reviewer decision -
    nothing about it is `auto` - and the number is still checked against the
    same band as a typed one.
    """
    eng = res.get("scale") or None
    sugg = res.get("scale_suggestions") or None
    eng_ft = None
    if eng:
        try:
            eng_ft = float(eng.get("ft_per_paper_inch") or 0) or None
        except (TypeError, ValueError):
            eng_ft = None
    pick = None
    want = 0.0
    if sent is not None:
        # Refuse rather than ignore. Quietly dropping a number the screen
        # was showing would export the ENGINE's scale under the human's
        # approval - the exact disagreement this function exists to end.
        checked = _sane_ft(sent.ft_per_paper_inch)
        if checked is None:
            raise _refuse_scale(sent.ft_per_paper_inch)
        want = checked
    if want > 0:
        # A screen that DIFFERS from the engine had a person change it,
        # whatever flag came with it. The converse is not true, and the
        # flag alone must NOT record a pick: renderScalePick stamps
        # picked_by_reviewer on every click including the button already
        # ON, so clicking away and back again would land here with the
        # engine's own value and make the export say 'set by the reviewer;
        # the engine had read <the same number>' - self-contradicting, and
        # it writes a sidecar where the rule below says to retire one.
        # Found by Gate 1 on the live UI. Value only.
        same_as_engine = eng_ft is not None and abs(eng_ft - want) < 1e-9
        if not same_as_engine:
            pick = {"ft_per_paper_inch": want,
                    "text": (sent.text or "").strip() or None,
                    "set_by": _BY_REVIEWER,
                    "set_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
            # Did the reviewer accept one of the engine's OWN unpromoted
            # readings? Decided on the VALUE against this page's stored
            # suggestions, never on the browser's flag alone - the same rule
            # `same_as_engine` above applies to the engine's pick, and for
            # the same reason: a flag can be stamped by a repaint, a value
            # cannot. If the screen's number is not one of the suggestions,
            # nothing is recorded and the pick reads as hand-set, which is
            # what it is.
            _sug = _scale_suggestion_for(sugg, want)
            if _sug is not None:
                pick["basis"] = _BASIS_SUGGESTION
                pick["suggestion_text"] = _sug.get("text")
                pick["suggestion_origin"] = _sug.get("origin")
                pick["engine_source"] = (sugg or {}).get("source")
            try:
                _scale_pick_path(job_id, page).write_text(
                    json.dumps(pick, indent=1))
            except OSError:
                pass  # unwritable: this approve still honours the screen
            # training ledger (observation only, never raises): a human
            # overruled the engine's reading - the core scale label.
            # `offered` is the label that makes an ABSTENTION trainable: the
            # engine put N readings up and a person chose this one, which is
            # exactly the ranking the engine refused to make.
            ledger.record("scale_set", job_id, page,
                          {"ft_per_paper_inch": eng_ft,
                           "text": (eng or {}).get("text"),
                           "source": (eng or {}).get("source"),
                           "abstained": bool(sugg)},
                          dict(pick),
                          {"offered": _scale_offered(sugg),
                           "accepted_suggestion": _sug is not None}
                          if sugg else None,
                          pdf_path=(JOBS.get(job_id) or {}).get("path"))
        else:
            _had_pick = _stored_scale_pick(job_id, page)
            try:
                _scale_pick_path(job_id, page).unlink(missing_ok=True)
            except OSError:
                pass
            if _had_pick:
                # the reviewer went BACK to the engine's own reading - as
                # much a label as the overrule was
                ledger.record("scale_pick_retired", job_id, page,
                              _had_pick,
                              {"ft_per_paper_inch": want, "set_by": "auto"},
                              {"engine_text": (eng or {}).get("text"),
                               "engine_source": (eng or {}).get("source")},
                              pdf_path=(JOBS.get(job_id) or {}).get("path"))
    else:
        # Nothing on the wire (an older UI, or a re-approve from a script):
        # an earlier human decision is still the best information this page
        # has, and it deliberately survived the re-detect.
        pick = _stored_scale_pick(job_id, page)
    if pick is None:
        return None if eng is None else {**eng, "set_by": "auto"}
    out = dict(eng or {})
    out.update({
        "text": pick.get("text") or (eng or {}).get("text"),
        "ft_per_paper_inch": float(pick["ft_per_paper_inch"]),
        "set_by": _BY_REVIEWER,
        "set_at": pick.get("set_at"),
        # read off the CURRENT result, so the comparison printed next to the
        # human's number is what the engine thinks NOW, not a stale copy
        "engine_ft_per_paper_inch": eng_ft,
        "engine_text": (eng or {}).get("text"),
        "engine_source": (eng or {}).get("source"),
        # both were measured against the engine's number, not this one
        "agrees": None,
        "delta_pct": None,
    })
    # A suggestion the reviewer accepted, so the export and the printed report
    # can say which of the two reviewer cases this is. Re-derived from THIS
    # result's suggestions as well as from the stored pick, so a re-approve
    # after a re-detect still describes itself correctly.
    _sug = _scale_suggestion_for(sugg, out["ft_per_paper_inch"])
    if _sug is not None or pick.get("basis") == _BASIS_SUGGESTION:
        out["basis"] = _BASIS_SUGGESTION
        out["suggestion_text"] = (
            (_sug or {}).get("text") or pick.get("suggestion_text"))
        out["suggestion_origin"] = (
            (_sug or {}).get("origin") or pick.get("suggestion_origin"))
        out["engine_source"] = (out.get("engine_source")
                                or (sugg or {}).get("source")
                                or pick.get("engine_source"))
    return out


def _ft(v) -> str:
    """4.0 -> "4", 5.3333 -> "5.33". Estimators read feet per paper inch."""
    try:
        return f"{float(v):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "?"


def _scale_name(det: dict) -> str:
    """What the export calls this scale. A number a person chose must never
    be labelled `auto`: his approval is on it, and a reader downstream has no
    other way to know the engine was overruled."""
    text = det.get("text") or f'1 inch = {_ft(det.get("ft_per_paper_inch"))} ft'
    if det.get("set_by") != _BY_REVIEWER:
        return f'{text} (auto, {det.get("source") or "unknown"})'
    if det.get("engine_ft_per_paper_inch"):
        return (f'{text} (set by the reviewer; the engine had read '
                f'{det.get("engine_text") or "?"} = '
                f'{_ft(det["engine_ft_per_paper_inch"])} ft per inch)')
    # The engine read NOTHING it would stand behind, but on a suggestion page
    # it did read something. "The engine read no scale here" would be false
    # about the sheet and would hide where the number came from, so the two
    # cases are named apart.
    if det.get("basis") == _BASIS_SUGGESTION:
        return (f'{text} (set by the reviewer, accepting a reading the engine '
                f'recovered but would not vouch for: '
                f'{det.get("suggestion_text") or "?"})')
    return f'{text} (set by the reviewer; the engine read no scale here)'


# The report PDF's base font has no glyph for the typographic prime marks an
# OCR'd caption comes back with, and draws them as empty boxes. Folded to the
# ASCII pair for PRINTING only - never for the stored value, the export or
# the record, all of which keep the caption exactly as it was read.
_MARK_FOLD = {0x201C: '"', 0x201D: '"', 0x2033: '"', 0x2032: "'",
              0x2018: "'", 0x2019: "'", 0x2014: "-", 0x2013: "-"}


def _ascii_marks(s) -> str:
    return str(s or "?").translate(_MARK_FOLD)


def _scale_report_lines(sc: dict | None, image_dpi=None) -> list[str]:
    """The scale statement printed on the face of the report PDF. A printed
    takeoff whose scale a person overrode has to say so where it is read.

    `ftPerPaperInch` is only written for a reviewer pick, and elements files
    approved before this endpoint learned about picks have neither - so the
    auto case is recovered from the pixels-per-foot the export already
    carried, which is exactly image_dpi / ft_per_paper_inch."""
    if not sc:
        return ["Scale: none was read on this sheet - no footage can be "
                "quoted from it."]
    ppu = sc.get("pixelsPerUnit")
    ft = sc.get("ftPerPaperInch")
    if ft is None and ppu and image_dpi:
        ft = image_dpi / ppu
    head = f"Scale: 1 inch = {_ft(ft)} ft" if ft else "Scale"
    if sc.get("setBy") != _BY_REVIEWER:
        return [f'{head}   [{sc.get("name", "")}]']
    eng = sc.get("engineFtPerPaperInch")
    out = [f"{head}   - SET BY THE REVIEWER, not read by the engine."]
    if eng:
        out.append(f'The engine had read 1 inch = {_ft(eng)} ft '
                   f'({sc.get("engineText") or "?"}). Every length below is '
                   f"computed from the reviewer's scale.")
    elif sc.get("basis") == _BASIS_SUGGESTION:
        # Not "the engine found no scale": it found this one and refused to
        # promote it, and the reviewer is the one who put it in force. A
        # reader of the printed takeoff has no other way to learn that the
        # number under every quantity was machine-read off a picture.
        #
        # WRAPPED, and the caption's typography folded to ASCII. The report
        # writer prints `_ln[:118]` in the PDF's base font, so a single long
        # sentence is CUT MID-WORD (measured 2026-09-01: this one lost
        # "...read by character recogniti|on off a picture of the caption",
        # i.e. it stopped before saying the one thing it exists to say), and
        # an OCR caption is exactly the text that carries typographic primes
        # - Hurricane's reads 1<U+201D> = 40<U+2019> - which that font draws
        # as empty boxes. Both are display-only: the value, the export and
        # the record all keep the caption verbatim.
        out += textwrap.wrap(
            f'The reviewer accepted a reading the engine recovered but would '
            f'not stand behind ({_ascii_marks(sc.get("suggestionText"))}, '
            f'read by character recognition off a picture of the caption). '
            f'The engine offered it as a suggestion only and set nothing. '
            f"Every length below is computed from the reviewer's scale.", 116)
    else:
        out.append("The engine found no scale on this sheet. Every length "
                   "below is computed from the reviewer's scale.")
    return out


class ApproveScale(BaseModel):
    # the scale in force ON SCREEN when Approve was pressed, in feet of real
    # world per paper inch. `picked_by_reviewer` is the review UI's own flag
    # for "a human chose this instead of the engine's reading".
    ft_per_paper_inch: float
    text: str | None = None
    picked_by_reviewer: bool = False
    # A PROFILE sheet prints two scales that are both right at once: the run
    # measured across, the rise measured up, deliberately exaggerated so the
    # slope is readable. When the reviewer has told the screen about the second
    # one, it has to travel with the approve or the report PDF quietly
    # disagrees with the panel it was approved from - and a report is the one
    # artifact that leaves the building. None = one scale governs both axes.
    v_ft_per_paper_inch: float | None = None


class QtyOverride(BaseModel):
    """A number a person typed over a computed one.

    Until 2026-08-24 there was no field for this and the server recomputed
    every footage from geometry, so the review panel and the CSV showed the
    operator's corrected figure while elements.json and the report PDF showed
    the detector's - same line item, different number, no warning anywhere.
    """
    label: str
    # the review UI's own two-way split (webui/app.js ovKey): "qty" for a row
    # counted in EACHes, "ft" for everything measured by length or carrying a
    # calculator's own unit. Keyed and split exactly as the UI keys it.
    kind: str = "ft"
    # float, not int: an EA box accepts 3.5 and a 422 on approve is not an
    # answer to that.
    qty: float
    # what the count column showed: "ft"/"pt" for a measured row, else
    # EA / CY / SF / LF. "pt" means this sheet had no scale.
    unit: str = "ft"
    # the computed figure being replaced. Never dropped: an export that
    # cannot show what was measured cannot be audited.
    engine_qty: float | None = None


class ApproveBody(BaseModel):
    # style_id -> final label; only listed styles are exported
    labels: dict[int, str]
    # target raster width in px for TakeoffDraft2 vertex coordinates
    # (the app stores vertices in 300dpi-image pixel space)
    image_dpi: int = 300
    # individual chains the reviewer erased in the canvas
    excluded_chain_ids: list[int] = []
    # label -> unit price per linear foot (optional, for itemization)
    prices: dict[str, float] = {}
    # label -> trade division (Earthwork & Grading, Wet Utilities, ...) as
    # grouped in the review UI; carried through to export/report rows
    trades: dict[str, str] = {}
    # label -> number of discrete objects, for styles the reviewer measures
    # as countable pieces (shear-wall segments, pads) instead of footage.
    # float, not int: the quantity box is an <input type="number"> and
    # nothing stops a reviewer typing 3.5 into it. Declared int, pydantic
    # answered a 422 whose detail is a LIST OF DICTS, which the review UI
    # rendered as the string "[object Object]" - so a whole approve failed
    # and the operator was told nothing at all. Half a pad is a strange
    # quantity, but refusing to record it is not this field's job.
    object_counts: dict[str, float] = {}
    # chains created by review-UI edits (split / extend): full geometry,
    # replacing originals the client puts in excluded_chain_ids
    added_chains: list[dict] = []
    # chain_id -> label override ("separate from group": same style, its
    # own itemized row)
    chain_labels: dict[int, str] = {}
    # calculator results (earthwork CY, trench bedding, symbol counts...):
    # [{label, qty, unit, unit_price?}] - they price and report like any
    # other takeoff row, they just are not linear footage
    extra_items: list[dict] = []
    # Free-text lines printed on the FACE of the report PDF, under the scale
    # statement. A quantity whose basis the reader cannot see is exactly what
    # this product must not ship: the review UI sends the earthwork caveat
    # here (contour interpolation, how many contours, region not bounded), so
    # a person holding only the PDF learns it too. Bounded in `approve`.
    notes: list[str] = []
    # the scale the reviewer had on screen. Omitted = keep whatever this page
    # already decided (a stored pick, else the engine's). See _page_scale.
    scale: ApproveScale | None = None
    # WORK AREA quantity mask, [[x0,y0,x1,y1],...] in page pt: per-label
    # footage counts only length inside the union of these boxes, clipped
    # with the same segment/rect math the review panel used (_wz_frac).
    # None or empty = no mask, and the export is byte-for-byte what it was.
    # Elements always keep full geometry - the mask changes what COUNTS,
    # never what was detected.
    work_zones: list[list[float]] | None = None
    # label -> the quantity the operator typed over the computed one, for
    # EVERY row kind the itemized panel shows: footage, EA object count,
    # symbol count, calculator CY/SF/LF. Built from computeTakeoffRows(), the
    # same single function the panel and the CSV are built from - so the
    # export cannot drift from the screen the way the hand-assembled payload
    # did. See QtyOverride.
    quantity_overrides: list["QtyOverride"] = []
    # ---- OPERATOR LINE COLOUR ------------------------------------------
    # The operator giving a black-and-white sheet a readable visual language
    # ("red for a fiber line, light yellow for existing contour"). Both maps
    # are OVERRIDES: the engine's measured colour is still what arrives on
    # each chain, it is still what identifies the family across the sheets of
    # a set, and nothing here rewrites it - `approve` ranks them below.
    #
    # Optional with empty defaults, so a client that predates the field posts
    # exactly what it always did. Its export then gains two EMPTY declarations
    # (`operator_created_style_ids: []` and an empty `line_colors`) and is
    # otherwise unchanged - written even when empty for the same reason
    # `overrides` is: [] says "this export knows what an operator colour is
    # and has none", which a file predating the field cannot say, and a
    # reader has to be able to tell those two apart.
    #
    # style_id -> "#rrggbb" the whole family is drawn in. Already RESOLVED by
    # the client, which is the only place that knows the operator's
    # cross-job colour convention (it lives in that browser's saved work
    # beside the prices, the same way the trades do).
    style_colors: dict[int, str] = {}
    # chain_id -> "#rrggbb" for ONE line, overriding its family. This is what
    # the individual-line detector bought: the app can address a single line,
    # so a single line can carry its own colour.
    chain_colors: dict[int, str] = {}
    # THE MACHINE-READABLE OPERATOR PROVENANCE. style_ids of families a HUMAN
    # created in the review UI (the detector never found them; their lines
    # were moved in by hand). Until this field the fact survived export only
    # as English inside `notes`, so a downstream reader had to parse prose to
    # tell an operator's family from a detected one. The note is still sent -
    # a person reading the report deserves it too - and this is its typed
    # twin, exactly as export["scale"]["setBy"] is the typed twin of the
    # sentence in its `name`.
    operator_created_style_ids: list[int] = []
    # families the operator counts ONCE: a wall drawn as its two faces is
    # measured on its outside face, a footing's two edges as one run
    # (GET .../twins; takeoff_engine/twin_lines.py). Geometry and lengthPt
    # stay as drawn; each element carries onceFrac and the itemization
    # multiplies by it, exactly as it does workAreaFrac.
    count_once_style_ids: list[int] = []


def _once_frac(once: dict, ch: dict) -> float:
    """The counted share of one line in a family counted once."""
    fam = once.get(ch.get("style_id")) or {}
    row = (fam.get("chains") or {}).get(str(ch.get("chain_id")))
    if not row or not row.get("d"):
        return 1.0                       # a line the pass never saw counts in full
    return round(max(0.0, min(1.0, float(row["c"]) / float(row["d"]))), 6)


def _hex_color(v) -> "str | None":
    """A colour an operator chose, normalised to "#rrggbb", or None.

    Validated rather than trusted: these strings are written straight into
    the export and onto the report, and a client that sent "red" or
    "#ff00cc; drop" must degrade to the engine's own measured colour rather
    than put a broken value in front of a reader. Silent fallback is right
    here and only here - the value is decoration, not a quantity, so nothing
    a person is billed for can move because a colour failed to parse.
    """
    if not isinstance(v, str):
        return None
    s = v.strip().lower()
    if s.startswith("#"):
        s = s[1:]
    if len(s) == 3 and all(c in "0123456789abcdef" for c in s):
        return "#" + "".join(c * 2 for c in s)
    if len(s) == 6 and all(c in "0123456789abcdef" for c in s):
        return "#" + s
    return None


@app.post("/api/job/{job_id}/page/{page}/approve")
def approve(job_id: str, page: int, body: ApproveBody):
    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    f = _job_dir(job_id) / f"p{page}_result.json"
    if not f.exists():
        raise HTTPException(404, "no result to approve")
    res = json.loads(f.read_text())
    scale = body.image_dpi / 72.0
    # One ratio for the whole page: fy/fx, or exactly 1.0 when the sheet has a
    # single scale - in which case _axis_len_pt is never called and every
    # existing export is byte-for-byte what it was.
    _v_ratio = 1.0
    if body.scale and body.scale.v_ft_per_paper_inch:
        _fx = body.scale.ft_per_paper_inch
        _fy = body.scale.v_ft_per_paper_inch
        if _fx > 0 and _fy > 0:
            _v_ratio = _fy / _fx

    _wz = _wz_norm(body.work_zones)
    _wz_fr: list = []   # per-element inside fraction, parallel to `elements`
    # families a human invented, as a set, for the per-element stamp below
    _mine_sids = {int(s) for s in body.operator_created_style_ids
                  if isinstance(s, (int, float)) and not isinstance(s, bool)}
    _sid_exported: set = set()          # style_ids that produced an element
    _style_col_used: dict = {}          # style_id -> the colour it drew in
    _chain_col_used: dict = {}          # chain_id -> its own colour override
    elements = []
    excluded = set(body.excluded_chain_ids)
    _once_sids = {int(v) for v in body.count_once_style_ids
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
    _once = {}
    if _once_sids:
        _tw = _page_twins(job_id, page, res)
        _once = {int(k): v for k, v in (_tw.get("families") or {}).items() if int(k) in _once_sids}
    for ch in list(res["chains"]) + list(body.added_chains):
        label = body.chain_labels.get(ch["chain_id"]) \
            or body.labels.get(ch["style_id"])
        if label is None or ch["chain_id"] in excluded:
            continue
        wf = _wz_frac(ch["points"], _wz, _v_ratio) if _wz else 1.0
        _wz_fr.append(wf)
        color = ch.get("color")
        # THE ENGINE'S OWN COLOUR, kept whatever else happens. It is a
        # measurement (chain.py grouped these chains by it), so it is carried
        # beside any override rather than replaced by one.
        engine_hex = "#%02x%02x%02x" % tuple(int(c * 255) for c in color) if color else "#333333"
        # PRECEDENCE - per-chain > per-family > engine. Read it as "the more
        # specific decision wins, and a measurement is what you get when
        # nobody decided". It mirrors webui/app.js colorResolve() exactly;
        # the convention-by-label rank that sits between per-family and
        # engine there is already folded into style_colors by the client,
        # which is the only side that holds the book.
        _col_ovr = (_hex_color(body.chain_colors.get(ch["chain_id"]))
                    or _hex_color(body.style_colors.get(ch["style_id"])))
        hexcol = _col_ovr or engine_hex
        elements.append({
            "type": "line",
            "label": label,
            "color": hexcol,
            # Machine-readable twin of the colour itself, written only when a
            # human actually chose one - so a reader can tell "this is red
            # because a person said so" from "this is red because the
            # drafter plotted it red", and can still recover what was
            # measured. Keyed on whether an override was SENT, not on whether
            # the two strings differ: choosing exactly the detected colour is
            # still a decision.
            **({"colorSetBy": _BY_REVIEWER, "engineColor": engine_hex}
               if _col_ovr else {}),
            # ...and the same for the family's provenance, per element, so a
            # reader never has to join against a list to learn it.
            **({"styleCreatedBy": _BY_REVIEWER}
               if ch["style_id"] in _mine_sids else {}),
            "vertices": [{"x": round(x * scale, 1), "y": round(y * scale, 1)}
                         for x, y in ch["points"]],
            "detectedClass": ch["class"],
            "lengthPt": ch["length_pt"],
            # `lengthPt` stays TRUE page points, always. When a vertical scale
            # is in force this carries the axis-adjusted length beside it
            # rather than quietly changing what lengthPt means - a field whose
            # units depend on a flag elsewhere is how silent unit bugs start.
            **({"lengthPtAxis": _axis_len_pt(ch["points"], _v_ratio,
                                             ch["length_pt"])}
               if _v_ratio != 1.0 else {}),
            # geometry stays FULL under a work-area mask; the fraction is
            # written beside it so a reader can reconcile clipped items to
            # elements without re-deriving the clip
            **({"workAreaFrac": round(wf, 6)} if _wz else {}),
            # counted once: the share of THIS line that is the run the
            # estimator bills (1.0 = all of it, 0.0 = an inside face)
            **({"onceFrac": _once_frac(_once, ch)}
               if ch.get("style_id") in _once else {}),
        })
        # what actually reached the export, for the summaries written below
        _sid_exported.add(ch["style_id"])
        if _col_ovr:
            if _hex_color(body.chain_colors.get(ch["chain_id"])):
                _chain_col_used[ch["chain_id"]] = _col_ovr
            else:
                _style_col_used[ch["style_id"]] = _col_ovr
    if _once:
        # which families were counted once, by which rule, and what that did
        _once_summary = {str(k): {"rule": v.get("rule"), "kind": v.get("kind"),
                                  "drawnFt": v.get("drawn_ft"), "countedFt": v.get("counted_ft")}
                         for k, v in _once.items()}
    export = {
        "source": Path(JOBS[job_id]["path"]).name if job_id in JOBS else "",
        "pageIndex": page,
        "imageDpi": body.image_dpi,
        "pageWidthPt": res["page"]["width_pt"],
        "pageHeightPt": res["page"]["height_pt"],
        "elements": elements,
        **({"countOnce": _once_summary} if _once else {}),
    }
    det = _page_scale(job_id, page, res, body.scale)
    if det:
        # pixels-per-foot in the export's own pixel space; the app rescales
        # to its stored image width at import
        export["scale"] = {
            "pixelsPerUnit": body.image_dpi / det["ft_per_paper_inch"],
            "unit": "ft",
            "name": _scale_name(det),
        }
        if det.get("set_by") == _BY_REVIEWER:
            # machine-readable twin of the sentence in `name`, so no consumer
            # has to parse English to learn that a human set this number
            export["scale"].update({
                "setBy": _BY_REVIEWER,
                "ftPerPaperInch": det["ft_per_paper_inch"],
                "engineFtPerPaperInch": det.get("engine_ft_per_paper_inch"),
                "engineText": det.get("engine_text"),
                "setAt": det.get("set_at"),
            })
            if det.get("basis") == _BASIS_SUGGESTION:
                # which of the two reviewer cases this is: their own number,
                # or one of the engine's own unpromoted readings accepted
                export["scale"].update({
                    "basis": _BASIS_SUGGESTION,
                    "suggestionText": det.get("suggestion_text"),
                    "suggestionOrigin": det.get("suggestion_origin"),
                    "engineSource": det.get("engine_source"),
                })
    # itemization: per-label count + footage (when scale known) + pricing
    ft_per_in = det["ft_per_paper_inch"] if det else None
    totals: dict = {}
    for el, _f in zip(elements, _wz_fr) if _wz else ((el, 1.0) for el in elements):
        # work-area mask: a chain fully outside counts nothing - not
        # footage and not a line count - exactly as computeTakeoffRows()
        # skips it in the panel the operator approved from
        if _wz and _f <= 0:
            continue
        t = totals.setdefault(el["label"], {"count": 0, "length_pt": 0.0})
        t["count"] += 1
        t["length_pt"] += el.get("lengthPtAxis", el["lengthPt"]) * _f * el.get("onceFrac", 1.0)
    # `qty <= 0` is "no override" here because it is "no override" in the box:
    # computeTakeoffRows applies one only `if (!isNaN(ov) && ov > 0)`.
    ov_ft: dict = {}
    ov_qty: dict = {}
    ov_used: set = set()
    ov_warn: list = []
    for _o in body.quantity_overrides:
        _lab = (_o.label or "").strip()
        if not _lab or _o.qty is None or _o.qty <= 0:
            continue
        (ov_qty if _o.kind == "qty" else ov_ft)[_lab] = _o

    items = []
    for label, t in sorted(totals.items(), key=lambda kv: -kv[1]["length_pt"]):
        ft = t["length_pt"] / 72.0 * ft_per_in if ft_per_in else None
        price = body.prices.get(label)
        n_obj = body.object_counts.get(label)
        # PRESENCE, not truthiness. object_counts now carries the DETECTED
        # count, and a style the reviewer put in object mode that yielded
        # zero objects sent 0 - which `if n_obj:` read as "not an object
        # row", quietly rerouting it into the footage branch and dropping
        # any EA override on it. CLAUDE.md section 5, "0.0 is not None".
        if n_obj is not None:
            o = ov_qty.get(label)
            qty = float(o.qty) if o else n_obj
            if o:
                ov_used.add(("qty", label))
            # priced per EACH: footage stays as reference, money follows count
            items.append({
                "label": label, "count": t["count"],
                "trade": body.trades.get(label),
                "length_ft": round(ft, 1) if ft is not None else None,
                "qty": qty, "unit": "EA",
                "unit_price": price,
                "extended": round(qty * price, 2) if price else None,
                **({"qty_set_by": _BY_REVIEWER, "engine_qty": n_obj} if o else {}),
            })
        else:
            o = ov_ft.get(label)
            billed = ft
            if o and ft is not None:
                billed = round(float(o.qty), 1)
                ov_used.add(("ft", label))
            items.append({
                "label": label, "count": t["count"],
                "trade": body.trades.get(label),
                "length_ft": round(billed, 1) if billed is not None else None,
                "unit_price": price,
                "extended": round(billed * price, 2) if (billed is not None and price) else None,
                # The geometry still measures the original, and the per-chain
                # elements in this same file still sum to it. Both figures are
                # carried so a reader can reconcile them rather than guess.
                **({"qty_set_by": _BY_REVIEWER,
                    "engine_length_ft": round(ft, 1)} if (o and ft is not None) else {}),
            })
    for ex in body.extra_items:
        try:
            qty = round(float(ex.get("qty", 0)), 1)
        except (TypeError, ValueError):
            continue
        raw_label = str(ex.get("label", "")).strip()
        label = raw_label[:80]
        if not label or qty <= 0:
            continue
        price = ex.get("unit_price")
        try:
            price = float(price) if price is not None else None
        except (TypeError, ValueError):
            price = None
        unit = str(ex.get("unit", ""))[:8] or "EA"
        # `unit == "EA"` exactly, not .upper(): the UI splits on the same
        # literal, and a case difference here would read the wrong bucket.
        # Looked up on the UNTRUNCATED label, which is what the UI keyed on.
        o = (ov_qty if unit == "EA" else ov_ft).get(raw_label)
        eng_qty = qty
        if o:
            qty = round(float(o.qty), 1)
            ov_used.add(("qty" if unit == "EA" else "ft", raw_label))
        items.append({
            "label": label, "count": 1, "length_ft": None,
            "trade": body.trades.get(label),
            "qty": qty, "unit": unit,
            "unit_price": price,
            "extended": round(qty * price, 2) if price else None,
            **({"qty_set_by": _BY_REVIEWER, "engine_qty": eng_qty} if o else {}),
        })
    # Mirrors TRADES in webui/app.js, in the same order, so the report and
    # the panel group and sort identically. Keep the two in step.
    trade_rank = {t: i for i, t in enumerate((
        "Earthwork & Grading", "Wet Utilities", "Dry Utilities",
        "Paving & Concrete", "Footing & Foundation", "Structures",
        "Fencing & Sitework", "General"))}
    items.sort(key=lambda it: (trade_rank.get(it.get("trade"), 99),))
    # Anything the operator typed that matched no row here. Silence would let
    # a renamed or unticked row swallow a correction without trace.
    for _o in body.quantity_overrides:
        _lab = (_o.label or "").strip()
        if not _lab or _o.qty is None or _o.qty <= 0:
            continue
        if (_o.kind, _lab) not in ov_used:
            ov_warn.append(f'"{_lab}": a typed quantity matched no row in this '
                           f"export and was NOT applied.")

    export["items"] = items
    # Written even when empty, on purpose: [] says "this export knows what an
    # override is and has none", which a file predating the field cannot say.
    # A reader has to be able to tell those two apart.
    export["overrides"] = [
        {"label": it["label"],
         "kind": "qty" if it.get("unit") else "ft",
         "unit": it.get("unit") or "ft",
         "qty": it.get("qty") if it.get("qty") is not None else it["length_ft"],
         "engine_qty": it.get("engine_qty") if it.get("engine_qty") is not None
                       else it.get("engine_length_ft")}
        for it in items if it.get("qty_set_by") == _BY_REVIEWER
    ]
    if ov_warn:
        export["override_warnings"] = ov_warn[:12]
    # Written even when empty, for the same reason `overrides` is: [] says
    # "this export knows what an operator-created family is and has none",
    # which a file predating the field cannot say. Only ids that actually
    # reached an element are listed - a family with nothing in it exports
    # nothing, so naming it here would be a claim with no rows behind it.
    _mine_used = sorted({ch_sid for ch_sid in _mine_sids
                         if ch_sid in _sid_exported})
    export["operator_created_style_ids"] = _mine_used
    # ...and the colours a person chose, as a summary beside the per-element
    # stamps, so a reader can see the sheet's whole visual language at once
    # without walking every element.
    export["line_colors"] = {
        "by_style": {str(k): v for k, v in sorted(_style_col_used.items())},
        "by_chain": {str(k): v for k, v in sorted(_chain_col_used.items())},
    }
    # verbatim onto the report (report_pdf prints them under the scale
    # lines); capped so no caller can paper over the table with prose.
    #
    # SEVEN, NOT SIX, since 2026-09-02. The earthwork block now carries a
    # SEVENTH fact the report has to state - how much of the sheet's contour
    # linework is behind the volume (webui/app.js ewCoverageNote): on Oncor
    # C-04 that is 41 of 368 contour lines, with 218 of them carrying no
    # elevation at all. Six slots meant adding it silently dropped whichever
    # caveat sat last, which is the failure mode the cap exists to prevent,
    # not an instance of it. The cap still bounds prose - each note is
    # capped at 400 characters as well - and webui/app.js APPROVE_NOTES_MAX
    # mirrors this number; approveNotesDropped() discloses any trim.
    export["notes"] = [str(x).strip()[:400] for x in body.notes[:7]
                       if str(x).strip()]
    if _wz:
        # Disclosure travels with the record (CLAUDE.md: a mask states what
        # it removed, never silently): the zones, and counted vs excluded
        # footage, in the export AND as a note the report PDF prints.
        _in_pt = sum(el.get("lengthPtAxis", el["lengthPt"]) * f
                     for el, f in zip(elements, _wz_fr))
        _all_pt = sum(el.get("lengthPtAxis", el["lengthPt"]) for el in elements)
        wa = {"zones": [list(z) for z in _wz],
              "counted_pt": round(_in_pt, 1),
              "excluded_pt": round(_all_pt - _in_pt, 1)}
        if ft_per_in:
            wa["counted_ft"] = round(_in_pt / 72.0 * ft_per_in, 1)
            wa["excluded_ft"] = round((_all_pt - _in_pt) / 72.0 * ft_per_in, 1)
        export["work_area"] = wa
        _u = "ft" if ft_per_in else "pt"
        export["notes"].append(
            f"Work area: {len(_wz)} box(es) drawn by the reviewer. Every "
            f"quantity in this export counts only length inside them - "
            f"{wa.get('counted_ft', wa['counted_pt']):,} {_u} counted, "
            f"{wa.get('excluded_ft', wa['excluded_pt']):,} {_u} outside "
            f"excluded. Element geometry is complete; each element's "
            f"workAreaFrac says how much of it counted.")
    out = _job_dir(job_id) / f"p{page}_elements.json"
    out.write_text(json.dumps(export, indent=1))
    _ledger_approve(job_id, page, body, res, export)
    return JSONResponse({"exported": len(elements), "file": str(out),
                         "items": items, "scale": export.get("scale"),
                         "overrides": export["overrides"],
                         "override_warnings": ov_warn})


# A legend sheet is one page of drafting, not a plan set. 64 MB is well past
# any real one and still refuses a mis-dropped 500 MB scan before it is read.
LEGEND_UPLOAD_MAX = 64 * 1024 * 1024


class LegendSheetBody(BaseModel):
    # optional: a separate legend PDF. Packages are often split one sheet
    # per file, so the legend may not be a page of the plan being reviewed.
    path: str | None = None


def _legend_names(src: Path) -> dict:
    """Read a dedicated legend sheet ("C1.1 LEGEND & NOTES") and return the
    trade names it defines, as a vocabulary for the reviewer to pick from.

    Deliberately NOT applied automatically: see legend_detect for why
    matching these to styles by geometry produces confident wrong labels.

    Lifted out of the endpoint on 2026-09-02 so the TYPED-PATH route and the
    new DRAG-AND-DROP one read a legend the same way. Two copies of this scan
    is exactly how the drop route would end up finding a different vocabulary
    than the path route on the same PDF.
    """
    from takeoff_engine.legend_detect import read_legend_sheet

    doc = pymupdf.open(src)
    best: list = []
    scanned = []
    for i in range(doc.page_count):
        pg = doc[i]
        txt = pg.get_text()[:4000].upper()
        if "LEGEND" not in txt:
            continue
        scanned.append(i)
        words = page_words(pg)
        chains = build_chains(extract_strokes(pg), words=words)
        ent = read_legend_sheet(words, chains)
        if len(ent) < 8:
            # exploded-text legend sheets (GCHC/RPA): the row labels are
            # vector strokes, not text - OCR the render and retry
            try:
                import numpy as _np
                from takeoff_engine.text_detect import ocr_words
                _pix = pg.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72),
                                     colorspace="rgb")
                _img = _np.frombuffer(_pix.samples, dtype=_np.uint8).reshape(
                    _pix.height, _pix.width, 3)
                ocr = ocr_words(_img, px_to_pt=72.0 / 200)
                ent2 = read_legend_sheet(list(words) + ocr, chains)
                if len(ent2) > len(ent):
                    ent = ent2
            except Exception:
                pass
        if len(ent) > len(best):
            best = ent
    doc.close()
    names, seen = [], set()
    for e in best:
        n = e["label"].strip()
        if n.upper() in seen:
            continue
        seen.add(n.upper())
        names.append(n)
    return {"source": str(src), "pages_scanned": scanned,
            "names": names, "count": len(names)}


@app.post("/api/job/{job_id}/legend-sheet")
def legend_sheet(job_id: str, body: LegendSheetBody):
    """The typed-path route, kept verbatim for the owner's own use: a path on
    this machine, or blank to search the plan being reviewed."""
    job = JOBS.get(job_id) or _404()
    src = _client_pdf(body.path) if body.path else Path(job["path"])
    if not src.exists():
        raise HTTPException(404, "not found")
    return JSONResponse(_legend_names(src))


@app.post("/api/job/{job_id}/legend-sheet-upload")
async def legend_sheet_upload(job_id: str, file: UploadFile, request: Request):
    """The DROP route. Same scan, a file instead of a path.

    From the 2026-09-02 screen share: the legend control was a prompt() asking
    for a filesystem path, and the owner's business partner could not work out
    what to put in it - "what do I put here to actually tell it it's this
    page?". A path is a thing only the owner, sitting at his own laptop, can
    supply; on the hosted box _client_pdf refuses paths outright, so there was
    no way in at all.

    Named by CONTENT like /api/upload, for the same reason: dropping the same
    legend twice must not litter the store with copies. It is NOT registered
    as a job - a legend sheet is a vocabulary, not a plan to review, and
    putting it in the plan library is how the page strip fills up with sheets
    nobody asked to take off.
    """
    JOBS.get(job_id) or _404()
    # REFUSE BEFORE READING, when the client says how big it is. The size
    # check below runs on `raw`, which means a 2 GB drop is already in this
    # process's memory before anything rejects it - one careless drag on a
    # box with other work on it. Content-Length is the client's claim and a
    # client can lie, so the check on the real bytes STAYS; this is the
    # cheap door in front of it (2026-09-03).
    _cl = request.headers.get("content-length")
    if _cl and _cl.isdigit() and int(_cl) > LEGEND_UPLOAD_MAX * 2:
        raise HTTPException(413, "that upload is larger than "
                                 f"{LEGEND_UPLOAD_MAX // (1024 * 1024)} MB")
    raw = await file.read()
    if not raw:
        raise HTTPException(400, "that file was empty")
    if raw[:5] != b"%PDF-":
        # the drop zone accepts anything a person can drag, so say what is
        # wrong here rather than failing inside pymupdf with a parser message
        raise HTTPException(400, "that is not a PDF - a legend sheet has to "
                                 "be a PDF")
    if len(raw) > LEGEND_UPLOAD_MAX:
        raise HTTPException(413, "that PDF is larger than "
                                 f"{LEGEND_UPLOAD_MAX // (1024 * 1024)} MB")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    stem = Path(file.filename or "legend.pdf").name
    dest = JOBS_DIR / f"legend_{digest}_{stem}"
    if not dest.exists() or dest.stat().st_size != len(raw):
        dest.write_bytes(raw)
    out = _legend_names(dest)
    out["source"] = stem          # the operator's own filename, not our path
    return JSONResponse(out)


@app.get("/api/job/{job_id}/page/{page}/scale/dimensions")
def get_scale_dimensions(job_id: str, page: int):
    """The scale in force checked against the dimensions the sheet prints
    (takeoff_engine.dimcheck). A second witness beside the drawn bar, and
    the one that catches a caption read off the wrong viewport: every
    printed `60'` sits on a line whose paper length is known exactly.

    Reports agreement / disagreement / could-not-tell with the witnesses
    behind it. It never sets the scale. Cached per (page, scale in force)
    beside the result, so a re-read after a scale pick recomputes and a
    plain reload does not."""
    from takeoff_engine.dimcheck import dimension_check

    job = JOBS.get(job_id) or _404()
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    sc = _page_scale(job_id, page, res)
    ft = None
    if sc:
        try:
            ft = float(sc.get("ft_per_paper_inch") or 0) or None
        except (TypeError, ValueError):
            ft = None
    cache = _job_dir(job_id) / f"p{page}_dimcheck.json"
    key = {"scale": ft, "engine_sig": res.get("engine_sig")}
    try:
        cached = json.loads(cache.read_text())
        if cached.get("key") == key:
            return JSONResponse(cached["check"])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    doc = pymupdf.open(job["path"])
    words = page_words(doc[page])
    doc.close()
    check = dimension_check(words, res.get("chains") or [], ft)
    try:
        cache.write_text(json.dumps({"key": key, "check": check}))
    except OSError:
        pass
    return JSONResponse(check)


@app.get("/api/job/{job_id}/page/{page}/scale")
def get_scale_pick(job_id: str, page: int):
    """The scale decision recorded for this page: what the engine reads NOW,
    and whether a human overruled it and with what.

    Read-only, and it changes nothing. It exists so the recorded decision is
    inspectable without opening the job directory - and so the review UI can
    one day restore a pick after a browser reload, which today it cannot:
    the pick lives in an in-memory Map (webui/app.js `store`), so a reload
    forgets it, the screen falls back to the engine's reading, and approving
    that screen correctly retires the recorded pick. See _page_scale.
    """
    f = _job_dir(job_id) / f"p{page}_result.json"
    eng = None
    sugg = None
    if f.exists():
        try:
            _r = json.loads(f.read_text())
            eng = (_r.get("scale") or {}) or None
            sugg = (_r.get("scale_suggestions") or {}) or None
        except (OSError, ValueError):
            eng = sugg = None
    pick = _stored_scale_pick(job_id, page)
    return {
        "engine": None if not eng else {
            "ft_per_paper_inch": eng.get("ft_per_paper_inch"),
            "text": eng.get("text"), "source": eng.get("source")},
        # Readings the engine recovered and refused to promote. Listing them
        # here does NOT put one in force: `in_force` deliberately ignores
        # them, because nothing is in force until a person picks.
        "suggestions": None if not sugg else {
            "source": sugg.get("source"),
            "why": (sugg.get("pick") or {}).get("why"),
            "candidates": _scale_offered(sugg)},
        "reviewer": pick,
        "in_force": (pick or {}).get("ft_per_paper_inch")
                    or (eng or {}).get("ft_per_paper_inch"),
    }


# ---------- training-ledger hooks ------------------------------------------
# Server-side capture of operator decisions (takeoff_engine/ledger.py). Every
# helper here is OBSERVATION ONLY: fully try/except-wrapped, and nothing in
# the detection or calculation path ever reads what they write - a ledger
# failure can cost an event, never a request, and deleting out/ledger changes
# no number any endpoint returns.


def _ledger_pdf(job_id: str):
    j = JOBS.get(job_id)
    return j.get("path") if j else None


def _ledger_calcstate(job_id: str, page: int, old: dict, new: dict) -> None:
    """Semantic, row-level events for one calculator-sidecar write: the old
    and new blobs are diffed key by key (ledger.calcstate_events), and any
    event that names a chain gets the engine's own cached per-chain features
    attached - copied from p<N>_result.json, never recomputed."""
    try:
        evs = ledger.calcstate_events(old, new)
        if not evs:
            return
        feats: dict = {}
        if any((e.get("context") or {}).get("chain_id") is not None
               for e in evs):
            feats = ledger.chain_feature_map(
                _job_dir(job_id) / f"p{page}_result.json")
        pdf = _ledger_pdf(job_id)
        for e in evs:
            ctx = dict(e.get("context") or {})
            cid = ctx.get("chain_id")
            if cid in feats:
                ctx["chain"] = feats[cid]
            ledger.record(e["event"], job_id, page, e.get("before"),
                          e.get("after"), ctx, pdf_path=pdf)
    except Exception:
        pass


# chain_excluded events per approve are capped: a mass-erase of a dense
# sheet is one gesture, not thousands of independent judgments, and the
# ledger should not balloon on it. The cap is disclosed in the summary
# event's context (n_excluded carries the true count).
_LEDGER_MAX_EXCLUDED = 2000


def _ledger_approve(job_id: str, page: int, body: "ApproveBody",
                    res: dict, export: dict) -> None:
    """Label events for one approve: per-style labels (engine suggestion ->
    final), per-chain relabels, per-chain erasures (junk labels), and one
    approve_export summary with the final itemized counts."""
    try:
        pdf = _ledger_pdf(job_id)
        styles = {int(s["style_id"]): s for s in res.get("styles") or []
                  if isinstance(s, dict) and s.get("style_id") is not None}
        chains = {c.get("chain_id"): c for c in res.get("chains") or []
                  if isinstance(c, dict)}
        for c in body.added_chains:
            if isinstance(c, dict) and c.get("chain_id") is not None:
                chains.setdefault(c["chain_id"], c)
        mine = {int(s) for s in body.operator_created_style_ids
                if isinstance(s, (int, float)) and not isinstance(s, bool)}
        for sid, label in body.labels.items():
            st = styles.get(int(sid))
            ledger.record(
                "style_label", job_id, page,
                {"suggested_label": (st or {}).get("suggested_label")},
                {"label": label},
                {"style_id": int(sid),
                 # PROVENANCE, on the label event itself: a model trained on
                 # these must not learn an operator's invented family as if
                 # the detector had proposed it and been renamed. Written on
                 # every style_label, true or false, so its absence means
                 # "an export from before the field" and never "detected".
                 "operator_created": int(sid) in mine,
                 "style": ledger.style_features(st) if st else None},
                pdf_path=pdf)
        # COLOUR, recorded like every other operator correction: what the
        # engine measured, what the human left in force, and the features the
        # engine had. Two events because they are two decisions at two
        # scopes - see webui/app.js colorResolve for the ranking between them.
        for sid, hexv in (body.style_colors or {}).items():
            hexv = _hex_color(hexv)
            if hexv is None:
                continue
            st = styles.get(int(sid))
            ledger.record(
                "style_color", job_id, page,
                {"color": (st or {}).get("color")},
                {"color": hexv, "set_by": _BY_REVIEWER},
                {"style_id": int(sid),
                 "label": body.labels.get(int(sid)),
                 "operator_created": int(sid) in mine,
                 "style": ledger.style_features(st) if st else None},
                pdf_path=pdf)
        for cid, hexv in (body.chain_colors or {}).items():
            hexv = _hex_color(hexv)
            if hexv is None:
                continue
            ch = chains.get(int(cid))
            ledger.record(
                "chain_color", job_id, page,
                {"color": (ch or {}).get("color")},
                {"color": hexv, "set_by": _BY_REVIEWER},
                {"chain_id": int(cid),
                 "chain": ledger.chain_features(ch) if ch else None},
                pdf_path=pdf)
        for cid, label in body.chain_labels.items():
            ch = chains.get(int(cid))
            ledger.record(
                "chain_relabel", job_id, page,
                {"label": body.labels.get((ch or {}).get("style_id"))},
                {"label": label},
                {"chain_id": int(cid),
                 "chain": ledger.chain_features(ch) if ch else None},
                pdf_path=pdf)
        for cid in body.excluded_chain_ids[:_LEDGER_MAX_EXCLUDED]:
            ch = chains.get(cid)
            ledger.record(
                "chain_excluded", job_id, page,
                {"label": body.labels.get((ch or {}).get("style_id"))},
                None,
                {"chain_id": cid,
                 "chain": ledger.chain_features(ch) if ch else None},
                pdf_path=pdf)
        wa = {k: v for k, v in (export.get("work_area") or {}).items()
              if k != "zones"}
        ledger.record(
            "approve_export", job_id, page, None,
            {"n_elements": len(export.get("elements") or []),
             "items": export.get("items"),
             "overrides": export.get("overrides"),
             "scale": export.get("scale"),
             "work_area": wa or None},
            {"n_excluded": len(body.excluded_chain_ids),
             "n_excluded_ledgered": min(len(body.excluded_chain_ids),
                                        _LEDGER_MAX_EXCLUDED),
             "n_added_chains": len(body.added_chains),
             "n_extra_items": len(body.extra_items),
             "object_counts": body.object_counts or None,
             # the sheet's visual language and its provenance, in the one
             # event that summarises a whole approve
             "n_style_colors": len(body.style_colors or {}),
             "n_chain_colors": len(body.chain_colors or {}),
             "operator_created_style_ids":
                 export.get("operator_created_style_ids") or [],
             "image_dpi": body.image_dpi},
            pdf_path=pdf)
    except Exception:
        pass


def _ledger_earthwork(job_id: str, page: int, body: "EarthworkBody",
                      result: dict) -> None:
    """One event per Calculate press: the inputs' shape and the volumes the
    operator was shown. The row-level decisions behind it (z per chain,
    surface assignments) are captured by the calcstate hook; this records
    that the operator ran THIS configuration and saw THESE numbers."""
    try:
        after = {k: result.get(k) for k in
                 ("cut_yd3", "fill_yd3", "net_yd3", "area_sf", "cell_ft",
                  "existing_offset_ft", "proposed_offset_ft")}
        ctx = {"n_existing": len(body.existing),
               "n_proposed": len(body.proposed),
               "n_existing_points": len(body.existing_points),
               "n_proposed_points": len(body.proposed_points),
               "n_existing_layers": len(body.existing_layers),
               "n_proposed_layers": len(body.proposed_layers),
               "strip_area_sf": body.strip_area_sf,
               "cut_reuse_pct": body.cut_reuse_pct,
               "has_boundary": bool(body.boundary)}
        ledger.record("earthwork_calc", job_id, page, None, after, ctx,
                      pdf_path=_ledger_pdf(job_id))
    except Exception:
        pass


def _ledger_structure(job_id: str, page: int, body: "StructureBody",
                      rows: list, totals: dict) -> None:
    try:
        after = {"totals": totals, "n_structures": len(rows)}
        ctx = {"structures": [
            {"name": s.get("name"),
             "n_points": len(s.get("points") or []),
             "depth_ft": s.get("depth_ft"),
             "layback_ft": s.get("layback_ft"),
             "footing_ft": s.get("footing_ft")}
            for s in body.structures if isinstance(s, dict)][:50]}
        ledger.record("structure_calc", job_id, page, None, after, ctx,
                      pdf_path=_ledger_pdf(job_id))
    except Exception:
        pass


@app.get("/api/ledger/stats")
def ledger_stats(request: Request):
    """Training-ledger statistics: totals, per-event-type and per-job
    counts, month files, time range, bytes on disk. This endpoint reads
    the ledger files directly - it sits OUTSIDE the detection/calculation
    pipeline, so the write-only invariant in takeoff_engine/ledger.py
    holds: no pipeline code path reads the ledger.

    by_job is keyed by job id, and a job id is the one handle that opens a
    document. It used to list every account's ids to any signed-in user -
    on a box holding several contractors' bid sets, a directory of the
    other clients' jobs and how hard each was worked. A non-staff caller
    now sees only the ids of jobs they own; staff, and single-password
    mode, see all of them. The aggregate counts carry no id and are left
    as they were."""
    out = ledger.stats()
    if not _is_staff(request):
        me = _who(request)
        with JOBS_LOCK:
            mine = {jid for jid, j in JOBS.items()
                    if me is not None and j.get("owner") == me}
        out["by_job"] = {j: n for j, n in out.get("by_job", {}).items()
                         if j in mine}
    return JSONResponse(out)


# ---------- calculator-input sidecar --------------------------------------
# The review UI's calculator panels (earthwork contours, trench, structure)
# hold operator DECISIONS - surface assignments, corrected elevations, layer
# stacks, trench depths, structure outlines - and until this sidecar they
# lived only in browser memory: a reload emptied the panel while the priced
# row it produced survived, so the takeoff could say "Site Cut 13,677 CY"
# with nothing behind it. Same pattern as the scale pick (_scale_pick_path):
# one JSON file per page in the job dir, so a reload AND another seat both
# get the inputs back. The blob is opaque to the server on purpose - its
# shape belongs to the UI (webui/app.js calcStatePayload), and it carries
# engine_sig so the UI can tell when chain-keyed choices predate the current
# detection instead of silently mis-applying them.

CALCSTATE_MAX_BYTES = 256 * 1024


def _calcstate_path(job_id: str, page: int) -> Path:
    return _job_dir(job_id) / f"p{page}_calcstate.json"


def _calcstate_guard(job_id: str) -> None:
    """Refuse a job_id that is not a direct child of JOBS_DIR (`..` and
    friends), same rule the delete route enforces - this route WRITES."""
    d = (JOBS_DIR / job_id).resolve()
    if d.parent != JOBS_DIR.resolve():
        raise HTTPException(404, "unknown job")


def _calcstate_check(raw: bytes) -> dict:
    """The blob the UI may store: a JSON object, size-capped.

    The cap exists because this is an unstructured write path into the job
    dir: without one a bug that serialises the whole detection result (or a
    hostile client) turns every page flip into megabytes on disk. 256 KB is
    ~13x a deliberately heavy blob (200 contour rows + 4 layers + 3 drawn
    structures serialises to 19.4 KB, measured 2026-08-30)."""
    if len(raw) > CALCSTATE_MAX_BYTES:
        raise HTTPException(413, (
            f"calculator state is {len(raw):,} bytes; the cap is "
            f"{CALCSTATE_MAX_BYTES:,}. Nothing was saved - the earlier "
            f"record, if any, still stands."))
    try:
        data = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "calculator state must be JSON")
    if not isinstance(data, dict):
        raise HTTPException(400, "calculator state must be a JSON object")
    return data


def _calcstate_put(job_id: str, page: int, raw: bytes) -> dict:
    """Validate and store one page's calculator inputs. Atomic: a crash
    mid-write must leave the previous record intact, not half a file -
    json.loads on the GET side would otherwise return {} and the operator's
    decisions would look deleted rather than merely stale."""
    _calcstate_guard(job_id)
    # The registry row, not the directory. This accepted EITHER until
    # 2026-09-01, and a directory with no row is precisely an orphan (see
    # _forbidden_job): that `or` is how account B's calcstate POST rewrote
    # A's pricing and client name with a 200.
    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    data = _calcstate_check(raw)
    f = _calcstate_path(job_id, page)
    # the record being replaced, read BEFORE the replace: the training
    # ledger diffs old vs new to emit row-level events (observation only -
    # an unreadable old blob just means the diff starts from {})
    old: dict = {}
    if f.exists():
        try:
            _o = json.loads(f.read_text())
            old = _o if isinstance(_o, dict) else {}
        except (OSError, ValueError):
            old = {}
    # unique tmp name: two debounced saves landing together must not
    # interleave in one tmp file and publish a torn record
    tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, f)
    _ledger_calcstate(job_id, page, old, data)
    return {"ok": True, "bytes": len(raw)}


@app.get("/api/job/{job_id}/page/{page}/calcstate")
def get_calcstate(job_id: str, page: int):
    """The stored calculator inputs for this page, or {} when none exist.
    {} rather than 404 so the UI's restore path is one unconditional GET."""
    _calcstate_guard(job_id)
    f = _calcstate_path(job_id, page)
    if not f.exists():
        return {}
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


@app.post("/api/job/{job_id}/page/{page}/calcstate")
async def post_calcstate(job_id: str, page: int, request: Request):
    return JSONResponse(_calcstate_put(job_id, page, await request.body()))


@app.get("/api/job/{job_id}/page/{page}/elements")
def get_elements(job_id: str, page: int):
    f = _job_dir(job_id) / f"p{page}_elements.json"
    if not f.exists():
        raise HTTPException(404, "not approved yet")
    return FileResponse(f, media_type="application/json",
                        filename=f"elements_p{page}.json")


@app.get("/api/job/{job_id}/report.pdf")
def report_pdf(job_id: str, request: Request):
    """Compile every approved page of this job into a deliverable PDF:
    per page an overlay of the approved lines on the sheet, then an
    itemized table (label / count / footage / pricing)."""
    import io
    import textwrap

    from PIL import Image, ImageDraw

    job = JOBS.get(job_id) or _404()
    jd = _job_dir(job_id)
    pages = sorted(
        int(f.name[1:].split("_")[0])
        for f in jd.glob("p*_elements.json")
    )
    if not pages:
        raise HTTPException(404, "no approved pages yet")

    doc = pymupdf.open(job["path"])
    out = pymupdf.open()
    grand: dict = {}
    all_notes: list[str] = []

    # THE LETTERHEAD. Resolved for the account that asked for this file, not
    # for the job's owner: "prepared by" is a statement about who produced
    # the document, and staff opening a client's job produced it. Blank on a
    # box nobody has branded, and a blank one draws nothing at all - the PDF
    # is then byte-for-byte the one this endpoint made before branding
    # existed.
    #
    # It sits in the TOP MARGIN of the text pages (baselines 36 and 46; the
    # itemized title's own baseline is 60 and its 16pt cap-height reaches
    # only to y=48.6), so not one existing line moves. It is deliberately
    # NOT drawn on the sheet-overlay pages: those are a full-bleed raster of
    # the client's own drawing and text over them would sit on the linework.
    _brec, _bscope = _branding.resolve(_who(request))
    _bco = (_brec.get("company") or "").strip()
    _bct = (_brec.get("contact") or "").strip()
    _bs_pages: list = []            # every approved page's rows, for the bid-sheet page

    def _letterhead(pg) -> None:
        """Stamp the company on one 612x792 text page. Self-declared and
        printed verbatim - this is the one thing on the report that is not a
        measurement, so it carries no caveat and earns none. It never
        displaces the beta band, which is drawn last, at the bottom, over
        every page including this one."""
        if not _bco:
            return
        pg.insert_text((50, 36), _bco[:80], fontsize=10)
        if _bct:
            pg.insert_text((50, 46), _bct[:120], fontsize=7.5,
                           color=(0.38, 0.38, 0.38))
    for pno in pages:
        exp = json.loads((jd / f"p{pno}_elements.json").read_text())
        pg = doc[pno]
        dpi = 130
        sc = dpi / 72.0
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(sc, sc), colorspace="rgb")
        im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
        im = Image.blend(im, Image.new("RGB", im.size, (255, 255, 255)), 0.45)
        d = ImageDraw.Draw(im)
        px2img = 72.0 / exp["imageDpi"] * sc  # export px -> report image px
        for el in exp["elements"]:
            col = el.get("color") or "#cc0000"
            pts = [(v["x"] * px2img, v["y"] * px2img) for v in el["vertices"]]
            if len(pts) >= 2:
                d.line(pts, fill=col, width=3)
        buf = io.BytesIO()
        im.save(buf, format="PNG")
        newp = out.new_page(width=pg.rect.width, height=pg.rect.height)
        newp.insert_image(newp.rect, stream=buf.getvalue())

        # itemized table page
        items = exp.get("items", [])
        _bs_pages.append({"page": pno, "items": items})
        tp = out.new_page(width=612, height=792)
        _letterhead(tp)
        y = 60
        # Head the table with the PRINTED sheet number when detection read
        # one off the title block ("Sheet C3.1 (page 4)"): the client names
        # sheets by that number, not by PDF position. The index stays in
        # print because the two disagree on purpose (CLAUDE.md - page
        # numbering is ambiguous). Falls back to the old header untouched.
        _sn = None
        _rf = jd / f"p{pno}_result.json"
        if _rf.exists():
            try:
                _sn = json.loads(_rf.read_text()).get("sheet_no")
            except (OSError, ValueError):
                _sn = None
        _hdr = (f"Sheet {_sn} (page {pno + 1}) - Itemized takeoff" if _sn
                else f"Page {pno + 1} - Itemized takeoff")
        tp.insert_text((50, y), _hdr, fontsize=16)
        y += 17
        # Every number in the table below is this multiplication. When a
        # person overrode the engine's reading, the printed takeoff says so
        # on its own face - the reader of the PDF has no other way to know.
        for _ln in _scale_report_lines(exp.get("scale"), exp.get("imageDpi")):
            tp.insert_text((50, y), _ln[:118], fontsize=8)
            y += 11
        # Caveats the reviewer's calculators attached to their own rows. They
        # sit ABOVE the table on purpose: a reader who stops at the first
        # number has already passed them.
        # Above the table, like every other caveat here: a reader who stops
        # at the first number has already passed it.
        _ovs = exp.get("overrides") or []
        if _ovs:
            for _seg in textwrap.wrap(
                    f"{len(_ovs)} quantity(ies) on this sheet were SET BY THE "
                    "REVIEWER, replacing the measured figure. Each is marked * "
                    "with the measured value printed under it. The line "
                    "geometry in the data export still measures the original "
                    "figure.", 118):
                tp.insert_text((50, y), _seg, fontsize=8)
                y += 11
        for _nt in (exp.get("notes") or []):
            if _nt not in all_notes:
                all_notes.append(_nt)
            for _seg in (textwrap.wrap(str(_nt), 118) or [""]):
                tp.insert_text((50, y), _seg, fontsize=8)
                y += 11
        y += 8
        tp.insert_text((50, y), "Label", fontsize=9)
        tp.insert_text((300, y), "Count", fontsize=9)
        tp.insert_text((360, y), "Length (ft)", fontsize=9)
        tp.insert_text((440, y), "Unit $", fontsize=9)
        tp.insert_text((500, y), "Extended $", fontsize=9)
        y += 14
        page_total = 0.0
        for it in items:
            # a row printing a sub-line needs the break a little earlier
            _sub = (it.get("qty_set_by") == _BY_REVIEWER) \
                or (it.get("unit") and it.get("length_ft") is not None)
            if y > (722 if _sub else 740):
                tp = out.new_page(width=612, height=792)
                _letterhead(tp)
                y = 60
            _ov = it.get("qty_set_by") == _BY_REVIEWER
            tp.insert_text((50, y), (str(it["label"])[:50] + " *") if _ov
                           else str(it["label"])[:52], fontsize=9)
            tp.insert_text((300, y), str(it["count"]), fontsize=9)
            # Choose the column by the row's UNIT, not by whether length_ft
            # happens to be populated. An EA row keeps its footage as a
            # REFERENCE, so the old test printed that footage in the
            # "Length (ft)" cell of a row that was priced from a COUNT -
            # Length x Unit $ did not equal Extended $, which is either an
            # unnoticed wrong quantity or a visible contradiction. The
            # reference footage moves to the sub-line below.
            if it.get("unit"):
                _q = it.get("qty")
                _u = (" " + it["unit"]).rstrip()
            else:
                _q = it["length_ft"]
                _u = ""
            # a calculator row that rounded to whole units is a whole
            # number: round(x, 1) leaves 13677.0, which prints
            # "13,677.0 CY" and re-asserts the tenth the rounding removed.
            # Guarded on _u so the footage column is untouched.
            if _q is not None and _u and float(_q).is_integer():
                _q = int(_q)
            tp.insert_text((360, y), f"{_q:,}{_u}" if _q is not None else "-", fontsize=9)
            tp.insert_text((440, y), f'{it["unit_price"]:.2f}' if it.get("unit_price") else "-", fontsize=9)
            tp.insert_text((500, y), f'{it["extended"]:,.2f}' if it.get("extended") else "-", fontsize=9)
            if it.get("extended"):
                page_total += it["extended"]
            # An EA/CY row's reference footage, stated as reference.
            if it.get("unit") and it.get("length_ft") is not None:
                y += 11
                tp.insert_text((60, y),
                               f'({it["length_ft"]:,} ft of line carries this row)',
                               fontsize=8)
            if _ov:
                _e = it.get("engine_length_ft")
                _eu = "ft"
                if _e is None:
                    _e = it.get("engine_qty")
                    _eu = it.get("unit") or ""
                _typed = it.get("qty") if it.get("qty") is not None else it["length_ft"]
                y += 11
                tp.insert_text((60, y),
                               f"* set by the reviewer: {_typed:,} "
                               f"{it.get('unit') or 'ft'}"
                               + (f". Measured on this sheet: {_e:,} {_eu}."
                                  if _e is not None else "."),
                               fontsize=8)
            # Keyed by (label, UNIT). Footage-only accumulation printed an EA
            # row's REFERENCE footage into the project footage total, and gave
            # a 172,094 CY earthwork row a summary line reading "0.0" under
            # "Length (ft)" - a real quantity rendered as nothing, on the page
            # a client is most likely to read.
            _unit = it.get("unit") or "ft"
            g = grand.setdefault((it["label"], _unit),
                                 {"count": 0, "qty": 0.0, "ext": 0.0,
                                  "eng": 0.0, "ov": False, "unit": _unit})
            g["count"] += it["count"]
            if _unit == "ft":
                g["qty"] += it["length_ft"] or 0.0
                _ef = it.get("engine_length_ft")
                g["eng"] += (_ef if _ef is not None else it["length_ft"]) or 0.0
            else:
                g["qty"] += it.get("qty") or 0.0
                _eq = it.get("engine_qty")
                g["eng"] += (_eq if _eq is not None else it.get("qty")) or 0.0
            g["ov"] = g["ov"] or it.get("qty_set_by") == _BY_REVIEWER
            g["ext"] += it.get("extended") or 0.0
            y += 13
        if page_total:
            y += 8
            tp.insert_text((360, y), "Page total:", fontsize=10)
            tp.insert_text((500, y), f"{page_total:,.2f}", fontsize=10)

    # grand summary
    tp = out.new_page(width=612, height=792)
    _letterhead(tp)
    y = 60
    tp.insert_text((50, y), f"{Path(job['path']).name} - Takeoff summary",
                   fontsize=16)
    y += 17
    # Truth in labelling, not de-duplication. These rows add each approved
    # sheet's footage together; the same run drawn on four sheets is in here
    # four times. Matching runs ACROSS sheets is a rotation-invariant problem
    # whose first attempt failed on a bucket edge, and getting it wrong
    # deletes real footage - so the row says what it is instead of pretending.
    tp.insert_text((50, y), f"{len(pages)} approved sheet(s), added together. "
                            "Nothing is de-duplicated across sheets.",
                   fontsize=8)
    y += 11
    # This page repeats the rows, so it repeats what they rest on. A reader
    # who turns straight to the summary must not be the one person who never
    # sees the caveat attached to a quantity in it.
    for _nt in all_notes[:8]:
        for _seg in (textwrap.wrap(str(_nt), 118) or [""]):
            tp.insert_text((50, y), _seg, fontsize=8)
            y += 11
    # Define the star BEFORE the rows that carry it. A row whose typed and
    # measured totals happen to match prints the star with no per-row
    # explanation, so the page needs its own.
    if any(g["ov"] for g in grand.values()):
        for _seg in textwrap.wrap(
                "* one or more quantities under this label were SET BY THE "
                "REVIEWER rather than measured. Where the totals differ, the "
                "measured figure is printed under the row.", 118):
            tp.insert_text((50, y), _seg, fontsize=8)
            y += 11
    y += 10
    tp.insert_text((50, y), "Label", fontsize=9)
    tp.insert_text((300, y), "Count", fontsize=9)
    tp.insert_text((360, y), "Quantity", fontsize=9)
    tp.insert_text((500, y), "Extended $", fontsize=9)
    y += 14
    total = 0.0
    # Footage rows first and biggest-first within a unit, so the page still
    # reads the way it did; a CY or EA row can no longer sort as if it were 0.
    for (label, _u), g in sorted(
            grand.items(),
            key=lambda kv: (kv[1]["unit"] != "ft", -kv[1]["qty"])):
        if y > (728 if g["ov"] else 740):
            tp = out.new_page(width=612, height=792)
            _letterhead(tp)
            y = 60
        tp.insert_text((50, y), (label[:50] + " *") if g["ov"] else label[:52],
                       fontsize=9)
        tp.insert_text((300, y), str(g["count"]), fontsize=9)
        _qs = f'{g["qty"]:,.1f}' if g["unit"] == "ft" else f'{g["qty"]:,g}'
        tp.insert_text((360, y), f'{_qs} {g["unit"]}', fontsize=9)
        tp.insert_text((500, y), f'{g["ext"]:,.2f}' if g["ext"] else "-", fontsize=9)
        total += g["ext"]
        if g["ov"] and abs(g["eng"] - g["qty"]) > 0.05:
            y += 11
            tp.insert_text((60, y),
                           "* includes a quantity set by the reviewer; the "
                           f'measured total was {g["eng"]:,.1f} {g["unit"]}.',
                           fontsize=8)
        y += 13
    if total:
        y += 10
        tp.insert_text((277, y), "SUM OF APPROVED SHEETS:", fontsize=11)
        tp.insert_text((500, y), f"{total:,.2f}", fontsize=11)
        y += 14
        tp.insert_text((50, y), "Not a project total: a run drawn on several "
                                "sheets is counted once per sheet.",
                       fontsize=8)

    # Stamp every page, not just the first. A summary page gets printed on its
    # own and handed to somebody who never saw the rest of the document, and a
    # quantity with no caveat attached is exactly how a beta number reaches a
    # bid.
    # The band is sized to the text, never the other way round. The first cut
    # wrapped to 118 characters and printed [:2], which silently truncated the
    # notice mid-sentence at "...are produced by" - a half-printed disclaimer
    # is worse than none, and it would have broken again the moment anyone
    # edited the wording.
    # THE BID SHEET PAGE: the same approved rows filed onto this seat's bid
    # sheet (Settings -> Bid sheet) in its order and its units; a line
    # nothing matched prints blank, rows no line names print at the end.
    _bs_lines, _bs_scope = _bidsheet.resolve(_who(request))
    _bs = _bidsheet.build(_bs_lines, _bs_pages)
    tp = out.new_page(width=612, height=792)
    _letterhead(tp)
    y = 60
    tp.insert_text((50, y), "Bid sheet", fontsize=16)
    y += 17
    tp.insert_text((50, y), (f"{_bidsheet.scope_text(_bs_scope)} - {len(pages)} approved "
                             "sheet(s); a run on several sheets counts "
                             "once per sheet")[:118], fontsize=8)
    y += 16
    for _x, _h in ((50, "#"), (70, "Item"), (300, "Unit"), (340, "Qty"), (420, "Rows")):
        tp.insert_text((_x, y), _h, fontsize=9)
    y += 14
    _bs_rows = list(_bs["lines"])
    if _bs["unmatched"]:
        _bs_rows.append({"n": "", "name": "Not on the bid sheet", "unit": "", "qty": None, "rows": [], "other": {}})
        _bs_rows += [{"n": "", "name": "   " + r["label"], "unit": r["unit"], "qty": r["qty"], "rows": [r], "other": {}}
                     for r in _bs["unmatched"]]
    for _L in _bs_rows:
        if y > 740:
            tp = out.new_page(width=612, height=792)
            _letterhead(tp)
            y = 60
        tp.insert_text((50, y), str(_L["n"]), fontsize=9)
        tp.insert_text((70, y), str(_L["name"])[:44], fontsize=9)
        tp.insert_text((300, y), str(_L["unit"]), fontsize=9)
        _qv = _L["qty"]
        _qs = "" if _qv is None or (_qv == 0 and not _L["rows"]) else f"{_qv:,.1f}".rstrip("0").rstrip(".")
        tp.insert_text((340, y), _qs, fontsize=9)
        _oth = ", ".join(f"{v:,g} {u} not summed" for u, v in (_L.get("other") or {}).items())
        tp.insert_text((420, y), (str(len(_L["rows"])) + (f" ({_oth})" if _oth else "")) if _L["rows"] else "",
                       fontsize=8)
        y += 12
    _beta = textwrap.wrap(BETA_LONG, 150) or [""]
    for _pg in out:
        _w, _h = _pg.rect.width, _pg.rect.height
        _band = 10 + 8 * len(_beta)
        _pg.draw_rect(pymupdf.Rect(0, _h - _band, _w, _h),
                      color=None, fill=(0.97, 0.93, 0.85), overlay=True)
        _y = _h - _band + 9
        for _seg in _beta:
            _pg.insert_text((36, _y), _seg, fontsize=6.5, color=(0.45, 0.25, 0))
            _y += 8

    pdf_path = jd / "takeoff_report.pdf"
    # deflate=True, garbage=4 - NOT cosmetic. Measured on one Beaumont sheet
    # (engine page 6, 5460x3900 overlay at 130 dpi): out.save() alone writes
    # 63.89 MB against a 4.30 MB source stream, because the image goes in
    # uncompressed and the xref is never collected. With these two arguments
    # it is 3.45 MB - 18.5x smaller, pixels byte-identical (verified by
    # comparing re-rendered pixmaps). The ratio varies with ink density; a
    # sparser sheet measured 25.1x. A 15-sheet job was ~958 MB, i.e. the
    # product's only deliverable could not be emailed. Do not remove.
    out.save(pdf_path, deflate=True, garbage=4)
    return FileResponse(pdf_path, media_type="application/pdf",
                        filename=f"takeoff_{Path(job['path']).stem[:30]}.pdf")


@app.get("/api/job/{job_id}/bidsheet.csv")
def bidsheet_csv(job_id: str, request: Request, ignore: list[str] = Query([])):
    """Every approved page of this job filed onto this seat's bid sheet, in
    its order and units: one CSV row per bid line, blanks kept, the rows no
    line names listed at the end (never dropped). `ignore` repeats
    "page:label" for the sheet contributions the operator unticked on the
    cross-sheet panel, so the CSV agrees with the screen."""
    job = JOBS.get(job_id) or _404()
    jd = _job_dir(job_id)
    pages = sorted(int(f.name[1:].split("_")[0]) for f in jd.glob("p*_elements.json"))
    if not pages:
        raise HTTPException(404, "no approved pages yet")
    skip = set()
    for k in ignore or []:
        pg_, _, label = str(k).partition(":")
        if pg_.isdigit() and label:
            skip.add((int(pg_), label))
    lines, scope = _bidsheet.resolve(_who(request))
    rows = []
    for pno in pages:
        try:
            exp = json.loads((jd / f"p{pno}_elements.json").read_text())
        except (OSError, ValueError):
            continue
        rows.append({"page": pno, "items": exp.get("items", [])})
    text = _bidsheet.to_csv(_bidsheet.build(lines, rows, ignore=skip),
                            Path(job["path"]).name, scope)
    return Response(text, media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="bidsheet_{Path(job["path"]).stem[:30]}.csv"'})


class AnalyzeResponse(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Analyze: an assistant whose hit rate is known.
#
# This endpoint reads a sheet with a vision model. It was OFF until
# 2026-08-23 for a reason that has not gone away: its output re-ticks and
# re-labels a takeoff, and until we had ground truth there was nothing to
# score it against. There now is (ground-truth/*.json, 33 pages), so every
# field below carries the hit rate it actually earned, measured over three
# runs on those pages. The numbers live in _AI_GRADES and travel with the
# payload so the UI can label each suggestion with its own score instead of
# presenting all of them as equally trustworthy.
#
# Two rules that are not negotiable regardless of any future score:
#   * nothing here writes the takeoff. Every proposal is shown, attributed
#     to the model, accepted one at a time and individually reversible.
#   * nothing here is evidence for the confidence banner. A model that
#     agrees with the engine is not a second independent source - they can
#     read the same printed caption and be wrong together.
#
# _AI_GRADES fields: n = scored opportunities, hit = hit rate over the three
# runs, stable = share of items identical across all three runs, verdict =
# what the UI is allowed to do with it. Provenance for every number is in
# out/verify/2026-08-23/aiscored/SCORES.md.
_AI_GRADES: dict = {
    "_measured": "33 ground-truth pages x 3 runs (99 calls, 98 answered), "
                 "xai:grok-4-fast-reasoning, 2026-08-23. Full working in "
                 "out/verify/2026-08-23/aiscored/SCORES.md.",
    "sheet_id": {
        "hit": "18/18 replies right on the 6 pages that carry a "
               "human-confirmed sheet number",
        "stable": "identical in all 3 runs on 32/32 pages",
        "use": "show"},
    "sheet_title": {
        "hit": "24/33 replies right on the 11 pages that carry a "
               "human-confirmed title; 8/11 pages right in every run and "
               "the other 3 wrong in every run",
        "stable": "identical in all 3 runs on 24/32 pages",
        "use": "show"},
    "scale_text": {
        "hit": "27 of the 31 times it answered on a page whose printed "
               "caption we read by eye; 4 wrong, all four on the scanned "
               "aqua-vista set. Where the engine already has a scale it "
               "contradicted it 0 times in 44 replies and abstained 16",
        "stable": "identical in all 3 runs on 30/32 pages",
        "use": "show, never apply"},
    "focus": {
        "hit": "15% of its focus rows on a layered sheet (29/190) name a "
               "HATCH layer, whose stroke length is not a quantity. One "
               "click on all of them keeps a median 18% of the page's "
               "drawn length and drops the rest",
        "stable": "same set in all 3 runs on 8/32 pages; pairwise Jaccard "
                  "0.68 mean, 0.00 worst",
        "use": "one row at a time"},
    "ignore": {
        "hit": "17% of its ignore rows on a layered sheet (248/1471) drop "
               "a WALL / FTNG / SLAB / CURB / PIPE layer - real work",
        "stable": "same set in all 3 runs on 9/32 pages",
        "use": "one row at a time"},
    "label": {
        "hit": "not scorable: on layered sheets the CAD layer name is "
               "already in the prompt (it repeats it 47% of the time), and "
               "the 57 focus rows with no layer have no truth file",
        "stable": "50% - the label matched on 28 of the 56 style_ids that "
                  "all three runs focused",
        "use": "one row at a time"},
    "page_modes": {
        "hit": "earthwork and trench never flickered between runs (1 and 2 "
               "pages); count flickered on 6/32 pages and objects on 5/32",
        "stable": "identical in all 3 runs on 21/32 pages",
        "use": "earthwork and trench chips only"},
    "confidence": {
        "hit": "NOT calibrated - 'high' was right on 10/13 title-block "
               "fields and 'medium' on 32/38, i.e. the wrong way round",
        "stable": "identical in all 3 runs on 18/32 pages",
        "use": "hidden"},
}


def _ai_norm_name(s) -> str:
    """Loose comparison key for two names of the same thing: case, spaces,
    punctuation and CAD's abbreviation dashes all dropped."""
    return "".join(ch for ch in str(s or "").lower() if ch.isalnum())


def _ai_same_name(a, b) -> bool:
    """True when the model's label is the CAD layer name back again. Not a
    quality judgement - an echo can be the right answer - but the UI must
    not sell it as the model having read the drawing."""
    na, nb = _ai_norm_name(a), _ai_norm_name(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def _ai_prompt(text: str, styles_ctx: str, legend_ctx: str,
               variant: str = "graded") -> str:
    """The question we put to the model.

    variant="shipped" reproduces the prompt as it stood at c344420, kept so
    the added asks (sheet id / title / scale) can be shown not to have moved
    the focus and ignore hit rates. Set TAKEOFF_AI_PROMPT_VARIANT=shipped on
    a scratch instance to measure that arm; the service never sets it.
    """
    head = (
        "You are helping a construction estimator do a quantity takeoff from "
        "this plan sheet. Decide WHAT THIS SHEET IS FOR, then which of the "
        "detected line styles an estimator should actually measure on it.\n\n"
        "Sheet text (excerpt):\n" + text +
        "\n\nDetected line styles (these style_id values are the only ones "
        "that exist - never invent one):\n" + styles_ctx + legend_ctx +
        "\n\nGuidance: a sheet usually has ONE job, and most linework on it "
        "is context rather than takeoff. A shear-wall plan means only the "
        "shear walls get measured; a roof plan means only roof framing. "
        "Title blocks, grid lines, dimension strings, leaders, hatching and "
        "match lines are never takeoff. Put a style in focus only if this "
        "sheet's purpose is what that line represents; if you are unsure "
        "about a style, leave it out of both lists.\n\n"
        "Reply with STRICT JSON only, no prose outside it, with keys:\n"
        '  purpose: one sentence on what this sheet is for\n'
        '  discipline: short string\n'
        '  focus: array of {"style_id": int, "label": "short trade name for '
        'this line", "why": "short reason", "measure": "line" or "object"}\n'
        '    measure="object" when the style marks DISCRETE COUNTABLE pieces '
        'an estimator prices each (shear-wall segments, footing pads, beams, '
        'fixtures); measure="line" for continuous runs priced per foot '
        '(pipe, curb, fence, contours).\n'
        '  ignore: array of style_id ints only, no objects\n'
        '  inline_legend: array of {"symbol": ..., "meaning": ...} for any '
        'legend defined on the sheet itself\n'
        '  page_modes: array from ["lines","objects","earthwork","trench",'
        '"count"] - the measurement modes this sheet needs\n'
        '  confidence: "high" | "medium" | "low"\n')
    if variant == "shipped":
        return head + ('Keep every "why" under 12 words so the reply is not '
                       'truncated.')
    # The title block and the scale caption are things the sheet ASSERTS
    # about itself, so they are cheap to ask for and cheap to check. Both
    # are scored; see _AI_GRADES for what the UI does with them.
    return head + (
        '  sheet_id: the sheet number printed in the title block, e.g. '
        '"A-2.00" or "C4.3", or null if you cannot read one\n'
        '  sheet_title: the sheet title printed in the title block, copied '
        'exactly, or null\n'
        '  scale_text: the drawing scale exactly as printed on the sheet, '
        'e.g. "1/4\\" = 1\'-0\\"" or "1\\"=20\'", or null if no scale is '
        'printed. If several are printed, give the one for the main plan '
        'view. Never compute or guess one: null is the right answer when '
        'the sheet does not say.\n'
        'Copy sheet_id, sheet_title and scale_text off the drawing '
        'character for character - do not tidy, expand or normalise them.\n'
        'Keep every "why" under 12 words so the reply is not truncated.')


@app.post("/api/job/{job_id}/page/{page}/analyze")
def analyze_page(job_id: str, page: int, fresh: bool = False):
    """LLM page analyst: what is this sheet FOR, and which line styles
    matter for takeoff? Provider by available key, cheapest-first:
    XAI_API_KEY (Grok) > GROQ_API_KEY > ANTHROPIC_API_KEY.

    The verdict caches for THIS SERVER SESSION only (re-opening the banner
    should not re-bill the model), and never on disk: a pinned verdict that
    survives restarts is blueprint memory a fresh seat would not have, and
    memory was removed 2026-08-20 for report consistency. fresh=1 re-reads
    the sheet."""
    import base64
    import os

    t_start = time.time()
    _ck = (job_id, page)
    if not fresh and _ck in _AI_SESSION_CACHE:
        return JSONResponse(_AI_SESSION_CACHE[_ck])

    # A missing dependency used to surface as a bare 500 with the traceback
    # only in the server log, which is how this endpoint spent two weeks
    # "broken" for an install reason. Name the package.
    try:
        import requests as _rq
    except ImportError:
        raise HTTPException(
            503, "Analyze needs the 'requests' package on the detection "
                 "service (pip install -r requirements.txt).")

    provider = None
    if os.environ.get("XAI_API_KEY"):
        provider = ("xai", "https://api.x.ai/v1", os.environ["XAI_API_KEY"],
                    os.environ.get("TAKEOFF_LLM_MODEL", "grok-4-fast-non-reasoning"))
    elif os.environ.get("GROQ_API_KEY"):
        provider = ("groq", "https://api.groq.com/openai/v1",
                    os.environ["GROQ_API_KEY"],
                    os.environ.get("TAKEOFF_LLM_MODEL",
                                   "meta-llama/llama-4-scout-17b-16e-instruct"))
    elif os.environ.get("ANTHROPIC_API_KEY"):
        provider = ("anthropic", None, os.environ["ANTHROPIC_API_KEY"],
                    os.environ.get("TAKEOFF_LLM_MODEL", "claude-haiku-4-5-20251001"))
    if provider is None:
        raise HTTPException(
            503, "Analyze has no LLM key. Set XAI_API_KEY (Grok), "
                 "GROQ_API_KEY or ANTHROPIC_API_KEY in the detection "
                 "service's .env and restart it; nothing else on this page "
                 "needs a key.")

    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    pg = doc[page]
    scale = 1400 / max(pg.rect.width, pg.rect.height)
    pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale * 2, scale * 2))
    img_b64 = base64.standard_b64encode(pix.tobytes("jpeg")).decode()
    img_px = [pix.width, pix.height]
    text = pg.get_text()[:3000]

    styles_ctx = "(run Detect first - no styles known yet)"
    legend_ctx = ""
    known_ids: set = set()
    layer_of: dict = {}
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if rf.exists():
        res = json.loads(rf.read_text())
        det = res.get("scale") or {}
        ft_per_in = det.get("ft_per_paper_inch")
        rows = []
        for st in res["styles"][:28]:
            known_ids.add(int(st["style_id"]))
            layer_of[int(st["style_id"])] = st.get("layer") or ""
            col = st.get("color")
            hexcol = ("#%02x%02x%02x" % tuple(int(c * 255) for c in col)) \
                if col else "n/a"
            qty = (f'{st["length_pt"] / 72.0 * ft_per_in:,.0f} ft'
                   if ft_per_in else f'{st["length_pt"]:,.0f} pt')
            rows.append(
                f'- style_id {st["style_id"]}: class={st["class"]}, '
                f'{st["n"]} lines, {qty}, colour {hexcol}, '
                f'width {st.get("width")}, pattern "{st.get("signature")}", '
                f'current label "{st["suggested_label"]}"')
        if rows:
            styles_ctx = "\n".join(rows)
        if res.get("legend"):
            legend_ctx = ("\n\nLegend rows the detector already read off this "
                          "sheet:\n" + "\n".join(
                              f'- "{e.get("label")}" -> style_id '
                              f'{e.get("sample_style")}'
                              for e in res["legend"]))

    prompt = _ai_prompt(text, styles_ctx, legend_ctx,
                        os.environ.get("TAKEOFF_AI_PROMPT_VARIANT", "graded"))

    name, base, key, model = provider
    usage = {}
    t_model = time.time()
    # Every failure mode below reaches the operator as its own sentence.
    # "AI analysis failed (500)" tells nobody whether to retry, top up a
    # balance, or fix an install, and this endpoint has now been each of
    # those three.
    if name == "anthropic":
        import anthropic

        try:
            client = anthropic.Anthropic()
            msg = client.messages.create(
                model=model, max_tokens=2000, temperature=0,
                messages=[{"role": "user", "content": [
                    {"type": "image", "source": {"type": "base64",
                     "media_type": "image/jpeg", "data": img_b64}},
                    {"type": "text", "text": prompt}]}])
        except Exception as e:  # anthropic raises its own typed errors
            raise HTTPException(
                502, f"{name} ({model}) refused the request: "
                     f"{type(e).__name__}: {str(e)[:200]}")
        raw = msg.content[0].text
        u = getattr(msg, "usage", None)
        if u is not None:
            usage = {"prompt_tokens": getattr(u, "input_tokens", None),
                     "completion_tokens": getattr(u, "output_tokens", None)}
    else:
        try:
            r = _rq.post(
                f"{base}/chat/completions",
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                json={"model": model, "max_tokens": 2000, "temperature": 0,
                      "messages": [{"role": "user", "content": [
                          {"type": "image_url", "image_url": {
                              "url": f"data:image/jpeg;base64,{img_b64}"}},
                          {"type": "text", "text": prompt}]}]},
                timeout=90)
        except _rq.exceptions.Timeout:
            raise HTTPException(
                504, f"{name} ({model}) did not answer within 90s. The "
                     f"sheet is unchanged - press Analyze again to retry.")
        except _rq.exceptions.RequestException as e:
            raise HTTPException(
                502, f"Cannot reach {name} at {base}: "
                     f"{type(e).__name__}. Check the network, then retry.")
        if not r.ok:
            body = (r.text or "")[:200]
            # xAI answers a wrong key with 400 invalid-argument, not 401, so
            # the status alone does not identify this case. Measured against
            # a deliberately wrong key on 2026-08-23.
            bad_key = ("api key" in body.lower()
                       or "invalid-argument" in body.lower())
            if r.status_code in (401, 403) or (
                    r.status_code == 400 and bad_key):
                msg_ = (f"{name} rejected the API key (HTTP {r.status_code}). "
                        f"The key in the service's .env is wrong, revoked or "
                        f"has no access to {model}.")
            elif r.status_code == 429:
                ra = r.headers.get("retry-after")
                msg_ = (f"{name} rate-limited this request (HTTP 429)"
                        + (f"; retry after {ra}s. " if ra else ". ")
                        + "Nothing was changed on the sheet.")
            elif r.status_code == 404:
                msg_ = (f"{name} has no model called '{model}' (HTTP 404). "
                        f"Fix TAKEOFF_LLM_MODEL in the service's .env.")
            elif r.status_code in (402,):
                msg_ = (f"{name} declined for billing (HTTP 402): {body}")
            elif 500 <= r.status_code < 600:
                msg_ = (f"{name} is failing on its side (HTTP "
                        f"{r.status_code}): {body}. Retry shortly.")
            else:
                msg_ = f"{name} API error {r.status_code}: {body}"
            raise HTTPException(502, msg_)
        try:
            jr = r.json()
            raw = jr["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError):
            raise HTTPException(
                502, f"{name} returned a reply this service could not read: "
                     f"{(r.text or '')[:200]}")
        u = jr.get("usage") or {}
        usage = {"prompt_tokens": u.get("prompt_tokens"),
                 "completion_tokens": u.get("completion_tokens"),
                 "total_tokens": u.get("total_tokens")}
    model_ms = int((time.time() - t_model) * 1000)

    parsed = None
    try:
        parsed = json.loads(raw[raw.index("{"): raw.rindex("}") + 1])
    except Exception:
        # a reply cut off by the token limit leaves unbalanced JSON; recover
        # the fields that did arrive rather than dumping raw text at the user
        for closer in ("]}", "}]}", '"}]}'):
            try:
                cut = raw.rindex(",")
                parsed = json.loads(raw[raw.index("{"):cut] + closer)
                parsed["_truncated"] = True
                break
            except Exception:
                continue
    if not isinstance(parsed, dict):
        # Not a 500 and not a silent shrug: say what came back instead.
        raise HTTPException(
            502, f"{name} ({model}) replied with something that is not JSON, "
                 f"so there is nothing to show. First 200 characters: "
                 f"{str(raw)[:200]}")

    # The UI turns focus/ignore into checkbox state, so a hallucinated
    # style_id would hide real linework from the takeoff. Keep only ids the
    # detector actually produced, and say plainly how many were discarded
    # rather than quietly shrinking the list.
    def _clean(items):
        out, dropped = [], 0
        for it in items if isinstance(items, list) else []:
            # focus arrives as objects, ignore as bare style_id ints
            if isinstance(it, (int, float)) or (
                    isinstance(it, str) and it.strip().lstrip("-").isdigit()):
                it = {"style_id": it}
            if not isinstance(it, dict):
                dropped += 1
                continue
            try:
                sid = int(it.get("style_id"))
            except (TypeError, ValueError):
                dropped += 1
                continue
            if known_ids and sid not in known_ids:
                dropped += 1
                continue
            row = {"style_id": sid}
            for k in ("label", "why"):
                if it.get(k):
                    row[k] = str(it[k])[:120]
            if str(it.get("measure", "")).lower() == "object":
                row["measure"] = "object"
            # A label that just repeats the CAD layer we already handed the
            # model in the prompt is an echo, not a reading. The UI says so,
            # because "the AI named this" is a much stronger claim than the
            # model earned on those rows.
            lay = (layer_of.get(sid) or "").split("|")[-1]
            if lay:
                row["layer"] = lay
                row["echo"] = _ai_same_name(row.get("label"), lay)
            out.append(row)
        return out, dropped

    _MODES = ("lines", "objects", "earthwork", "trench", "count")
    pm = parsed.get("page_modes")
    parsed["page_modes"] = [m for m in (pm if isinstance(pm, list) else [])
                            if isinstance(m, str) and m in _MODES]
    focus, drop_f = _clean(parsed.get("focus"))
    ignore, drop_i = _clean(parsed.get("ignore"))
    seen = {f["style_id"] for f in focus}
    ignore = [i for i in ignore if i["style_id"] not in seen]  # focus wins
    parsed["focus"] = focus
    parsed["ignore"] = ignore
    for k in ("sheet_id", "sheet_title", "scale_text"):
        v = parsed.get(k)
        parsed[k] = str(v)[:120] if isinstance(v, str) and v.strip() else None
    parsed["_invalid_style_ids"] = drop_f + drop_i
    parsed["_known_style_ids"] = len(known_ids)
    parsed["_layered_style_ids"] = sum(1 for v in layer_of.values() if v)
    parsed["_provider"] = f"{name}:{model}"
    parsed["_usage"] = usage
    parsed["_ms"] = {"model": model_ms,
                     "total": int((time.time() - t_start) * 1000)}
    parsed["_img_px"] = img_px
    parsed["_grades"] = _AI_GRADES
    _AI_SESSION_CACHE[_ck] = parsed
    return JSONResponse(parsed)


class EarthworkBody(BaseModel):
    # [{chain_id, z}] per surface, elevations in feet
    existing: list[dict] = []
    proposed: list[dict] = []
    # [{x, y, z}] spot grades in page pt - the printed FG/TS/TW elevations.
    # A detailed grading sheet often prints far more of these than labelled
    # contours, and a TIN can be built from them alone.
    existing_points: list[dict] = []
    proposed_points: list[dict] = []
    cell_ft: float = 2.0
    # How each surface is EVALUATED between its contours: "auto" (measure
    # the sheet's label sparsity and pick), "linear" (the piecewise-linear
    # TIN this app has always used), "clamped_ct" (C1-smooth, clipped to
    # the local contour bracket). See earthwork.SURFACE_MODELS for the
    # measurement behind the choice. The answer always names the model it
    # used, whichever way this arrives.
    surface_model: str = "auto"
    # Layer stacks (Beaumont workflow, F52). Each {name, thickness_in}.
    # existing_layers come OFF first (topsoil strip from the soils report,
    # old asphalt, road base): their total lowers the existing surface
    # before cut/fill. proposed_layers are built back on the proposed
    # side (driveway base + paving): the sheet shows finish grade, the
    # dig stops that much lower, so their total lowers the proposed
    # surface. Each layer's own volume = area x thickness.
    existing_layers: list[dict] = []
    proposed_layers: list[dict] = []
    # area the layers apply over, SF. Default: the TIN overlap area_sf
    # (where both surfaces are defined) - override with the clear & grub
    # area when the strip extends beyond the graded region.
    strip_area_sf: float | None = None
    label_spacing_ft: float | None = None   # spot depths on the cut/fill map, one per block this wide
    place_depth_in: float | None = None     # Rough Place Topsoil: depth over the green area (Beaumont)
    place_less_sf: float | None = None      # footprints + paving taken off the limit of work
    # --- material balance (F87 B7) -------------------------------------
    # Each existing_layers row may also carry {reuse_pct, respread_pct,
    # destination}: what share of that removal is reused as structural
    # fill, what share is respread on site, and where the rest goes. The
    # remainder is exported. The defaults leave every removal at 100%
    # export - Beaumont Estimate #853 shows the stripped topsoil is NOT
    # structural reuse (earthwork.material_balance carries the arithmetic).
    # Site cut IS reused 1:1 against site fill in #853, so this defaults to
    # 100 - the only defaulted assumption in the balance.
    cut_reuse_pct: float = 100.0
    # Volumes are in-place yards between the two surfaces. #853 applies no
    # swell / shrink / compaction factor, so these default to 1.0; they
    # exist only so a user whose job needs one can enter it.
    export_factor: float = 1.0
    import_factor: float = 1.0
    # LIMIT OF WORK: [[x, y], ...] in page points. Outside it no volume is
    # counted. Omitted, the region is wherever both TINs are defined - the
    # intersection of two convex hulls, which bulges wherever the graded area
    # is concave. Two clients hit that independently: "can we boundary these?"
    # on a 172,094 CY read, and a 600-acre sheet scoring cut 2.1M against a
    # true 2.2M but fill 2.6M against a true 1.6M, "filling all that stuff
    # outside". Omitting it leaves every existing number untouched.
    boundary: list[list[float]] | None = None
    # THE BOUNDARY IS THE LIMIT OF DISTURBANCE, not just a crop. The panel
    # sends true when the operator picked or drew a limit of work (never
    # for the one-box work-area fallback). Then the layer area defaults to
    # the ring's own area - Beaumont strips topsoil and clears "everything
    # inside" the limits line (2026-09-15, 05:48-06:27) - and the reply
    # carries the ring's SF and LF for the Clear & Grub and fence rows.
    # False keeps every recorded body's numbers exactly where they were.
    boundary_is_limit: bool = False


# ONE ELEVATION READ PER PAGE AT A TIME.
#
# gap_label_elevations IS the cost of the route below - measured 217.2 s
# cold on Oncor Cloudcrest engine page index 3 = printed C-04 against 1.4 s
# warm - and the only thing that ever made a second request cheap was the
# FINISHED file on disk. Nothing stopped two requests OCRing the same page
# at once, and two is the normal case, not an edge:
#
#   * two people on one job. Measured 2026-09-02, two clients 3 s apart on
#     a cold C-04: 220.0 s and 219.3 s. Two full OCR passes, one answer.
#   * ONE person who reloads, which is what a client did on the 2026-09-02
#     screen share when the panel appeared to hang. The browser's abort
#     does NOT stop the work - this is a `def`, so it runs to completion on
#     the threadpool - but the reloaded page reads the cache as absent and
#     starts its own pass. Reloading at t=60 s therefore does not shorten
#     the wait, it moves the finish from t=220 to t=280 and doubles the
#     load in between.
#
# With the lock the second caller waits for the first and then reads its
# cache, so a page is read once however many people are looking at it.
# A waiter holds a threadpool thread (anyio's default limiter is 40), which
# is why this guards the OCR and not the whole route: everything else here
# is milliseconds.
_ELEVGAP_LOCKS: dict[tuple[str, int], threading.Lock] = {}
_ELEVGAP_GUARD = threading.Lock()


# The label-reading OCR pass's clocks (earthwork.gap_label_elevations).
#
# WHAT WAS MEASURED before choosing them (2026-09-08, this box, per sheet,
# offline: tesseract calls made / first successful raw read at call N /
# wall time, unloaded unless noted):
#
#   Oncor C-04        868 calls / first read at call 4   /  200 s
#   C2.1-Grading      848 calls / call 5                 / 2183 s  (LOADED:
#                     2.57 s per call against 0.23 unloaded - another
#                     agent's detects on the same box; 10x)
#   Belle ROAD 1of2   421 calls / call 57                /   96 s
#   C4_ GRADING       517 calls / call 54                /  105 s
#   C0.00             288 calls / call 5                 /   55 s
#   Barn C3.0         382 calls / call 158 (2 raw reads, /   87 s
#                     both rejected by consensus: 0 chains)
#
# So the "twenty minutes on C3.0" was the 10x load, not the sheet: unloaded
# it is 87 s. And an idle rule ("stop when nothing has been read by T")
# cannot separate them - C3.0 DOES read (at 33 s), and under the same 10x
# load Belle's first read (18 s unloaded) lands at ~180 s, so a 120 s idle
# clock would empty a real sheet before it touched the empty one. A
# wall-clock rule is a bet on the box, and this box has other tenants.
#
#   TAKEOFF_OCR_IDLE_S   the idle clock - OFF by default (0) for the reason
#                        above; available for a dedicated box.
#   TAKEOFF_OCR_BUDGET_S the hard cap, wide: 900 s bounds a runaway pass at
#                        fifteen minutes and the panel SAYS the read was cut
#                        short (elevation_ocr / "Label reading stopped
#                        early"). A cut-short read on a real sheet loses
#                        contours, which is the worse failure, so it is not
#                        tighter. The measured 300 s first cut, run under
#                        load, emptied C2.1 (18 rows on 09-03) to 0.
_idle = float(os.environ.get("TAKEOFF_OCR_IDLE_S", "0") or 0)
OCR_IDLE_S = _idle if _idle > 0 else None
OCR_BUDGET_S = float(os.environ.get("TAKEOFF_OCR_BUDGET_S", "900") or 900)
# ...AND HOW IT READS (2026-09-17). Barn C3.0 kept stopping at the 900 s cap
# with most of its contour labels unread: this box was paying 0.3-1.2 s to
# START each tesseract process, a patch took up to four, and 70% of the
# plan sat on lines that are not contours. The pass now reads contour
# families first and reads patches in batches, several batches at once
# (earthwork.gap_label_elevations - measured in FINDINGS 38).
#   TAKEOFF_OCR_WORKERS  batches read at once. Default half the cores, at
#                        most 4: the box has other tenants.
#   TAKEOFF_OCR_CALLS    the call budget. The engine's 1,400 was sized when
#                        a call cost 0.23 s and nothing else bounded the
#                        pass; it silently cut Barn C3.1 (2,620 places) at
#                        exactly 1,400. The clock above is the bound now,
#                        and a pass the budget stops says so.
_workers = int(os.environ.get("TAKEOFF_OCR_WORKERS", "0") or 0)
OCR_WORKERS = _workers if _workers > 0 else min(4, max(1, (os.cpu_count() or 2) // 2))
OCR_CALLS = int(os.environ.get("TAKEOFF_OCR_CALLS", "6000") or 6000)
# the budget every read before 2026-09-17 ran under (earthwork._OCR_BUDGET),
# which stopped a pass without saying so
_OLD_CALL_BUDGET = 1400


def _elevgap_lock(job_id: str, page: int) -> threading.Lock:
    key = (job_id, page)
    with _ELEVGAP_GUARD:
        lk = _ELEVGAP_LOCKS.get(key)
        if lk is None:
            lk = _ELEVGAP_LOCKS[key] = threading.Lock()
        return lk


@app.get("/api/job/{job_id}/page/{page}/earthwork/progress")
def earthwork_progress(job_id: str, page: int):
    """Where this page's elevation read is, for the panel's busy line.

    {phase: idle | render | ocr | done, pos, planned, calls, reads,
    seconds}. `idle` = nothing running and nothing cached; `done` = the
    OCR cache exists (the next suggest is ~1 s). No lock, no work: it reads
    the sidecar the suggest route writes every ten regions, so the poll is
    cheap however many people are watching one page."""
    JOBS.get(job_id) or _404()
    gcache = _job_dir(job_id) / f"p{page}_elevgaps2.json"
    pfile = gcache.with_suffix(".progress.json")
    try:
        d = json.loads(pfile.read_text())
        if d.get("note") == "render":
            d["phase"] = "render"
        # a sidecar nobody has touched for a while is a crashed pass, not
        # a running one: say idle rather than freeze the bar
        if time.time() - float(d.get("at") or 0) > 600:
            return JSONResponse({"phase": "idle", "stale": True})
        return JSONResponse(d)
    except (OSError, ValueError, TypeError):
        pass
    return JSONResponse({"phase": "done" if gcache.exists() else "idle"})


@app.get("/api/job/{job_id}/page/{page}/earthwork/suggest")
def earthwork_suggest(job_id: str, page: int, more: int = 0):
    """Auto-pair elevation labels with the contours they sit on - a
    suggestion list for the review UI, not a decision.

    `more=1` - KEEP READING: when this page's label read was cut short,
    carry it on from where it stopped (its place and reads are kept beside
    the cache, p<N>_elevgaps2.resume.json) instead of serving the short
    list again."""
    from takeoff_engine.earthwork import suggest_elevations

    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    words = page_words(doc[page])
    # Lines as well as words: a spot grade written up the side of a sheet has
    # its tag and value on one PDF line but nowhere near each other on a
    # horizontal row, so the word reader misses every rotated label and can
    # pair a tag to the value beside it. See earthwork.read_spot_grades.
    from takeoff_engine.extract import page_lines as _page_lines
    lines = _page_lines(doc[page])
    doc.close()

    class _C:  # minimal chain view over the stored result
        def __init__(self, d):
            import numpy as _np
            self.chain_id = d["chain_id"]
            self.pts = _np.asarray(d["points"], float)
            self.length = d["length_pt"]
            self.width = d.get("width")
            # layer and linetype are what stop a profile box or a title
            # block entering the contour TIN, and stop an elevation being
            # inherited across two different objects whose ends happen to
            # meet (earthwork._is_plan_chain / the pass-2 union guard).
            # They are in the stored result; not lifting them here made
            # those guards a silent no-op on the service path even though
            # they measured correctly offline.
            self.layer = d.get("layer") or ""
            self.klass = d.get("class") or ""
            # color + style_id feed the surface split and the exclusion
            # rules below: white = invisible mask ink, style_id = the
            # legend verdict for the family
            self.color = d.get("color")
            self.style_id = d.get("style_id")
    chains = [_C(c) for c in res["chains"]]
    # Which chains may seed contour rows at all - an explicit precedence,
    # never first-writer-wins (earthwork.family_verdicts has the rules):
    #  a. a family the LEGEND matched to a non-contour row (property line,
    #     swale, grade break...) never seeds - on C-04 those families
    #     seeded 4 junk rows, each with a stolen nearby spot elevation;
    #  b. near-white strokes never seed - they are mask ink, invisible on
    #     paper (C-04 sids 44-48 seeded 10 junk rows the same way).
    # Excluded chains also leave the label-matching pool entirely, so the
    # labels they used to steal reach the real contours (earthwork.py).
    from takeoff_engine.earthwork import (_chain_white, contour_coverage,
                                          contour_interval, family_verdicts,
                                          is_printed_how, split_rows_surface)
    verdicts = family_verdicts(res.get("styles") or [])
    excl = set()
    fam_excluded = []
    n_by_sid = {}
    for c in chains:
        n_by_sid[c.style_id] = n_by_sid.get(c.style_id, 0) + 1
    # Self-consistency gate on the legend evidence: a non-contour verdict
    # is trusted only when the SAME legend read also matched a contour
    # family somewhere on the sheet. On Stice's 600-acre grading plan the
    # legend matcher hung "PROPOSED LOT LINE" on the proposed CONTOUR
    # families themselves (1,733 chains, wiggly terraces by eye - see
    # out/verify/2026-08-30/stice-recipe) and matched NO contour row at
    # all; applying the exclusions there deleted 634 of the operator's
    # 1,487 verified rows. A legend read that cannot find the contours
    # has no standing to disqualify anything else.
    legend_saw_contours = any(v["role"] in ("existing", "proposed",
                                            "contour")
                              for v in verdicts.values())
    if legend_saw_contours:
        for sid, v in sorted(verdicts.items()):
            if v["role"] == "noncontour":
                fam_excluded.append({"style_id": sid, "label": v["label"],
                                     "reason": "legend: not a contour",
                                     "n_chains": n_by_sid.get(sid, 0)})
    noncontour_sids = {f["style_id"] for f in fam_excluded}
    n_white = 0
    for c in chains:
        if c.style_id in noncontour_sids:
            excl.add(int(c.chain_id))
        elif _chain_white(c):
            excl.add(int(c.chain_id))
            n_white += 1
    if n_white:
        fam_excluded.append({"style_id": None, "label": "near-white strokes",
                             "reason": "white ink (mask)",
                             "n_chains": n_white})
    # UTILITY LINES ARE NOT CONTOURS (Barn C3.0): a family whose layer or
    # name says water / sewer / storm / gas / electric / pipe never seeds
    # and never joins a fan, and the panel says so in the excluded list.
    from takeoff_engine.earthwork import is_utility_name as _is_util
    style_name = {int(s["style_id"]): (s.get("suggested_label") or s.get("layer") or "")
                  for s in (res.get("styles") or []) if s.get("style_id") is not None}
    n_util: dict = {}
    for c in chains:
        if int(c.chain_id) in excl:
            continue
        if _is_util(c.layer) or _is_util(style_name.get(c.style_id, "")):
            excl.add(int(c.chain_id))
            n_util[c.style_id] = n_util.get(c.style_id, 0) + 1
    for sid, n in sorted(n_util.items(), key=lambda t: (t[0] is None, t[0])):
        fam_excluded.append({"style_id": sid, "label": style_name.get(sid, ""),
                             "reason": "utility line, not a contour",
                             "n_chains": n})
    # contour labels printed inside line breaks are rotated + often
    # exploded to vectors: read them by deskewing each gap with the
    # line's own angle. OCR of a fixed raster is deterministic, so the
    # per-page cache reproduces on any seat (compute cache, not memory).
    # v2 suffix: the v1 cache was built with the old trigger (first-come
    # 400-patch cap, lone-gap threshold) and holds none of the mask-covered
    # or split-break labels; keeping the old filename would read that stale
    # result back forever. Orphaned v1 files are harmless.
    gcache = _job_dir(job_id) / f"p{page}_elevgaps2.json"
    rcache = gcache.with_suffix(".resume.json")
    from takeoff_engine.earthwork import GAP_READER as _GAP_READER

    def _read_gdiag():
        try:
            d = json.loads(gcache.with_suffix(".diag.json").read_text())
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _read_gcache():
        if not gcache.exists():
            return None
        # A READ AN OLDER READER CUT SHORT is read again. Before 2026-09-17
        # the pass paid a process launch per patch variant and visited
        # contours in no particular order, so a cap-stopped cache (Barn
        # C3.0: 426 of 1,901 places) says more about that reader than about
        # the sheet. So is one its 1,400-CALL budget stopped: that reader
        # recorded those as not stopped early (Barn C3.1: 1,400 calls,
        # 2,620 places, `stopped_early: false`). A complete one stands: the
        # new reader returns the same elevations (FINDINGS 38).
        d = _read_gdiag()
        if d.get("reader") != _GAP_READER and (
                d.get("stopped_early")
                or (d.get("ocr_calls") or 0) >= _OLD_CALL_BUDGET):
            return None
        try:
            return {int(k): tuple(v)
                    for k, v in json.loads(gcache.read_text()).items()}
        except Exception:
            return None

    gap_z = _read_gcache()
    if gap_z is None or more:
        # SINGLE FLIGHT (see _elevgap_lock). The second look is taken INSIDE
        # the lock: whoever waited here was waiting for exactly this file,
        # and re-reading it is the difference between one 217 s pass and N.
        with _elevgap_lock(job_id, page):
            gap_z = _read_gcache()
            resume = None
            if more and gap_z is not None and _read_gdiag().get("stopped_early"):
                # KEEP READING: from the stopped pass's place, with its
                # reads. Whoever held the lock before us may have carried
                # it on already - then the diag says so and this is a read.
                try:
                    resume = json.loads(rcache.read_text())
                except (OSError, ValueError):
                    resume = None
                if isinstance(resume, dict):
                    gap_z = None
                else:
                    resume = None
            if gap_z is None:
                import numpy as _np3

                from takeoff_engine.earthwork import gap_label_elevations
                # PROGRESS SIDECAR. The panel polls /earthwork/progress once
                # a second while its suggest is in flight; this is what it
                # reads. Phase first (render -> ocr -> done), the OCR pass's
                # own counts as they come. Written whole each time so a
                # reader never sees half a file.
                pfile = gcache.with_suffix(".progress.json")

                def _progress(d):
                    try:
                        pfile.write_text(json.dumps(
                            {"phase": "ocr", "at": time.time(), **d}))
                    except OSError:
                        pass
                _progress({"pos": 0, "planned": 0, "calls": 0, "reads": 0,
                           "seconds": 0.0, "final": False, "note": "render"})
                doc2 = pymupdf.open(job["path"])
                pix = doc2[page].get_pixmap(dpi=150)
                img = _np3.frombuffer(pix.samples, _np3.uint8).reshape(
                    pix.height, pix.width, pix.n)[:, :, :3].copy()
                doc2.close()
                # WALL CLOCK, not just a call cap. Barn C3.0 - a site plan
                # with no contours - spent the whole 1,400-call budget on
                # utility and property linework: more than ten minutes of
                # OCR that read nothing (the probe's 600 s timeout expired
                # first) while the panel sat on "finding elevations" - ten
                # minutes that were the box's load, not the sheet (87 s
                # unloaded; see OCR_BUDGET_S). The pass stops at the
                # TAKEOFF_OCR_BUDGET_S hard cap and SAYS so:
                # ocr_diag goes into the cache beside the values and out in
                # the reply as `elevation_ocr`, so a truncated read is shown
                # as truncated, never served as "this sheet has no labels".
                ocr_diag = {}
                # CONTOURS FIRST: the families this sheet calls contours
                # (legend row or CAD layer name) are read before anything
                # else, so a pass the clock stops has spent its time on them
                from takeoff_engine.earthwork import contour_priority
                pri = contour_priority(chains, res.get("styles") or [],
                                       exclude=excl)
                gap_z = gap_label_elevations(chains, img, 150.0 / 72.0,
                                             max_patches=OCR_CALLS,
                                             exclude=excl,
                                             time_budget_s=OCR_BUDGET_S,
                                             idle_budget_s=OCR_IDLE_S,
                                             diag=ocr_diag,
                                             progress=_progress,
                                             priority=pri,
                                             workers=OCR_WORKERS,
                                             resume=resume)
                # the place and reads a later "keep reading" carries on
                # from - kept beside the cache, not sent to the browser
                resume_state = ocr_diag.pop("resume", None)
                try:
                    gcache.write_text(json.dumps(
                        {str(k): list(v) for k, v in gap_z.items()}))
                    gcache.with_suffix(".diag.json").write_text(
                        json.dumps(ocr_diag))
                    if resume_state:
                        rcache.write_text(json.dumps(resume_state))
                    elif rcache.exists():
                        rcache.unlink()
                except OSError:
                    pass
                try:
                    pfile.unlink()
                except OSError:
                    pass
    # what the OCR pass did, whether it ran now or was read back from the
    # cache - a cached truncated read is still a truncated read
    try:
        ocr_diag = json.loads(gcache.with_suffix(".diag.json").read_text())
    except (OSError, ValueError):
        ocr_diag = {"cached": gap_z is not None}
    if isinstance(ocr_diag, dict) and ocr_diag.get("stopped_early"):
        # the panel offers "Keep reading" only when there is a place to
        # carry on from
        ocr_diag["resumable"] = rcache.exists()
    # ew_diag carries what the LADDER GATE did (earthwork._ladder_gate): on a
    # page with no glyph-backed row it is the only consensus filter that can
    # run, and what it drops has to be as visible as what it keeps.
    ew_diag = {}
    sug = suggest_elevations(chains, words, gap_z=gap_z, exclude=excl,
                             diag=ew_diag)
    ladder = ew_diag.get("ocr_ladder") or {"applied": False, "dropped": 0}
    by_id = {c.chain_id: c for c in chains}
    for r in sug:
        w = by_id[r["chain_id"]].width
        r["width"] = w
    # Spot grades ("FG: 15.96") are the elevations a detailed grading sheet
    # actually prints - Barn C3.4 has 45 of them against 2 labelled contours,
    # and the whole population used to be invisible. They are POINT
    # elevations, so they ride alongside the contour rows.
    from takeoff_engine.earthwork import read_spot_grades
    spot_datum_info = {}
    # legend_samples: the reader drops spot labels inside the merged legend
    # block (the legend's own sample rows - C-04 prints "ME 100.0"/"TW
    # 100.0"/... as its illustration) and discloses the count.
    spots = read_spot_grades(words, lines=lines,
                             datum_info=spot_datum_info,
                             legend_boxes=res.get("legend_samples") or [])

    # Two junk sources make the contour suggestions untrustworthy on sparse
    # sheets (Barn C3.4 offered 6 rows of which 4 were noise):
    #  - the sheet's own LEGEND text: "7900 EXISTING CONTOUR INTERVAL" reads
    #    as an elevation label sitting on the legend's sample bar. We already
    #    know where those bars are, so drop anything inside one.
    #  - stray 4-digit numbers, above all the DATE (2026). Judge by consensus
    #    like the spot grades do: keep the dominant hundreds band, and only
    #    when at least two candidates agree on it. A lone outlier is noise;
    #    if nothing agrees, nothing is dropped (better a noisy list than a
    #    silently emptied one).
    # Gap-OCR-minted rows must cohere with what the sheet's own TEXT
    # witnesses: the glyph-derived row values plus the resolved spot
    # grades. With >= 6 witnessed values, an OCR row further than 100 ft
    # from every one of them is a misread minting terrain that nothing on
    # the sheet corroborates - Barn C3.1 (272 spot grades around 7,7xx,
    # zero labeled contours) got three such rows at 424-444 read out of
    # dimension texts, and with no native rows at all the group-consensus
    # below cannot reject them. 100 ft matches the consensus gap; 6
    # matches the OCR anchor rule's threshold.
    wit = sorted({float(r["z"]) for r in sug
                  if r.get("how") in ("radius", "inline")}
                 | {float(g["z"]) for g in spots
                    if g.get("resolved") and g.get("z") is not None})
    n_before_filters = len(sug)
    if len(wit) >= 6:
        sug = [r for r in sug
               if r.get("how") != "gap-ocr"
               or min(abs(float(r["z"]) - a) for a in wit) <= 100.0]
    # ...inside the MERGED legend block, not just a row's own box: C-08's
    # "468" contour samples sat 11 pt past a short row box and on a row the
    # legend reader had missed, and both reached the panel as elevations
    # (earthwork.label_in_legend has the measurement).
    from takeoff_engine.earthwork import label_in_legend as _label_in_legend
    boxes = res.get("legend_samples") or []
    def _in_legend(r):
        return _label_in_legend(r.get("label_xy"), boxes)
    kept = [r for r in sug if not _in_legend(r)]
    # cluster on GAPS, not on hundreds: a site that spans 7690-7710 is one
    # surface, and a hundreds bucket would cut it in half
    if len(kept) > 1:
        order = sorted(kept, key=lambda r: float(r["z"]))
        groups, cur = [], [order[0]]
        for prev, r in zip(order, order[1:]):
            if float(r["z"]) - float(prev["z"]) > 100.0:
                groups.append(cur); cur = []
            cur.append(r)
        groups.append(cur)
        # rank groups by GLYPH-backed rows first, count second: OCR can
        # mint mutually-supporting misreads (Barn C3.1: three dimension
        # texts read as 424-444 against the sheet's one real 7,7xx row),
        # and on pure row count that junk band outvotes the surface the
        # sheet's own text witnesses. A native token cannot be minted.
        top = max(groups, key=lambda g: (
            sum(1 for r in g if r.get("how") in ("radius", "inline")),
            len(g)))
        if len(top) >= 2 or (len(kept) > len(top)
                             and any(r.get("how") in ("radius", "inline")
                                     for r in top)):
            kept = top
    # The ladder gate ran INSIDE suggest_elevations, so its drops are not in
    # n_before_filters; add them or suggestions_dropped would report 0 on
    # exactly the page where the only filter that fired was that one.
    dropped = n_before_filters - len(kept) + int(ladder.get("dropped") or 0)
    sug = kept
    # surface split with the rule disclosed per row (surface + split_by):
    # legend verdict > color screening > width median - the UI keeps its
    # own width-median default until it adopts these fields, so they are
    # advisory, but the row set above (exclusions, label sources) is not
    chain_info = {c.chain_id: {"style_id": c.style_id, "color": c.color,
                               "width": c.width} for c in chains}
    split_counts = split_rows_surface(sug, chain_info, verdicts)
    # INTERVAL INFILL: the unlabelled contours between two labelled ones.
    # A grading sheet labels a minority of its contours and expects the
    # reader to COUNT the rest; the engine has been interpolating blindly
    # across them. Each accepted fan is worth real volume - measured on
    # C-04's existing surface, labelled-only vs +infill on identical cells,
    # cut 1,781.0 -> 2,072.3 CY.
    #
    # These arrive with surface=None (skip) and inferred=True: unlike a
    # read label they have NO printed evidence, and against the offline
    # analyst's answer key this pass scored 24 of 41 rows vouched (16 of
    # his 20 transect counts, 8 more of his 22 welds, zero disagreements)
    # with 17 he did not assign, 3 of which he had explicitly refused. A
    # 59%-vouched row is a suggestion to confirm, not an elevation to bill,
    # so nothing here moves a volume until the operator clicks EXIST/PROP.
    from takeoff_engine.earthwork import _is_plan_chain, infill_intervals
    by_cid = {c.chain_id: c for c in chains}
    seeded = {int(r["chain_id"]) for r in sug}
    infill_rows, infill_diag = [], {}
    for side in ("existing", "proposed"):
        assigned = [{"chain_id": int(r["chain_id"]),
                     "pts": by_cid[r["chain_id"]].pts, "z": r["z"],
                     "family": "side:" + side}
                    for r in sug
                    if r.get("surface") == side and r.get("z") is not None
                    and r["chain_id"] in by_cid]
        # SIBLINGS BY SIDE, NOT BY STYLE (Barn C3.0, 2026-09-16): the index
        # contours (C-TOPO, labelled) and the 1 ft intermediates (the legend's
        # PROPOSED CONTOUR families) are different linetypes by drafting
        # convention, so a same-style rule left all 161 intermediates
        # outside every fan. A chain joins this side's fan when the legend
        # calls its family this side's contour (or a contour with no side),
        # or when it shares a style with one of the side's anchors; any
        # other family keeps its own key and never matches.
        anchor_sids = {by_cid[r["chain_id"]].style_id for r in sug
                       if r.get("surface") == side and r.get("z") is not None
                       and r["chain_id"] in by_cid}

        def _fam_key(c, _side=side, _anchor_sids=anchor_sids):
            role = (verdicts.get(c.style_id) or {}).get("role")
            if role in (_side, "contour") or c.style_id in _anchor_sids:
                return "side:" + _side
            return c.style_id
        cands = [{"chain_id": int(c.chain_id), "pts": c.pts,
                  "family": _fam_key(c)}
                 for c in chains
                 if int(c.chain_id) not in seeded
                 and int(c.chain_id) not in excl
                 and _is_plan_chain(c)]
        # WHAT BACKS EACH ANCHOR. An assigned row's own elevation may be
        # `inherited` - no label printed on that line at all - and calling
        # it "labelled" in the suggestion's sentence is a false statement
        # about the sheet (13 of C-04's 35 infilled rows had BOTH ends
        # inherited). The reader already knows; hand it over.
        anchor_how = {int(r0["chain_id"]): r0.get("how")
                      for r0 in sug if r0.get("chain_id") is not None}
        r = infill_intervals(assigned, cands, anchor_how=anchor_how)
        infill_diag[side] = {"interval": r["interval"],
                             "families": r.get("families"),
                             "candidates_in_family":
                                 r.get("n_candidates_in_family", 0),
                             "transects": r["n_transects"],
                             "refused": r["refused"],
                             "n_rows": len(r["rows"])}
        for row in r["rows"]:
            cid = row["chain_id"]
            if cid in seeded:
                continue
            seeded.add(cid)
            ch = by_cid.get(cid)
            row.update({"surface": None, "split_by": "interval-infill",
                        "for_surface": side,
                        "width": (ch.width if ch else None), "dist": None})
            infill_rows.append(row)
    sug = sug + infill_rows
    # ---- WHAT THIS SHEET'S CONTOUR LINEWORK ADDS UP TO --------------------
    #
    # Every fact below was already computed above and thrown away: the rows
    # know their `how`, the exclusions know their chain counts, and nothing
    # stated a DENOMINATOR. So a sheet whose reader elevated 150 of 368
    # contour lines was byte-identical on screen to one that elevated all of
    # them (measured 2026-09-02 by grep on 512f474: `spot_z_missing` exists
    # for spot grades, no contour denominator exists anywhere). The
    # arithmetic is takeoff_engine.earthwork.contour_coverage - pure, and
    # frozen in tests/test_ew_coverage.py against a recorded row set.
    coverage = contour_coverage(res.get("chains") or [], sug,
                                verdicts=verdicts, excluded=excl)
    # THE SHEET'S OWN CONTOUR INTERVAL, read off the labels the engine
    # believes are printed (inline / radius / gap-ocr - never an inferred
    # value, which was itself produced by assuming an interval). The review
    # surface uses it to rank rows: on Oncor C-04 the interval is 1.0 ft and
    # 18 of the 20 `radius` rows carry a FRACTIONAL elevation (551.2 BW,
    # 557.4 TW, 583.5 ME...) - printed numbers that cannot be contour
    # elevations at this sheet's own step, and 10 of them carry a
    # legend-keyed spot tag saying as much. Shipped as evidence, never as a
    # correction: nothing here changes a z or a surface.
    coverage["interval"] = contour_interval(
        [float(r["z"]) for r in sug
         if is_printed_how(r.get("how")) and r.get("z") is not None])
    return {"suggestions": sug,
            "contour_coverage": coverage,
            "suggestions_dropped": dropped,
            # WHY they were dropped, not just how many. suggestions_dropped
            # existed since the first version of this route and no screen
            # ever rendered it, so a page that silently lost every row
            # looked exactly like a page that had none. ocr_ladder carries
            # the verdict (interval, repeats, share, bands) so the panel can
            # print the sentence and the operator can disagree with it.
            "ocr_ladder": ladder,
            # the label-reading pass itself: patches planned / read, seconds,
            # and whether the wall clock cut it short (see TAKEOFF_OCR_BUDGET_S)
            "elevation_ocr": ocr_diag,
            # numbers the sheet tags as points on what is being built
            # (earthwork.is_built_spot_tag): they never become a line's
            # height, and the panel says how many and which tags
            "spot_tag_refused": ew_diag.get("spot_tag_refused"),
            "families_excluded": fam_excluded,
            "split_counts": split_counts,
            # every inferred row is counted here as well as flagged on
            # itself, so a caller that ignores the flag still cannot show
            # the row set without the number being available
            "interval_infill": infill_diag,
            "interval_infill_rows": len(infill_rows),
            "spot_grades": spots,
            "spot_datum": spot_datum_info,
            # disclosed heuristics: how many legend sample rows were dropped
            # and how many spots came back with no z at all ("N legend
            # samples excluded" / "N spots unresolved" in the UI). Additive
            # keys; the per-spot rows still carry resolved/why themselves.
            "spot_legend_excluded": spot_datum_info.get("legend_excluded", 0),
            "spot_z_missing": spot_datum_info.get("z_missing", 0),
            "spot_datum_needed": bool(spots) and not any(
                g.get("resolved") for g in spots),
            "scale": res.get("scale"),
            "note": "confirm/edit in the UI; split into existing vs "
                    "proposed before calculating"}


def _layer_stack(rows: list) -> tuple[list, float]:
    """[{name, thickness_in}] -> (clean rows, total thickness ft).

    A removal row may also carry the material-balance disposition
    (reuse_pct / respread_pct / destination). It rides along untouched for
    material_balance() to read; it never affects the surface offset.
    """
    out, tot_in = [], 0.0
    for r in rows or []:
        try:
            t = float(r.get("thickness_in", 0) or 0)
        except (TypeError, ValueError):
            continue
        if t <= 0:
            continue
        out.append({"name": str(r.get("name") or "layer").strip() or "layer",
                    "thickness_in": t,
                    "reuse_pct": r.get("reuse_pct"),
                    "respread_pct": r.get("respread_pct"),
                    "destination": r.get("destination")})
        tot_in += t
    return out, tot_in / 12.0


def _ew_refusal_detail(result: dict) -> dict:
    """The structured 400 body for a cut_fill refusal.

    THE REFUSAL CARRIES ITS EVIDENCE. A bare sentence was all the panel ever
    got, so an operator told "each surface needs at least one labeled
    contour" could not see WHICH surface or what it held. The detail is an
    object: `message` is the same human sentence (a caller that only reads
    that is unchanged), `code` names the guard, `surface` is the offending
    side's counts, and `basis` is BOTH sides so the panel can show the
    comparison. See takeoff_engine.earthwork._floor_refusal.

    It is a FUNCTION so that /earthwork and /earthwork/map cannot answer the
    same refusal in two different shapes - the map route reached its own
    refusal by a different door and used to fall off the end of it (below).
    """
    detail = {"message": str(result["error"]),
              "code": result.get("error_code") or "earthwork_refused"}
    if result.get("surface"):
        detail["surface"] = result["surface"]
    if result.get("basis"):
        detail["basis"] = result["basis"]
    return detail


def _earthwork_run(job_id: str, page: int, body: EarthworkBody,
                   return_grid: bool = False) -> dict:
    """Shared by the volumes endpoint and the map endpoint: build both
    surfaces from the stored chains, lower each by its layer stack, and
    difference them. Adds the per-layer volumes and the offsets used."""
    import numpy as _np

    from takeoff_engine.earthwork import (Surface, boundary_metrics,
                                          clamp_pct, cut_fill,
                                          material_balance)

    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    # _page_scale, NOT res["scale"]. The reviewer can type a scale or pick a
    # different printed caption, and until 2026-08-23 these two volume routes
    # read the ENGINE's number straight out of the cached result and ignored
    # them both. Measured on Barn Civil engine page 2 = printed sheet 3 (C3.0):
    # type 1" = 60', watch every length on screen double, press Calculate, and
    # the CY came back computed at the engine's 30 ft/in - then priced,
    # exported and printed. Screen and deliverable disagreeing with nothing
    # saying so is the harm this whole sidecar exists to prevent. It also made
    # these two the only places left that answer "calibrate first" on a page
    # the operator HAS calibrated by hand.
    det = _page_scale(job_id, page, res)
    if not det:
        raise HTTPException(400, "no scale on this page - type one into the "
                                 "scale box in the sheet header first; volumes "
                                 "need real-world units")
    ft_per_pt = det["ft_per_paper_inch"] / 72.0
    pts_by_id = {c["chain_id"]: _np.asarray(c["points"], float)
                 for c in res["chains"]}

    def build(name, rows, points=()):
        surf = Surface(name)
        for r in rows:
            cid = int(r["chain_id"])
            if cid in pts_by_id:
                surf.contours.append((pts_by_id[cid], float(r["z"])))
        for q in points:
            try:
                surf.points.append((float(q["x"]), float(q["y"]), float(q["z"])))
            except (KeyError, TypeError, ValueError):
                continue
        return surf
    # the ring's own numbers, whatever the boundary is used for below
    bmet = (boundary_metrics(body.boundary, ft_per_pt)
            if body.boundary and len(body.boundary) >= 3 else None)
    ex_layers, ex_off = _layer_stack(body.existing_layers)
    pr_layers, pr_off = _layer_stack(body.proposed_layers)
    ex_surf = build("existing", body.existing, body.existing_points)
    pr_surf = build("proposed", body.proposed, body.proposed_points)
    result = cut_fill(ex_surf, pr_surf,
                      ft_per_pt, cell_ft=body.cell_ft,
                      existing_offset_ft=ex_off, proposed_offset_ft=pr_off,
                      return_grid=return_grid, boundary=body.boundary,
                      surface_model=body.surface_model)
    if "error" in result:
        # A MAP IS A PICTURE OF THE GRID, so there is no layers-only answer
        # for it. Until this fix /earthwork/map went on through the unweld
        # below, got back the layers_only dict, and its caller did
        # result.pop("grid") on a dict with no "grid" key - KeyError, HTTP
        # 500, body "Internal Server Error" in text/plain. Measured 2026-09-02
        # on dem_hurricane_bench_a_pad_2ft_outline p0: existing
        # [{205, 324.0}], proposed [{214, 826.0}], a 6-in topsoil strip and
        # strip_area_sf 50000 -> 500 on this branch, 200 image/png on
        # 4010783, which is a NEW crash on a body the base served. The floor
        # is right to refuse the volume; the map must say so in the floor's
        # own words instead of crashing, and the panel needs that detail to
        # tell the operator which surface was short.
        if return_grid:
            raise HTTPException(400, _ew_refusal_detail(result))
        # UNWELD (audit item 10): stripping is pure area x depth and owes
        # nothing to a TIN. A clearing / erosion-control sheet has no
        # proposed contours, so cut_fill legitimately fails - but welding
        # the layer slabs to that failure returned 0 CY for an item family
        # worth $88,815 on Estimate #853. If the operator gave an area to
        # strip over (a typed strip area, or a limit-of-work polygon whose
        # shoelace area stands in for it), the layers still compute; the
        # cut/fill failure is REPORTED, never papered over with zeros -
        # cut_yd3/fill_yd3 stay absent so no consumer can print a zero.
        area0 = None
        if body.strip_area_sf:
            area0 = float(body.strip_area_sf)
        elif body.boundary and len(body.boundary) >= 3:
            a = 0.0
            bpts = body.boundary
            for i in range(len(bpts)):
                x0, y0 = bpts[i - 1][0], bpts[i - 1][1]
                x1, y1 = bpts[i][0], bpts[i][1]
                a += (x0 + x1) * (y0 - y1)
            area0 = abs(a / 2.0) * ft_per_pt * ft_per_pt
        if not ((ex_layers or pr_layers) and area0):
            raise HTTPException(400, _ew_refusal_detail(result))
        result = {"layers_only": True,
                  "cut_fill_error": str(result["error"]),
                  "cut_fill_error_code": result.get("error_code"),
                  "surface_basis": result.get("basis"),
                  "area_source": ("typed strip area" if body.strip_area_sf
                                  else "limit-of-work outline"),
                  "area_sf": None, "net_yd3": None}
    result["ft_per_pt"] = round(ft_per_pt, 5)
    result["existing_offset_ft"] = round(ex_off, 4)
    result["proposed_offset_ft"] = round(pr_off, 4)
    # layer volumes: a slab of the strip area at the layer's thickness.
    # Default area is the TIN overlap (the region the cut/fill itself
    # covers); strip_area_sf overrides it with e.g. the clear & grub area.
    if result.get("layers_only"):
        area = area0
    elif body.strip_area_sf:
        area = float(body.strip_area_sf)
        result["area_source"] = "typed strip area"
    elif body.boundary_is_limit and bmet:
        # the limit of work IS the disturbed area: strip and clear over all
        # of it, not just where the two surfaces happen to overlap
        area = float(bmet["area_sf"])
        result["area_source"] = "limit-of-work outline"
    else:
        area = float(result["area_sf"])
        result["area_source"] = "surface overlap"
    result["layer_area_sf"] = round(area, 0)
    if bmet:
        result["boundary"] = dict(bmet, is_limit=bool(body.boundary_is_limit))
    layers = []
    for side, rows in (("existing", ex_layers), ("proposed", pr_layers)):
        for r in rows:
            row = {"name": r["name"], "side": side,
                   "thickness_in": r["thickness_in"],
                   "volume_yd3": round(area * r["thickness_in"] / 12.0 / 27.0, 1)}
            if side == "existing":
                # only a REMOVAL has anywhere to go. A proposed layer is
                # purchased material sitting above subgrade, not site dirt.
                row["reuse_pct"] = clamp_pct(r.get("reuse_pct"), 0.0)
                row["respread_pct"] = clamp_pct(r.get("respread_pct"), 0.0)
                row["destination"] = str(r.get("destination") or "on site")[:40]
            layers.append(row)
    result["layers"] = layers
    # Where the dirt goes: export / reuse / import (F87 B7, Beaumont). The
    # two areas differ on purpose and every row says which one it used -
    # removal layers are stripped over the disturbed area (layer_area_sf),
    # cut and fill exist only where the two TINs overlap (area_sf). #853
    # bids it the same way: Strip Topsoil over the Clear & Grub area,
    # Site Cut / Site Fill over the graded region.
    result["balance"] = material_balance(
        result.get("cut_yd3") or 0.0, result.get("fill_yd3") or 0.0, layers,
        cut_reuse_pct=body.cut_reuse_pct,
        export_factor=body.export_factor,
        import_factor=body.import_factor,
        cut_area_sf=result.get("area_sf") or area, layer_area_sf=area,
        place_depth_in=body.place_depth_in, place_less_sf=body.place_less_sf)
    # The scale this volume was computed at. Since 2026-08-23 the route
    # honours a scale the reviewer typed, which means the answer can now
    # depend on a number they are free to change afterwards - and a cubic
    # yard figure that silently belongs to a superseded scale is exactly
    # the confident wrong number this project ranks worst. Reporting it is
    # what lets the panel notice.
    result["ft_per_paper_inch"] = det["ft_per_paper_inch"]
    # WHAT BACKS THIS NUMBER (CUTFILL-ACCURACY-SPEC.md stage 3). The volume
    # kernel is exact on analytic truth and grid-insensitive, so a wrong
    # answer is a wrong INPUT - and the estimator cannot see inputs. These
    # are checks on them: the surface against the elevations the sheet
    # prints as points, the same rows through the other surface model and a
    # finer grid, and whether the region is the estimator's own line or
    # just where the two surfaces happen to overlap. Additive: nothing
    # here changes the volume, and a failure to compute it costs the
    # checks, never the answer.
    if result.get("cut_yd3") is not None and not return_grid:
        try:
            from takeoff_engine.earthwork import volume_proof
            result["proof"] = volume_proof(
                ex_surf, pr_surf, result,
                _page_spot_grades(job_id, page), ft_per_pt,
                cell_ft=float(result.get("cell_ft") or body.cell_ft),
                boundary=body.boundary,
                existing_offset_ft=ex_off, proposed_offset_ft=pr_off)
        except Exception as e:                                  # noqa: BLE001
            result["proof"] = {"error": f"{type(e).__name__}: {e}"[:200]}
    return result


@app.post("/api/job/{job_id}/page/{page}/earthwork")
def earthwork_calc(job_id: str, page: int, body: EarthworkBody):
    """Cut & fill volumes between two labeled contour surfaces, after
    lowering each by its layer stack (see EarthworkBody)."""
    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    result = _earthwork_run(job_id, page, body)
    _ledger_earthwork(job_id, page, body, result)
    return JSONResponse(result)


@app.post("/api/job/{job_id}/page/{page}/earthwork/map")
def earthwork_map(job_id: str, page: int, body: EarthworkBody):
    """Cut/fill map for the same request body as /earthwork: an RGBA PNG
    of the dz grid (cut blue, fill yellow-orange, depth -> opacity,
    undefined cells clear). Registration for the caller rides in headers:
      X-Map-Bbox    x0,y0,x1,y1 in page pt (the PNG stretches to this box)
      X-Map-Cell-Pt grid cell size in pt
      X-Map-Ref-Dz  |dz| in ft at which the colour reaches full opacity
    The PNG + its meta are cached as the LAST map for (job, page); GET the
    same path to re-read them without recomputing."""
    from takeoff_engine.earthwork import map_labels, render_cut_fill_map

    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    result = _earthwork_run(job_id, page, body, return_grid=True)
    grid = result.pop("grid")
    png, meta = render_cut_fill_map(grid)
    # spot depths, one per block: the crew's map has the numbers on it
    meta["labels"] = map_labels(grid, result.get("cell_ft") or 2.0,
                                body.label_spacing_ft or 30.0)
    meta["label_spacing_ft"] = body.label_spacing_ft or 30.0
    meta.update({k: result[k] for k in
                 ("cut_yd3", "fill_yd3", "net_yd3", "area_sf", "cell_ft",
                  "existing_offset_ft", "proposed_offset_ft")})
    d = _job_dir(job_id)
    try:
        (d / f"p{page}_ewmap.png").write_bytes(png)
        (d / f"p{page}_ewmap.json").write_text(json.dumps(meta))
    except OSError:
        pass
    return Response(png, media_type="image/png",
                    headers=_ewmap_headers(meta))


def _ewmap_headers(meta: dict) -> dict:
    return {
        "X-Map-Bbox": ",".join(f"{v:.3f}" for v in meta["bbox_pt"]),
        "X-Map-Cell-Pt": f"{meta['cell_pt']:.5f}",
        "X-Map-Ref-Dz": f"{meta['ref_dz_ft']:.3f}",
        "Cache-Control": "no-store",
    }


@app.get("/api/job/{job_id}/page/{page}/earthwork/map")
def earthwork_map_cached(job_id: str, page: int, meta: int = 0):
    """The last map rendered for (job, page): PNG by default, or its
    JSON meta (bbox, cell, ref depth, volumes) with ?meta=1."""
    d = _job_dir(job_id)
    pj, pp = d / f"p{page}_ewmap.json", d / f"p{page}_ewmap.png"
    if not (pj.exists() and pp.exists()):
        raise HTTPException(404, "no cut/fill map yet - POST this path first")
    m = json.loads(pj.read_text())
    if meta:
        return JSONResponse(m)
    return Response(pp.read_bytes(), media_type="image/png",
                    headers=_ewmap_headers(m))


class GradingZoneBody(BaseModel):
    """One grading zone: a polygon plus a DECLARED design.

    The zone workflow exists because a sheet often does not draw a full
    proposed surface (measured on the Stice case, EXCAVATION-PLAN.md
    section 25): the estimator picks a region and declares the design -
    pad / plane / balance / strip - and the engine computes exact volumes
    of that declared design against the detected existing ground.
    """
    # the zone outline, [[x, y], ...] in page points
    polygon: list[list[float]]
    # {mode:"pad", elev_ft} | {mode:"plane", elev_ft, slope_pct,
    #  azimuth_deg, anchor:[x,y]} | {mode:"balance"} | {mode:"strip_only"}
    design: dict = {}
    # topsoil strip in inches over the zone; 0 = none. Lowers the existing
    # surface before differencing (cut_fill's existing_offset convention)
    # and reports its own strip_yd3 = area x depth.
    strip_in: float = 0.0
    # base course(s) ON TOP of the earthwork, [{name, thickness_in}], the
    # same rows the whole-sheet /earthwork route takes. The declared
    # elevation is the FINISHED grade: a pad or plane is lowered by the
    # stack's thickness before differencing (the dig goes to the subgrade),
    # a balance solves the subgrade and reports the finished grade above
    # it, and each layer comes back as a "place" volume = zone area x
    # thickness (the base covers the whole zone whatever the TIN reaches).
    # Beaumont, 2026-09-15: a base under a slab or a road is its own line.
    proposed_layers: list[dict] = []
    # existing ground, same shapes the /earthwork route takes:
    # rows [{chain_id, z}] and spot points [{x, y, z}]
    existing_rows: list[dict] = []
    existing_points: list[dict] = []
    proposed_rows: list[dict] = []          # mode "proposed": the sheet's own design surface
    proposed_points: list[dict] = []
    cell_ft: float | None = None


# Words that name a STRUCTURE on a civil sheet. A grading zone is one
# building's volume exhibit (Beaumont, call 2026-09-15), so only text with
# one of these may name it. Measured on Barn C3.0 (2026-09-16): the tallest
# text inside a zone drawn round the barn was the water-line label "WL", a
# zone over it "SS", and the patio note "SLAB ONGRADE / STONE PATIO; RE. TO
# / STRUCTURAL & ARCH" is taller than either - none of them is a name.
_ZONE_NAME_WORDS = frozenset("""
RESIDENCE HOUSE HOME BARN GARAGE SHOP BUILDING BLDG CABIN STUDIO GUEST SHED
PAVILION CASITA CLUBHOUSE OFFICE WAREHOUSE STORE UNIT ARENA STABLE STABLES
SUBSTATION HANGAR LODGE ADU DWELLING APARTMENT APARTMENTS CONDO CONDOS DUPLEX
TOWNHOME TOWNHOMES SCHOOL CHURCH CLINIC HOTEL MOTEL RESTAURANT RETAIL BANK
STATION GYM PLANT TOWER SILO SHELTER KENNEL GREENHOUSE WORKSHOP CARPORT
ADDITION DORM DORMITORY LIBRARY GATEHOUSE BOATHOUSE BUNKHOUSE
""".split())
_SPOT_CACHE: dict = {}


def _page_spot_grades(job_id: str, page: int) -> list:
    """The spot elevations this page PRINTS, cached per (file, mtime, page).

    The suggest route reads these for the panel's spot list; the calc route
    needs them for a different reason - to check the surface it just built
    against the sheet's own numbers (earthwork.volume_proof). Cached
    because Calculate is pressed repeatedly and the page text costs ~1 s.
    """
    job = JOBS.get(job_id)
    if not job:
        return []
    path = job.get("path")
    try:
        key = (path, os.path.getmtime(path), int(page))
    except (OSError, TypeError, ValueError):
        return []
    hit = _SPOT_CACHE.get(key)
    if hit is not None:
        return hit
    from takeoff_engine.earthwork import read_spot_grades
    from takeoff_engine.extract import page_lines as _pl
    from takeoff_engine.extract import page_words as _pw
    try:
        doc = pymupdf.open(path)
        try:
            words, lines = _pw(doc[page]), _pl(doc[page])
        finally:
            doc.close()
        spots = read_spot_grades(words, lines=lines, datum_info={})
    except Exception:
        return []
    if len(_SPOT_CACHE) > 32:
        _SPOT_CACHE.clear()
    _SPOT_CACHE[key] = spots
    return spots


_ZONE_TEXT_CACHE: dict = {}


def _zone_page_text(job_id: str, page: int) -> list:
    """[(x0, y0, x1, y1, text)] of the page's text lines, cached per (file,
    mtime, page): the zone modal's live preview asks on every keystroke."""
    job = JOBS.get(job_id)
    if not job:
        return []
    path = job.get("path")
    try:
        key = (path, os.path.getmtime(path), int(page))
    except (OSError, TypeError, ValueError):
        return []
    hit = _ZONE_TEXT_CACHE.get(key)
    if hit is not None:
        return hit
    from takeoff_engine.extract import page_lines as _page_lines
    try:
        doc = pymupdf.open(path)
        try:
            lines = [(float(a), float(b), float(c), float(d), " ".join(str(t).split()))
                     for a, b, c, d, t in _page_lines(doc[page])]
        finally:
            doc.close()
    except Exception:
        return []
    if len(_ZONE_TEXT_CACHE) > 32:
        _ZONE_TEXT_CACHE.clear()
    _ZONE_TEXT_CACHE[key] = lines
    return lines


def _zone_text_name(job_id: str, page: int, polygon) -> str | None:
    """The structure's name printed inside the polygon - "BARN", "GUIDE
    RESIDENCE" - offered as the zone's name; None when nothing inside it
    names a structure, so the zone keeps "Zone N" (an abstention, never a
    guess). A candidate line is plain words (letters, digits, spaces,
    hyphen, apostrophe - no colon, semicolon, period or ampersand, which
    mark notes, elevations and sheet references), stacked lines of one
    size join into one name ("GUIDE" over "RESIDENCE"), and the block must
    carry a word from _ZONE_NAME_WORDS. The tallest such block wins. The
    UI takes the suggestion only for a zone just drawn, still "Zone N",
    not yet added to the takeoff."""
    import numpy as _np
    from takeoff_engine.earthwork import _points_in_poly
    try:
        poly = _np.asarray(polygon, float)
        if poly.ndim != 2 or poly.shape[0] < 3:
            return None
    except (TypeError, ValueError):
        return None
    cands = []
    for x0, y0, x1, y1, t in _zone_page_text(job_id, page):
        if not t or len(t) > 30 or (y1 - y0) < 4.0:
            continue
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9' \-]*", t) or not re.search(r"[A-Za-z]{3,}", t):
            continue
        cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        try:
            inside = bool(_points_in_poly(_np.array([[cx]]), _np.array([[cy]]), poly)[0, 0])
        except Exception:
            inside = False
        if inside:
            cands.append((y0, x0, x1, y1, t))
    cands.sort()
    blocks: list = []
    for y0, x0, x1, y1, t in cands:
        h = y1 - y0
        if blocks and len(blocks[-1]) < 3:
            py0, px0, px1, py1, _pt = blocks[-1][-1]
            ph = py1 - py0
            if (abs(h - ph) <= 0.3 * max(h, ph)
                    and (y0 - py1) < 0.8 * max(h, ph)
                    and min(x1, px1) - max(x0, px0) > 0.3 * min(x1 - x0, px1 - px0)):
                blocks[-1].append((y0, x0, x1, y1, t))
                continue
        blocks.append([(y0, x0, x1, y1, t)])
    best = None
    for b in blocks:
        text = " ".join(l[4] for l in b)
        if len(text) > 40 or not (set(re.findall(r"[A-Z]+", text.upper())) & _ZONE_NAME_WORDS):
            continue
        h = max(l[3] - l[0] for l in b)
        if best is None or h > best[0]:
            best = (h, text)
    return best[1] if best else None


@app.post("/api/job/{job_id}/page/{page}/earthwork/zone")
def earthwork_zone(job_id: str, page: int, body: GradingZoneBody):
    """Volumes of a declared design surface vs the detected existing
    ground inside one polygon. Same honesty rules as _earthwork_run:
    the scale comes from _page_scale (a typed scale is honoured), every
    refusal carries its reason, and `coverage` says how much of the zone
    the existing TIN actually reaches - never silently less."""
    import base64

    import numpy as _np

    from takeoff_engine.earthwork import (Surface, render_cut_fill_map,
                                          zone_cut_fill)

    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    # _page_scale, NOT res["scale"] - same rule as _earthwork_run: the
    # reviewer's typed scale governs, or these volumes silently belong to
    # a scale nobody is using any more.
    det = _page_scale(job_id, page, res)
    if not det:
        raise HTTPException(400, "no scale on this page - type one into the "
                                 "scale box in the sheet header first; "
                                 "volumes need real-world units")
    ft_per_pt = det["ft_per_paper_inch"] / 72.0
    pts_by_id = {c["chain_id"]: _np.asarray(c["points"], float)
                 for c in res["chains"]}
    surf = Surface("existing")
    for r in body.existing_rows:
        try:
            cid, z = int(r["chain_id"]), float(r["z"])
        except (KeyError, TypeError, ValueError):
            continue
        if cid in pts_by_id:
            surf.contours.append((pts_by_id[cid], z))
    for q in body.existing_points:
        try:
            surf.points.append((float(q["x"]), float(q["y"]), float(q["z"])))
        except (KeyError, TypeError, ValueError):
            continue
    # the base stack: the typed elevation is finished grade, the earthwork
    # stops at the subgrade below it
    prop = None
    if str((body.design or {}).get("mode") or "") == "proposed":
        prop = Surface("proposed")
        for r in body.proposed_rows:
            try:
                cid, z = int(r["chain_id"]), float(r["z"])
            except (KeyError, TypeError, ValueError):
                continue
            if cid in pts_by_id:
                prop.contours.append((pts_by_id[cid], z))
        for q in body.proposed_points:
            try:
                prop.points.append((float(q["x"]), float(q["y"]), float(q["z"])))
            except (KeyError, TypeError, ValueError):
                continue
    pr_layers, pr_off = _layer_stack(body.proposed_layers)
    design = dict(body.design or {})
    mode = design.get("mode")
    if mode == "proposed" and pr_off > 0:
        design["subgrade_offset_ft"] = pr_off
    if pr_off > 0 and mode in ("pad", "plane"):
        try:
            design["elev_ft"] = float(design["elev_ft"]) - pr_off
        except (KeyError, TypeError, ValueError):
            pass
    out = zone_cut_fill(surf, body.polygon, ft_per_pt, design,
                        cell_ft=body.cell_ft or 2.0,
                        strip_in=body.strip_in, proposed=prop)
    if "error" not in out:
        # AUTO-LABEL: the biggest text inside the polygon, offered as the
        # zone's name (the UI takes it only while the zone is still "Zone N")
        out["suggested_name"] = _zone_text_name(job_id, page, body.polygon)
    if "error" in out:
        # The SAME shape as /earthwork and /earthwork/map. This route was the
        # third door to the volume floor and the only one still answering a
        # bare string: measured 2026-09-02, an existing surface of one contour
        # at 324 ft against a declared pad published 417,664 CY here while
        # POST /earthwork on the same rows answered 400 surface_no_relief.
        raise HTTPException(400, _ew_refusal_detail(out))
    out["layers"] = []
    out["subgrade_offset_ft"] = 0.0
    if pr_off > 0 and mode != "strip_only":
        if mode == "balance" and out.get("balance_elev_ft") is not None:
            # the bisection solved the SUBGRADE; say both, report the
            # finished grade as the elevation (what the reviewer types
            # everywhere else)
            out["balance_subgrade_ft"] = out["balance_elev_ft"]
            out["balance_elev_ft"] = round(out["balance_elev_ft"] + pr_off, 2)
            out["balance_delta_ft"] = round(out["balance_elev_ft"] - out["existing_mean_ft"], 2)
        area = float(out.get("zone_area_sf") or out.get("area_sf") or 0.0)
        out["layers"] = [{"name": lay["name"], "thickness_in": lay["thickness_in"],
                          "volume_yd3": round(area * lay["thickness_in"] / 12.0 / 27.0, 1)}
                         for lay in pr_layers]
        out["subgrade_offset_ft"] = round(pr_off, 3)
    # dz map clipped to the zone's grid, same palette as the whole-sheet
    # cut/fill map (cut blue / fill yellow-orange - the client's own
    # convention), base64 so one JSON reply carries numbers + picture.
    png, meta = render_cut_fill_map(out.pop("grid"))
    out["map_png_base64"] = base64.b64encode(png).decode("ascii")
    out["bbox"] = meta["bbox_pt"]
    out["ref_dz"] = meta["ref_dz_ft"]
    out["ft_per_paper_inch"] = det["ft_per_paper_inch"]
    return JSONResponse(out)


def _doc_footing_schedule(job_id: str, path: str) -> dict:
    """The footing schedule for this DOCUMENT, cached per job.

    A structural set prints its schedule once, on the notes sheet (Barn
    S0-0.4, Ellis's notes page), while the marks that need it sit on every
    foundation plan - so this looks at the pages whose text says FOOTING
    SCHEDULE first, then the rest, and keeps the first row it finds for
    each mark with the page it came from. Cached because it is the same
    answer for every page of the job.
    """
    from takeoff_engine.footing_callouts import read_footing_schedule
    cache = _job_dir(job_id) / "footing_schedule.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    out = {"rows": {}, "pages": [], "scanned": 0}
    try:
        doc = pymupdf.open(path)
    except Exception:
        return out
    try:
        n = doc.page_count
        heads, rest = [], []
        for i in range(min(n, 80)):
            try:
                t = doc[i].get_text().upper()
            except Exception:
                t = ""
            (heads if "FOOTING SCHEDULE" in t else rest).append(i)
        for i in heads + rest[:40]:
            # the header pages are all read; the rest only until a
            # schedule turns up somewhere (a set without the words FOOTING
            # SCHEDULE over its table)
            if i in rest and out["rows"]:
                break
            out["scanned"] += 1
            try:
                words = page_words(doc[i])
            except Exception:
                continue
            s = read_footing_schedule(words)
            if not s["found"]:
                continue
            out["pages"].append(i)
            for mark, row in s["rows"].items():
                out["rows"].setdefault(mark, dict(row, page=i))
            # every header page is read; a schedule found elsewhere ends
            # the search once the header pages are done
            if i in rest:
                break
    finally:
        doc.close()
    try:
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return out


@app.get("/api/job/{job_id}/datum")
def datum_notes(job_id: str):
    """What this set says the architectural datum 100'-0" equals - the
    civil's [100'=7732'] and FFE SUMMARY levels, the structural note that
    names the datum - as SUGGESTIONS with page and text. Document-wide and
    cached per job; the operator types the value that applies to each
    structure, nothing here is applied by itself."""
    from takeoff_engine.datum_notes import find_datum_notes
    job = JOBS.get(job_id) or _404()
    cache = _job_dir(job_id) / "datum_notes.json"
    if cache.exists():
        try:
            return JSONResponse(json.loads(cache.read_text()))
        except (OSError, ValueError):
            pass
    doc = pymupdf.open(job["path"])
    try:
        pages = []
        for i in range(min(doc.page_count, 120)):
            try:
                pages.append((i, doc[i].get_text()))
            except Exception:
                continue
    finally:
        doc.close()
    out = {"notes": find_datum_notes(pages), "pages": len(pages)}
    try:
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return JSONResponse(out)


# ---------------------------------------------------------------------------
# TWIN LINES COUNTED ONCE (Wave 2 item 11). A foundation plan draws each wall
# as its two faces and each continuous footing as its two edges; both clients
# take ONE run per wall (takeoff_engine/twin_lines.py has the measurements
# and the rule). Offered only on a set that prints a footing schedule: on a
# civil sheet the families that run parallel are contours and property
# lines, and a switch on each of 280 rows would be noise.
_TWIN_MIN_SHARE = 0.15      # a family offers the switch when this much of it pairs
_TWIN_MAX_FAMILY_SAMPLES = 60000


def _page_twins(job_id: str, page: int, res: dict | None = None) -> dict:
    """{"families": {style_id: {kind, rule, drawn_ft, counted_ft, paired_ft,
    interior_ft, middle_ft, refused, chains: {chain_id: {d, c, skip}}}},
    "reason"} for one page; cached."""
    import hashlib
    from takeoff_engine import twin_lines as _tw
    job = JOBS.get(job_id) or _404()
    d = _job_dir(job_id)
    rf = d / f"p{page}_result.json"
    if not rf.exists():
        return {"families": {}, "reason": "run Detect first - there are no lines to pair yet"}
    if res is None:
        res = json.loads(rf.read_text())
    det = _page_scale(job_id, page, res)
    if not det:
        return {"families": {}, "reason": "no scale on this page - wall thickness needs real-world units"}
    sched = _doc_footing_schedule(job_id, job["path"])
    widths = sorted({round(float(r["width_ft"]), 3) for r in (sched.get("rows") or {}).values()
                     if isinstance(r, dict) and r.get("width_ft")})
    if not (sched.get("rows") or {}):
        return {"families": {}, "reason": "this set prints no footing schedule - "
                                          "counting walls once is offered on foundation plans"}
    fpp = det["ft_per_paper_inch"] / 72.0
    key = "|".join([str(res.get("engine_sig")), str(rf.stat().st_mtime_ns),
                    hashlib.sha1(pathlib_bytes(_tw.__file__)).hexdigest()[:12],
                    f"{fpp:.8f}", ",".join(f"{w:g}" for w in widths)])
    cf = d / f"p{page}_twins.json"
    if cf.exists():
        try:
            cached = json.loads(cf.read_text())
            if cached.get("key") == key:
                return cached
        except (OSError, ValueError):
            pass
    by_sid: dict = {}
    for ch in res.get("chains") or []:
        if ch.get("class") == "annotation" or not ch.get("points") or len(ch["points"]) < 2:
            continue
        by_sid.setdefault(ch["style_id"], []).append(ch)
    fams = {}
    for sid, chs in by_sid.items():
        if len(chs) < 2:
            continue
        drawn_ft = sum(float(c.get("length_pt") or 0.0) for c in chs) * fpp
        if drawn_ft < 10.0 or drawn_ft / 0.5 > _TWIN_MAX_FAMILY_SAMPLES:
            continue
        r = _tw.family_twins(chs, fpp, schedule_widths=widths)
        if r.get("refused") or not r.get("drawn_ft"):
            continue
        if r["paired_ft"] < _TWIN_MIN_SHARE * r["drawn_ft"] or r["counted_ft"] > r["drawn_ft"] - 1.0:
            continue
        fams[str(sid)] = {
            "kind": r.get("kind"), "rule": r.get("rule"),
            "drawn_ft": r["drawn_ft"], "counted_ft": r["counted_ft"],
            "paired_ft": r["paired_ft"], "interior_ft": r.get("interior_ft", 0.0),
            "middle_ft": r.get("middle_ft", 0.0),
            "chains": {str(cid): {"d": v["drawn_pt"], "c": v["counted_pt"], "skip": v["skip"]}
                       for cid, v in r["chains"].items()},
        }
    out = {"key": key, "families": fams, "reason": None,
           "schedule_widths_ft": widths, "ft_per_pt": round(fpp, 6)}
    try:
        cf.write_text(json.dumps(out))
    except OSError:
        pass
    return out


def pathlib_bytes(path) -> bytes:
    try:
        return Path(path).read_bytes()
    except OSError:
        return b""


@app.get("/api/job/{job_id}/page/{page}/twins")
def page_twins(job_id: str, page: int):
    """The families on this page drawn as twin lines (a wall's two faces, a
    footing's two edges), each with its as-drawn and one-run footage and the
    stretches the one-run count leaves out. Detect first; offered on a set
    that prints a footing schedule."""
    out = dict(_page_twins(job_id, page))
    out.pop("key", None)
    return JSONResponse(out)


@app.get("/api/job/{job_id}/page/{page}/footings")
def footings(job_id: str, page: int):
    """The footing marks on this sheet, each with the elevations printed
    beside it and its schedule row (or its own digits, flagged). Words
    only - no detect needed - and every value carries its evidence, so the
    structure panel can offer them as chips the operator confirms."""
    from takeoff_engine.footing_callouts import (find_footing_callouts,
                                                 group_walls_by_bof,
                                                 join_schedule)
    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    try:
        if page < 0 or page >= doc.page_count:
            raise HTTPException(404, "no such page")
        words = page_words(doc[page])
    finally:
        doc.close()
    c = find_footing_callouts(words)
    sched = _doc_footing_schedule(job_id, job["path"])
    join_schedule(c, sched)
    steps = _wall_steps(words)
    # WALLS GROUPED BY BOTTOM OF FOOTING (Beaumont colours every wall by the
    # elevation its footing bottoms at): needs this sheet's lines and its
    # scale, so it is absent - with the reason - until Detect has run.
    groups, why = None, None
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        why = "run Detect first - the wall groups need this sheet's lines"
    else:
        res = json.loads(rf.read_text())
        det = _page_scale(job_id, page, res)
        if not det:
            why = "no scale on this page - the wall groups need real-world units"
        else:
            labels = {st["style_id"]: (st.get("label") or st.get("suggested_label"))
                      for st in res.get("styles") or []}
            chains = [dict(ch, label=labels.get(ch.get("style_id")))
                      for ch in res.get("chains") or []]
            # ONE RUN PER FOOTING: the footing family's inner edges are left
            # out when the twins are known (Barn S1-1.3: 1,055 ft as drawn,
            # 760 ft counted once, against the client's own 704 ft)
            tw = _page_twins(job_id, page, res)
            skip_by_style = {sid: {cid: v["skip"] for cid, v in f["chains"].items()}
                             for sid, f in (tw.get("families") or {}).items()}
            groups = group_walls_by_bof(c["callouts"], chains,
                                        det["ft_per_paper_inch"] / 72.0, sched,
                                        steps=steps, skip_by_style=skip_by_style)
            groups["ft_per_pt"] = round(det["ft_per_paper_inch"] / 72.0, 5)
    return JSONResponse({"callouts": c["callouts"], "slabs": c["slabs"],
                         "by_mark": c["by_mark"], "steps": steps,
                         "groups": groups, "groups_reason": why,
                         "schedule": {"pages": sched.get("pages", []),
                                      "rows": len(sched.get("rows") or {}),
                                      "scanned": sched.get("scanned", 0)}})


def _wall_steps(words) -> list:
    """Centres of the WALL STEP words on a sheet - the printed word STEP,
    alone or fused with WALL - where a footing changes elevation along a
    wall (19 on Barn S1-1.3, always a separate STEP word)."""
    return [{"x": round((w[0] + w[2]) / 2, 1), "y": round((w[1] + w[3]) / 2, 1)}
            for w in words if str(w[4]).strip().upper() in ("STEP", "WALL STEP")]


class FootingSplitBody(BaseModel):
    points: list
    reach_ft: float = 15.0


@app.post("/api/job/{job_id}/page/{page}/footings/split")
def footings_split(job_id: str, page: int, body: FootingSplitBody):
    """A drawn outline's perimeter split by bottom-of-footing elevation:
    each edge joins the callouts within reach of it (a WALL STEP printed
    between two callouts is where the split falls), so the groups add up
    to the perimeter and the structure panel can say which part of the
    wall digs to which elevation. A suggestion; nothing is written."""
    from takeoff_engine.footing_callouts import (find_footing_callouts,
                                                 split_outline_by_bof)
    job = JOBS.get(job_id) or _404()
    pts = body.points or []
    if len(pts) < 2 or len(pts) > 2000 or any(not isinstance(q, (list, tuple)) or len(q) != 2 for q in pts):
        raise HTTPException(400, "an outline needs at least two [x, y] points")
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    det = _page_scale(job_id, page, json.loads(rf.read_text()))
    if not det:
        raise HTTPException(400, "no scale on this page - type one into the "
                                 "scale box in the sheet header first")
    doc = pymupdf.open(job["path"])
    try:
        if page < 0 or page >= doc.page_count:
            raise HTTPException(404, "no such page")
        words = page_words(doc[page])
    finally:
        doc.close()
    c = find_footing_callouts(words)
    reach = max(2.0, min(60.0, float(body.reach_ft or 15.0)))
    out = split_outline_by_bof(pts, c["callouts"], det["ft_per_paper_inch"] / 72.0,
                               reach_ft=reach, steps=_wall_steps(words))
    out["callouts"] = sum(1 for x in c["callouts"] if x.get("bof_ft") is not None)
    return JSONResponse(out)


@app.get("/api/job/{job_id}/page/{page}/watchlist")
def watchlist(job_id: str, page: int):
    """The watch-list callouts on this sheet - septic tank, propane tank,
    culvert, area inlet, cleanout, wye - each with the size printed beside
    it and its BY OTHERS / N.I.C. / RE. TO tag. Words only, no detect; a
    keyword list because the estimator rejected a full callout dump. Never
    counted in the takeoff by itself (the panel's add button does that)."""
    from takeoff_engine.extract import page_lines as _page_lines
    from takeoff_engine.watchlist import find_watch_callouts
    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    try:
        if page < 0 or page >= doc.page_count:
            raise HTTPException(404, "no such page")
        lines = _page_lines(doc[page])
    finally:
        doc.close()
    return JSONResponse(find_watch_callouts(lines))


class PipeSizesBody(BaseModel):
    labels: dict | None = None


@app.post("/api/job/{job_id}/page/{page}/pipesizes")
def pipesizes(job_id: str, page: int, body: PipeSizesBody):
    """Pipe sizes on this sheet's runs, per line family: the size in the
    family's name, and the sizes its runs carry (inline codes on the line,
    leader callouts pointing at it) weighted by run length - so a nameless
    storm family can be named from what the sheet prints beside it, and a
    mixed one is said to be mixed rather than guessed. Needs the detect
    result; leaders come from the callouts cache once Sheet callouts has
    been opened. `labels` are the UI's current family names."""
    from takeoff_engine.pipesize import family_sizes
    job = JOBS.get(job_id) or _404()
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    det = _page_scale(job_id, page, res)
    fpp = det["ft_per_paper_inch"] / 72.0 if det else 1.0
    cf = _job_dir(job_id) / f"p{page}_callouts.json"
    callouts = None
    if cf.exists():
        try:
            callouts = json.loads(cf.read_text()).get("callouts", [])
        except (OSError, ValueError):
            callouts = None
    labels = {int(k): str(v) for k, v in (body.labels or {}).items()
              if str(k).lstrip("-").isdigit() and isinstance(v, str)}
    fams = family_sizes(res.get("chains", []), res.get("styles", []), labels, callouts, fpp)
    return JSONResponse({"families": fams, "unit": "ft" if det else "pt",
                         "leaders": len(callouts) if callouts is not None else None,
                         "leaders_note": None if callouts is not None
                         else "leader callouts not read yet - open Sheet callouts once"})


@app.get("/api/job/{job_id}/page/{page}/callouts")
def callouts(job_id: str, page: int):
    """The sheet's own callouts as data: every detected leader paired with
    the text at its tail and the takeoff line it points at. Leaders stay
    out of the math; this makes them navigation/context instead."""
    import numpy as np

    job = JOBS.get(job_id) or _404()
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    cache = _job_dir(job_id) / f"p{page}_callouts.json"
    if cache.exists():
        return JSONResponse(json.loads(cache.read_text()))
    res = json.loads(rf.read_text())
    leaders = [c for c in res["chains"] if c.get("dash_signature") == "leader"]
    out = {"callouts": []}
    if leaders:
        doc = pymupdf.open(job["path"])
        pg = doc[page]
        words = page_words(pg)
        # sheets whose labels are outlines have no words worth pairing:
        # OCR once per page and cache with the result
        if len(words) < 8 * len(leaders):
            from takeoff_engine.text_detect import ocr_words
            pix = pg.get_pixmap(dpi=150)
            img = np.frombuffer(pix.samples, np.uint8).reshape(
                pix.height, pix.width, pix.n)[:, :, :3].copy()
            words = list(words) + ocr_words(img, 72.0 / 150.0)
        doc.close()
        W = [(w[0], w[1], w[2], w[3], str(w[4])) for w in words
             if str(w[4]).strip()]
        takeoff = [c for c in res["chains"] if c["class"] != "annotation"]
        segs = []
        for c in takeoff:
            p = np.asarray(c["points"], float)
            segs.append((c, p))

        def words_near(e, rad):
            hits = []
            for w in W:
                dx = max(w[0] - e[0], 0, e[0] - w[2])
                dy = max(w[1] - e[1], 0, e[1] - w[3])
                if (dx * dx + dy * dy) ** 0.5 <= rad:
                    hits.append(w)
            return hits

        def gather_text(e):
            seed = words_near(e, 40.0)
            if not seed:
                return ""
            # expand to same-row neighbours so multi-line notes read whole
            box = [min(w[0] for w in seed), min(w[1] for w in seed),
                   max(w[2] for w in seed), max(w[3] for w in seed)]
            grown = [w for w in W
                     if w[0] < box[2] + 90 and w[2] > box[0] - 90
                     and w[1] < box[3] + 26 and w[3] > box[1] - 26]
            grown.sort(key=lambda w: (round((w[1] + w[3]) / 2 / 9), w[0]))
            toks, prev = [], None
            for w in grown:
                t = w[4].strip()
                # OCR debris: bare punctuation and repeated words (the
                # sparse-PSM pass reads outline text twice along edges)
                if len(t) == 1 and not t.isalnum():
                    continue
                if prev is not None and t.upper() == prev.upper():
                    continue
                toks.append(t)
                prev = t
            return " ".join(toks)[:120]

        for c in leaders:
            p = np.asarray(c["points"], float)
            ends = [p[0], p[-1]]
            n_near = [len(words_near(e, 24.0)) for e in ends]
            ti = 0 if n_near[0] >= n_near[1] else 1
            text_end, tip = ends[ti], ends[1 - ti]
            text = gather_text(text_end)
            best, bd = None, 15.0
            for tc, tp in segs:
                d = np.min(np.hypot(tp[:, 0] - tip[0], tp[:, 1] - tip[1]))
                if d < bd:
                    bd, best = float(d), tc
            out["callouts"].append({
                "leader_chain_id": c["chain_id"],
                "text": text,
                "tx": round(float(text_end[0]), 1),
                "ty": round(float(text_end[1]), 1),
                "gx": round(float(tip[0]), 1),
                "gy": round(float(tip[1]), 1),
                "target_chain_id": best["chain_id"] if best else None,
                "target_style_id": best["style_id"] if best else None,
            })
    try:
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return JSONResponse(out)


def _doc_schedule(job_id: str, path: str, page: int) -> dict:
    """The mark schedule for this DOCUMENT, not just this page.

    An estimator's schedule usually lives on its own sheet - Bill Dunn's
    Aqua Vista set puts the window schedule on A830 (page 0) and the floor
    plans he counts on pages 1-5 - so a per-page lookup finds nothing on
    exactly the pages that need it.  This page first, then the rest of the
    document; the answer is cached per job because the OCR fallback on a
    scanned schedule sheet is slow.
    """
    from takeoff_engine import marks as _marks

    cache = _job_dir(job_id) / "schedule.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
    doc = pymupdf.open(path)
    best: dict = {"marks": [], "qty": {}, "header": "", "source": "none",
                  "page": None}
    order = [page] + [i for i in range(doc.page_count) if i != page]
    ocr_left = 3          # OCRing a scanned sheet costs ~40 s; bound it
    try:
        for i in order[:12]:
            pg = doc[i]
            words = list(page_words(pg))
            s = _marks.read_mark_schedule(words) if words else None
            if not (s and s["marks"]) and len(words) < 40 and ocr_left > 0:
                ocr_left -= 1
                # a scanned schedule sheet has no text layer at all; OCR it
                # upright (these sheets are landscape scanned onto portrait)
                # and read the mark column through the lookalike map
                try:
                    import numpy as _np

                    from takeoff_engine.text_detect import ocr_words
                    dpi = 600
                    pix = pg.get_pixmap(matrix=pymupdf.Matrix(dpi / 72, dpi / 72),
                                        colorspace="rgb")
                    img = _np.frombuffer(pix.samples, _np.uint8).reshape(
                        pix.height, pix.width, 3)
                    deg = _marks.orient_ccw(img[:, :, ::-1])
                    ow = ocr_words(_marks._rot_ccw(img, deg), px_to_pt=72.0 / dpi)
                    s = _marks.read_mark_schedule(list(ow), fuzzy=True)
                except Exception:
                    s = None
            if s and len(s["marks"]) > len(best["marks"]):
                best = dict(s)
                best["page"] = i
            # the first sheet that yields a real mark column IS the schedule
            # sheet - pages are visited current-first, and a floor plan does
            # not carry one, so stop rather than OCR the whole set
            if len(best["marks"]) >= 3:
                break
    finally:
        doc.close()
    try:
        cache.write_text(json.dumps(best))
    except OSError:
        pass
    return best


# bump when the symbol finder's own logic changes (it lives in this file,
# outside engine_sig's hash): 3 = disclosed caps + legend glyph naming
# (line-row veto, composed glyphs, twin candidates)
SYMBOLS_ALGO = 4


@app.get("/api/job/{job_id}/page/{page}/symbols/suggest")
def symbols_suggest(job_id: str, page: int):
    """Cached wrapper. The finding is deterministic for a given page, and on
    a scanned sheet it costs minutes (takeoff_engine.marks renders, deskews
    and template-matches at 300 dpi). Caching it per page is what makes a
    whole-document rollup affordable: /marks-rollup reads these files rather
    than recomputing six floor plans."""
    from takeoff_engine.detect_page import engine_sig

    sig = engine_sig()
    f = _job_dir(job_id) / f"p{page}_symbols.json"
    if f.exists():
        try:
            js = json.loads(f.read_text())
            # Same rule as a detect result (F96): a cache keyed only on the
            # PDF happily serves counts the current code would not produce.
            # This one feeds /marks-rollup, so a stale entry becomes a stale
            # quantity in the number Bill faxes a supplier. SYMBOLS_ALGO
            # exists because this route's own logic lives in server.py,
            # which engine_sig does not hash - bumping it here is what
            # invalidates these caches when the finder changes.
            if (js.get("engine_sig") == sig
                    and js.get("symbols_algo") == SYMBOLS_ALGO):
                return JSONResponse(js)
        except (OSError, ValueError):
            pass
    out = _symbols_compute(job_id, page)
    try:
        out["engine_sig"] = sig
        out["symbols_algo"] = SYMBOLS_ALGO
        _job_dir(job_id).mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(out))
    except OSError:
        pass
    return JSONResponse(out)


def _symbols_compute(job_id: str, page: int):
    """Repeated congruent small shapes on the page - the countable symbols
    (grid bubbles, fixtures, window/door glyphs). Geometry does the finding
    and counting so the number is verifiable; the human (or the AI's
    inline-legend read) supplies the NAME.

    Two finders, one contract. Vector sheets group congruent closed stroke
    units. Scanned sheets have no strokes at all, so they go through
    takeoff_engine.marks, which matches the bubble as a picture and reads
    the code inside it. Either way the answer is {groups, schedule}."""
    import re as _re

    import numpy as np

    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    pg = doc[page]
    units = extract_strokes(pg)
    words = page_words(pg)
    doc.close()

    # mark schedule, when the SET carries one: a header row containing
    # MARK (or WINDOW TYPE) anchors a table whose rows map mark -> quantity.
    # Used three ways: cross-check the counted symbols, EXCLUDE the table's
    # own cell rectangles from symbol grouping (they are congruent boxes and
    # count themselves otherwise - measured on the window benchmark), and
    # constrain what a scanned bubble is allowed to be read as.
    doc_sched = _doc_schedule(job_id, job["path"], page)
    here = read_mark_schedule(words) if words else {"marks": [], "qty": {},
                                                    "box": None}
    sched: dict = dict(here.get("qty") or {})
    table_box = here.get("box")
    if not sched and doc_sched.get("qty"):
        sched = dict(doc_sched["qty"])
    vocab = here.get("marks") or doc_sched.get("marks") or []

    if not units and not words:
        return _symbols_raster(job, page, vocab, sched, doc_sched)

    MARK_WORD = _re.compile(r"^[A-Z]{1,3}-?\d+[A-Z]?$")
    groups: dict = {}
    for u in units:
        if not u.closed or not (4.0 <= u.length <= 160.0):
            continue
        bb = u.pts.max(axis=0) - u.pts.min(axis=0)
        w, h = float(bb[0]), float(bb[1])
        if max(w, h) > 45.0 or max(w, h) < 2.0:
            continue
        key = (round(w), round(h), round(u.length / 4.0))
        c = (u.pts.min(axis=0) + u.pts.max(axis=0)) / 2.0
        groups.setdefault(key, []).append([round(float(c[0]), 1),
                                           round(float(c[1]), 1)])

    def _in_table(c):
        return table_box and table_box[0] <= c[0] <= table_box[2] \
            and table_box[1] <= c[1] <= table_box[3]

    def _mark_at(c):
        # the mark text lives INSIDE its bubble (measured 0.7pt): the word
        # box containing (or within 3pt of) the symbol center names it
        for w in words:
            t = str(w[4]).strip().upper()
            if not MARK_WORD.match(t):
                continue
            if w[0] - 3 <= c[0] <= w[2] + 3 and w[1] - 3 <= c[1] <= w[3] + 3:
                return t
        return None

    out = []
    for (w, h, l4), centers in groups.items():
        # a symbol repeats; also drop coincident duplicates (drawn twice)
        uniq = []
        for c in centers:
            if all(abs(c[0]-d[0]) > 2 or abs(c[1]-d[1]) > 2 for d in uniq):
                uniq.append(c)
        # the schedule's own cell boxes are congruent shapes too
        if uniq and sum(1 for c in uniq if _in_table(c)) >= 0.6 * len(uniq):
            continue
        if len(uniq) < 3:
            continue
        marks: dict = {}
        unmarked = []
        for c in uniq:
            m = _mark_at(c)
            if m:
                marks.setdefault(m, []).append(c)
            else:
                unmarked.append(c)
        if sum(len(v) for v in marks.values()) >= 0.6 * len(uniq):
            # mark-bubble family: one countable group per mark, named by
            # the sheet, cross-checked against its own schedule
            for m in sorted(marks):
                g = {"w": w, "h": h, "l4": l4, "n": len(marks[m]),
                     "centers": marks[m], "name": m}
                if m in sched:
                    g["schedule_qty"] = sched[m]
                out.append(g)
            if len(unmarked) >= 3:
                out.append({"w": w, "h": h, "l4": l4, "n": len(unmarked),
                            "centers": unmarked})
        else:
            out.append({"w": w, "h": h, "l4": l4, "n": len(uniq),
                        "centers": uniq})
    out.sort(key=lambda g: -g["n"])

    # Legend-glyph naming (Pierce's missed manhole) was BUILT AND REMOVED
    # here on 2026-08-30 - three versions, each measured on Kayden C5.1:
    # a flat (w, h, length) key named a NOTES row onto 882 letter glyphs
    # and a waterline row onto its 12 line markers; composed-glyph
    # verification (all strip units co-occurring) then found ZERO real
    # structures, because the legend draws its symbol at a different size
    # or block than the plan instances (4 anchor units on the plan, the
    # companion circle never beside them; 1 of 3 hydrants verified). A
    # namer that hits one instance in three is a confident wrong count.
    # The working route is raster template matching seeded from the
    # legend-strip render - marks.build_family already does exactly this
    # for scanned bubbles - measured design in EXCAVATION-PLAN.md.
    # a silent cap reports as full coverage (section-5 trap): the bound
    # stays for response size, but the total is disclosed so the panel can
    # say "40 of 57" instead of implying the sheet holds 15 symbol groups
    return {"groups": out[:40], "groups_total": len(out), "schedule": sched,
            "schedule_marks": doc_sched.get("marks") or [],
            "schedule_page": doc_sched.get("page"),
            "schedule_header": doc_sched.get("header", ""),
            "schedule_source": doc_sched.get("source", "none")}


def _symbols_raster(job: dict, page: int, vocab: list, sched: dict,
                    doc_sched: dict) -> dict:
    """Scanned sheet: count mark bubbles as pictures (takeoff_engine.marks).

    Same {groups, schedule} contract as the vector path, so the Count panel
    needs no idea which finder ran. Centres come back in page points."""
    from takeoff_engine import marks as _marks

    mp = _marks.detect_marks(job["path"], page, vocab or None)
    by: dict = {}
    for t in mp.tags:
        if t.kind == "legend":
            continue
        # three buckets, not two: a schedule mark, a bubble read confidently
        # as something the schedule does not list (a neighbouring tag family
        # - the WALL tags read L04/L06 on Bill's floor plans), and a bubble
        # nothing could be read from. Collapsing the middle one into "not
        # read" hides a real answer from the estimator.
        key = t.code or ("~" + t.off_schedule if t.off_schedule else "")
        by.setdefault(key, []).append([round(t.cx_pt, 1), round(t.cy_pt, 1)])
    px_pt = 72.0 / _marks.RENDER_DPI
    w_pt = round(mp.tag_size[0] * px_pt, 1)
    h_pt = round(mp.tag_size[1] * px_pt, 1)
    out = []
    for code, cs in by.items():
        g = {"w": w_pt, "h": h_pt, "n": len(cs), "centers": cs,
             "source": "raster"}
        if code.startswith("~"):
            g["off_schedule"] = code[1:]
        elif code:
            g["name"] = code
            if code in sched:
                g["schedule_qty"] = sched[code]
        out.append(g)
    out.sort(key=lambda g: (0 if g.get("name") else 1 if g.get("off_schedule")
                            else 2, -g["n"]))
    return {"groups": out[:40], "groups_total": len(out), "schedule": sched,
            "schedule_marks": doc_sched.get("marks") or [],
            "schedule_page": doc_sched.get("page"),
            "schedule_header": doc_sched.get("header", ""),
            "schedule_source": doc_sched.get("source", "none"),
            "raster": True, "notes": mp.notes,
            "tag_px": list(mp.tag_size), "orient": mp.orient,
            "skew_deg": mp.skew_deg}


class StructureBody(BaseModel):
    # [{name?, points: [[x_pt, y_pt], ...] (>= 3 corners, page pt),
    #   depth_ft, fill_depth_ft?, footing_ft?, footing_w_ft?, layback_hv?}]
    # - one entry per building. `layback_ft` (a constant trench width) was
    # the pre-2026-09-16 shape and is ignored: the trench is now the
    # depth-proportional wedge the client bids (see _structure_one).
    structures: list[dict] = []


def _inset_perim(ring, t: float) -> float:
    """Perimeter of a simple polygon offset INWARD by t (same units as the
    ring), with mitred corners: each convex corner of turning angle phi
    shortens the ring by 2 t tan(phi/2), each concave corner lengthens it
    by the same. A rectangle loses 8 t; an L loses 8 t at its five convex
    corners and gains 2 t at its one concave corner. Floors at 0 when the
    wall is thicker than the shape. Same arithmetic as webui/app.js
    stPolyInsetPerim - the radon row is the inside face of the exterior
    walls (Beaumont, 2026-09-15, 32:49)."""
    import math
    n = len(ring)
    if n < 3:
        return 0.0
    area2 = 0.0
    per = 0.0
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
        per += math.hypot(x2 - x1, y2 - y1)
    orient = 1.0 if area2 >= 0 else -1.0
    loss = 0.0
    for i in range(n):
        ax, ay = ring[i - 1]
        bx, by = ring[i]
        cx, cy = ring[(i + 1) % n]
        ux, uy = bx - ax, by - ay
        vx, vy = cx - bx, cy - by
        lu, lv = math.hypot(ux, uy), math.hypot(vx, vy)
        if lu < 1e-9 or lv < 1e-9:
            continue
        cross = (ux * vy - uy * vx) / (lu * lv)
        dot = (ux * vx + uy * vy) / (lu * lv)
        phi = math.atan2(abs(cross), dot)          # turning angle, 0..pi
        if phi >= math.pi - 1e-9:
            continue                               # a spike: no mitre
        convex = (cross * orient) >= 0
        loss += (2.0 if convex else -2.0) * t * math.tan(phi / 2.0)
    return max(per - loss, 0.0)


def _structure_one(s: dict, ft_per_pt: float) -> dict:
    """One building footprint -> the excavator's four bid-sheet numbers.
    Same arithmetic as the review UI (webui/app.js stCalc); this is the
    server-side source of truth for the export/report path.

    Beaumont Excavating's own rule (call of 2026-09-15, 28:00-31:17; bid
    sheet p.3 items 8 and 9): the trench outside the wall is dug to the
    bottom of the footing with the sides laid back at 1:1 for safety, so
    per foot of wall the section is depth x (footing width + half the
    depth); "a 6 ft cut is 6 by 6 by 3" plus the footing sticking out.
    Backfill is the SAME section figured to the PROPOSED grade, which can
    be higher or lower than the ground he dug from - so it is not the dig.

      Dt = depth_ft + footing_ft                (ground -> footing bottom)
      Df = (fill_depth_ft or depth_ft) + footing_ft   (proposed -> bottom)
      Mass Excavation CY      = footprint SF x depth_ft / 27
      Structure Excavation CY = wall LF x Dt x (W + 0.5 x hv x Dt) / 27
      Structure Backfill CY   = wall LF x Df x (W + 0.5 x hv x Df) / 27
      Slab Prep SF            = footprint SF
      Perimeter Drain LF      = the outline's perimeter (exterior face)
      Radon LF                = the outline inset by the wall thickness
                                (inside face of the exterior walls)
    with W = footing_w_ft (default 2, a CF2.0 footing) and hv = layback_hv
    (horizontal per vertical, default 1.0 = 1:1). When fill_depth_ft is
    blank the backfill is figured to the dig depth and the reply says so
    (backfill_assumed), so a bid never silently carries that guess.
    """
    pts = s.get("points") or []
    try:
        ring = [(float(p[0]), float(p[1])) for p in pts]
    except (TypeError, ValueError, IndexError):
        raise HTTPException(400, "points must be [[x, y], ...] in page pt")
    if len(ring) >= 2 and abs(ring[0][0] - ring[-1][0]) < 0.5 \
            and abs(ring[0][1] - ring[-1][1]) < 0.5:
        ring.pop()     # first == last: one vertex, not two
    if len(ring) < 3:
        raise HTTPException(400, "an outline needs at least 3 corners")
    for x, y in ring:
        if not (x == x and y == y and abs(x) < 1e7 and abs(y) < 1e7):
            raise HTTPException(400, "non-finite corner coordinate")

    def num(key, default=0.0):
        v = s.get(key, default)
        try:
            v = float(v)
        except (TypeError, ValueError):
            raise HTTPException(400, f"{key} must be a number")
        if v != v or v < 0 or v > 1000:
            raise HTTPException(400, f"{key} out of range (0-1000 ft)")
        return v
    depth, footing = num("depth_ft"), num("footing_ft")
    width, hv = num("footing_w_ft", 2.0), num("layback_hv", 1.0)
    wall_in = num("wall_in", 8.0)
    fill_raw = s.get("fill_depth_ft")
    assumed = fill_raw is None or fill_raw == ""
    fill = depth if assumed else num("fill_depth_ft")
    dt = depth + footing
    df = fill + footing
    area2 = 0.0
    perim = 0.0
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i]
        x2, y2 = ring[(i + 1) % n]
        area2 += x1 * y2 - x2 * y1
        perim += ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
    area_sf = abs(area2) / 2.0 * ft_per_pt * ft_per_pt
    perim_lf = perim * ft_per_pt
    radon_lf = _inset_perim(ring, (wall_in / 12.0) / ft_per_pt) * ft_per_pt
    struct = perim_lf * dt * (width + 0.5 * hv * dt) / 27.0
    backfill = perim_lf * df * (width + 0.5 * hv * df) / 27.0
    return {
        "name": str(s.get("name", ""))[:60],
        "corners": n,
        "area_sf": round(area_sf, 1),
        "perim_lf": round(perim_lf, 1),
        "depth_ft": depth, "fill_depth_ft": fill, "footing_ft": footing,
        "footing_w_ft": width, "layback_hv": hv, "wall_in": wall_in,
        "dig_w_ft": round(width + 0.5 * hv * dt, 2),
        "drain_lf": round(perim_lf, 1),
        "radon_lf": round(radon_lf, 1),
        "backfill_assumed": bool(assumed),
        "mass_cy": round(area_sf * depth / 27.0, 1),
        "struct_cy": round(struct, 1),
        "backfill_cy": round(backfill, 1),
        "slab_sf": round(area_sf, 1),
    }


@app.get("/api/job/{job_id}/marks-rollup")
def marks_rollup(job_id: str):
    """Every mark code in the DOCUMENT, per page and in total.

    Bill Dunn asked for exactly this on 2026-08-21: "I want to know that I
    have 27 type D windows on the fifth floor". A per-page count cannot
    answer it and neither can a per-page CSV - what he faxes a supplier is
    one quantity per type for the whole building.

    Reads the per-page symbol caches only; it never triggers detection, so
    it is instant and it reports which pages are still pending rather than
    quietly counting a partial document as a total. `schedule_qty` is the
    architect's own printed quantity where the schedule carries one, so the
    row doubles as the reconciliation an estimator is actually paid for.
    """
    from takeoff_engine.detect_page import engine_sig

    _sig = engine_sig()
    job = JOBS.get(job_id) or _404()
    d = _job_dir(job_id)
    npages = int(job.get("pages") or 0)
    per_page: dict[int, dict[str, int]] = {}
    pending: list[int] = []
    sched: dict[str, float] = {}
    sched_page = None
    for pno in range(npages):
        f = d / f"p{pno}_symbols.json"
        if not f.exists():
            pending.append(pno)
            continue
        try:
            js = json.loads(f.read_text())
        except (OSError, ValueError):
            pending.append(pno)
            continue
        # a count produced by older engine code is not a count for this
        # document - report the sheet as still needing Count rather than
        # folding a stale quantity into the total (F96)
        if js.get("engine_sig") != _sig:
            pending.append(pno)
            continue
        if not sched and js.get("schedule"):
            sched = {str(k): v for k, v in js["schedule"].items()}
            sched_page = js.get("schedule_page")
        counts: dict[str, int] = {}
        for g in js.get("groups") or []:
            nm = (g.get("name") or "").strip()
            if not nm:
                continue          # unread bubbles are reported separately
            counts[nm] = counts.get(nm, 0) + int(g.get("n") or 0)
        if counts:
            per_page[pno] = counts

    codes = sorted({c for pc in per_page.values() for c in pc}
                   | {str(k) for k in sched})
    rows = []
    for c in codes:
        by = {str(p): per_page[p][c] for p in sorted(per_page) if c in per_page[p]}
        found = sum(by.values())
        q = sched.get(c)
        row = {"code": c, "per_page": by, "found": found}
        if q is not None:
            try:
                q = int(round(float(q)))
                row["schedule_qty"] = q
                row["delta"] = found - q
            except (TypeError, ValueError):
                pass
        rows.append(row)
    return JSONResponse({
        "rows": rows,
        "pages_counted": sorted(per_page),
        "pages_pending": pending,
        "schedule_page": sched_page,
        "total_found": sum(r["found"] for r in rows),
        "total_schedule": sum(r["schedule_qty"] for r in rows
                              if "schedule_qty" in r) or None,
    })


@app.post("/api/job/{job_id}/page/{page}/structure")
def structure_calc(job_id: str, page: int, body: StructureBody):
    """Building footprint(s) -> Mass Excavation / Structure Excavation /
    Structure Backfill (CY) and Slab Prep (SF), in the excavator's own
    bid-sheet terms. Validates the polygons and returns per-structure rows
    plus the page totals; the UI does the same math client-side and uses
    this as a cross-check."""
    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    # _page_scale, NOT res["scale"]. The reviewer can type a scale or pick a
    # different printed caption, and until 2026-08-23 these two volume routes
    # read the ENGINE's number straight out of the cached result and ignored
    # them both. Measured on Barn Civil engine page 2 = printed sheet 3 (C3.0):
    # type 1" = 60', watch every length on screen double, press Calculate, and
    # the CY came back computed at the engine's 30 ft/in - then priced,
    # exported and printed. Screen and deliverable disagreeing with nothing
    # saying so is the harm this whole sidecar exists to prevent. It also made
    # these two the only places left that answer "calibrate first" on a page
    # the operator HAS calibrated by hand.
    det = _page_scale(job_id, page, res)
    if not det:
        raise HTTPException(400, "no scale on this page - type one into the "
                                 "scale box in the sheet header first; volumes "
                                 "need real-world units")
    ft_per_pt = det["ft_per_paper_inch"] / 72.0
    if not body.structures:
        raise HTTPException(400, "no structures given")
    if len(body.structures) > 200:
        raise HTTPException(400, "too many structures")
    rows = [_structure_one(s, ft_per_pt) for s in body.structures]
    totals = {k: round(sum(r[k] for r in rows), 1)
              for k in ("mass_cy", "struct_cy", "backfill_cy", "slab_sf",
                        "drain_lf", "radon_lf")}
    items = [{"label": label, "qty": totals[key], "unit": unit}
             for label, key, unit in (
                 ("Mass Excavation", "mass_cy", "CY"),
                 ("Structure Excavation", "struct_cy", "CY"),
                 ("Structure Backfill", "backfill_cy", "CY"),
                 ("Slab Prep", "slab_sf", "SF"),
                 ("Perimeter Drain", "drain_lf", "LF"),
                 ("Radon", "radon_lf", "LF"))
             if totals[key] > 0]
    _ledger_structure(job_id, page, body, rows, totals)
    return JSONResponse({"structures": rows, "totals": totals,
                         "extra_items": items,
                         "ft_per_pt": round(ft_per_pt, 5)})


# Analyze verdicts live for this server session only - never on disk
_AI_SESSION_CACHE: dict = {}


@app.middleware("http")
async def _no_stale_ui(request, call_next):
    """UI assets must revalidate every load. A browser that keeps serving a
    cached app.js resurrects removed behavior (the blueprint-memory code
    lived on in stale tabs after removal); ETag revalidation keeps reloads
    fast while guaranteeing the current build."""
    resp = await call_next(request)
    if not request.url.path.startswith("/api"):
        resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


app.mount("/", StaticFiles(directory=ROOT / "webui", html=True), name="ui")
