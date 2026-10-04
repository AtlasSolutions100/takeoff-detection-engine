"""THE TAKEOFF STARTS EMPTY - the server half (2026-10-03, the owner: "make it where takeoffs are empty and items need
to be added to takeoff").

Until now every style the engine picked was in the takeoff after Detect and the person dropped what they did not want;
the ticks lived in one browser, keyed by the document's name. Now nothing is in the takeoff until the person adds it,
and what they added is the server's record (p<N>_ticks.json, server.py THE TAKEOFF STARTS EMPTY):

  A. a page nobody ticked reads EMPTY, and opening it (five times) writes nothing;
  B. Approve with nothing in the takeoff is refused, in plain words, and writes nothing (no export, no scale record);
  C. the page sends the TICKED set; the server keeps exactly that (unknown ids dropped); a stale write is refused;
  D. the export, the bid-sheet CSV and the report carry only what was added;
  E. a shared sheet: each account its own ticks, nobody sees another's, nothing is brought over; a demo card's fresh
     open sweeps them;
  H. a re-detect keeps the ticks whose style is still on the page, by the style key (never by position), drops the
     rest (counted), and lists new styles unticked;
  I. a page with only a calculator row (Areas, Flooring, Cut / fill...) may still be approved.

Nothing is brought over from an earlier version (the repair after CRITIC-empty-1 took the 2.0 migration out, with the
sections F and G that pinned it): tests/test_empty_repair.py holds that rule, and the approve held to the record.

Every request goes through the real middleware (sign-in, ownership, client gate) with no server listening.

    python tests/test_empty_takeoff.py          (fails on e09a605: no /ticks route, an empty approve is exported)
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="empty_tk_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="empty_tk_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

CARD = "C4.0-SITE-GRADING-PLAN-Rev.1.pdf"       # a shared sheet (demo-plans.json shared_names)


def style(sid, klass, layer, color, width, sig, length):
    return {"style_id": sid, "suggested_label": layer, "class": klass, "layer": layer, "color": color,
            "width": width, "signature": sig, "length_pt": length, "n": 1, "texture": False}


def chain(cid, st, pts):
    import math
    ln = sum(math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1]) for i in range(1, len(pts)))
    return {"chain_id": cid, "style_id": st["style_id"], "class": st["class"], "color": st["color"],
            "width": st["width"], "layer": st["layer"], "length_pt": round(ln, 3), "points": pts}


S_WAT = style(1, "dashed", "C-WATR", [0.0, 0.0, 1.0], 0.5, "d6.0 g3.0", 400.0)
S_CRB = style(2, "solid", "C-CURB", [0.0, 0.0, 0.0], 1.0, "solid", 900.0)
S_STM = style(3, "dashed", "C-STRM", [0.0, 0.6, 0.0], 0.5, "d9.0 g4.5", 300.0)
S_ANN = style(4, "annotation", "C-ANNO", [0.0, 0.0, 0.0], 0.25, "sheet furniture", 100.0)


def result(styles, ids=None):
    """A detection: each style with two lines. `ids` renumbers the styles (a re-detect)."""
    ids = ids or {}
    sts, chs = [], []
    for k, s in enumerate(styles):
        s2 = dict(s, style_id=ids.get(s["style_id"], s["style_id"]))
        sts.append(s2)
        y = 100 + 60 * k
        chs.append(chain(len(chs) + 1, s2, [[72.0, y], [372.0, y]]))
        chs.append(chain(len(chs) + 1, s2, [[72.0, y + 20], [272.0, y + 20], [272.0, y + 40]]))
    return {"page": {"width_pt": 612, "height_pt": 792}, "styles": sts, "chains": chs, "engine_sig": "sig-a",
            "scale": {"ft_per_paper_inch": 20.0, "text": "1\" = 20'", "source": "text"}}


RES = result([S_WAT, S_CRB, S_STM, S_ANN])
reg = {}
IDS = {}
for i, (k, name, owner) in enumerate((("SH", CARD, None), ("A", "alice-fresh.pdf", "alice"),
                                      ("F", "alice-redetect.pdf", "alice"), ("N", "alice-new.pdf", "alice"))):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / ("upload_%012x_%s" % (i + 1, name))
    d.save(str(pdf))
    d.close()
    jid = (k.lower() * 12)[:12]
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(RES))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": owner, "bid": None}
    IDS[k] = jid
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471"), "bob": U.hash_password("basalt-meadow-9083")})
server._UI_PASSWORD = None
CK = {u: server._sign_session(u) for u in ("alice", "bob")}
# two names the repair after CRITIC-empty-1 added, read with a default so this file runs to its end on the tree before it
ADDED = getattr(server, "EXPORT_ADDED_KEY", "takeoff_added")
NO_MIG = getattr(server, "TICKS_NO_MIGRATE", "(no such sentence before the repair)")
fails = 0


def check(cond, msg, got=None):
    global fails
    print(("ok: " if cond else "FAIL: ") + msg + ("" if cond or got is None else f"  (got {got!r})"))
    if not cond:
        fails += 1


async def _asgi(method, path, cookie, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()),
               (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 1), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"s": None, "b": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["s"] = m["status"]
        elif m["type"] == "http.response.body":
            out["b"] += m.get("body", b"")
    await server.app(scope, receive, send)
    return out["s"], out["b"]


def call(method, path, who, body=None, raw=False):
    st, b = asyncio.run(_asgi(method, path, CK[who], json.dumps(body).encode() if body is not None else b""))
    if raw:
        return st, b
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def snap(jid):
    """Every file of the job folder with its bytes: 'writes nothing' is this not changing."""
    return {f.name: f.read_bytes() for f in sorted((J / jid).iterdir()) if f.is_file()}


def ticks(jid, who="alice"):
    return call("GET", f"/api/job/{jid}/page/0/ticks", who)


def put(jid, version, styles, who="alice", migrate=None):
    b = {"version": version, "styles": styles}
    if migrate:
        b["migrate"] = migrate
    return call("POST", f"/api/job/{jid}/page/0/ticks", who, b)


def approve(jid, labels, who="alice", extra=None):
    """As the page approves: on the version of the takeoff record it holds (the server holds the approve to it)."""
    stamp = call("GET", f"/api/job/{jid}/page/0/scale", who)[1]["stamp"]
    return call("POST", f"/api/job/{jid}/page/0/approve", who,
                {"labels": {str(k): v for k, v in labels.items()}, "scale": None, "scale_seen": stamp,
                 "extra_items": extra or [], "ticks_version": ticks(jid, who)[1].get("version", 0)})


A, SH, F, N = IDS["A"], IDS["SH"], IDS["F"], IDS["N"]

# ---- A. a fresh page is empty, and opening it writes nothing -----------------------------------------------------
before = snap(A)
st, d = ticks(A)
check(st == 200 and d.get("state") == "fresh" and d.get("styles") == [] and d.get("version") == 0,
      "A: a detected page nobody ticked: the takeoff is EMPTY (state fresh, no styles)", (st, d))
check("may_migrate" not in d and d.get("earlier") is None,
      "A: ...and the reply offers nothing to bring over (no page is ever asked to)", d)
for _ in range(5):
    ticks(A)
check(snap(A) == before, "A: five opens write nothing (the folder is byte for byte the same)", sorted(snap(A)))

# ---- B. approve with nothing in the takeoff is refused, and writes nothing ---------------------------------------
st, d = approve(A, {})
check(st == 409 and d.get("detail") == "Nothing is in the takeoff. Tick the styles you want first.",
      "B: Approve with nothing ticked is refused: 'Nothing is in the takeoff. Tick the styles you want first.'", (st, d))
check(snap(A) == before and not (J / A / "p0_elements.json").exists() and not (J / A / "p0_scale.json").exists(),
      "B: ...and nothing was written (no export, no scale record)", sorted(snap(A)))

# ---- C. the page sends the ticked set; the server keeps exactly that ----------------------------------------------
st, d = put(A, 0, [1, 3, 99, "2", True])
check(st == 200 and d.get("version") == 1 and d.get("styles") == [1, 3],
      "C: the ticked set is stored: styles 1 and 3 (an id not on the page, a string, a bool are not)", (st, d))
rec = json.loads((J / A / "p0_ticks.json").read_text())["by"]["job"]
check([t["sid"] for t in rec["styles"]] == [1, 3] and all(t.get("key") for t in rec["styles"]) and "migrated" not in rec,
      "C: the record holds each added style with its style key, and nothing else", rec)
check(not any(t["sid"] == 2 for t in rec["styles"]), "C: an unticked style is simply absent (no 'untick' is kept)")
st, d = ticks(A)
check(st == 200 and d.get("state") == "explicit" and d.get("styles") == [1, 3] and d.get("version") == 1,
      "C: a reopen reads exactly what was added", d)
mid = snap(A)
st, d = put(A, 0, [2])
check(st == 409 and d.get("detail") == server.TICKS_STALE and snap(A) == mid,
      "C: a write on an old version (another tab) is refused with a sentence and changes nothing", (st, d))
st, d = put(A, 1, [])
check(st == 200 and d.get("styles") == [] and d.get("version") == 2, "C: none: the takeoff is empty again (v2)", d)
st, d = put(A, 2, [1, 3])
check(st == 200 and d.get("styles") == [1, 3], "C: ...and ticked again (v3)", d)

# ---- D. the export, the bid-sheet CSV and the report carry only what was added -----------------------------------
st, d = approve(A, {1: "WATER MAIN", 3: "STORM PIPE"})
check(st == 200, "D: approve with two styles added", (st, str(d)[:200]))
exp = json.loads((J / A / "p0_elements.json").read_text())
labs = {it["label"] for it in exp["items"]}
check(labs == {"WATER MAIN", "STORM PIPE"} and {e["label"] for e in exp["elements"]} == labs,
      "D: the export holds only the two added styles (no curb, no annotation)", labs)
check(exp.get(ADDED) == 1, "D: ...and says it holds what the person added (the mark every export of this build carries)",
      sorted(exp))
st, b = call("GET", f"/api/job/{A}/bidsheet.csv", "alice", raw=True)
csv = b.decode("utf-8-sig")
check(st == 200 and "WATER MAIN" in csv and "STORM PIPE" in csv and "C-CURB" not in csv and "C-ANNO" not in csv,
      "D: the bid-sheet CSV has the two added rows and nothing else of the sheet", csv[:300])
st, b = call("GET", f"/api/job/{A}/report.pdf", "alice", raw=True)
txt = ""
if st == 200:
    rp = pymupdf.open(stream=b, filetype="pdf")
    txt = " ".join(p.get_text() for p in rp)
check(st == 200 and "WATER MAIN" in txt and "STORM PIPE" in txt and "C-CURB" not in txt,
      "D: the report has the two added rows and not the curb", st)

# ---- I. a page with only a calculator row may be approved ---------------------------------------------------------
st, d = approve(N, {}, extra=[{"label": "Pavement (edge)", "qty": 120.0, "unit": "LF"}])
check(st == 200, "I: no style added but an Areas row with a quantity: the takeoff is not empty, approve goes", (st, d))
st, d = approve(N, {}, extra=[{"label": "Pavement (edge)", "qty": 0, "unit": "LF"}])
check(st == 409 and str(d.get("detail")).startswith(server.TAKEOFF_EMPTY),
      "I: ...a row with no quantity is not something", (st, d))

# ---- E. a shared sheet: each account its own; nobody sees another's; nothing brought over -------------------------
st, d = ticks(SH, "alice")
check(st == 200 and d.get("state") == "fresh" and d.get("styles") == [] and "may_migrate" not in d,
      "E: a shared sheet opens empty and brings nothing over", d)
st, d = put(SH, 0, [2], "alice")
check(st == 200 and d.get("styles") == [2], "E: alice adds the curb on the shared sheet", d)
st, d = ticks(SH, "bob")
check(st == 200 and d.get("styles") == [] and d.get("state") == "fresh", "E: bob opens it: EMPTY - none of alice's ticks", d)
st, d = put(SH, 0, [1], "bob")
check(st == 200 and d.get("styles") == [1] and ticks(SH, "alice")[1].get("styles") == [2],
      "E: bob's own tick is his; alice still has only hers", (d, ticks(SH, "alice")[1]))
st, d = put(SH, 1, [1, 3], "bob", migrate={"source": "browser"})
check(st == 409 and d.get("detail") == NO_MIG and ticks(SH, "bob")[1].get("styles") == [1],
      "E: a write that asks to bring ticks over is refused in words and changes nothing", (st, d))
st, d = call("GET", f"/api/job/{SH}/page/0/ticks", "alice")
check("by" not in json.dumps(d) and "bob" not in json.dumps(d), "E: the reply names no other account", d)
server._sweep_job_dir(SH)                       # a demo card's fresh open (and a reset)
check(not (J / SH / "p0_ticks.json").exists(), "E: a demo card's fresh open sweeps every account's ticks")
(J / SH / "p0_result.json").write_text(json.dumps(RES))
check(ticks(SH, "alice")[1].get("styles") == [] and ticks(SH, "bob")[1].get("styles") == [],
      "E: ...so the card opens empty for both")

# ---- H. a re-detect keeps the ticks whose style is still there, by key ---------------------------------------------
# page F: the person added the water main (1) and the curb (2)
st, d = put(F, 0, [1, 2])
check(st == 200 and d.get("styles") == [1, 2], "H: (the water main and the curb are added on this page)", (st, d))
# Detected again: every style renumbered, the curb gone, a new fence style
S_FNC = style(5, "dashed", "C-FENC", [0.5, 0.0, 0.5], 0.5, "d3.0 g3.0", 250.0)
(J / F / "p0_result.json").write_text(json.dumps(result([S_FNC, S_STM, S_WAT, S_ANN], ids={5: 1, 3: 2, 1: 3, 4: 4})))
st, d = ticks(F)
check(st == 200 and d.get("styles") == [3], "H: the water main is kept under its NEW id (3), found by its style key", d)
check(d.get("dropped") == 1, "H: the curb, no longer on the sheet, is dropped and counted", d)
check(1 not in d.get("styles") and 2 not in d.get("styles"),
      "H: never by position: id 1 (now the fence) and id 2 (now the storm) are NOT ticked - new styles start unticked", d)
st, d = put(F, d["version"], [3, 1])
check(st == 200 and d.get("styles") == [1, 3] and d.get("dropped") == 0, "H: the next tick writes the new ids", d)

# style keys: one per style on a page (the engine never emits a family twice)
keys = [server._style_key(s) for s in RES["styles"]]
check(len(set(keys)) == len(keys), "the style key tells every style of the page apart", keys)

print(f"\n{'PASS' if not fails else 'FAIL'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
