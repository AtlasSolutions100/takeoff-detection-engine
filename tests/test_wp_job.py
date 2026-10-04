"""THE WALL PANEL JOB'S MODEL AND MONEY (takeoff_engine/wp_job.py, wp_settings.py; 2026-10-03).

Every number here is made up for the test: no installer's rate, threshold, sentence or note.
Each block names the ruling it holds:
  * no number is a default: an account with nothing set has no rate, and no rate line has an amount
  * wall lengths as typed: the sum is the wall length, exactly; a slip refuses the whole entry
  * a wall on a sheet is measured at the scale of the view it is drawn in (the sheet's scale in force
    where the sheet prints one scale); none in force, or a view that is not known = nothing in feet
  * the suggested count: wall length / the sheet's width, rounded up, on the shortest sheet long
    enough - with its arithmetic and "geometry only: no waste, no openings"; never applied
  * the costs: labor, overtime, travel, hotel, freight, profit (flat under the threshold, a day
    otherwise, or typed and shown as typed); printed qty x printed rate = printed amount; the total
    is the sum of the printed amounts; an unset rate or an untyped quantity says so and holds
  * a price item: per sheet, or per square foot x the sheet's area, half up to the cent
  * the scope sentence: named blanks; a blank the job itself knows (its sheets, its sizes, its rooms'
    product words) is filled from the job as it is now and is never typed over; rooms that differ
    are all printed; only a blank no part of the job holds is typed
  * the proposal is an allow-list; an act carries no figure

    python tests/test_wp_job.py
"""
import json
import os
import sys
import tempfile
from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="wpjob_")

from takeoff_engine import wp_job as W  # noqa: E402
from takeoff_engine import wp_settings as S  # noqa: E402
from takeoff_engine.fl_job import ActError  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:400]})"))
    if not ok:
        fails += 1


def refused(fn, *a, **k):
    try:
        fn(*a, **k)
    except ActError as e:
        return str(e)
    return None


NOW = "2026-10-03 09:00"
# a made-up installer
RATES = {"labor_rate": "41.37", "overtime_after_hours": "9", "overtime_rate": "17.25", "travel_per_mile": "0.93",
         "hotel_nightly": "141.19", "profit_flat": "650", "profit_per_day": "733.37", "profit_flat_under_sheets": 6,
         "sizes": ["4x8", "4x10", "5x10"], "molding_types": ["vinyl", "steel"],
         "price_items": [{"id": "pa", "name": "Panel A", "code": "PA-1", "per": "sqft", "price": "1.2345"},
                         {"id": "pb", "name": "Panel B", "per": "sheet", "price": "61.20"}],
         "scope_template": "Panels {thickness} thick, rated {fire_rating}, in {colour}, {finish} finish, {molding} trim. {sheets} sheets of {sizes}.",
         "notes": ["One trip to the site.", "Net 30."]}
REC, BAD = S.clean(RATES)


def job(acts, rates=REC):
    d = W.default_doc()
    for a in acts:
        W.apply_act(d, a, "tester", NOW, rates)
    return d


def line(comp, lid):
    return next(x for x in comp["lines"] if x["id"] == lid)


print("no number is a default")
b = S.blank()
check(all(b[k] is None for k in S.RATE_KEYS) and b["sizes"] == [] and b["scope_template"] is None and b["notes"] == [] and b["price_items"] == [],
      "the blank record: every rate and threshold not set, no size, no sentence, no note, no price item", b)
c0 = W.compute(job([{"kind": "job_set", "field": "days", "value": "2"}, {"kind": "job_set", "field": "hours", "value": "8"},
                    {"kind": "job_set", "field": "miles", "value": "10"}, {"kind": "job_set", "field": "nights", "value": "1"},
                    {"kind": "job_set", "field": "freight", "value": "0"}], S.blank()), S.blank(), company="Example Co")
check(all(line(c0, k)["amount"] == "" and line(c0, k)["missing"] for k in ("labor", "overtime", "travel", "hotel", "profit")),
      "with nothing set, labor, overtime, travel, hotel and profit have no amount and each says what is not set",
      [(x["id"], x["amount"]) for x in c0["lines"]])
check(c0["total"] == "0.00" and not c0["issue"] and len(c0["hold"]) >= 6, "the total is 0.00 and the proposal is held with the list", c0["hold"])
check(S.resolve("nobody@example.test") == (S.blank(), "none"), "an account that set nothing has no rates (it never takes another's)")

print("settings: one way in, every problem named, nothing guessed")
check(BAD == [] and REC["labor_rate"] == "41.37" and REC["profit_flat"] == "650.00" and REC["sizes"] == ["4x8", "4x10", "5x10"], "a sound record is taken as it is", BAD)
_r, bad = S.clean(dict(RATES, labour_rate="40"))
check(any("labour_rate" in x and "not a wall panel setting" in x for x in bad), "an unknown key is refused by name (a typo is not 'not set')", bad)
_r, bad = S.clean(dict(RATES, hotel_nightly="141.195"))
check(any("hotel_nightly" in x and "more than 2 decimals" in x for x in bad), "an amount with a third decimal is refused, never rounded", bad)
_r, bad = S.clean(dict(RATES, sizes=["4x8", "eight by four", "4x8"]))
check(len(bad) == 2 and "not a sheet size" in bad[0] and "listed twice" in bad[1], "a size that is not one, and a size listed twice, are each said", bad)
_r, bad = S.clean(dict(RATES, price_items=[{"name": "X", "per": "sqft", "price": "1.23456"}, {"name": "", "per": "roll", "price": 1}]))
check(len(bad) == 3, "a price a square foot keeps 4 decimals and no more; an item needs a name and what its price is for", bad)
_r, bad = S.clean(dict(RATES, scope_template="Panels {thickness thick"))
check(any("brace" in x for x in bad), "a template with a brace left over is refused", bad)
_r, bad = S.clean(dict(RATES, profit_flat_under_sheets="6.5", overtime_after_hours=25))
check(len(bad) == 2, "a threshold that is not a whole number of sheets, hours past 24: refused", bad)
r0, bad = S.clean({"travel_per_mile": 0, "hotel_nightly": ""})
check(bad == [] and r0["travel_per_mile"] == "0.00" and r0["hotel_nightly"] is None, "0 is a decision; blank is not set", (r0["travel_per_mile"], r0["hotel_nightly"]))
try:
    S.save("ann@example.test", dict(RATES, labor_rate="x"))
    saved = True
except ValueError as e:
    saved = False
check(not saved and not S.store_path().exists(), "a record with a problem is not written at all")
rec1 = S.save("ann@example.test", RATES, by="ann", at=NOW)
check(rec1["rev"] == 1 and S.resolve("ann@example.test")[1] == "account", "a sound record is written, rev 1")
try:
    S.save("ann@example.test", RATES, rev=0)
    stale = False
except S.Stale:
    stale = True
check(stale and S.resolve("ann@example.test")[0]["rev"] == 1, "a save that read rev 0 over rev 1 is refused")
S.save(None, dict(RATES, labor_rate="99"))
check(S.resolve("ben@example.test") == (S.blank(), "none") and S.resolve(None)[0]["labor_rate"] == "99.00",
      "an account with no record does not inherit the install's rates")
check(S.size_dims("10x4")[1:] == (Fraction(4), Fraction(10)) and S.size_dims("4 x 8")[0] == "4x8" and S.size_dims("4x") is None,
      "a size's width is its smaller number, its length the larger")

print("wall lengths as typed: the sum is the wall length")
check(sum(W.parse_lengths("8 + 8 + 12.5")[0]) == Fraction(57, 2), "8 + 8 + 12.5 = 28.5")
check(sum(W.parse_lengths("8'-6\" + 4'")[0]) == Fraction(25, 2) and sum(W.parse_lengths("8, 8")[0]) == 16, "8'-6\" + 4' = 12.5; a comma joins too")
check(sum(W.parse_lengths("0.1 + 0.2")[0]) == Fraction(3, 10) and sum(W.parse_lengths(" + ".join(["0.1"] * 10))[0]) == 1, "exact: 0.1 + 0.2 is 0.3, ten 0.1 are 1")
for slip in ("8 - 6", "8 + abc", "8 + 6\"x", "8 +  4 5"):
    check(W.parse_lengths(slip)[1] is not None and W.parse_lengths(slip)[0] == [], f"{slip!r} refuses the whole entry (no part of it is summed)", W.parse_lengths(slip))
d = job([{"kind": "room_add", "name": "Kitchen"}])
rid = d["rooms"][0]["id"]
why = refused(W.apply_act, d, {"kind": "room_set", "id": rid, "field": "typed", "value": "8 + 8 - 6"}, "t", NOW, REC)
check(why and "not a length" in why and d["rooms"][0]["typed"] == "", "a slip typed in a room is refused in words and nothing is saved", why)
why = refused(W.apply_act, d, {"kind": "room_set", "id": rid, "field": "height", "value": "8"}, "t", NOW, REC)
check(why and "unit" in why, "a panel height needs its unit: a bare 8 is refused", why)

print("walls on a sheet: feet at the sheet's scale in force, or nothing in feet")
SHEET = {0: {"detected": True, "ft": 4.0, "sheet": "clean", "words": None, "multi": False,
             "chains": {7: {"len": 288.0, "a": [100, 100], "b": [388, 100]}}}}
W.apply_act(d, {"kind": "room_set", "id": rid, "field": "typed", "value": "8 + 8 + 12.5"}, "t", NOW, REC)
W.apply_act(d, {"kind": "wall_put", "room": rid, "wall": {"page": 0, "src": "line", "chain": 7, "len_pt": 288.0, "a": [100, 100], "b": [388, 100]}}, "t", NOW, REC)
W.apply_act(d, {"kind": "wall_put", "room": rid, "wall": {"page": 0, "src": "drawn", "seg": [100, 100, 100, 244]}}, "t", NOW, REC)
f = W.compute(d, REC, SHEET)["rooms"][rid]
check([w["lf"] for w in f["walls"]] == ["16", "8"] and f["typed_lf"] == "28.5" and f["wall_lf"] == "52.5",
      "a picked line of 288 pt and a drawn wall of 144 pt at 1\" = 4 ft are 16 and 8 LF; with 28.5 typed the room is 52.5 LF", (f["wall_lf"], f["walls"]))
check("288.00 pt at 1\" = 4 ft = 16 LF" in f["walls"][0]["words"], "each wall shows its arithmetic", f["walls"][0]["words"])
nos = {0: dict(SHEET[0], ft=None, sheet="none", words="the readings disagree")}
f2 = W.compute(d, REC, nos)["rooms"][rid]
txt = json.dumps(f2)
check(f2["wall_lf"] is None and all(w["lf"] is None for w in f2["walls"]) and "no scale is in force" in f2["walls"][0]["words"]
      and not f2["suggestion"]["options"] and " LF" not in txt.replace("typed 28.5 LF", ""),
      "no scale in force: the walls show in pt, nothing of them in feet, the room has no wall length and no suggestion", f2)
amber = W.compute(d, REC, {0: dict(SHEET[0], sheet="amber")})["rooms"][rid]
check("sheet scale not confirmed" in amber["walls"][0]["words"], "a scale in force but not confirmed is said on the wall")
# a sheet that prints more than one scale: each wall at the scale of ITS OWN VIEW (the server's decision, given here
# as it gives it); a wall whose view is not known has no feet - never the sheet's scale by default (critic wp-1, MAJOR-2)
wid = [w["id"] for w in d["rooms"][0]["walls"]]
views = {wid[0]: {"ft": 2.0, "view": 'its view: 1/2" = 1\'-0"', "why": "the view title nearest this wall"},
         wid[1]: {"ft": None, "view": None, "why": "two view titles are as near this wall and print different scales: which view it is in can not be told"}}
multi = W.compute(d, REC, {0: dict(SHEET[0], multi=True, views=views)})["rooms"][rid]
check(multi["walls"][0]["lf"] == "8" and 'at 1" = 2 ft = 8 LF (its view: 1/2" = 1\'-0")' in multi["walls"][0]["words"] and multi["walls"][0]["state"] == "amber",
      "on a sheet that prints several scales a wall is measured at its own view's scale (288 pt at 1\" = 2 ft = 8 LF), said, never the clean colour", multi["walls"][0])
check(multi["walls"][1]["lf"] is None and multi["walls"][1]["pt"] == "144.00" and "two view titles" in multi["walls"][1]["words"]
      and "type this wall's length" in multi["walls"][1]["words"] and multi["wall_lf"] is None and not multi["suggestion"]["options"],
      "a wall whose view can not be told has no feet and says why; the room then has no wall length and no suggestion", multi["walls"][1])
check(multi["flags"] and "more than one scale" in multi["flags"][0] and "view" in multi["flags"][0], "and the room says the sheet prints several scales", multi["flags"])
bare = W.compute(d, REC, {0: dict(SHEET[0], multi=True)})["rooms"][rid]
check(all(w["lf"] is None for w in bare["walls"]) and bare["wall_lf"] is None,
      "a sheet that prints several scales with no view worked out for a wall: no feet (the sheet's scale is not a default)", bare["walls"])
moved = {0: dict(SHEET[0], chains={7: {"len": 301.5, "a": [100, 100], "b": [401.5, 100]}})}
f3 = W.compute(d, REC, moved)["rooms"][rid]
check(f3["walls"][0]["lf"] is None and "detected again" in f3["walls"][0]["words"] and f3["wall_lf"] is None,
      "a sheet detected again where the picked line is no longer that line: not counted, said", f3["walls"][0])
gone = {0: {"detected": False, "ft": None, "sheet": "none", "words": None, "multi": False, "chains": {}}}
f4 = W.compute(d, REC, gone)["rooms"][rid]
check(f4["walls"][0]["lf"] is None and "no detection" in f4["walls"][0]["words"], "a sheet with no detection now: the picked line is not counted", f4["walls"][0]["words"])
why = refused(W.apply_act, d, {"kind": "wall_put", "room": rid, "wall": {"page": 0, "src": "line", "chain": 7, "len_pt": 288.0, "a": [100, 100], "b": [388, 100]}}, "t", NOW, REC)
check(why and "already" in why, "the same line twice on one room is refused", why)

print("the suggested count: offered with its arithmetic, never applied")
sz = [S.size_dims(s) for s in ("4x8", "4x10", "5x10")]
s1 = W.suggest(Decimal("42.5"), Fraction(8), sz)
check([(o["size"], o["count"]) for o in s1["options"]] == [("4x8", 11)] and "42.5 LF ÷ 4 ft wide = 10.63, rounded up: 11 sheets of 4x8" in s1["options"][0]["arith"],
      "42.5 LF at 8 ft: 42.5 / 4 = 10.63, rounded up 11 of 4x8, the shortest sheet at least 8 ft long", s1)
check(s1["words"] == "geometry only: no waste, no openings" and "inference" not in s1["words"] and "smaller number" in s1["inference"],
      "it says 'geometry only: no waste, no openings', and that the width is read from the size (an inference)")
check(W.suggest(Decimal("32"), Fraction(8), sz)["options"][0]["count"] == 8, "32 LF / 4 is 8 sheets exactly, not 9")
check(W.suggest(Decimal("32.01"), Fraction(8), sz)["options"][0]["count"] == 9, "32.01 LF is 9")
s2 = W.suggest(Decimal("42.5"), Fraction(9), sz)
check(sorted((o["size"], o["count"]) for o in s2["options"]) == [("4x10", 11), ("5x10", 9)],
      "at 9 ft two sizes tie on the shortest length that fits (4x10, 5x10): both are offered, neither is ranked", s2["options"])
s3 = W.suggest(Decimal("42.5"), Fraction(12), sz)
check(not s3["options"] and "as long as" in s3["why"], "no sheet as long as the height: no suggestion, in words", s3["why"])
check("panel height" in W.suggest(Decimal("42.5"), None, sz)["why"] and "no wall length" in W.suggest(None, Fraction(8), sz)["why"],
      "no height, or no wall length: no suggestion, in words")
d2 = job([{"kind": "room_add", "name": "Prep"}, {"kind": "sheet_add", "size": "4x8"}])
W.apply_act(d2, {"kind": "room_set", "id": d2["rooms"][0]["id"], "field": "typed", "value": "42.5"}, "t", NOW, REC)
W.apply_act(d2, {"kind": "room_set", "id": d2["rooms"][0]["id"], "field": "height", "value": "8'"}, "t", NOW, REC)
c2 = W.compute(d2, REC)
check(c2["suggested"] == [{"size": "4x8", "count": 11}] and d2["sheets"][0]["count"] is None and c2["sheets_so_far"] == 0,
      "a suggestion of 11 leaves the sheet row's count untyped: it is never applied by itself", (c2["suggested"], d2["sheets"]))

print("the costs")


def priced(extra=(), rates=REC):
    acts = [{"kind": "sheet_add", "size": "4x8"}]
    d = job(acts, rates)
    sid = d["sheets"][0]["id"]
    for a in [{"kind": "sheet_set", "id": sid, "field": "count", "value": "6"}, {"kind": "sheet_set", "id": sid, "field": "price", "value": "58.75"},
              {"kind": "job_set", "field": "days", "value": "3.25"}, {"kind": "job_set", "field": "hours", "value": "9.5"},
              {"kind": "job_set", "field": "miles", "value": "117.5"}, {"kind": "job_set", "field": "nights", "value": "3"},
              {"kind": "job_set", "field": "freight", "value": "185.45"}] + list(extra):
        W.apply_act(d, a, "t", NOW, rates)
    return d


d3 = priced()
c3 = W.compute(d3, REC, company="Example Co")
want = {"labor": ("30.875", "41.37", "1,277.30"), "overtime": ("1.625", "17.25", "28.03"), "travel": ("117.5", "0.93", "109.28"),
        "hotel": ("3", "141.19", "423.57"), "profit": ("3.25", "733.37", "2,383.45")}
for k, (q, r, a) in want.items():
    ln = line(c3, k)
    check((ln["qty"], ln["rate"], ln["amount"]) == (q, r, a), f"{k}: {q} x {r} = {a}", (ln["qty"], ln["rate"], ln["amount"]))
check(line(c3, "freight")["amount"] == "185.45" and c3["lines"][0]["amount"] == "352.50", "freight is the amount typed; 6 sheets x 58.75 = 352.50")
for ln in c3["lines"]:
    if ln["qty"] and ln["rate"]:
        exact = (Decimal(ln["qty"].replace(",", "")) * Decimal(ln["rate"].replace(",", ""))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        check(f"{exact:,.2f}" == ln["amount"], f"{ln['what']}: printed qty x printed rate = printed amount ({ln['qty']} x {ln['rate']} = {ln['amount']})", exact)
check(c3["total_cents"] == sum(x["amount_cents"] for x in c3["lines"]) and c3["total"] == "4,759.58",
      "the total is the sum of the printed amounts: 4,759.58", c3["total"])
check(c3["total_words"] == "Four Thousand Seven Hundred Fifty-Nine Dollars and Fifty-Eight Cents", "and in words", c3["total_words"])
at = W.compute(priced([{"kind": "job_set", "field": "hours", "value": "9"}]), REC)
check(line(at, "overtime")["qty"] == "0" and line(at, "overtime")["amount"] == "0.00", "hours exactly at the threshold (9 of 9): 0 overtime hours, 0.00")
d5 = priced()
W.apply_act(d5, {"kind": "sheet_set", "id": d5["sheets"][0]["id"], "field": "count", "value": "5"}, "t", NOW, REC)
under = W.compute(d5, REC)
check(line(under, "profit")["amount"] == "650.00" and "fewer than 6" in line(under, "profit")["how"], "5 sheets, under the threshold of 6: the flat 650.00", line(under, "profit"))
check(line(c3, "profit")["amount"] == "2,383.45" and "not fewer than 6" in line(c3, "profit")["how"],
      "6 sheets, exactly at the threshold: not under it - 3.25 days x 733.37", line(c3, "profit")["how"])
ov = W.compute(priced([{"kind": "job_set", "field": "profit", "value": "1500"}]), REC)
pl = line(ov, "profit")
check(pl["amount"] == "1,500.00" and "typed for this job" in pl["what"] and "the rule gives 2,383.45" in pl["how"],
      "a profit typed for the job replaces the rule and is shown as typed, with what the rule gives", pl)
back = priced([{"kind": "job_set", "field": "profit", "value": "1500"}, {"kind": "job_set", "field": "profit", "value": ""}])
check(line(W.compute(back, REC), "profit")["amount"] == "2,383.45", "cleared, the rule is back")
lr = W.compute(priced([{"kind": "job_set", "field": "labor_rate", "value": "50"}]), REC)
check(line(lr, "labor")["rate"] == "50.00" and "typed for this job" in line(lr, "labor")["how"], "a labor rate typed for the job is used and said")
zero = W.compute(priced([{"kind": "job_set", "field": "miles", "value": "0"}]), REC, company="Example Co")
check(line(zero, "travel")["amount"] == "0.00" and not line(zero, "travel")["missing"], "0 miles is a decision: 0.00, nothing missing")
blank = W.compute(priced([{"kind": "job_set", "field": "miles", "value": ""}, {"kind": "job_set", "field": "freight", "value": ""}]), REC, company="Example Co")
check(any("miles not typed" in h for h in blank["hold"]) and any("Freight: not typed" in h for h in blank["hold"]) and not blank["issue"]
      and line(blank, "travel")["amount"] == "", "a quantity not typed says so and holds the proposal", blank["hold"])
norate = S.clean(dict(RATES, hotel_nightly=None, profit_per_day=""))[0]
nr = W.compute(priced(rates=norate), norate, company="Example Co")
check(any("Hotel: the amount a night is not set" in h for h in nr["hold"]) and any("Profit: the amount a day is not set" in h for h in nr["hold"])
      and line(nr, "hotel")["amount"] == "" and not nr["issue"], "a line whose rate is not set says so and the proposal can not be issued", nr["hold"])
why = refused(W.apply_act, d3, {"kind": "job_set", "field": "freight", "value": "12.345"}, "t", NOW, REC)
check(why and "more than 2 decimals" in why, "an amount typed with a third decimal is refused, never rounded", why)
why = refused(W.apply_act, d3, {"kind": "sheet_set", "id": d3["sheets"][0]["id"], "field": "count", "value": "2.5"}, "t", NOW, REC)
check(why and "whole number" in why, "a sheet count is a whole number", why)
why = refused(W.apply_act, d3, {"kind": "job_set", "field": "days", "value": "-1"}, "t", NOW, REC)
check(why and "not a quantity" in why, "a negative quantity is refused", why)
why = refused(W.apply_act, d3, {"kind": "job_set", "field": "project", "value": "x" * 130}, "t", NOW, REC)
check(why and "nothing was cut" in why, "a text too long is refused, never cut", why)

print("typed lines (moldings, adhesive): what, quantity, unit, price - no rule of ours")
d6 = priced([{"kind": "line_add"}])
lid = d6["lines"][0]["id"]
check(len(W.compute(d6, REC)["lines"]) == len(c3["lines"]), "an empty typed line prints nothing and holds nothing")
W.apply_act(d6, {"kind": "line_set", "id": lid, "field": "what", "value": "Adhesive"}, "t", NOW, REC)
h6 = W.compute(d6, REC, company="Example Co")
check(any("Adhesive: no quantity" in h for h in h6["hold"]) and any("Adhesive: no price" in h for h in h6["hold"]), "a named line needs its quantity and its price", h6["hold"])
for fld, v in (("qty", "7.5"), ("unit", "tubes"), ("price", "6.49")):
    W.apply_act(d6, {"kind": "line_set", "id": lid, "field": fld, "value": v}, "t", NOW, REC)
c6 = W.compute(d6, REC, company="Example Co")
ad = line(c6, "line:" + lid)
check((ad["qty"], ad["unit"], ad["rate"], ad["amount"]) == ("7.5", "tubes", "6.49", "48.68") and c6["total_cents"] == c3["total_cents"] + 4868,
      "7.5 tubes x 6.49 = 48.68 (half up), in the total", ad)

print("a price item: per sheet, or per square foot x the sheet's area, half up to the cent")
check(W.item_price_cents("sheet", "61.20", "4x8") == 6120, "a price a sheet is that price")
check(W.item_price_cents("sqft", "1.2345", "4x8") == 3950, "1.2345 a square foot x 32 SF = 39.504 -> 39.50")
check(W.item_price_cents("sqft", "1.2345", "5x10") == 6173 and round(1.2345 * 50 * 100) == 6172,
      "1.2345 x 50 SF = 61.725 -> 61.73 half up, exactly (binary floating point gives 61.72)", W.item_price_cents("sqft", "1.2345", "5x10"))
d7 = job([{"kind": "sheet_add", "size": "5x10"}])
sid = d7["sheets"][0]["id"]
said = W.apply_act(d7, {"kind": "sheet_price_item", "id": sid, "item": "pa"}, "t", NOW, REC)
check(d7["sheets"][0]["price_cents"] == 6173 and "1.2345 a square foot x 50 SF" in said, "picked on a row: the price and where it came from", said)
said = W.apply_act(d7, {"kind": "sheet_set", "id": sid, "field": "size", "value": "4x8"}, "t", NOW, REC)
check(d7["sheets"][0]["price_cents"] == 3950 and "follows the size" in said, "the row's size changes: a square-foot price follows it", said)
W.apply_act(d7, {"kind": "sheet_set", "id": sid, "field": "price", "value": "40"}, "t", NOW, REC)
check(d7["sheets"][0]["price_cents"] == 4000 and d7["sheets"][0]["price_from"] is None, "a price typed over it is the typed price")
why = refused(W.apply_act, d7, {"kind": "sheet_price_item", "id": sid, "item": "nope"}, "t", NOW, REC)
check(why and "not in your settings" in why, "a price item that is gone is refused", why)

print("the scope sentence: a template with named blanks")
d8 = priced([{"kind": "room_add", "name": "A"}, {"kind": "room_add", "name": "B"}])
ra, rb = d8["rooms"][0]["id"], d8["rooms"][1]["id"]
for rid_, fld, v in ((ra, "thickness", '.090"'), (ra, "fire", "Rated A"), (rb, "fire", "rated a"), (ra, "colour", "White"), (rb, "colour", "Almond"), (ra, "molding", "vinyl")):
    W.apply_act(d8, {"kind": "room_set", "id": rid_, "field": fld, "value": v}, "t", NOW, REC)
s8 = W.compute(d8, REC, company="Example Co")["scope"]
bl = {x["name"]: x for x in s8["blanks"]}
check(bl["thickness"]["value"] == '.090"' and bl["fire_rating"]["value"] == "Rated A" and bl["fire_rating"]["by"] == "from the rooms",
      "rooms that agree fill a blank (case aside)", bl["fire_rating"])
check(bl["colour"]["value"] == "White and Almond" and "2 different colours" in bl["colour"]["by"] and bl["colour"]["kind"] == "rooms",
      "rooms that differ are ALL printed, in the rooms' order - no first room wins, and nothing is typed over the job", bl["colour"])
check(bl["finish"]["value"] is None and bl["finish"]["kind"] == "typed" and bl["sheets"]["value"] == "6" and bl["sizes"]["value"] == "4x8"
      and bl["sheets"]["kind"] == "sheets", "a blank no part of the job holds is typed for the job; sheets and sizes come from the sheet rows", bl)
check(s8["text"] == 'Panels .090" thick, rated A, in White and Almond, ___ finish, vinyl trim. 6 sheets of 4x8.' and len(s8["missing"]) == 1,
      "the sentence with its open blank; a value that opens with the template's own word is not said twice", s8["text"])
why = refused(W.apply_act, d8, {"kind": "blank_set", "name": "colour", "value": "Ivory"}, "t", NOW, REC)
check(why and "filled by the job itself" in why and "rooms" in why, "a product blank can not be typed over: the job fills it from its rooms", why)
W.apply_act(d8, {"kind": "blank_set", "name": "finish", "value": "pebbled"}, "t", NOW, REC)
c8 = W.compute(d8, REC, company="Example Co")
check(c8["scope"]["text"] == 'Panels .090" thick, rated A, in White and Almond, pebbled finish, vinyl trim. 6 sheets of 4x8.' and c8["issue"],
      "with its one typed blank typed, the sentence is whole and the proposal can be issued", (c8["scope"]["text"], c8["hold"]))
check(not W.compute(d8, REC, company="")["issue"], "no company name: held")
nt = W.compute(d8, S.clean(dict(RATES, scope_template=""))[0], company="Example Co")
check(not nt["issue"] and any("scope sentence is not set" in h for h in nt["hold"]), "no sentence in the settings: held, in words", nt["hold"])
two = priced([{"kind": "sheet_add", "size": "4x10"}])
W.apply_act(two, {"kind": "sheet_set", "id": two["sheets"][1]["id"], "field": "count", "value": "2"}, "t", NOW, REC)
check({x["name"]: x["value"] for x in W.compute(two, REC)["scope"]["blanks"]}["sizes"] == "4x8 and 4x10", "two sizes on the job: '4x8 and 4x10'")

print("the proposal is an allow-list")
p = W.proposal(c8, d8, "Example Co", "555-0100", "October 3, 2026")
check(tuple(p) == W.PROPOSAL_KEYS, "exactly the listed keys", list(p))
flat = json.dumps(p)
check(all(s not in flat for s in ("41.37", "733.37", "141.19", "17.25", "0.93", "185.45", "3.25", "30.875", "2,383.45", "1,277.30", "Labor", "Profit", "Overtime"))
      and c8["total"] in flat, "no rate, no days, no hours, no profit, no cost line in it - the total is", flat)
ws = W.worksheet_csv(c8, d8, REC, "plan.pdf")
check("41.37" in ws and "Profit" in ws and f'"{c8["total"]}"' in ws and "issued" in ws, "the worksheet carries the breakdown and the same total")
check("HELD" in W.worksheet_csv(nt, d8, REC, "plan.pdf"), "a held job's worksheet says HELD and why")

print("an act carries what a person did, never a figure")
check("lf is a figure" in (W.act_problem({"kind": "room_set", "id": "r", "field": "typed", "value": "8", "lf": 99}) or ""), "a figure in an act is refused by name")
check("amount_cents" in (W.act_problem({"kind": "line_set", "id": "l", "field": "price", "value": "1", "amount_cents": 5}) or ""), "an amount too")
check("server's own act" in (W.act_problem({"kind": "wall_put", "room": "r", "wall": {}}) or ""), "a measured wall is the server's own act")
check("not part of it" in (W.act_problem({"kind": "room_del", "id": "r", "note": "x"}) or ""), "any other extra key is refused")
check("unknown act" in (W.act_problem({"kind": "total_set"}) or "") and W.act_problem({"kind": "room_add", "name": "x"}) is None, "an unknown act is refused; a plain one passes")
check("plain value" in (W.act_problem({"kind": "room_set", "id": "r", "field": "name", "value": {"wall_lf": 99}}) or ""), "a value that is a structure is refused (a figure can not ride inside one)")
check("two ends" in (W.act_problem({"kind": "wall_draw", "room": "r", "page": 0, "seg": [1, 2, 3]}) or "")
      and W.act_problem({"kind": "wall_draw", "room": "r", "page": 0, "seg": [1, 2, 3, 4]}) is None, "a drawn wall is four numbers, nothing else")
why = refused(W.apply_act, W.default_doc(), {"kind": "wall_pick", "room": "r", "page": 0, "chain": 1}, "t", NOW, REC)
check(why and "measured by the server" in why, "a pick that was not measured by the server is not applied", why)

print("the job stands on the rates it took")
d9 = priced()
W.apply_act(d9, {"kind": "rates_take"}, "t", NOW, REC)
newer = S.clean(dict(RATES, hotel_nightly="150"))[0]
check(W.compute(d9, d9["rates"])["total"] == c3["total"] and S.differences(d9["rates"], newer) == ["hotel, dollars a night: 141.19 here, 150.00 in your settings now"],
      "a later change to the settings leaves the job's total; what differs is said", S.differences(d9["rates"], newer))
W.apply_act(d9, {"kind": "rates_take"}, "t2", NOW, newer)
check(line(W.compute(d9, d9["rates"]), "hotel")["amount"] == "450.00" and d9["rates_at"]["by"] == "t2", "taken again on request, the job moves to them")
rt = W.clean_doc(json.loads(json.dumps(d9)))
check(rt == W.clean_doc(rt) and rt["rates"]["hotel_nightly"] == "150.00", "the document survives its own store and read")

print("the amount in words")
for cents_, words in ((0, "Zero Dollars"), (100, "One Dollar"), (101, "One Dollar and One Cent"), (531807, "Five Thousand Three Hundred Eighteen Dollars and Seven Cents"),
                      (100000000, "One Million Dollars"), (1900019, "Nineteen Thousand Dollars and Nineteen Cents"), (111111199, "One Million One Hundred Eleven Thousand One Hundred Eleven Dollars and Ninety-Nine Cents")):
    check(W.amount_words(cents_) == words, f"{cents_} cents: {words}", W.amount_words(cents_))

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
