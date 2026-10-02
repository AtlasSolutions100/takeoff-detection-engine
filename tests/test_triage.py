"""The sheet-triage scorer (server.triage_score), pinned on synthetic text.

Triage is CLASSIFICATION only - it paints attention in the page strip and
never touches a quantity - but a wrong tier still misdirects an operator,
so the rules that came out of measuring three real sets (Barn civil 15p,
Kayden Springs 48p, ellis 54p on 2026-08-30) are pinned here:

  * a page with no text ABSTAINS ("unknown"), never guesses "other";
  * a C sheet number alone is "maybe", never "core" - core demands keyword
    evidence beyond the title block;
  * per-family caps: a word repeated 500 times scores like a word repeated
    5 times (ellis SD1 says FILL five times in structural compaction notes
    and must stay "other");
  * word boundaries: OPPOSITE is not SITE, UPGRADING is not GRADING;
  * the ellis A1.02 shape: an architectural index sheet that name-drops
    GRADING/EXCAVATION/DRAINAGE still lands "other", because the MEP
    vocabulary and the A prefix outweigh the borrowed words;
  * hits is the complete term-by-term computation, strongest first, so the
    UI tooltip can show WHY.

fastapi.testclient is not usable in this venv (see tests/test_calcstate.py),
and triage_score is pure anyway, so this calls it directly.

Dependency-free like tests/test_gate.py:  python tests/test_triage.py
"""

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# server.py resolves and creates JOBS_DIR at import time - point it
# somewhere disposable BEFORE importing (same move as test_calcstate.py).
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="triage_test_")

import server  # noqa: E402

score = server.triage_score


def test_no_text_abstains():
    for text in ("", "   ", "\n\t  \n"):
        r = score(text, None)
        assert r["tier"] == "unknown", r
        assert r["score"] == 0 and r["hits"] == [] and r["words"] == 0
    # even a C sheet number cannot rescue a page with no text: the number
    # comes from the same text layer, so this combination cannot happen
    # live, and if it ever does the honest answer is still "no text"
    r = score("", "C3.0")
    assert r["tier"] == "unknown", r


def test_c_prefix_alone_is_maybe_never_core():
    r = score("PLAN AND PROFILE STA 10+00", "C2.1")
    assert r["tier"] == "maybe", r
    assert r["score"] == server._TRIAGE_CIVIL_PREFIX == 4, r
    assert any("C2.1" in h[0] for h in r["hits"]), r


def test_civil_keywords_plus_c_sheet_is_core():
    # Kayden C5.1 replica (the known utility sheet, engine idx 38)
    txt = ("SANITARY SEWER PLAN SANITARY SEWER UTILITY UTILITY UTILITY "
           "TRENCH TRENCHING EXCAVATION DETAILS")
    r = score(txt, "C5.1")
    assert r["tier"] == "core", r
    # 2x SANITARY(4) + 2x SEWER(4) + 3x UTILITY(6) + 2x TRENCH(4)
    # + 1x EXCAVATION(3) + C prefix(4) = 25
    assert r["score"] == 25, r
    labels = [h[0] for h in r["hits"]]
    assert "SANITARY" in labels and "UTILITY" in labels, labels


def test_divider_page_scores_without_sheet_number():
    # Kayden p37 replica: a section divider with no title block
    r = score("SANITARY SEWER EXCAVATION TRENCH SAFETY", None)
    assert r["tier"] == "core" and r["score"] == 9, r


def test_other_trade_prefix_alone_is_other():
    r = score("STEEL BEAM NOTES", "S1.02")        # a structural detail sheet
    assert r["tier"] == "other", r
    assert r["score"] == server._TRIAGE_OTHER_PREFIX == -6, r
    assert r["kind"] is None, r


# BUILDING PLANS (2026-09-26, audit D38): a floor plan or a foundation plan
# is a sheet Flooring, Count and Structure work on, and was tiered "looks
# like another trade" - Seer Stone A2.1 at -12, the Barn set's foundation
# plan (page 14) at 0. The excavation score is unchanged; the TITLE PHRASE
# names the kind and lifts the tier to "plan", never past "core".
def test_foundation_plan_is_a_plan_not_another_trade():
    r = score("FOUNDATION PLAN NOTES", "S1.02")   # ellis idx 42 replica
    assert r["tier"] == "plan" and r["kind"] == "foundation plan", r
    assert r["score"] == server._TRIAGE_OTHER_PREFIX == -6, r   # the score itself did not move


def test_floor_plan_with_schedules_is_a_plan():
    # Seer Stone A2.1's shape: FLOOR PLAN + FLOORING PLAN titles, a door and
    # a window schedule (-3 each), the A prefix (-6): score -12, tier plan
    r = score("FLOOR PLAN FLOORING PLAN DOOR SCHEDULE WINDOW SCHEDULE", "A2.1")
    assert r["tier"] == "plan" and r["kind"] == "floor plan", r
    assert r["score"] == -12, r


def test_footing_vocabulary_without_a_plan_title_is_not_a_plan():
    # the Barn set's footing-schedule sheet: FOOTING x7, SLAB x10, no plan title
    r = score("FOOTING SCHEDULE FOOTING FOOTING SLAB SLAB SLAB SEE PLAN", "S0-0.4")
    assert r["tier"] == "other" and r["kind"] is None, r


def test_index_sheet_listing_plans_is_not_a_plan():
    # an index lists every sheet title: six or more "<X> PLAN" titles
    txt = ("SITE PLAN GRADING PLAN FLOOR PLAN FOUNDATION PLAN ROOF PLAN "
           "FRAMING PLAN ELECTRICAL PLAN REFLECTED CEILING PLAN")
    r = score(txt, "A0.1")
    assert r["kind"] is None and r["tier"] != "plan", r


def test_civil_core_stays_core_even_with_a_plan_title():
    r = score("GRADING PLAN GRADING EARTHWORK EXCAVATION CONTOURS", "C2.0")
    assert r["tier"] == "core", r


def test_neutral_prefix_adds_nothing():
    base = score("SEWER MANHOLE", None)
    v = score("SEWER MANHOLE", "V210")            # ellis idx 3's prefix
    assert v["score"] == base["score"], (v, base)
    assert v["tier"] == base["tier"]


def test_ellis_a102_shape_stays_other():
    # the arch index sheet that name-drops civil vocabulary (3,413 words
    # live; the counted families replicated here) - measured hits:
    # GRADINGx2 EXCAVATx2 DRAINAGEx2 DRAINx3 CUTx1 WATERLINEx1 HYDRANTx1
    # vs ELECTRICALx3 MECHANICALx3 PLUMBINGx4 HVACx1, sheet A1.02
    txt = ("GRADING GRADING EXCAVATION EXCAVATION DRAINAGE DRAINAGE "
           "DRAIN DRAIN DRAIN CUT WATERLINE HYDRANT "
           "ELECTRICAL ELECTRICAL ELECTRICAL MECHANICAL MECHANICAL "
           "MECHANICAL PLUMBING PLUMBING PLUMBING PLUMBING HVAC")
    r = score(txt, "A1.02")
    assert r["score"] == -5, r      # +23 keywords, -22 MEP, -6 prefix
    assert r["tier"] == "other", r


def test_caps_stop_a_spammed_word():
    r = score("STORM " * 500, None)
    assert r["score"] == 2 * 5, r      # weight 2, cap 5 - not 1000
    assert r["hits"][0][:2] == ["STORM", 500], r["hits"]
    # and the structural-notes shape: FILL many times is still weak
    r2 = score("FILL " * 40, "SD1")
    assert r2["score"] == 1 * 3 + server._TRIAGE_OTHER_PREFIX, r2
    assert r2["tier"] == "other", r2


def test_word_boundaries():
    r = score("OPPOSITE CAMPSITE UPGRADING GRADE 60 REBAR", "C9.9")
    # none of OPPOSITE/CAMPSITE (SITE), UPGRADING (GRADING), GRADE 60
    # (GRADING) may score; only the C prefix does
    assert r["score"] == server._TRIAGE_CIVIL_PREFIX, r
    assert len(r["hits"]) == 1, r["hits"]


def test_tier_thresholds_exact():
    assert score("STORM STORM STORM", None)["score"] == 6      # boundary
    assert score("STORM STORM STORM", None)["tier"] == "core"
    assert score("STORM", None) == {
        "tier": "maybe", "score": 2, "words": 1,
        "hits": [["STORM", 1, 2]], "kind": None}               # boundary
    assert score("CUT", None)["tier"] == "other"               # score 1
    assert score("LOREM IPSUM", None)["tier"] == "other"       # score 0


def test_negative_schedules():
    r = score("DOOR SCHEDULE WINDOW SCHEDULE REFLECTED CEILING PLAN", None)
    assert r["score"] == -9 and r["tier"] == "other", r


def test_hits_are_evidence_sorted_strongest_first():
    r = score("GRADING GRADING CUT SEWER", "C3.0")
    pts = [abs(h[2]) for h in r["hits"]]
    assert pts == sorted(pts, reverse=True), r["hits"]
    for h in r["hits"]:
        assert isinstance(h[0], str) and isinstance(h[1], int) \
            and isinstance(h[2], int), h
    # the strongest term leads: GRADING x2 = +6
    assert r["hits"][0] == ["GRADING", 2, 6], r["hits"]


def test_case_and_whitespace_insensitive():
    a = score("storm   sewer\n\tgrading", "c4.0")
    b = score("STORM SEWER GRADING", "C4.0")
    assert a == b, (a, b)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
