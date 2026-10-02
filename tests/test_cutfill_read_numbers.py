"""READING THE PRINTED HEIGHTS (2026-09-27, Home 2 Suites C3: six of the seven printed "5991"
were misread, a "5981" read 5984 gave its line that height, and the live checker and the second
critic both found it):

1. the word's LETTERS: in outline lettering an open stroke in the word is the line the word is
   printed on; it stays the word's own stroke (kept out of the line joining) and is not shown to
   the OCR;
2. the word's ANGLE is where its letters stand lowest, not the main axis of its points (slanted
   lettering turns the axis);
3. a word is never dropped by either step (which strokes are words decides which are lines);
4. a number is read more than once and the reads vote; a tie stays with the first read;
5. a word the text layer holds is not read a second time from its strokes.

    python tests/test_cutfill_read_numbers.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from cutfill import glyphs as G  # noqa: E402
from cutfill import ocr  # noqa: E402
from cutfill.ingest import Stroke  # noqa: E402
from cutfill.project import _same_word  # noqa: E402

fails = 0


def check(ok, what):
    global fails
    print(("ok: " if ok else "FAIL: ") + what)
    if not ok:
        fails += 1


def stroke(idx, pts, closed=False, also_filled=False, gray=0.5, width=0.38):
    return Stroke(pts=np.asarray(pts, dtype=float), color=(gray, gray, gray), width=width, layer="", closed=closed,
                  dash=False, opacity=1.0, filled=False, idx=idx, also_filled=also_filled)


def turn(pts, deg, about=(0.0, 0.0)):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    P = np.asarray(pts, dtype=float) - about
    return np.column_stack([P[:, 0] * c - P[:, 1] * s, P[:, 0] * s + P[:, 1] * c]) + about


def digit(x, slant=0.35, w=4.0, h=9.0, n=24):
    """One slanted outline 'digit': a closed loop, w wide and h high, its top pushed to the right
    (italic). Standing on the baseline y = 0, reaching y = -h (the sheet's y runs down)."""
    t = np.linspace(0, 2 * math.pi, n, endpoint=False)
    px = x + w / 2 + (w / 2) * np.cos(t)
    py = -h / 2 + (h / 2) * np.sin(t)
    px = px + slant * (-py)                      # the higher, the further right
    return np.column_stack([px, py])


# ---- 2. the angle: where the letters stand lowest -----------------------------------------------------------
word = [digit(0), digit(6), digit(12), digit(18)]
P = np.vstack(word)
q = P - P.mean(axis=0)
w_, v_ = np.linalg.eigh(q.T @ q / len(q))
axis = math.degrees(math.atan2(v_[:, 1][1], v_[:, 1][0]))
axis = (axis + 90) % 180 - 90
ang, h_low, h_axis = G.lean_angle(q, math.radians(axis))
check(abs(axis) > 2.0, f"the main axis of four slanted digits on a level baseline is off the level: {axis:+.1f} degrees")
check(abs(math.degrees(ang)) <= 0.5 and h_low < h_axis and abs(h_low - 9.0) < 0.2,
      f"the letters stand lowest on the level: {math.degrees(ang):+.1f} degrees, {h_low:.2f} pt high (along the axis: {h_axis:.2f})")
for deg in (25.0, -40.0, 80.0):
    Pt = turn(P, deg)
    qt = Pt - Pt.mean(axis=0)
    wt, vt = np.linalg.eigh(qt.T @ qt / len(qt))
    ax = math.atan2(vt[:, 1][1], vt[:, 1][0])
    a2, _, _ = G.lean_angle(qt, ax)
    off = (math.degrees(a2) - deg + 90) % 180 - 90
    check(abs(off) <= 0.5, f"the same word printed at {deg:+.0f} degrees is read at {math.degrees(a2):+.1f} ({off:+.1f} off)")

# ---- 1 / 3. the letters of a word printed in its line ---------------------------------------------------------
base = 21.0                                                     # the contour runs at 21 degrees; the word along it
glyph = [stroke(100 + k, turn(d, base), closed=True, also_filled=True) for k, d in enumerate(word)]
dash_pts = [[(-11.0, -4.5), (-2.0, -4.5)], [(2.0, -4.5), (11.0, -4.5)], [(15.0, -4.5), (24.0, -4.5)], [(28.0, -4.5), (37.0, -4.5)]]
dashes = [stroke(200 + k, turn(d, base)) for k, d in enumerate(dash_pts)]
cl = G.find_text_clusters(glyph + dashes)
check(len(cl) == 1, f"one word: {len(cl)}")
c = cl[0]
inside = {201, 202}                                             # the two dashes that run through the digits
check(set(c.stroke_idx) >= {100, 101, 102, 103} | inside, f"the dashes through the word stay the word's own strokes: {sorted(c.stroke_idx)}")
check(c.letter_idx is not None and sorted(c.letter_idx) == [100, 101, 102, 103], f"its letters are the four outlines: {c.letter_idx}")
off = (math.degrees(c.angle) - base + 90) % 180 - 90
check(abs(off) <= 0.75, f"and it stands at the angle of its baseline: {math.degrees(c.angle):.1f} (the line runs at {base:g})")
check(abs(c.height - 9.0) < 0.4 and 20.0 < c.length < 27.0, f"its box is the letters': {c.length:.1f} x {c.height:.1f} pt")
seen = []
real = ocr.ImageDraw.Draw


def spy(cluster):
    img = ocr.render_cluster(cluster, {s.idx: s for s in glyph + dashes}, 300)
    return img


img_word = spy(c)
img_all = spy(SimpleNamespace(angle=c.angle, center=c.center, length=c.length, height=c.height, stroke_idx=c.stroke_idx))
mid = img_word.shape[0] // 2
check((img_word[mid] == 0).sum() < (img_all[mid] == 0).sum(), "the OCR is shown the letters: the row of the dash is blacker with every stroke drawn than with the letters")
old = SimpleNamespace(angle=c.angle, center=c.center, length=c.length, height=c.height, stroke_idx=c.stroke_idx)       # a reading kept from before
check(ocr.render_cluster(old, {s.idx: s for s in glyph + dashes}, 300).shape == img_all.shape, "a word of a reading kept from before (no such field) is drawn as it was")
# a stroke font: open strokes ARE the letters
sf = [stroke(300 + k, turn([(6.0 * k, 0.0), (6.0 * k + 1.5, -9.0), (6.0 * k + 3.5, -4.0), (6.0 * k + 4.5, -9.0), (6.0 * k + 4.0, 0.0)], 10.0))
      for k in range(4)]
cs = G.find_text_clusters(sf)
check(len(cs) == 1 and cs[0].letter_idx is None and sorted(cs[0].stroke_idx) == [300, 301, 302, 303], "in a stroke font every stroke is a letter, as before")
# never dropped: letters too small to be a word leave the word as the main axis measured it
tiny = [stroke(400 + k, turn(digit(3.0 * k, w=2.0, h=2.6, n=10), 0.0), closed=True, also_filled=True) for k in range(4)]
bar = [stroke(451, [(3.0, -3.4), (9.0, -3.4)])]                 # a stroke over the letters: with it the word is 3.5 pt high
ct = G.find_text_clusters(tiny + bar)
check(len(ct) == 1 and set(ct[0].stroke_idx) == {400, 401, 402, 403, 451} and ct[0].letter_idx is None and ct[0].height >= 3.0,
      f"a word whose letters alone are under 3 pt high is kept, whole, as it was measured: {[(c.stroke_idx, c.letter_idx, round(c.height, 1)) for c in ct]}")
loops = [stroke(500, [(2.0, 0), (2.4, -4.5), (3.5, -9.0)])] + [stroke(501 + k, digit(5.0 + 6.0 * k, slant=0.0), closed=True) for k in range(3)]     # "1000" in a stroke font
c4 = G.find_text_clusters(loops)
check(len(c4) == 1 and c4[0].letter_idx is None and 500 in c4[0].stroke_idx,
      "closed loops that are only stroked are a stroke font's zeros: the open 1 beside them stays a letter")

# ---- 4. a number is read more than once ------------------------------------------------------------------------
calls = []


def fake_batch(reads):
    it = iter(reads)

    def run(patches, psm=7, extra=None, workers=1):
        out = []
        for _ in patches:
            t, cf = next(it) if extra is None else ("", 0.0)
            out.append([{"text": t, "conf": cf, "left": 0}] if t else [])
        calls.append(len(patches))
        return out
    return run


def second_look(first, more, numeric=True, other_side=None):
    cl_ = SimpleNamespace(id=7, angle=0.3, center=(10.0, 10.0), length=24.0, height=9.0, stroke_idx=[100, 101, 102, 103], letter_idx=None)
    reads = {(7, False): [ocr._read_score(first[0], first[1], numeric, False), first[0], first[1], numeric]}
    if other_side is not None:
        reads[(7, True)] = [ocr._read_score(other_side[0], other_side[1], False, True), other_side[0], other_side[1], False]
    keep_r, keep_b = ocr.render_cluster, ocr.run_batch
    ocr.render_cluster = lambda *a, **k: np.full((40, 120), 255, dtype=np.uint8)
    ocr.run_batch = fake_batch(more)
    calls.clear()
    try:
        ocr._second_look([cl_], {}, 300, reads, 1)
    finally:
        ocr.render_cluster, ocr.run_batch = keep_r, keep_b
    return reads[(7, False)][1], reads[(7, False)][2]


t, cf = second_look(("5980", 74.0), [("5990", 97.0), ("5990", 95.0), ("5990", 96.0)])
check(t == "5990" and abs(cf - 96.0) < 0.01, f"read 5980 once and 5990 three times: it is 5990, with the confidence of the reads that agree ({t}, {cf:.0f})")
t, cf = second_look(("5991", 96.0), [("5991", 90.0), ("5997", 80.0), ("5991", 85.0)])
check(t == "5991" and cf == 96.0, "three reads of four agree with the first: it stands as it was")
t, cf = second_look(("5997", 84.0), [("5991", 96.0), ("5997", 70.0), ("5991", 97.0)])
check(t == "5997", f"two against two: a tie stays with the first read ({t})")
t, cf = second_look(("5994", 68.0), [("5991", 96.0), ("5991", 95.0), ("5997", 70.0)])
check(t == "5991", f"two reads agree against the first and another: the two ({t})")
t, cf = second_look(("5990", 97.0), [("", 0.0), ("", 0.0), ("", 0.0)])
check(t == "5990", "reads that gave nothing take nothing away")
t, cf = second_look(("5990", 97.0), [("FG", 90.0), ("FG", 90.0), ("FG", 90.0)])
check(t == "5990", "a read that is no number has no vote on a number")
t, cf = second_look(("5990", 97.0), [("5991 FG", 90.0), ("5991 FG", 90.0), ("5991 FG", 90.0)])
check(t == "5990", "nor has a look that reads a number with a tag: a number's looks are numbers")

# ---- 4a. a number with its tag is a height too: the looks are counted by what they say ----------------------------
from cutfill.labels import clean_read, parse_text  # noqa: E402

check(ocr._meaning("92.82 H.P.") == ocr._meaning("92.82 HP.") == (92.82, "HP") and ocr._meaning("92.82 H.-P.") is None,
      f"'92.82 H.P.' and '92.82 HP.' say one thing, '92.82 H.-P.' says nothing: {ocr._meaning('92.82 H.P.')}, {ocr._meaning('92.82 H.-P.')}")
check(ocr._meaning("4.50%") is None and ocr._meaning("1\" = 20'") is None and ocr._meaning("12+50") is None,
      "a slope, a scale and a station are no heights: they have no vote")
t, cf = second_look(("92.82 H.-P.", 75.0), [("92.82 H.P.", 93.0), ("92.82 H.P.", 90.0), ("92.82 HP.", 70.0)], numeric=False)
check(t == "92.82 H.P." and abs(cf - (93.0 + 90.0 + 70.0) / 3) < 0.01,
      f"a first look that says nothing and three that say 92.82 HP: the best of the three, with their confidence ({t!r}, {cf:.0f})")
check(parse_text(clean_read(t))["kind"] == "spot" and parse_text(clean_read(t))["tag"] == "HP", "and it is a spot grade")
t, cf = second_look(("92.82 H.-P.", 75.0), [("92.82 H.P.", 93.0), ("92.62 H.P.", 90.0), ("", 0.0)], numeric=False)
check(t == "92.82 H.-P.", f"one look alone does not overrule the first read ({t!r})")
t, cf = second_look(("92.35 x", 65.0), [("92.35", 90.0), ("92.35", 92.0), ("92.35 x", 60.0)], numeric=False)
check(t == "92.35", f"two looks that read the number without the mark beside it: the number ({t!r})")
t, cf = second_look(("92.46 FG", 70.0), [("92.48 FG", 93.0), ("92.46 FG", 90.0), ("92.48 FG", 70.0)], numeric=False)
check(t == "92.46 FG", f"two against two: a tie stays with the first read ({t!r})")
n0 = len(calls)
t, cf = second_look(("OUTLET PIPE WITHIN BOX 14, SEE DETAIL", 90.0), [("x", 1.0)] * 3, numeric=False)
check(t.startswith("OUTLET") and not calls, "a sentence is not looked at again")
t, cf = second_look(("SIDEWALK", 90.0), [("x", 1.0)] * 3, numeric=False)
check(not calls, "nor a word with no number in it")
t, cf = second_look(("d'H 28.26", 40.0), [("x", 1.0)] * 3, numeric=False, other_side=("92.82 H.P.", 93.0))
check(calls == [3, 3], f"nor the side of a word that reads worse than its other side: three more pictures, of the better side ({calls})")
t, cf = second_look(("1.83%", 95.0), [("1.834", 90.0)] * 3, numeric=False)
check(t == "1.83%" and not calls, f"a slope is not looked at again: the read says what the word is ({t!r})")


def rows(text, conf=90.0):
    return [{"text": text, "conf": conf, "left": 0}] if text else []


check(ocr._pick_read(rows("1.83%", 95.0), rows("1.834")) == ("1.83%", 95.0, False),
      f"a slope keeps its percent sign whatever the digits-only read makes of it: {ocr._pick_read(rows('1.83%', 95.0), rows('1.834'))}")
check(ocr._pick_read(rows("88:93", 96.0), rows("88393", 97.0)) == ("88.93", 97.0, True),
      f"a colon between the feet and the hundredths is the decimal point: {ocr._pick_read(rows('88:93', 96.0), rows('88393', 97.0))}")
check(ocr._pick_read(rows("3:1", 90.0), rows("")) == ("3:1", 90.0, False) and ocr._pick_read(rows("10:1", 90.0), rows(""))[2] is False,
      "a slope ratio (3:1) is no height")
check(ocr._pick_read(rows("S991", 80.0), rows("5991", 90.0)) == ("5991", 90.0, True) and ocr._pick_read(rows("5991", 80.0), rows("5991", 90.0)) == ("5991", 80.0, True),
      "the look-alikes of a stroke font and a plain number read as before")
check(clean_read("~ = 92.46 FG") == "92.46 FG" and clean_read("—~ 92.46 FG") == "92.46 FG",
      f"the marks in front of a number with its tag are not the word's: {clean_read('~ = 92.46 FG')!r}")
check(clean_read("~ = 92.46 XQ") == "~ = 92.46 XQ" and clean_read("(100' PUBLIC ROW)") == "(100' PUBLIC ROW)",
      "with no tag this reader knows, no text loses a character")
check(clean_read("= 92.46") == "92.46" and clean_read("—663") == "663", "a plain number between marks is the number, as before")

# ---- 4b. the read from the other side is kept, and the sheet's range decides --------------------------------------
def read_word(upright, flipped, again_upright=(), again_flipped=()):
    """ocr_text_clusters on one word with the OCR replaced: `upright` / `flipped` = (text, confidence)."""
    cl_ = SimpleNamespace(id=3, angle=1.1, center=(10.0, 10.0), length=24.0, height=9.0, stroke_idx=[1, 2, 3], letter_idx=None,
                          text="", conf=0.0, flipped=False, alt_text="", alt_conf=0.0)
    first = iter([upright, flipped])
    second = iter(list(again_upright) + list(again_flipped))
    state = {"n": 0}

    def run(patches, psm=7, extra=None, workers=1):
        out = []
        src = first if state["n"] < 2 else second
        for _ in patches:
            t, cf = next(src, ("", 0.0)) if extra is None else ("", 0.0)
            out.append([{"text": t, "conf": cf, "left": 0}] if t else [])
        state["n"] += 1
        return out
    keep_r, keep_b = ocr.render_cluster, ocr.run_batch
    ocr.render_cluster = lambda *a, **k: np.full((40, 120), 255, dtype=np.uint8)
    ocr.run_batch = run
    try:
        ocr.ocr_text_clusters(None, 300, [cl_], strokes=[])
    finally:
        ocr.render_cluster, ocr.run_batch = keep_r, keep_b
    return cl_


w = read_word(("0665", 88.0), ("5990", 97.0))
check(w.text == "0665" and w.flipped is False and w.alt_text == "5990" and w.alt_conf == 97.0,
      f"a word that reads as a number from both sides keeps both reads: {w.text!r} / from the other side {w.alt_text!r}")
w = read_word(("FG", 90.0), ("5990", 97.0))
check(w.text == "5990" and w.flipped is True and w.alt_text == "", f"one side a word, the other a number: the number, and nothing kept beside it ({w.text!r}, {w.alt_text!r})")
from cutfill.labels import make_label, upright_heights  # noqa: E402


def lab(lid, text, source="glyph", turned_=None, angle=67.0):
    l = make_label(lid, 0, text, (100.0 + 30 * lid, 100.0, 120.0 + 30 * lid, 110.0), angle, source, 90.0, 0.0)
    l.turned = turned_
    return l


sheet = [lab(k, str(v)) for k, v in enumerate((5984, 5986, 5988, 5989, 5990, 5991, 5992, 5993, 5994, 5996))]
a, b, c = lab(20, "0665", turned_="5990"), lab(21, "6865", turned_="5989"), lab(22, "0665", turned_=None)
got = upright_heights(sheet + [a, b, c])
check([l.id for l in got] == [20, 21] and a.value == 5990.0 and a.text == "5990" and a.turned == "0665" and abs(a.angle - 247.0) < 1e-6
      and b.value == 5989.0, f"on a sheet of 5984 .. 5996 a '0665' whose other side read 5990 is 5990: {a.text} at {a.angle:g} (it read {a.turned!r})")
check(c.value == 665.0, "with no read from the other side, and a 5 among its digits, a number stays as read")

# ---- 5. a word the text layer holds ---------------------------------------------------------------------------
check(_same_word((687, 1205, 704, 1227), (694, 1211, 705, 1226)), "the strokes of a turned word and the text layer's box of it: one word (the text box lies in the strokes' box)")
check(_same_word((100, 100, 130, 110), (99, 99, 131, 111)), "a word's strokes inside the text layer's box: one word, as before")
check(not _same_word((100, 100, 400, 112), (120, 101, 150, 111)), "a short word of the text layer inside a long stroke word's box does not silence the long word")
check(not _same_word((100, 100, 130, 110), (128, 100, 160, 110)), "two words side by side are two words")

print(f"\n{'ALL PASS' if not fails else str(fails) + ' FAILED'}")
sys.exit(1 if fails else 0)
