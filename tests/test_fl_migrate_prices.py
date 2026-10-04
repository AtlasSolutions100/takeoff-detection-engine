"""A 2.0 JOB'S FLOORING MONEY, MIGRATED AND RECONCILED (critic 4, M2, 2026-10-02).

Critic 4 built a 2.0 job: sheet 2's Carpet and LVP priced on their types, sheet 1's Tile priced ONLY in
2.0's takeoff panel at $6/SF (the browser's own price book; the approved export carried it). On
8116831 the migration never saw the Tile price ("Tile: rooms and no price"), and after sheet 2 was
re-approved in the new build the report's SUM OF APPROVED SHEETS fell from 5,184.70 to 3,231.60 with
nothing said, while sheet 1 still printed the 2.0 Tile money the estimate said did not exist.
Each block FAILS on 8116831:
  * the migration takes a flooring price from an approved export when the page record has none
  * the report's summary carries ONE "Flooring estimate" line with the estimate's total, added to the sum
  * a 2.0-priced flooring row stays printed as approved, marked, and is not added to the totals

    python tests/test_fl_migrate_prices.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="flmig_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="flmig_lg_")
os.environ["TAKEOFF_HOSTED"] = "1"
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

ALICE = "alice_synth"
J = "flm000000001"
d = pymupdf.open()
for _ in range(2):
    d.new_page(width=612, height=792)
pdf = _JOBS / "upload_00000000000a_two-sheets.pdf"
d.save(str(pdf))
d.close()
(_JOBS / J).mkdir()
RESULT = {"page": {"width_pt": 612, "height_pt": 792}, "styles": [{"style_id": 1, "class": "solid", "width": 1.0, "n": 4, "color": [0, 0, 0]}],
          "chains": [], "openings": [], "scale": {"ft_per_paper_inch": 4.0, "text": "1/4\" = 1'-0\""}}
BOX = [[100, 100], [388, 100], [388, 244], [100, 244]]          # 288 x 144 pt: 16 x 8 ft at 1/4" = 128 SF, 48 LF
TYPES20 = [{"name": "Carpet", "color": "#4e9af1", "sf_price": 4, "lf_price": 2, "waste_pct": 0},
           {"name": "LVP", "color": "#f2a33a", "sf_price": 3, "lf_price": 2, "waste_pct": 10},
           {"name": "Tile", "color": "#4fc27a", "sf_price": 0, "lf_price": 0, "waste_pct": 0}]


def room20(t):
    return {"name": "Room 1", "pts": BOX, "area_sf": 128.0, "area_pt2": 41472.0, "perim_lf": 48.0, "perim_pt": 864.0, "openings_lf": 0,
            "holes_sf": 0, "doors": 0, "closures": 0, "type": t, "source": "drawn", "bridges": [], "objects": [], "deductions": []}


def export(items):
    return {"source": "two-sheets.pdf", "pageIndex": 0, "imageDpi": 300, "pageWidthPt": 612.0, "pageHeightPt": 792.0, "elements": [],
            "scale": {"ftPerPaperInch": 4.0, "setBy": "engine"}, "items": items, "overrides": [], "notes": []}


for p, t, items in ((0, 2, [{"label": "solid solid", "count": 1, "trade": "General", "length_ft": 100.0, "unit_price": 2.0, "extended": 200.0},
                            {"label": "Flooring — Tile", "count": 1, "length_ft": None, "qty": 128.0, "unit": "SF", "unit_price": 6.0, "extended": 768.0},
                            {"label": "Base/molding — Tile", "count": 1, "length_ft": None, "qty": 48.0, "unit": "LF", "unit_price": None, "extended": None}]),
                    (1, 0, [{"label": "Flooring — Carpet", "count": 1, "length_ft": None, "qty": 128.0, "unit": "SF", "unit_price": 4.0, "extended": 512.0},
                            {"label": "Base/molding — Carpet", "count": 1, "length_ft": None, "qty": 48.0, "unit": "LF", "unit_price": 2.0, "extended": 96.0}])):
    (_JOBS / J / f"p{p}_result.json").write_text(json.dumps(RESULT))
    (_JOBS / J / f"p{p}_calcstate.json").write_text(json.dumps({"v": 1, "engine_sig": "s", "prices": {} if p == 0 else
                                                                {"Flooring — Carpet": 4, "Base/molding — Carpet": 2},
                                                                "fl": {"types": TYPES20, "rooms": [room20(t)], "closures": [], "seq": 2}}))
    (_JOBS / J / f"p{p}_elements.json").write_text(json.dumps(export(items)))
(_JOBS / "registry.json").write_text(json.dumps({J: {"path": str(pdf), "pages": 2, "mtime": round(pdf.stat().st_mtime, 2), "kinds": None,
                                                     "sizes": [[612, 792], [612, 792]], "owner": ALICE, "bid": None}}, indent=1))

import server  # noqa: E402
from takeoff_engine import users as _users  # noqa: E402

server._classify_async = lambda *a, **k: None
_users.save({ALICE: _users.hash_password("orchid-lantern-4471")})
server._UI_PASSWORD = None
CK = server._sign_session(ALICE)
fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:500]})"))
    if not ok:
        fails += 1


async def _asgi(method, path, body=b""):
    headers = [(b"host", b"testserver"), (b"x-core-client", b"3"), (b"content-type", b"application/json"),
               (b"content-length", str(len(body)).encode()), (b"cookie", f"{server.SESSION_COOKIE}={CK}".encode())]
    scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.3"}, "http_version": "1.1",
             "method": method, "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
             "root_path": "", "headers": headers, "client": ("127.0.0.1", 40000), "server": ("testserver", 80)}
    sent = {"b": False}
    out = {"status": None, "body": b""}

    async def receive():
        if not sent["b"]:
            sent["b"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(m):
        if m["type"] == "http.response.start":
            out["status"] = m["status"]
        elif m["type"] == "http.response.body":
            out["body"] += m.get("body", b"")

    await server.app(scope, receive, send)
    return out["status"], out["body"]


def call(method, path, body=None, raw=False):
    try:
        st, data = asyncio.run(_asgi(method, path, b"" if body is None else json.dumps(body).encode()))
    except Exception as e:          # noqa: BLE001
        return 500, {"detail": f"server error: {e}"}
    if raw:
        return st, data
    try:
        return st, json.loads(data)
    except ValueError:
        return st, data.decode("utf-8", "replace")


print("M2: the migration takes every price an earlier build kept")
st, g = call("GET", f"/api/job/{J}/flooring")
types = {t["name"]: t for t in g["est"]["types"]}
check(st == 200 and types["Tile"]["rate_cents"] == 600, "Tile, priced only in 2.0's takeoff panel ($6/SF, in the approved export): $6.00 carried", types["Tile"])
check(types["Carpet"]["rate_cents"] == 400 and types["Carpet"]["base_rate_cents"] == 200, "Carpet keeps its own $4 / $2")
check(sorted(t["name"] for t in g["est"]["types"]) == ["Carpet", "LVP", "Tile"],
      "2.0 kept a type list per sheet: the same name on both sheets is one floor type of the job", [t["name"] for t in g["est"]["types"]])
check(not any("Tile: rooms and no price" in c["text"] for c in g["computed"]["open"]), "no 'Tile: rooms and no price'", [c["text"] for c in g["computed"]["open"]])
st, ws = call("GET", f"/api/job/{J}/flooring_worksheet.csv", raw=True)
check(b"approved export" in ws, "the worksheet says where the Tile price came from", ws[-600:])
est_total = g["computed"]["total"]
check(est_total == "1,376.00", "the estimate: Tile 128 SF x 6 + Carpet 128 SF x 4 + 48 LF x 2 = 1,376.00", est_total)

print("M2: the report reconciles the flooring money into the sum")
st, rp = call("GET", f"/api/job/{J}/report.pdf", raw=True)
txt = "\n".join(p.get_text() for p in pymupdf.open(stream=rp, filetype="pdf")) if st == 200 else ""
summ = txt[txt.find("Takeoff summary"):] if "Takeoff summary" in txt else ""
summ = summ[:summ.find("Bid sheet")] if "Bid sheet" in summ else summ
check("Flooring estimate" in summ and "1,376.00" in summ and "flooring is priced on the flooring estimate" in summ,
      "the summary carries ONE line: Flooring estimate 1,376.00, with the note", summ[:900])
check("1,576.00" in summ, "SUM OF APPROVED SHEETS = 200.00 of the sheets' own rows + 1,376.00 of the estimate = 1,576.00 (2.0 printed 1,576.00)", summ[-500:])
check("priced in the previous version; now on the flooring estimate" in txt,
      "the 2.0 Tile and Carpet rows stay printed as approved, each marked: not added to the totals", txt[:2000])

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
