"""Abstained scale readings reach the operator - as SUGGESTIONS, never a pick.

The caption-window OCR rescue (FABLE-002) recovers real scale captions on
sheets whose text is plotted as glyph outlines, and it deliberately ABSTAINS
rather than promoting one: promotion was measured confidently wrong twice in
one evening (Thielen ID-1.04's eaten `0"` -> 40 where the truth is 4; Hurricane
C.3.5, which PRINTS three scales and whose OCR recovered only the key map's
1"=300' - 7.5x wrong as an "uncontested single"). See CLAUDE.md section 5,
fourth shape.

Until 2026-09-01 `ScaleResult.to_dict()` returned None whenever `best` was
None, so an abstained result serialised identically to a sheet that prints
nothing at all and 26 corpus pages carrying real candidates showed the
operator nothing. This pins the fix and, just as hard, its NO-REGRESSION
property.

What is pinned here:

  1. An abstention SERIALISES: `abstained` true, an explicitly-None
     ft_per_paper_inch, and every candidate with its value, where it was read
     (`origin`, `at`) and the engine's own reason (`pick.why`).
  2. Nothing-found still serialises as None. The honest empty state is not
     regressed into an empty list.
  3. A page that READS a scale is untouched, key for key AND byte for byte -
     `origin`/`at` are omitted when unset precisely so this holds.
  4. detect_page's scale boundary: an abstained reading never lands in
     `result["scale"]`, because ~40 sites in webui/app.js and the export's
     `image_dpi / ft_per_paper_inch` read a truthy value there as a scale in
     force. It lands in `result["scale_suggestions"]`.
  5. The server records an accepted suggestion as the third reviewer case,
     matched on the VALUE (never on a browser flag), and says so on the
     export name and on the face of the report PDF.

Pure parts run everywhere:

    python tests/test_scale_suggestions.py

The corpus half needs the plan corpus AND Tesseract; it is skipped with a
message, never silently. It pins the MEASURED 2026-09-01 behaviour of the
three demo sheets used in out/verify/2026-09-01/scale-candidates/.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# server.py resolves and creates JOBS_DIR at import time (same pattern as
# tests/test_workzone_server.py). Point it - and the training ledger - at
# throwaways before the import: a test must never write into the real job
# store or contaminate the ledger.
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="scalesug_jobs_")
os.environ.setdefault("TAKEOFF_LEDGER_DIR",
                      tempfile.mkdtemp(prefix="scalesug_ledger_"))

import server  # noqa: E402
from takeoff_engine.scale_detect import (  # noqa: E402
    ScaleCandidate, ScaleResult)

FAILS = []


def ok(cond, msg):
    if cond:
        print("ok: " + msg)
    else:
        FAILS.append(msg)
        print("FAIL: " + msg)


def eq(got, want, msg):
    ok(got == want, f"{msg} (got {got!r}, want {want!r})")


# --- 1. an abstention serialises, with provenance -------------------------

RESCUED = [
    ScaleCandidate('1"=20\'', 20.0, True, count=2,
                   origin="ocr-caption-window", at=(100.0, 200.0, 140.0, 210.0)),
    ScaleCandidate('1"=5\'', 5.0, True, count=2,
                   origin="ocr-caption-window", at=(400.0, 600.0, 440.0, 610.0)),
]
ABSTAINED = ScaleResult(
    None, RESCUED, False, "ocr",
    pick={"why": "abstained-ocr",
          "reason": "OCR-rescued captions are candidates only, never a pick",
          "values": [5.0, 20.0]})

d = ABSTAINED.to_dict()
ok(d is not None, "an abstained result no longer serialises to nothing")
d = d or {}
eq(d.get("abstained"), True, "it says so: abstained is True")
ok("ft_per_paper_inch" in d and d["ft_per_paper_inch"] is None,
   "ft_per_paper_inch is PRESENT and None - an absence, not a default")
ok("text" in d and d["text"] is None, "text is present and None")
eq(d.get("source"), "ocr", "the source rides along")
eq((d.get("pick") or {}).get("why"), "abstained-ocr",
   "the engine's own reason for not promoting rides along")
eq(len(d.get("candidates") or []), 2, "both survivors are carried")
c0 = (d.get("candidates") or [{}])[0]
eq(c0.get("ft_per_paper_inch"), 20.0, "candidate carries its value")
eq(c0.get("text"), '1"=20\'', "candidate carries the caption as read")
eq(c0.get("origin"), "ocr-caption-window", "candidate carries WHERE FROM")
eq(c0.get("at"), [100.0, 200.0, 140.0, 210.0],
   "candidate carries where on the page it was read")
ok(d.get("agrees") is None and d.get("delta_pct") is None,
   "no corroboration is claimed - neither was measured against a best")

# --- 2. nothing found is still nothing ------------------------------------

eq(ScaleResult(None, [], False, "").to_dict(), None,
   "no reading and no candidates still serialises as None")
eq(ScaleResult(None, [], True, "ocr-unavailable").to_dict(), None,
   "an OCR-unavailable page with no candidates is still None")

# --- 3. a page that READS a scale is untouched ----------------------------

BEST = ScaleCandidate('1" = 40\'', 40.0, True, count=3, governs_pt=1234.5,
                      governs_pct=88.0, sites=3)
READ = ScaleResult(BEST, [BEST], False, "text",
                   bar_ft_per_paper_inch=40.0, agrees=True,
                   pick={"why": "single"})
r = READ.to_dict() or {}
eq(sorted(r.keys()),
   sorted(["text", "ft_per_paper_inch", "near_scale_keyword", "count",
           "governs_pt", "governs_pct", "sites", "source", "has_nts",
           "candidates", "bar_ft_per_paper_inch", "agrees", "delta_pct",
           "pick"]),
   "a page that reads a scale serialises exactly the keys it always did")
ok("abstained" not in r, "and carries no `abstained` key at all")
eq(sorted((r["candidates"][0]).keys()),
   sorted(["text", "ft_per_paper_inch", "near_scale_keyword", "count",
           "governs_pt", "governs_pct", "sites"]),
   "its candidates carry no origin/at - omitted when unset, so the "
   "serialised bytes of an already-reading page cannot move")
eq(ScaleCandidate("x", 1.0, False).to_dict().get("origin", "<absent>"),
   "<absent>", "ScaleCandidate omits origin when it was never set")
eq(ScaleCandidate("x", 1.0, False).to_dict().get("at", "<absent>"),
   "<absent>", "ScaleCandidate omits at when it was never set")

# --- 4. the boundary: an abstention is never a scale in force -------------
# Replays detect_page's routing rule against this module's own output, so
# the two cannot drift: `result["scale"]` must be None for an abstention and
# the payload must land in `result["scale_suggestions"]`.

_dp = (Path(__file__).resolve().parent.parent
       / "takeoff_engine" / "detect_page.py").read_text(encoding="utf-8")
ok('"scale_suggestions": scale_suggestions' in _dp,
   "detect_page emits scale_suggestions in the page result")
ok('if _sc and _sc.get("abstained"):' in _dp
   and "scale_suggestions, _sc = _sc, None" in _dp,
   "detect_page routes an abstained reading OUT of result['scale']")


def route(res_dict):
    """detect_page's boundary, applied to a to_dict() payload."""
    sc, sug = res_dict, None
    if sc and sc.get("abstained"):
        sug, sc = sc, None
    return sc, sug


_s, _g = route(ABSTAINED.to_dict())
ok(_s is None, "an abstained page's scale-in-force is None, as it always was")
ok(_g is not None and len(_g["candidates"]) == 2,
   "and its survivors travel in scale_suggestions")
_s, _g = route(READ.to_dict())
ok(_s is not None and _g is None,
   "a page that reads a scale routes unchanged and offers no suggestions")
_s, _g = route(ScaleResult(None, [], False, "").to_dict())
ok(_s is None and _g is None,
   "a page with nothing on it gets neither - the honest empty state stays")

# --- 5. accepting a suggestion is a recorded reviewer DECISION ------------

SUG_RES = {"scale": None, "scale_suggestions": ABSTAINED.to_dict()}

eq((server._scale_suggestion_for(SUG_RES["scale_suggestions"], 20.0) or {})
   .get("text"), '1"=20\'',
   "the server recognises an accepted suggestion BY VALUE")
eq(server._scale_suggestion_for(SUG_RES["scale_suggestions"], 20.5), None,
   "a number merely close to a suggestion is not that suggestion")
eq(server._scale_suggestion_for(None, 20.0), None,
   "a page with no suggestions matches nothing")
eq(len(server._scale_offered(SUG_RES["scale_suggestions"])), 2,
   "both offered readings are recorded for the training ledger")

_jobs = Path(os.environ["TAKEOFF_JOBS_DIR"]) / "scalesugjob"
_jobs.mkdir(parents=True, exist_ok=True)
det = server._page_scale(
    "scalesugjob", 0, SUG_RES,
    server.ApproveScale(ft_per_paper_inch=20.0, text='1"=20\'',
                        picked_by_reviewer=True, how="typed"))
eq((det or {}).get("set_by"), server._BY_REVIEWER,
   "an accepted suggestion is recorded as a REVIEWER decision, never auto")
eq((det or {}).get("basis"), server._BASIS_SUGGESTION,
   "and its basis says it came from one of the engine's own readings")
eq((det or {}).get("ft_per_paper_inch"), 20.0, "with the value it accepted")
_side = json.loads(
    (_jobs / "p0_scale.json").read_text()) if (_jobs / "p0_scale.json").exists() else {}
eq(_side.get("basis"), server._BASIS_SUGGESTION,
   "the durable sidecar records the basis, so a reload/re-approve agrees")

ok("accepting a reading the engine recovered but would not vouch for"
   in server._scale_name(det or {}),
   "the export NAMES it as an accepted unvouched reading - not as "
   "'the engine read no scale here', which would be false about this sheet")

_lines = server._scale_report_lines({
    "setBy": server._BY_REVIEWER, "ftPerPaperInch": 20.0,
    "basis": server._BASIS_SUGGESTION, "suggestionText": '1” = 20’',
    "name": "x"})
_joined = " ".join(_lines)
# The report writer prints `_ln[:118]`, so a line over that is CUT MID-WORD.
# Measured on the live report before this was wrapped: the sentence stopped
# at "read by character recogniti" - it lost the one thing it exists to say.
ok(all(len(ln) <= 118 for ln in _lines),
   f"every printed line fits the report's 118-char clip "
   f"(longest {max(len(ln) for ln in _lines)})")
# ...and an OCR caption's typographic primes are folded for PRINTING only,
# because the report PDF's base font draws them as empty boxes.
ok('1" = 20\'' in _joined,
   "the caption prints with ASCII marks the report font can actually draw")
ok("”" not in _joined and "’" not in _joined,
   "no typographic prime reaches the printed line")
eq(server._ascii_marks('1” = 20’'), '1" = 20\'',
   "_ascii_marks folds the pair the OCR reader produces")
ok("SET BY THE REVIEWER" in _joined,
   "the printed report still says the reviewer set it")
ok("would not stand behind" in _joined and '1" = 20\'' in _joined,
   "and says the engine recovered that very reading and refused to vouch")
ok("The engine found no scale on this sheet" not in _joined,
   "and does NOT claim the engine found nothing here")

# a reviewer number that matches no suggestion is still plain hand-setting
det2 = server._page_scale(
    "scalesugjob", 1, SUG_RES,
    server.ApproveScale(ft_per_paper_inch=33.0, text='1" = 33\'', how="typed"))
ok((det2 or {}).get("basis") is None,
   "a typed number that matches no suggestion records no suggestion basis")
ok("the engine read no scale here" in server._scale_name(det2 or {}),
   "and reads as hand-set, exactly as before this change")

# --- corpus: the three demo sheets ---------------------------------------

W = Path(r"C:\Users\kobys\OneDrive\Desktop\past\P\Takeoff Automation")
# (relative path, page index, what the page must do)
#   ("abstain", [values])  the rescue's abstention, with the values offered
#   ("reads", value)       a promoted reading within 2% of value
#
# Two rows moved with stage 1b (SPEC 7.4: psm 6 in the bar zoom, and the
# zoom running when the 150-dpi rows were refused or are non-native).
# Both were pinned here as "the engine has nothing to say"; both now read
# their DRAWN bar off tick geometry, and both readings were checked
# against 400-dpi crops of the sheets themselves on 2026-09-02:
#   Hurricane C.3.1 p2 prints `0 40' 80'` over `SCALE: 1" = 40'`
#       - was: abstained, candidate 40.0 from the caption window
#       - now: reads 40.0167 off the bar's ticks (source "graphic-bar")
#   RC Ontario p7 prints `0 5 10 20` over `1" = 10' WHEN PRINTED AT FULL
#       SIZE (24" X 36")`
#       - was: to_dict() None - no reading and no candidates at all
#       - now: reads 10.0 off the bar's ticks
# The Bryce Gurr row is the one this file is really about (two printed
# scales, neither promotable) and is unchanged.
CORPUS = [
    (r"TakeoffPro AI\Plans 2\2026.02.13_Civil Design_HURRICANE STORAGE UNITS.pdf",
     2, ("reads", 40.0)),
    (r"TakeoffPro AI\Plans 2\Approved Construction Set_2-17-26 (2).pdf",
     2, ("abstain", [5.0, 20.0])),
    (r"TakeoffPro AI\Plans\RC Ontario.pdf", 7, ("reads", 10.0)),
]


def _have_tesseract():
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


if not W.exists():
    print(f"SKIP corpus half: plan corpus not found at {W}")
elif not _have_tesseract():
    print("SKIP corpus half: Tesseract is not installed/on PATH")
else:
    import pymupdf
    from takeoff_engine.scale_detect import detect_scale

    for rel, pi, want in CORPUS:
        p = W / rel
        if not p.exists():
            print(f"SKIP {p.name} p{pi}: file not present")
            continue
        doc = pymupdf.open(p)
        got = detect_scale(doc[pi]).to_dict()
        doc.close()
        kind, want = want
        if kind == "reads":
            v = (got or {}).get("ft_per_paper_inch")
            ok(v is not None and abs(v - want) / want <= 0.02,
               f"{p.name} p{pi}: reads its drawn bar (got {v}, want ~{want})")
            ok((got or {}).get("abstained") is None,
               f"{p.name} p{pi}: a promoted reading carries no abstained key")
            continue
        ok(got is not None and got.get("abstained") is True,
           f"{p.name} p{pi}: abstains with candidates carried")
        vals = sorted(round(c["ft_per_paper_inch"], 4)
                      for c in (got or {}).get("candidates") or [])
        eq(vals, want, f"{p.name} p{pi}: the values the operator is offered")
        ok(all(c.get("origin") == "ocr-caption-window"
               for c in (got or {}).get("candidates") or []),
           f"{p.name} p{pi}: every candidate says it was OCR'd off a "
           f"caption window")
        ok(all(isinstance(c.get("at"), list) and len(c["at"]) == 4
               for c in (got or {}).get("candidates") or []),
           f"{p.name} p{pi}: every candidate says where on the page")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED")
    for m in FAILS:
        print("  - " + m)
    sys.exit(1)
print("all checks passed")
