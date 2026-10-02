"""The host <-> cut / fill bridge after the merge audit (PLAN sections 4 and 5):

* the first sheet opened from the host is the grading sheet; a second one starts as "none"
  (opening two sheets used to make two grading sheets and stack them: two islands +4 % -> -11.5 %);
* the scale in force on the sheet is handed to the engine with its source, by rank: the reviewer's
  pick in the host over anything, a scale set in the engine over the host's reading, the reading
  fills an empty one;
* title-block words never "confirm the same frame" for two sheets, and two sheets that meet at a
  match line are not stacked by the same-size guess;
* the coverage warning names the part of the limit with no surface under it.
"""
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("TAKEOFF_JOBS_DIR", tempfile.mkdtemp(prefix="cf-bridge-jobs-"))
os.environ.setdefault("CUTFILL_OUT", tempfile.mkdtemp(prefix="cf-bridge-projects-"))

from cutfill.align import auto_align, text_anchor_pairs  # noqa: E402


def _lab(text, x, y):
    return {"text": text, "bbox": [x, y, x + 8 * len(text), y + 9]}


def _title_block(dx=0.0):
    # the frame's own words (dictionary) and a firm's name / certificate words (not in any
    # dictionary: measured in the right strip of the Clearwater sheets), plus the sheet corners
    words = ["SHEET", "SCALE", "DATE", "DRAWN", "CHECKED", "ENGINEERS", "SURVEY", "LEGEND", "NOTES", "PLAN", "GRADING",
             "COLLIERS", "CHAIRMAN", "SECRETARY", "WWW.SOMEFIRM.COM"]
    return [_lab(w, 2440 + dx, 100 + 20 * i) for i, w in enumerate(words)] + [_lab("ATTY", 60, 500), _lab("AGED", 60, 700),
            _lab("BORDER", 20, 20), _lab("CORNER", 2540, 1700)]


def test_frame_words_are_not_anchors():
    a = _title_block(); b = _title_block()
    assert text_anchor_pairs(a, b) == []


def test_two_sheets_along_a_match_line_are_not_stacked():
    # the same title block on both, a MATCH LINE note on each, no shared drawing words
    a = _title_block() + [_lab("MATCH LINE - SEE SHEET 3.2", 2000, 900)]
    b = _title_block() + [_lab("MATCH LINE - SEE SHEET 3.1", 300, 900)]
    r = auto_align(a, b, same_size=True)
    assert r["source"] == "unaligned", r
    # the old code: identity from the title block. Without the match line the same-size guess stands
    r2 = auto_align(_title_block(), _title_block(), same_size=True)
    assert r2["source"] == "identity" and r2["words"] == []


def test_shared_drawing_words_still_confirm_or_shift_the_frame():
    draw = [("INCENSE", 500, 400), ("LAVENDER", 900, 700), ("BLOOM", 1400, 300), ("MEADOW", 700, 1200)]
    same = _title_block() + [_lab(w, x, y) for w, x, y in draw]
    r = auto_align(same, list(same), same_size=True)
    assert r["source"] == "identity" and len(r["words"]) >= 3, r
    # the neighbour sheet prints the same street names shifted by 600 pt: a translation, not identity
    shifted = _title_block() + [_lab(w, x + 600, y) for w, x, y in draw] + [_lab("MATCH LINE", 100, 900)]
    r = auto_align(same, shifted, same_size=True)
    assert r["source"] == "text" and abs(r["tx"] - 600) < 2 and abs(r["ty"]) < 2, r
    # the same title block on both, a firm's name and certificate words in the right strip, a match
    # line, no drawing word in common: nothing confirms one frame
    a = _title_block() + [_lab("MATCHLINE \"A\" SEE SHEET 3.2", 2000, 900), _lab("EASEMENT", 900, 800)]
    b = _title_block() + [_lab("MATCHLINE \"A\" SEE SHEET 3.1", 300, 900), _lab("PARKWAY", 1300, 600)]
    r = auto_align(a, b, same_size=True)
    assert r["source"] == "unaligned", r


def test_first_sheet_is_the_grading_sheet_and_the_second_starts_as_none():
    import server  # noqa: E402  (TAKEOFF_JOBS_DIR points at a scratch store)
    from cutfill.project import Project

    pid = "job_bridgetest"
    p = Project.create(name="bridge", pid=pid)
    server._cutfill._projects[pid] = p
    # the first sheet opened: grading; the second: none; the operator's later choice stands
    for key, want in (("d0p3", "both"), ("d0p4", "none")):
        ps = p.page_settings(key)
        if not ps.get("host_seen"):
            ps["host_seen"] = True
            others = any((v or {}).get("role") in ("existing", "proposed", "both", "grading")
                         for k, v in p.data["pages"].items() if k != key)
            ps["role"] = "none" if others else "both"
        assert ps["role"] == want, (key, ps["role"])
    # and the route's own code path, on a job that does not exist, refuses cleanly
    try:
        server.cutfill_open("no-such-job", server.CutfillOpenBody(page=0))
        assert False, "expected a 404"
    except Exception as e:  # HTTPException
        assert getattr(e, "status_code", None) == 404


def test_scale_rank():
    """The hand-off's decision table (server._cutfill_scale_handoff), the function the route calls."""
    import server
    auto = {"ft_per_paper_inch": 50.0, "set_by": "auto"}
    picked = {"ft_per_paper_inch": 50.0, "set_by": server._BY_REVIEWER}
    assert server._cutfill_scale_handoff(None, None, auto) == (50.0, "read in review", None)      # empty: the reading fills it
    assert server._cutfill_scale_handoff(40.0, "typed", auto) is None                            # set in the engine: stands
    got = server._cutfill_scale_handoff(40.0, "typed", picked)                                  # the reviewer's pick wins, and says so
    assert got[0] == 50.0 and got[1] == "confirmed in review" and "replaces the one set here" in got[2] and "40" in got[2]
    assert server._cutfill_scale_handoff(50.0, "read in review", picked) == (50.0, "confirmed in review", None)  # same value: no note
    # the reviewer's pick of the number the operator typed in the engine (the Scale card writes its typing into the
    # review): the operator's word stands, its source kept (2026-09-29, round 16)
    assert server._cutfill_scale_handoff(50.0, "typed", picked) is None
    assert server._cutfill_scale_handoff(50.0, "SCALE : 1\" = 50'", picked) is None
    assert server._cutfill_scale_handoff(None, None, None) is None                              # no scale on the sheet
    assert server._cutfill_scale_handoff(None, None, {"ft_per_paper_inch": 0}) is None
    # a scale the hand-off itself put there earlier, from another sheet, has no rank: the grading
    # sheet opened now brings its own (a utility sheet's 1" = 20' stood over a 1" = 50' sheet and
    # the volumes came out 6.25x low - the independent check, 2026-09-27)
    got = server._cutfill_scale_handoff(20.0, "read in review", auto)
    assert got[0] == 50.0 and got[1] == "read in review" and "handed over from another sheet" in got[2] and "20" in got[2], got
    got = server._cutfill_scale_handoff(20.0, "confirmed in review", auto)
    assert got[0] == 50.0 and "handed over from another sheet" in got[2], got
    # a printed scale the operator confirmed in the engine (its source is the printed text) stands
    assert server._cutfill_scale_handoff(40.0, "SCALE : 1\" = 40'", auto) is None


def test_first_sheet_must_read_as_a_grading_sheet():
    """The words a sheet prints give ADVICE, never a role: 15 of the corpus's 36 grading pages
    score under 8 on the grading words and 18 print none of them as text (checked 2026-09-27),
    so a rule that decided the role from them called real grading sheets non-grading."""
    import json
    import server
    jid = "bridge-triage-test"
    d = server._job_dir(jid)
    d.mkdir(parents=True, exist_ok=True)
    (d / "triage.json").write_text(json.dumps({"version": 1, "pages": [
        {"page": 0, "sheet_no": "C2", "hits": [["UTILITY", 6, 12], ["WATER", 5, 5], ["SEWER", 4, 4]]},
        {"page": 1, "sheet_no": "3.1", "hits": [["GRADING", 1, 3], ["UTILITY", 6, 12]]},
        {"page": 2, "sheet_no": "C4.0", "hits": []}]}), encoding="utf-8")
    h = server._cutfill_sheet_hint(jid, 0)
    assert h and "utility" in h and "grading sheet" in h, h  # utility words, no grading word: advice
    assert server._cutfill_sheet_hint(jid, 1) is None         # one grading word: nothing is said
    assert server._cutfill_sheet_hint(jid, 2) is None         # no words read: nothing claimed
    assert server._cutfill_sheet_hint(jid, 7) is None         # no such page
    assert server._cutfill_sheet_hint("no-such-job-at-all", 0) is None


def test_compute_checks_the_scale_in_force_against_the_sheet():
    from cutfill.project import scale_check
    sheet = [("d0p1", "3.1", 50.0, 'SCALE : 1" = 50\'')]
    # handed over from another sheet and disagreeing with this sheet's print: refused
    err, w = scale_check(20.0, "read in review", "d0p0", sheet)
    assert err and "another sheet" in err and "50" in err and "20" in err, err
    err, w = scale_check(20.0, "confirmed in review", "d0p0", sheet)
    assert err and "another sheet" in err
    # read (by nobody confirmed) on this very sheet, and the engine's own reading disagrees: refused
    err, w = scale_check(20.0, "read in review", "d0p1", sheet)
    assert err and "two readings" in err
    # a set scale with no source is the operator's own (typed): said, not refused
    err, w = scale_check(20.0, None, None, sheet)
    assert err is None and len(w) == 1
    # the reviewer confirmed it on this sheet, or the operator typed it: said, not refused
    err, w = scale_check(20.0, "confirmed in review", "d0p1", sheet)
    assert err is None and len(w) == 1 and "square of the scale" in w[0]
    err, w = scale_check(20.0, "typed", None, sheet)
    assert err is None and len(w) == 1
    # agreement (within 1 %), or a sheet that prints no scale: nothing to say
    assert scale_check(50.0, "read in review", "d0p0", sheet) == (None, [])
    assert scale_check(50.2, "typed", None, sheet) == (None, [])
    assert scale_check(20.0, "read in review", "d0p0", [("d0p1", "3.1", None, "")]) == (None, [])


def test_coverage_warning_text():
    from cutfill import project as pj
    src = open(pj.__file__, encoding="utf-8").read()
    assert "the surfaces cover" in src and "left out of the volumes" in src


if __name__ == "__main__":
    test_frame_words_are_not_anchors()
    test_two_sheets_along_a_match_line_are_not_stacked()
    test_shared_drawing_words_still_confirm_or_shift_the_frame()
    test_first_sheet_is_the_grading_sheet_and_the_second_starts_as_none()
    test_scale_rank()
    test_first_sheet_must_read_as_a_grading_sheet()
    test_compute_checks_the_scale_in_force_against_the_sheet()
    test_coverage_warning_text()
    print("ok")
