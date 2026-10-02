"""No generated API docs, for any account (critic bid-content-1, RISK-4).

FastAPI serves /openapi.json, /docs and /redoc by default, built from every route's docstring. Signed in, any of
the eighteen client accounts could read them, and the docstrings name clients (a printed bid sheet's owner, jobs
used as examples, a real datum). They are off on the host app AND on every mounted sub-app (cut / fill), and this
checks it through the real session middleware for a signed-out caller, a client and staff.

    python tests/test_no_api_docs.py
"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="no_api_docs_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="no_api_docs_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

from fastapi import FastAPI  # noqa: E402
from starlette.routing import Mount  # noqa: E402

import server  # noqa: E402
from takeoff_engine import users as U  # noqa: E402

U.save({"alice": U.hash_password("orchid-lantern-4471"),
        "staff": dict(U.hash_password("granite-sparrow-2210"), staff=True)})
server._UI_PASSWORD = None
CK = {"signed out": None, "client": server._sign_session("alice"), "staff": server._sign_session("staff")}
CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


async def _asgi(path, cookie):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"2")]
    if cookie:
        headers.append((b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode()))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": "GET", "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    out = {"s": None, "b": b""}

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(m):
        if m["type"] == "http.response.start":
            out["s"] = m["status"]
        elif m["type"] == "http.response.body":
            out["b"] += m.get("body", b"")
    await server.app(scope, receive, send)
    return out["s"], out["b"]


# every FastAPI app the host serves: itself and each mount, however deep
apps = []


def walk(a, prefix):
    apps.append((prefix or "/", a))
    for r in getattr(a, "routes", []):
        if isinstance(r, Mount) and isinstance(r.app, FastAPI):
            walk(r.app, prefix + r.path)


walk(server.app, "")
ok(len(apps) >= 2, "the host and its mounted cut / fill app are both found", [p for p, _ in apps])
for prefix, a in apps:
    ok(a.openapi_url is None and a.docs_url is None and a.redoc_url is None,
       f"{prefix}: openapi / docs / redoc are off", (a.openapi_url, a.docs_url, a.redoc_url))

PATHS = []
for prefix, _a in apps:
    base = "" if prefix == "/" else prefix
    PATHS += [base + "/openapi.json", base + "/docs", base + "/redoc", base + "/docs/oauth2-redirect"]
for who, ck in CK.items():
    for path in PATHS:
        st, body = asyncio.run(_asgi(path, ck))
        served = st == 200 and (b"openapi" in body.lower() or b"swagger" in body.lower() or b"redoc" in body.lower())
        ok(not served, f"{who}: {path} serves no API description", (st, body[:80]))

fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
sys.exit(1 if fails else 0)
