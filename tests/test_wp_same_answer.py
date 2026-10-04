"""THE WALL PANEL TOTAL AGAINST THE SAME FORMULA IN BINARY FLOATING POINT (2026-10-03).

An installer's earlier takeoff sheet computed this estimate in a browser, in floating point, with no
rounding until the total was shown. CORE computes it in Decimal: each printed line half up to the
cent, the total the sum of the printed lines. For the same inputs the two must give the same total to
the cent - and where they can not, the difference is one cent, it has a reason, and CORE's is the
total that equals the lines it prints. The settings and every input here are made up.

  * 24 input sets where every line is whole cents (edges included: hours exactly at the overtime
    threshold, the sheet count exactly at the profit threshold and one either side, a price a square
    foot, a typed profit, zero days): CORE equals the float formula, by both of the roundings a
    browser page shows a total with
  * 3 input sets where a line carries a part of a cent: the float total is one cent away from the sum
    of the lines as they print; CORE's total is that sum

    python tests/test_wp_same_answer.py
"""
import math
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from takeoff_engine import wp_job as W  # noqa: E402
from takeoff_engine import wp_settings as S  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:300]})"))
    if not ok:
        fails += 1


RAW = {"overtime_after_hours": 9, "overtime_rate": "30.00", "travel_per_mile": "11.00", "hotel_nightly": "310.00",
       "profit_flat": "700.00", "profit_per_day": "950.00", "profit_flat_under_sheets": 6, "sizes": ["4x8", "4x10", "5x10"]}
REC, BAD = S.clean(RAW)
K = {k: float(REC[k]) for k in ("overtime_after_hours", "overtime_rate", "travel_per_mile", "hotel_nightly", "profit_flat",
                                "profit_per_day", "profit_flat_under_sheets")}


def js_round(x: float) -> int:
    f = math.floor(x)
    return f + 1 if x - f >= 0.5 else f


def in_floats(i: dict) -> tuple:
    """The formula as a browser evaluates it: doubles, left to right, no rounding until the end.
    -> (the total as the screen formats it, the total as round(total x 100), both in cents)."""
    materials = sheets = 0.0
    for r in i["sheets"]:
        price = r.get("price")
        if price is None:
            w, l = (float(v) for v in r["size"].split("x"))
            price = js_round(r["per_sqft"] * (w * l) * 100) / 100
        materials = materials + float(r["count"]) * float(price)
        sheets = sheets + float(r["count"])
    rate, days, hours = float(i["rate"]), float(i["days"]), float(i["hours"])
    labor = rate * days * hours
    overtime = max(0.0, hours - K["overtime_after_hours"]) * days * K["overtime_rate"]
    travel = float(i["miles"]) * K["travel_per_mile"]
    hotel = float(i["nights"]) * K["hotel_nightly"]
    profit = float(i["profit"]) if i.get("profit") is not None else (K["profit_flat"] if sheets < K["profit_flat_under_sheets"] else K["profit_per_day"] * days)
    total = materials + labor + overtime + travel + hotel + float(i["freight"]) + profit
    return int(Decimal(total).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100), js_round(total * 100)


def in_core(i: dict) -> dict:
    items = [r for r in i["sheets"] if r.get("price") is None]
    rec = S.clean(dict(RAW, price_items=[{"id": f"it{n}", "name": f"item {n}", "per": "sqft", "price": repr(r["per_sqft"])} for n, r in enumerate(items)]))[0]
    d, now, n_item = W.default_doc(), "2026-10-03 00:00", 0
    for r in i["sheets"]:
        W.apply_act(d, {"kind": "sheet_add", "size": r["size"]}, "t", now, rec)
        sid = d["sheets"][-1]["id"]
        W.apply_act(d, {"kind": "sheet_set", "id": sid, "field": "count", "value": str(r["count"])}, "t", now, rec)
        if r.get("price") is None:
            W.apply_act(d, {"kind": "sheet_price_item", "id": sid, "item": f"it{n_item}"}, "t", now, rec)
            n_item += 1
        else:
            W.apply_act(d, {"kind": "sheet_set", "id": sid, "field": "price", "value": str(r["price"])}, "t", now, rec)
    for fld, key in (("labor_rate", "rate"), ("days", "days"), ("hours", "hours"), ("miles", "miles"), ("nights", "nights"), ("freight", "freight")):
        W.apply_act(d, {"kind": "job_set", "field": fld, "value": str(i[key])}, "t", now, rec)
    if i.get("profit") is not None:
        W.apply_act(d, {"kind": "job_set", "field": "profit", "value": str(i["profit"])}, "t", now, rec)
    return W.compute(d, rec)


def J(sheets, rate="40", days="2", hours="8", miles="0", nights="0", freight="0", profit=None):
    return dict(sheets=sheets, rate=rate, days=days, hours=hours, miles=miles, nights=nights, freight=freight, profit=profit)


S6 = [{"size": "4x8", "count": 6, "price": "50.50"}]
WHOLE = [
    ("3 sheets, one 8 hour day", J([{"size": "4x8", "count": 3, "price": "50.50"}], days="1")),
    ("5 sheets: one under the profit threshold", J([{"size": "4x8", "count": 5, "price": "50.50"}])),
    ("6 sheets: exactly at the profit threshold", J(S6)),
    ("7 sheets: one over it", J([{"size": "4x8", "count": 7, "price": "50.50"}])),
    ("6 sheets in two rows", J([{"size": "4x8", "count": 3, "price": "50.50"}, {"size": "4x10", "count": 3, "price": "64.25"}], days="1.5")),
    ("hours exactly at the overtime threshold", J(S6, hours="9", days="3")),
    ("half an hour over, 3 days", J(S6, hours="9.5", days="3")),
    ("11 hour days, 2.5 days", J(S6, rate="42.50", hours="11", days="2.5")),
    ("under the threshold", J(S6, hours="8.75")),
    ("travel and hotel", J(S6, miles="140", nights="2")),
    ("travel 61.5 miles, freight 212.40", J(S6, miles="61.5", freight="212.40")),
    ("profit typed 1250", J(S6, miles="30", profit="1250")),
    ("profit typed 0", J(S6, miles="30", profit="0")),
    ("a price a square foot: 1.75 x 32 SF, 18 sheets", J([{"size": "4x8", "count": 18, "per_sqft": 1.75}])),
    ("a price a square foot: 1.2345 x 40 SF", J([{"size": "4x10", "count": 12, "per_sqft": 1.2345}])),
    ("a price a square foot: 0.995 x 50 SF", J([{"size": "5x10", "count": 4, "per_sqft": 0.995}], days="0.5")),
    ("three sizes", J([{"size": "4x8", "count": 12, "price": "58.75"}, {"size": "4x10", "count": 6, "price": "73.10"}, {"size": "5x10", "count": 3, "price": "91.99"}],
                      rate="44", days="3", hours="9", miles="35", freight="95")),
    ("everything at once", J([{"size": "4x8", "count": 46, "price": "61.37"}, {"size": "4x10", "count": 18, "price": "77.89"}],
                             rate="47.50", days="4", hours="11", miles="212", nights="3", freight="412.66")),
    ("no sheets: labor only", J([], days="1", hours="6", miles="12")),
    ("zero days", J([{"size": "4x8", "count": 3, "price": "50.50"}], days="0", hours="0", freight="25")),
    ("a large job", J([{"size": "4x8", "count": 160, "price": "59.93"}, {"size": "5x10", "count": 80, "price": "94.17"}],
                      rate="49.75", days="9", hours="10", miles="340", nights="8", freight="1260.48")),
    ("quarter hours and quarter days that land on whole cents", J(S6, rate="48", days="1.25", hours="9.5")),
    ("a rate in cents", J(S6, rate="43.75", days="2", hours="8")),
    ("dimes and cents", J([{"size": "4x8", "count": 3, "price": "0.10"}, {"size": "4x8", "count": 3, "price": "0.20"}], rate="0.30", days="1", hours="1", miles="0.1", freight="0.07", profit="0.01")),
]

print(f"{len(WHOLE)} input sets where every line is whole cents: the same total")
for what, inp in WHOLE:
    c = in_core(inp)
    screen, words = in_floats(inp)
    whole = all(Decimal(x["qty"].replace(",", "") or "1") * Decimal(x["rate"].replace(",", "") or "1") * 100 % 1 == 0 for x in c["lines"] if x["qty"] and x["rate"])
    check(whole, f"{what}: every line is whole cents (the set is what it says)")
    check(c["total_cents"] == screen == words and c["total_cents"] == sum(x["amount_cents"] for x in c["lines"]),
          f"{what}: CORE {c['total']} = the float formula, by both roundings", (c["total_cents"], screen, words))

print("the edges, by name")
at = in_core(J(S6, hours="9", days="3"))
check(next(x for x in at["lines"] if x["id"] == "overtime")["amount"] == "0.00", "overtime exactly at the threshold: 0.00")
p6 = next(x for x in in_core(J(S6))["lines"] if x["id"] == "profit")
p5 = next(x for x in in_core(J([{"size": "4x8", "count": 5, "price": "50.50"}]))["lines"] if x["id"] == "profit")
check(p5["amount"] == "700.00" and p6["amount"] == "1,900.00", "5 sheets: the flat 700.00; 6 sheets (at the threshold): 2 days x 950.00", (p5["amount"], p6["amount"]))
sq = in_core(J([{"size": "5x10", "count": 4, "per_sqft": 0.995}], days="0.5"))["lines"][0]
check(sq["rate"] == "49.75" and sq["amount"] == "199.00", "0.995 a square foot x 50 SF = 49.75 a sheet; 4 sheets 199.00", sq)

print("where a line carries a part of a cent: one cent, for a reason, and CORE's total is the sum of its printed lines")
PART = [
    # overtime 0.25 h x 1.25 d = 0.3125 h x 30.00 = 9.375 -> prints 9.38; with freight 0.10 the exact sum ends in a half cent,
    # which the double holds a hair low: the screen's rounding drops it, round(total x 100) does not
    ("the total sits on a half cent", J([{"size": "4x8", "count": 5, "price": "50.50"}], rate="48", days="1.25", hours="9.25", freight="0.10"), (+1, 0)),
    # labor 43.37 x 1.25 x 9.25 = 501.465625 -> prints 501.47 (+0.4375 c); overtime 9.375 -> 9.38 (+0.5 c): the unrounded sum loses both
    ("labor and overtime each round up", J([{"size": "4x8", "count": 5, "price": "50.50"}], rate="43.37", days="1.25", hours="9.25"), (+1, +1)),
    # labor 43.33 x 1.25 x 9.25 = 501.003125 -> prints 501.00 (-0.3125 c); overtime +0.5 c: the two parts add to less than a cent's rounding
    ("parts of a cent that do not carry", J([{"size": "4x8", "count": 5, "price": "50.50"}], rate="43.33", days="1.25", hours="9.25"), (0, 0)),
]
for what, inp, (d_screen, d_words) in PART:
    c = in_core(inp)
    screen, words = in_floats(inp)
    check(c["total_cents"] == sum(x["amount_cents"] for x in c["lines"]), f"{what}: CORE's total {c['total']} is the sum of its printed lines")
    check((c["total_cents"] - screen, c["total_cents"] - words) == (d_screen, d_words),
          f"{what}: CORE less the float formula is {d_screen:+d} cent by the screen's rounding, {d_words:+d} by round(total x 100)",
          (c["total_cents"], screen, words))
    for x in c["lines"]:
        if x["qty"] and x["rate"]:
            exact = (Decimal(x["qty"].replace(",", "")) * Decimal(x["rate"].replace(",", ""))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            if f"{exact:,.2f}" != x["amount"]:
                check(False, f"{what}: {x['what']} prints qty x rate half up", (x["qty"], x["rate"], x["amount"]))

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
