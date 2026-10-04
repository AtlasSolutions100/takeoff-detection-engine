"""The approve route's operator-colour precedence and the new fields'
defaults, plus the machine-readable operator-provenance field.

WHY THIS EXISTS

The owner asked to be able to give a black-and-white sheet a readable visual
language ("red for like a fiber line, light yellow for existing contour")
and to have it reach the export. Three things about that are easy to get
silently wrong on the server, and each has a section below:

1. PRECEDENCE. per-chain > per-family > engine. Get the order backwards and
   the operator's per-line correction is swallowed by the family colour with
   no symptom anywhere - the export just quietly disagrees with the screen.
   (The convention-by-label rank that sits between per-family and engine in
   webui/app.js is folded into style_colors by the client, which is the only
   side that holds the book; the server sees two ranks, not three.)

2. THE ENGINE'S VALUE SURVIVES. Colour is a measurement here - chain.py
   grouped these chains by it - so an overridden element has to carry
   `engineColor` beside the chosen one, and an element nobody touched has to
   be byte-for-byte what it always was.

3. DEFAULTS. Every new field is optional. A client that predates them must
   post exactly what it always did and get the same elements back, or an old
   tab open on another machine starts producing different exports mid-set.

Also pinned: _hex_color, which is the only thing standing between a
hand-edited convention file and a broken colour on the face of a report.

Dependency-free like tests/test_gate.py:  python tests/test_approve_colors.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# server.py resolves and creates JOBS_DIR at import time - point it somewhere
# disposable first (same pattern as tests/test_workzone_server.py)
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="approve_colors_")
# ...and keep the training ledger out of the repo's out/ while we drive the
# approve route. The hook is observation-only, but it does write files.
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="approve_colors_led_")

import server  # noqa: E402

CHECKS = []


def ok(cond, msg, got=None, want=None):
    CHECKS.append((bool(cond), msg, got, want))


def eq(got, want, msg):
    CHECKS.append((got == want, msg, got, want))


# ---------------------------------------------------------------- fixture
# One page, three chains in two families. Black linework, because a
# black-and-white sheet is the case the owner is describing.
JOB = "colortestjob"
PAGE = 0
RESULT = {
    "page": {"width_pt": 1224.0, "height_pt": 792.0},
    # a scale in force: since scale verdict S4 an approve that exports lengths needs one (the elements checked here
    # do not depend on it)
    "scale": {"text": "1\" = 20'", "ft_per_paper_inch": 20.0, "source": "text", "near_scale_keyword": True,
              "count": 1, "candidates": [{"text": "1\" = 20'", "ft_per_paper_inch": 20.0}],
              "bar_ft_per_paper_inch": None, "agrees": None, "delta_pct": None, "pick": {"why": "single"}},
    "styles": [
        {"style_id": 3, "class": "solid", "color": [0.0, 0.0, 0.0],
         "width": 0.5, "signature": "solid", "suggested_label": "solid solid",
         "layer": "C-COMM", "length_pt": 300.0, "n": 2, "texture": False},
        {"style_id": 7, "class": "dash1", "color": [0.0, 0.0, 0.0],
         "width": 0.72, "signature": "d18.0 g9.0",
         "suggested_label": "dash1 d18.0 g9.0",
         "layer": "C-TOPO", "length_pt": 100.0, "n": 1, "texture": False},
    ],
    "chains": [
        {"chain_id": 1, "style_id": 3, "class": "solid", "color": [0.0, 0.0, 0.0],
         "width": 0.5, "length_pt": 200.0, "points": [[0, 0], [200, 0]]},
        {"chain_id": 2, "style_id": 3, "class": "solid", "color": [0.0, 0.0, 0.0],
         "width": 0.5, "length_pt": 100.0, "points": [[0, 10], [100, 10]]},
        {"chain_id": 3, "style_id": 7, "class": "dash1", "color": [0.0, 0.0, 0.0],
         "width": 0.72, "length_pt": 100.0, "points": [[0, 20], [100, 20]]},
    ],
}
_d = server._job_dir(JOB)
(_d / f"p{PAGE}_result.json").write_text(json.dumps(RESULT))
# the registry row too: approve refuses a directory with no row (an orphan -
# tests/test_orphan_job.py), exactly as the service would
server.JOBS[JOB] = {"path": str(_d.with_suffix(".pdf")), "pages": 1,
                    "owner": "tester", "status": {}}
# both families are ADDED to the page's takeoff first, as the page does: the takeoff starts empty, and the server holds
# an approve to its record of it (the record's version, and every line style exported: critic empty-1, MAJOR-4)
server.ticks_post(JOB, PAGE, server.TicksBody(version=0, styles=[3, 7]))


def approve(**over):
    """Drive the real route and return the export file it wrote."""
    body = dict(labels={3: "FIBER OPTIC", 7: "EXISTING CONTOUR"},
                image_dpi=300)
    body.update(over)
    body.setdefault("scale_seen", server._scale_stamp(JOB, PAGE))   # the page loaded the record just now
    body.setdefault("ticks_version", server.ticks_get(JOB, PAGE)["version"])   # ...and holds the takeoff record's version
    server.approve(JOB, PAGE, server.ApproveBody(**body))
    return json.loads((_d / f"p{PAGE}_elements.json").read_text())


def by_chain(export):
    """elements keyed by the chain they came from - the route emits them in
    chain order, so zip against the fixture."""
    return {c["chain_id"]: el
            for c, el in zip(RESULT["chains"], export["elements"])}


# ================================================================ 1. DEFAULT
# The no-colour path. Everything an old client posts, nothing more.
base = approve()
eq(len(base["elements"]), 3, "all three chains export with no colour fields")
for el in base["elements"]:
    eq(el["color"], "#000000", "black linework stays black when nobody chose")
    ok("colorSetBy" not in el,
       "an untouched element carries NO colorSetBy — absence is the signal")
    ok("engineColor" not in el,
       "...and no engineColor, because nothing overrode anything")
    ok("styleCreatedBy" not in el, "...and no styleCreatedBy")
# The two summaries are written even when empty, for the same reason
# `overrides` is: [] says "this export knows what an operator colour is and
# has none", which a file predating the field cannot say.
eq(base["operator_created_style_ids"], [],
   "operator_created_style_ids is written EMPTY, not omitted")
eq(base["line_colors"], {"by_style": {}, "by_chain": {}},
   "line_colors is written empty too")

# ============================================================ 2. PRECEDENCE
# per-family paints the whole family...
fam = approve(style_colors={3: "#e03131"})
els = by_chain(fam)
eq(els[1]["color"], "#e03131", "the family colour reaches every line of the family (chain 1)")
eq(els[2]["color"], "#e03131", "...and chain 2")
eq(els[3]["color"], "#000000", "...and NOT the other family")
eq(els[1]["colorSetBy"], "reviewer", "an overridden element says a human set it")
eq(els[1]["engineColor"], "#000000",
   "...and still carries the colour the detector measured, so a reader can "
   "recover the measurement")
ok("colorSetBy" not in els[3], "the untouched family is unmarked")

# ...and a per-CHAIN colour beats its family for that one line. This is the
# rank that is easy to invert, and inverting it is invisible.
both = approve(style_colors={3: "#e03131"}, chain_colors={2: "#ffe066"})
els = by_chain(both)
eq(els[1]["color"], "#e03131", "chain 1 still follows the family")
eq(els[2]["color"], "#ffe066", "chain 2's OWN colour beats its family")
eq(els[2]["engineColor"], "#000000", "...and still names the measurement")
eq(els[3]["color"], "#000000", "the other family is untouched")
# the summary splits them the same way the ranks do
eq(both["line_colors"]["by_style"], {"3": "#e03131"},
   "the by_style summary lists only lines that took the FAMILY colour")
eq(both["line_colors"]["by_chain"], {"2": "#ffe066"},
   "...and the by_chain summary only the line that overrode it")

# a chain colour with no family colour under it still wins over the engine
solo = approve(chain_colors={3: "#1864ab"})
eq(by_chain(solo)[3]["color"], "#1864ab", "a lone per-chain colour beats the engine")
eq(by_chain(solo)[1]["color"], "#000000", "...and touches nothing else")

# CHOOSING THE DETECTED COLOUR IS STILL A DECISION. The stamp keys on whether
# an override was sent, not on whether the strings differ - or an operator who
# deliberately pinned a family to black would be recorded as never having
# decided.
same = approve(style_colors={3: "#000000"})
eq(by_chain(same)[1]["colorSetBy"], "reviewer",
   "picking exactly the detected colour is recorded as a decision")

# ======================================================= 3. GARBAGE IS SAFE
# A colour is decoration, not a quantity: an unparseable one must fall back to
# the engine's own value rather than put a broken string on a report, and it
# must never fail the approve.
junk = approve(style_colors={3: "red", 7: "#ff00cc; drop table"},
               chain_colors={1: "", 2: "#nope!!"})
for cid, el in by_chain(junk).items():
    eq(el["color"], "#000000", f"chain {cid}: an unparseable colour degrades to the engine's")
    ok("colorSetBy" not in el, f"chain {cid}: ...and is not recorded as a decision")
eq(junk["line_colors"], {"by_style": {}, "by_chain": {}},
   "nothing unparseable reaches the summary either")

# _hex_color on its own
eq(server._hex_color("#E03131"), "#e03131", "_hex_color lower-cases")
eq(server._hex_color("e03131"), "#e03131", "_hex_color accepts a bare hex")
eq(server._hex_color("#f00"), "#ff0000", "_hex_color expands the short form")
eq(server._hex_color("  #FFE066  "), "#ffe066", "_hex_color trims")
eq(server._hex_color("red"), None, "_hex_color refuses a colour name")
eq(server._hex_color("#12345"), None, "_hex_color refuses a 5-digit hex")
eq(server._hex_color("#gggggg"), None, "_hex_color refuses non-hex digits")
eq(server._hex_color(None), None, "_hex_color refuses None")
eq(server._hex_color(0xFF0000), None, "_hex_color refuses a number")

# ================================================== 4. OPERATOR PROVENANCE
# The machine-readable twin of the free-text note. Before this field the fact
# that a HUMAN invented a family survived export only as English in `notes`,
# so a downstream reader had to parse prose to tell an operator's family from
# a detected one.
prov = approve(operator_created_style_ids=[7])
els = by_chain(prov)
eq(prov["operator_created_style_ids"], [7],
   "the export names the families a human created")
eq(els[3]["styleCreatedBy"], "reviewer",
   "...and every element of one says so on the element itself")
ok("styleCreatedBy" not in els[1],
   "a detected family's element is NOT marked — the two must never blur")

# A family named in the field that produced no element is dropped: naming it
# would be a claim with no rows behind it.
ghost = approve(operator_created_style_ids=[7, 99])
eq(ghost["operator_created_style_ids"], [7],
   "a created family with nothing in the export is not listed")

# ...and the same holds when the family is excluded from this approve. It is taken out of the takeoff first, as the
# page does: the export is exactly the takeoff record, so an approve that leaves out a style the record holds is
# refused (critic empty-2, MINOR-2)
server.ticks_post(JOB, PAGE, server.TicksBody(version=server.ticks_get(JOB, PAGE)["version"], styles=[3]))
gone = approve(labels={3: "FIBER OPTIC"}, operator_created_style_ids=[7])
eq(gone["operator_created_style_ids"], [],
   "an unlabelled (unticked) created family exports nothing and is not claimed")
server.ticks_post(JOB, PAGE, server.TicksBody(version=server.ticks_get(JOB, PAGE)["version"], styles=[3, 7]))

# ======================================== 5. THE NOTE IS STILL THERE TOO
# The typed field REPLACES nothing: a person reading the report PDF deserves
# the sentence, and the route still copies notes verbatim.
noted = approve(operator_created_style_ids=[7],
                notes=["Line families created by the reviewer on this sheet: "
                       '"EXISTING CONTOUR" (1 line, 100.0 ft).'])
ok(any("created by the reviewer" in n for n in noted["notes"]),
   "the human-readable note survives alongside the machine-readable field")
eq(noted["operator_created_style_ids"], [7], "...and both say the same thing")

# ============================================ 6. NOTHING ELSE MOVED
# The zero-change invariant: with no colour and no provenance sent, every
# element is identical to what the route produced before these fields
# existed, field for field apart from the two new empty declarations.
again = approve()
eq(json.dumps(again["elements"]), json.dumps(base["elements"]),
   "the elements array is unchanged when nobody chose a colour")
for el in again["elements"]:
    eq(sorted(el.keys()),
       sorted(["type", "label", "color", "vertices", "detectedClass", "lengthPt"]),
       "an untouched element carries exactly the fields it always did")

# ---------------------------------------------------------------- report
failures = 0
for cond, msg, got, want in CHECKS:
    if cond:
        print("ok:", msg)
    else:
        failures += 1
        print("FAIL:", msg,
              "" if got is None and want is None
              else "(got %r, want %r)" % (got, want))
if failures:
    sys.exit("%d failure(s)" % failures)
print("all approve-colour tests passed (%d checks)" % len(CHECKS))
