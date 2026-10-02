"""THE CLIENT GATE (critic legacy-3, WRONG-1 / RISK-1).

After the deploy, a browser tab still running v1.8.5's JavaScript keeps its session and could WRITE to the new server:
its approve got `src_marks: 1` stamped over rows it never marked (true old rows stopped counting as old, and the next
seat's approve dropped them silently); its calcstate save wiped `rows`, `cf`, prices and trades from the page record.
Every page this app serves loads webui/client.js first, which puts `X-Core-Client: 2` on every state-changing request;
server.py _client_gate refuses a write without it, after sign-in and ownership, writing nothing:
"CORE was updated. Reload this page to keep working." (v1.8.5's approve and calcstate save show a 409's `detail` on the
status line.)

  1. EVERY write route of the app and of the cut / fill mount - enumerated from the live route tables, so a route added
     tomorrow is swept the day it lands - is refused without the marker; the sign-in / sign-out / change-password forms
     are the only exemptions.
  2. A v1.8.5-shaped approve and calcstate save are refused and change nothing on disk; the same with the marker work.
  3. The marker: client.js is the first element of <head> on every page that writes, and under node it marks fetch
     (a Request too), XMLHttpRequest and sendBeacon writes, never reads.

    python tests/test_client_gate.py
"""
import asyncio
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="cgate_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cgate_lg_")
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("TAKEOFF_HOSTED", None)

import pymupdf  # noqa: E402
from fastapi.routing import APIRoute  # noqa: E402
from starlette.routing import Mount  # noqa: E402

import server  # noqa: E402
from takeoff_engine.detect_page import engine_sig  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


JID = "c9c9c9c9c9c9"
pdf = Path(server.JOBS_DIR) / f"{JID}.pdf"
doc = pymupdf.open()
doc.new_page(width=612, height=792)
doc.save(str(pdf))
doc.close()
D = server._job_dir(JID)
D.mkdir(parents=True, exist_ok=True)
(D / "p0_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "chains": [], "styles": [],
                                              "engine_sig": engine_sig(), "scale": {"ft_per_paper_inch": 50.0}}))
server.JOBS[JID] = {"path": str(pdf), "pages": 1, "status": {0: "done"}, "owner": None,
                    "mtime": round(pdf.stat().st_mtime, 2), "sizes": [[612.0, 792.0]]}


async def _asgi(method, path, body: bytes, marker: bool):
    headers = [(b"host", b"testserver"), (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]
    if marker:
        headers.append((b"x-core-client", b"2"))
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1", "method": method,
             "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
             "headers": headers, "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"body": False}
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["body"]:
            sent["body"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(msg):
        if msg["type"] == "http.response.start":
            out["status"] = msg["status"]
        elif msg["type"] == "http.response.body":
            out["body"] += msg.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"]


def call(method, path, body=None, marker=False):
    raw = body if isinstance(body, bytes) else (b"" if body is None else json.dumps(body).encode())
    st, b = asyncio.run(_asgi(method, path, raw, marker))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, {"raw": b[:80]}


def write_routes():
    """(method, path template) of every state-changing route: the app's and the cut / fill mount's."""
    out = []
    for r in server.app.routes:
        if isinstance(r, APIRoute):
            out += [(m, r.path) for m in sorted(r.methods) if m not in ("GET", "HEAD", "OPTIONS")]
        elif isinstance(r, Mount) and r.path == "/cutfill":
            for rr in r.routes:
                if isinstance(rr, APIRoute):
                    out += [(m, "/cutfill" + rr.path) for m in sorted(rr.methods) if m not in ("GET", "HEAD", "OPTIONS")]
    return out


def fill(tpl):
    p = tpl.replace("{job_id}", JID).replace("{page}", "0").replace("{pid}", f"job_{JID}")
    return re.sub(r"\{[^}]+\}", "x", p)


GATE = "CORE was updated. Reload this page to keep working."
# ---- 1. every write route refuses without the marker -------------------------------------------------------------
routes = write_routes()
api = [(m, t) for m, t in routes if t.startswith("/api/") or t.startswith("/cutfill/api/")]
exempt = {"/api/login", "/api/logout", "/api/change-password"}
open_ = []
refused = 0
for m, t in api:
    if t in exempt:
        continue
    st, j = call(m, fill(t))
    if st == 409 and j.get("detail") == GATE:
        refused += 1
    else:
        open_.append((m, t, st, str(j)[:80]))
outside = [(m, t) for m, t in routes if not (t.startswith("/api/") or t.startswith("/cutfill/api/"))]
n_src = sum(len(re.findall(r'@app\.(?:post|put|delete|patch)\(', (ROOT / f).read_text(encoding="utf-8")))
            for f in ("server.py", "cutfill/server.py"))
n_cf = sum(1 for _, t in api if t.startswith("/cutfill/"))
check(not open_ and refused == len(api) - len(exempt) == n_src - len(exempt) and n_cf >= 10 and not outside,
      f"1: every one of the {refused} write routes (app + cut / fill mount, from the route tables) refuses a write "
      f"without the marker; none answers otherwise: {open_[:5]}; writes outside the APIs: {outside}")
st_lo, j_lo = call("POST", "/api/logout")
check(st_lo == 200, f"1: sign-out works without the marker (v1.8.5's tab can still sign out): {st_lo}")
check(sorted(exempt) == sorted(server._CLIENT_EXEMPT), f"1: the exemptions are exactly the sign-in forms: {server._CLIENT_EXEMPT}")

# ---- 2. a v1.8.5-shaped approve and calcstate save: refused, nothing on disk changes -----------------------------------
exp0 = {"imageDpi": 300, "scale": {"pixelsPerUnit": 6.0, "unit": "ft"}, "elements": [], "notes": [],
        "items": [{"label": "Zone 1 cut", "qty": 100.0, "unit": "CY", "count": 1, "length_ft": None}]}
cs0 = {"v": 1, "rows": [{"label": "Zone 1 cut", "qty": 100.0, "unit": "CY", "src": "earthwork-old"}], "cf": {"computed_at": 1},
       "prices": {"Zone 1 cut": 5.0}, "trades": {"Zone 1 cut": "Earthwork & Grading"}, "ew": {"rows": []}}
(D / "p0_elements.json").write_text(json.dumps(exp0))
(D / "p0_calcstate.json").write_text(json.dumps(cs0))
before = {f.name: f.read_bytes() for f in D.iterdir()}
old_approve = {"labels": {}, "image_dpi": 300, "extra_items": [{"label": "Zone 1 cut", "qty": 100.0, "unit": "CY"}], "notes": []}
old_calc = {"v": 1, "engine_sig": "x", "ew": {"rows": []}, "checks": {"done": []}}      # v1.8.5's payload: no rows, cf, ...
st_a, j_a = call("POST", f"/api/job/{JID}/page/0/approve", old_approve)
st_c, j_c = call("POST", f"/api/job/{JID}/page/0/calcstate", old_calc)
after = {f.name: f.read_bytes() for f in D.iterdir()}
check(st_a == 409 and j_a.get("detail") == GATE and st_c == 409 and j_c.get("detail") == GATE and after == before,
      f"2: a v1.8.5 tab's approve and calcstate save are refused, and nothing on disk changes: {st_a} {j_a} / {st_c}")
st_a2, j_a2 = call("POST", f"/api/job/{JID}/page/0/approve", dict(old_approve, extra_items=[
    {"label": "Zone 1 cut", "qty": 100.0, "unit": "CY", "src": "earthwork-old"}],
    scale_seen=server._scale_stamp(JID, 0)), marker=True)   # this app's page also sends the scale record it loaded
st_c2, _ = call("POST", f"/api/job/{JID}/page/0/calcstate", dict(cs0, checks={"done": ["a"]}), marker=True)
check(st_a2 == 200 and st_c2 == 200 and json.loads((D / "p0_calcstate.json").read_text())["checks"] == {"done": ["a"]},
      f"2: the same writes from this app's page (with the marker) work: {st_a2} {j_a2 if st_a2 != 200 else ''} / {st_c2}")

# ---- 3. the marker is installed first, and marks every write transport ----------------------------------------------
W = ROOT / "webui"
heads = {}
for f in ("index.html", "cutfill/index.html", "login.html", "change-password.html"):
    s = (W / f).read_text(encoding="utf-8")
    head = s[s.index("<head>"):s.index("</head>")]
    first = re.search(r"<script[^>]*>", head)
    heads[f] = bool(first) and first.group(0).startswith('<script src="/client.js') and head.index("<script") < head.index("<link")
check(all(heads.values()), f"3: client.js is the first script of <head>, before any stylesheet, on every page that writes: {heads}")
js = (W / "client.js").read_text(encoding="utf-8")
harness = r"""
const seen = [];
class Headers { constructor(h) { this.m = {}; if (h && h.m) Object.assign(this.m, h.m); else if (h) for (const [k, v] of Object.entries(h)) this.m[k.toLowerCase()] = v; }
  set(k, v) { this.m[k.toLowerCase()] = v; } get(k) { return this.m[k.toLowerCase()]; } }
class Request { constructor(u, o) { this.url = u; this.method = (o && o.method) || "GET"; this.headers = new Headers((o && o.headers) || {}); } }
const window = { fetch: (input, init) => { seen.push(["fetch", (init && init.method) || (input && input.method) || "GET", init && init.headers ? init.headers.get("X-Core-Client") || null : null]); return Promise.resolve({}); } };
function XMLHttpRequest() { this.h = {}; }
XMLHttpRequest.prototype.open = function (m) { this.m = m; };
XMLHttpRequest.prototype.setRequestHeader = function (k, v) { this.h[k] = v; };
XMLHttpRequest.prototype.send = function () { seen.push(["xhr", this.m, this.h["X-Core-Client"] || null]); };
const navigator = { sendBeacon: () => { seen.push(["beacon-raw"]); return true; } };
globalThis.Request = Request; globalThis.Headers = Headers;
""" + js + r"""
window.fetch("/api/x", { method: "POST", headers: { "Content-Type": "application/json" } });
window.fetch("/api/x");
window.fetch(new Request("/api/y", { method: "DELETE" }));
const x = new XMLHttpRequest(); x.open("POST", "/api/upload"); x.send("b");
const g = new XMLHttpRequest(); g.open("GET", "/api/me"); g.send();
navigator.sendBeacon("/api/z", "d");
console.log(JSON.stringify(seen));
"""
out = subprocess.run(["node", "-e", harness], capture_output=True, text=True, encoding="utf-8", errors="replace")
try:
    seen = json.loads(out.stdout.strip().splitlines()[-1])
except (ValueError, IndexError):
    seen = out.stderr[:300]
check(seen == [["fetch", "POST", "2"], ["fetch", "GET", None], ["fetch", "DELETE", "2"], ["xhr", "POST", "2"], ["xhr", "GET", None],
               ["fetch", "POST", "2"]],
      f"3: client.js marks fetch (a Request too), XMLHttpRequest and sendBeacon writes, and never a read: {seen}")
# the scripts that write without the page carry the marker too
seed = (ROOT / "seed-demo.sh").read_text(encoding="utf-8")
check("-H 'X-Core-Client: 2'" in seed, "3: seed-demo.sh (the one script that writes over HTTP) sends the marker")

print("\n" + (f"{fails} failure(s)" if fails else "all ok"))
sys.exit(1 if fails else 0)
