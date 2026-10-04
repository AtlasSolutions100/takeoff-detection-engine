"""A PAGE'S SCALE IS A VERDICT FROM WITNESSES (takeoff_engine.scale_verdict).

Table tests for every rule of the contract (docs/fable/TOOLS-MISSION.md
Part 1, decided 2026-10-02) and its edges, plus the adapter that builds the
witnesses from the fields the app already computes. No PDF, no I/O.

The real-page rows quote shapes measured in the scale census
(out/verify/2026-10-02/scale-census/CENSUS.md); their values are what the
reader returned there, not truth.

Run:  python tests/test_scale_verdict.py
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from takeoff_engine.scale_verdict import (  # noqa: E402
    CONFIRMED, CONFLICT, NONE, UNCHECKED, agree, rel_diff, verdict,
    verdict_from_app, witnesses_from_app, person_from_review)

FAILS = []


def check(ok, msg):
    print(("ok   " if ok else "FAIL ") + msg)
    if not ok:
        FAILS.append(msg)


def W(kind, value, in_force=False, role="reading", source=""):
    return {"kind": kind, "value": value, "in_force": in_force, "role": role,
            "source": source}


def P(value, how="typed"):
    return {"value": value, "how": how, "who": "tester", "when": "2026-10-02T12:00:00"}


def raises(fn):
    try:
        fn()
    except ValueError:
        return True
    return False


# --------------------------------------------------------------------------
# the four states, one table: (name, witnesses, person, state, value)
# --------------------------------------------------------------------------
TABLE = [
    ("no witness at all", [], None, NONE, None),
    ("one caption", [W("caption", 20, True)], None, UNCHECKED, 20),
    ("one bar", [W("bar", 40.0167, True)], None, UNCHECKED, 40.0167),
    # ruling 3: the printed dimensions ALONE are offered, not applied
    ("dimensions only", [W("dimensions", 7.93)], None, NONE, None),
    ("profile only", [W("profile_axis", 39.998)], None, UNCHECKED, 39.998),
    ("caption + bar agree", [W("caption", 20, True), W("bar", 20.0)], None,
     CONFIRMED, 20),
    ("caption + dimensions agree", [W("caption", 8, True), W("dimensions", 7.9)],
     None, CONFIRMED, 8),
    ("bar + dimensions agree, value is the bar's",
     [W("bar", 30.0, True), W("dimensions", 29.8)], None, CONFIRMED, 30.0),
    ("caption + profile agree", [W("caption", 40, True), W("profile_axis", 39.998)],
     None, CONFIRMED, 40),
    ("caption vs bar disagree", [W("caption", 8, True), W("bar", 10.67)], None,
     CONFLICT, None),
    # a structural sheet: caption and bar both say 4, the printed
    # dimensions say ~7.93 -> CONFLICT even though two witnesses agree
    ("three witnesses, two agree, one not = CONFLICT",
     [W("caption", 4, True), W("bar", 3.965), W("dimensions", 7.93)], None,
     CONFLICT, None),
    ("three witnesses all agree", [W("caption", 20, True), W("bar", 20.02),
                                   W("dimensions", 19.9)], None, CONFIRMED, 20),
    # a person always wins
    ("person overrides a conflict", [W("caption", 4, True), W("bar", 3.965),
                                     W("dimensions", 7.93)], P(8), CONFIRMED, 8),
    ("person value differs from all witnesses",
     [W("caption", 20, True), W("bar", 20.0)], P(40, "calibrated"), CONFIRMED, 40),
    ("person on a page with no witness", [], P(10, "typed"), CONFIRMED, 10),
    ("person confirms the one witness", [W("caption", 20, True)],
     P(20, "confirmed"), CONFIRMED, 20),
]
v = verdict([W("dimensions", 7.93)])
check([w["kind"] for w in v["offered"]] == ["dimensions"] and v["offered"][0]["value"] == 7.93
      and "offered as a choice, not applied" in v["why"],
      f"a dimension-only page offers its one reading and says so: {v['why']}")
v = verdict([W("dimensions", 7.93), W("caption", 4, role="offered")])
check(v["state"] == CONFLICT, "a lone dimension reading still contradicts an offered caption that disagrees: CONFLICT")
v = verdict([W("dimensions", 7.93)], P(8, "picked"))
check(v["state"] == CONFIRMED and v["value"] == 8, "a person picking near the offered dimension reading: CONFIRMED")
for name, ws, person, st, val in TABLE:
    v = verdict(ws, person)
    check(v["state"] == st and v["value"] == val,
          f"{name}: {v['state']} {v['value']}  (want {st} {val}) - {v['why']}")

# --------------------------------------------------------------------------
# the 2 % boundary, on both sides and in both orders
# --------------------------------------------------------------------------
check(agree(100, 102) and agree(102, 100), "exactly 2 % (100 vs 102) agrees, either order")
check(not agree(100, 102.01) and not agree(102.01, 100),
      "just over 2 % (100 vs 102.01) disagrees, either order")
check(agree(100, 101.99), "just under 2 % agrees")
check(not agree(100, 98) and not agree(98, 100),
      "100 vs 98 is 2.04 % of the smaller and disagrees (the stricter denominator)")
check(agree(98, 99.96) and agree(99.96, 98), "98 vs 99.96 is exactly 2 % of 98: agrees")
check(abs(rel_diff(20, 20.4) - 0.02) < 1e-12 and agree(20, 20.4), "20 vs 20.4: 2 %, agrees")
check(verdict([W("caption", 100, True), W("bar", 102)])["state"] == CONFIRMED,
      "verdict at exactly 2 %: CONFIRMED")
check(verdict([W("caption", 100, True), W("bar", 102.01)])["state"] == CONFLICT,
      "verdict just over 2 %: CONFLICT")
check(verdict([W("bar", 102, True), W("caption", 100)])["state"] == CONFIRMED,
      "the order of the witness list does not matter at the boundary")
# a chain of agreements is not agreement: a-b and b-c within 2 %, a-c not
v = verdict([W("caption", 100, True), W("bar", 101.9), W("dimensions", 103.8)])
check(v["state"] == CONFLICT, f"100 / 101.9 / 103.8: each neighbour within 2 %, the ends 3.8 % apart -> CONFLICT ({v['state']})")

# --------------------------------------------------------------------------
# same kind = one witness
# --------------------------------------------------------------------------
v = verdict([W("caption", 20, True, source="text caption"),
             W("caption", 20, source="ocr caption")])
check(v["state"] == UNCHECKED and len(v["agree"]) == 1,
      f"a text caption and its OCR twin are ONE witness: UNCHECKED ({v['state']}, {len(v['agree'])} voting)")
v = verdict([W("caption", 20), W("caption", 20.1)])
check(v["state"] == UNCHECKED, "two agreeing captions with none in force: one witness, UNCHECKED")
v = verdict([W("bar", 20, True), W("bar", 20.0)])
check(v["state"] == UNCHECKED, "two bars agreeing: one witness")

# --------------------------------------------------------------------------
# multi-scale page: the governing pick stays, UNCHECKED, other scales named
# --------------------------------------------------------------------------
v = verdict([W("caption", 20, True), W("caption", 5), W("caption", 300)])
check(v["state"] == UNCHECKED and v["value"] == 20
      and sorted(w["value"] for w in v["other_printed"]) == [5, 300],
      f"plan 20 governs, profile V 5 and key map 300 printed: UNCHECKED at 20, others named ({v['state']}, {v['other_printed']})")
check("also prints" in v["why"], "the words name the other printed scales: " + v["why"])
v = verdict([W("caption", 20, True), W("caption", 5), W("bar", 20.02)])
check(v["state"] == CONFIRMED and v["value"] == 20,
      "multi-scale page whose governing value has a bar: CONFIRMED at 20")
v = verdict([W("caption", 20, True), W("caption", 40), W("bar", 40.0)])
check(v["state"] == CONFLICT,
      "a bar that matches a NON-governing caption still contradicts the pick: CONFLICT")
v = verdict([W("caption", 60, True), W("caption", 20), W("caption", 80), W("bar", 60.02),
             W("profile_axis", 80.0)])
check(v["state"] == CONFLICT,
      "plan-and-profile page: a profile fit at the profile's own H scale contradicts the plan pick (see DESIGN.md)")

# --------------------------------------------------------------------------
# offered (not promoted) readings: never a value, never a confirmation,
# but a disagreement when nothing of theirs matches
# --------------------------------------------------------------------------
v = verdict([W("caption", 5, role="offered"), W("caption", 20, role="offered")])
check(v["state"] == NONE and v["value"] is None and len(v["offered"]) == 2,
      f"OCR-rescued captions alone: NONE, both offered ({v['state']}: {v['why']})")
v = verdict([W("caption", 4, role="offered")])
check(v["state"] == NONE, "a single rescued caption is NOT promoted (FACTS A5): NONE")
v = verdict([W("caption", 10, role="offered"), W("bar", 20.0)])
check(v["state"] == CONFLICT and v["value"] is None
      and sorted(c["value"] for c in v["choices"]) == [10, 20.0],
      f"a refused caption against the bar that refuted it: CONFLICT, both offered ({v['state']}, {v['choices']})")
v = verdict([W("caption", 10, role="offered"), W("caption", 20, role="offered"), W("bar", 20.0)])
check(v["state"] == UNCHECKED and v["value"] == 20.0,
      f"an offered value that matches the bar: no conflict, still only the bar votes: UNCHECKED at 20 ({v['state']})")
v = verdict([W("caption", 20, role="offered"), W("bar", 20.0), W("dimensions", 20.1)])
check(v["state"] == CONFIRMED and v["value"] == 20.0,
      "offered caption + bar + dimensions: bar and dimensions confirm; the value is the bar's, never the offered caption's")
v = verdict([W("caption", 20), W("caption", 5)])
check(v["state"] == NONE,
      "two disagreeing captions with none in force: the kind is unresolved -> NONE")
v = verdict([W("caption", 20, True), W("caption", 7, role="offered")])
check(v["state"] == UNCHECKED and [w["value"] for w in v["other_printed"]] == [7],
      "an offered value beside an in-force caption is named, does not vote")

# --------------------------------------------------------------------------
# bad values: zero / negative / missing / not a number - dropped, said
# --------------------------------------------------------------------------
for bad, word in ((0.0, "zero"), (0, "zero"), (-20, "negative"), (None, "no value"),
                  (float("nan"), "finite"), (float("inf"), "finite"),
                  ("20", "not a number"), (True, "not a number")):
    v = verdict([W("caption", 20, True), W("bar", bad)])
    check(v["state"] == UNCHECKED and len(v["ignored"]) == 1
          and word in v["ignored"][0]["reason"],
          f"bar value {bad!r} is ignored ({word}), not a witness: UNCHECKED")
v = verdict([W("bar", 0.0, True)])
check(v["state"] == NONE and v["ignored"][0]["reason"].startswith("zero"),
      "a measured 0.0 alone is no scale (and is not mistaken for 'missing'): NONE, reason 'zero'")
v = verdict([W("caption", 20, True)], P(0.0))
check(v["state"] == UNCHECKED and v["value"] == 20 and v["ignored"][0]["kind"] == "person",
      "a person's record with 0.0 is ignored and said; the witnesses decide")
v = verdict([W("caption", 20, True)], P(None))
check(v["state"] == UNCHECKED, "a person's record with no value is ignored")
v = verdict([W("caption", 20, True)], P(-8))
check(v["state"] == UNCHECKED, "a negative person value is ignored")

# programming errors are loud
check(raises(lambda: verdict([W("ruler", 20)])), "an unknown kind raises")
check(raises(lambda: verdict([W("person", 20)])), "a person passed as a witness raises (use person=)")
check(raises(lambda: verdict([W("caption", 20, role="maybe")])), "an unknown role raises")
check(raises(lambda: verdict([], {"value": 20, "how": "guessed"})), "an unknown person.how raises")
check(raises(lambda: verdict([W("caption", 20, True), W("caption", 5, True)])),
      "two captions IN FORCE with different values raises (one scale in force per page)")

# person record carries who / when / how / value
v = verdict([W("caption", 20, True), W("bar", 20.0)], P(40, "calibrated"))
check(v["person"] == {"value": 40, "how": "calibrated", "who": "tester",
                      "when": "2026-10-02T12:00:00"}
      and len(v["disagree"]) == 2 and v["agree"] == [],
      "a person's record keeps who / when / how / value; witnesses sorted against it")
v = verdict([W("caption", 20, True), W("bar", 40)], P(20, "confirmed"))
check([w["kind"] for w in v["agree"]] == ["caption"] and [w["kind"] for w in v["disagree"]] == ["bar"],
      "person confirms one side of a conflict: the other side is listed as disagreeing")

# CONFLICT: no value, every reading a choice
v = verdict([W("caption", 4, True), W("bar", 3.965), W("dimensions", 7.93)])
check(v["value"] is None and sorted(c["kind"] for c in v["choices"]) == ["bar", "caption", "dimensions"],
      "CONFLICT has no value and offers every reading as a choice")
check(verdict([W("caption", 20, True)])["choices"] == [], "no choices outside CONFLICT")
check(verdict([W("caption", 20, True), W("bar", 20.0)])["value"] == 20,
      "CONFIRMED value is the caption's exact number, not the bar's 20.0x")

# purity: the caller's witness dicts are not changed
ws = [W("caption", 20, True), W("bar", 20.3)]
before = [dict(w) for w in ws]
verdict(ws)
check(ws == before, "verdict() does not modify its input")

# --------------------------------------------------------------------------
# the adapter: today's to_dict shapes -> witnesses
# --------------------------------------------------------------------------
cap = lambda v, t: {"text": t, "ft_per_paper_inch": v, "near_scale_keyword": True,  # noqa: E731
                    "count": 1, "governs_pt": None, "governs_pct": None, "sites": 1}
# text caption, bar agrees (label centres, _corroborate without a page)
s = {**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
     "candidates": [cap(20.0, '1" = 20\'')], "bar_ft_per_paper_inch": 20.04,
     "agrees": True, "delta_pct": 0.2, "pick": {"why": "single"}}
v = verdict_from_app(scale=s)
check(v["state"] == CONFIRMED and v["value"] == 20.0, f"text caption + bar: CONFIRMED ({v['why']})")
ws = witnesses_from_app(scale=s)
check([w["kind"] for w in ws] == ["caption", "caption", "bar"] and "label centres" in ws[2]["source"],
      "the text path's bar is named as read off label centres")
# CRITIC-scale-1 MINOR 1 / 4: the witness says how the bar was read; a label-centre bar still votes (DESIGN risk 6)
ws_t = witnesses_from_app(scale=dict(s, pick={"why": "single", "bar_source": "native-ticks"}))
check("measured on its drawn ticks" in ws_t[2]["source"] and ws_t[2]["note"] == "bar_source=native-ticks",
      "a bar measured on drawn ticks says so")
check("ticks not measured" in ws[2]["source"] and ws[2]["note"] == "bar_source=none"
      and verdict_from_app(scale=s)["state"] == CONFIRMED,
      "a label-centre bar says its ticks were not measured, and still votes (for now)")
# multi-scale governs pick, no bar
s2 = {**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
      "candidates": [cap(20.0, '1" = 20\''), cap(5.0, '1" = 5\''), cap(300.0, '1" = 300\'')],
      "bar_ft_per_paper_inch": None, "agrees": None, "delta_pct": None,
      "pick": {"why": "governs"}}
v = verdict_from_app(scale=s2)
check(v["state"] == UNCHECKED and v["value"] == 20.0 and len(v["other_printed"]) == 2,
      "governs pick with two other printed scales: UNCHECKED at 20, the others named")
# a structural sheet whose caption 4 and bar 3.965 agree and whose printed dimensions say ~7.93
s3 = {**cap(4.0, '1/4" - 1\'-0"'), "source": "text", "has_nts": True,
      "candidates": [cap(4.0, '1/4" - 1\'-0"'), cap(8.0, '1/8"=1\'-0"')],
      "bar_ft_per_paper_inch": 3.965, "agrees": True, "delta_pct": 0.9,
      "pick": {"why": "governs"}}
dim = {"verdict": "disagrees", "witnesses": [{}] * 7, "witness_consensus_n": 5,
       "witness_consensus_ft_per_in": 7.93}
v = verdict_from_app(scale=s3, dimcheck=dim)
check(v["state"] == CONFLICT and v["value"] is None,
      f"a structural sheet (caption 4, bar 3.965, dimensions 7.93): CONFLICT ({v['why']})")
# dimension check that cannot speak is no witness
for d, why in (({"witnesses": [{}] * 2, "witness_consensus_n": 2, "witness_consensus_ft_per_in": 7.9}, "2 witnesses"),
               ({"witnesses": [{}] * 10, "witness_consensus_n": 6, "witness_consensus_ft_per_in": 7.9}, "6 of 10 is under 70 %"),
               ({"verdict": "no-witnesses", "witnesses": []}, "no witnesses")):
    check(not any(w["kind"] == "dimensions" for w in witnesses_from_app(dimcheck=d)),
          f"a dimension check that could not reach a verdict is no witness ({why})")
check(any(w["kind"] == "dimensions" for w in witnesses_from_app(
    dimcheck={"n_witnesses": 10, "witness_consensus_n": 7, "witness_consensus_ft_per_in": 7.9})),
      "7 of 10 (exactly 70 %) is a witness; n_witnesses is read when the list is absent")
# bar-only page
s4 = {"text": "graphic bar 0-80 ft over 144.0pt", "ft_per_paper_inch": 40.0, "near_scale_keyword": True,
      "count": 1, "governs_pt": None, "governs_pct": None, "sites": 0, "source": "graphic-bar",
      "has_nts": False, "candidates": [{"text": "graphic bar 0-80 ft over 144.0pt", "ft_per_paper_inch": 40.0}],
      "bar_ft_per_paper_inch": None, "agrees": None, "delta_pct": None,
      "pick": {"why": "bar", "bar_source": "native-ticks"}}
v = verdict_from_app(scale=s4)
check(v["state"] == UNCHECKED and v["agree"][0]["kind"] == "bar",
      "a bar-only page is UNCHECKED (today's chip is green), the bar candidate is not also a caption")
# OCR caption with no independent bar
s5 = {**cap(10.0, "SCALE: 1 = 10'-0\""), "source": "ocr", "has_nts": False,
      "candidates": [cap(10.0, "SCALE: 1 = 10'-0\"")], "bar_ft_per_paper_inch": None,
      "agrees": None, "delta_pct": None, "pick": {"why": "single", "corroborated": False, "bar_source": None}}
check(verdict_from_app(scale=s5)["state"] == UNCHECKED, "an amber OCR caption with no bar: UNCHECKED")
# abstained-ocr-single: caption refused because an independent bar contradicts it
sug = {"abstained": True, "ft_per_paper_inch": None, "text": None, "source": "ocr", "has_nts": False,
       "candidates": [cap(1.0, "1\" = 1'-0\"")], "bar_ft_per_paper_inch": 40.017, "agrees": None,
       "delta_pct": None, "pick": {"why": "abstained-ocr-single", "values_refused": [1.0],
                                   "bar_source": "psm6-zoom-ticks"}}
v = verdict_from_app(suggestions=sug)
check(v["state"] == CONFLICT and sorted(c["value"] for c in v["choices"]) == [1.0, 40.017],
      f"refused OCR caption vs the bar: CONFLICT, the bar is offered as a choice ({v['why']})")
# abstained-ocr: rescued captions only
sug2 = {"abstained": True, "ft_per_paper_inch": None, "text": None, "source": "ocr", "has_nts": False,
        "candidates": [cap(5.0, '1"=5\''), cap(20.0, '1"=20\'')], "bar_ft_per_paper_inch": None,
        "agrees": None, "delta_pct": None, "pick": {"why": "abstained-ocr", "values": [5.0, 20.0]}}
v = verdict_from_app(suggestions=sug2)
check(v["state"] == NONE and len(v["offered"]) == 2, "rescued captions only: NONE, both offered")
check(verdict_from_app()["state"] == NONE, "nothing at all: NONE")
# refused join artefacts are not witnesses
s6 = dict(s, refused=[cap(90.0, '1"= 90\'')])
check(not any(w["value"] == 90.0 for w in witnesses_from_app(scale=s6)),
      "a refused join artefact (`refused`) is never a witness")
# an unknown source is loud
check(raises(lambda: witnesses_from_app(scale={"ft_per_paper_inch": 20.0, "source": "magic"})),
      "an unknown scale source raises instead of guessing a kind")
# profile fit: both axes trustworthy, else no witness
prof_ok = {"h_ft_per_in": 39.998, "station": {"max_resid_ft": 0.3}, "elevation": {"max_resid_ft": 0.2}}
prof_bad = {"h_ft_per_in": 39.998, "station": {"max_resid_ft": 0.3}, "elevation": {"max_resid_ft": 1.7}}
prof_none = {"h_ft_per_in": 39.998, "station": {"max_resid_ft": 0.3}, "elevation": None}
check(witnesses_from_app(profiles=[prof_ok]) == [],
      "the profile fit is OFF by default (ruling 2): no witness from it")
check([w["kind"] for w in witnesses_from_app(profiles=[prof_ok], use_profile=True)] == ["profile_axis"],
      "with use_profile, a profile with both axes trustworthy is a witness")
check(witnesses_from_app(profiles=[prof_bad, prof_none], use_profile=True) == [],
      "a profile with an untrustworthy or missing elevation axis is not")
# NAMED CASES FOR THE PROFILE WITNESS (ruling 2: kept for the day plan and profile views are told apart).
# A HALF-SIZE PRINT: compound caption 20 (unranked), profile labels 39.998;
# FACTS A2 truth for this PDF 40 - the profile fit catches it.
s7 = {**cap(20.0, '1" = 20\''), "source": "text", "has_nts": False,
      "candidates": [cap(20.0, '1" = 20\''), cap(5.0, '1" = 5\'')], "bar_ft_per_paper_inch": None,
      "agrees": None, "delta_pct": None, "pick": {"why": "unranked", "reason": "compound caption"}}
check(verdict_from_app(scale=s7)["state"] == UNCHECKED, "a half-size print: compound caption alone: UNCHECKED")
check(verdict_from_app(scale=s7, profiles=[prof_ok])["state"] == UNCHECKED,
      "a half-size print with its profile fit passed but the witness OFF: still UNCHECKED")
check(verdict_from_app(scale=s7, profiles=[prof_ok], use_profile=True)["state"] == CONFLICT,
      "a half-size print with the profile witness ON: CONFLICT (the true 2x catch)")
# A PLAN-AND-PROFILE SHEET: plan caption 60 governs (also prints 20, 80); the profile's own H fit is
# 80.0157. FACTS A2: the plan IS 60 - with the profile witness on this is a FALSE conflict.
s8 = {**cap(60.0, "1 in = 60 ft"), "source": "text", "has_nts": False,
      "candidates": [cap(60.0, "1 in = 60 ft"), cap(20.0, "1 in = 20 ft"), cap(80.0, "1 in = 80 ft")],
      "bar_ft_per_paper_inch": None, "agrees": None, "delta_pct": None,
      "pick": {"why": "unranked", "reason": "compound caption"}}
prof80 = {"h_ft_per_in": 80.0157, "station": {"max_resid_ft": 0.2}, "elevation": {"max_resid_ft": 0.2}}
v = verdict_from_app(scale=s8, profiles=[prof80])
check(v["state"] == UNCHECKED and v["value"] == 60.0,
      "a plan-and-profile sheet with the profile witness OFF: UNCHECKED at 60 (right)")
check(verdict_from_app(scale=s8, profiles=[prof80], use_profile=True)["state"] == CONFLICT,
      "a plan-and-profile sheet with the profile witness ON: a false CONFLICT (why it is off)")

# person from the review record
check(person_from_review(None) is None and person_from_review({"set_by": "auto", "ft_per_paper_inch": 20}) is None,
      "no review pick, or an auto scale: no person")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 8.0, "set_at": "2026-10-01T09:00:00"})
check(p == {"value": 8.0, "how": "typed", "who": None, "when": "2026-10-01T09:00:00"},
      "a reviewer pick reads as typed (the record cannot tell typed from picked); who is not recorded today")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 20.0, "basis": "engine-suggestion"})
check(p["how"] == "picked", "an accepted engine suggestion (an old record) reads as picked")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 26.6667,
                        "text": "24' over a measured line"})
check(p["how"] == "calibrated", "the Measure tool's 'set scale' text reads as calibrated")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 5.0, "text": '1" = 5\'',
                        "candidates": [cap(20.0, '1" = 20\''), cap(5.0, '1" = 5\'')]})
check(p["how"] == "picked", "picking another printed caption (an old record) reads as picked")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 20.0, "text": "1 in = 20 ft",
                        "candidates": [cap(20.0, "1 in = 20 ft")], "set_how": "confirmed", "set_by_user": "kim"})
check(p["how"] == "confirmed" and p["who"] == "kim", "a record of this build says how and who itself")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 20.0, "set_how": "guessed"})
check(p["how"] == "typed", "an unknown stored how falls back to the inference, never to confirmed")
check(person_from_review({"retired": True, "ft_per_paper_inch": 20.0}) is None,
      "a tombstone is never a person (ruling 10)")
# ruling 9: a person's value outlives a re-detect that reads another number, and the new reading is listed
v = verdict_from_app(scale={**cap(40.0, "1 in = 40 ft"), "source": "text", "candidates": [cap(40.0, "1 in = 40 ft")],
                     "bar_ft_per_paper_inch": None, "pick": {"why": "single"}},
                     review={"set_by": "reviewer", "ft_per_paper_inch": 20.0, "set_how": "typed"})
check(v["state"] == CONFIRMED and v["value"] == 20.0 and [w["value"] for w in v["disagree"]] == [40.0],
      "the review wins after a re-detect reads 40; the 40 is listed as disagreeing (ruling 9)")
p = person_from_review({"set_by": "reviewer", "ft_per_paper_inch": 30.0, "text": '1" = 30\'',
                        "candidates": [cap(20.0, '1" = 20\'')]})
check(p["how"] == "typed", "a number matching no reading reads as typed")
v = verdict_from_app(scale=s3, dimcheck=dim, review={"set_by": "reviewer", "ft_per_paper_inch": 8.0})
check(v["state"] == CONFIRMED and v["value"] == 8.0,
      "the review record wins over the structural sheet's conflict: CONFIRMED at 8 by a person")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
