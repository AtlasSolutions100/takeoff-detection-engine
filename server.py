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
import contextvars
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
import weakref
from collections import Counter, OrderedDict, deque
from pathlib import Path

import pymupdf
from fastapi import FastAPI, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from takeoff_engine import audit as _audit_log
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
# the sheet number the host SHOWS (page list, sheet heading, report header): the refined reader, kept out of
# detect_page.py so a fix there does not mark every detection out of date (takeoff_engine/sheet_no.py)
from takeoff_engine.sheet_no import read_sheet_number as read_sheet_number_shown
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
#                       local plan corpus + the client folder next to this repo,
#                       and our own upload dir)
#   TAKEOFF_HOSTED=1    hosted mode - open-path/legend-sheet paths are
#                       refused outright (uploads only) and /api/samples
#                       is empty, so one tenant never sees another's files
HOSTED = os.environ.get("TAKEOFF_HOSTED", "").strip().lower() in ("1", "true", "yes")
# The product this service fronts. core-cutfill is the unified product -
# excavation and construction tools in one Tools menu, nothing hidden by
# product - so these only name it (the BETA notice, the sign-in pages).
# TAKEOFF_PRODUCT is still honoured for the NAME, for a box that sets it.
PRODUCT = os.environ.get("TAKEOFF_PRODUCT", "all").strip().lower() or "all"
PRODUCT_NAME = {"construction": "CET"}.get(PRODUCT, "CORE Takeoff")
PRODUCT_LINE = {"construction": "GENERAL CONSTRUCTION"}.get(PRODUCT, "TAKEOFF")
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
    without adding a leg, and on a 48-page civil set's p39 that makes 515 of 682
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

# No generated API docs (/docs, /redoc, /openapi.json): they were served to every signed-in account and are
# built from the route docstrings, which name clients (critic bid-content-1, RISK-4)
app = FastAPI(title="Takeoff Detection Service", docs_url=None, redoc_url=None, openapi_url=None)

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

# What a VIEWER account is told when it tries to change anything. One string,
# answered by the middleware for every write and by the two GET routes that
# change something (reopen_job, and /api/bids/{id}/plans via the middleware).
VIEWER_READ_ONLY = ("Read-only: your role is Viewer. Ask an admin for "
                    "Estimator access.")

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
    from takeoff_engine import users as _u
    # `v`: the account's session counter now (users.py, ENDING SESSIONS); v1.8.5 reads past it
    body = json.dumps({"u": username, "exp": int(_t.time()) + SESSION_DAYS * 86400,
                       "v": _u.session_version(username)},
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
        # ENDED: made before the account's last sign-out, password change, reset or move to viewer. A cookie from
        # before the counter existed carries none and reads as 0, like an account that has none yet
        from takeoff_engine import users as _u
        if d.get("u") is not None and int(d.get("v", 0)) < _u.session_version(d.get("u")):
            return False, None
        return True, d.get("u")
    except (ValueError, TypeError):
        return False, None


# the page's own marker on every write (webui/client.js; _client_gate): its absence means a page from before this one
CLIENT_HEADER = "X-Core-Client"
CLIENT_MIN = 2
CLIENT_OLD_WORDS = "CORE was updated. Reload this page to keep working."
# the forms that must work without the page's scripts (and v1.8.5's sign-out still has to sign out)
_CLIENT_EXEMPT = {"/api/login", "/api/logout", "/api/change-password"}


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
               "/legal.css", "/lockup-animated.svg",
               # The landing page, and the palette + self-hosted fonts that
               # it, the login, change-password and legal pages all load.
               "/welcome", "/welcome.html", "/welcome.css", "/tokens.css",
               "/fonts/ibm-plex-sans-latin.woff2",
               "/fonts/ibm-plex-mono-latin-400.woff2",
               "/fonts/ibm-plex-mono-latin-500.woff2",
               "/client.js"}           # the page marker every page loads first (_client_gate): no data, the sign-in page too

    # Reachable while signed in but still on the issued password. Everything
    # else is refused until it is changed - that gate is the entire reason a
    # shared starting password is acceptable rather than reckless, because
    # until then the account is only as private as one word that 20 people
    # were told.
    _RESET_OK = {"/change-password", "/change-password.html",
                 "/api/change-password", "/api/me", "/api/logout",
                 "/api/beta-notice", "/favicon.ico", "/favicon.svg",
                 "/favicon-32.png", "/favicon-180.png", "/client.js"}

    # SIGN-IN LIMIT (2026-10-02). It counted failures per ADDRESS only (12 in 5 minutes), and behind the reverse proxy
    # every client most likely arrives from the proxy's one private address: twelve wrong passwords from anyone blocked
    # sign-in for every client. Now failures count per NAME TRIED (12 in 5 minutes: only that name waits; another
    # account's sign-in is never held by it), and per address only when the address is a public one (60 in 5 minutes,
    # so one office behind one router is not blocked by a colleague); loopback and private ranges are never blocked as
    # an address. Both maps are capped (the oldest entry goes), and each keeps at most its limit of times.
    # NOT covered: a slow guess spread over many names, or from many public addresses, stays under both limits.
    import collections as _co
    import ipaddress as _ipa
    _FAIL_WINDOW = 300
    _FAIL_NAME_MAX = 12
    _FAIL_ADDR_MAX = 60
    _FAIL_TRACK = 5000               # names (and addresses) remembered at once
    _FAILS: "dict" = _co.OrderedDict()        # name tried (folded) -> recent failure times
    _FAILS_ADDR: "dict" = _co.OrderedDict()   # public address -> recent failure times
    _PRIVATE_NETS = [_ipa.ip_network(n) for n in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16",
                                                  "::1/128", "fc00::/7")]

    def _public_addr(ip: str) -> bool:
        """True only for an address that parses and is outside loopback and the private ranges; anything else (the
        proxy's private address, a test client's name) is never limited as an address."""
        try:
            a = _ipa.ip_address(str(ip).split("%")[0])
        except ValueError:
            return False
        if a.version == 6 and a.ipv4_mapped is not None:
            a = a.ipv4_mapped
        return not any(a in n for n in _PRIVATE_NETS if n.version == a.version)

    def _fail_name(name: str) -> str:
        return (name or "").strip().casefold()[:200]

    def _recent(m, key, now) -> list:
        hits = [t for t in m.get(key, ()) if now - t < _FAIL_WINDOW]
        if hits:
            m[key] = hits
        else:
            m.pop(key, None)
        return hits

    def _throttled(ip: str, name: str = "") -> bool:
        import time as _t
        now = _t.time()
        if len(_recent(_FAILS, _fail_name(name), now)) >= _FAIL_NAME_MAX:
            return True
        return _public_addr(ip) and len(_recent(_FAILS_ADDR, ip, now)) >= _FAIL_ADDR_MAX

    def _note_fail(ip: str, name: str = "") -> None:
        import time as _t
        now = _t.time()
        for m, key, cap in ((_FAILS, _fail_name(name), _FAIL_NAME_MAX),
                            (_FAILS_ADDR, ip if _public_addr(ip) else None, _FAIL_ADDR_MAX)):
            if key is None:
                continue
            hits = (_recent(m, key, now) + [now])[-cap:]
            m.pop(key, None)
            m[key] = hits                # newest last: the oldest-touched entry is first to go
            while len(m) > _FAIL_TRACK:
                m.popitem(last=False)

    def _log_failed_login(name: str) -> None:
        """A failed sign-in in the activity log, where this build has one (takeoff_engine/audit.py): the account name
        tried when it is an account, never what was typed otherwise (a password typed into the name box must not be
        kept), and never the password. Best effort: a log failure never touches the sign-in."""
        try:
            from takeoff_engine import audit as _al
        except ImportError:
            return
        try:
            known = _users.find(name)
            _al.write({"user": None, "action": "login_failed", "name": known or "(no such account)"})
        except Exception:                                   # noqa: BLE001 - the log is never the request's problem
            pass

    # THE VIEWER ROLE IS READ-ONLY, and it is enforced HERE for the same
    # reason ownership is (below): fifty-odd write routes, more every week,
    # and a check each route has to remember is a check one of them forgets.
    # So the rule is by METHOD, not by route: a viewer's request that is not
    # a read, to anything under the two APIs, is refused before routing -
    # uploads, detect, approve, calcstate, reset, delete, demo open, the cut /
    # fill engine's own PATCHes and computes, and whatever is added next.
    # The exceptions are the four writes that are about the SESSION, not the
    # work: signing in and out, choosing a password (a viewer on a temporary
    # one could otherwise never get past the must-change gate), and feedback.
    _VIEWER_READ = {"GET", "HEAD", "OPTIONS"}
    _VIEWER_WRITE_OK = {"/api/logout", "/api/login", "/api/change-password",
                        "/api/feedback"}
    # GETs that CHANGE something, refused to a viewer like a write. Measured
    # 2026-09-25 by reading every GET route: this one makes Bid Sniper
    # download a plan set from the portal (plans(fetch=True)). The other one,
    # reopening a registry row that has no page sizes (it re-registers the
    # PDF), is refused inside reopen_job, the only place that knows whether
    # the row has sizes. Every other GET writes at most a CACHE (a page
    # render, triage.json, a dimension check, the report PDF rebuilt from the
    # approved exports) - derived from what is already there, never a
    # decision - and a viewer may cause those.
    _VIEWER_GET_REFUSED = re.compile(r"^/api/bids/[^/]+/plans$")

    def _viewer_refused(method: str, path: str) -> bool:
        if not (path.startswith("/api/") or path.startswith("/cutfill/api/")):
            return False
        if method in _VIEWER_READ:
            return method != "OPTIONS" and bool(_VIEWER_GET_REFUSED.match(path))
        return path not in _VIEWER_WRITE_OK

    # Ownership is enforced HERE rather than in each route. There are twenty-odd
    # /api/job/{id}/... routes and more will be added; a check that has to be
    # remembered per route is a check that eventually is not.
    # ANY path segment, not only a well-formed hex id: the first question
    # below is "does the registry hold this row", and an id that could never
    # have one is exactly the kind that must not fall through to the router.
    _JOB_PATH = re.compile(r"^/api/job/([^/]+)(?:/|$)")
    # The cut / fill engine keys a job's project "job_<job id>", so the same
    # ownership rule covers its routes; everything else under its API (the
    # project list, projects with other ids, the local file browser) is the
    # team's and never a client's.
    _CUTFILL_PATH = re.compile(r"^/cutfill/api/projects/job_([^/]+)(?:/|$)")

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
        m = _JOB_PATH.match(path) or _CUTFILL_PATH.match(path)
        if not m:
            if (path.startswith("/cutfill/api/") and user is not None
                    and not staff and not path.startswith("/cutfill/api/health")):
                return _StarJSON({"detail": "unknown job"}, status_code=404)
            return None
        with JOBS_LOCK:
            j = JOBS.get(m.group(1))
        if j is None:
            return _StarJSON({"detail": "unknown job"}, status_code=404)
        if user is None or staff:
            return None                 # single-password mode, or our own team
        if _can_open(j, user, staff):
            return None                 # shared with all (a demo / shared set), or theirs
        # a stranger's job, or an owner-less row that is staff-only
        # (_shared_for_all): the same 404 either way
        return _StarJSON({"detail": "unknown job"}, status_code=404)

    # THE CLIENT GATE (critic legacy-3, WRONG-1 / RISK-1). A tab still running an OLDER page's JavaScript - v1.8.5 left
    # open across the deploy - keeps its session and could write: its approve was stamped `src_marks` over rows it never
    # marked (the old dirt then counted as nobody's and the next approve dropped it silently), and its calcstate save
    # wiped `rows` / `cf` / prices / trades. Every page this app serves installs webui/client.js FIRST, which puts
    # CLIENT_HEADER on every state-changing request (fetch, XMLHttpRequest, sendBeacon); a write under the host API or
    # the cut / fill API without it is refused here, AFTER sign-in and ownership (their answers stand as they were), and
    # nothing is written. Exempt: the sign-in / sign-out / change-password forms. A script that writes (seed-demo.sh)
    # sends the header like the page does.
    def _client_gate(request, path: str):
        if request.method in ("GET", "HEAD", "OPTIONS") or path in _CLIENT_EXEMPT:
            return None
        if not (path.startswith("/api/") or path.startswith("/cutfill/api/")):
            return None
        try:
            ok = int(str(request.headers.get(CLIENT_HEADER) or "0").strip()) >= CLIENT_MIN
        except ValueError:
            ok = False
        if ok:
            return None
        return _StarJSON({"detail": CLIENT_OLD_WORDS, "client": "old"}, status_code=409)

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
                blocked = _forbidden_job(path, None, True) or _client_gate(request, path)
                if blocked is not None:
                    return blocked
                tok = _CF_CALLER.set((None, True))
                try:
                    return await call_next(request)
                finally:
                    _CF_CALLER.reset(tok)

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
            # The role, once per request: admin is what staff always was, so
            # tk_staff is read through it (users.is_staff says the same).
            # None in single-password mode - there is no account to have one.
            _role = _users.role_of(user) if user else None
            request.scope["tk_role"] = _role
            request.scope["tk_staff"] = _role == "admin"
            if user:
                _users.note_seen(user)      # throttled, never raises

            if user and _users.must_change(user) and path not in _RESET_OK:
                if path.startswith("/api/"):
                    return _StarJSON(
                        {"detail": "choose a new password before continuing"},
                        status_code=403)
                return _StarRedirect("/change-password", status_code=303)

            # Before the ownership check on purpose: the answer depends only
            # on the caller's role, so it says nothing about whether a job id
            # exists or whose it is.
            if _role == "viewer" and _viewer_refused(request.method, path):
                return _StarJSON({"detail": VIEWER_READ_ONLY}, status_code=403)

            blocked = _forbidden_job(path, user, request.scope["tk_staff"])
            if blocked is None:
                blocked = _cf_linked_gate(path, user, request.scope["tk_staff"])
            if blocked is None:
                blocked = _client_gate(request, path)
            if blocked is not None:
                return blocked
            # who is asking, for the cut / fill hooks that reach a LINKED plan set (_cf_job_readable): they run
            # inside the engine's routes, which carry no Request (critic bid-content-1, WRONG-2)
            tok = _CF_CALLER.set((user, request.scope["tk_staff"]))
            try:
                return await call_next(request)
            finally:
                _CF_CALLER.reset(tok)

    app.add_middleware(_SessionAuth)

    @app.get("/terms")
    def terms_page():
        return FileResponse(ROOT / "webui" / "terms.html", media_type="text/html")

    @app.get("/privacy")
    def privacy_page():
        return FileResponse(ROOT / "webui" / "privacy.html", media_type="text/html")

    @app.get("/welcome")
    def welcome_page():
        return FileResponse(ROOT / "webui" / "welcome.html", media_type="text/html")

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
        # with no accounts there is one password whatever name is typed: one count for every name
        tried = name if _users.load() else ""
        if _throttled(ip, tried):
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
            _note_fail(ip, tried)
            _log_failed_login(name)
            # One message for both cases, so this cannot be used to find out
            # which of these twenty addresses has an account.
            raise HTTPException(401, "That email and password do not match.")

        # Successful sign-ins only: a failed one would record whatever was
        # typed into the name box, and people type passwords there.
        _users.note_seen(who, login=True)
        _audit(request, "login", user=who)
        r = JSONResponse({"ok": True, "user": who})
        # Secure only when the request actually arrived over TLS, or a laptop
        # on plain http could never hold a session.
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        r.set_cookie(SESSION_COOKIE, _sign_session(who),
                     max_age=SESSION_DAYS * 86400, httponly=True,
                     samesite="lax", secure=(proto == "https"), path="/")
        return r

    @app.post("/api/logout")
    def logout(request: Request):
        # signs this account out EVERYWHERE: a copied cookie must not outlive the sign-out (users.bump_session)
        u = request.scope.get("tk_user")
        if u:
            _users.bump_session(u)
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
        # Re-issue the cookie so the session outlives the change cleanly. The change moved the account's session
        # counter on (users.save), so every OTHER session of it has ended; this browser's new cookie carries the new one.
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
                "accounts": bool(_users.load()),
                # admin / estimator / viewer; null with no account signed in
                # (single-password mode, or a laptop with no auth at all)
                "role": request.scope.get("tk_role")}


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


def _is_viewer(request) -> bool:
    """A signed-in viewer. False for a request the middleware never saw (a
    test or script calling a route function directly passes None)."""
    return request is not None and request.scope.get("tk_role") == "viewer"


def _audit(request, action: str, *, job=None, page=None, detail=None,
           name=None, user=None) -> None:
    """One line in the audit log (takeoff_engine/audit.py) for something this
    request DID. The actor is the signed-in account unless `user` says
    otherwise (sign-in, where the route is public and the scope has no user
    yet); `name` defaults to the job's display name. Best effort by
    construction: an audit failure costs the line, never the request.

    Nothing secret goes in: callers pass names, ids, a page and a short
    detail, and no route passes a password, temporary or otherwise."""
    try:
        if user is None and request is not None:
            user = _who(request)
        if name is None and job is not None:
            j = JOBS.get(job)
            if j is not None:
                name = _display_name(j.get("path", ""))
        _audit_log.write({"user": user, "action": action, "job": job,
                          "name": name, "page": page, "detail": detail})
    except Exception:
        pass


def _n(n, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


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
#
# ROLES (2026-09-25): staff is now the ADMIN role, a non-staff account an
# ESTIMATOR, and VIEWER is new (read-only, enforced in _SessionAuth). Every
# rule above reads "admin" where it says staff. /api/users/{u}/role sets any
# of the three; /api/users/{u}/staff still works and maps onto them. Every
# account change is written to the audit log (takeoff_engine/audit.py) with
# the actor in `user` and the account changed in `name` - never a password.

_USERNAME_MAX = 120
_ROLE_WORDS = "Use admin, estimator or viewer."


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
    """Every account, with what the team page shows beside it: the role,
    when it was last seen and last signed in (users.activity - null until
    it first signs in after this shipped), and how many registry jobs it
    owns."""
    _account_gate(request)
    act = _users.activity()
    owned: dict = {}
    with JOBS_LOCK:
        for j in JOBS.values():
            o = j.get("owner")
            if o is not None:
                owned[o] = owned.get(o, 0) + 1
    rows = []
    for r in _users.list_users():
        a = act.get(r["user"]) or {}
        rows.append(dict(r, last_active=a.get("last_active"),
                         last_login=a.get("last_login"),
                         projects=owned.get(r["user"], 0)))
    return {"me": _who(request), "users": rows}


@app.post("/api/users")
async def api_users_add(request: Request):
    _account_gate(request)
    body = await request.json()
    name = str(body.get("username") or "").strip()
    if not name or len(name) > _USERNAME_MAX or any(c.isspace() for c in name):
        raise HTTPException(
            400, "Enter the sign-in name as one word, usually their email address.")
    # `role` wins over the older `staff` checkbox when both are sent
    role = None
    if body.get("role") not in (None, ""):
        role = _users.norm_role(body.get("role"))
        if role is None:
            raise HTTPException(400, "Unknown role. " + _ROLE_WORDS)
    elif body.get("staff"):
        role = "admin"
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
    rec = _users.add_user(name, pw, role=role or "estimator", must_change=True)
    _audit(request, "user_add", name=rec["user"], detail=rec["role"])
    return {"ok": True, "user": rec["user"], "staff": rec["staff"],
            "role": rec["role"],
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
    _users.forget_activity(name)
    _audit(request, "user_remove", name=name)
    return {"ok": True, "removed": name, "left": len(_users.load())}


@app.post("/api/users/{username}/reset")
def api_users_reset(username: str, request: Request):
    _account_gate(request)
    name = _users.find(username)
    if name is None:
        raise HTTPException(404, "no such account")
    pw = _users.temp_password()
    rec = _users.add_user(name, pw, must_change=True)     # keeps the role
    _audit(request, "user_reset_password", name=name)   # never the password
    return {"ok": True, "user": rec["user"], "staff": rec["staff"],
            "role": rec["role"], "must_change": True, "temp_password": pw}


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
    old = _users.role_of(name)
    _users.set_staff(name, flag)
    new = _users.role_of(name)
    if new != old:
        _audit(request, "user_role", name=name, detail=f"{old}->{new}")
    return {"ok": True, "user": name, "staff": flag, "role": new}


@app.post("/api/users/{username}/role")
async def api_users_role(username: str, request: Request):
    """Set an account's role: {"role": "admin" | "estimator" | "viewer"}.

    The staff route's rules, in roles, and one answer changed on purpose:
    demoting the LAST admin is 409, not 400 - it is a conflict with the
    state of the box (make someone else an admin and it goes through), not
    a malformed request. That check runs BEFORE the self rule, so the last
    admin trying to step down is told the real reason. Your own role stays
    off limits otherwise (see api_users_staff: it happened to the owner on
    day one), even with other admins about. Re-stating an account's current
    role is a 200 that changes and records nothing."""
    _account_gate(request)
    try:
        body = await request.json()
    except ValueError:
        body = None
    role = _users.norm_role(body.get("role")) if isinstance(body, dict) else None
    if role is None:
        raise HTTPException(400, "Unknown role. " + _ROLE_WORDS)
    name = _users.find(username)
    if name is None:
        raise HTTPException(404, "no such account")
    old = _users.role_of(name)
    if old == "admin" and role != "admin" and _staff_count(exclude=name) == 0:
        raise HTTPException(
            409, f"{name} is the last admin on this server. Make someone else "
                 "an admin first.")
    if name == _who(request):
        raise HTTPException(
            400, "You cannot change your own role. Ask another admin to do it.")
    if role != old:
        _users.set_role(name, role)
        _audit(request, "user_role", name=name, detail=f"{old}->{role}")
    return {"ok": True, "user": name, "role": role, "staff": role == "admin"}


@app.get("/api/team/activity")
def team_activity(request: Request, limit: int = 50):
    """The audit log, newest first: who did what across the box. Admins
    only, gated exactly like /api/users (404 to anyone else, 409 on a
    single-password box, where there is no team to report on)."""
    _account_gate(request)
    return {"events": _audit_log.read(limit)}


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


def _bidsheet_payload(lines, scope, request: Request, added=()) -> dict:
    return {"lines": lines, "scope": scope,
            "seat": "account" if _who(request) else "install",
            "units": list(_bidsheet.UNITS), "default": _bidsheet.default_lines(),
            "line_max": _bidsheet.LINE_MAX, "added": list(added or ())}


@app.get("/api/bidsheet")
def get_bidsheet(request: Request):
    """This seat's bid sheet: ordered lines with units and the words that
    file a takeoff row onto them (a client's printed sheet until the seat
    saves its own). `scope` says where it came from, like the letterhead.
    `added` names the lines a sheet saved on an older template gained on
    this read (bidsheet.upgrade_lines) until the seat saves it."""
    lines, scope, added = _bidsheet.resolve_full(_who(request))
    return _bidsheet_payload(lines, scope, request, added)


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
# The words are the owner's (2026-10-01): a plain flag, no "BETA". The names
# (BETA_*, /api/beta-notice, #betaBanner) stay so nothing that reads them moves.
# BETA_CHIP is the chip's face, BETA_SHORT its hover, BETA_FLAG the full line
# (sign-in / change-password / Terms / Privacy bars), BETA_LONG the export
# line: the full line plus the one extra caution the old notice carried.
BETA_CHIP = "Automated detection can be wrong"
BETA_SHORT = "Double-check every quantity against the drawings."
BETA_FLAG = BETA_CHIP + ". " + BETA_SHORT
BETA_LONG = BETA_FLAG + " Line and scale readings can also be incomplete."

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
        # filtered, which is exactly how this behaved before. An owner-less
        # row outside the demo / shared sets is staff-only (_shared_for_all).
        if not _can_open(j, me, staff):
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
            "shared": _shared_for_all(j),
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
    return _can_open(j, me, staff)


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
def reopen_job(job_id: str, request: Request = None):
    """Reopen a document already on this box, by job id.

    Same reply shape as /api/open-path and /api/upload so the UI has one code
    path for all three. By id, never by path - a hosted box must not take a
    filesystem location from the client.

    A GET that can WRITE: a row with no page sizes is re-registered (below),
    which may mint a new registry row and start classification. So a viewer
    is refused that branch - and only that one; a row with sizes (every row
    registered since sizes existed) opens for a viewer like for anyone.
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
        if _is_viewer(request):
            raise HTTPException(403, VIEWER_READ_ONLY)
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
        # an owner-less row outside the demo / shared sets is staff-only
        if not _can_open(j, me, staff):
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
        # sheet in front of it: a set's page 0 is often a title block and the
        # plan the demo is about sits later. Everything else still pictures
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
            # open to every account; an owner-less row that is staff-only
            # (_shared_for_all) is NOT shared, and only staff ever see it
            "shared": _shared_for_all(j),
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


def _csv_flag_line() -> str:
    """BETA_LONG as one CSV row (one cell: quoted only if it ever needs to be), for the CSVs the server writes."""
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    _csv.writer(buf, lineterminator="").writerow([BETA_LONG])
    return buf.getvalue()


@app.get("/api/beta-notice")
def beta_notice():
    """The UI renders this rather than hard-coding it, so the banner on screen
    and the stamp on a printed report are always the same words."""
    return {"chip": BETA_CHIP, "short": BETA_SHORT, "flag": BETA_FLAG,
            "long": BETA_LONG}


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
                     "shows": str(meta.get("shows") or ""),
                     # the tool the card's sheet is for ("flooring"), or ""
                     "tool": str(meta.get("tool") or "")}
    return out


DEMO_PLANS = _load_demo_plans()


def _load_shared_names() -> frozenset:
    """demo-plans.json `shared_names`: the EXACT display names of the two shared
    plan sets' sheets (Loves Lane civil sheets, Belle Farms), which stay open to
    every account even when they are not demo cards. Case-folded: compared
    exactly, case aside. Prefixes were too wide - an owner-less "C4.0-SITE-
    GRADING-PLAN for <someone>.pdf" opened for every account (critic
    bid-content-1, RISK-3). Absent or unreadable is empty, which fails CLOSED:
    an owner-less row is then open to everyone only as a demo card."""
    try:
        raw = json.loads(DEMO_MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return frozenset()
    got = raw.get("shared_names") if isinstance(raw, dict) else None
    if not isinstance(got, list):
        return frozenset()
    return frozenset(s.strip().casefold() for s in got if isinstance(s, str) and s.strip())


SHARED_NAMES = _load_shared_names()


def _shared_for_all(j: dict) -> bool:
    """Is this owner-less row open to EVERY account?

    Owner None has always been the SHARED marker, and every signed-in account
    could list, open, render and detect such a row. But the live store holds
    owner-less rows seeded under older manifests (cards since removed because
    they are other parties' drawings), and shrinking demo-plans.json did not
    take those off anybody's screen: they stayed on /api/projects as ownerless
    projects, on /api/recent, and opened through every job route. So since
    2026-10-01 an owner-less row is open to all only when its display name is
    exactly (case aside) a demo card or one of the shared sets' sheet names;
    any other owner-less row is STAFF-ONLY. Hidden, never deleted - staff still see and manage it."""
    if j.get("owner") is not None:
        return False
    name = _display_name(j.get("path", ""))
    folded = name.casefold()          # exact, case aside: no trimming either
    return folded in SHARED_NAMES or folded in {n.casefold() for n in DEMO_PLANS}


def _can_open(j: dict, me, staff) -> bool:
    """The one row-visibility rule: staff (which single-password mode counts
    as, _is_staff) see everything; anybody else sees their own rows and the
    rows shared with all. A nameless caller who is not staff owns nothing."""
    if staff:
        return True
    owner = j.get("owner")
    if owner is None:
        return _shared_for_all(j)
    return me is not None and owner == me


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
#     15 registered jobs for the substation sheet and 14 for Belle Farms
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

    The cut / fill engine's own store for the job (JOBS_DIR/cutfill/job_<id>:
    datum and limit, typed heights, the reading cache, the analysis and the
    computed volumes) goes with it, and its cached Project is evicted, so
    Analyze after a reset reads the sheet again instead of serving the old
    volumes as current (audit 2026-09-26, D07: a datum set by one visitor
    survived a demo open; Analyze after reset answered "done" in 0.02 s).
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
    cf = _cutfill_forget(job_id)
    removed += cf["removed"]
    failed += cf["failed"]
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
    fresh jobs are how this box reached 15 rows for a substation sheet and 14 for Belle
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
            # the tool the card's sheet is for, when the manifest names one
            "tool": meta.get("tool") or "",
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
def demo_open(name: str, request: Request = None):
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
    _audit(request, "demo_open", job=job_id, name=name)
    return {"job_id": job_id, "pages": pages, "kinds": kinds, "sizes": sizes,
            "name": name,
            # the sheet the card described, clamped to the set as it is now
            "demo_page": page if 0 <= page < pages else 0,
            # the tool the card's sheet is for ("flooring"): the UI opens
            # that panel on the fresh document; nothing is detected here
            "tool": meta.get("tool") or "",
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
            # and 54 for one substation sheet - months of restarts and killed dev
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
    # simply opening the file: measured 16.3 s for a 20-page client set and
    # 38.0 s for the 15-page Res-A civil, all of it inside the POST /open-path
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
    reg = _register(_client_pdf(body.path), owner=_who(request))
    # the display name only: a filesystem path is not the audit log's business
    _audit(request, "project_open_path", job=reg.get("job_id"),
           name=reg.get("name"), detail=_n(reg.get("pages") or 0, "page"))
    return reg


def _upload_check(raw: bytes, name: str) -> int:
    """Refuse what cannot become a job, in plain words, before a byte is
    written: not a .pdf name, not PDF bytes, a file pymupdf cannot open
    (truncated, damaged), or one with no pages. Returns the page count."""
    if not raw:
        raise HTTPException(400, "that file was empty")
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, f"{name} is not a PDF - plans have to be a PDF")
    if raw[:5] != b"%PDF-":
        raise HTTPException(400, "that is not a PDF - plans have to be a PDF")
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
        try:
            n = int(doc.page_count)
            if n < 1:
                raise ValueError("no pages")
            doc[0].rect          # the first page must at least open
        finally:
            doc.close()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "that PDF could not be opened - the file is damaged "
                                 "or incomplete; export it again and upload that")
    return n


def _pdf_or_400(raw: bytes, filename: str | None) -> None:
    """Refuse an upload that is not a readable PDF, by CONTENT: the header
    (%PDF- within the first 1 KB, where a preamble may sit) and pymupdf
    opening it as a PDF with at least one page. Raises 400 with the file's
    name; a caller that gets past this can write the bytes."""
    name = Path(filename or "").name or "that file"
    if b"%PDF-" not in (raw or b"")[:1024]:
        raise HTTPException(400, f"{name} is not a PDF - plans have to be a PDF")
    try:
        doc = pymupdf.open(stream=raw, filetype="pdf")
        try:
            if not doc.is_pdf or doc.page_count < 1:
                raise ValueError("no pages")
        finally:
            doc.close()
    except Exception:
        raise HTTPException(400, f"{name} could not be read as a PDF - the file is damaged or incomplete; export it again and upload that")


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

    Refused BEFORE anything is written (audit 2026-09-26, D35: a damaged
    PDF answered 500 from inside pymupdf and left its bytes behind as an
    orphan upload; a .txt was accepted as a job): a name that is not .pdf,
    bytes that are not a PDF, a PDF pymupdf cannot open, or one with no
    pages, each answer 400 with the reason.
    """
    raw = await file.read()
    stem = Path(file.filename or "plans.pdf").name
    _upload_check(raw, stem)
    # the same refusal by content (header within the first 1 KB), from the UI slice: a second
    # look costs nothing and nothing is written before both pass
    _pdf_or_400(raw, file.filename)
    digest = hashlib.sha256(raw).hexdigest()[:12]
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
    reg = _register(dest, classify=bool(classify), owner=owner)
    _audit(request, "project_upload", job=reg.get("job_id"),
           name=reg.get("name"), detail=_n(reg.get("pages") or 0, "page"))
    return reg


PAGE_IMAGE_MAX_PX = 160_000_000      # a 48 x 36 in sheet at 300 dpi is 155.5 million; about 480 MB of RGB


@app.get("/api/job/{job_id}/page/{page}/image")
def page_image(job_id: str, page: int, dpi: int = 130):
    job = JOBS.get(job_id) or _404()
    # The page rasterises in the web process and is cached per dpi: any dpi was taken before, and
    # `?dpi=5000` asked for tens of GB (D41 follow-up). The page asks for 130 (IMG_DPI) and 28 (THUMB_DPI),
    # and maps points to pixels with the dpi it asked for, so a dpi outside 10-300, or a picture past
    # PAGE_IMAGE_MAX_PX, is refused: never drawn at another dpi than asked (it would be mis-placed).
    if not 10 <= dpi <= 300:
        raise HTTPException(422, "A page picture is drawn at 10 to 300 dpi")
    cache = _job_dir(job_id) / f"p{page}_{dpi}.png"
    if not cache.exists():
        doc = pymupdf.open(job["path"])
        r = doc[page].rect
        if (r.width * dpi / 72) * (r.height * dpi / 72) > PAGE_IMAGE_MAX_PX:
            doc.close()
            raise HTTPException(413, "This page is too big to draw at that size")
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
# Default cap is 4, not cpu_count-1: a heavy sheet (Res-A civil p3, 412k
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
        # import, measured 2026-08-30 on a 54-page set's p42) as done: the status
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
def detect(job_id: str, page: int, request: Request = None):
    job = JOBS.get(job_id) or _404()
    if job["status"].get(page) == "running":
        _audit(request, "sheet_detect", job=job_id, page=page,
               detail="already running")
        return {"status": "running", "queued": _is_queued(job_id, page)}
    if (_job_dir(job_id) / f"p{page}_result.json").exists() \
            and not _result_stale(job_id, page):
        job["status"][page] = "done"
        _audit(request, "sheet_detect", job=job_id, page=page,
               detail="cached result")
        return {"status": "done"}
    _enqueue_detect(job_id, page)
    queued = _is_queued(job_id, page)
    _audit(request, "sheet_detect", job=job_id, page=page,
           detail="queued" if queued else "started")
    return {"status": "running", "queued": queued}


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
# 2026-08-30 (Res-A civil 15p, a 48p civil set, a 54p building set; the synthetic
# edge of each rule is pinned in tests/test_triage.py):
#   * counts are CAPPED per family (min(count, cap)) so one repeated word
#     cannot carry a page: the 54p set's SD1, a structural-detail sheet, says FILL
#     five times in compaction notes and must stay "other";
#   * bare GRADE, RIM, INV and ELEVATION are deliberately NOT signals -
#     measured ambiguity: "GRADE 60" is rebar steel on that SD1, RIM is a
#     rim joist on its SD1/SD3 (x11/x26), and ELEVATION is a building
#     facade on every one of its A3.x sheets;
#   * DRAIN scores +1 where DRAINAGE scores +2: roof drains put DRAINx3 on
#     its A4.10, an architectural wall-section sheet.

TRIAGE_VERSION = 4          # bump when the table below changes - cached
                            # (3: the sheet number from takeoff_engine/sheet_no.py - subdivision A 3.0 read "LOT910";
                            #  4: that reader back on the old ranking, cacd919 - critic 5, m-g)
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
# the 54p set's A1.02 - an architectural index sheet, 3,413 words - name-drops
# GRADING, EXCAVATION and DRAINAGE and needs the prefix to outweigh the
# borrowed vocabulary. Every other letter (V survey, G general, T title...)
# is neutral: no evidence either way.
_TRIAGE_CIVIL_PREFIX = 4
_TRIAGE_OTHER_PREFIX = -6

# Tier thresholds. A bare C prefix (+4) lands in "maybe", not "core": a C
# detail sheet with no excavation words on it is plausibly relevant, not
# certainly - core demands keyword evidence. Calibrated so that on the 54p set
# (54 pages, 3 civil) exactly the civil sheets are core, and on Res-A
# (15 pages, all civil) all 15 are.
_TRIAGE_CORE_MIN = 6    # score >= 6  -> "core"
_TRIAGE_MAYBE_MIN = 2   # score >= 2  -> "maybe"; below -> "other"

# BUILDING PLANS THIS PRODUCT WORKS ON. The excavation score above is one
# axis; a floor plan (flooring rooms, door and window counts) and a
# foundation plan (footings, structure excavation) are the other, and both
# were tiered "looks like another trade" - house set A2.1 at -12, the Res-A
# set's foundation plan (page 14, printed S1-1.3) at 0 - so "detect only the
# plan pages" would have skipped exactly the sheets those tools need (audit
# D38). A page is a PLAN by its TITLE PHRASE, never by vocabulary: the
# Res-A set's footing-schedule sheet says FOOTING seven times and SLAB ten
# and is a schedule; its foundation plans say FOUNDATION PLAN. A sheet that
# lists many "<X> PLAN" titles is an index or cover, not a plan.
_TRIAGE_PLAN_KINDS = [
    ("floor plan",      re.compile(r"\b(?:FLOOR|FLOORING|FINISH)\s+PLAN\b")),
    ("foundation plan", re.compile(r"\b(?:FOUNDATION|FOOTING|SLAB)\s+PLAN\b")),
]
_TRIAGE_PLAN_TITLE = re.compile(r"\b[A-Z]+\s+PLAN\b")
_TRIAGE_INDEX_TITLES = 6    # this many distinct "<X> PLAN" titles = an index sheet


def triage_score(text: str, sheet_no: "str | None") -> dict:
    """Score one page's TEXT LAYER for excavation relevance. Pure - no I/O,
    nothing but the constant table above - so tests/test_triage.py can run
    it on synthetic word lists with no PDF and no server.

    Returns {"tier", "score", "words", "hits", "kind"} where hits is the
    full term-by-term computation [[label, count, points], ...] sorted by
    |points| descending, so a tooltip can lead with the strongest evidence,
    and kind names a building plan ("floor plan" / "foundation plan") or is
    None. A building plan that is not excavation "core" is tier "plan":
    kept in the strip, never dimmed as another trade. Never a quantity: the
    only outputs are a tier and the words that earned it.

    A page with NO text abstains: tier "unknown". A raster/scanned sheet is
    invisible to a text probe, and calling it "other" would dim a page this
    code knows nothing about.
    """
    T = " ".join((text or "").upper().split())
    if not T:
        return {"tier": "unknown", "score": 0, "words": 0, "hits": [], "kind": None}
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
    kind = None
    if len(set(_TRIAGE_PLAN_TITLE.findall(T))) < _TRIAGE_INDEX_TITLES:
        for name, rx in _TRIAGE_PLAN_KINDS:
            if rx.search(T):
                kind = name
                break
    if kind and tier != "core":
        tier = "plan"
    return {"tier": tier, "score": score, "words": words, "hits": hits,
            "kind": kind}


def _triage_sheet_no(jd: Path, page: int, mtime=None) -> tuple:
    """(known, sheet number) from a set's triage sidecar: the number the page list shows (takeoff_engine/sheet_no.py,
    which fixed subdivision A 3.0 read as "LOT910"). known is False when the set has no current triage - another version, or
    (given `mtime`, the registry's PDF mtime) another copy of the PDF (critic 4, m7) - then the detection's own
    reading stands."""
    try:
        t = json.loads((jd / "triage.json").read_text())
        if t.get("version") != TRIAGE_VERSION or (mtime is not None and t.get("mtime") != mtime):
            return False, None
        row = next((r for r in t.get("pages") or [] if isinstance(r, dict) and r.get("page") == int(page)), None)
        return (True, row.get("sheet_no")) if row is not None else (False, None)
    except (OSError, ValueError):
        return False, None


@app.get("/api/job/{job_id}/triage")
def job_triage(job_id: str):
    """Per-page excavation-relevance tiers for the whole set, with evidence.

    Cheap by construction - text layer plus the title-block sheet-number
    probe, page by page; no detection, no AI, no render. Measured first-call
    cost on this box: 5.0 s for 15 pages (Res-A), 12.0 s for 48 (a civil set),
    5.7 s for 54 (the 54p set); afterwards served from the triage.json sidecar,
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
                sn = read_sheet_number_shown(pg)
            except Exception:
                sn = None
            row = triage_score(txt, sn)
            row["page"] = i
            row["sheet_no"] = sn
            pages.append(row)
    finally:
        doc.close()
    counts = {"core": 0, "maybe": 0, "plan": 0, "other": 0, "unknown": 0}
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
def reset_job(job_id: str, preview: bool = False, request: Request = None):
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
    # The cut / fill engine's store for this job is OPERATOR WORK as much as
    # cache (datum, limit, typed heights beside the reading cache and the
    # volumes), so the preview names it with a count of its own and the
    # dialog says what goes.
    cf_files, cf_bytes = _cutfill_store_size(job_id)
    counts["cutfill"] = cf_files
    freed += cf_bytes

    out = {"ok": True, "counts": counts, "total": len(files) + cf_files,
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
    # the preview above changes nothing and is not recorded; this is
    _audit(request, "project_reset", job=job_id,
           detail=_n(removed, "file") + " removed")
    return out


@app.delete("/api/job/{job_id}")
def delete_job(job_id: str, request: Request):
    """Remove a document from this box entirely.

    `reset` throws away everything DERIVED and deliberately keeps the source
    PDF, so a plan set reset in front of a client is still sitting on the box
    afterwards, still listed, still openable by anyone who can log in. This is
    the other half, and the one you want when a client's live bid set should
    not outlive the meeting.

    Deletes the job directory, the registry row, the cut / fill engine's
    store for the job (and its cached Project, which held the PDF open -
    the reason 6 of 6 deletes after the Earthwork tab kept the client's PDF
    on Windows, audit 2026-09-26, D06), and the uploaded PDF. It will NOT
    unlink a source that lives outside the job store - a sample opened by
    path is somebody's actual file - and it will not unlink a
    content-addressed upload that another job still points at, because two
    people uploading identical bytes share one file on disk. Whatever
    happened to the PDF is said in the reply: `pdf_removed` and, when it
    stayed, `pdf_why` in plain words for the screen - except that a file
    another job shares reads as removed: the reply never reveals that
    another document exists.
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
    # the engine's handles on the PDF are closed here, BEFORE the unlink below
    cf = _cutfill_forget(job_id)
    removed += cf["removed"]

    with JOBS_LOCK:
        JOBS.pop(job_id, None)
        shared_source = any(x.get("path") == str(src) for x in JOBS.values())
        _save_registry()

    pdf_removed = False
    pdf_why = None
    # THE REPLY SPEAKS ONLY OF THE CALLER'S OWN PROJECT (2026-10-02). When another account uploaded the identical file,
    # the bytes stay for that account, and the reply used to say "another document on this server is the same file":
    # in bidding, that tells a contractor a rival holds the same bid set. Now that case reads exactly like a removal
    # (this project and its copy of the PDF are gone for the caller); only the ledger records that the bytes stayed.
    kept_for_other = False
    if not (_UPLOAD_PREFIX.match(src.name) and src.parent.resolve() == JOBS_DIR.resolve()):
        pdf_why = "its PDF stays: it was opened from its own folder, not uploaded here"
    elif shared_source:
        kept_for_other = True
        pdf_removed = True
    elif not src.exists():
        pdf_removed = True              # already gone: nothing of it is left
    else:
        # Windows keeps a file another handle holds open; the engine's were
        # just closed, so a short retry covers the release
        for _i in range(20):
            try:
                src.unlink()
                pdf_removed = True
                break
            except OSError:
                time.sleep(0.05)
        if not pdf_removed:
            pdf_why = "its PDF could NOT be deleted - it is still on this server"
    # training ledger (observation only): the job's events end here, and a
    # trainer reading the ledger later should know the source went away
    ledger.record("job_deleted", job_id, None, None,
                  {"files_removed": removed, "pdf_removed": pdf_removed and not kept_for_other,
                   "cutfill_removed": cf["dir_removed"]},
                  None)
    # the row is gone by now, so the name is passed rather than looked up
    _audit(request, "project_delete", job=job_id, name=name,
           detail=_n(removed, "file") + " removed"
                  + ("; PDF removed" if pdf_removed else ""))
    return {"ok": True, "name": name, "files_removed": removed,
            "pdf_removed": pdf_removed, "pdf_why": pdf_why,
            "cutfill_removed": cf["dir_removed"], "cutfill_failed": cf["failed"]}


@app.get("/api/job/{job_id}/page/{page}/confidence")
def page_confidence(job_id: str, page: int):
    """Page-level trust signals for the review banner. Read-only.

    Two facts the detect result does not already carry, and both decide
    whether an operator should believe this sheet's numbers:

      * NOT TO SCALE when the sheet prints no ratio at all. ScaleResult
        .to_dict() returns None when there is no `best`, so `has_nts` is
        dropped in exactly the case where it matters most.
      * the cut / fill engine's own reading of the sheet, when it has one
        (the job's engine project, this page's stored analysis): spot grades
        that carry no height, and datum notes that disagree. "pending" until
        the sheet has been read in Cut / fill.

    Text layer only - no OCR, no rasterising - and nothing here feeds a
    quantity. It costs one page parse (0.4-2.3 s measured), so the UI
    fetches it once per page and upgrades the banner when it lands, rather
    than holding up detect. Deliberately NOT folded into the detect result:
    that would change `detect_page.engine_sig` and stale every cached page
    in the corpus for a reporting change.
    """
    from takeoff_engine.scale_detect import _NTS

    job = JOBS.get(job_id) or _404()
    doc = pymupdf.open(job["path"])
    try:
        if page < 0 or page >= doc.page_count:
            raise HTTPException(404, "no such page")
        pg = doc[page]
        has_nts = bool(_NTS.search(pg.get_text()))
    finally:
        doc.close()
    # The spot-grade datum check went with the earthwork calculator: the
    # cut / fill engine (cutfill/) reads spot grades and datum notes itself.
    spots: dict = {"state": "pending"}
    datum: dict = {"state": "pending"}
    try:
        cp = _cutfill._projects.get(f"job_{job_id}")
        ap = Path(cp.analysis_path(f"d0p{int(page)}")) if cp else None
        if ap is not None and ap.exists():
            an = json.loads(ap.read_text(encoding="utf-8"))
            labs = [l for l in (an.get("labels") or []) if l.get("kind") in ("spot", "wall", "floor")
                    and l.get("used") is not False]
            spots = {"state": "checked", "n": len(labs), "unresolved": sum(1 for l in labs if l.get("z") is None)}
            vals = []
            for c in an.get("datum_candidates") or []:
                d = c.get("datum")
                if d and len(d) == 2 and (float(d[0]), round(float(d[1]), 2)) not in vals:
                    vals.append((float(d[0]), round(float(d[1]), 2)))
            used = an.get("datum_used")
            datum = {"state": "checked", "conflict": len(vals) > 1,
                     "used": [float(used[0]), float(used[1])] if used else None,
                     "notes": [[a, b] for a, b in vals],
                     "off": bool((an.get("resolution") or {}).get("datum_off"))}
    except Exception:
        spots = {"state": "failed"}
        datum = {"state": "failed"}
    return {"has_nts": has_nts, "spots": spots, "datum": datum}


# ---------- the scale a human chose --------------------------------------
# Every foot, square foot and cubic yard on a sheet is one multiplication away
# from `ft_per_paper_inch`, and on a quarter of the pages that return a scale
# at all the sheet prints more than one - a plan and its details. The review
# UI lets the estimator pick which one governs the drawing and redraws every
# number on screen from it (webui/app.js renderScalePick). Until this file
# learned to read that pick, `approve` recomputed every length from the
# ENGINE's choice, so the screen and the exported takeoff disagreed with no
# symptom - Res-A landscaping L604 exported 4x low - and the export still
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
#         a foundation addendum sheet S-01, page 0). The 0.3333
#         is the engine reading `Scale 3:1` as if it were 3" = 1'-0"; read
#         as a ratio that caption means a 3x ENLARGEMENT (0.0278 ft/in),
#         so it is quoted here as the observed extreme, not as a scale we
#         vouch for.
#       - CONVENTION: the largest scale an architect's rule carries is
#         3" = 1'-0" = 0.3333 ft/in (see scale_detect._scan, which measured
#         it against drawn geometry on Res-A landscaping L804).
#       - full size is one step finer again, so the floor sits 4x below the
#         smallest thing this corpus reads and still admits a full-size
#         detail. Under it the drawing is ENLARGED past life size (2:1,
#         3:1, 4:1 shop detailing), which is not geometry anyone takes
#         quantities off.
#     It is 1/12 rounded DOWN to four places, so the number this module
#     PRINTS in its refusal is itself inside the band when typed back.
#   * CEILING 1000 ft/in is 4x the corpus maximum (1" = 250' on
#     a client folder's residence B civil set (5.8.26), engine
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


_PICK_LAST: dict = {}   # pick file path -> its last whole reading (a read caught mid-write answers with it)


def _stored_scale_tombstone(job_id: str, page: int) -> dict | None:
    """The retire's tombstone on file ({"retired": true, "at", "ft_per_paper_inch": the engine's number the reviewer went
    back to}), or None."""
    d = _stored_scale_raw(job_id, page)
    return d if isinstance(d, dict) and d.get("retired") else None


def _stored_scale_pick(job_id: str, page: int) -> dict | None:
    d = _stored_scale_raw(job_id, page)
    if d is None:
        return None
    if isinstance(d, dict) and d.get("retired"):
        return None             # a retired pick (the approve's tombstone): the engine's own reading stands
    # An out-of-band sidecar can now only come from a hand edit or an
    # older build - the approve route refuses to write one. Ignoring it
    # drops the page back to the engine's reading, which the chip, the
    # banner and the export all state as `auto`: visible, not silent.
    return d if _sane_ft(d.get("ft_per_paper_inch")) is not None else None


def _stored_scale_raw(job_id: str, page: int) -> dict | None:
    """The pick file as it reads now - a pick or a tombstone - or None."""
    f = _scale_pick_path(job_id, page)
    if not f.exists():
        _PICK_LAST.pop(str(f), None)
        return None
    try:
        d = json.loads(f.read_text())
    except (OSError, ValueError):
        # written in place by the approve: a read that catches the write half way (or a file held open on Windows)
        # answers with the last whole pick, never "no pick" - that made the cut / fill's result stale for a moment and
        # a page load took the rows out (critic 10, r-2; critic 11, r-1)
        d = _PICK_LAST.get(str(f))
        if d is None:
            return None
    else:
        if isinstance(d, dict):
            _PICK_LAST[str(f)] = d
    return d if isinstance(d, dict) else None


def _scale_stamp(job_id: str, page: int) -> str:
    """The stamp of the page's scale record as it reads now (a pick, a tombstone, or none): any change to the record changes
    it. The review page loads it with the scale (GET .../scale) and sends it back with Approve (`scale_seen`), so an approve
    from a page that never saw a newer record - the Scale card typed in a tab of its own, another tab, another seat - is
    refused instead of writing its old number over the review's (critic 23, RISK-1)."""
    return _scale_stamp_of(_stored_scale_raw(job_id, page))


def _scale_stamp_of(d) -> str:
    """_scale_stamp of a record already read (one read gives the number and its stamp together)."""
    if d is None:
        return "none"
    return "r" + hashlib.sha1(json.dumps(d, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


_SCALE_LOCKS: dict = {}
_SCALE_LOCKS_GUARD = threading.Lock()


def _scale_lock(job_id: str, page: int) -> "threading.Lock":
    """The lock of one page's scale record: every route that writes it (the approve - a pick, the engine's reading, a
    go-back - and the Cut / fill Scale card's typing and confirm) holds it around its check and its write. In-process
    only, which is enough: production runs ONE uvicorn process. It is always the innermost lock: only the record's check
    and write run under it (no cut / fill project lock, no other route), so it cannot deadlock with any lock a caller
    already holds."""
    k = (str(job_id), int(page))
    with _SCALE_LOCKS_GUARD:
        lk = _SCALE_LOCKS.get(k)
        if lk is None:
            lk = _SCALE_LOCKS[k] = threading.Lock()
        return lk


def _scale_seen_check(job_id: str, page: int, res: dict, body) -> None:
    """Approve's first step: refuse, writing nothing, when the page did not see the scale record now in force and its
    scale disagrees with it. A page that sends no stamp is refused too: every page of this build sends one, and a page
    from before it is also deaf to the Scale card (it never asks the record when the browser saved a scale)."""
    seen = getattr(body, "scale_seen", None)
    if not isinstance(seen, str) or not seen:
        raise HTTPException(409, "Nothing was saved: this page is out of date. Reload the page, then approve again.")
    if seen == _scale_stamp(job_id, page):
        return
    # the record changed since this page loaded it: an approve that agrees with it is never refused for the stamp alone
    sent = getattr(body, "scale", None)
    if sent is None:
        return                       # nothing on screen to write over the record (_page_scale keeps the pick on file)
    try:
        now = float((_page_scale(job_id, page, res) or {}).get("ft_per_paper_inch") or 0)
        want = float(sent.ft_per_paper_inch)
    except (TypeError, ValueError):
        return
    if now <= 0 or abs(want - now) <= 1e-6 * now:
        return
    raise HTTPException(409, f'Nothing was saved: the scale of this sheet was changed to 1" = {_ft(now)} ft (in Cut / fill '
                             f'or in another tab), and this page still shows 1" = {_ft(want)} ft. Reload the page: it will '
                             f'show 1" = {_ft(now)} ft. Then approve again.')


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
            # WHEN the reviewer set this number: an approve that sends the same number again is no new decision and
            # keeps the first one's time (every approve rewrote it, and the cut / fill read each re-approve as a
            # correction made after the operator's typed scale: critic 9, WRONG-2). `at` is that moment in epoch
            # seconds, exact: the text is local time in whole seconds, a pick 0.1 s after a typing read as before it,
            # and the fall-back hour read an hour late (critic 9, r-6)
            _prev = _stored_scale_pick(job_id, page)
            _same_pick = bool(_prev) and abs(float(_prev.get("ft_per_paper_inch") or 0) - want) < 1e-9
            pick = {"ft_per_paper_inch": want,
                    "text": (sent.text or "").strip() or None,
                    "set_by": _BY_REVIEWER,
                    "set_at": (_prev.get("set_at") if _same_pick and _prev.get("set_at")
                               else time.strftime("%Y-%m-%dT%H:%M:%S"))}
            _at = _prev.get("at") if _same_pick else time.time()
            _timeless = _same_pick and not _prev.get("at") and not _prev.get("set_at")
            if _timeless:
                _at = time.time()       # a pick with no time at all is dated by this approve (critic 14, RISK-1)
            if _at:
                pick["at"] = _at
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
                # written in place: a temp file + os.replace failed on Windows whenever a reader held the file open
                # (WinError 5, 1,397 of 2,000 writes against a busy reader), silently - the correction never reached
                # the cut / fill and its rows were exported 26.6 % high (critic 11, r-1). A reader that catches the write
                # half way keeps the last whole pick instead (_stored_scale_pick; critic 10, r-2). The same pick on file
                # is not written again: an unwritable file then failed every re-approve of the page (critic 13, m-c)
                if not _same_pick or _timeless:
                    _pf = _scale_pick_path(job_id, page)
                    _pf.write_text(json.dumps(pick, indent=1))
                    _PICK_LAST[str(_pf)] = dict(pick)      # the last whole reading is this one (critic 13, m-b)
            except OSError:
                # unwritable: the cut / fill reads this file, so exporting over the pick still on file would measure the
                # dirt at the old scale beside the page's new one - the approve fails, said (critic 12, r-1)
                raise HTTPException(503, "the scale could not be recorded on disk: approve again")
            # training ledger (observation only, never raises): a human
            # overruled the engine's reading - the core scale label.
            # `offered` is the label that makes an ABSTENTION trainable: the
            # engine put N readings up and a person chose this one, which is
            # exactly the ranking the engine refused to make. Once per
            # decision: the same number approved again is not another label
            # (each retry added one: critic 9, m8)
            if not _same_pick:
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
            if _had_pick:
                # retired by a tombstone written in place, never an unlink: on Windows the unlink was refused while a
                # reader held the file (WinError 32: 314 of 500 under a busy reader), silently - the old pick stood and
                # the rows at its scale were exported 36 % low (critic 12, r-1); in-place writes landed 2,000 of 2,000.
                # _stored_scale_pick reads it as no pick. Not written: the approve fails, said, rather than export over
                # a pick the cut / fill still reads
                try:
                    # dated now - unless the engine merely came round to the reviewer's own number (a re-detect): that
                    # is no new decision, and keeps the pick's time (critic 15, m1)
                    _tat = time.time()
                    try:
                        if abs(float(_had_pick.get("ft_per_paper_inch") or 0) - want) < 1e-9:
                            _tat = float(_had_pick.get("at") or 0) or time.mktime(time.strptime(str(_had_pick.get("set_at")), "%Y-%m-%dT%H:%M:%S"))
                    except (TypeError, ValueError, OverflowError):
                        _tat = time.time()
                    _tomb = {"retired": True, "at": _tat, "ft_per_paper_inch": want}
                    _pf = _scale_pick_path(job_id, page)
                    _pf.write_text(json.dumps(_tomb, indent=1))
                    _PICK_LAST[str(_pf)] = _tomb           # a half-written read right after answers "no pick" (m-b)
                except OSError:
                    raise HTTPException(503, "the scale could not be recorded on disk (the reviewer's earlier pick is still "
                                             "on file): approve again")
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
        # the same moment in epoch seconds, exact (a pick written before it was kept has the text only)
        "set_at_epoch": pick.get("at"),
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
              0x2018: "'", 0x2019: "'", 0x2014: "-", 0x2013: "-",
              # the flooring rows' typography: "Wall area - perimeter x
              # ceiling", "-51 SF of doors", "BED #2 <-> FOYER" - the
              # report's base font draws each of these as an empty box
              0x00D7: "x", 0x2212: "-", 0x2194: "<->", 0x00B7: "."}


def _ascii_marks(s) -> str:
    return str(s or "?").translate(_MARK_FOLD)


# EVERY STRING THE REPORT DRAWS, IN ITS FONT'S OWN CHARACTERS (UI verify 2026-10-02 M1: the scale
# line printed "1· = 20·"). The report draws with PyMuPDF's base font, which has the Latin-1
# characters and nothing past them (measured: every character 0x20-0xFF but the C1 controls
# round-trips through insert_text / get_text; en / em dash, curly quotes, primes, ellipsis,
# bullet, euro, <=, -> do not, and come out as a dot or an empty box). So the report's page
# wrapper folds each one to what the font has, and anything still past Latin-1 prints as "?"
# rather than a mark that looks like data. Display only: values and exports keep their text.
_REPORT_FOLD = dict(_MARK_FOLD)
_REPORT_FOLD.update({0x2026: "...", 0x2022: "*", 0x2264: "<=", 0x2265: ">=", 0x2192: "->", 0x2190: "<-",
                     0x21D2: "=>", 0x2248: "~", 0x2260: "!=", 0x20AC: "EUR", 0x2122: "TM", 0x2010: "-",
                     0x2011: "-", 0x2012: "-", 0x2015: "-", 0x2044: "/", 0x201A: ",", 0x201E: '"',
                     0x2039: "<", 0x203A: ">", 0x2009: " ", 0x200A: " ", 0x202F: " ", 0x2007: " ",
                     0x00A0: " ", 0x00AD: "-", 0x200B: "", 0x2713: "ok", 0x2714: "ok", 0x2717: "x",
                     0x2718: "x", 0x25B2: "^", 0x25BC: "v", 0x2191: "^", 0x2193: "v", 0x2194: "<->"})


def _report_text(s) -> str:
    t = str("" if s is None else s).translate(_REPORT_FOLD)
    return "".join(c if (" " <= c <= "~" or "¡" <= c <= "ÿ") else
                   (" " if c in "\t\n\r" else "?") for c in t)


class _ReportPage:
    """A report page whose text goes through _report_text: the one place every insert_text of
    report_pdf passes, so no string - the scale caption, a typed row name, a fixed caption - can
    draw a mark the font does not have."""
    def __init__(self, pg):
        self._pg = pg

    def __getattr__(self, k):
        return getattr(self._pg, k)

    def insert_text(self, point, text, *a, **kw):
        return self._pg.insert_text(point, _report_text(text), *a, **kw)


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
        # - a storage site's reads 1<U+201D> = 40<U+2019> - which that font draws
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
    # the stamp of the scale record the page loaded (GET .../scale `stamp`, or the last approve's `scale_stamp`):
    # _scale_seen_check. None = a page from before the stamp: refused, reload
    scale_seen: str | None = None


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
def approve(job_id: str, page: int, body: ApproveBody, request: Request = None):
    JOBS.get(job_id) or _404()      # writes: see _forbidden_job
    f = _job_dir(job_id) / f"p{page}_result.json"
    if not f.exists():
        raise HTTPException(404, "no result to approve")
    res = json.loads(f.read_text())
    # first, before anything is written: a page that did not see the scale record now in force does not write its old
    # number over it (critic 23, RISK-1: a tab deaf to the Scale card retired the reviewer's 50, or exported at 45)
    _scale_seen_check(job_id, page, res, body)
    # a row a build of 2026-10-01 marked as the old Earthwork calculator's (a tab still open from it) counts nowhere
    _extra = [it for it in body.extra_items if not (isinstance(it, dict) and it.get("src") == "earthwork-old")]
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
    _el_sid: list = []                  # style id per element, parallel to `elements`
    for ch in list(res["chains"]) + list(body.added_chains):
        label = body.chain_labels.get(ch["chain_id"]) \
            or body.labels.get(ch["style_id"])
        if label is None or ch["chain_id"] in excluded:
            continue
        wf = _wz_frac(ch["points"], _wz, _v_ratio) if _wz else 1.0
        _wz_fr.append(wf)
        _el_sid.append(ch.get("style_id"))
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
        "source": _display_name(JOBS[job_id]["path"]) if job_id in JOBS else "",
        "pageIndex": page,
        "imageDpi": body.image_dpi,
        "pageWidthPt": res["page"]["width_pt"],
        "pageHeightPt": res["page"]["height_pt"],
        "elements": elements,
        **({"countOnce": _once_summary} if _once else {}),
    }
    # the stamp checked again and the scale written under the page's scale lock, as one step: a Scale-card write or
    # another tab's approve landing between the first check and this write was overwritten with nothing said (critic 24,
    # MINOR-1). Nothing above this line writes the record; the work above runs unlocked
    with _scale_lock(job_id, page):
        _scale_seen_check(job_id, page, res, body)
        det = _page_scale(job_id, page, res, body.scale)
    # the cut / fill rows this approve would export stand only while the engine's own numbers do. Asked here, after the
    # scale above is recorded, so a correction made in this very approve is seen: refused, and said - the host takes
    # the rows out (or brings the engine's in) and the reviewer approves again (critic 8, WRONG-1: the rows at 50 were
    # exported beside the page's own quantities at 40, the fill 56 % high)
    cf_why = _cf_rows_refused(job_id, page, _extra, det)
    cf_other = _cf_rows_exported_elsewhere(job_id, page, _extra) if not cf_why else None
    # the scale above was recorded before these refusals: the page takes the record's new stamp from them, or its next
    # deliberate change read as one made elsewhere (_scale_seen_check)
    if cf_why:
        raise HTTPException(409, {"cutfill": cf_why, "scale_stamp": _scale_stamp(job_id, page)})
    if cf_other is not None:
        _op, _row = cf_other
        raise HTTPException(409, {"cutfill": f"page {_op + 1}'s approved takeoff still carries the job's cut / fill rows "
                                              f"(\"{_row}\"): approve page {_op + 1} again first, without the cut / fill "
                                              f"rows (a row of your own with that name stays) - one set per job, here they "
                                              f"would count the dirt twice", "export_page": _op,
                                  "scale_stamp": _scale_stamp(job_id, page)})
    _cf_rows_claim(job_id, page, _extra)
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
    for _k, el in enumerate(elements):
        _f = _wz_fr[_k] if _wz else 1.0
        # work-area mask: a chain fully outside counts nothing - not
        # footage and not a line count - exactly as computeTakeoffRows()
        # skips it in the panel the operator approved from
        if _wz and _f <= 0:
            continue
        t = totals.setdefault(el["label"], {"count": 0, "length_pt": 0.0, "by_sid": {}})
        t["count"] += 1
        _pt = el.get("lengthPtAxis", el["lengthPt"]) * _f * el.get("onceFrac", 1.0)
        t["length_pt"] += _pt
        # the same footage split by style id, so the bid sheet can tell which
        # part of a family a trench run was calculated over (bidsheet.py,
        # "a trench run replaces its family"); an operator-added chain with
        # no style id goes under "" and is never a run's style
        _s = _el_sid[_k] if _k < len(_el_sid) else None
        _sk = str(_s) if isinstance(_s, int) and not isinstance(_s, bool) else ""
        t["by_sid"][_sk] = t["by_sid"].get(_sk, 0.0) + _pt
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
                # measured footage per style id (engine figure, not the
                # override): what the bid sheet subtracts when a trench run
                # row says it was calculated over one of these styles
                **({"by_style": {k: round(v / 72.0 * ft_per_in, 1)
                                 for k, v in t["by_sid"].items()}} if ft_per_in else {}),
            })
    for ex in _extra:
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
        # THE ROOMS BEHIND A FLOORING ROW. "Flooring - Carpet 371.1 SF" is
        # one number a client cannot check; "BED #2 135.5 SF, BED #3 123.3
        # SF, ..." under it is a list they can walk with the plan. Carried
        # verbatim (bounded), printed as sub-lines on the report, never
        # added into any figure - the row's qty is the row's qty.
        details = []
        for dt in (ex.get("details") or [])[:200]:
            if not isinstance(dt, dict):
                continue
            try:
                dq = round(float(dt.get("qty")), 1)
            except (TypeError, ValueError):
                continue
            dn = str(dt.get("name") or "").strip()[:60]
            if not dn:
                continue
            # a part's own unit when given (an empty one is a count of
            # something with no unit, e.g. faces per wall), else the row's
            details.append({"name": dn, "qty": dq,
                            "unit": str(dt["unit"] if dt.get("unit") is not None else unit)[:8],
                            **({"note": str(dt.get("note"))[:80]} if dt.get("note") else {})})
        # THE TRENCH TOOL'S PROVENANCE. A run row names the family it was
        # calculated over (`run_of`, its label; `run_sid`, its style id), what
        # kind of row it is and the utility the operator picked, so the bid
        # sheet can replace the family's footage with the run's and file a
        # pattern-named run on the utility's line (bidsheet.py). Carried only
        # when the row sent them; an older browser's rows export as before.
        _prov = {}
        if isinstance(ex.get("kind"), str) and ex["kind"].strip():
            _prov["kind"] = ex["kind"].strip()[:24]
        # the job's cut / fill rows say so in the export: one set per job, and another page's approve looks for them
        # here (_cf_rows_exported_elsewhere). An Areas-tool row named alike is not one (critic 11, r-2)
        if ex.get("_area"):
            _prov["src"] = "area"           # the Areas tool's, whatever its name (critic 12, WRONG-2)
        elif _CF_HOST_ROW.match(label):
            _prov["src"] = "cutfill"
        # the browser's rows spell these `runOf` / `run` (calcSave keeps them so); an export
        # that read only the snake_case names lost them, and the CSV then replaced a whole
        # two-style family with one run (checker R4x1, 2026-09-26)
        _ro = ex.get("run_of") if isinstance(ex.get("run_of"), str) else ex.get("runOf")
        if isinstance(_ro, str) and _ro.strip():
            _prov["run_of"] = _ro.strip()[:80]
        _rs = ex.get("run_sid") if ex.get("run_sid") is not None else ex.get("run")
        if isinstance(_rs, (int, float)) and not isinstance(_rs, bool) and float(_rs).is_integer():
            _prov["run_sid"] = int(_rs)
        if isinstance(ex.get("utility"), str) and ex["utility"].strip():
            _prov["utility"] = ex["utility"].strip().lower()[:16]
        items.append({
            "label": label, "count": 1, "length_ft": None,
            "trade": body.trades.get(label),
            "qty": qty, "unit": unit,
            "unit_price": price,
            "extended": round(qty * price, 2) if price else None,
            **({"qty_set_by": _BY_REVIEWER, "engine_qty": eng_qty} if o else {}),
            **({"details": details} if details else {}),
            **_prov,
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
    # linework is behind the volume (webui/app.js ewCoverageNote): on substation
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
    # a page that showed the note: this approve takes the old calculator's earthwork out of the bid, said once (critic
    # legacy-5, RISK-4)
    try:
        _ew_was = page in _ew_removed_pages(job_id)
    except OSError:
        _ew_was = False
    # this export's rows carry their own marks: an export without them is v1.8.5's, whose old Earthwork calculator rows
    # count nowhere (_export_view). Never read off its file time (critic legacy-1, RISK-4)
    export[EXPORT_MARKS_KEY] = 1
    out.write_text(json.dumps(export, indent=1))
    _ledger_approve(job_id, page, body, res, export)
    _audit(request, "sheet_approve", job=job_id, page=page,
           detail=_n(len(elements), "line") + ", " + _n(len(items), "item"))
    return JSONResponse({"exported": len(elements), "file": str(out),
                         "items": items, "scale": export.get("scale"),
                         "overrides": export["overrides"],
                         "override_warnings": ov_warn,
                         # the scale record as this approve left it: the page's next approve sends it (_scale_seen_check)
                         "scale_stamp": _scale_stamp(job_id, page),
                         **({"ew_said": EW_APPROVED_SAID} if _ew_was else {})})


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
            # exploded-text legend sheets (the stair set): the row labels are
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
    # ONE read of the record gives the number and its stamp: read apart, a record written between them paired a new stamp
    # with the old number, and the page's approve of that old number would pass the stamp check
    raw = _stored_scale_raw(job_id, page)
    pick = (None if raw is None or raw.get("retired") or _sane_ft(raw.get("ft_per_paper_inch")) is None else raw)
    return {
        # the record's stamp (_scale_stamp): the page sends it back with Approve. `recorded`: a review decision is on
        # file - a pick, or a tombstone (back to the engine's reading) - and the page's own saved scale gives way to it
        "stamp": _scale_stamp_of(raw),
        "recorded": raw is not None,
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


# A SHARED JOB'S RECORD HOLDS NO ONE'S PRICED TAKEOFF (2026-10-02). On a job every account opens (owner-less: a demo
# card or a shared sheet, with accounts on the box), the page record never stores and never serves any account's rows,
# unit prices, trades or flooring prices: they stay in that browser (its `::calc2` cache), as v1.8.5 kept them for every
# job. Rows saved there before this are left in the file and never served. An owner's own job, and a box with no
# accounts (one person), are unchanged. Detection, approvals, scale and cut / fill on a shared sheet are shared by design
# and not touched here.
_CALC_PERSONAL = ("rows", "prices", "trades", "rows_scale")


def _calcstate_shared(job_id: str) -> bool:
    j = JOBS.get(job_id)
    return isinstance(j, dict) and j.get("owner") is None and bool(_users.load())


def _fl_prices(fl, keep=None) -> None:
    """Flooring types' prices on a shared job: the stored ones by type name (`keep`, the old record's), else none."""
    if not isinstance(fl, dict) or not isinstance(fl.get("types"), list):
        return
    old = {}
    if isinstance(keep, dict) and isinstance(keep.get("types"), list):
        old = {t.get("name"): t for t in keep["types"] if isinstance(t, dict)}
    for t in fl["types"]:
        if isinstance(t, dict):
            o = old.get(t.get("name")) or {}
            for k in ("sf_price", "lf_price"):
                t[k] = o.get(k, 0)


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
    # THE OLD EARTHWORK CALCULATOR'S INPUTS ride along untouched (v1.8.5's ew / ew_bound / gzones): this app has no panel
    # that writes them, so a save that lacks them means "not mine", never "deleted" (PARITY.md section 4d). webui/app.js
    # calcStatePayload carries them too; this is the backstop for a page that does not
    if isinstance(data, dict):
        for _k in ("ew", "ew_bound", "gzones", "ew_dropped"):
            if _k in old and _k not in data:
                data[_k] = old[_k]
        # the old calculator's rows count nowhere: a row a build of 2026-10-01 marked as its (`earthwork-old`) is not
        # stored, and the record says the page had such work (the note: _ew_removed_pages)
        if isinstance(data.get("rows"), list):
            kept = [r for r in data["rows"] if not (isinstance(r, dict) and r.get("src") == "earthwork-old")]
            if len(kept) != len(data["rows"]):
                data["rows"] = kept
                data["ew_dropped"] = True
        for _k in ("ew_removed", "ew_put_back", "ew_replaced", "_ew_approved", "job_shared"):
            data.pop(_k, None)
        # a shared job: this account's rows and prices are not stored; what the file already held stays as it was
        if _calcstate_shared(job_id):
            for _k in _CALC_PERSONAL:
                data.pop(_k, None)
                if _k in old:
                    data[_k] = old[_k]
            _fl_prices(data.get("fl"), old.get("fl"))
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
    d: dict = {}
    if f.exists():
        try:
            d = json.loads(f.read_text())
        except (OSError, ValueError):
            d = {}
    d = d if isinstance(d, dict) else {}
    # a row a build of 2026-10-01 marked as the old calculator's is not shown (the file is left as it is)
    if isinstance(d.get("rows"), list) and any(isinstance(r, dict) and r.get("src") == "earthwork-old" for r in d["rows"]):
        d["rows"] = [r for r in d["rows"] if not (isinstance(r, dict) and r.get("src") == "earthwork-old")]
        d["ew_dropped"] = True
    # a shared job: nobody's rows or prices (the page keeps its own in that browser and stops sending them)
    if _calcstate_shared(job_id):
        for _k in _CALC_PERSONAL:
            d.pop(_k, None)
        _fl_prices(d.get("fl"))
        d["job_shared"] = True
    return d


@app.post("/api/job/{job_id}/page/{page}/calcstate")
async def post_calcstate(job_id: str, page: int, request: Request):
    return JSONResponse(_calcstate_put(job_id, page, await request.body()))


def _export_outdated(job_id: str, pno: int, exp: dict) -> "str | None":
    """Why page `pno`'s approved export no longer carries the page's scale, or None. Its scale record changed after the
    approve - a Scale-card typing writes the review's record and not the export (2026-09-29), and a refused approve
    records its scale before the refusal - so the report and the bid sheet, which read the export, would carry its
    quantities at the old scale with nothing said (critic 17 and 19, RISK-3). A v1.8.5 approval is never out of date for
    its old Earthwork calculator rows (they count nowhere: _export_view)."""
    return _export_outdated_why(job_id, pno, exp)


def _export_outdated_why(job_id: str, pno: int, exp: dict) -> "str | None":
    try:
        sc = exp.get("scale") or {}
        ppu, dpi = float(sc.get("pixelsPerUnit") or 0), float(exp.get("imageDpi") or 0)
        was = dpi / ppu if ppu > 0 and dpi > 0 else 0.0
        res = json.loads((_job_dir(job_id) / f"p{pno}_result.json").read_text(encoding="utf-8"))
        det = _page_scale(job_id, pno, res) or {}
        now = float(det.get("ft_per_paper_inch") or 0)
    except (OSError, ValueError, TypeError, HTTPException):
        return None
    # the export's cut / fill rows: the ones its approve marked as the cut / fill's (`src`). MARKS DECIDE: an export
    # without the marks is v1.8.5's, never judged against the engine (critic legacy-2; round 22's judging stays deleted). A quantity the reviewer typed
    # over a row is judged by the row's own number (`engine_qty`), which the approve's guard checked (critic 21, WRONG-1)
    own = [it for it in (exp.get("items") or []) if isinstance(it, dict) and it.get("src") == "cutfill"]
    cf_rows = [dict(it, qty=it["engine_qty"]) if it.get("qty_set_by") == _BY_REVIEWER and it.get("engine_qty") is not None else it
               for it in own]
    # the page that holds the job's cut / fill rows (host_rows) - whether its export carries them or not: approved while
    # they were out of the takeoff (a stale result takes them out until a Compute), the dirt left every export, the flag
    # cleared and the bid sheet's Site Fill line was blank (critic 21, WRONG-2)
    try:
        st0 = _cf_state(job_id)
    except Exception:            # noqa: BLE001 - no answer makes no claim
        st0 = {}
    rp = st0.get("rows_page")
    holds = rp == int(pno)
    # the export carries the cut / fill's own rows, but no page's takeoff holds them now: an approve that carries them
    # records its page (_cf_rows_claim), so they were taken out after it - Remove from takeoff. The bid still priced the
    # dirt the estimator took out, and nothing said so (critic 23, RISK-3). Marked rows only (`src`): the old Earthwork
    # calculator's rows are not the engine's and keep their own notes
    if cf_rows and st0.get("project") and rp is None:
        return (f"OUT OF DATE: page {pno + 1}'s approval still has cut / fill rows that were removed from the takeoff, so "
                f"the bid still counts that dirt - approve page {pno + 1} again (to keep the dirt, add the rows back in Cut / "
                f"fill first)")
    # compute first wherever this page's approval carries (or should carry) the cut / fill rows: approving again with
    # rows that stand no more is refused, and without them the dirt leaves the bid (critic 20, MINOR-6; critic 21, WRONG-2).
    # Rows that belong to ANOTHER page's takeoff now leave this page's export: approve it again without them (critic 22)
    if cf_rows and isinstance(rp, int) and rp != int(pno):
        step = f"approve page {pno + 1} again without its cut / fill rows (they belong to page {rp + 1}'s takeoff now)"
    elif cf_rows or holds:
        step = f"compute in the Cut / fill panel, then approve page {pno + 1} again with its cut / fill rows"
    else:
        step = "approve it again"
    if was > 0 and now > 0 and abs(now - was) > 1e-3 * now:
        return (f'OUT OF DATE: page {pno + 1} was approved at 1" = {_ft(was)} ft, but its scale is now 1" = {_ft(now)} ft - '
                f'{step}; its quantities here are at the old scale')
    if (was > 0) != (now > 0):
        # a scale then and none now, or none then and one now (critic 20, MINOR-5)
        then = f'1" = {_ft(was)} ft' if was > 0 else "no scale"
        nowt = f'1" = {_ft(now)} ft' if now > 0 else "no scale"
        return (f"OUT OF DATE: page {pno + 1} was approved with {then}, and it reads {nowt} now - {step}; its quantities "
                f"here are at the old scale")
    # the page's cut / fill rows as the approve's own guard judges them now: measured on another sheet, or at a scale a
    # review changed since, they stand no more - and the export still carries them (critic 20, WRONG-1)
    if cf_rows:
        try:
            # a job with no cut / fill at all: only the scale is judged (critic 21, RISK-2)
            why = _cf_rows_refused(job_id, pno, cf_rows, det or None) if st0.get("project") else None
        except Exception:        # noqa: BLE001 - no answer makes no claim
            why = None
        if why:
            return (f"OUT OF DATE: page {pno + 1}'s approved cut / fill rows no longer stand ({why}) - {step}; the cut / "
                    f"fill quantities here are the old ones")
    if holds and not cf_rows:
        return (f"OUT OF DATE: page {pno + 1} holds the job's cut / fill rows, but its approved export carries none (it was "
                f"approved while they were out of the takeoff) - {step}; the bid has no cut / fill quantities from it")
    return None


def _cf_rows_unapproved(job_id: str, approved) -> "tuple | None":
    """(page, words) when the page that holds the job's cut / fill rows (host_rows) is not approved at all: the dirt is in
    no export, and nothing said so (critic 22, RISK-2)."""
    try:
        rp = _cf_state(job_id).get("rows_page")
    except Exception:            # noqa: BLE001 - no answer makes no claim
        return None
    if not isinstance(rp, int) or rp in set(approved):
        return None
    return rp, (f"NOT IN THE BID: page {rp + 1} holds the job's cut / fill rows, but it is not approved - approve page "
                f"{rp + 1} with its cut / fill rows to put them in the bid")


def _bid_warning(note) -> bool:
    """A note that says the bid's numbers are not the takeoff's now, or may count the same dirt twice (CHECK): printed
    first, whole, wherever the bid is."""
    return str(note).startswith(("OUT OF DATE", "NOT IN THE BID", "CHECK"))


@app.get("/api/job/{job_id}/approved")
def approved_pages(job_id: str):
    """Which pages of a job are approved (carry p<N>_elements.json), so a
    whole-job reader asks once instead of probing every page and logging
    a 404 per unapproved sheet in the browser console. `outdated`: the
    approved pages whose scale changed since, with why (_export_outdated)."""
    if job_id not in JOBS:
        _404()
    pages = []
    outdated = {}
    notes = {}
    try:
        for f in _job_dir(job_id).glob("p*_elements.json"):
            try:
                pno = int(f.name[1:].split("_")[0])
            except ValueError:
                continue
            pages.append(pno)
            try:
                why = _export_outdated(job_id, pno, json.loads(f.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                why = None
            if why:
                outdated[str(pno)] = why
    except OSError:
        pass
    un = _cf_rows_unapproved(job_id, pages)
    if un:
        outdated[str(un[0])] = un[1]
    # `notes`: the pages whose old Earthwork calculator work was removed (_ew_removed_pages), approved or not - the page
    # shows it as its lasting note (webui/app.js approvalOutdated)
    ew_pages: list = []
    try:
        ew_pages = _ew_removed_pages(job_id)
        for pno in ew_pages:
            notes[str(pno)] = _ew_note(job_id, pno)
    except OSError:
        pass
    # `ew_removed`: the job-level list of those pages (a whole-job view - the frame's Plans tiles, Summary - says the note
    # once per page from it, without reading every page's notes); `ew_note`: the sentence itself
    return {"pages": sorted(pages), "outdated": outdated, "notes": notes, "ew_removed": ew_pages, "ew_note": EW_REMOVED_NOTE}


@app.get("/api/job/{job_id}/activity")
def job_activity(job_id: str, request: Request, limit: int = 30):
    """This project's history, newest first: the audit log's lines for the
    job merged with its training-ledger events (takeoff_engine/audit.py
    job_activity has the merge and the action names).

    Who may ask is the ownership rule every /api/job route gets from
    _forbidden_job: the owner, admins, and anyone for a shared row; another
    account's job and an orphan are 404 "unknown job" before this runs.
    WHO DID IT is admin-only: an estimator reading a shared demo's history
    must not learn which other accounts on the box touched it, so for a
    non-admin the `user` field is left out entirely, not nulled."""
    JOBS.get(job_id) or _404()
    return {"events": _audit_log.job_activity(job_id, limit,
                                              with_user=_is_staff(request))}


@app.get("/api/job/{job_id}/page/{page}/elements")
def get_elements(job_id: str, page: int):
    f = _job_dir(job_id) / f"p{page}_elements.json"
    if not f.exists():
        raise HTTPException(404, "not approved yet")
    # a v1.8.5 export is served without its old Earthwork calculator rows (_export_view); the file stays as it is
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raw = None
    view = _export_view(job_id, page, raw) if isinstance(raw, dict) else raw
    # a page whose old Earthwork calculator work was removed carries its note (`ew_note`) - in the answer only
    try:
        ew_n = _ew_note(job_id, page) if page in _ew_removed_pages(job_id) else None
    except OSError:
        ew_n = None
    if ew_n and isinstance(view, dict):
        view = dict(view, ew_note=ew_n)
    if view is not raw:
        return JSONResponse(view, headers={"Content-Disposition": f'attachment; filename="elements_p{page}.json"'})
    return FileResponse(f, media_type="application/json",
                        filename=f"elements_p{page}.json")


# The report is a 612 pt Letter page with 50 pt margins: 512 pt of text
# width. A 16 pt title is set with pymupdf's own text measure and wrapped on
# word boundaries inside that width; a single word longer than the width is
# cut by characters rather than left running off the page.
_REPORT_TEXT_W = 612.0 - 2 * 50.0


def _report_title_lines(text: str, fontsize: float = 16.0,
                        width: float = _REPORT_TEXT_W) -> list:
    text = " ".join(_report_text(text).split())   # measured as it will print (_ReportPage)
    if not text:
        return [""]
    fits = lambda s: pymupdf.get_text_length(s, fontsize=fontsize) <= width
    if fits(text):
        return [text]
    lines: list = []
    cur = ""
    for word in text.split(" "):
        trial = (cur + " " + word) if cur else word
        if fits(trial):
            cur = trial
            continue
        if cur:
            lines.append(cur)
        cur = word
        while not fits(cur):                 # one word wider than the page
            n = len(cur)
            while n > 1 and not fits(cur[:n]):
                n -= 1
            lines.append(cur[:n])
            cur = cur[n:]
    if cur:
        lines.append(cur)
    return lines


# A table cell's text: the report's row labels are wrapped inside their column,
# never cut. The page table and the summary print the label from x = 50 to the
# Count column at x = 300; the bid page prints the item from x = 70 to the Unit
# column at x = 300. 6 pt is left before the next column.
_REPORT_LABEL_W = 300.0 - 50.0 - 6.0
_REPORT_BID_ITEM_W = 300.0 - 70.0 - 6.0
_REPORT_CELL_LH = 10                       # line pitch of a wrapped 9 pt label


def _report_cell_lines(text: str, width: float, fontsize: float = 9.0) -> list:
    return _report_title_lines(text, fontsize=fontsize, width=width)


def _report_cell_draw(pg, x: float, y: float, lines: list, fontsize: float = 9.0) -> None:
    for i, ln in enumerate(lines):
        pg.insert_text((x, y + i * _REPORT_CELL_LH), ln, fontsize=fontsize)


def _bid_skip(ignore) -> set:
    """The cross-sheet panel's unticks, "page:label" each, as (page, label)."""
    skip = set()
    if not isinstance(ignore, (list, tuple, set)):
        return skip                 # a direct call that left the Query default in place
    for k in ignore:
        pg_, _, label = str(k).partition(":")
        if pg_.isdigit() and label:
            skip.add((int(pg_), label))
    return skip


def _bid_build(job_id: str, pages: list, user, ignore=()) -> tuple:
    """(bidsheet.build result, template scope) for a job's approved pages:
    the ONE place the bid-sheet CSV and the report's bid page get their
    rows, template and unticks, so the two cannot print different money."""
    jd = _job_dir(job_id)
    rows = []
    for pno in pages:
        exp = _read_export(job_id, pno)        # without v1.8.5's old Earthwork calculator rows
        if not exp:
            continue
        rows.append({"page": pno, "items": exp.get("items", [])})
    lines, scope = _bidsheet.resolve(user)
    return _bidsheet.build(lines, rows, ignore=_bid_skip(ignore)), scope


@app.get("/api/job/{job_id}/report.pdf")
def report_pdf(job_id: str, request: Request, ignore: list[str] = Query([])):
    """Compile every approved page of this job into a deliverable PDF:
    per page an overlay of the approved lines on the sheet, then an
    itemized table (label / count / footage / pricing). `ignore` is the
    bid-sheet CSV's: the cross-sheet unticks, applied to the bid page."""
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
    # the page that holds the job's cut / fill rows, not approved: the dirt is in no page below (critic 22, RISK-2)
    _un = _cf_rows_unapproved(job_id, pages)
    if _un:
        all_notes.append(_un[1])

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
    _ewp = set(_ew_removed_pages(job_id))
    for pno in pages:
        # without v1.8.5's old Earthwork calculator rows and their caveats (_export_view)
        exp = _export_view(job_id, pno, json.loads((jd / f"p{pno}_elements.json").read_text()))
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
        tp = _ReportPage(out.new_page(width=612, height=792))
        _letterhead(tp)
        y = 60
        # Head the table with the PRINTED sheet number when detection read
        # one off the title block ("Sheet C3.1 (page 4)"): the client names
        # sheets by that number, not by PDF position. The index stays in
        # print because the two disagree on purpose (CLAUDE.md - page
        # numbering is ambiguous). Falls back to the old header untouched.
        # the page list's reading (takeoff_engine/sheet_no.py) when the set has been triaged, else the detection's
        _known, _sn = _triage_sheet_no(jd, pno, job.get("mtime"))
        _rf = jd / f"p{pno}_result.json"
        if not _known and _rf.exists():
            try:
                _sn = json.loads(_rf.read_text()).get("sheet_no")
            except (OSError, ValueError):
                _sn = None
        _hdr = (f"Sheet {_sn} (page {pno + 1}) - Itemized takeoff" if _sn
                else f"Page {pno + 1} - Itemized takeoff")
        for _tl in _report_title_lines(_hdr):
            tp.insert_text((50, y), _tl, fontsize=16)
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
        # a page whose scale changed after its approve says so first, here and on the summary page (critic 17 and 19,
        # RISK-3: its quantities were printed at the old scale with nothing said)
        # ...and, on a page whose old Earthwork calculator work was removed, the one note (_ew_removed_pages)
        _old = _export_outdated(job_id, pno, exp)
        _ewn = _ew_note(job_id, pno) if pno in _ewp else None
        for _nt in ([_old] if _old else []) + ([_ewn] if _ewn else []) + list(exp.get("notes") or []):
            if _nt not in all_notes:
                all_notes.append(_nt)
            for _seg in (textwrap.wrap(str(_nt), 118) or [""]):
                tp.insert_text((50, y), _seg, fontsize=8)
                y += 11
        y += 8
        tp.insert_text((50, y), "Label", fontsize=9)
        tp.insert_text((300, y), "Count", fontsize=9)
        tp.insert_text((360, y), "Quantity", fontsize=9)      # each row prints its own unit
        tp.insert_text((440, y), "Unit $", fontsize=9)
        tp.insert_text((500, y), "Extended $", fontsize=9)
        y += 14
        page_total = 0.0
        # a trench run REPLACES the feet of the family it was calculated over (the bid
        # sheet's rule): the family row says how many of its feet its runs carry
        _runs = _bidsheet.run_rows(items)
        for it in items:
            _rep_ft, _rep_n = (_bidsheet.family_replacement(it, _runs)
                               if (_runs and not it.get("unit") and it.get("length_ft") is not None) else (0.0, 0))
            # a row printing a sub-line needs the break a little earlier
            _sub = (it.get("qty_set_by") == _BY_REVIEWER) \
                or (it.get("unit") and it.get("length_ft") is not None) or _rep_ft > 0.05
            _ov = it.get("qty_set_by") == _BY_REVIEWER
            # folded to ASCII: the base font draws an em dash or a times
            # sign as an empty box (the flooring rows carry both)
            _lab = _ascii_marks(it["label"])
            # the whole label, wrapped inside its column (it was cut at 52 characters)
            _labl = _report_cell_lines(_lab + (" *" if _ov else ""), _REPORT_LABEL_W)
            if y + _REPORT_CELL_LH * (len(_labl) - 1) > (722 if _sub else 740):
                tp = _ReportPage(out.new_page(width=612, height=792))
                _letterhead(tp)
                y = 60
            _report_cell_draw(tp, 50, y, _labl)
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
            y += _REPORT_CELL_LH * (len(_labl) - 1)       # the label's wrapped lines, under its first
            if it.get("extended"):
                page_total += it["extended"]
            # An EA/CY row's reference footage, stated as reference.
            if _rep_ft > 0.05:
                y += 11
                _left = max(0.0, float(it["length_ft"] or 0.0) - _rep_ft)
                tp.insert_text((60, y),
                               f'({_rep_ft:,.1f} ft of this family are carried by {_rep_n} trench run row'
                               f'{"" if _rep_n == 1 else "s"}; {_left:,.1f} ft not run still count)',
                               fontsize=7)
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
            # The rooms (or other parts) behind a calculator row, one
            # sub-line each, so the client can check the row against the
            # plan. Reference only: nothing here is added to any total.
            for _dt in (it.get("details") or []):
                y += 11
                if y > 752:
                    tp = _ReportPage(out.new_page(width=612, height=792))
                    _letterhead(tp)
                    y = 60
                _dq = _dt.get("qty")
                _dq = int(_dq) if isinstance(_dq, float) and _dq.is_integer() else _dq
                _du = _dt.get("unit") or ""
                tp.insert_text((60, y),
                               _ascii_marks(f'- {str(_dt.get("name", ""))[:44]}: {_dq:,}'
                                            + (f" {_du}" if _du else "")
                                            + (f'  ({_dt["note"]})' if _dt.get("note") else ""))[:118],
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
                # the feet a trench run replaced are counted on the run's rows, not here
                _rep_ft = (_bidsheet.family_replacement(it, _runs)[0]
                           if (_runs and it.get("length_ft") is not None) else 0.0)
                g["qty"] += max(0.0, (it["length_ft"] or 0.0) - _rep_ft)
                _ef = it.get("engine_length_ft")
                g["eng"] += max(0.0, ((_ef if _ef is not None else it["length_ft"]) or 0.0) - _rep_ft)
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
    tp = _ReportPage(out.new_page(width=612, height=792))
    _letterhead(tp)
    y = 60
    # the document's DISPLAY name (the upload_<hash>_ prefix is plumbing),
    # wrapped inside the page: the audit measured this title running to
    # x = 734.7 on a 612 pt page (D47)
    for _tl in _report_title_lines(f"{_display_name(job['path'])} - Takeoff summary"):
        tp.insert_text((50, y), _tl, fontsize=16)
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
    # every OUT OF DATE note first and whole - the summary is the page most often read alone, and eight notes from the
    # first pages crowded out a later page's (critic 20, RISK-1) - then the first eight of the rest
    _out = [n for n in all_notes if _bid_warning(n)]
    # then each page whose old Earthwork calculator work was removed, once - an unapproved one too
    for _p in sorted(_ewp):
        if _ew_note(job_id, _p) not in all_notes:
            all_notes.append(_ew_note(job_id, _p))
    _out = _out + [n for n in all_notes if n not in _out and str(n).endswith(EW_REMOVED_NOTE)]
    for _nt in _out + [n for n in all_notes if n not in _out][:8]:
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
        _labl = _report_cell_lines(str(label) + (" *" if g["ov"] else ""), _REPORT_LABEL_W)
        if y + _REPORT_CELL_LH * (len(_labl) - 1) > (728 if g["ov"] else 740):
            tp = _ReportPage(out.new_page(width=612, height=792))
            _letterhead(tp)
            y = 60
        _report_cell_draw(tp, 50, y, _labl)
        tp.insert_text((300, y), str(g["count"]), fontsize=9)
        _qs = f'{g["qty"]:,.1f}' if g["unit"] == "ft" else f'{g["qty"]:,g}'
        tp.insert_text((360, y), f'{_qs} {g["unit"]}', fontsize=9)
        tp.insert_text((500, y), f'{g["ext"]:,.2f}' if g["ext"] else "-", fontsize=9)
        y += _REPORT_CELL_LH * (len(_labl) - 1)
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
    # _bid_build: the same rows, template and cross-sheet unticks as the
    # bid-sheet CSV (critic bid-content-1, RISK-6: this page ignored them)
    _bs, _bs_scope = _bid_build(job_id, pages, _who(request), ignore)
    tp = _ReportPage(out.new_page(width=612, height=792))
    _letterhead(tp)
    y = 60
    tp.insert_text((50, y), "Bid sheet", fontsize=16)
    y += 17
    tp.insert_text((50, y), (f"{_bidsheet.scope_text(_bs_scope)} - {len(pages)} approved "
                             "sheet(s); a run on several sheets counts "
                             "once per sheet")[:118], fontsize=8)
    y += 11
    if _bs.get("ignored"):
        tp.insert_text((50, y), (f"{_bs['ignored']} row(s) unticked on the cross-sheet panel left out "
                                 "(as in the bid-sheet CSV)")[:118], fontsize=8)
        y += 11
    y += 5
    # an approval out of date is said here too, above the lines it feeds: this page printed 900 CY where the cut /
    # fill gives 576.1 with nothing said, while the CSV, the page's table and the summary said it (critic 21, RISK-1)
    # (at most six here, so the table head stays on the page - the summary page has them all: critic 22)
    _bw = [n for n in all_notes if _bid_warning(n)]
    for _nt in _bw[:6] + ([f"... and {len(_bw) - 6} more such note(s) on the summary page"] if len(_bw) > 6 else []):
        for _seg in (textwrap.wrap(str(_nt), 118) or [""]):
            tp.insert_text((50, y), _seg, fontsize=8, color=(0.7, 0.0, 0.0))
            y += 11
    # each page whose old Earthwork calculator work was removed, once (the summary page and its own page say it too)
    for _nt in [n for n in all_notes if str(n).endswith(EW_REMOVED_NOTE)]:
        for _seg in (textwrap.wrap(str(_nt), 118) or [""]):
            tp.insert_text((50, y), _seg, fontsize=8)
            y += 11
    for _x, _h in ((50, "#"), (70, "Item"), (300, "Unit"), (340, "Qty"), (420, "Rows")):
        tp.insert_text((_x, y), _h, fontsize=9)
    y += 14
    _bs_rows = list(_bs["lines"])
    if _bs["unmatched"]:
        _bs_rows.append({"n": "", "name": "Not on the bid sheet", "unit": "", "qty": None, "rows": [], "other": {}})
        _bs_rows += [{"n": "", "name": "   " + r["label"], "unit": r["unit"],
                      "qty": r.get("counted", r["qty"]), "rows": [r], "other": {}}
                     for r in _bs["unmatched"]]
    for _L in _bs_rows:
        # the whole item name, wrapped inside its column (it was cut at 44 characters:
        # "Removal of existing sidewalk (sub-item of" with the parent lost)
        _nm = str(_L["name"])
        _ind = len(_nm) - len(_nm.lstrip(" "))            # an unmatched row's indent, kept on every line
        _labl = [" " * _ind + s for s in
                 _report_cell_lines(_nm.lstrip(" "), _REPORT_BID_ITEM_W - pymupdf.get_text_length(" " * _ind, fontsize=9))]
        if y + _REPORT_CELL_LH * (len(_labl) - 1) > 740:
            tp = _ReportPage(out.new_page(width=612, height=792))
            _letterhead(tp)
            y = 60
        tp.insert_text((50, y), str(_L["n"]), fontsize=9)
        _report_cell_draw(tp, 70, y, _labl)
        tp.insert_text((300, y), str(_L["unit"]), fontsize=9)
        _qv = _L["qty"]
        _qs = "" if _qv is None or (_qv == 0 and not _L["rows"]) else f"{_qv:,.1f}".rstrip("0").rstrip(".")
        tp.insert_text((340, y), _qs, fontsize=9)
        _oth = ", ".join(f"{v:,g} {u} not summed" for u, v in (_L.get("other") or {}).items())
        tp.insert_text((420, y), (str(len(_L["rows"])) + (f" ({_oth})" if _oth else "")) if _L["rows"] else "",
                       fontsize=8)
        y += 12 + _REPORT_CELL_LH * (len(_labl) - 1)
    _beta = textwrap.wrap(BETA_LONG, 150) or [""]
    for _pg in map(_ReportPage, out):
        _w, _h = _pg.rect.width, _pg.rect.height
        _band = 10 + 8 * len(_beta)
        _pg.draw_rect(pymupdf.Rect(0, _h - _band, _w, _h),
                      color=None, fill=(0.97, 0.93, 0.85), overlay=True)
        _y = _h - _band + 9
        for _seg in _beta:
            _pg.insert_text((36, _y), _seg, fontsize=6.5, color=(0.45, 0.25, 0))
            _y += 8

    pdf_path = jd / "takeoff_report.pdf"
    # deflate=True, garbage=4 - NOT cosmetic. Measured on one client sheet
    # (engine page 6, 5460x3900 overlay at 130 dpi): out.save() alone writes
    # 63.89 MB against a 4.30 MB source stream, because the image goes in
    # uncompressed and the xref is never collected. With these two arguments
    # it is 3.45 MB - 18.5x smaller, pixels byte-identical (verified by
    # comparing re-rendered pixmaps). The ratio varies with ink density; a
    # sparser sheet measured 25.1x. A 15-sheet job was ~958 MB, i.e. the
    # product's only deliverable could not be emailed. Do not remove.
    out.save(pdf_path, deflate=True, garbage=4)
    return FileResponse(pdf_path, media_type="application/pdf",
                        filename=f"takeoff_{Path(_display_name(job['path'])).stem[:30]}.pdf")


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
    old = []
    for pno in pages:
        try:
            exp = json.loads((jd / f"p{pno}_elements.json").read_text())
        except (OSError, ValueError):
            continue
        why = _export_outdated(job_id, pno, exp)
        if why:
            old.append(why)
    un = _cf_rows_unapproved(job_id, pages)
    if un:
        old.append(un[1])
    # after every OUT OF DATE: each page whose old Earthwork calculator work was removed, once (one cell: the sentence)
    ew_notes = [_ew_note(job_id, pno) for pno in _ew_removed_pages(job_id)]
    res, scope = _bid_build(job_id, pages, _who(request), ignore)
    text = _bidsheet.to_csv(res, _display_name(job["path"]), scope)
    if old or ew_notes:
        # an approved page whose scale changed since, or the cut / fill rows in no approved page: said in the header, above
        # every number (critic 17 and 19, RISK-3; critic 22, RISK-2)
        import csv as _csv
        import io as _io
        head = _io.StringIO()
        wr = _csv.writer(head, lineterminator="\n")
        for why in old:
            label, _, rest = why.partition(": ")
            wr.writerow([label, rest])
        for n in ew_notes:
            wr.writerow([n])
        parts = text.split("\n", 4)
        text = "\n".join(parts[:4]) + "\n" + head.getvalue() + (parts[4] if len(parts) > 4 else "")
    # the accuracy flag, last, after a blank row - the way every CSV of the app carries it (the takeoff CSV's tail,
    # webui/app.js BETA_CSV_TAIL): the header notes above every number stay as they are (critic bid-content-1, RISK-5)
    text = text.rstrip("\n") + "\n\n" + _csv_flag_line() + "\n"
    # utf-8-sig, as the other CSV: Excel reads a plain UTF-8 file as the system codepage, and a grading
    # zone's row ("Site Cut – Island") printed as "Site Cut â€“ Island"
    return Response(text.encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="bidsheet_{Path(_display_name(job["path"])).stem[:30]}.csv"'})


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
               "caption we read by eye; 4 wrong, all four on one scanned "
               "set. Where the engine already has a scale it "
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


_FOOTING_SCHEDULE_V = 2     # cache stamp: bump when what a schedule may say changes


def _doc_footing_schedule(job_id: str, path: str) -> dict:
    """The footing schedule for this DOCUMENT, cached per job.

    A structural set prints its schedule once, on the notes sheet (Res-A
    S0-0.4, a foundation set's notes page), while the marks that need it sit on every
    foundation plan - so this reads the pages whose text says FOOTING
    SCHEDULE, and ONLY those, and keeps the first row it finds for each
    mark with the page it came from. Cached because it is the same answer
    for every page of the job.

    Why only those pages (audit 2026-09-26, D01): the first version went on
    to scan the rest of the set when no page said FOOTING SCHEDULE, and on
    Res-A civil C4.3 it read a wall-grade table's "CF3.0 ... TOF: 7715'" as
    a footing 7,715 ft wide. That row made the civil set "print a
    schedule", which is the only thing that switched the twin pass on, and
    the pass then ran with a neighbour radius wider than the sheet. A set
    that prints its table without the words is a set with no schedule read
    - said so in the reason, never guessed at. Rows outside the sane range
    (takeoff_engine.footing_callouts.schedule_row_problem) are listed under
    "rejected" with the reason, whichever page they came from.
    """
    from takeoff_engine.footing_callouts import read_footing_schedule, schedule_row_problem
    cache = _job_dir(job_id) / "footing_schedule.json"
    if cache.exists():
        try:
            cached = json.loads(cache.read_text())
            # a cache written before the range check may hold the 7,715 ft
            # row: only a stamped cache whose rows all pass is served
            if (cached.get("v") == _FOOTING_SCHEDULE_V
                    and not any(schedule_row_problem(r) for r in (cached.get("rows") or {}).values()
                                if isinstance(r, dict))):
                return cached
        except (OSError, ValueError, AttributeError):
            pass
    out = {"v": _FOOTING_SCHEDULE_V, "rows": {}, "pages": [], "scanned": 0, "rejected": []}
    try:
        doc = pymupdf.open(path)
    except Exception:
        return out
    try:
        n = doc.page_count
        for i in range(min(n, 80)):
            try:
                t = doc[i].get_text().upper()
            except Exception:
                t = ""
            if "FOOTING SCHEDULE" not in t:
                continue
            out["scanned"] += 1
            try:
                words = page_words(doc[i])
            except Exception:
                continue
            s = read_footing_schedule(words)
            for r in s.get("rejected") or []:
                out["rejected"].append(dict(r, page=i))
            if not s["found"]:
                continue
            out["pages"].append(i)
            for mark, row in s["rows"].items():
                out["rows"].setdefault(mark, dict(row, page=i))
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
    civil's [100'=5000'] and FFE SUMMARY levels, the structural note that
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
#
# WHAT KEEPS IT OFF A CIVIL SHEET (audit 2026-09-26, D01). The schedule was
# the only gate, and one misread row on Res-A civil C4.3 opened it for every
# civil sheet of the set: 103 of 217 families on C3.0, a neighbour radius
# wider than the sheet, ~29 minutes of O(n^2) work and 7-17 GB. Four fences
# now, each with a reason string the UI can show:
#   1. the schedule reader bounds its rows (1-20 ft) and reads only pages
#      that say FOOTING SCHEDULE (_doc_footing_schedule);
#   2. _twin_sheet_guard: never on a civil / grading sheet - by the printed
#      sheet number (C = civil) or the page's triage tier (its words);
#   3. a total-work cap per page: over _TWIN_MAX_PAGE_SAMPLES samples of
#      eligible linework the answer is "too big to check", not the work;
#   4. twin_lines counts neighbour pairs before listing them (MAX_PAIRS).
_TWIN_MIN_SHARE = 0.15      # a family offers the switch when this much of it pairs
_TWIN_MAX_FAMILY_SAMPLES = 60000
_TWIN_MAX_PAGE_SAMPLES = 120000     # Res-A S1-1.3 needs 18,264; Res-A civil C3.0 would need 416,724
_TWIN_KEY_V = "g2"                  # cache stamp: bump when the fences change


def _twin_sheet_guard(job_id: str, page: int, res: dict) -> str | None:
    """Why the twin pass must not run on this sheet, or None when it may.

    Foundation plans are structural sheets: a printed S / F sheet number
    (S1-1.3, F1.0) allows the pass whatever the words say. A C sheet
    (C3.0, C4.3) is civil and refused. Without a sheet number the page's
    own triage tier decides (triage_score on its text: GRADING, CONTOUR,
    STORM, SANITARY... make it "core"), and a set whose every page triaged
    core is a civil set. The reason names what decided it.

    Release repair 2026-10-01 (critic WRONG-1): a sheet numbered C is civil
    only when its letter prefix IS "C" (C3.0, C-101) - "CS-1" is a cover /
    sheet-index style prefix, not a C sheet, and goes to the words like any
    other unnumbered or other-trade sheet. And a sheet whose own title says
    it is a foundation / footing / slab plan (triage `kind`, from the title
    phrase - _TRIAGE_PLAN_KINDS) is allowed whatever its words score or its
    number says, except a C sheet: an architect-numbered foundation plan
    (A2.0, "2") lists EXCAVATE / BACKFILL / FOUNDATION DRAIN TO STORM in its
    general notes and triaged core. The schedule fence and the work cap
    in _page_twins are untouched."""
    sn = str(res.get("sheet_no") or "").strip().upper()
    m = re.match(r"[A-Z]+", sn)
    prefix = m.group(0) if m else ""
    letter = prefix[:1]
    if letter in ("S", "F"):
        return None
    if prefix == "C":
        return (f"a civil sheet ({sn}) - counting walls once is for foundation plans; "
                "the lines that run parallel here are contours, curbs and property lines")
    job = JOBS.get(job_id) or {}
    tier, hits, set_note, kind = None, [], None, None
    side = _job_dir(job_id) / "triage.json"
    if side.exists():
        try:
            tri = json.loads(side.read_text())
            if tri.get("version") == TRIAGE_VERSION and tri.get("mtime") == job.get("mtime"):
                rows = tri.get("pages") or []
                row = next((r for r in rows if r.get("page") == page), None)
                if row:
                    tier, hits, kind = row.get("tier"), row.get("hits") or [], row.get("kind")
                cnt = tri.get("counts") or {}
                if rows and cnt.get("core") == len(rows):
                    set_note = f"every one of the set's {len(rows)} sheets is grading / utility work"
        except (OSError, ValueError, AttributeError):
            tier = None
    if tier is None:
        try:
            doc = pymupdf.open(job["path"])
            try:
                txt = doc[page].get_text("text") if 0 <= page < doc.page_count else ""
            finally:
                doc.close()
        except Exception:
            txt = ""
        row = triage_score(txt, sn or None)
        tier, hits, kind = row.get("tier"), row.get("hits") or [], row.get("kind")
    if kind == "foundation plan":
        return None
    if tier == "core" or set_note:
        words = ", ".join(h[0] for h in hits[:4] if isinstance(h, (list, tuple)) and h)
        why = set_note or f"its words say grading / utility work ({words})"
        return (f"a civil sheet by its content - {why}; counting walls once is for "
                "foundation plans")
    return None


_NO_SCHEDULE_WHY = ("this set prints no footing schedule - "
                    "counting walls once is offered on foundation plans")


def _schedule_widths(sched: dict) -> list:
    """The document schedule's footing widths inside the sane range."""
    from takeoff_engine.footing_callouts import SCHEDULE_WIDTH_FT
    return sorted({round(float(r["width_ft"]), 3) for r in (sched.get("rows") or {}).values()
                   if isinstance(r, dict) and r.get("width_ft")
                   and SCHEDULE_WIDTH_FT[0] <= float(r["width_ft"]) <= SCHEDULE_WIDTH_FT[1]})


def _page_twins(job_id: str, page: int, res: dict | None = None) -> dict:
    """{"families": {style_id: {kind, rule, drawn_ft, counted_ft, paired_ft,
    interior_ft, middle_ft, refused, chains: {chain_id: {d, c, skip}}}},
    "reason"} for one page; cached. `reason` is set, and `families` empty,
    whenever the pass did not run: it says which fence stopped it."""
    import hashlib
    from takeoff_engine import twin_lines as _tw
    from takeoff_engine.footing_callouts import SCHEDULE_WIDTH_FT
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
    widths = _schedule_widths(sched)
    if not widths:
        return {"families": {}, "reason": _NO_SCHEDULE_WHY}
    fpp = det["ft_per_paper_inch"] / 72.0
    key = "|".join([_TWIN_KEY_V, str(res.get("engine_sig")), str(rf.stat().st_mtime_ns),
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

    def refuse(reason: str) -> dict:
        out = {"key": key, "families": {}, "reason": reason,
               "schedule_widths_ft": widths, "ft_per_pt": round(fpp, 6)}
        try:
            cf.write_text(json.dumps(out))
        except OSError:
            pass
        return out

    why = _twin_sheet_guard(job_id, page, res)
    if why:
        return refuse(why)
    by_sid: dict = {}
    for ch in res.get("chains") or []:
        if ch.get("class") == "annotation" or not ch.get("points") or len(ch["points"]) < 2:
            continue
        by_sid.setdefault(ch["style_id"], []).append(ch)
    # COUNT FIRST: the samples every eligible family would put on the grid,
    # from length_pt alone (no geometry touched). Over the page cap nothing
    # is paired and the answer says so.
    eligible = []
    total_samples = 0
    for sid, chs in by_sid.items():
        if len(chs) < 2:
            continue
        drawn_ft = sum(float(c.get("length_pt") or 0.0) for c in chs) * fpp
        if drawn_ft < 10.0 or drawn_ft / 0.5 > _TWIN_MAX_FAMILY_SAMPLES:
            continue
        eligible.append((sid, chs))
        total_samples += int(drawn_ft / 0.5)
    if total_samples > _TWIN_MAX_PAGE_SAMPLES:
        return refuse(f"too big to check: {len(eligible)} families, {total_samples:,} samples of "
                      f"linework, over the {_TWIN_MAX_PAGE_SAMPLES:,} this pass looks at on one sheet")
    fams = {}
    for sid, chs in eligible:
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
           "schedule_widths_ft": widths, "ft_per_pt": round(fpp, 6),
           "samples": total_samples}
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
    # WALLS GROUPED BY BOTTOM OF FOOTING (a client colours every wall by the
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
            # out when the twins are known (Res-A S1-1.3: 1,055 ft as drawn,
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
    wall (19 on Res-A S1-1.3, always a separate STEP word)."""
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
    return JSONResponse(_page_callouts(job_id, page))


def _page_callouts(job_id: str, page: int) -> dict:
    """The callouts route's work as a dict, so the run builder (pipe runs
    split at the structures the callouts name) can read the same rows
    without an HTTP round trip. Cached as p<N>_callouts.json."""
    import numpy as np

    job = JOBS.get(job_id) or _404()
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    cache = _job_dir(job_id) / f"p{page}_callouts.json"
    if cache.exists():
        try:
            return json.loads(cache.read_text())
        except (OSError, ValueError):
            pass
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
    return out


class RunsBody(BaseModel):
    # the utility families to split into runs (their current style ids)
    style_ids: list[int] = []
    # lines the operator deleted in the review UI
    exclude_chain_ids: list[int] = []
    # style_id -> the family's current name on screen (the system code and
    # the name-size are read from it)
    labels: dict | None = None
    # the picked families' CURRENT geometry when the operator has merged,
    # split or drawn lines since detection: [{chain_id, style_id, points,
    # inline_labels?}]. Replaces the stored chains of those families only;
    # every other family (the structure symbols live there) is read from
    # the result on disk.
    chains: list[dict] | None = None
    # True: a structure ends a run (manhole to manhole). False: the main runs
    # through its structures and breaks only where a lateral leaves it.
    split_at_structures: bool = True
    # the scale the review UI has ON SCREEN (a typed scale lives only in the
    # browser until Approve). Omitted = what the server has.
    scale_ft_per_in: float | None = None


@app.post("/api/job/{job_id}/page/{page}/runs")
def page_runs(job_id: str, page: int, body: RunsBody):
    """The picked utility families split into runs - structure to
    structure, through the wyes - with names, pipe sizes and the structure
    count (takeoff_engine.runs.build_runs). Structures come from the
    sheet's own callouts (Sheet callouts, computed here if not yet cached)
    and from closed symbols on structure-named layers."""
    from takeoff_engine.runs import build_runs
    from takeoff_engine.extract import page_lines

    job = JOBS.get(job_id) or _404()
    rf = _job_dir(job_id) / f"p{page}_result.json"
    if not rf.exists():
        raise HTTPException(404, "run detect first")
    res = json.loads(rf.read_text())
    det = _page_scale(job_id, page, res)
    fpp = det["ft_per_paper_inch"] / 72.0 if det else 0.0
    if body.scale_ft_per_in is not None and float(body.scale_ft_per_in) > 0:
        fpp = float(body.scale_ft_per_in) / 72.0      # the scale in force on screen
    sids = set(int(s) for s in body.style_ids)
    chains = list(res.get("chains") or [])
    if body.chains:
        sent = []
        for c in body.chains:
            try:
                pts = [[float(p[0]), float(p[1])] for p in (c.get("points") or [])]
                if len(pts) < 2:
                    continue
                sent.append({"chain_id": int(c.get("chain_id")), "style_id": int(c.get("style_id")),
                             "points": pts, "class": str(c.get("class") or "solid"),
                             "inline_labels": [str(x) for x in (c.get("inline_labels") or [])][:12],
                             "dash_signature": str(c.get("dash_signature") or "")})
            except (TypeError, ValueError):
                continue
        chains = [c for c in chains if c.get("style_id") not in sids] + sent
    try:
        callouts = _page_callouts(job_id, page).get("callouts") or []
    except HTTPException:
        callouts = []
    doc = pymupdf.open(job["path"])
    try:
        words = page_lines(doc[page]) if 0 <= page < doc.page_count else []
    finally:
        doc.close()
    labels = {int(k): str(v) for k, v in (body.labels or {}).items()
              if str(k).lstrip("-").isdigit() and isinstance(v, str)}
    # one graph per SYSTEM: the storm families join each other's runs, a
    # water service ending on a sewer line does not become a tee in it
    from takeoff_engine.runs import system_code
    st_by = {s.get("style_id"): s for s in res.get("styles") or []}
    groups: dict = {}
    for sid in sorted(sids):
        s = st_by.get(sid) or {}
        nm = labels.get(sid) or str(s.get("suggested_label") or s.get("layer") or "")
        groups.setdefault(system_code(nm, str(s.get("layer") or "")), []).append(sid)
    out = {"systems": [], "runs": [], "structures": [], "structures_on_runs": 0,
           "structure_symbols": 0, "junctions": [], "notes": [],
           "split_at_structures": body.split_at_structures,
           "ft_per_pt": fpp, "unit": "ft" if det else "pt"}
    for code, group in groups.items():
        g = build_runs(chains, res.get("styles") or [], group, fpp, words=words,
                       callouts=callouts, labels=labels,
                       exclude_chain_ids=body.exclude_chain_ids,
                       split_at_structures=body.split_at_structures)
        base = len(out["runs"])
        sbase = len(out["structures"])
        for r in g["runs"]:
            r["id"] = base + r["id"]
            r["structures"] = [sbase + x for x in r["structures"]]
            r["from_sym"] = None if r["from_sym"] is None else sbase + r["from_sym"]
            r["to_sym"] = None if r["to_sym"] is None else sbase + r["to_sym"]
            r["style_ids"] = group
        for s in g["structures"]:
            s["id"] = sbase + s["id"]
            s["system"] = code
        out["runs"].extend(g["runs"])
        out["structures"].extend(g["structures"])
        out["structures_on_runs"] += g["structures_on_runs"]
        out["structure_symbols"] += g["structure_symbols"]
        out["junctions"].extend(g["junctions"])
        # a group with no line work (a family of annotation strokes) says so
        # once, and not at all when another group has runs
        for n in g["notes"]:
            if n not in out["notes"]:
                out["notes"].append(n)
        out["systems"].append({"code": code, "families": g["families"],
                               "runs": [r["id"] for r in g["runs"]],
                               "structures_on_runs": g["structures_on_runs"],
                               "length_ft": round(sum(r["length_ft"] or 0 for r in g["runs"]), 1)})
    if out["runs"]:
        out["notes"] = [n for n in out["notes"] if n != "no lines in the picked families"]
    return JSONResponse(out)


_TD_WIDTH = re.compile(r"(?i)\b(?:Bc|O\.?\s?D\.?|PIPE(?:\s+O\.?\s?D\.?)?|DIA(?:METER)?\.?)\s*\+\s*(\d{1,2})\s*(?:\"|”|'')")
_TD_COVER = re.compile(r"(?i)(\d{1,2}(?:\.\d)?)\s*['’′]?\s*(?:FT\.?|FEET)?\s*(?:MIN(?:IMUM)?\.?\s*)?(?:DEPTH\s+OF\s+)?(?:COVER|BURY)\b")
_TD_COVER2 = re.compile(r"(?i)MIN(?:IMUM)?\.?\s*(?:DEPTH\s+OF\s+)?(?:COVER|BURY)\D{0,14}(\d{1,2}(?:\.\d)?)\s*['’′]")
# a bedding depth is a number with BEDDING / PIPE ZONE in the same sentence,
# or a number so many inches BELOW / ABOVE the PIPE: "4" BELOW GRADE" and
# "2' ABOVE WATER LINE" (marker tape) are not bedding, and the depths drawn
# as dimension figures on a detail (6", 12") never reach the text at all
_TD_BED = re.compile(r"(?i)(\d{1,2})\s*(?:\"|”|'')\s*(?:(?:OF\s+|MIN\.?\s+)?(?:BEDDING|BED|PIPE ZONE|GRAVEL BEDDING|SAND BEDDING)\s*)?(BELOW|UNDER|BENEATH|ABOVE|OVER)\s+(?:THE\s+)?(?:PIPE|BARREL|CROWN|INVERT)\b"
                     r"|(\d{1,2})\s*(?:\"|”|'')\s*(?:MIN\.?\s+)?(BEDDING|PIPE ZONE)\b")
_TD_UTIL = re.compile(r"(?i)\b(WATER|SEWER|SANITARY|STORM|DRAIN|GAS|ELECTRIC|UTILITY)\b")
TD_PAGES_MAX = 60


@app.get("/api/job/{job_id}/trench-details")
def trench_details(job_id: str):
    """What the set PRINTS about trench geometry, read off its text: a
    width rule (Bc + 18", O.D. + 24"), a minimum cover (7' MIN. COVER,
    6.5' MIN. BURY) and bedding depths (6" BELOW, 12" ABOVE), each with
    the page and the sentence it came from, and the utility the sentence
    names. The trench tool offers these as the "printed detail" width
    convention and fills the preset's cover from them; nothing is applied
    without the operator seeing the sentence. First TD_PAGES_MAX pages of
    text, cached as trench_details.json."""
    from takeoff_engine.extract import page_lines

    job = JOBS.get(job_id) or _404()
    cache = _job_dir(job_id) / "trench_details.json"
    if cache.exists():
        try:
            return JSONResponse(json.loads(cache.read_text()))
        except (OSError, ValueError):
            pass
    out = {"widths": [], "covers": [], "bedding": [], "pages_read": 0}
    doc = pymupdf.open(job["path"])
    try:
        # the first TD_PAGES_MAX pages (a set's details sit at the front or
        # the back; the back of a 300-page set is not read) plus every page
        # the operator has detected - the sheet being worked on prints its
        # own minimum bury more often than not
        pages = set(range(min(doc.page_count, TD_PAGES_MAX)))
        for f in _job_dir(job_id).glob("p*_result.json"):
            try:
                pages.add(int(f.name[1:].split("_")[0]))
            except ValueError:
                continue
        for p in sorted(pages):
            if p < 0 or p >= doc.page_count:
                continue
            try:
                lines = page_lines(doc[p])
            except Exception:
                continue
            out["pages_read"] += 1
            for ln in lines:
                t = " ".join(str(ln[4]).split())
                if not t:
                    continue
                util = _TD_UTIL.search(t)
                u = util.group(1).upper() if util else None
                m = _TD_WIDTH.search(t)
                if m:
                    out["widths"].append({"page": p, "each_side_in": int(m.group(1)) / 2.0,
                                          "total_in": int(m.group(1)), "text": t[:100], "utility": u,
                                          "_box": [float(ln[0]), float(ln[1]), float(ln[2]), float(ln[3])]})
                m = _TD_COVER.search(t) or _TD_COVER2.search(t)
                if m:
                    try:
                        out["covers"].append({"page": p, "cover_ft": float(m.group(1)), "text": t[:100], "utility": u,
                                              "_box": [float(ln[0]), float(ln[1]), float(ln[2]), float(ln[3])]})
                    except ValueError:
                        pass
                for m in _TD_BED.finditer(t):
                    inches = m.group(1) or m.group(3)
                    word = (m.group(2) or "BELOW").upper()
                    side = "below" if word in ("BELOW", "UNDER", "BENEATH") else "above"
                    try:
                        out["bedding"].append({"page": p, "in": int(inches), "side": side, "text": t[:100], "utility": u,
                                               "_box": [float(ln[0]), float(ln[1]), float(ln[2]), float(ln[3])]})
                    except (TypeError, ValueError):
                        pass
            # A sentence that names no utility takes the utility of the NEAREST
            # detail title on its sheet ("TYPICAL WATER LINE TRENCH DETAIL"): the
            # details sheet carries water, storm and sewer details side by side,
            # so one utility per page would name none of them (and the first
            # cut handed the set's first cover to every preset).
            titles = []
            for ln in lines:
                t = " ".join(str(ln[4]).split()).upper()
                if ("DETAIL" in t or "TRENCH" in t or "SECTION" in t) and len(t) <= 60:
                    m = _TD_UTIL.search(t)
                    if m and m.group(1).upper() != "UTILITY":
                        titles.append((float(ln[0]), float(ln[1]), float(ln[2]), float(ln[3]), m.group(1).upper(), t))
            if titles:
                for k in ("widths", "covers", "bedding"):
                    for row in out[k]:
                        if row["page"] != p or row.get("utility") or "_box" not in row:
                            continue
                        x0, y0, x1, y1 = row.pop("_box")
                        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
                        best = None
                        for tx0, ty0, tx1, ty1, u, t in titles:
                            d = ((cx - (tx0 + tx1) / 2) ** 2 + (cy - (ty0 + ty1) / 2) ** 2) ** 0.5
                            if d <= 450 and (best is None or d < best[0]):
                                best = (d, u, t)
                        if best:
                            row["utility"] = best[1]
                            row["utility_from"] = f"the nearest detail title: {best[2][:50]}"
            for k in ("widths", "covers", "bedding"):
                for row in out[k]:
                    row.pop("_box", None)
            if len(out["widths"]) + len(out["covers"]) + len(out["bedding"]) > 200:
                break
    finally:
        doc.close()
    try:
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return JSONResponse(out)


def _profile_reader_sig() -> str:
    """Hash of takeoff_engine/profile.py: the profile cache is keyed on it,
    so a reader edit re-reads every page once instead of serving the old
    read as current (the same rule the cut/fill reading cache follows)."""
    import hashlib
    try:
        raw = (Path(__file__).parent / "takeoff_engine" / "profile.py").read_bytes()
    except OSError:
        return "none"
    return hashlib.sha1(raw).hexdigest()[:12]


@app.get("/api/job/{job_id}/page/{page}/profile")
def page_profile(job_id: str, page: int, refresh: int = 0):
    """The sheet's profile graph(s), read for the trench tool: each region's
    station and elevation axes (snapped to the grid), every ground / grade
    line and every pipe sampled per station, and the printed elevations on
    the graph compared with the read (takeoff_engine.profile.read_profile).

    Off the detection path on purpose: nothing here touches the chains or
    the result file, and profile.py is not in engine_sig. Cached as
    p<N>_profile.json under the reader's own hash; `refresh=1` re-reads.
    A sheet with no profile answers {"profiles": []} - the trench tool then
    falls back to typed end depths and says so."""
    from takeoff_engine import profile as _profile
    from takeoff_engine.extract import extract_strokes

    job = JOBS.get(job_id) or _404()
    cache = _job_dir(job_id) / f"p{page}_profile.json"
    sig = _profile_reader_sig()
    if cache.exists() and not refresh:
        try:
            d = json.loads(cache.read_text())
            if isinstance(d, dict) and d.get("reader") == sig:
                return JSONResponse(d)
        except (OSError, ValueError):
            pass
    doc = pymupdf.open(job["path"])
    try:
        if page < 0 or page >= doc.page_count:
            raise HTTPException(404, "no such page")
        pg = doc[page]
        units = extract_strokes(pg)
        regions = _profile.find_profiles(pg, units)
        out = {"reader": sig, "page": page, "profiles": [], "notes": []}
        for r in regions:
            if r.box[2] <= r.box[0]:
                out["notes"].append("a profile is stated by caption only (two scales) but "
                                    "its graph was not located")
                continue
            rd = _profile.read_profile(pg, r, units)
            if rd is None:
                out["notes"].append("a profile box was found but its axis labels do not fit "
                                    "an axis well enough to read from: " + r.why[:160])
                continue
            out["profiles"].append(rd.to_dict())
    finally:
        doc.close()
    try:
        cache.write_text(json.dumps(out))
    except OSError:
        pass
    return JSONResponse(out)


def _doc_schedule(job_id: str, path: str, page: int) -> dict:
    """The mark schedule for this DOCUMENT, not just this page.

    An estimator's schedule usually lives on its own sheet - a client's
    apartment set puts the window schedule on A830 (page 0) and the floor
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
                    up = _marks._rot_ccw(img, deg)
                    ow = ocr_words(up, px_to_pt=72.0 / dpi)
                    # a mark-column cell the page read garbled is read
                    # again on its own box, table rules stripped, and a
                    # row the page read never saw is found from the
                    # table's rules (apartment set: W2L read "WAT", W3L a
                    # rule fragment). takeoff_engine.marks_naming, not
                    # marks: naming code lives outside the detection
                    # fingerprint and under SYMBOLS_ALGO instead.
                    from takeoff_engine import marks_naming as _mn
                    s = _mn.read_scanned_schedule(
                        list(ow),
                        reread=lambda w: _mn.read_cell(up, w, dpi / 72.0),
                        cells=lambda hw: _mn.column_cells(up, hw, dpi / 72.0))
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
SYMBOLS_ALGO = 7   # 6: scanned schedule cells re-read, prose vocab refused, tags named off themselves when the set has no schedule
                   # 7: the answer names the page of the schedule that was USED (a sheet's own schedule is not the set's)


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
            # quantity in the number a client faxes a supplier. SYMBOLS_ALGO
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

    # every table on the sheet, not only the largest: a window schedule
    # and a door schedule stacked in one corner are two boxes, and the
    # mark cells of the second one counted as a symbol family (house set
    # A2.1: a 24x12 pt "1 / 2 / 3 / A / B / C / D" family of one each)
    table_boxes = [t.get("box") for t in (here.get("tables") or []) if t.get("box")]
    if table_box and table_box not in table_boxes:
        table_boxes.append(table_box)

    def _in_table(c):
        return any(b[0] <= c[0] <= b[2] and b[1] <= c[1] <= b[3] for b in table_boxes)

    vocab_set = set(str(v).strip().upper() for v in (vocab or []))

    def _mark_at(c):
        # the mark text lives INSIDE its bubble (measured 0.7pt): the word
        # box containing (or within 3pt of) the symbol center names it.
        # A bare "2" or "B" names a bubble only when the sheet's own
        # schedule lists it - otherwise any digit near a shape would.
        for w in words:
            t = str(w[4]).strip().upper()
            if not MARK_WORD.match(t) and t not in vocab_set:
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
                # which schedule lists this mark: "WINDOW NO." -> WINDOW, so
                # the panel can call the group "WINDOW 2" and not just "2"
                for t in here.get("tables") or []:
                    if m in (t.get("marks") or []):
                        word = str(t.get("header") or "").replace("NO.", "").replace("TYPE", "").strip()
                        if word:
                            g["table"] = word
                        break
                out.append(g)
            if len(unmarked) >= 3:
                out.append({"w": w, "h": h, "l4": l4, "n": len(unmarked),
                            "centers": unmarked})
        else:
            out.append({"w": w, "h": h, "l4": l4, "n": len(uniq),
                        "centers": uniq})
    out.sort(key=lambda g: -g["n"])

    # Legend-glyph naming (a client's missed manhole) was BUILT AND REMOVED
    # here on 2026-08-30 - three versions, each measured on a civil set's C5.1:
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
    # the schedule the counts were CHECKED against: this sheet's own when it prints one (its
    # quantities are this sheet's), else the set's. The page travels with it: a sheet that
    # prints its own schedule answered "schedule on sheet 1" and the panel took its 7 of 8
    # for one sheet of a set-wide quantity (check 5B, 2026-09-27)
    own = bool(here.get("qty"))
    return {"groups": out[:40], "groups_total": len(out), "schedule": sched,
            "schedule_marks": (here.get("marks") if own else None) or doc_sched.get("marks") or [],
            "schedule_page": page if own else doc_sched.get("page"),
            "schedule_header": (here.get("header") if own else None) or doc_sched.get("header", ""),
            "schedule_source": "text" if own else doc_sched.get("source", "none")}


def _symbols_raster(job: dict, page: int, vocab: list, sched: dict,
                    doc_sched: dict) -> dict:
    """Scanned sheet: count mark bubbles as pictures (takeoff_engine.marks).

    Same {groups, schedule} contract as the vector path, so the Count panel
    needs no idea which finder ran. Centres come back in page points."""
    from takeoff_engine import marks as _marks
    from takeoff_engine import marks_naming as _mn

    # the detector counts; the naming post-pass (marks_naming.finish_naming)
    # reads what the word read left blank - glyph by glyph under the
    # schedule's shape, or off the tag itself when the set has no schedule.
    # Naming lives outside the detection fingerprint, under SYMBOLS_ALGO.
    dbg: dict = {}
    mp = _marks.detect_marks(job["path"], page, vocab or None, debug=dbg)
    from_tag = _mn.finish_naming(mp, dbg, vocab or None)
    by: dict = {}
    for t in mp.tags:
        if t.kind == "legend":
            continue
        # three buckets, not two: a schedule mark, a bubble read confidently
        # as something the schedule does not list (a neighbouring tag family
        # - the WALL tags read L04/L06 on a client's floor plans), and a bubble
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
            # named off the tag itself: this set carries no schedule, so
            # there is no printed quantity to reconcile against
            if code in from_tag:
                g["from_tag"] = True
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
    walls (a client, 2026-09-15, 32:49)."""
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

    An excavation client's own rule (call of 2026-09-15, 28:00-31:17; bid
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

    A client asked for exactly this on 2026-08-21: "I want to know that I
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


# ---- ROOMS (flooring) --------------------------------------------------------
class RoomPickBody(BaseModel):
    x: float
    y: float
    styles: list[int] | None = None     # visible style ids = "these are walls"
    dpi: int = 100
    # operator-drawn segments [[x0,y0,x1,y1], ...] in pt that count as wall
    closures: list[list[float]] | None = None
    # the scale the review UI has ON SCREEN (a typed scale lives only in the
    # browser until Approve; a pick is on disk). Omitted = what the server
    # has: the reviewer's recorded pick, else the engine's reading.
    scale_ft_per_in: float | None = None


def _scale_sites(job: dict, job_id: str, page: int) -> list:
    """Every printed scale caption on the page with its title-bar rule, as
    scale_detect._caption_sites finds them, cached beside the page's other
    sidecars (extract_strokes is the cost). Each site: {fpi, text, box,
    rule, compound}. Never raises: no sites means no inset override."""
    from takeoff_engine.scale_detect import _caption_sites, _page_linework

    cache = _job_dir(job_id) / f"p{page}_scale_sites.json"
    key = f"{job.get('mtime')}|1"
    try:
        d = json.loads(cache.read_text(encoding="utf-8"))
        if d.get("key") == key:
            return d.get("sites") or []
    except (OSError, ValueError, AttributeError):
        pass
    sites: list = []
    try:
        doc = pymupdf.open(job["path"])
        try:
            pg = doc[page]
            words = page_words(pg)
            _paths, hlines, _div = _page_linework(pg)
            sites = _caption_sites(words, hlines)
        finally:
            doc.close()
    except Exception:
        sites = []
    out = [{"fpi": float(s["fpi"]), "text": str(s["text"]),
            "box": [round(float(v), 1) for v in s["box"]],
            "rule": ([round(float(v), 1) for v in s["rule"][:3]] if s.get("rule") else None),
            "compound": bool(s.get("compound"))} for s in sites]
    try:
        cache.write_text(json.dumps({"key": key, "sites": out}), encoding="utf-8")
    except OSError:
        pass
    return out


def _site_scale_at(sites: list, x: float, y: float) -> dict | None:
    """The caption site whose view a point sits in: the same assignment
    scale_detect._governance makes per stroke - the caption BENEATH the
    point with the least cost (vertical gap to its title-bar rule plus the
    horizontal excess outside the rule's span). The document's convention:
    a detail's title bar sits below its drawing. None when no caption sits
    below the point, or the nearest one is compound (one printed line that
    yielded two values - nothing downstream can tell them apart)."""
    from takeoff_engine.scale_detect import _RULE_BELOW

    best, bc = None, None
    for s in sites or []:
        if s.get("rule"):
            rx0, rx1, ry = s["rule"][0], s["rule"][1], s["rule"][2]
        else:
            rx0, _y0, rx1, ry = s["box"]
        if ry < y - _RULE_BELOW:
            continue                   # caption sits above this: not its view
        # A view is the drawing ABOVE its title bar, and a title bar spans
        # its view: a point beside the bar's span is another view's. The
        # per-stroke proxy charges horizontal excess as cost and lets a
        # nearer bar win a point off its end - house set A2.1's RV GARAGE
        # (x 838, beside the 1/8" inset's bar at x 138-540) came back at
        # the inset's scale, four times its size. Slack for a drawing that
        # overhangs its bar a little (the inset's BED #3 sits 40 pt left of
        # the bar's start).
        slack = max(40.0, 0.1 * (rx1 - rx0))
        if x < rx0 - slack or x > rx1 + slack:
            continue
        cost = ry - y
        if bc is None or cost < bc:
            best, bc = s, cost
    if best is None or best.get("compound"):
        return None
    return best


def _room_pick_scale(job: dict, job_id: str, page: int, res: dict,
                     body: "RoomPickBody") -> tuple:
    """(ft per paper inch, who set it) for a room pick at (body.x, body.y).

    THE SCALE IN FORCE, in the order the rest of the product uses it: the
    scale the review UI has on screen (its typed box or its caption pick),
    else the reviewer's recorded pick (p<N>_scale.json), else the engine's
    reading. Then ONE override: a click inside an INSET - a view whose own
    printed caption governs a different value from the sheet's default
    (house set A2.1: the FLOORING PLAN at 1/8" beside the 1/4" plan) -
    takes that caption's value, because the drawing under the click was
    drawn at it. The override needs an engine default to differ FROM: on a
    page the engine read no scale for, the typed scale stands everywhere.
    A refused (out of band) on-screen number is ignored with the same rule
    the approve route applies, never substituted silently: the reply's
    `scale_by` says what was used."""
    ft, by = None, None
    sent = _sane_ft(body.scale_ft_per_in) if body.scale_ft_per_in is not None else None
    if sent:
        ft, by = sent, "on screen"
    else:
        det = _page_scale(job_id, page, res)
        if det and _sane_ft(det.get("ft_per_paper_inch")):
            ft = float(det["ft_per_paper_inch"])
            by = "reviewer" if det.get("set_by") == _BY_REVIEWER else "engine"
    eng = (res.get("scale") or {}).get("ft_per_paper_inch")
    try:
        eng = float(eng) if eng else None
    except (TypeError, ValueError):
        eng = None
    if eng:
        site = _site_scale_at(_scale_sites(job, job_id, page), float(body.x), float(body.y))
        if site and abs(float(site["fpi"]) - eng) > 1e-6 and _sane_ft(site["fpi"]):
            return float(site["fpi"]), f"inset caption {site['text']}"
    return ft, by


@app.post("/api/job/{job_id}/page/{page}/rooms/pick")
def rooms_pick(job_id: str, page: int, body: RoomPickBody):
    """The room enclosing a click, off the detected walls of the visible
    styles, door openings bridged. takeoff_engine/rooms.py has the method
    and its limits; this route only feeds it the result, the page's words,
    the scale in force (see _room_pick_scale) and a cache key. Needs a
    detection result: the walls ARE the chains."""
    from takeoff_engine import rooms as _rooms
    job = JOBS.get(job_id) or _404()
    f = _job_dir(job_id) / f"p{page}_result.json"
    if not f.exists():
        raise HTTPException(409, "run Detect on this page first - the room is found between its detected walls")
    try:
        res = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(500, "could not read this page's detection result")
    dpi = max(50, min(200, int(body.dpi or 100)))
    styles = set(int(v) for v in body.styles) if body.styles is not None else None
    try:
        doc = pymupdf.open(job["path"])
        try:
            pg = doc[page]
            words = pg.get_text("words")
            # get_text answers in the UNROTATED page frame; the chains and
            # the rendered image are in the rotated one (house set A2.1 is
            # stored /Rotate 270 and every room label landed on the wrong
            # room until this). Same fix the rest of the engine applies.
            if pg.rotation:
                M = pg.rotation_matrix
                words = [tuple(pymupdf.Rect(w[:4]) * M) + tuple(w[4:]) for w in words]
            strokes = _page_layer_strokes(job, job_id, page, pg)
        finally:
            doc.close()
    except Exception:
        words = []
        strokes = []
    try:
        stamp = f.stat().st_mtime
    except OSError:
        stamp = 0
    # ANNOTATION INK NEVER ACTS AS WALL. The layer names of the ticked
    # styles say what a wall is on this sheet; dimension / text strokes the
    # chain stage fused into a wall chain are taken out of the wall raster
    # (rooms.annotation_mask). Part of the cache key: the same styles with
    # and without the mask are two rasters.
    wall_layers = set()
    if styles is not None:
        for s in res.get("styles") or []:
            if isinstance(s, dict) and s.get("style_id") in styles and str(s.get("layer") or "").strip():
                wall_layers.add(str(s.get("layer")).strip())
    pg_meta = res.get("page") or {}
    anno = extra = None
    if strokes and wall_layers:
        anno, extra = _rooms.annotation_mask(strokes, wall_layers,
                                             float(pg_meta.get("width_pt") or 0),
                                             float(pg_meta.get("height_pt") or 0), dpi)
    layered = anno is not None or extra is not None
    key = _rooms._cache_key(job_id, page, dpi, styles, stamp,
                            anno_tag=("layers", tuple(sorted(wall_layers))) if layered else None)
    scale, scale_by = _room_pick_scale(job, job_id, page, res, body)
    try:
        return _rooms.pick_room(res, words, float(body.x), float(body.y),
                                styles, dpi, cache_key=key,
                                closures=body.closures or None,
                                scale=scale, scale_by=scale_by,
                                anno=anno, extra=extra)
    except ValueError as e:
        raise HTTPException(422, str(e))


# (job id, page) -> (pdf mtime, [(layer, pts), ...]): the page's stroke units
# with their CAD layer names, kept for the room pick's annotation mask.
# extract_strokes costs seconds on a dense sheet; a dozen picks reuse one.
_PAGE_STROKES: "OrderedDict" = OrderedDict()
_PAGE_STROKES_MAX = 6


def _page_layer_strokes(job: dict, job_id: str, page: int, pg) -> list:
    key = (job_id, page)
    mtime = job.get("mtime")
    hit = _PAGE_STROKES.get(key)
    if hit and hit[0] == mtime:
        _PAGE_STROKES.move_to_end(key)
        return hit[1]
    out = []
    try:
        for u in extract_strokes(pg):
            lay = str(u.layer or "").strip()
            if not lay or len(u.pts) < 2:
                continue
            out.append((lay, u.pts.astype(float)))
    except Exception:
        out = []
    _PAGE_STROKES[key] = (mtime, out)
    while len(_PAGE_STROKES) > _PAGE_STROKES_MAX:
        _PAGE_STROKES.popitem(last=False)
    return out


@app.get("/api/job/{job_id}/page/{page}/scale/at")
def scale_at(job_id: str, page: int, x: float, y: float,
             scale_ft_per_in: float | None = None):
    """The scale that governs one point of the sheet - the same decision a
    room pick makes (see _room_pick_scale), for a drawn outline: an outline
    traced inside an inset is measured at the inset's caption, not the
    sheet's default. Read-only."""
    job = JOBS.get(job_id) or _404()
    f = _job_dir(job_id) / f"p{page}_result.json"
    res: dict = {}
    if f.exists():
        try:
            res = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            res = {}
    body = RoomPickBody(x=x, y=y, scale_ft_per_in=scale_ft_per_in)
    ft, by = _room_pick_scale(job, job_id, page, res, body)
    return {"ft_per_paper_inch": ft, "scale_by": by}


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
    # them both. Measured on Res-A civil engine page 2 = printed sheet 3 (C3.0):
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


# ---------- CUT / FILL: the cut / fill engine, mounted under /cutfill ---------
# It replaced the earthwork calculator on 2026-09-25. The engine is its own
# package (cutfill/) with its own project store, kept under the job store; it
# is served by this process behind the same session cookie, and its UI
# (webui/cutfill/) runs embedded in the review UI's Cut / fill stage. A job's
# cut / fill project is keyed by the job id and made on first open from the
# job's own PDF (no copy). Nothing in this block measures anything.
os.environ.setdefault("CUTFILL_OUT", str(JOBS_DIR / "cutfill"))
os.environ.setdefault("CUTFILL_UI_DIR", str(ROOT / "webui" / "cutfill"))
from cutfill import server as _cutfill  # noqa: E402  - reads the two settings above at import
from cutfill.project import Project as _CutfillProject  # noqa: E402
from cutfill.project import scale_txt as _cf_scale_txt  # noqa: E402  - a scale in a sentence: two decimals at most
# the sheet the engine shows (its frame holds the volumes) and the roles that take part: the engine's own rule
from cutfill.placement import ACTIVE_ROLES as _CF_ACTIVE, display_for as _cf_display_for  # noqa: E402
from cutfill import readslot as _cf_readslot  # noqa: E402
from cutfill import placement as _cf_placement  # noqa: E402  - the scale check asks the review live

_CUTFILL_LOCK = threading.Lock()


def _cutfill_job_dir(job_id: str) -> "Path | None":
    """The engine's store for a job, or None when the id would not resolve
    to a direct child of JOBS_DIR/cutfill (the same walk-out guard as
    _sweep_job_dir)."""
    root = (JOBS_DIR / "cutfill").resolve()
    d = (root / f"job_{job_id}").resolve()
    return d if d.parent == root else None


def _cutfill_store_size(job_id: str) -> tuple:
    """(files, bytes) in the engine's store for a job - the reset preview."""
    d = _cutfill_job_dir(job_id)
    if d is None or not d.is_dir():
        return (0, 0)
    n = size = 0
    for p in d.rglob("*"):
        if p.is_file():
            n += 1
            try:
                size += p.stat().st_size
            except OSError:
                pass
    return (n, size)


def _cutfill_forget(job_id: str) -> dict:
    """Drop everything the cut / fill engine holds for a job: the cached
    Project (whose open pymupdf handles kept the PDF from being deleted on
    Windows), its in-memory readings and results, and the store on disk
    (JOBS_DIR/cutfill/job_<id>: datum, limit, typed heights, readings,
    analysis, volumes). Delete, reset and a demo's fresh open all call it.
    Returns {"evicted", "dir_removed", "removed", "failed"}; a folder that
    cannot be removed is reported, never counted as gone."""
    import shutil
    pid = f"job_{job_id}"
    out = {"evicted": False, "dir_removed": False, "removed": 0, "failed": 0}
    with _CUTFILL_LOCK:
        p = _cutfill._projects.pop(pid, None)
        _cutfill._results.pop(pid, None)
        if p is not None:
            out["evicted"] = True
            # its reads run to their end on the closed project: they still count against the server's two
            # (critic 4, r5: reads the engine itself started were not counted after a reset - 4 at once)
            _cf_reader(p)
            try:
                p.close()
            except Exception:
                pass
    d = _cutfill_job_dir(job_id)
    if d is None or not d.is_dir():
        return out
    files = [q for q in d.rglob("*") if q.is_file()]
    # Windows releases a handle a moment after close(): a short retry, then
    # the honest count of what is still there.
    for _i in range(20):
        try:
            shutil.rmtree(d)
        except OSError:
            time.sleep(0.05)
        if not d.exists():
            break
    if d.exists():
        left = [q for q in d.rglob("*") if q.is_file()]
        out["failed"] = len(left)
        out["removed"] = len(files) - len(left)
    else:
        out["dir_removed"] = True
        out["removed"] = len(files)
    return out


def _cutfill_project_ok(pid: str) -> bool:
    """Only a job in the registry is a cut / fill project on this box: a
    folder left behind by an older delete, or a project made some other
    way, is neither listed nor opened (audit 2026-09-26, D06)."""
    return pid.startswith("job_") and pid[4:] in JOBS


_cutfill.PROJECT_OK = _cutfill_project_ok
# the accuracy flag the engine's own CSV export ends with: the server's words, never a copy (critic bid-content-1, RISK-5)
_cutfill.EXPORT_NOTICE = lambda: _csv_flag_line()


class CutfillOpenBody(BaseModel):
    page: int = 0
    # what the reviewer did to this sheet's lines on the review screen, as it stands now:
    # {excluded_chain_ids, added_chains: [{points}], labels: {style id: label}, chain_labels:
    # {chain id: label}, work_zones: [[x0, y0, x1, y1]]}. None = not sent (what the engine
    # holds stands); an empty dict = nothing erased, named or drawn.
    edits: dict | None = None
    # the sheets ticked in "Sheets for cut / fill" (Koby 2026-09-28: "why do we have to go back and press
    # detect and cut/fill on both sheets"): each detected one joins the job's cut / fill as both surfaces (a
    # sheet already in it keeps its role), hands over its detection, and starts being read. None = only the
    # sheet on screen, as before (the first sheet opened is the grading one, a later one starts as "none").
    pages: list[int] | None = None


_CF_HANDED = ("read in review", "confirmed in review")
_CF_SHEETS_CACHE: dict = {}          # job id -> (PDF mtime, [printed sheet number per page])
# the host's own title-block reading (the page strip's, triage.json) where the engine reads none, only when it is
# shaped like a sheet number: one or two letters, a number, an optional decimal ("C4.1", "C-04"), or "3.1". A
# longer run of letters is a lot or a note: the host read "LOT910", a lot label, on a subdivision sheet that prints 3.0
_CF_SHEET_NO = re.compile(r"^(?:[A-Z]{1,2}-?\d{1,3}(?:\.\d{1,2})?[A-Z]?|\d{1,2}\.\d{1,2})$")


def _cf_engine_labels(job: dict, job_id: str) -> list:
    """The cut / fill engine's reading of every page's sheet number (the title block's largest short number: 3.0,
    3.1, C4.0), cached per PDF version (9 s cold on a 132-page set)."""
    key = (job.get("mtime"), int(job.get("pages") or 0))
    hit = _CF_SHEETS_CACHE.get(job_id)
    if hit and hit[0] == key:
        return hit[1]
    labels = []
    try:
        from cutfill.ingest import Document as _CfDoc
        d = _CfDoc(job["path"])
        try:
            labels = [d.page_info(i).label or "" for i in range(len(d))]
        finally:
            d.close()
    except Exception:          # noqa: BLE001 - a list without numbers still works (pages are named by number)
        labels = []
    _CF_SHEETS_CACHE[job_id] = (key, labels)
    return labels


def _cf_sheet_labels(job: dict, job_id: str) -> list:
    """The printed sheet number of every page, "" where none can be trusted. Reading only.
    - First the cut / fill engine's reading, then where it has none the host's own (the page strip's, triage.json,
      read on every call: it is written a few seconds after an upload, often after the first list - the second
      critic's N3). Either counts only when shaped like a sheet number (_CF_SHEET_NO): the engine took "OF 62"
      from "SHEET NO. 2 OF 6", "LOT 12", a bare "1", a 7-digit number (the second critic's W2 table); the host
      took "LOT910".
    - A number read on two pages or more is nobody's sheet number: "SHEET 3 OF 24" gave "24" on 20 of a set's
      25 pages (the first critic's W2). Those pages are named by their page number only."""
    labels = list(_cf_engine_labels(job, job_id))
    host = {}
    try:
        t = json.loads((_job_dir(job_id) / "triage.json").read_text(encoding="utf-8"))
        if t.get("version") == TRIAGE_VERSION and t.get("mtime") == job.get("mtime"):
            host = {int(r["page"]): str(r.get("sheet_no") or "").strip().upper()
                    for r in (t.get("pages") or []) if isinstance(r, dict) and "page" in r}
    except Exception:          # noqa: BLE001 - only a cached reading is used; none is fine
        host = {}
    n = max(len(labels), int(job.get("pages") or 0))
    labels += [""] * (n - len(labels))
    # the engine's reading as it came (its reader allows a space: "OF 62", "LOT 12" fail the shape; a spaced "C 4.1"
    # fails too, and that page is named by its number)
    labels = [next((x for x in (str(lb or "").strip().upper(), host.get(i) or "") if _CF_SHEET_NO.match(x)), "")
              for i, lb in enumerate(labels)]
    seen = Counter(lb for lb in labels if lb)
    return [lb if lb and seen[lb] == 1 else "" for lb in labels]


_CF_SIG_CACHE: dict = {}          # result path -> ((mtime_ns, size), engine_sig it was made with)


def _cf_detected(job_id: str, page: int) -> tuple:
    """(detected, stale): a detection made by the engine running here. One made by older code is not handed to
    the cut / fill by the picker (the page itself asks for Detect again): it detects it again first (critic R1).
    The signature is read once per file version (a list parsed every page's whole result: 2.2 s on 132 pages)."""
    f = result_path(_job_dir(job_id), page)
    try:
        st = os.stat(str(f))
    except OSError:
        return False, False
    # the file's version is its time AND size: a rewrite on this disk kept the same mtime_ns in 22 of 40 runs, and
    # an answer read mid-write is never kept (critic 3, m3: the page stayed "older" until a restart)
    mt = (st.st_mtime_ns, st.st_size)
    hit = _CF_SIG_CACHE.get(str(f))
    if hit and hit[0] == mt:
        sig = hit[1]
    else:
        try:
            sig = json.loads(Path(f).read_text()).get("engine_sig")
            _CF_SIG_CACHE[str(f)] = (mt, sig)
        except (OSError, ValueError):
            sig = None
    from takeoff_engine.detect_page import engine_sig
    stale = sig != engine_sig()
    return not stale, stale


_CF_REVIEW_CACHE: dict = {}
_CF_RESULT_NAME = re.compile(r"p(\d+)_result\.json")


def _cf_review_scale_live(project, key: str) -> "dict | None":
    """A cut / fill sheet's scale as its review holds it NOW (the reviewer's pick, else the detection's reading:
    _page_scale), for the engine's scale check (cutfill/placement.py review_scale, which asks this each time), as
    {"ft", "reviewer" (a reviewer's pick), "at" (the pick's time, epoch seconds)}. The sheet's detection names its job
    and page (its `core_result`, <jobs>/<job>/p<N>_result.json): a linked plan set's sheet answers from its own job,
    read in THIS store - a project copied from another store kept the old paths, and the review went unasked there: a
    correction on the scale's own sheet passed silently (critic 8, r-5). A copy kept on the sheet when it was handed
    over went stale (critic 7, WRONG-1). The detection's scale is parsed once per file version (its time and size); the
    pick is a small file read each time. None: no review this sheet answers to (_cf_sheet_job_page), or no scale in it."""
    jp = _cf_sheet_job_page(project, key)
    if jp is None:
        return None
    job_id, page = jp
    # a review this project reads but may not set (another owner's set, linked here): the refusal words say so
    writable = _cf_sheet_job_page(project, key, write=True) is not None
    try:
        f = (JOBS_DIR / job_id).resolve() / f"p{page}_result.json"
        st = f.stat()
    except OSError:
        return None
    ver = (st.st_mtime_ns, st.st_size)
    hit = _CF_REVIEW_CACHE.get(str(f))
    if hit and hit[0] == ver:
        res = hit[1]
    else:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # read mid-write (the detector writes this file in place): the last whole reading answers, not kept for
            # this version, and the next check reads again. None made the cut / fill's record read "sheet 3.1's scale
            # readings changed" for a moment, and a page load then took the rows out (critic 10, r-2)
            if not hit:
                return None
            res = hit[1]
        else:
            res = {"scale": d.get("scale"), "scale_suggestions": d.get("scale_suggestions")}
            _CF_REVIEW_CACHE[str(f)] = (ver, res)
    try:
        det = _page_scale(job_id, page, res)
    except Exception:           # noqa: BLE001 - no reading is simply no reading
        return None
    ft = float((det or {}).get("ft_per_paper_inch") or 0)
    if ft <= 0:
        return None
    by = (det or {}).get("set_by") == _BY_REVIEWER
    at = None
    if not by:
        # going back to the engine's own reading is a reviewer's decision too, dated by its tombstone: a scale typed
        # before it no longer outranks it (the reviewer's 40, the operator's 40 typed as the refusals asked, then the
        # reviewer back to the engine's 50: the typed 40 stood, 576 CY exported beside the page's 50, 36 % low -
        # critic 14, WRONG-1). The decision was "the engine's reading": it holds for what the engine reads NOW - a
        # re-detect to 45 let the typed 40 stand, 21 % low (critic 15, RISK-2) - and for a tombstone written before it
        # kept the number (critic 15, WRONG-1)
        tomb = _stored_scale_tombstone(job_id, page)
        try:
            if tomb and tomb.get("at"):
                return {"ft": ft, "reviewer": True, "at": float(tomb["at"]), "writable": writable}
        except (TypeError, ValueError):
            pass
    if by and det.get("set_at_epoch"):
        at = float(det["set_at_epoch"])        # exact (critic 9, r-6)
    elif by and det.get("set_at"):
        try:
            at = time.mktime(time.strptime(str(det["set_at"]), "%Y-%m-%dT%H:%M:%S"))   # an older pick: local time, whole seconds
        except (ValueError, OverflowError):
            at = None
    return {"ft": ft, "reviewer": by, "at": at, "writable": writable}


_cf_placement.REVIEW_SCALE = _cf_review_scale_live


# WHO IS ASKING (critic bid-content-1, WRONG-2). A cut / fill project can hold another plan set's sheets (a link,
# cutfill_link) and reaches that set's job: its drawing (page image / clip), its detection (`core_result`) and its
# review scale (read and written). The link was checked once, when it was made; a set that stopped being visible to
# the caller later (a demo card taken off the list, so its owner-less row became staff-only) stayed reachable through
# the old link. So every reach asks NOW, with the host's own rule for the caller of this request (_can_open, the same
# one /api/job/* applies): _CF_CALLER is (user, staff), set by the session middleware for every request; outside a
# request (a background read) it is the project owner's view. Nothing is deleted: a link the caller may not see
# answers nothing (and its pages and the project-wide routes are refused in words, _cf_linked_gate).
_CF_CALLER: "contextvars.ContextVar" = contextvars.ContextVar("tk_cf_caller", default=None)


def _cf_viewer(own: str) -> tuple:
    """(user, staff) whose eyes a cut / fill reach uses: this request's caller, else the project owner's."""
    c = _CF_CALLER.get()
    if c is not None:
        return c
    owner = (JOBS.get(own) or {}).get("owner")
    return owner, bool(owner) and _users.is_staff(owner)


def _cf_job_readable(own: str, job_id: str) -> bool:
    """May this cut / fill (the project of job `own`) read job `job_id` - its drawing, detection, review scale - for
    the caller now? The host's rule (_can_open) for that caller and that job; the project's own job always."""
    if job_id == own:
        return True
    j = JOBS.get(job_id)
    if not j:
        return False
    user, staff = _cf_viewer(own)
    return _can_open(j, user, staff)


def _cf_job_writable(own: str, job_id: str) -> bool:
    """May it WRITE that job's review scale? Only what it may read (the host's rule: whoever may open a job may
    approve it, and an approve records its scale - shared sets included, see tests/test_cutfill_link_visibility.py),
    and of those only its own job, one of the same owner, or a shared set: another owner's set linked by staff stays
    read only (critic 18, RISK-1)."""
    if job_id == own:
        return True
    if not _cf_job_readable(own, job_id):
        return False
    return (JOBS[job_id] or {}).get("owner") in (None, (JOBS.get(own) or {}).get("owner"))


def _cf_doc_jobs(p, own: str) -> dict:
    """{document index: {job ids}} for the project's documents other than its own (doc 0): the job the host linked
    it to (`host_links`), the job its sheets' detection records name, and any job whose PDF IS that document."""
    out: dict = {}
    links = p.data.get("host_links") or {}
    docs = p.data.get("docs") or []
    for k, v in links.items():
        if str(k).isdigit() and int(k) > 0 and v:
            out.setdefault(int(k), set()).add(str(v))
    for key, ps in (p.data.get("pages") or {}).items():
        km = re.fullmatch(r"d(\d+)p(\d+)", str(key))
        cr = (ps or {}).get("core_result")
        if km and int(km.group(1)) > 0 and cr:
            jid = Path(str(cr)).parent.name
            if jid != own:
                out.setdefault(int(km.group(1)), set()).add(jid)
    with JOBS_LOCK:
        paths = {os.path.normcase(str(j.get("path", ""))): jid for jid, j in JOBS.items() if jid != own}
    for di, d in enumerate(docs):
        if di > 0:
            jid = paths.get(os.path.normcase(str((d or {}).get("path", ""))))
            if jid:
                out.setdefault(di, set()).add(jid)
    return out


def _cf_hidden_docs(p, own: str, user, staff: bool) -> list:
    """The project's documents that come from a plan set this caller may not open now (sorted indexes)."""
    if staff:
        return []
    return sorted(di for di, jids in _cf_doc_jobs(p, own).items()
                  if any(jid in JOBS and not _can_open(JOBS[jid], user, staff) for jid in jids))


_CF_PAGE_PATH = re.compile(r"^/cutfill/api/projects/job_([^/]+)/pages/d(\d+)p\d+(?:/|$)")
_CF_PROJECT_WIDE = re.compile(r"^/cutfill/api/projects/job_([^/]+)/(compute|result|takeoff|export\.csv|crewmap\.png|"
                              r"transforms|grade_at|limit/pick)$")
# the host's own route that reads the cut / fill surfaces (the ground under a structure's walls)
_CF_HOST_WIDE = re.compile(r"^/api/job/([^/]+)/page/\d+/cutfill/grade$")
_CF_HIDDEN_WORDS = ("comes from a plan set your account can no longer open, so it is not shown, read or measured "
                    "here. The rest of this cut / fill is untouched and nothing was deleted.")


def _cf_doc_of(key: str):
    """The document index of a sheet key "d<doc>p<page>", or None."""
    km = re.fullmatch(r"d(\d+)p\d+", str(key))
    return int(km.group(1)) if km else None


def _cf_linked_gate(path: str, user, staff: bool):
    """A refusal (JSON response) for a cut / fill request that would reach a linked plan set the caller may not open
    now, else None. Its pages (image, clip, analysis, analyze, walls, limit candidates, settings) are refused; the
    project-wide routes (compute, result, takeoff, export, crew map, transforms, grade_at, limit pick, and the host's
    /api/job/<id>/page/<n>/cutfill/grade) are refused
    while such a sheet still takes part - opening Cut / fill from the job takes it out (cutfill_open). Staff and
    single-password mode pass."""
    if staff:
        return None
    page_route = _CF_PAGE_PATH.match(path)
    m = page_route or _CF_PROJECT_WIDE.match(path) or _CF_HOST_WIDE.match(path)
    if not m:
        return None
    own = m.group(1)
    try:
        p = _cutfill.get_project(f"job_{own}")
    except HTTPException:
        return None
    with p._lock:
        hidden = _cf_hidden_docs(p, own, user, staff)
        if not hidden:
            return None
        if page_route:
            di = int(m.group(2))
            if di in hidden:
                return _StarJSON({"detail": f"This sheet {_CF_HIDDEN_WORDS}"}, status_code=403)
            return None
        live = sorted(k for k, v in p.data["pages"].items()
                      if (v or {}).get("role") in _CF_ACTIVE and _cf_doc_of(k) in hidden)
    if live:
        return _StarJSON({"detail": f"{len(live)} sheet(s) of this cut / fill {_CF_HIDDEN_WORDS} Open Cut / fill "
                                    f"from the job page to take them out of it, then compute again."},
                         status_code=409)
    return None


def _cf_detach_hidden(p, own: str) -> list:
    """Take the sheets of documents the caller may not open now out of the cut / fill (role "none"): their
    document, link, detection record and readings stay as they are (nothing is deleted), so staff - or the caller,
    once the set is visible to them again - can put them back. Returns the keys taken out. Under the project lock.
    Only for the caller of a request (an open): with no caller (a direct call) nothing is taken out."""
    if _CF_CALLER.get() is None:
        return []
    user, staff = _cf_viewer(own)
    hidden = set(_cf_hidden_docs(p, own, user, staff))
    out = []
    for k, v in p.data["pages"].items():
        km = re.fullmatch(r"d(\d+)p\d+", k)
        if km and int(km.group(1)) in hidden and (v or {}).get("role") in _CF_ACTIVE:
            v["role"] = "none"
            out.append(k)
    return sorted(out)


def _cf_sheet_job_page(project, key: str, write: bool = False) -> "tuple | None":
    """(job id, page) of the review a cut / fill sheet answers to - read (_cf_review_scale_live) and written
    (_cf_review_scale_set, `write`) the same way - or None:
    - the detection handed over with it (`core_result`, <jobs>/<job>/p<N>_result.json), read in THIS store (a project
      copied from another store kept the old paths: critic 8, r-5);
    - else, for a sheet that takes part or has been read, the detection of its page in the plan set its document IS:
      the project's own job for doc 0 (Cut / fill opened before Detect recorded none, and the rows at 50 were exported
      under the page's 40, 56 % high: critic 17, WRONG-1), the linked job the host recorded for another document
      (`host_links`: a linked sheet detected after the link had no record, and its scale could not be set: critic 18,
      RISK-3).
    Of a job in the registry that the project's own job may reach: itself, a job with the same owner, or a shared set -
    read and written; a job the host LINKED to this very document (staff may link any set) - read only, so the rule
    holds there (critic 18, RISK-1: unread, its sheet was computed at 50 against its review's 40). Any other job's
    review is neither read nor written (a `core_result` pointed at another client's job let a Scale card set that job's
    review scale: critic 17, WRONG-2)."""
    pid = str(getattr(project, "id", "") or "")
    own = pid[4:] if pid.startswith("job_") else None
    ps = project.data["pages"].get(key) or {}
    km = re.fullmatch(r"d(\d+)p(\d+)", str(key))
    linked = ((project.data.get("host_links") or {}).get(km.group(1)) if km else None)
    cr = ps.get("core_result")
    if cr:
        m = _CF_RESULT_NAME.fullmatch(Path(str(cr)).name)
        if not m:
            return None
        job_id, page = Path(str(cr)).parent.name, int(m.group(1))
    else:
        if not (km and own and (ps.get("role") in _CF_ACTIVE or os.path.exists(project.analysis_path(key)))):
            return None
        job_id = own if km.group(1) == "0" else linked
        if not job_id:
            return None
        page = int(km.group(2))
    if not own or own not in JOBS or job_id not in JOBS:
        return None
    # the host's rule for the caller NOW (a link made when the set was visible does not outlive it: WRONG-2), then
    # the write rule (_cf_job_writable); another owner's set is read only through the link the host made for it
    if not _cf_job_readable(own, job_id):
        return None
    if write and not _cf_job_writable(own, job_id):
        return None
    if job_id != own and (JOBS[job_id] or {}).get("owner") not in (None, (JOBS[own] or {}).get("owner")):
        if write or linked != job_id:
            return None
    try:
        jd = (JOBS_DIR / job_id).resolve()
        if jd.parent != JOBS_DIR.resolve() or not (jd / f"p{page}_result.json").exists():
            return None                 # a name that would not be a job of this store, or no detection there
    except OSError:
        return None
    return job_id, page


def _cf_record_reachable(project, key: str, cr) -> bool:
    """A sheet's detection record (`core_result`) names a job this project may reach: its own, one of the same owner,
    a shared set, or the job the host linked this very document to (`host_links`)."""
    pid = str(getattr(project, "id", "") or "")
    own = pid[4:] if pid.startswith("job_") else None
    if not own or not _CF_RESULT_NAME.fullmatch(Path(str(cr)).name):
        return False
    job_id = Path(str(cr)).parent.name
    if job_id == own:
        return True
    if job_id not in JOBS or own not in JOBS:
        return False
    if not _cf_job_readable(own, job_id):
        return False                    # the host's rule for the caller now (WRONG-2)
    if (JOBS[job_id] or {}).get("owner") in (None, (JOBS[own] or {}).get("owner")):
        return True
    km = re.fullmatch(r"d(\d+)p(\d+)", str(key))
    return bool(km and (project.data.get("host_links") or {}).get(km.group(1)) == job_id)


def _cf_adopt_link(project, key: str, cr) -> bool:
    """A link the host made before it recorded its links (`host_links`, 15c2247): the record names a job whose PDF IS
    this sheet's document (not doc 0 - the job's own), which is how cutfill_link adds another set. Recorded now, and
    kept - dropped, the staff link's sheets lost their detection at the next open and the volumes moved (critic 19,
    RISK-2). Call under the project's lock."""
    km = re.fullmatch(r"d(\d+)p(\d+)", str(key))
    m = _CF_RESULT_NAME.fullmatch(Path(str(cr)).name)
    if not (km and m) or km.group(1) == "0":
        return False
    job_id = Path(str(cr)).parent.name
    docs = project.data.get("docs") or []
    di = int(km.group(1))
    if job_id not in JOBS or di >= len(docs):
        return False
    if os.path.normcase(str((docs[di] or {}).get("path"))) != os.path.normcase(str((JOBS[job_id] or {}).get("path"))):
        return False
    links = project.data.setdefault("host_links", {})
    if links.get(str(di)) not in (None, job_id):
        return False
    links[str(di)] = job_id
    return True


def _cf_review_scale_set(project, key: str, ft: float, text: str) -> "bool | None":
    """A cut / fill sheet's review scale, set from the Scale card (Koby, 2026-09-29: the review wins, and an operator
    who finds the scale read wrong types the right one - in the review or in the Scale card - and every number
    follows). Recorded as the review's own Approve records it (_page_scale: a pick, or a dated tombstone when it is the
    engine's own reading), without exporting anything. None: the sheet has no review here; False: not recorded. A
    review this project may read but not write (another owner's set, linked by staff): refused, said."""
    jp = _cf_sheet_job_page(project, key, write=True)
    if jp is None:
        if _cf_sheet_job_page(project, key) is not None:
            # read only (another owner's set, linked here): its own number is taken - there is nothing to write - so the
            # sheet can be measured with it (every typing was refused, its own number too: a dead end, critic 19,
            # WRONG-1); another number is refused, naming the review's
            rv = _cf_review_scale_live(project, key) or {}
            try:
                have = float(rv.get("ft") or 0)
            except (TypeError, ValueError):
                have = 0.0
            if have <= 0:
                # no scale in that review: nothing to agree with or write - the sheet's scale is the operator's here, as
                # for a sheet with no review (every typing was refused: a dead end, critic 20, WRONG-3)
                return None
            if abs(float(ft) - have) <= 0.01 * max(float(ft), have):
                return True
            from cutfill.project import _sheet_words as _cf_sheet_words
            w = _cf_sheet_words(project, key)
            raise HTTPException(409, f'{w[:1].upper() + w[1:]}\'s review says 1" = {_cf_scale_txt(have)} ft: it is another '
                                     f"owner's plan set, linked here, so its scale is set in that set's own review, not "
                                     f"here. Nothing was changed.")
        return None
    job_id, page = jp
    try:
        res = json.loads((_job_dir(job_id) / f"p{page}_result.json").read_text(encoding="utf-8"))
        with _scale_lock(job_id, page):     # one writer of the page's scale record at a time (_scale_lock)
            _page_scale(job_id, page, res, ApproveScale(ft_per_paper_inch=float(ft), text=(text or None), picked_by_reviewer=True))
            if _stored_scale_raw(job_id, page) is None:
                # the engine's own reading confirmed with no pick on file: _page_scale writes nothing for that, so nothing
                # told a review page that kept another number in its browser - its approve of that number went through
                # (critic 23, RISK-1). Recorded as the review's go-back is: a dated tombstone, read as "the engine's
                # reading" everywhere
                _pf = _scale_pick_path(job_id, page)
                _tomb = {"retired": True, "at": time.time(), "ft_per_paper_inch": float(ft)}
                _pf.write_text(json.dumps(_tomb, indent=1))
                _PICK_LAST[str(_pf)] = _tomb
    except HTTPException as e:
        if e.status_code == 400:
            raise                   # a number no review takes (_refuse_scale), said as the review says it
        return False
    except (OSError, ValueError, TypeError):
        return False
    return True


_cf_placement.REVIEW_SET = _cf_review_scale_set

# the host's cut / fill rows (webui/app.js CF_ROW_RE): a takeoff row with one of these labels is the job's cut / fill's
_CF_HOST_ROW = re.compile(r"^(Site Cut|Site Fill)( – .+)?$|^(Export Cut|Import Structural|Silt / Construction Fence|"
                          r"Clear & Grub|Strip Topsoil|Rough Place Topsoil|Export Topsoil|Import Topsoil|Site walls face)$")


def _cf_engine_item(it) -> bool:
    """A takeoff row the job's cut / fill rules judge: one of its names, not the Areas tool's (`_area`)."""
    return isinstance(it, dict) and not it.get("_area") and bool(_CF_HOST_ROW.match(str(it.get("label") or "")))


def _cf_state(job_id: str, rows: bool = False) -> dict:
    """The job's cut / fill as the engine stands NOW, asked without its page: a correction in review reached the engine
    and never the host's takeoff - the rows at 50 stayed, were approved and exported, 56 % high (critic 8, WRONG-1).
    {"project": False} without one; else {"why": why its rows do not stand (the scale is not checked for the sheet
    shown, the last compute was refused, or its result stands no more) or None, "computed_at": the stored result's
    time, "rows_page": the page whose takeoff holds the rows}; with `rows` and standing, also "summary": the engine's
    rows now with the facts the host keeps, in the shape the page posts (webui/cutfill/app.js hostSummary)."""
    pid = f"job_{job_id}"
    try:
        p = _cutfill.get_project(pid)
    except HTTPException:
        return {"project": False}
    stored = _cutfill._stored_result(pid, p)
    with p._lock:
        why = _cf_placement.foreign_scale(p) or (
            "the last compute was refused (the Cut / fill panel says why)" if (stored or {}).get("error") else
            _cf_placement.stale_result(p, stored))
        s = json.loads(json.dumps(p.data["settings"]))
    hr = s.get("host_rows") or {}
    out = {"project": True, "why": why or None, "computed_at": None if (stored or {}).get("error") else (stored or {}).get("computed_at"),
           "rows_page": hr.get("page") if isinstance(hr.get("page"), int) else None}
    if rows and not why:
        t = _cutfill.takeoff_route(pid)
        why2 = t.get("result_stale") or ((t.get("notes") or [None])[0] if t.get("scale") == "not checked for the sheet shown" else None)
        if why2:                             # changed between the two looks
            out["why"] = why2
            return out
        res = stored if stored and not stored.get("error") else None
        tot = (res or {}).get("totals") or {}
        tk = s.get("takeoff") or {}
        rs = (res or {}).get("ft_per_in") or s.get("scale_ft_per_in")
        moved = bool(rs and s.get("scale_ft_per_in") and abs(float(rs) - float(s["scale_ft_per_in"])) > 1e-3 * float(rs))
        out["summary"] = {
            "no_volumes": res is None, "cut_cy": tot.get("cut_cy"), "fill_cy": tot.get("fill_cy"), "net_cy": tot.get("net_cy"),
            "area_sf": tot.get("area_sf"), "coverage": tot.get("coverage"), "computed_at": (res or {}).get("computed_at"),
            "scale_ft_per_in": rs, "scale_source": "the scale of that compute" if moved else s.get("scale_source"),
            "section_ft": (res or {}).get("section_ft") or 0, "region_source": (res or {}).get("region_source"),
            "factors": {"export": float(tk.get("export_factor") or 1), "import": float(tk.get("import_factor") or 1)},
            "zones_itemized": bool((res or {}).get("zones_itemized")), "zones_rest": bool((res or {}).get("zones_rest")),
            "rows": [{k: r.get(k) for k in ("label", "qty", "unit", "basis", "kind")} for r in t.get("rows") or []],
            "stale": False, "warnings": (res or {}).get("warnings") or []}
    return out


@app.get("/api/job/{job_id}/cutfill/state")
def cutfill_state(job_id: str, rows: int = 0):
    """_cf_state: the host asks it on every page load that holds the job's cut / fill rows (webui/app.js cfReconcile)."""
    JOBS.get(job_id) or _404()
    return _cf_state(job_id, rows=bool(rows))


def _cf_rows_refused(job_id: str, page: int, items, det: "dict | None" = None) -> "str | None":
    """Why the cut / fill rows an approve carries must not be exported, or None: they are on a page that does not hold
    the job's rows (one set per job - the same rows passed on two pages, and the dirt was exported twice: critic 9,
    WRONG-3), there is no cut / fill behind them (a reset, or the removed earthwork calculator's rows), they do not stand
    (_cf_state), or they are not the engine's rows now (filed before a change this page never heard of). A row the Areas
    tool made (`_area`) is the operator's, whatever its name ("Clear & Grub"). Asked after the approve records its
    scale, so a correction made in that very approve counts."""
    have = sorted((str(it.get("label"))[:80], round(float(it.get("qty") or 0), 1)) for it in (items or [])
                  if _cf_engine_item(it))
    if not have:
        return None
    st = _cf_state(job_id, rows=True)
    if not st.get("project"):
        return ("these cut / fill rows have no cut / fill behind them now (it was reset): remove them, or compute in the "
                "Cut / fill panel")
    if isinstance(st.get("rows_page"), int) and st["rows_page"] != int(page):
        return (f"the job's cut / fill rows belong to page {st['rows_page'] + 1}'s takeoff (one set per job - here they "
                f"would count the dirt twice): remove them from this page")
    if st.get("why"):
        return st["why"]
    # a backstop that needs no review hook: the rows' own compute measured THIS page (its sheet shown) at one scale, and
    # this approve exports the page at another (`det`, the scale it records). A sheet opened before Detect answered to
    # no review, the rule above never saw the reviewer's 40, and the rows at 50 were exported under it, 56 % high
    # (critic 17, WRONG-1)
    try:
        ft_page = float((det or {}).get("ft_per_paper_inch") or 0)
        res = _cutfill._stored_result(f"job_{job_id}", _cutfill.get_project(f"job_{job_id}")) or {}
        ft_res = float(res.get("ft_per_in") or 0) if not res.get("error") else 0.0
    except (HTTPException, TypeError, ValueError):
        ft_page = ft_res = 0.0
        res = {}
    if ft_page > 0 and ft_res > 0 and res.get("display_page") == f"d0p{int(page)}" and abs(ft_res - ft_page) > 0.01 * ft_res:
        return (f'these cut / fill rows were computed at 1" = {_cf_scale_txt(ft_res)} ft, but this page is approved at '
                f'1" = {_cf_scale_txt(ft_page)} ft: type the right scale in the Cut / fill Scale card with this sheet on '
                f'screen (it sets the review\'s scale too), then compute')
    want = sorted((str(r.get("label"))[:80], round(float(r.get("qty") or 0), 1)) for r in (st.get("summary") or {}).get("rows") or []
                  if _CF_HOST_ROW.match(str(r.get("label") or "")) and float(r.get("qty") or 0) > 0)
    if want != have:
        return "they are not the engine's numbers now (a change reached it while this page was not listening)"
    return None


# ---- THE OLD EARTHWORK CALCULATOR'S ROWS COUNT NOWHERE (v1.8.5's; removed here, Cut / fill replaces it) ------------------
# Koby, 2026-10-01 (evening): "Clients were told this is beta, so old earthwork work does not need to be kept. Do the
# simplest thing: the old calculator's earthwork rows count nowhere: not in the takeoff, the bid sheet or the report.
# Those pages show one short note ... Don't build anything to keep or convert them. Leaving the old files on disk is fine,
# because it keeps an undo clean. Touch nothing else." docs/fable/LEGACY-EARTHWORK-DESIGN.md.
# Every reader of an approved page goes through _export_view: the file on disk is never rewritten.
EW_REMOVED_NOTE = "Earthwork from the old calculator was removed. Redo it in Cut / fill."
# ...and what the approve that ends it says, once (critic legacy-5, RISK-4)
EW_APPROVED_SAID = "Approved. The old calculator's earthwork is not in it."
# every export this app writes says so; an export without it is v1.8.5's (MARKS DECIDE: no file time, project or
# calcstate rule; v1.8.5 reads past an unknown top-level key)
EXPORT_MARKS_KEY = "src_marks"
# the cubic-yard rows only the old calculator wrote (v1.8.5 webui/app.js ewApplyItems: Site Cut / Site Fill, a layer's
# "Strip Topsoil" / "Remove X" / "Place X"; takeoff_engine/earthwork.py's balance rows: "Export X", "Rough Place X",
# "Import Topsoil", "Export Surplus Cut", "Import Structural"; gzApplyItems: "<zone> cut / fill / topsoil strip /
# place X"). No other v1.8.5 tool writes CY under these names (trench: "Trench excavation (...)", "Bedding gravel (...)",
# "Backfill (...)"; structures: "Mass Excavation", "Structure Excavation", "Structure Backfill"; the Areas tool writes SF
# and LF only; a line style exports feet, a count EA)
_EW_OLD_CY = re.compile(r"^(Site Cut|Site Fill|Strip Topsoil|Import Structural|Import Topsoil|Import Fill|Remove .+|Rough Place .+|"
                        r"Place .+|Export .+|(Cut|Fill) \(earthwork\)|.+ (cut|fill|topsoil strip|place .+))$")
# ...and its limit of work's two rows (v1.8.5 ewApplyItems): "Clear & Grub" = Math.round(the limit's area) SF and
# "Silt / Construction Fence" = Math.round(its perimeter) LF. The Areas tool can write a row of either name (a renamed
# area, quantity rounded to 0.1 - a whole number about one time in ten), and v1.8.5's export does not say which tool wrote
# a row; its trade, count, length and place in the list are the same for both. The one CERTAIN mark: the row's number
# is the limit's own area / perimeter, measured again here from the page record's limit of work (`ew_bound`: the drawn
# corners, or the picked chain in the page's result) at the export's scale, to the calculator's rounding. A row that
# does not match is kept (critic legacy-5, RISK-1 / RISK-2)
_EW_LIMIT_ROWS = {("Clear & Grub", "SF"): "area", ("Silt / Construction Fence", "LF"): "perim"}


def _ew_old_row(it) -> bool:
    if not isinstance(it, dict) or it.get("src") not in (None, ""):
        return False                                   # a row with its own mark (cutfill, area) is never the old calculator's
    lab, unit = str(it.get("label") or ""), str(it.get("unit") or "").strip().upper()
    return unit == "CY" and bool(_EW_OLD_CY.match(lab))


def _ew_limit_geom(exp: dict, rec: dict, job_id: "str | None" = None, pno: "int | None" = None) -> "dict | None":
    """{"area": SF, "perim": LF} of the page record's limit of work at the export's scale, or None (no limit, no scale,
    or a picked chain this page's result no longer has)."""
    bd = (rec or {}).get("ew_bound") if isinstance(rec, dict) else None
    if not isinstance(bd, dict):
        return None
    pts = bd.get("pts")
    if not pts and bd.get("chain_id") is not None and job_id is not None and pno is not None:
        try:
            res = json.loads((_job_dir(job_id) / f"p{pno}_result.json").read_text(encoding="utf-8"))
            ch = next((c for c in res.get("chains") or [] if c.get("chain_id") == bd["chain_id"]), None)
            pts = (ch or {}).get("points")
        except (OSError, ValueError, TypeError):
            pts = None
    try:
        pts = [(float(x), float(y)) for x, y in pts or []]
        sc = exp.get("scale") or {}
        ft_in = float(exp.get("imageDpi") or 0) / float(sc.get("pixelsPerUnit") or 0)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if len(pts) < 3 or ft_in <= 0:
        return None
    k = ft_in / 72.0                                   # feet per page point
    n = len(pts)
    area = abs(sum(pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1] for i in range(n))) / 2.0
    perim = sum(math.dist(pts[i], pts[(i + 1) % n]) for i in range(n))
    return {"area": area * k * k, "perim": perim * k}


def _ew_old_items(exp: dict, rec: "dict | None" = None, job_id: "str | None" = None, pno: "int | None" = None) -> list:
    """The items of an approved export that the old Earthwork calculator wrote: none in an export this app wrote
    (EXPORT_MARKS_KEY); in v1.8.5's, its CY rows by name, and its limit of work's two rows where their numbers are the
    record's limit's own (_ew_limit_geom; one row of each at most)."""
    if not isinstance(exp, dict) or exp.get(EXPORT_MARKS_KEY):
        return []
    items = [it for it in (exp.get("items") or []) if isinstance(it, dict)]
    geom = _ew_limit_geom(exp, rec or {}, job_id, pno)
    out, used = [], set()
    for it in items:
        if _ew_old_row(it):
            out.append(it)
            continue
        key = (str(it.get("label") or ""), str(it.get("unit") or "").strip().upper())
        what = _EW_LIMIT_ROWS.get(key)
        if not what or geom is None or key in used or it.get("src") not in (None, ""):
            continue
        try:
            q = float(it.get("qty"))
        except (TypeError, ValueError):
            continue
        if q == round(q) and abs(q - geom[what]) < 1.0:     # Math.round of the measure (float noise apart)
            out.append(it)
            used.add(key)
    return out


def _ew_page_record(job_id: str, page: int) -> dict:
    try:
        d = json.loads(_calcstate_path(job_id, page).read_text(encoding="utf-8"))
    except (OSError, ValueError, HTTPException):
        return {}
    return d if isinstance(d, dict) else {}


def _ew_caveat(note) -> bool:
    return str(note).startswith("EARTHWORK (")


def _export_view(job_id: str, pno: int, exp: dict) -> dict:
    """An approved export as every reader sees it: a v1.8.5 export without the old Earthwork calculator's rows and without
    its caveat notes (v1.8.5's EARTHWORK block runs from "EARTHWORK (" to the end of the notes, the work area's note
    apart); anything else exactly as it is. A copy: the file is never rewritten (an undo to v1.8.5 finds it as it was)."""
    if not isinstance(exp, dict) or exp.get(EXPORT_MARKS_KEY):
        return exp
    old = _ew_old_items(exp, _ew_page_record(job_id, pno), job_id, pno)
    notes = [str(x) for x in (exp.get("notes") or [])]
    cut = next((i for i, x in enumerate(notes) if _ew_caveat(x)), None)
    if not old and cut is None:
        return exp
    out = dict(exp)
    out["items"] = [it for it in (exp.get("items") or []) if not any(it is o for o in old)]
    if cut is not None:
        out["notes"] = notes[:cut] + [x for x in notes[cut:] if x.startswith("Work area:")]
    return out


def _read_export(job_id: str, pno: int) -> dict:
    """The page's approved export through _export_view; {} when there is none or it cannot be read."""
    try:
        d = json.loads((_job_dir(job_id) / f"p{pno}_elements.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return _export_view(job_id, pno, d) if isinstance(d, dict) else {}


def _ew_had_old(rec: dict) -> bool:
    """This browser's move to this build dropped old Earthwork calculator rows from the page (`ew_dropped`). Saved inputs
    alone (contour sides, a zone, a calculation never added) say nothing: nothing was in the takeoff (critic legacy-5,
    RISK-3)."""
    return isinstance(rec, dict) and bool(rec.get("ew_dropped"))


def _ew_removed_pages(job_id: str) -> list:
    """The pages whose old Earthwork calculator work was removed, until this app approves them again: a v1.8.5 export
    holding rows the filter removes, or this browser's move dropped some (_ew_had_old), on a page not approved by this app."""
    d = _job_dir(job_id)
    pages = set()
    for f in list(d.glob("p*_elements.json")) + list(d.glob("p*_calcstate.json")):
        m = re.fullmatch(r"p(\d+)_(elements|calcstate)\.json", f.name)
        if m:
            pages.add(int(m.group(1)))
    out = []
    for pno in sorted(pages):
        try:
            exp = json.loads((d / f"p{pno}_elements.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            exp = None
        if isinstance(exp, dict) and exp.get(EXPORT_MARKS_KEY):
            continue                                   # approved again by this app
        rec = _ew_page_record(job_id, pno)
        if (isinstance(exp, dict) and _ew_old_items(exp, rec, job_id, pno)) or _ew_had_old(rec):
            out.append(pno)
    return out


def _ew_note(job_id: str, pno: int) -> str:
    """The note, with the page number in front on a job of several pages."""
    try:
        many = int((JOBS.get(job_id) or {}).get("pages") or 1) > 1
    except (TypeError, ValueError):
        many = False
    return f"Page {pno + 1}: {EW_REMOVED_NOTE}" if many else EW_REMOVED_NOTE


def _cf_rows_exported_elsewhere(job_id: str, page: int, items) -> "tuple | None":
    """Another page whose approved export (p<N>_elements.json) still carries the job's cut / fill rows, when this
    approve carries them too, or None. Rows approved on one page, then moved to another (or approved before a page was
    on record), stayed in the first page's export, and the report and the bid sheet read every export: the dirt was
    counted twice (critic 11, r-2). The cut / fill's rows are the ones its approve marked (`src: "cutfill"`); an
    unmarked row is the old Earthwork calculator's (marks decide: _ew_old_items), never the job's cut / fill rows."""
    if not any(_cf_engine_item(it) for it in (items or [])):
        return None
    d = _job_dir(job_id)
    for f in sorted(d.glob("p*_elements.json")):
        m = re.fullmatch(r"p(\d+)_elements\.json", f.name)
        if not m or int(m.group(1)) == int(page):
            continue
        try:
            raw = f.read_text(encoding="utf-8")
        except OSError:
            continue
        if '"cutfill"' not in raw:
            continue                            # the text alone rules most pages out: no parse
        try:
            exp_o = json.loads(raw) or {}
        except ValueError:
            continue
        for it in (exp_o.get("items") or []) if isinstance(exp_o, dict) else []:
            if isinstance(it, dict) and it.get("src") == "cutfill":
                return int(m.group(1)), str(it.get("label") or "")
    return None


def _cf_rows_claim(job_id: str, page: int, items) -> None:
    """An approve that exports the job's cut / fill rows (they passed `_cf_rows_refused`) where no page is on record
    makes its page the one that holds them: with none recorded (rows filed before the record existed, or an Add whose
    request never landed) the same rows passed on two pages and the dirt was exported twice (critic 10, r-1)."""
    eng = [it for it in (items or []) if _cf_engine_item(it)]
    if not eng:
        return
    try:
        with _CUTFILL_LOCK:
            p = _cutfill.get_project(f"job_{job_id}")
            with p._lock:
                s = p.data["settings"]
                if not isinstance((s.get("host_rows") or {}).get("page"), int):
                    s["host_rows"] = {"page": int(page), "t": time.time(), "by": "approve"}
                    p.save()
    except Exception:           # noqa: BLE001 - no project: nothing to record (the guard refused such rows already)
        pass


def _cf_result_stands(p) -> bool:
    """The last compute's volumes still stand on the sheet shown (placement.stale_result): an open from another sheet
    must not move the sheet shown under them - a visit from 3.1 to look took the earthwork rows off 3.0's takeoff, and
    coming back left a false STALE (critic 8, r-2). A Compute on the sheet opened moves it (the page asks it to).
    Call under the project's lock."""
    res = _cutfill._stored_result(p.id, p)
    return bool(res) and not res.get("error") and _cf_placement.stale_result(p, res) is None


def _cf_detection_changed(p, key: str) -> bool:
    """This sheet's detection changed since the engine read it (the page was detected again): the engine's reading
    holds the old detection's lines, and an Update would interpret them again (critic 3, r1). Either sign counts:
    - the file is not the one handed over at the last open (its time and size, recorded here at every open): this
      catches a detection written while the engine was still reading the old file;
    - the engine's cached reading is older than the file: a project from before the record, or a read queued by
      an earlier open that never ran (a restart).
    - a full read an earlier open queued for it has not finished (`core_redo_at`): a restart, or a read that failed,
      in the middle of it (the full read drops the old reading first) leaves neither sign above (critic 4, r1). The
      mark is cleared once the READING and the ANALYSIS are both newer than it (the reading is cached before the
      interpretation, which can still fail: critic 5, r-a) and no read of the sheet runs or waits to run again (a
      read that ends with a rerun queued is "done" for a moment: r-b).
    Only the detector writes that file (detect_page.py). Records the file as handed over now; call under the
    project's lock."""
    ps = p.page_settings(key)
    cr = (ps or {}).get("core_result")
    if not cr:
        return False
    try:
        st = os.stat(cr)
    except OSError:
        return False
    sig = f"{st.st_mtime_ns}:{st.st_size}"
    was = ps.get("core_sig")
    ps["core_sig"] = sig
    try:
        read_at = os.path.getmtime(p.reading_path(key))
    except OSError:
        read_at = None         # never read (it takes this detection at its first read), or a full read under way
    changed = (bool(was) and was != sig) or (read_at is not None and st.st_mtime > read_at + 1.0)
    pend = ps.get("core_redo_at")
    if pend and not changed:
        j = p.jobs.get(key) or {}
        running = j.get("status") in ("running", "queued") or bool(j.get("rerun"))
        if not running:
            try:
                an_at = os.path.getmtime(p.analysis_path(key))
            except OSError:
                an_at = None
            if read_at is not None and an_at is not None and min(read_at, an_at) > float(pend):
                ps.pop("core_redo_at", None)   # the full read was made, and interpreted
            else:
                changed = True                  # it was not: read again
    if changed:
        ps["core_redo_at"] = time.time()
    return changed


def _cf_roles(job_id: str, with_display: bool = False):
    """The job's cut / fill roles by page of its plan set (a role of none left out), read under the project's
    lock; {} when there is no project yet. With `with_display`, (roles, the page the engine shows or None): the
    engine's own rule (placement.display_for), never a role-none page the settings name (critic 3, N2's cause)."""
    pid = f"job_{job_id}"
    p = _cutfill._projects.get(pid)
    if p is None and os.path.exists(os.path.join(_cutfill.OUT, pid, "project.json")):
        try:
            p = _cutfill.get_project(pid)
        except HTTPException:
            p = None
    items, disp = [], None
    if p is not None:
        with p._lock:
            items = [(k, (v or {}).get("role")) for k, v in p.data["pages"].items()]
            disp = _cf_display_for(p.data["settings"], p.data["pages"])
    roles = {int(k[3:]): r for k, r in items
             if k.startswith("d0p") and k[3:].isdigit() and r not in (None, "", "none")}
    if not with_display:
        return roles
    dp = int(disp[3:]) if isinstance(disp, str) and disp.startswith("d0p") and disp[3:].isdigit() else None
    return roles, dp


@app.get("/api/job/{job_id}/cutfill/sheets")
def cutfill_sheets(job_id: str, labels: int = 0):
    """What "Sheets for cut / fill" lists: every page of the plan set with its printed sheet number, whether
    it is detected here (and not by older code), whether it is being detected now, and its role in the job's
    cut / fill when it has one. Without `labels` only the answer the open needs first, fast: is the job's cut /
    fill set up, and how many pages the set has (reading the sheet numbers took 9 s on a 132-page set).
    Reading only."""
    job = JOBS.get(job_id) or _404()
    n = int(job.get("pages") or 0)
    roles = _cf_roles(job_id)
    if not labels:
        return {"n": n, "in_project": bool(roles)}
    names = _cf_sheet_labels(job, job_id)
    status = dict(job.get("status") or {})
    pages = []
    for i in range(n):
        det, stale = _cf_detected(job_id, i)
        pages.append({"page": i, "label": names[i] if i < len(names) else "", "detected": det, "stale": stale,
                      "running": status.get(i) == "running", "role": roles.get(i)})
    return {"n": n, "pages": pages, "in_project": bool(roles)}


# engine reads the picker starts at once: each read's OCR runs up to 4 tesseract processes, and an open of two
# sheets read together took the server to 1,771 MB (the picker's critic, R2). The rest wait their turn.
# (The engine itself lets only CUTFILL_MAX_READS fresh reads run at once in the whole server, across its
# processes, cutfill/readslot.py; a second one started here waits there, saying the same words. D41.)
_CF_READS_AT_ONCE = 2
_CF_WAIT = _cf_readslot.WAITING          # the engine's own words for a read waiting in line


_CF_QUEUES: dict = {}                 # project id -> {"project", "keys": [waiting sheet keys]} while its queue runs
_CF_QUEUES_LOCK = threading.Lock()
# every project the host opened or queued reads for: a reset or replaced project's reads run to their end, and count
# until then. Its own lock: a request thread adds while the queue thread counts (critic 4, m8)
_CF_READERS: "weakref.WeakSet" = weakref.WeakSet()
_CF_READERS_LOCK = threading.Lock()


def _cf_reader(p) -> None:
    """Count this project's reads in _cf_busy for as long as it lives, even once it leaves the engine's registry."""
    with _CF_READERS_LOCK:
        _CF_READERS.add(p)


def _cf_busy() -> int:
    """Sheets being read now in EVERY cut / fill project of this server, whoever started them (the engine's own
    Analyze too): the limit is the server's memory, not one project's (critic 3, r2: a reset, then Cut / fill
    again, read 4 sheets at once - the reset project's 2 ran on). Each project's lock is taken on its own."""
    n = 0
    with _CF_READERS_LOCK:
        known = list(_CF_READERS)
    for pp in set(list(_cutfill._projects.values()) + known):
        with pp._lock:
            n += sum(1 for j in pp.jobs.values()
                     if (j or {}).get("status") in ("running", "queued") and not (j or {}).get("host_wait"))
    return n


def _cutfill_read_queue(p, keys: list, full=()) -> None:
    """Start the engine's reads of `keys` (sheets whose reading is missing or stale) with at most _CF_READS_AT_ONCE
    sheets reading at a time in the whole server (_cf_busy), whoever started them: ONE queue per project, and a
    later open adds to it (the second critic's R2: an open and a "Sheets…" ran 4 at once). A waiting sheet shows
    as queued in the engine, saying so. The queue stops, and its waiting sheets are dropped, when the project is
    closed or replaced (N1: after a reset it went on reading the old project's sheets, and held the job's PDF
    open). A key in `full` is read again from its lines, never from the cached reading (its detection changed);
    one being read already is read again in full when that read ends."""
    if not keys:
        return
    pid = p.id
    full = set(full or ())
    with p._lock:
        for k in keys:
            j = p.jobs.get(k) or {}
            if j.get("status") in ("running", "queued"):
                if k in full:
                    j["rerun"] = "full"        # the engine's loop reads it again once this read ends
                continue
            p.jobs[k] = {"status": "queued", "progress": _CF_WAIT, "full": False,
                         "rerun": "full" if k in full else None, "error": "", "host_wait": True}
    _cf_reader(p)
    with _CF_QUEUES_LOCK:
        q = _CF_QUEUES.get(pid)
        if q is not None and q["project"] is p:
            q["keys"].extend(k for k in keys if k not in q["keys"])
            return
        q = {"project": p, "keys": list(keys)}
        _CF_QUEUES[pid] = q

    def alive() -> bool:
        return not getattr(p, "_closed", False) and _cutfill._projects.get(pid) is p

    def run():
        try:
            while True:
                with _CF_QUEUES_LOCK:
                    if not q["keys"]:
                        if _CF_QUEUES.get(pid) is q:
                            del _CF_QUEUES[pid]        # in the same hold: a later open starts a new queue
                        return
                    k = q["keys"][0]
                t0 = time.time()
                # a read that hangs lets the next one start after 30 min: three then run at once
                while alive() and _cf_busy() >= _CF_READS_AT_ONCE and time.time() - t0 < 1800:
                    time.sleep(1.5)
                if not alive():
                    return
                with _CF_QUEUES_LOCK:
                    q["keys"].remove(k)
                with p._lock:          # take it off the queue and start it in one hold: a poll never reads "none"
                    j = p.jobs.get(k) or {}
                    if not j.get("host_wait"):
                        continue       # started meanwhile (the engine's Analyze), or gone
                    del p.jobs[k]
                    if getattr(p, "_closed", False):
                        return         # closed (a reset) since the check above: close() sets it under this lock (critic 3, m1)
                    if p.page_settings(k).get("role") in (None, "", "none"):
                        continue       # set to none while it waited: nothing to read
                    rerun = j.get("rerun")     # an Update or Re-read pressed while it waited
                    try:
                        p.start_analysis(k, force=rerun == "fast", full=rerun == "full")
                    except Exception:  # noqa: BLE001 - a read that cannot start shows as unread in the engine
                        pass
        finally:
            # the sheets this queue still held (a closed project, or an error) no longer say they wait
            with _CF_QUEUES_LOCK:
                left, q["keys"] = list(q["keys"]), []
                if _CF_QUEUES.get(pid) is q:
                    del _CF_QUEUES[pid]
            with p._lock:
                for kk in left:
                    if (p.jobs.get(kk) or {}).get("host_wait"):
                        del p.jobs[kk]
    threading.Thread(target=run, daemon=True, name=f"cutfill-read-queue-{pid}").start()


def _cutfill_scale_handoff(cur, cur_src, det) -> tuple | None:
    """One scale in force, by rank: the reviewer's pick on THIS sheet outranks everything, a scale
    the operator typed or confirmed IN THE ENGINE outranks the reading here, and the reading here
    fills an empty one. A scale this hand-off put there earlier (from whichever sheet was opened
    then) has no rank of its own: the sheet being opened now, a grading sheet, brings its own
    (a utility sheet's 1" = 20' stood over a 1" = 50' grading sheet: volumes 6.25x low).
    `cur` / `cur_src` are the engine project's scale and its source, `det` the host's scale dict
    for the sheet (`_page_scale`). Returns (ft_per_in, source, ledger note or None), or None when
    the engine's own scale stands."""
    fpi = float((det or {}).get("ft_per_paper_inch") or 0)
    if fpi <= 0:
        return None
    by_reviewer = (det or {}).get("set_by") == _BY_REVIEWER
    empty = cur in (None, "", 0)
    engine_set = (not empty) and str(cur_src or "typed") not in _CF_HANDED
    if engine_set and (not by_reviewer or abs(float(cur) - fpi) <= 1e-6):
        # set in the engine: it stands over the reading here, and over a reviewer's pick of the same number - the Scale
        # card writes its typing into the review (2026-09-29), and an open that turned the operator's own word into
        # "confirmed in review" re-armed the compute's refusal wherever another sheet prints another scale
        return None
    src = "confirmed in review" if by_reviewer else "read in review"
    note = None
    if not empty and abs(float(cur) - fpi) > 1e-6:
        # the note is shown in the engine: its scales print like every other (a measured bar's
        # 40.016655966932305 printed 40.0167 here)
        if engine_set:
            note = (f'the scale confirmed in review (1" = {_cf_scale_txt(fpi)} ft) replaces the one set here '
                    f'(1" = {_cf_scale_txt(cur)} ft, {cur_src or "typed"})')
        else:
            note = (f'the scale in force follows this sheet: 1" = {_cf_scale_txt(fpi)} ft ({src}); it was '
                    f'1" = {_cf_scale_txt(cur)} ft, handed over from another sheet')
    return fpi, src, note


# the words a grading sheet prints, and the words of sheets that are something else (the set's
# own triage counts them per page). Measured 2026-09-27 on the corpus's 36 grading pages: 15
# score under 8 on the grading words and 18 print none of them as text (outlined titles), so
# the words can never DECIDE a sheet's role - they can only hint that a sheet reads as another kind.
_CF_GRADING_WORDS = ("GRADING", "CONTOUR", "CONTOURS", "EARTHWORK", "TOPO", "TOPOGRAPHY", "TOPOGRAPHIC")
_CF_OTHER_WORDS = ("UTILITY", "UTILITIES", "WATER", "SEWER", "SANITARY", "ELECTRICAL", "PLUMBING", "MECHANICAL",
                   "FLOOR", "FOUNDATION", "FRAMING", "ROOF", "LANDSCAPE", "IRRIGATION", "STRIPING", "SIGNAGE")


def _cutfill_sheet_hint(job_id: str, page: int) -> "str | None":
    """Advice, never a decision: a sentence when the sheet prints none of a grading sheet's words
    and plenty of another kind's (a utility sheet, a floor plan), else None."""
    try:
        t = json.loads((_job_dir(job_id) / "triage.json").read_text(encoding="utf-8"))
        pages = t.get("pages")
        if isinstance(pages, dict):
            pg = pages.get(str(int(page))) or pages.get(int(page))
        else:
            pg = next((x for x in (pages or []) if isinstance(x, dict) and x.get("page") == int(page)), None)
        if not isinstance(pg, dict) or not pg.get("hits"):
            return None
        grading = other = 0.0
        words = []
        for h in pg["hits"]:
            if not (isinstance(h, (list, tuple)) and len(h) >= 3):
                continue
            w = str(h[0]).upper()
            if w in _CF_GRADING_WORDS:
                grading += float(h[2] or 0)
            elif w in _CF_OTHER_WORDS:
                other += float(h[2] or 0)
                words.append(w.lower())
        if grading == 0 and other >= 8.0:
            return ("this sheet reads as a " + " / ".join(words[:2]) + " sheet: if it is not the grading sheet, "
                    "open Cut / fill on the grading sheet or change this sheet's role in the Sheets card")
        return None
    except Exception:
        return None


@app.post("/api/job/{job_id}/cutfill")
def cutfill_open(job_id: str, body: CutfillOpenBody):
    """The job's cut / fill project, made from the job's PDF on first use. The
    FIRST sheet opened from here is the grading sheet (both surfaces); every
    later sheet starts as "none" and the operator says what it is in the
    engine's Sheets card (opening a second sheet used to make it a second
    grading sheet and stack the two). With `pages` (the "Sheets for cut / fill"
    picker) every ticked, detected sheet joins as both surfaces at once, hands
    over its detection and starts being read: no going back to open each one.
    The scale in force on the sheet (the reviewer's pick, else the reading) is
    handed to the engine with its source. Answers the project id the embedded
    engine opens."""
    job = JOBS.get(job_id) or _404()
    pid = f"job_{job_id}"
    # the sheets ticked in the picker. Each goes to the engine with its own detection, made by the engine running
    # here: a ticked sheet not detected (or detected by older code) is left out and said - the sheet on screen
    # too (the picker's critic, W3: it joined with no detection and no word). A sheet already in the job's cut /
    # fill stays as it is and is never reported left out (W5). The project opens on the sheet on screen when it
    # takes part, else on the first sheet that joins.
    picked = None
    joined, skipped, older = [], [], []
    if body.pages is not None:
        n_pages = int(job.get("pages") or 0)
        bad = sorted({int(x) for x in body.pages if not 0 <= int(x) < n_pages})
        if bad:
            raise HTTPException(400, f"page {bad[0] + 1} is not in this plan set (it has {n_pages})")
        picked = sorted({int(x) for x in body.pages})
        if not picked:
            raise HTTPException(400, "tick at least one sheet of this plan set")
        in_job, shown = _cf_roles(job_id, with_display=True)
        usable = {}
        for pg in picked:
            det, stale = _cf_detected(job_id, pg)
            usable[pg] = det
            if not det and pg not in in_job:
                skipped.append({"page": pg, "why": "detected by an older version: detect it again" if stale
                                else "not detected"})
            elif stale:
                older.append(pg)           # in the job, detected by an older version: said, not handed over again
        takes_part = [pg for pg in picked if pg in in_job or usable[pg]]
        if not takes_part:
            raise HTTPException(400, "none of the ticked sheets is detected: detect them first")
        # the sheet on screen when it takes part, else the sheet the project shows, else the first: never a sheet
        # picked by its page number over the one the job already works on (the second critic's N2: an added
        # sheet took over the scale in force)
        primary = (int(body.page) if int(body.page) in takes_part
                   else shown if shown in takes_part else takes_part[0])
    else:
        primary = int(body.page)
        if _cf_detected(job_id, primary)[1]:
            older.append(primary)          # the open hands it over as before, and says it is from an older version
    with _CUTFILL_LOCK:
        try:
            p = _cutfill.get_project(pid)
        except HTTPException:
            p = _CutfillProject.create(name=Path(_display_name(job["path"])).stem[:60], pid=pid)
            _cutfill._projects[pid] = p
        _cf_reader(p)               # its reads count in _cf_busy whoever starts them, a reset or not
        with p._lock:
            if not p.data["docs"]:
                p.add_document(job["path"], name=_display_name(job["path"]), copy=False)
            # the sheets taking part before this open: what it added is the difference after it (critic 3, w2:
            # "added 0 sheets" when the sheet it opened on was the one added)
            before = {k for k, v in p.data["pages"].items() if (v or {}).get("role") in _CF_ACTIVE}
            for v in p.data["pages"].values():
                (v or {}).pop("review_scale", None)     # b888800's copy of the review: stale by nature, never read now
            # a detection record naming a job this project may not reach feeds no lines: the page route took any key
            # before 3101f2a, and such a record fed another owner's detection to the engine (critic 18, RISK-2)
            # a link recorded onto doc 0 (the job's own PDF: a same-file link before c99840a refused it) is never a link
            # (critic 20, MINOR-9)
            (p.data.get("host_links") or {}).pop("0", None)
            for k2, v2 in list(p.data["pages"].items()):
                cr2 = (v2 or {}).get("core_result")
                if cr2 and not _cf_record_reachable(p, k2, cr2) and not _cf_adopt_link(p, k2, cr2):
                    v2["core_result"] = None
            # a linked plan set the caller may not open NOW (a demo card taken off the list): its sheets stop taking
            # part - document, link and readings stay (critic bid-content-1, WRONG-2); the reply says so
            hidden_out = _cf_detach_hidden(p, job_id)
            key = f"d0p{primary}"
            ps = p.page_settings(key)
            if not ps.get("host_seen"):
                ps["host_seen"] = True
                if picked is not None:
                    # ticked in the picker: it takes part (a role set before stands)
                    if ps.get("role") in (None, "", "none"):
                        ps["role"] = "both"
                elif ps.get("role") in (None, "", "none"):
                    # a role the operator set in the engine's Sheets card stands: the first host open took such a
                    # sheet out of the job with nothing said (critic 4, r6)
                    others = any((v or {}).get("role") in _CF_ACTIVE
                                 for k, v in p.data["pages"].items() if k != key)
                    ps["role"] = "none" if others else "both"
            elif picked is not None and ps.get("role") in (None, "", "none"):
                ps["role"] = "both"                # left out before, ticked now: it takes part
            # advice only (the words a sheet prints cannot decide its role): shown on the bar
            role_hint = (_cutfill_sheet_hint(job_id, primary)
                         if ps.get("role") in _CF_ACTIVE else None)
            # one line detection feeding every tool: when this sheet has been detected here, the
            # engine reads its lines from that result (its own linework, CORE's joins - see
            # cutfill/core_reading.py) instead of chaining the sheet a second time
            rf = str(result_path(_job_dir(job_id), primary))
            if picked is None:
                ps["core_result"] = rf if os.path.exists(rf) else None
            elif usable.get(primary):
                ps["core_result"] = rf          # a sheet in the job read by the engine alone keeps its reading
            # every other ticked sheet joins the same way, with its own detection (the picker detects the
            # ticked sheets before it opens; one it could not detect was put in `skipped` above)
            for pg in (picked or []):
                if pg == primary or not usable[pg]:
                    continue
                ps2 = p.page_settings(f"d0p{pg}")
                ps2["host_seen"] = True
                if ps2.get("role") in (None, "", "none"):
                    ps2["role"] = "both"
                ps2["core_result"] = str(result_path(_job_dir(job_id), pg))
                if pg not in in_job:
                    joined.append(pg)              # new to the job (one already in it hands over its detection again)
            # the review's decisions travel with the lines (PLAN 5.4): a line erased in review is
            # not a contour here, a family named as a surface takes it, the work area is the
            # limit when none is drawn. Matched by geometry in the engine (cutfill/core_edits.py).
            # They are the sheet ON SCREEN's decisions: another ticked sheet keeps what it handed over before
            review_changed, review_error = False, None
            if body.edits is not None and ps.get("core_result") and primary == int(body.page):
                try:
                    from cutfill.core_edits import normalize as _cf_review
                    _core = json.loads(Path(rf).read_text(encoding="utf-8"))
                    review_changed = p.put_core_edits(key, _cf_review(_core, body.edits))
                except Exception as e:      # noqa: BLE001 - the hand-over never blocks the open; it is said
                    review_error = str(e)[:200] or type(e).__name__
            s = p.data["settings"]
            # the sheet the engine shows: the one the operator is looking at, except that a limit
            # or grading zones drawn on another sheet keep that sheet on top (they belong to their
            # sheet: zones read in another sheet's frame land somewhere else), and so do volumes that still stand on
            # it (_cf_result_stands: a visit to look must not make them stale). Settled BEFORE the
            # scale: the scale in force is the sheet shown's
            pinned = (s.get("region") or any((z or {}).get("page") in (None, "", s.get("display_page")) for z in (s.get("zones") or []))
                      or _cf_result_stands(p))
            # never a sheet that takes no part: the engine would then show the lowest page taking part, not the
            # one it showed, under the old scale (critic 4, W1a). The engine page still opens on the sheet on screen
            if ps.get("role") in _CF_ACTIVE and (not pinned or s.get("display_page") in (None, key)):
                s["display_page"] = key
            # one scale in force (_cutfill_scale_handoff), and it is the SHEET SHOWN's: every other sheet
            # is placed on that sheet's frame, so its scale turns the frame's points into feet. Only the
            # sheet the engine shows after this open hands its scale over, by rank; another sheet says what
            # it reads and leaves the scale alone (critic 3, w1: the picker's guard kept a scale set from
            # another sheet while the sheet shown moved - 40 ft on a sheet drawn at 100, volumes 6.25x low -
            # and an open under a pinned sheet put the opened sheet's scale on the pinned sheet's frame)
            handed = None
            if ps.get("core_result") and ps.get("role") in _CF_ACTIVE:
                try:
                    det = _page_scale(job_id, primary, json.loads(Path(rf).read_text(encoding="utf-8")))
                except Exception:
                    det = None
                got = _cutfill_scale_handoff(s.get("scale_ft_per_in"), s.get("scale_source"), det)
                if got and _cf_display_for(s, p.data["pages"]) != key:
                    fpi, src, _note = got
                    if s.get("scale_ft_per_in") and abs(float(s["scale_ft_per_in"]) - fpi) > 1e-6:
                        p.note(key, f'this sheet reads 1" = {_cf_scale_txt(fpi)} ft ({src}); it is not the sheet '
                                    f'shown, whose scale is in force: 1" = {_cf_scale_txt(s["scale_ft_per_in"])} ft')
                elif got:
                    fpi, src, note = got
                    if note:
                        p.note(key, note)
                    s["scale_ft_per_in"] = fpi
                    s["scale_source"] = src
                    s["scale_page"] = key          # the sheet this scale belongs to (compute checks it)
                    handed = src
            # a scale read on another sheet, when the sheet shown hands none, is not dropped here: the engine's compute
            # refuses it while the sheet shown prints no scale to check it against (placement.foreign_scale; critic 4
            # W1b, and critic 5 WRONG-1: dropping it lost a confirmed scale and turned refusals into computes)
            # a sheet detected again since the engine read it: its reading holds the old detection's lines
            # (critic 3, r1: the host showed the new detection, the volumes stood on the old one, nothing
            # said). Read again in full, on any open (_cf_detection_changed)
            redo = [k2 for k2, v2 in list(p.data["pages"].items())
                    if (v2 or {}).get("role") in _CF_ACTIVE and _cf_detection_changed(p, k2)]
            p.save()
        # a sheet already read here is read again with the review's decisions (seconds, on the
        # cached reading); a sheet never read takes them at its first read; one detected again is
        # read in full below instead
        if review_changed and key not in redo and os.path.exists(p.analysis_path(key)):
            p.start_analysis(key, force=True)
        # the picker's sheets start being read (what "Analyze" on each did), two at a time in the server; a sheet
        # read before keeps its reading unless it is stale (made by other code, or before the review's decisions).
        # Every ticked sheet that takes part, not only the new ones (critic 3, m4: a stale sheet in the job stayed
        # stale, and one whose read a restart dropped stayed unread)
        want = []
        if picked is not None:
            for pg in [primary] + [x for x in takes_part if x != primary]:
                k2 = f"d0p{pg}"
                if p.page_settings(k2).get("role") in (None, "", "none"):
                    continue
                if (p.jobs.get(k2) or {}).get("status") in ("running", "queued"):
                    continue
                if not os.path.exists(p.analysis_path(k2)) or p.analysis_stale(k2):
                    want.append(k2)
        want += [k2 for k2 in redo if k2 not in want]
        _cutfill_read_queue(p, want, full=redo)
        with p._lock:
            after = {k for k, v in p.data["pages"].items() if (v or {}).get("role") in _CF_ACTIVE}
        in_job_now = sum(1 for k in after if k.startswith("d0p"))
    added = sorted(int(k[3:]) for k in after - before if k.startswith("d0p") and k[3:].isdigit())
    reread = sorted(int(k[3:]) for k in redo if k.startswith("d0p") and k[3:].isdigit())
    hr = p.data["settings"].get("host_rows") or {}
    return {"pid": pid, "url": f"/cutfill/?embed=1&pid={pid}", "core_result": bool(ps.get("core_result")),
            "role": ps.get("role"), "role_hint": role_hint,
            "review": ps.get("core_edits"), "review_changed": review_changed, "review_error": review_error,
            "scale_source": p.data["settings"].get("scale_source"), "scale_handed": handed,
            # ONE set of cut / fill rows per job: the page whose takeoff holds them (or None)
            "rows_page": hr.get("page") if isinstance(hr.get("page"), int) else None,
            # the picker: the sheet the project opens on, the other sheets that joined, any left out, and how
            # many sheets of this plan set take part now
            "page": primary, "joined": joined, "skipped": skipped, "in_job": in_job_now,
            # the sheets that take part now and did not before this open (the one it opened on too)
            "added": added,
            # sheets taking part whose detection an older version made (the page asks for Detect again)
            "older": older,
            # sheets detected again since the engine read them: being read again in full (a linked plan set's
            # sheets are counted apart: they have no page number in this set)
            "reread": reread, "reread_linked": sum(1 for k in redo if not k.startswith("d0p")),
            # sheets of a linked plan set this account may no longer open, taken out of the cut / fill by this open
            "hidden_out": hidden_out,
            "hidden_note": (f"{len(hidden_out)} sheet(s) {_CF_HIDDEN_WORDS} They were taken out of this cut / fill."
                            if hidden_out else None)}


class CutfillLinkBody(BaseModel):
    job: str = ""


@app.post("/api/job/{job_id}/cutfill/link")
def cutfill_link(job_id: str, body: CutfillLinkBody, request: Request):
    """Another plan set's sheets in this job's cut / fill project: a structural set's walls need
    the civil set's existing ground under them (the estimator's dig depth per wall is existing
    ground minus the footing bottom). The other set's PDF joins this job's project as a second
    document, its grading sheet is read there and aligned to this sheet with two matching
    points, and the structure panel asks for the ground under each wall. Only a set the caller
    can open joins; nothing is copied."""
    job = JOBS.get(job_id) or _404()
    other = JOBS.get(body.job) or _404()
    me = _who(request)
    if me is not None and not _can_open(other, me, _is_staff(request)):
        _404()                          # a stranger's set, or a staff-only owner-less one
    if body.job == job_id:
        raise HTTPException(400, "that is this plan set")
    pid = f"job_{job_id}"
    with _CUTFILL_LOCK:
        try:
            p = _cutfill.get_project(pid)
        except HTTPException:
            p = _CutfillProject.create(name=Path(_display_name(job["path"])).stem[:60], pid=pid)
            _cutfill._projects[pid] = p
        with p._lock:
            if not p.data["docs"]:
                p.add_document(job["path"], name=_display_name(job["path"]), copy=False)
            di = None
            for i, d in enumerate(p.data["docs"]):
                if os.path.normcase(str(d.get("path"))) == os.path.normcase(str(other["path"])):
                    di = i
            if di == 0:
                # the same PDF as this job's own (two uploads of one file share its stored path): linked, its records
                # went onto this job's own sheets and made them another owner's (critic 19, MINOR-1)
                raise HTTPException(400, "that plan set is the same PDF as this one: its sheets are already here")
            _was = (p.data.get("host_links") or {}).get(str(di)) if di is not None else None
            if _was not in (None, body.job) and _was in JOBS:
                # a PDF already linked here as another plan set (one stored file for two owners' uploads): relinked, the
                # owner's own set answered to the other owner's review (critic 20, MINOR-7). A link to a set deleted
                # since is no claim: the same PDF uploaded again links (it was refused for good: critic 21, RISK-3)
                raise HTTPException(409, "that PDF is already linked here as another plan set")
            if di is None:
                rec = p.add_document(other["path"], name=Path(other["path"]).name, copy=False)
                di = len(p.data["docs"]) - 1
            # which job this document is, recorded by the host (the engine's routes cannot write it): its sheets answer
            # to that job's review (_cf_sheet_job_page) - read, and written only for a set of the same owner
            p.data.setdefault("host_links", {})[str(di)] = body.job
            # the other set's detected sheets feed the engine's lines there too - on every link, so a sheet detected
            # after the first link gets its record (critic 18, RISK-3)
            for pg in range(int(other.get("pages") or 0)):
                rf = str(result_path(_job_dir(body.job), pg))
                if os.path.exists(rf) and not p.page_settings(f"d{di}p{pg}").get("core_result"):
                    p.page_settings(f"d{di}p{pg}")["core_result"] = rf
            p.save()
    return {"pid": pid, "doc": di, "pages": int(other.get("pages") or 0), "name": Path(other["path"]).name}


class CutfillGradeBody(BaseModel):
    # sample points on this job's page (page pt); answered with the existing ground under each
    pts: list = []
    surface: str = "existing"


@app.post("/api/job/{job_id}/page/{page}/cutfill/grade")
def cutfill_grade(job_id: str, page: int, body: CutfillGradeBody):
    """The existing (or proposed) ground under points on this sheet, from the job's cut / fill
    project: the surface the volumes rest on, mapped through the sheet alignment. Heights are
    null where the surface does not reach; the answer names the sheet the surface was built on."""
    JOBS.get(job_id) or _404()
    pid = f"job_{job_id}"
    try:
        p = _cutfill.get_project(pid)
    except HTTPException:
        raise HTTPException(400, "open Cut / fill for this plan set first")
    pts = body.pts or []
    if len(pts) > 20000:
        raise HTTPException(400, "too many points")
    from cutfill.project import grade_at as _grade_at
    return JSONResponse(_grade_at(p, f"d0p{int(page)}", pts, "proposed" if body.surface == "proposed" else "existing"))


class CutfillRowsBody(BaseModel):
    added: bool = False
    pid: str = ""
    rows: list = []
    summary: dict | None = None


@app.post("/api/job/{job_id}/page/{page}/cutfill/rows")
def cutfill_rows(job_id: str, page: int, body: CutfillRowsBody):
    """The operator put the engine's volumes into (or took them out of) the
    itemized takeoff. Recorded in the ledger like every calculator decision;
    the rows themselves live in the takeoff as always."""
    JOBS.get(job_id) or _404()
    ledger.record("cutfill_rows", job_id, page, None,
                  {"added": body.added, "rows": body.rows[:60]},   # every zone's row pair and the net (8 cut them off)
                  {"pid": body.pid, "summary": body.summary},
                  pdf_path=_ledger_pdf(job_id))
    # ONE set of cut / fill rows per job (the engine's project is the job's): the project
    # remembers which page's takeoff holds them, so another page cannot add them a second time
    # (two pages doubled the bid sheet) and a fresh compute knows where they belong
    rows_page = None
    try:
        with _CUTFILL_LOCK:
            p = _cutfill.get_project(f"job_{job_id}")
            with p._lock:
                s = p.data["settings"]
                cur = s.get("host_rows") or {}
                if body.added:
                    s["host_rows"] = {"page": int(page), "t": time.time()}
                elif cur.get("page") in (None, int(page)):
                    s.pop("host_rows", None)
                rows_page = (s.get("host_rows") or {}).get("page")
                p.save()
    except Exception:
        pass
    return {"ok": True, "rows_page": rows_page}


app.mount("/cutfill", _cutfill.app, name="cutfill")
app.mount("/", StaticFiles(directory=ROOT / "webui", html=True), name="ui")
