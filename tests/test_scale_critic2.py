"""CRITIC-scale-2's reproductions, as tests (out/verify/2026-10-02/scale-verdict/CRITIC-scale-2.md). Each failed on
d2c2ba7 and passes now.

B1  A page whose readings disagree was shown with no scale, then APPROVED, EXPORTED and REPORTED in feet at the
    engine's 4 (Belle Farms Structural A1.00, engine page index 0, FACTS A2 truth 8). Now: approve with nothing chosen
    -> 409 naming the choices, no export written, the report refuses; the same with the check not yet run and with the
    engine's number sent back as if on screen; every quantity caller sees no scale.
M1  One click turned an unchecked reading into "confirmed". Now `confirmed` needs the person's own measure, agreeing
    within 2 %; a click is `accepted`, and the export / bid sheet / report say "accepted by the reviewer without
    measuring" (it replaces "not confirmed", it does not vanish); a disagreeing measure kept as read is recorded and
    said.
M2  The dimension check could stay "checking" for good, and its route could 500. Now a check that cannot be made is
    an answer ("failed", 200), resolves to not-confirmed at most, and never lets two agreeing kinds read as confirmed.
Minors: 1 `picked` only for an exact reading the page says it picked; 2 a 2.0-build tab is told to reload; 3 a
malformed cached check is recomputed; 4 the cache is keyed by the detection it measured; 5 empty chains are not
"checked"; 9 the gate's human-confirmed rows need a sha256 and a JSON boolean.

Run:  python tests/test_scale_critic2.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
J = Path(tempfile.mkdtemp(prefix="scale_critic2_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(J)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="scale_critic2_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ["TAKEOFF_PLAN_ROOTS"] = str(J)
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("CUTFILL_OUT", None)

import pymupdf  # noqa: E402

BELLE = ROOT.parent / "TakeoffPro AI" / "Plans" / "Belle Farms Structural Plans.pdf"
for cand in (BELLE, Path("C:/Users/kobys/OneDrive/Desktop/past/p/Takeoff Automation/TakeoffPro AI/Plans/"
                         "Belle Farms Structural Plans.pdf")):
    if cand.exists():
        BELLE = cand
        break
FAILS = []


def check(cond, msg, got=None):
    print(("ok   " if cond else "FAIL ") + msg + ("" if cond or got is None else f"  [{str(got)[:300]}]"))
    if not cond:
        FAILS.append(msg)


def cap(v, t):
    return {"text": t, "ft_per_paper_inch": v, "near_scale_keyword": True, "count": 1,
            "governs_pt": None, "governs_pct": None, "sites": 1}


if not BELLE.exists():
    print("SKIP-REQUIRED: the shared Belle Farms Structural Plans PDF is not on this machine")
    sys.exit(1)
reg = {}
pdf_b = J / "upload_00000000000b_structural.pdf"
d = pymupdf.open(str(BELLE))
one = pymupdf.open()
one.insert_pdf(d, from_page=0, to_page=0)
one.save(str(pdf_b))
one.close()
d.close()
from takeoff_engine.detect_page import detect_page, engine_sig  # noqa: E402
RES_B = json.loads(json.dumps(detect_page(str(pdf_b), 0)))
RES_B["engine_sig"] = engine_sig()
# a blank page with one caption (the OCR example's shape: one reading), a line to measure, and a caption + bar page
BASE = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [{"style_id": 1}], "engine_sig": "c2",
        "chains": [{"chain_id": 1, "style_id": 1, "points": [[100, 100], [388, 100]], "length_pt": 288.0,
                    "class": "solid", "color": [0, 0, 0]}]}
R_ONE = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                          "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": None,
                          "agrees": None, "delta_pct": None, "pick": {"why": "single"}})
R_AGREE = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                            "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": 20.0,
                            "agrees": True, "delta_pct": 0.0, "pick": {"why": "single", "bar_source": "native-ticks"}})
# one caption that governs and a second printed caption (5): a pickable other reading
R_TWO = dict(BASE, scale={**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
                          "candidates": [cap(20.0, '1" = 20\''), cap(5.0, '1" = 5\'')], "bar_ft_per_paper_inch": None,
                          "agrees": None, "delta_pct": None, "pick": {"why": "governs"}})
JOBS = {"b" * 12: (pdf_b, RES_B)}
for jid, res in (("o" * 12, R_ONE), ("g" * 12, R_AGREE), ("e" * 12, dict(R_AGREE, chains=[])),
                 ("p" * 12, R_TWO)):
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    pdf = J / f"upload_{jid}_sheet.pdf"
    d.save(str(pdf))
    d.close()
    JOBS[jid] = (pdf, res)
for jid, (pdf, res) in JOBS.items():
    (J / jid).mkdir()
    (J / jid / "p0_result.json").write_text(json.dumps(res))
    reg[jid] = {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                "sizes": [[612, 792]], "owner": "alice", "bid": None}
(J / "registry.json").write_text(json.dumps(reg))

import server  # noqa: E402
# THE TAKEOFF STARTS EMPTY (2026-10-03): an approve with nothing in the takeoff is refused, so the approves here that
# are about the scale carry one counted row ("Test count", 1 EA - a count needs no scale).
from takeoff_engine import users as U  # noqa: E402

server._classify_async = lambda *a, **k: None
U.save({"alice": U.hash_password("quartz-meadow-5182")})
server._UI_PASSWORD = None
CK = {"alice": server._sign_session("alice")}
B, O, G, E, P = "b" * 12, "o" * 12, "g" * 12, "e" * 12, "p" * 12


async def _asgi(method, path, cookie, body=b"", marker=b"3"):
    headers = [(b"host", b"testserver"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), (b"cookie", f"{server.SESSION_COOKIE}={cookie}".encode())]
    if marker is not None:
        headers.append((b"x-core-client", marker))
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


def call(method, path, body=None, marker=b"3"):
    st, b = asyncio.run(_asgi(method, path, CK["alice"], json.dumps(body).encode() if body is not None else b"",
                              marker))
    try:
        return st, json.loads(b)
    except ValueError:
        return st, b


def scale(jid):
    st, d = call("GET", f"/api/job/{jid}/page/0/scale")
    assert st == 200, (st, d)
    return d


def record(jid):
    f = J / jid / "p0_scale.json"
    return json.loads(f.read_text()) if f.exists() else None


def confirm(jid, ft, how, measured=None):
    body = {"ft_per_paper_inch": ft, "how": how, "scale_seen": scale(jid)["stamp"]}
    if measured is not None:
        # the measure itself (CRITIC-scale-3 MINOR 5): two points one paper inch apart and the true length typed
        body["measure"] = {"points": [[100.0, 100.0], [172.0, 100.0]], "true_ft": measured}
    return call("POST", f"/api/job/{jid}/page/0/scale/confirm", body)


def forget(jid):
    for n in ("p0_scale.json", "p0_elements.json", "p0_dimcheck.json"):
        f = J / jid / n
        if f.exists():
            f.unlink()
    server._PICK_LAST.pop(str(J / jid / "p0_scale.json"), None)


def report_text(jid):
    st, b = asyncio.run(_asgi("GET", f"/api/job/{jid}/report.pdf", CK["alice"]))
    if st != 200:
        return st, ""
    doc = pymupdf.open(stream=b, filetype="pdf")
    t = "\n".join(p.get_text() for p in doc)
    doc.close()
    return st, t


def no_dims(jid):
    """A dimension check that RAN and found none on this sheet, cached for this detection."""
    res = json.loads((J / jid / "p0_result.json").read_text())
    (J / jid / "p0_dimcheck.json").write_text(json.dumps({
        "key": {"scale": 20.0, "engine_sig": res.get("engine_sig"),
                "detection": server._scale_detection_digest_of(res)},
        "check": {"verdict": "no-witnesses", "witnesses": []}}))


def words(jid, res):
    return server._scale_plain_words(server._scale_verdict_full(jid, 0, res,
                                                                server._page_scale(jid, 0, res, unhidden=True)))


def section(name, fn):
    try:
        fn()
    except Exception as e:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, f"{name}: raised {type(e).__name__}: {e}")


def takeoff(jid, labels):
    """As the page: the line styles about to be approved are ADDED to the page's takeoff first (it starts empty); the
    answer is the version of the takeoff record, which the approve names (the server holds the approve to it)."""
    tk = call("GET", f"/api/job/{jid}/page/0/ticks")[1]
    want = sorted(int(k) for k in (labels or {}))
    if want != tk.get("styles"):
        tk = call("POST", f"/api/job/{jid}/page/0/ticks", {"version": tk["version"], "styles": want})[1]
    return tk["version"]


def lengths_body(jid, scale_sent=None):
    styles = [s["style_id"] for s in RES_B["styles"]][:6]
    labels = {str(s): f"S{s}" for s in styles}
    return {"labels": labels, "image_dpi": 300, "scale": scale_sent,
            "scale_seen": scale(jid)["stamp"], "ticks_version": takeoff(jid, labels)}


# ================================================================ B1
def _b1():
    forget(B)
    check(not (J / B / "p0_dimcheck.json").exists(), "B1 set-up: Belle, the dimension check not run yet")
    st, d = call("POST", f"/api/job/{B}/page/0/approve", lengths_body(B))
    check(st == 409 and "disagree" in str(d) and "7.9" in str(d) and not (J / B / "p0_elements.json").exists(),
          "B1: approve with nothing chosen (scale: null), the check not yet run -> 409 naming the choices, no export",
          (st, str(d)[:240]))
    rs, _ = report_text(B)
    check(rs != 200, "B1: ...and the report refuses (no export to report)", rs)
    eng = RES_B["scale"]["ft_per_paper_inch"]
    st, d = call("POST", f"/api/job/{B}/page/0/approve",
                 lengths_body(B, {"ft_per_paper_inch": eng, "text": RES_B["scale"].get("text"),
                                  "picked_by_reviewer": False}))
    check(st == 409 and not (J / B / "p0_elements.json").exists(),
          f"B1: approve sending the engine's {eng} back as if on screen (not chosen) -> 409, no export", st)
    check(server._page_scale(B, 0, RES_B) is None,
          "B1: every quantity caller of _page_scale sees no scale on this page (footings, twins, trench, export)")
    check(scale(B)["in_force"] is None and scale(B)["verdict"]["state"] == "CONFLICT",
          "B1: GET .../scale serves nothing in force, verdict readings-disagree")
    # (the measured rows are taken out of the takeoff first, as the page's own note tells the person to: the export is
    # exactly the takeoff record, so an approve that leaves out a style the record holds is refused - critic empty-2, MINOR-2)
    st, d = call("POST", f"/api/job/{B}/page/0/approve", dict(lengths_body(B), labels={}, ticks_version=takeoff(B, {}),
                                                              extra_items=[{"label": "Test count", "qty": 1, "unit": "EA"}]))
    check(st == 200, "B1: a count-only approve still passes", st)


# ================================================================ M1
def _m1():
    forget(O)
    no_dims(O)
    v = scale(O)["verdict"]
    check(v["state"] == "UNCHECKED" and v["value"] == 20.0, "M1 set-up: one reading (the caption) at 20", v["why"])
    st, d = confirm(O, 20.0, "confirmed")
    check(st == 409 and record(O) is None and "measure" in str(d),
          "M1: a Confirm with no measure is refused (it was one click to 'confirmed')", (st, d))
    st, d = confirm(O, 20.0, "confirmed", measured=30.0)
    check(st == 409 and record(O) is None, "M1: a Confirm whose measure disagrees (30 vs 20) is refused", (st, d))
    st, d = confirm(O, 20.0, "accepted")
    rec = record(O)
    check(st == 200 and rec["how"] == "accepted" and "measured_ft" not in rec,
          "M1: Accept as read is stored `accepted`", rec)
    check(d["verdict"]["state"] == "UNCHECKED" and d["verdict"]["person"]["how"] == "accepted"
          and "without measuring" in d["verdict"]["why"],
          "M1: accepted is NOT confirmed: the verdict stays not-confirmed and says 'without measuring'", d["verdict"])
    st, d = call("POST", f"/api/job/{O}/page/0/approve", {"labels": {"1": "Pipe"}, "image_dpi": 300, "scale": None,
                                                          "scale_seen": scale(O)["stamp"],
                                                          "ticks_version": takeoff(O, {"1": "Pipe"})})
    check(server._scale_flag_words(O, [0]) == ["Scale accepted by the reviewer without measuring on page 1"],
          "M1: the bid sheet / readiness line: accepted replaces 'not confirmed', it does not vanish",
          server._scale_flag_words(O, [0]))
    exp = json.loads((J / O / "p0_elements.json").read_text())
    check(st == 200 and "accepted by the reviewer without measuring" in exp["scale"]["name"]
          and "confirmed" not in exp["scale"]["name"],
          "M1: the export's scale name says accepted without measuring, never confirmed", exp["scale"]["name"])
    check("accepted by the reviewer without measuring" in exp["scaleVerdict"]["words"]
          and "alice" not in json.dumps(exp), "M1: export scaleVerdict words, and no person's name in it",
          exp["scaleVerdict"])
    rs, rt = report_text(O)
    flat = " ".join(rt.split())
    check(rs == 200 and "ACCEPTED BY THE REVIEWER WITHOUT MEASURING" in flat and "CONFIRMED BY" not in flat
          and "accepted by the reviewer without measuring on page 1" in flat.replace("this page", "page 1")
          or ("ACCEPTED BY THE REVIEWER WITHOUT MEASURING" in flat and "without measuring on this page" in flat),
          "M1: the report says accepted without measuring", [ln for ln in rt.splitlines() if "cale" in ln])
    st, b = asyncio.run(_asgi("GET", f"/api/job/{O}/bidsheet.csv", CK["alice"]))
    bid = b.decode("utf-8-sig")
    check(st == 200 and "Scale accepted by the reviewer without measuring on page 1" in bid,
          "M1: the bid sheet CSV says it", bid.splitlines()[:6])
    # a measure that disagrees, kept as read: recorded beside it, said in the words
    st, d = confirm(O, 20.0, "accepted", measured=30.0)
    rec = record(O)
    check(st == 200 and rec["how"] == "accepted" and rec["measure_disagreed"]["ft"] == 30.0,
          "M1: Keep 20 after a measure said 30 stores accepted with the disagreeing measure beside it", rec)
    w = words(O, R_ONE)
    check("a measure on" in w and "said 1 in = 30 ft" in w, "M1: its words: accepted; a measure on <date> said 30", w)
    # a measure that agrees: confirmed, the queue and flags clear
    st, d = confirm(O, 20.0, "confirmed", measured=20.3)
    rec = record(O)
    check(st == 200 and rec["how"] == "confirmed" and rec["measured_ft"] == 20.3 and "measure_disagreed" not in rec,
          "M1: a Confirm with a measure within 2 % (20.3 vs 20) is stored confirmed, with the measure", rec)
    check(d["verdict"]["state"] == "CONFIRMED" and server._scale_flag_words(O, [0]) == [],
          "M1: confirmed by measuring: CONFIRMED, no flag", (d["verdict"]["state"], server._scale_flag_words(O, [0])))
    check("checked by the reviewer by measuring a known length" in words(O, R_ONE),
          "M1: its words: checked by the reviewer by measuring a known length", words(O, R_ONE))
    # the measured value must be a sane scale
    st, d = confirm(O, 20.0, "confirmed", measured=-5.0)
    check(st in (400, 409, 422), "M1: a nonsense measure is refused", st)


# ================================================================ M2
def _m2():
    forget(G)
    real = server._scale_dimcheck_compute
    server._scale_dimcheck_compute = lambda *a, **k: None
    try:
        v = server._scale_verdict_full(G, 0, R_AGREE, server._page_scale(G, 0, R_AGREE, unhidden=True))
    finally:
        server._scale_dimcheck_compute = real
    check(v is not None and v["state"] == "UNCHECKED" and v["dimensions"] == "failed"
          and "could not be checked" in v["why"],
          "M2: caption + bar agree but the check cannot be made -> not confirmed, 'could not be checked'", v)
    check(server._page_scale(G, 0, R_AGREE) is not None,
          "M2: ...the scale stays in force (not confirmed is not disagree)")
    # the route: an unreadable PDF -> an answer, not a 500
    forget(G)
    keep = server.JOBS[G]["path"]
    server.JOBS[G]["path"] = str(J / "gone.pdf")
    try:
        st, d = call("GET", f"/api/job/{G}/page/0/scale/dimensions")
    finally:
        server.JOBS[G]["path"] = keep
    check(st == 200 and d.get("verdict") == "failed" and d["scale_verdict"]["dimensions"] == "failed"
          and d["scale_verdict"]["state"] == "UNCHECKED",
          "M2: the dimension route answers a failure (200, verdict failed, scale verdict not confirmed)", (st, d))
    check(not (J / G / "p0_dimcheck.json").exists(), "M2: a failure is not cached: the next call tries again")
    # a check that ran and found nothing: 'none on this sheet', the other kinds decide
    no_dims(G)
    v = scale(G)["verdict"]
    check(v["state"] == "CONFIRMED" and v["dimensions"] == "checked",
          "M2: a check that RAN and found no dimensions: the other kinds decide (caption + bar agree)", v["why"])


# ================================================================ minors
def _minors():
    # MINOR 1: picked only for an exact reading the page says it picked
    forget(P)
    no_dims(P)
    for sent_how, ft, want in (("typed", 20.3, "typed"), ("calibrated", 19.9, "calibrated"),
                               ("picked", 5.05, "typed"), ("picked", 5.0, "picked")):
        forget(P)
        no_dims(P)
        st, d = call("POST", f"/api/job/{P}/page/0/approve",
                     {"labels": {}, "extra_items": [{"label": "Test count", "qty": 1, "unit": "EA"}], "scale": {"ft_per_paper_inch": ft, "text": "x", "picked_by_reviewer": True,
                                              "how": sent_how}, "scale_seen": scale(P)["stamp"]})
        check(st == 200 and (record(P) or {}).get("how") == want,
              f"MINOR 1: approve {ft} sent as {sent_how} is stored {want}", (st, record(P)))
    forget(P)
    no_dims(P)
    st, d = confirm(P, 20.3, "picked")
    check(st == 200 and record(P)["how"] == "typed", "MINOR 1: the confirm route never keeps picked for 20.3 (no reading)",
          record(P))
    # MINOR 2: a 2.0-build tab (marker 2) is told to reload, nothing written
    forget(P)
    st, d = call("POST", f"/api/job/{P}/page/0/scale/confirm",
                 {"ft_per_paper_inch": 20.0, "how": "accepted", "scale_seen": scale(P)["stamp"]}, marker=b"2")
    check(st == 409 and d.get("detail") == "CORE was updated. Reload this page to keep working." and record(P) is None,
          "MINOR 2: a write with the 2.0 marker -> 409 'CORE was updated. Reload this page to keep working.'", (st, d))
    js = (ROOT / "webui" / "client.js").read_text(encoding="utf-8")
    check('VALUE = "3"' in js and server.CLIENT_MIN == 3, "MINOR 2: client.js sends 3 and the server wants 3")
    # MINOR 3: a malformed cached check (witnesses not a list) is recomputed, so Belle's Confirm 4 is refused
    forget(B)
    (J / B / "p0_dimcheck.json").write_text(json.dumps({
        "key": {"scale": 4.0, "engine_sig": RES_B["engine_sig"], "detection": server._scale_detection_digest_of(RES_B)},
        "check": {"verdict": "agrees", "witnesses": "x"}}))
    st, d = confirm(B, 4.0, "accepted")
    check(st == 409 and record(B) is None and "disagree" in str(d),
          "MINOR 3: Belle with a malformed cached check: accept 4 -> 409 (the check was recomputed: disagree)", (st, d))
    # MINOR 4: a check cached for another detection (same engine_sig) is not used
    forget(G)
    (J / G / "p0_dimcheck.json").write_text(json.dumps({
        "key": {"scale": 20.0, "engine_sig": "c2", "detection": "another-detection"},
        "check": {"verdict": "no-witnesses", "witnesses": []}}))
    check(scale(G)["verdict"]["dimensions"] == "pending",
          "MINOR 4: a cached check keyed on another detection is not served as this page's")
    # MINOR 5: empty chains are never 'checked'
    forget(E)
    st, d = call("GET", f"/api/job/{E}/page/0/scale/dimensions")
    check(st == 200 and d["scale_verdict"]["dimensions"] == "failed" and d["scale_verdict"]["state"] != "CONFIRMED",
          "MINOR 5: a stored result with no chains: dimensions failed, not confirmed", (st, str(d)[:200]))
    st, d = confirm(E, 20.0, "confirmed")
    check(st == 409 and record(E) is None, "MINOR 5: ...and a Confirm without a measure is refused (it was 200)", st)
    # MINOR 9: the gate
    from takeoff_engine import scale_gate as G9
    t = Path(tempfile.mkdtemp(prefix="c2gate_")) / "t.json"
    good = "a" * 64
    t.write_text(json.dumps([{"document": "x.pdf", "page_index": 0, "ft_per_paper_inch": 20.0, "confirmed_by_human": True},
                             {"document": "y.pdf", "page_index": 0, "ft_per_paper_inch": 20.0, "sha256": good,
                              "confirmed_by_human": "true"},
                             {"document": "z.pdf", "page_index": 0, "ft_per_paper_inch": 20.0, "sha256": good,
                              "confirmed_by_human": True}]))
    rows = G9.load_truth(t, proposed=False)
    bad = {r["document"]: r["bad_truth"] for r in rows}
    check(bad.get("x.pdf") and "sha256" in bad["x.pdf"], "MINOR 9: a human-confirmed row with no sha256 is an ERROR", bad)
    check(bad.get("y.pdf") and "boolean" in bad["y.pdf"],
          "MINOR 9: confirmed_by_human as a string is an ERROR", bad)
    check(bad.get("z.pdf") is None, "MINOR 9: a proper row is fine", bad)


section("B1", _b1)
section("M1", _m1)
section("M2", _m2)
section("minors", _minors)
print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
