"""SCALE VERDICT S4 (the server enforces) AND S5 (the words travel).

S4  1 a page whose full verdict is "readings disagree" with no person's record has NO scale in force (`_page_scale`);
      every caller behaves as no-scale; the record and the engine's reading stay readable unhidden
    2 Approve on such a page, or on a page with no scale, is refused ONLY when it would export a measured quantity
      (a labelled line not counted as objects, a calculator row whose unit is not EA); counts pass; the refusal names
      the choices
    3 a page approved before the readings were found to disagree is NOT out of date for that alone; a person changing
      the scale after approval still is
    4 cut / fill: the hand-off's words follow the record's `how`; nothing is handed over, and the engine does not fall
      back to its own caption reader, on readings that disagree; the Scale card's write carries the record's stamp
S5  the plain words; export["scaleVerdict"] (machine-readable, no name); the bid sheet's flags, grouped; the report

Requests go through the real app (session cookies, the client gate, the job rule), in a throwaway store.
Run:  python tests/test_scale_s4s5.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="scale_s4s5_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="scale_s4s5_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

FAILS = []


def check(cond, msg, got=None):
    print(("ok   " if cond else "FAIL ") + msg + ("" if cond or got is None else f"  [{str(got)[:400]}]"))
    if not cond:
        FAILS.append(msg)


def cap(v, t):
    return {"text": t, "ft_per_paper_inch": v, "near_scale_keyword": True, "count": 1,
            "governs_pt": None, "governs_pct": None, "sites": 1}


CHAIN = {"chain_id": 1, "style_id": 1, "points": [[100, 100], [388, 100]], "length_pt": 288.0, "class": "solid",
         "color": [0, 0, 0]}
BASE = {"page": {"width_pt": 612, "height_pt": 792}, "chains": [CHAIN], "styles": [{"style_id": 1}],
        "engine_sig": "t1"}
# readings that disagree: caption 4 and bar 3.965 agree, the printed dimensions (check on file) say 7.93
R_DIS = dict(BASE, scale={**cap(4.0, '1/4" - 1\'-0"'), "source": "text", "has_nts": True,
                          "candidates": [cap(4.0, '1/4" - 1\'-0"'), cap(8.0, '1/8"=1\'-0"')],
                          "bar_ft_per_paper_inch": 3.965, "agrees": True, "delta_pct": 0.9,
                          "pick": {"why": "governs", "bar_source": "native-ticks"}})
DIM_DIS = {"verdict": "disagrees", "witnesses": [{}] * 7, "witness_consensus_n": 5,
           "witness_consensus_ft_per_in": 7.93}
# one reading
R_ONE = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                          "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": None,
                          "agrees": None, "delta_pct": None, "pick": {"why": "single"}})
# caption and bar agree
R_AGREE = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                            "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": 20.0,
                            "agrees": True, "delta_pct": 0.0, "pick": {"why": "single", "bar_source": "native-ticks"}})
R_NONE = dict(BASE, scale=None)
NO_DIM = {"verdict": "no-witnesses", "witnesses": []}

reg = {}
JOB = {}
for i, (k, res, dim) in enumerate((("dis", R_DIS, DIM_DIS), ("one", R_ONE, NO_DIM), ("agree", R_AGREE, NO_DIM),
                                    ("none", R_NONE, NO_DIM))):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / ("upload_%012x_%s.pdf" % (i + 1, k))
    d.save(str(pdf))
    d.close()
    jid = (k[0] * 12)[:12] if k != "agree" else "g" * 12
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(res))
    (J / jid / "p0_dimcheck.json").write_text(json.dumps({"key": {"scale": None, "engine_sig": "t1"}, "check": dim}))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": "alice", "bid": None}
    JOB[k] = jid
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402


def _key_caches():
    """Every cached dimension check in this store, keyed on its page's detection as the server keys it
    (CRITIC-scale-2 MINOR 4)."""
    for f in J.glob("*/p*_dimcheck.json"):
        c = json.loads(f.read_text())
        r = json.loads((f.parent / f.name.replace("_dimcheck", "_result")).read_text())
        c["key"]["detection"] = server._scale_detection_digest_of(r)
        f.write_text(json.dumps(c))


_key_caches()
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("orchid-lantern-4471")})
server._UI_PASSWORD = None
CK = {"alice": server._sign_session("alice")}
D, O, G, N = JOB["dis"], JOB["one"], JOB["agree"], JOB["none"]


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


def call(method, path, body=None):
    st, b = asyncio.run(_asgi(method, path, CK["alice"], json.dumps(body).encode() if body is not None else b""))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def res_of(jid):
    return json.loads((J / jid / "p0_result.json").read_text())


def stamp(jid):
    return call("GET", f"/api/job/{jid}/page/0/scale")[1]["stamp"]


def takeoff(jid, labels):
    """As the page: the line styles about to be approved are ADDED to the page's takeoff first (it starts empty); the
    answer is the version of the takeoff record, which the approve names (the server holds the approve to it)."""
    tk = call("GET", f"/api/job/{jid}/page/0/ticks")[1]
    want = sorted(int(k) for k in (labels or {}))
    if want != tk.get("styles"):
        tk = call("POST", f"/api/job/{jid}/page/0/ticks", {"version": tk["version"], "styles": want})[1]
    return tk["version"]


def approve(jid, labels=None, counts=None, extra=None, scale=None):
    return call("POST", f"/api/job/{jid}/page/0/approve",
                {"labels": labels or {}, "object_counts": counts or {}, "extra_items": extra or [],
                 "scale": scale, "scale_seen": stamp(jid), "ticks_version": takeoff(jid, labels)})


# ================================================================ S4 rule 1
check(server._page_scale(D, 0, res_of(D)) is None,
      "1: readings that disagree, no person's record: _page_scale gives no scale in force")
check((server._page_scale(D, 0, res_of(D), unhidden=True) or {}).get("ft_per_paper_inch") == 4.0,
      "1: ...the engine's reading is still readable unhidden (the record's bookkeeping)")
g = call("GET", f"/api/job/{D}/page/0/scale")[1]
check(g["in_force"] is None and g["verdict"]["state"] == "CONFLICT", "1: GET .../scale: nothing in force", g["in_force"])
st, d = call("GET", f"/api/job/{D}/page/0/footings")
check("no scale" in json.dumps(d), "1: a quantity route behaves as on a page with no scale (footings)", (st, d))
for jid, ft in ((O, 20.0), (G, 20.0)):
    check((server._page_scale(jid, 0, res_of(jid)) or {}).get("ft_per_paper_inch") == ft,
          f"1: a one-reading / agreeing page keeps its scale ({ft})")

# ================================================================ S4 rule 2
st, d = approve(D, labels={"1": "Wall"})
check(st == 409 and "readings disagree" in str(d) and '1" = 4 ft (caption, drawn bar)' in str(d)
      and '1" = 7.93 ft (printed dimensions)' in str(d) and "Wall" in str(d)
      and not (J / D / "p0_elements.json").exists(),
      "2: an approve with a measured line on readings that disagree is refused, naming the choices; nothing written",
      (st, d))
st, d = approve(D, labels={"1": "Bollard"}, counts={"Bollard": 3})
check(st == 200 and (J / D / "p0_elements.json").exists(), "2: the same line counted as objects (EA) approves", (st, d))
st, d = approve(D, extra=[{"label": "Doors", "qty": 4, "unit": "EA"}])
check(st == 200, "2: a calculator row in EA approves without a scale", (st, d))
for unit in ("SF", "CY", "LF"):
    st, d = approve(D, extra=[{"label": f"Row {unit}", "qty": 10, "unit": unit}])
    check(st == 409 and f"Row {unit}" in str(d), f"2: a calculator row in {unit} needs a scale: refused", (st, d))
st, d = approve(N, labels={"1": "Curb"})
check(st == 409 and "has no scale" in str(d), "2: no scale at all, with a measured line: refused in its own words",
      (st, d))
st, d = approve(N, labels={"1": "Bollard"}, counts={"Bollard": 2})
check(st == 200, "2: no scale, counts only: approves", (st, d))
st, d = approve(O, labels={"1": "Curb"})
check(st == 200, "2: a one-reading page approves (flagged, not refused)", (st, d))

# ================================================================ S5: the export, the words
exp = json.loads((J / O / "p0_elements.json").read_text())
sv = exp.get("scaleVerdict") or {}
check(sv.get("state") == "UNCHECKED" and sv.get("words") == "1 in = 20 ft, not confirmed (one reading: the caption)"
      and exp["scale"].get("state") == "UNCHECKED" and sv.get("agree")[0]["kind"] == "caption",
      "S5: export.scaleVerdict carries the state, the words and the witnesses; export.scale its state", sv)
check("alice" not in json.dumps(exp), "S5: no person's name anywhere in the export")
for v, words in (({"state": "CONFIRMED", "value": 20, "agree": [{"kind": "caption"}, {"kind": "bar"}]},
                  "1 in = 20 ft, caption and drawn bar agree"),
                 ({"state": "UNCHECKED", "value": 20, "agree": [{"kind": "caption"}], "dimensions": "checked"},
                  "1 in = 20 ft, not confirmed (one reading: the caption)"),
                 ({"state": "CONFIRMED", "value": 20, "person": {"value": 20, "how": "calibrated",
                                                                 "when": "2026-10-02T12:00:00"}},
                  "1 in = 20 ft, set by measuring a known length on 2026-10-02"),
                 ({"state": "NONE"}, "no scale set: the engine read none")):
    check(server._scale_plain_words(v) == words, f"S5 words: {words}", server._scale_plain_words(v))
check("confirmed" not in server._scale_plain_words({"state": "UNCHECKED", "value": 20, "agree": [{"kind": "bar"}]})
      .replace("not confirmed", ""), "S5: one reading is never called confirmed")

# ================================================================ S4 rule 3 (+ S5 flags)
# a page approved at 4 "before" (an export written as the 2.0 code writes it: no scaleVerdict, pixelsPerUnit 300/4)
old = {"source": "x", "pageIndex": 0, "imageDpi": 300, "pageWidthPt": 612, "pageHeightPt": 792, "elements": [],
       "scale": {"pixelsPerUnit": 75.0, "unit": "ft", "name": '1/4" - 1\'-0" (auto, text)'},
       "items": [{"label": "Wall", "count": 1, "length_ft": 16.0, "unit_price": None, "extended": None}]}
(J / D / "p0_elements.json").write_text(json.dumps(old))
check(server._export_outdated(D, 0, old) is None,
      "3: approved at 4 before the readings were found to disagree: NOT out of date for that alone")
check(server._scale_flag_words(D, [0]) == ["Scale readings disagree on page 1: check it"],
      "S5: ...and flagged: 'Scale readings disagree on page 1: check it'", server._scale_flag_words(D, [0]))
st, csv = call("GET", f"/api/job/{D}/bidsheet.csv")
csv = csv.decode("utf-8-sig") if isinstance(csv, bytes) else json.dumps(csv)
check(st == 200 and "Scale readings disagree on page 1" in csv and "OUT OF DATE" not in csv,
      "S5: the bid sheet's header says it, and no OUT OF DATE", csv[:400])
# a person changes the scale after the approval: OUT OF DATE, as today
st, d = call("POST", f"/api/job/{D}/page/0/scale/confirm",
             {"ft_per_paper_inch": 7.93, "how": "picked", "scale_seen": stamp(D)})
why = server._export_outdated(D, 0, old) or ""
check(st == 200 and why.startswith("OUT OF DATE") and "1\" = 4 ft" in why and "7.93" in why,
      "3: a person changing the scale after approval: OUT OF DATE, as today", (st, why))
check(server._scale_flag_words(D, [0]) == [], "S5: a person's record: no scale flag")
# grouping: two one-reading pages
for p in (1, 2):
    (J / O / f"p{p}_result.json").write_text(json.dumps(R_ONE))
    (J / O / f"p{p}_elements.json").write_text(json.dumps(dict(old, scale={"pixelsPerUnit": 15.0, "unit": "ft",
                                                                          "name": "x"})))
    (J / O / f"p{p}_dimcheck.json").write_text(json.dumps({"key": {"scale": None, "engine_sig": "t1"}, "check": NO_DIM}))
_key_caches()
check(server._scale_flag_words(O, [0, 1, 2]) == ["Scale not confirmed on pages 1, 2, 3"],
      "S5: one line per kind, pages grouped", server._scale_flag_words(O, [0, 1, 2]))
check(server._scale_flag_words(G, [0]) == [], "S5: an agreeing page: no flag")
lines = server._scale_report_state_lines(O, 0, json.loads((J / O / "p0_elements.json").read_text()))
check(lines == ["Scale check: not confirmed (one reading: the caption)", "Scale not confirmed on this page"],
      "S5: the report's scale lines carry the same words", lines)

# ================================================================ S4 rule 4
H = server._cutfill_scale_handoff
check(H(None, None, {"ft_per_paper_inch": 20.0, "set_by": "reviewer", "set_how": "confirmed"})[1] == "confirmed in review"
      and H(None, None, {"ft_per_paper_inch": 20.0, "set_by": "reviewer", "set_how": "typed"})[1] == "set in review"
      and H(None, None, {"ft_per_paper_inch": 20.0, "set_by": "auto"}, {"state": "CONFIRMED"})[1] == "read in review"
      and H(None, None, {"ft_per_paper_inch": 20.0, "set_by": "auto"}, {"state": "UNCHECKED"})[1]
      == "read in review, not confirmed",
      "4: the hand-off's words follow the record's how and the verdict")
check(H(None, None, None) is None, "4: nothing in force (readings that disagree): nothing handed over")
from cutfill import placement as cfpl  # noqa: E402
fake = type("P", (), {})()
server._cf_sheet_job_page = (lambda project, key, write=False: {"kD": (D, 0), "kG": (G, 0)}.get(key))
(J / D / "p0_scale.json").unlink()
server._PICK_LAST.pop(str(J / D / "p0_scale.json"), None)
check(server._cf_review_conflict(fake, "kD") == "This sheet's scale readings disagree. Choose one on the sheet."
      and server._cf_review_conflict(fake, "kG") is None,
      "4: the cut / fill is told, in the words, that the sheet's readings disagree")
check(cfpl.review_conflict(fake, "kD") == server._CF_CONFLICT_WORDS, "4: placement.review_conflict answers it")
tok = server._CF_CALLER.set(("alice", False))
t2 = cfpl.REVIEW_SET_SEEN.set("sstale")
try:
    try:
        server._cf_review_scale_set(fake, "kG", 30.0, '1" = 30\'')
        refused = None
    except server.HTTPException as e:
        refused = e
finally:
    cfpl.REVIEW_SET_SEEN.reset(t2)
check(refused is not None and refused.status_code == 409 and not (J / G / "p0_scale.json").exists(),
      "4: a Scale card write with a stale stamp is refused, nothing written", refused and refused.detail)
t2 = cfpl.REVIEW_SET_SEEN.set(stamp(G))
try:
    ok = server._cf_review_scale_set(fake, "kG", 30.0, '1" = 30\'')
finally:
    cfpl.REVIEW_SET_SEEN.reset(t2)
    server._CF_CALLER.reset(tok)
check(ok is True and json.loads((J / G / "p0_scale.json").read_text())["ft_per_paper_inch"] == 30.0,
      "4: with the current stamp the card writes")
check(cfpl.HANDED == server._CF_HANDED and "set in review" in cfpl.HANDED
      and "read in review, not confirmed" in cfpl.HANDED, "4: the engine knows the new hand-off words as handed")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
