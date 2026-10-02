"""Layer-based evaluation: score detection against the PDF's own CAD layers.

When a plan PDF preserves OCG layer names, every stroke's layer is free
ground truth for two things hand labels would otherwise provide:

  recall  - how much of a layer's stroke ink ended up in emitted chains
            (dropped ink = pruned as junk, stray fragments, or lost)
  purity  - how much of a layer's chained ink lands in its dominant
            (class, signature-bucket) style. A real line layer (water main,
            easement) should resolve to ONE style; low purity means the
            cadence classifier is splitting or mixing it.

Hatch/label layers are marked textural - low recall there is the junk
pruner doing its job, not a failure.

Usage:
  python -m takeoff_engine.eval_layers <pdf> [--page N]
"""

import argparse
from collections import defaultdict

import pymupdf

from .chain import build_chains, _sig_bucket
from .extract import extract_strokes, page_words

TEXTURAL_HINTS = ("HATCH", "-LBL", "TEXT")


def is_textural(layer: str) -> bool:
    """Textural = annotation/hatch decoration. Judged on the LEAF layer
    name: 'X-CORR|O-ALIGN-LOVES CL' is a road corridor xref carrying a REAL
    centerline, while 'P1-PVMT-CORR' (leaf ends in -CORR) is corridor
    surface texture."""
    leaf = (layer or "").split("|")[-1].upper()
    if "NO HATCH" in leaf or "NOHATCH" in leaf:
        return False  # a layer named for NOT being hatch carries real linework
    return any(h in leaf for h in TEXTURAL_HINTS) or leaf.endswith("-CORR")


def _textural_by_geometry(lay_units) -> bool:
    """Texture regardless of the layer's NAME: a fill pattern is thousands
    of tiny strokes in dense crowds (stair set 'Shading|GRAVEL': 14,277 stones,
    median 3.0pt, 9.7 neighbours within 5pt), while real dashed linework
    runs 8pt+ marks with ~1-2 neighbours. Measured: corpus line layers stay
    below 4 mean-neighbours; hatch/stipple sits above 6."""
    import numpy as np
    if len(lay_units) < 200:
        return False
    lens = np.array([u.length for u in lay_units])
    if float(np.median(lens)) > 6.0:
        return False
    from scipy.spatial import cKDTree
    mids = np.array([u.pts.mean(axis=0) for u in lay_units])
    t = cKDTree(mids)
    n5 = t.query_ball_point(mids[:: max(1, len(mids) // 300)], 5.0,
                            return_length=True)
    if float(np.mean(n5)) > 6.0:
        return True
    return False


def _strip_field(lay_units) -> bool:
    """Second texture signature: HATCH STRIPS - uniform-length parallel
    strokes stacked SIDEWAYS. Real dash runs are also uniform and parallel,
    but their marks line up ALONG the shared axis; a hatch field spreads
    perpendicular to it. Measured: C4.0 P1-BLDG strips are all 16.0pt
    (p90/median = 1.00) with lateral spread ~ along spread; straight dashed
    property lines have lateral/along < 0.05 per line group."""
    import numpy as np
    if len(lay_units) < 20:
        return False
    lens = np.array([u.length for u in lay_units])
    med = float(np.median(lens))
    if med < 2.0 or med > 40.0:
        return False
    if float(np.percentile(lens, 90)) > 1.2 * med:
        return False  # mixed lengths: not a machine pattern
    dirs = np.array([u.direction() for u in lay_units])
    # dominant orientation (mod 180): doubled-angle mean
    ang = np.arctan2(dirs[:, 1], dirs[:, 0]) * 2
    R = np.hypot(np.mean(np.cos(ang)), np.mean(np.sin(ang)))
    if R < 0.8:
        return False  # not one shared direction
    mean_ang = np.arctan2(np.mean(np.sin(ang)), np.mean(np.cos(ang))) / 2
    axis = np.array([np.cos(mean_ang), np.sin(mean_ang)])
    mids = np.array([u.pts.mean(axis=0) for u in lay_units])
    rel = mids - mids.mean(axis=0)
    along = rel @ axis
    perp = rel @ np.array([-axis[1], axis[0]])
    sa, sp = float(np.std(along)) + 1e-6, float(np.std(perp))
    return sp / sa > 0.25   # sideways-stacked: a line family stays < 0.05


def _congruence_groups(lay_units):
    """Group strokes by congruence key (bbox + length bucket) - the same
    fingerprint symbols/suggest counts with. Returns {key: [units]}.

    THE LENGTH TERM IS A BUCKET ON PURPOSE - it is a fingerprint, not a
    proximity test, and converting it makes things worse. Both consumers ask
    only "how many strokes share this key", so a bin edge can SPLIT a repeat
    family but can never merge two: it errs toward fewer repeats, i.e. toward
    leaving a layer measurable rather than writing it off as texture, which
    is the safe direction for a construction quantity.

    Measured over 7,583 layer instances on 168 layered pages
    (the client folders + Plans + a contractor folder), bucket vs the same
    grouping done by sorting on length and splitting on a >2pt gap:
      _symbolic_by_congruence   121 layers either way, zero disagreements
      _scatter_glyphs           569 -> 583, i.e. 16 layers move, 15 of them
                                the wrong way
    The 15 the gap-walk newly calls texture include L-BOULDER-MOSS (8,363 pt,
    the layer the standing gate reports as LOST on Res-B civil p3), S-COLS,
    S-BEAM and SECTION - real linework that would silently leave the takeoff
    - and it stops recognising the genuine hatch layer BG-Hatch Stair type 1.
    Single-linkage chaining over-merges here. Do not "fix" this.
    """
    groups = {}
    for u in lay_units:
        bb = u.pts.max(axis=0) - u.pts.min(axis=0)
        key = (round(float(bb[0])), round(float(bb[1])), round(u.length / 4.0))
        groups.setdefault(key, []).append(u)
    return groups


def _symbolic_by_congruence(lay_units) -> bool:
    """A symbol layer is REPEATED CONGRUENT small shapes (tree canopies,
    fixtures, markers) - countable EA, not measurable LF. Symbolic when
    >=50% of the layer's ink sits in congruent groups of >=3 repeats of
    small shapes. Measured: stair-set TREES = congruent canopy glyphs; real
    line layers (contours, walls) have near-zero congruent-repeat ink
    because their mark lengths vary with geometry."""
    if len(lay_units) < 6:
        return False
    total = sum(u.length for u in lay_units) or 1.0
    # CLOSED shapes only, as symbols/suggest counts: grid bubbles and
    # fixtures are closed; uniform OPEN dashes are also congruent-repeat
    # and this test flagged real contour/pipe layers symbolic until the
    # closed gate was restored (caught by the C4.0 control: O-SURFACE and
    # O-PIPE excluded from LF truth, inflating the cert denominator)
    closed = [u for u in lay_units if u.closed]
    rep_ink = 0.0
    for key, us in _congruence_groups(closed).items():
        w, h, _ = key
        if len(us) >= 3 and 2 <= max(w, h) <= 45:
            rep_ink += sum(u.length for u in us)
    return rep_ink / total >= 0.5


def _radial_bursts(lay_units) -> bool:
    """Tree/shrub symbols drawn as RAY BURSTS: many strokes fanning out
    of a shared hub (stair-set TREES: 184 rays, median 21pt, several hubs of
    20+ rays each). Signature: >=8 stroke endpoints within a 4pt ball,
    arriving from >=5 distinct directions - dashes crossing a junction
    gather at most 2-4 directions. Symbolic when >=50% of layer ink
    touches a hub; trees are counted EA, never measured LF."""
    import numpy as np
    if len(lay_units) < 12:
        return False
    from scipy.spatial import cKDTree
    eps, owner, dirs = [], [], []
    for i, u in enumerate(lay_units):
        d = u.direction()
        for e in (u.pts[0], u.pts[-1]):
            eps.append(e)
            owner.append(i)
            dirs.append(d)
    E = np.asarray(eps)
    D = np.asarray(dirs)
    tree = cKDTree(E)
    hub_units = set()
    for idx in range(0, len(E), max(1, len(E) // 400)):
        nb = tree.query_ball_point(E[idx], 4.0)
        if len(nb) < 8:
            continue
        ang = np.degrees(np.arctan2(D[nb][:, 1], D[nb][:, 0])) % 180
        # distinct directions in 20-degree bins
        if len(set((ang // 20).astype(int).tolist())) < 5:
            continue
        for j in nb:
            hub_units.add(owner[j])
    if not hub_units:
        return False
    total = sum(u.length for u in lay_units) or 1.0
    hub_ink = sum(lay_units[i].length for i in hub_units)
    return hub_ink / total >= 0.5


def _scatter_glyphs(lay_units) -> bool:
    """Grass/riprap tick texture: dozens of repeats of the SAME tiny open
    mark scattered across the sheet. Measured: stair-set GRASS = 768 strokes,
    median 3.4pt, congruent in a handful of groups; dashed lines never
    congregate at such tiny lengths in >=20-repeat congruent groups
    because dash length tracks the pattern, not a glyph template."""
    tiny = [u for u in lay_units if u.length < 8.0]
    if len(tiny) < 40:
        return False
    total = sum(u.length for u in lay_units) or 1.0
    rep_ink = 0.0
    for key, us in _congruence_groups(tiny).items():
        if len(us) >= 20:
            rep_ink += sum(u.length for u in us)
    return rep_ink / total >= 0.6


def eval_page(page: pymupdf.Page):
    units = extract_strokes(page)
    # geometry flags come from PRISTINE strokes: build_chains mutates unit
    # geometry in place (twin merges, marker collapse), which can lift a
    # stipple layer's median length past any texture threshold
    _geo_tex = {}
    _by_layer = defaultdict(list)
    for u in units:
        _by_layer[u.layer].append(u)
    # aggregate by LEAF layer name: xrefs replot the same logical layer
    # under several path variants ('24007215.01-X-SITE|P1-WALK' + 'P1-WALK'
    # + a recolored twin), and chains inherit ONE variant - per-path
    # buckets then read a sibling's ink as 0-4% recall (C9.0 P1-WALK:
    # 4.2% by path, 100% coverage in fact). Leaf merging is aggregation
    # only; texturality still respects the X-CORR leaf caveat elsewhere.
    def _leaf(lay):
        return (lay or "").split("|")[-1] or "(none)"
    _geo_sym = {}
    _leaf_units = {}
    for lay, lus in _by_layer.items():
        _leaf_units.setdefault(_leaf(lay), []).extend(lus)
    _by_layer = _leaf_units
    for lay, lus in _by_layer.items():
        _geo_tex[lay] = (_textural_by_geometry(lus) or _strip_field(lus)
                         or _scatter_glyphs(lus))
        _geo_sym[lay] = _symbolic_by_congruence(lus) or _radial_bursts(lus)
    chains = build_chains(units, words=page_words(page))

    per_layer = defaultdict(lambda: {"unit_ink": 0.0, "chain_ink": 0.0,
                                     "styles": defaultdict(float), "n_chains": 0,
                                     "lens": []})
    for u in units:
        if u.color and min(u.color) > 0.92 and not getattr(u, 'on_fill', False):
            continue  # invisible whiteout, excluded from scoring like in build
        if getattr(u, "_absorbed", False):
            continue  # twin copy merged into a survivor - one printed line,
            # one expectation (double-drawn layers scored 50% otherwise)
        per_layer[_leaf(u.layer)]["unit_ink"] += u.length
        per_layer[_leaf(u.layer)]["lens"].append(u.length)

    for c in chains:
        s = per_layer[_leaf(c.layer)]
        ink = getattr(c, "ink", None) or c.length
        s["chain_ink"] += ink
        s["n_chains"] += 1
        s["styles"][(c.klass, _sig_bucket(c.dash_signature))] += ink

    rows = []
    for layer, s in per_layer.items():
        if s["unit_ink"] < 50:
            continue
        recall = min(s["chain_ink"] / s["unit_ink"], 1.0) if s["unit_ink"] else 0.0
        if s["styles"]:
            (dom_class, dom_sig), dom_ink = max(s["styles"].items(), key=lambda kv: kv[1])
            purity = dom_ink / max(s["chain_ink"], 1e-9)
        else:
            dom_class, dom_sig, purity = "-", "-", 0.0
        import numpy as _np
        # symbol layers: every stroke is a glyph-scale mark (culvert
        # markers, fixture symbols) - countable EA via symbols/suggest,
        # not measurable linework; line recall there is expected-low
        symbolic = ((s["lens"] and float(_np.median(s["lens"])) < 20.0
                     and s["unit_ink"] < 2000.0)
                    or _geo_sym.get(layer, False))
        rows.append({
            "layer": layer or "(none)",
            "textural": is_textural(layer)
                        or ("SHADING" in (layer or "").upper().split("|")[-1])
                        or _geo_tex.get(layer, False),
            "symbolic": bool(symbolic),
            "unit_ink": s["unit_ink"],
            "recall": recall,
            "purity": min(purity, 1.0),
            "n_chains": s["n_chains"],
            "dom": f"{dom_class} {dom_sig}",
        })
    rows.sort(key=lambda r: -r["unit_ink"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--page", type=int, default=0)
    args = ap.parse_args()

    doc = pymupdf.open(args.pdf)
    page = doc[args.page]
    units = extract_strokes(page)
    chains = build_chains(units, words=page_words(page))
    audit = phantom_span_audit(chains, units)
    print(f"PHANTOM-SPAN AUDIT: {len(audit['bad'])} of {audit['n']} emitted lines "
          f"sit on far less ink than their own pattern implies")
    for c, meas, exp in sorted(audit["bad"], key=lambda x: -x[0].length)[:10]:
        print(f"   {c.klass:9} len={c.length:8.0f} on-ink={meas:6.1%} "
              f"expected={exp:6.1%} sig='{c.dash_signature}' "
              f"layer='{(c.layer or '(none)')[:28]}'")
    print()
    rows = eval_page(page)
    line_rows = [r for r in rows if not r["textural"] and not r.get("symbolic")]
    if line_rows:
        tot = sum(r["unit_ink"] for r in line_rows)
        w_recall = sum(r["recall"] * r["unit_ink"] for r in line_rows) / tot
        w_purity = sum(r["purity"] * r["unit_ink"] for r in line_rows) / tot
        print(f"LINE LAYERS ink-weighted: recall={w_recall:.1%} purity={w_purity:.1%}\n")
    print(f"{'layer':46} {'ink':>8} {'recall':>7} {'purity':>7} {'n':>5}  dominant")
    for r in rows:
        tag = " [tex]" if r["textural"] else (" [sym]" if r.get("symbolic") else "")
        print(f"{r['layer'][:46]:46} {r['unit_ink']:8.0f} {r['recall']:7.1%} "
              f"{r['purity']:7.1%} {r['n_chains']:5}  {r['dom'][:38]}{tag}")
    # the same name-derived truth the gate scores, for one sheet
    print_derived_metrics(derived_layer_metrics(chains, units))
    doc.close()


# --- per-class milestone metrics -------------------------------------------

# Layers whose true line class is unambiguous (asserted from the legend /
# drawing conventions). Used to score classification accuracy.
TRUTH_CLASSES = {
    "O-SURFACE-EXISTING COMBINED": "dash1",
    "24007215.01-X-SITE|E3-LOTS": "dash1",
    "24007215.01-X-SITE|P1-FENCE": "dash1",
    # utility runs carry a repeating inline mark in every gap - verified by
    # rendering each one: water "- W -", firewater "- FW -", storm swale a
    # flow chevron "- > -" (see _mark_glyph_text_chains for why letters and
    # symbols share one class)
    "24007215.01-X-SITE|P2-WATER-LINE": "dash-text",
    "24007215.01-X-SITE|P2-STORM-SWALE": "dash-text",
    "24007215.01-X-SITE|P2-FIREWATER-LINE": "dash-text",
    "24007215.01-X-SITE|P1-PVMT-CL": "dash2",
}

DASH_CLASSES = ("dash1", "dash2", "dash3", "dash-text")


def class_metrics(chains, units) -> dict:
    """Milestone-facing numbers.

    precision proxy (layered PDFs): a dashed chain living on a textural
    layer (TEXT/LBL/HATCH names) is a false detection - real dashed lines
    do not live on annotation layers.

    classification accuracy: ink on TRUTH_CLASSES layers classified as the
    asserted class.
    """
    per_class = {}
    for klass in DASH_CLASSES + ("solid",):
        cs = [c for c in chains if c.klass == klass]
        total = sum(c.ink or c.length for c in cs)
        # junk = dashed ink on annotation layers, EXCEPT long structured
        # chains - firms draw real boundaries on "TEXT" layers (C8.0 basin
        # boundary on P6-TEXT-STORM), and those are intentional keeps that
        # mirror the engine's own demotion gate
        import numpy as _np
        junk = sum(
            (c.ink or c.length) for c in cs
            if is_textural(c.layer)
            and float(_np.hypot(*(c.pts.max(axis=0) - c.pts.min(axis=0)))) < 300.0
        )
        per_class[klass] = {
            "ink": total,
            "junk_ink": junk,
            "precision": (total - junk) / total if total else None,
            "n": len(cs),
        }

    truth = {}
    for lay, want in TRUTH_CLASSES.items():
        cs = [c for c in chains if c.layer == lay]
        if not cs:
            continue
        total = sum(c.ink or c.length for c in cs)
        good = sum((c.ink or c.length) for c in cs if c.klass == want)
        # ink recall against the layer's raw stroke ink
        lay_units = [u for u in units
                     if u.layer == lay
                     and not getattr(u, "_absorbed", False)
                     and not (u.color and min(u.color) > 0.92 and not getattr(u, 'on_fill', False))]
        lay_ink = sum(u.length for u in lay_units)
        truth[lay] = {
            "want": want, "class_acc": good / total if total else None,
            "recall": min(total / lay_ink, 1.0) if lay_ink else None,
            "coverage": _coverage(lay_units, cs),
        }
    return {"per_class": per_class, "truth_layers": truth}


def _chain_sample_tree(chains, step: float = 2.0):
    """KD-tree of points sampled along every chain. Built once per sheet so
    the any-chain coverage below does not rebuild it per layer."""
    import numpy as np
    from scipy.spatial import cKDTree

    from .chain import _sample_polyline

    samples = []
    for c in chains:
        s, _ = _sample_polyline(np.asarray(c.pts), step=step, max_n=100000)
        if len(s):
            samples.append(s)
    if not samples:
        return None
    return cKDTree(np.vstack(samples))


def _coverage(lay_units, chains, tol: float = 3.0, tree=None):
    """Decoration-aware recall: fraction of the layer's stroke ink whose
    units sit on (within tol of) an emitted chain. Fence X-marks and swale
    arrowheads live ON their line, so a found line covers them - unlike raw
    ink recall, which punishes every decorative stroke as 'missed'.

    Pass ``tree`` (from _chain_sample_tree) to score against a chain set
    that is not the layer's own - that is how the gate separates "we never
    traced this" from "we traced it but filed it under another layer".
    """
    import numpy as np

    if not lay_units or (tree is None and not chains):
        return None
    if tree is None:
        tree = _chain_sample_tree(chains)
        if tree is None:
            return 0.0
    covered = total = 0.0
    for u in lay_units:
        # a decoration counts as covered when any part of it touches the
        # emitted chain: fence X's drawn as offset halves have midpoints
        # ~half a leg off the centerline, but an endpoint at the X center
        # sits ON the line
        probes = (u.pts[len(u.pts) // 2], u.pts[0], u.pts[-1])
        d = min(tree.query(p, distance_upper_bound=tol)[0] for p in probes)
        total += u.length
        if np.isfinite(d):
            covered += u.length
    return covered / total if total else None


def phantom_span_audit(chains: list, units: list, min_len: float = 150.0,
                       tol: float = 1.2, slack: float = 0.65) -> dict:
    """Does every emitted line actually sit on ink the drawing contains?

    This is the money check: a chain that spans open paper reports footage
    for material nobody has to buy. It catches the two ways that happens -
    joining marks across empty space, and welding a line to something it
    only passed near - and it is independent of the layer-based metrics, so
    it still works on PDFs with no CAD layers at all.

    Each chain is scored against ITS OWN cadence, not a flat threshold: a
    dashed line's polyline crosses its own gaps, so `d18 g36` legitimately
    sits on ink only a third of the time, while a solid line should be on
    ink throughout. `ink / length` is that expectation, so a chain is only
    flagged when it covers far less ink than its own pattern implies.

    Returns {"n": audited, "bad": [(chain, measured, expected)]}.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    from .chain import _sample_polyline

    ink = []
    for u in units:
        if u.color and min(u.color) > 0.92 and not getattr(u, 'on_fill', False):
            continue
        s, _ = _sample_polyline(np.asarray(u.pts), step=1.0, max_n=20000)
        if len(s):
            ink.append(s)
    if not ink:
        return {"n": 0, "bad": []}
    tree = cKDTree(np.vstack(ink))
    audited, bad = 0, []
    for c in chains:
        if c.klass == "annotation" or c.length < min_len:
            continue
        audited += 1
        s, _ = _sample_polyline(np.asarray(c.pts), step=2.0, max_n=40000)
        if not len(s):
            continue
        d, _ = tree.query(s, distance_upper_bound=tol)
        frac = float(np.isfinite(d).mean())
        expect = (c.ink / c.length) if (c.ink and c.length) else 1.0
        expect = min(max(expect, 0.05), 1.0)
        if frac < slack * expect:
            bad.append((c, frac, expect))
    return {"n": audited, "bad": bad}


def print_class_metrics(m: dict) -> None:
    print(f"\n{'class':10} {'chains':>7} {'ink':>9} {'precision':>10}")
    for k, s in m["per_class"].items():
        p = f"{s['precision']:.1%}" if s["precision"] is not None else "-"
        print(f"{k:10} {s['n']:7} {s['ink']:9.0f} {p:>10}")
    if m["truth_layers"]:
        print(f"\n{'truth layer':46} {'want':7} {'class-acc':>10} {'recall':>8} {'coverage':>9}")
        for lay, s in m["truth_layers"].items():
            cov = f"{s['coverage']:.1%}" if s.get("coverage") is not None else "-"
            print(f"{lay[:46]:46} {s['want']:7} {s['class_acc']:10.1%} {s['recall']:8.1%} {cov:>9}")


# --- derived layer-role truth ----------------------------------------------
#
# TRUTH_CLASSES above is a hand list: seven layer paths, all from the one
# Loves Lane civil set, asserting a DASH CLASS each. It cannot grow by hand
# fast enough to matter - the corpus already carries 700+ distinct layer
# names - and a dash class is not derivable from a name anyway (linetype is
# a plotting property, not a semantic one).
#
# What IS derivable from the name is the layer's ROLE: is this measurable
# linework, or is it lettering / fill / sheet furniture that the junk pruner
# is SUPPOSED to drop? That expectation needs no human confirmation per
# sheet, so it turns every layered PDF into ground truth for coverage.
#
# The tokens below were read off the layer names actually present in this
# corpus - not from an industry standard. Sources, with an example each:
#
#   ../TakeoffPro AI/Plans/C{3.0,4.0,8.0,9.0}-*.pdf p0   (Loves Lane civil)
#       P1-PVMT-HATCH, P6-TEXT, P6-SHEET, O-SURFACE-LBL-FG,
#       E3-ROADNAME-TEXT, P1-PVMT-CORR, E1-MISC (NO HATCH)
#   (client folder) residence B civil set, 5.8.26
#       G-ANNO-TTLB, C-TOPO-TEXT, TXT-SPOT GRD, SE-P-CONC HATCH AB,
#       SE-P-GRAVEL HTCH AB RD, BG-L-Beach NON PLOT
#   (client folder) residence A landscaping set, 4.7.26
#       BG-ANNO-TEXT, BG-ANNO-DIMS, BG-ANNO-LGND, BG-ANNO-DETL-IDEN,
#       BG-ANNO-SHEET-SYMB, A-WALL-PATT, BG-SHEET MATCHLINE, BG-TOPO-NONPLOT
#   (client folder) residence C architectural set
#       TTL-BLK-B4UDIG, UTIL-NOTES, DETAIL-DIM
#
# INFERENCE, not a documented standard: that a layer whose leaf name carries
# one of these tokens holds no measurable linework. It is cross-checkable
# without trusting the inference - the engine prunes annotation-named layers
# to far lower coverage than linework-named ones on every sheet measured, and
# the independent geometry detectors above (_textural_by_geometry,
# _strip_field, _scatter_glyphs) agree on the large ones. Both checks are
# written up in out/verify/2026-08-22/gate/VERIFY.md.
#
# Deliberately NOT treated as annotation:
#   XREF / X-Ref / XR-*  - container names; the strokes under them are the
#                          real drawing (BG-XREF scores 100% coverage on
#                          Res-B civil p3, i.e. it is found linework)
#   PEN3 / BG-01 THIN    - lineweight buckets, they carry real geometry
#   BOULDER              - measured LF on some sheets, counted EA on others;
#                          calling it either way would be inventing a
#                          convention the project owner cannot check
ANNOTATION_TOKENS = (
    "ANNO", "TEXT", "TXT", "HATCH", "HTCH", "-HAT", "PATT", "DIM", "LBL",
    "NOTE", "TTLB", "TTL-BLK", "LGND", "LEGEND", "IDEN", "MATCHLINE",
    "SHEET", "BORDER", "NON PLOT", "NONPLOT",
)

# Symbols are countable EA, not measurable LF (a tree is one tree, not 40 ft
# of canopy outline). Scored on their own line rather than folded into the
# linework headline: a low number here means "symbols not vectorised", which
# is a different defect from "a pipe run was missed". Same inference caveat -
# read off BG-L-PLNT-TREE / BG-L-PLNT-SHRB / BG-Tree Aerial / I-FURN /
# BG-ANNO-SYMB in the residential sets, and it matches what the existing
# _symbolic_by_congruence and _radial_bursts detectors flag by geometry.
SYMBOL_TOKENS = ("PLNT", "TREE", "SHRB", "FURN", "SYMB")

# A layer under this much ink is a stray, not a scoreable expectation.
# 200 pt is roughly 28 ft at the 1"=10' these sheets plot at.
MIN_TRUTH_INK = 200.0


def is_letterform(u) -> bool:
    """Is this stroke a vector LETTER OUTLINE rather than linework?

    chain._glyph_blobs' seed test, reused verbatim rather than reinvented:
    a letter stroke is a FOLDED path inside a small box - its path length
    far exceeds its bounding diagonal - which no real dash ever is.

    Why the gate reports this instead of subtracting it: CAD plots some
    text as outlines, so glyph ink lands in the stroke pool and inflates
    the recall denominator with material no detector should ever chain
    (C8.0's sheet-number callouts "C8.2" etc. are drawn this way, on layer
    '0' and P6-SHEET). But subtracting it would be worse, because on this
    corpus most letterform ink on a LINEWORK layer is not stray lettering
    at all - it is the inline mark of a dash-text utility run, the "W" in
    "- W -" on P2-WATER-LINE (38.7% letterform), the "SD" on P2-GAS-LINE
    (29.1%), the flow chevron on P2-STORM-SWALE (20.7%). Deleting those
    would delete the only thing that tells a water line from a firewater
    line. So the ink stays in the denominator, the share is printed beside
    the numbers, and COVERAGE - which credits a decoration sitting ON its
    line - is the headline rather than raw recall.
    """
    import numpy as np
    if len(u.pts) < 3 or not (2.0 <= u.length <= 40.0):
        return False
    bb = u.pts.max(axis=0) - u.pts.min(axis=0)
    diag = float(np.hypot(*bb))
    if diag > 15.0 or diag <= 0:
        return False
    return diag / u.length <= 0.78


def layer_role(layer: str) -> str:
    """'annotation' | 'symbol' | 'linework', from the LEAF layer name only.

    Leaf, for the same reason is_textural() uses it: an xref path prefix
    describes the file a layer came from, not what the layer holds.
    Name-only on purpose - a role decided by geometry would be derived from
    the very output being scored, which is not ground truth.
    """
    leaf = (layer or "").split("|")[-1].strip().upper()
    if not leaf:
        return "unlayered"
    if "NO HATCH" in leaf or "NOHATCH" in leaf:
        return "linework"   # named for NOT being hatch - see is_textural
    if leaf.endswith("-CORR"):
        return "annotation"  # corridor surface texture, per is_textural
    if any(t in leaf for t in ANNOTATION_TOKENS):
        return "annotation"
    if any(t in leaf for t in SYMBOL_TOKENS):
        return "symbol"
    return "linework"


def layer_role_rows(chains, units, min_ink: float = MIN_TRUTH_INK) -> list:
    """Per-leaf-layer recall + decoration-aware coverage, for EVERY layer.

    The same two numbers class_metrics() reports for the seven hand-asserted
    layers, computed for all of them, with the expectation coming from
    layer_role() instead of from a human. Unlayered ink ("") is excluded: it
    carries no expectation either way.
    """
    from collections import defaultdict

    per = defaultdict(lambda: {"unit_ink": 0.0, "chain_ink": 0.0,
                               "glyph_ink": 0.0,
                               "units": [], "chains": [], "n_chains": 0})
    for u in units:
        if u.color and min(u.color) > 0.92 and not getattr(u, "on_fill", False):
            continue   # invisible whiteout, excluded from scoring as in build
        if getattr(u, "_absorbed", False):
            continue   # twin copy merged into a survivor - one printed line
        s = per[(u.layer or "").split("|")[-1]]
        s["unit_ink"] += u.length
        s["units"].append(u)
        if is_letterform(u):
            s["glyph_ink"] += u.length
    for c in chains:
        s = per[(c.layer or "").split("|")[-1]]
        s["chain_ink"] += (getattr(c, "ink", None) or c.length)
        s["chains"].append(c)
        s["n_chains"] += 1

    # one tree over ALL chains on the sheet: scoring a layer against every
    # chain, not just its own, separates "never traced" from "traced but
    # filed under another layer". Measured 2026-08-22: on the 7 layered gate
    # sheets same-layer coverage is 92.1% and any-chain coverage 99.2%, so
    # 7.1 of the 7.9 missing points are attribution, not detection.
    all_tree = _chain_sample_tree(chains)
    rows = []
    for leaf, s in per.items():
        if not leaf.strip() or s["unit_ink"] < min_ink:
            continue
        role = layer_role(leaf)
        rows.append({
            "layer": leaf,
            "role": role,
            "unit_ink": s["unit_ink"],
            "chain_ink": s["chain_ink"],
            "n_chains": s["n_chains"],
            "n_units": len(s["units"]),
            "recall": min(s["chain_ink"] / s["unit_ink"], 1.0),
            "coverage": _coverage(s["units"], s["chains"]),
            # upper bound, not a second opinion: on a dense sheet an
            # unrelated chain passing within 3 pt also satisfies this.
            # Linework only - it is the only role that reports it, and the
            # hatch layers this would otherwise scan run to 300k units.
            "coverage_any": (_coverage(s["units"], None, tree=all_tree)
                             if role == "linework" else None),
            "glyph_ink": s["glyph_ink"],
        })
    rows.sort(key=lambda r: -r["unit_ink"])
    return rows


def derived_layer_metrics(chains, units, min_ink: float = MIN_TRUTH_INK,
                          miss_at: float = 0.5) -> dict:
    """Ink-weighted recall/coverage per role, plus the layers that missed.

    Reported per role rather than pooled, because the three roles want
    opposite outcomes: linework coverage should be high, annotation coverage
    should be LOW (that is the junk pruner earning its keep), and symbol
    coverage says whether glyph layers were vectorised at all.
    """
    rows = layer_role_rows(chains, units, min_ink)
    out = {"rows": rows, "roles": {}}
    for role in ("linework", "symbol", "annotation"):
        rs = [r for r in rows if r["role"] == role]
        ink = sum(r["unit_ink"] for r in rs)
        if not ink:
            out["roles"][role] = {"n": 0, "ink": 0.0, "glyph_ink": 0.0,
                                  "recall": None, "coverage": None,
                                  "coverage_any": None}
            continue
        out["roles"][role] = {
            "n": len(rs),
            "ink": ink,
            "glyph_ink": sum(r["glyph_ink"] for r in rs),
            "recall": sum(r["recall"] * r["unit_ink"] for r in rs) / ink,
            "coverage": sum((r["coverage"] or 0.0) * r["unit_ink"]
                            for r in rs) / ink,
            # None, not 0, when the role never computes it - a printed 0.0%
            # would read as "nothing on this role was traced"
            "coverage_any": (sum((r["coverage_any"] or 0.0) * r["unit_ink"]
                                 for r in rs) / ink
                             if any(r["coverage_any"] is not None for r in rs)
                             else None),
        }
    # two different defects, never pooled: a LOST layer was not traced by
    # anything (a real hole in the takeoff), a MISFILED one was traced by a
    # chain that kept a different layer (the footage is there, the style
    # list attributes it wrongly). Reporting them as one number is how the
    # 92.1% headline would get read as 8 points of missing linework.
    low = [r for r in rows
           if r["role"] == "linework" and (r["coverage"] or 0.0) < miss_at]
    out["lost"] = sorted(
        [r for r in low if (r["coverage_any"] or 0.0) < miss_at],
        key=lambda r: -r["unit_ink"])
    out["misfiled"] = sorted(
        [r for r in low if (r["coverage_any"] or 0.0) >= miss_at],
        key=lambda r: -r["unit_ink"])
    out["misses"] = sorted(low, key=lambda r: -r["unit_ink"])
    return out


def print_derived_metrics(d: dict, top: int = 12) -> None:
    print(f"\n{'role':11} {'layers':>7} {'ink':>10} {'recall':>8} "
          f"{'coverage':>9} {'any-chain':>10}")
    for role, s in d["roles"].items():
        r = f"{s['recall']:.1%}" if s["recall"] is not None else "-"
        c = f"{s['coverage']:.1%}" if s["coverage"] is not None else "-"
        a = f"{s['coverage_any']:.1%}" if s.get("coverage_any") is not None \
            else "-"
        print(f"{role:11} {s['n']:7} {s['ink']:10.0f} {r:>8} {c:>9} {a:>10}")
    for kind in ("lost", "misfiled"):
        rs = d.get(kind) or []
        if not rs:
            continue
        what = ("traced by NOTHING - a real hole in the takeoff"
                if kind == "lost"
                else "traced, but the chain kept another layer")
        print(f"\n{kind}: linework layers under 50% own-layer coverage, "
              f"{what} ({len(rs)}):")
        for r in rs[:top]:
            print(f"   {r['layer'][:40]:40} ink={r['unit_ink']:9.0f} "
                  f"recall={r['recall']:6.1%} "
                  f"cov={(r['coverage'] or 0.0):6.1%} "
                  f"any={(r['coverage_any'] or 0.0):6.1%} "
                  f"chains={r['n_chains']}")


if __name__ == "__main__":
    main()
