"""THE FLOORING JOB MODEL (takeoff_engine/fl_job.py + fl_rooms.py; rulings H-K, P-W, 2026-10-02).

Synthetic jobs only (round numbers); nothing from a client. What is held:
  * money in whole cents, the amount = the PRINTED quantity x the rate in decimal arithmetic, half up;
    200,000 random quantity x rate cases against an all-integer reference: zero cent errors;
  * one quantity, one string: the line's qty is the string printed everywhere;
  * a unit change rounds the rate to whole cents, and a round trip returns the rate as typed (critic 3, m4);
  * a comma is a thousands separator only in a well-formed group: "49,75" is refused (critic 3, m3);
  * packs round UP to whole packs and print "(N Boxes)"; a base carton the same;
  * removal / prep and the typed lines exist ONCE per job, whatever the number of sheets;
  * every figure is computed from the stored geometry at the room's scale (ruling P);
  * a check closes by its data or by its own acknowledgement act with the digest of what was seen - never
    by a flag (ruling R), and re-scaling, reshaping or re-detecting opens it again;
  * the customer CSV carries Description, Qty, Rate, Amount, Total and the notes - nothing of ours;
  * the page's typed money is parsed to the same cents as the server's (webui flCents vs fl_job.cents).

    python tests/test_fl_job.py
"""
import json
import random
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from takeoff_engine import fl_job as J  # noqa: E402
from takeoff_engine import fl_rooms as FR  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {got!r})"))
    if not ok:
        fails += 1


FT = 4.0                       # 1/4" = 1'-0": 18 pt per foot
PPF = 72.0 / FT


def room(uid, net_sf, base_lf=0.0, tid="t1", **kw):
    """A room whose net floor is `net_sf` and whose base is `base_lf` at 1/4"; no openings."""
    side = (net_sf ** 0.5) * PPF
    r = {"uid": uid, "name": uid, "source": "drawn", "pts": [[0, 0], [side, 0], [side, side], [0, side]],
         "area_pt2": net_sf * PPF * PPF, "perim_pt": base_lf * PPF, "openings": [], "type": tid,
         "scale": {"how": "sheet", "ft": FT}}
    r.update(kw)
    return r


def doc(est, pages):
    d = J.default_doc()
    d["est"] = est
    for p, rooms in pages.items():
        J.sheet(d, p)["rooms"] = rooms
    return J.clean_doc(d)


print("money: cents, the printed quantity, decimal half up")
check(J.cents("49.75") == 4975 and J.cents(49.75) == 4975 and J.cents("$1,080") == 108000 and J.cents("12,345.50") == 1234550,
      "typed money -> whole cents; a thousands group is read")
check(J.cents("49,75") is None and J.cents("1,08") is None and J.cents(",5") is None,
      "a comma that is not a thousands group is refused - never $4,975 for 49,75 (critic 3, minor 3)")
check(J.cents("0") is None and J.cents("") is None and J.cents(None) is None and J.cents("-3") is None and J.cents(0.0) is None,
      "blank, 0 and negative are NO price")
check(J.cents("5.555") == 556 and J.cents("5.554") == 555, "past the cent: half up")
check(J.cents("10000000.01") is None, "a rate over $10,000,000 is a typing slip: refused")
check(J.amount_cents("1.5", 35) == 53, "1.5 x 0.35 = 0.53 (binary half-up gave 0.52)")
check(J.amount_cents("1817.3", 7355) == 13366242, "1,817.3 x 73.55 = 133,662.42")
check(J.money(955200) == "9,552.00" and J.money(5) == "0.05" and J.money(None) == "", "cents print as the document prints them")
check(J.qty_str(Decimal("1575"), 0) == "1,575" and J.qty_str(Decimal("2.25"), 2) == "2.25" and J.qty_str(Decimal("2.50"), 2) == "2.5",
      "one formatter per kind: whole units with commas, typed quantities as typed")
rnd = random.Random(20261002)
bad = 0
for _ in range(200000):
    qh = rnd.randint(1, 10_000_000)
    rate = rnd.randint(1, 1_000_000)
    qs = format((Decimal(qh) / 100).normalize(), "f")
    if J.amount_cents(qs, rate) != (qh * rate + 50) // 100:
        bad += 1
check(bad == 0, "fuzz: 200,000 quantity x rate cases, zero cent errors against an all-integer reference", bad)

print("unit change: whole cents, and a round trip returns the rate as typed")
d0 = J.default_doc()
J.apply_act(d0, {"kind": "type_add", "name": "CPT-1"}, "me", "now")
t0 = d0["est"]["types"][0]
J.apply_act(d0, {"kind": "type_set", "id": t0["id"], "field": "unit", "value": "SY"}, "me", "now")
J.apply_act(d0, {"kind": "type_set", "id": t0["id"], "field": "rate", "value": "49.75"}, "me", "now")
J.apply_act(d0, {"kind": "type_set", "id": t0["id"], "field": "unit", "value": "SF"}, "me", "now")
sf = t0["rate_cents"]
J.apply_act(d0, {"kind": "type_set", "id": t0["id"], "field": "unit", "value": "SY"}, "me", "now")
check(sf == 553 and t0["rate_cents"] == 4975, "$49.75/SY -> $5.53/SF -> back to $49.75/SY, not $49.77", (sf, t0["rate_cents"]))

print("lines: one per type, packs, base cartons, area lines once per job, typed lines once")
T = {"id": "t1", "name": "CPT-1", "unit": "SY", "rate_cents": 4975, "waste_pct": "7",
     "pack": {"size": "48", "unit": "SF", "word": "Boxes"}, "base_rate_cents": 300, "base_code": "WB-1",
     "base_pack": {"size": "120", "unit": "LF", "word": "Cartons"}}
EST = {"types": [T], "removal": {"on": True, "desc": "Removal", "rate_cents": 75},
       "prep": {"on": True, "desc": "Prep", "rate_cents": 50},
       "lines": [{"id": "a", "desc": "Adhesive", "qty": "1", "rate_cents": 30000},
                 {"id": "f", "desc": "Freight", "qty": "1", "rate_cents": 35000}]}
D2 = doc(EST, {0: [room("a", 1000, 200)], 1: [room("b", 575, 125.5)]})
c = J.compute(D2, {0: "s", 1: "s"})
L = {l["id"]: l for l in c["lines"]}
check(L["floor:t1"]["qty"] == "192" and "(36 Boxes)" in L["floor:t1"]["desc"] and L["floor:t1"]["amount"] == "9,552.00",
      "1,575 SF net = 175 SY, +7 % = 187.25 SY -> 36 boxes of 48 SF = 192 SY = $9,552.00 (computed from the outlines)", L["floor:t1"])
check(L["base:t1"]["qty"] == "360" and "(3 Cartons)" in L["base:t1"]["desc"], "325.5 LF -> 3 cartons of 120 = 360 l/f", L["base:t1"]["desc"])
check(L["removal"]["qty"] == "1,575" and L["prep"]["qty"] == "1,575", "removal and prep: the net area of EVERY sheet's typed rooms, once")
check(sum(1 for l in c["lines"] if l["desc"] == "Freight") == 1, "a typed line exists once per job, however many sheets")
check(c["total_cents"] == sum(l["amount_cents"] for l in c["lines"]) and c["total"] == J.money(c["total_cents"]), "total = the sum of the printed amounts")
check(all(J.amount_cents(l["qty"], J.cents(l["rate"])) == l["amount_cents"] for l in c["lines"] if l["amount_cents"] is not None and l["rate"]),
      "every line: printed qty x printed rate = printed amount")

print("the figures are the server's: from the outline at the room's scale")
r = room("x", 100, 40, openings=[{"x": 0, "y": 1, "w_pt": 0, "w_ft": 3, "by": "added by hand"}])
f = FR.figures(J.clean_room(r), FT / 72)
check(abs(f["net_sf"] - 100) < 1e-6 and abs(f["base_lf"] - 37) < 1e-6 and f["base"]["openings"][0]["slid"],
      "100 SF; base 40 LF - a 3'-0\" opening clicked 1 pt from a corner = 37 LF (moved along its edge to fit)", (f["net_sf"], f["base_lf"]))
f2 = FR.figures(J.clean_room(dict(r, scale={"how": "chosen", "ft": 8.0})), 8.0 / 72)
check(abs(f2["net_sf"] - 400) < 1e-6, "the same outline at 1/8\": 400 SF (by the square of the scale)")
r3 = J.clean_room(room("o", 100, 40, objects=[{"kind": "tub", "area_pt2": 10 * PPF * PPF, "deduct": True, "pts": []}],
                       deductions=[{"name": "hearth", "area_pt2": 5 * PPF * PPF, "pts": []}]))
check(abs(FR.figures(r3, FT / 72)["net_sf"] - 85) < 1e-6, "a tub (10 SF) and a hearth clicked out (5 SF) come out of the net: 85 SF")

print("checks close by acts with the digest of what was seen, never by a flag")
e3 = json.loads(json.dumps(EST))
e3["types"][0]["rate_cents"] = None
e3["types"][0]["waste_pct"] = None
D3 = doc(e3, {0: [room("a", 1000, 200)]})
c3 = J.compute(D3, {0: "s"})
check(sorted(x["kind"] for x in c3["open"]) == ["openings", "price", "waste"], "no price, no waste, openings not reviewed: three open", [x["kind"] for x in c3["open"]])
check(not hasattr(J, "clean") and "acks" not in J.clean_est({"acks": {"price:t1": {"what": "x"}}}), "the estimate keeps no acknowledgement flags at all")
J.apply_act(D3, {"kind": "type_set", "id": "t1", "field": "no_charge", "value": True}, "me", "now")
J.apply_act(D3, {"kind": "type_set", "id": "t1", "field": "waste", "value": "0"}, "me", "now")
c3 = J.compute(D3, {0: "s"})
check([x["kind"] for x in c3["open"]] == ["openings"], "marked no charge and waste 0 on purpose: those two close by their data", [x["kind"] for x in c3["open"]])
op = c3["open"][0]
try:
    J.apply_act(D3, {"kind": "openings_ack", "page": 0, "uid": "a", "count": 0, "digest": "not-it"}, "me", "now", {0: "s"})
    check(False, "a stale digest is refused")
except J.ActError as e:
    check("changed since you looked" in str(e), "an acknowledgement of something the estimator did not see (stale digest) is refused", str(e))
J.apply_act(D3, {"kind": "openings_ack", "page": 0, "uid": "a", "count": 0, "digest": op["digest"]}, "me", "now", {0: "s"})
check(not J.compute(D3, {0: "s"})["open"], "acknowledged with the count it has and the digest: none open")
check(J.compute(D3, {0: "s2"})["open"], "the sheet detected again: its openings check is open again")
D4 = doc(EST, {0: [room("m", 1000, 200, scale={"how": "view", "ft": 4.0, "fpi": 4.0, "text": "1/4", "multi": True,
                                                "choices": [{"fpi": 4.0, "text": "1/4"}, {"fpi": 2.0, "text": "1/2"}]})]})
sc = [x for x in J.compute(D4, {0: "s"})["checks"] if x["kind"] == "scale"][0]
check(sc["open"] and [(o["text"], o["net_sf"]) for o in sc["options"]] == [("1/4", 1000.0), ("1/2", 250.0)],
      "a room on a multi-scale sheet: its scale check offers each printed scale with its area side by side", sc["options"])
J.apply_act(D4, {"kind": "scale_ack", "page": 0, "uid": "m", "fpi": 4.0, "digest": sc["digest"]}, "me", "now", {0: "s"})
check(not [x for x in J.compute(D4, {0: "s"})["checks"] if x["kind"] == "scale" and x["open"]], "acknowledged as 1/4 with its digest: closed")
J.apply_act(D4, {"kind": "room_put", "page": 0, "room": dict(J.find_room(D4, 0, "m"), pts=[[0, 0], [600, 0], [600, 600], [0, 600]])}, "me", "now")
check([x for x in J.compute(D4, {0: "s"})["checks"] if x["kind"] == "scale" and x["open"]], "the outline reshaped: its scale check is open again")

print("the customer document and the worksheet")
csvtxt = J.customer_csv(c)
check(csvtxt.splitlines()[0].lstrip("﻿") == "Description,Qty,Rate,Amount" and "Total" in csvtxt
      and not any(w in csvtxt for w in ("waste", "net ", "Automated", "CORE", "How the quantity", "rounded")),
      "customer CSV: the five columns and the total, none of our notes", csvtxt[:200])
ws = J.worksheet_csv(c, D2, "job")
check("not for the customer" in ws and "OPEN" in ws and "sheet 1: a" in ws and "1000.00 SF net" in ws,
      "worksheet: per line and per room, and every check with its state")

print("clean: bounds")
cl = J.clean_est({"types": [{"id": "x", "name": "N" * 500, "rate_cents": 10 ** 15, "waste_pct": -50},
                            {"id": "y", "rate_cents": True, "waste_pct": 150}],
                  "lines": [{"id": "l", "desc": "big", "qty": 1e12, "rate_cents": 5}, {"id": "m", "desc": "neg", "qty": -2, "rate_cents": -500},
                            {"id": "n", "desc": "frac", "qty": "2.255", "rate_cents": 100}]})
check(len(cl["types"][0]["name"]) == 40 and cl["types"][0]["rate_cents"] is None and cl["types"][0]["waste_pct"] is None
      and cl["types"][1]["rate_cents"] is None and cl["types"][1]["waste_pct"] is None,
      "a name is cut at 40; a rate of 10^15 cents, a boolean rate, a waste of -50 % or 150 % are refused")
check([l["qty"] for l in cl["lines"]] == [None, None, None] and cl["lines"][1]["rate_cents"] is None,
      "a quantity of 10^12, a negative one, one with 3 decimals; a negative rate: refused")
w = J.clean_est({"walls": {"solid|0.72|0,0,0": "wall", "dashed|0.1|0,0,0": "not", "x": "maybe", "y" * 200: "wall", 5: "wall"}})["walls"]
check(w == {"solid|0.72|0,0,0": "wall", "dashed|0.1|0,0,0": "not"}, "wall ticks by line-type signature are kept: wall / not only, bounded keys", w)
check(J.default_est()["walls"] == {} and J.clean_est(None)["walls"] == {}, "a new estimate has no wall ticks")
dd = J.clean_doc({"sheets": {"0": {"rooms": [dict(room("a", 10), type="ghost")]}}})
check(dd["sheets"]["0"]["rooms"][0]["type"] is None, "a room's type that is not in the estimate is no type")

print("the page's typed money: the same cents as the server's (webui flCents)")
src = (ROOT / "webui" / "app.js").read_text(encoding="utf-8")
i = src.index("function flCents(")
j = src.index("\n}\n", i) + 3
cases = ["49.75", "5.555", "5.554", "0", "", "0.004", "0.005", "1,080", "12,345.50", "49,75", "$3", "12.", ".5", "abc", "1e3", "-1",
         "999999999.99", "1234567890"]
node = subprocess.run(["node", "-e", src[i:j] + f"\nconsole.log(JSON.stringify({json.dumps(cases)}.map(flCents)));"],
                      capture_output=True, text=True)
got = json.loads(node.stdout.strip() or "[]")
ref = [J.cents(x) if x not in ("1e3",) else None for x in cases]
check(got == ref, "flCents = fl_job.cents on " + ", ".join(cases), (got, ref))

print("\nall passed" if not fails else f"\n{fails} FAILED")
sys.exit(1 if fails else 0)
