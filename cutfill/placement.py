"""Where each sheet sits on the sheet shown: the compute maps every sheet's evidence with page_transform, and the
Overlay places another sheet (its image and its contour lines) with the same function, no volumes computed.

Kept out of project.py on purpose: project.py is hashed into the reading stamps (READER and INTERP), so any edit
there reads every sheet of every project again from the PDF (30-70 s a sheet). This module is in neither stamp, and
nothing here changes a reading. It imports nothing from project.py (project.py imports page_transform from here)."""
from __future__ import annotations

import os
from collections import deque

import numpy as np

from .align import auto_align, from_pairs, has_match_line, identity, matrix_stats

ACTIVE_ROLES = ("existing", "proposed", "both", "grading")


def display_for(settings: dict, pages: dict) -> str | None:
    """The sheet whose frame holds the volumes: the one set as shown, unless it takes no part (role
    none), then the first sheet that does. This is surfaces_for's own rule (project.py), written again
    here; tests/test_cutfill_transforms.py runs the two side by side and fails when they part."""
    display = settings.get("display_page")
    roles = {k: (v or {}).get("role", "none") for k, v in pages.items()}
    if display is None or roles.get(display, "none") == "none":
        display = next((k for k, r in roles.items() if r in ACTIVE_ROLES), None)
    return display


def _pairs_dst(project, key: str, al: dict) -> str | None:
    """The sheet a sheet's matching points were picked against. Saved with them since 2026-09-28; pairs saved
    before that do not say, and were picked against the sheet shown then: the only other sheet taking part when
    there is one, else the sheet the engine shows now - display_for, what the compute always assumed (critic 4,
    r2: the settings' raw page, when it took no part, sent old pairs nowhere)."""
    dst = al.get("dst")
    if dst:
        return dst if dst != key else None
    others = [k for k, v in project.data["pages"].items() if k != key and (v or {}).get("role") in ACTIVE_ROLES]
    if len(others) == 1:
        return others[0]
    d = display_for(project.data["settings"], project.data["pages"])
    return d if d and d != key else None


def _links(project, bad: dict | None = None) -> dict:
    """Every sheet's saved matching points as a link: key -> (the sheet they were picked against, matrix). Points
    that cannot make a placement (two points at one place, a fit that is not finite) are left out, and named in
    `bad` when given: one sheet's bad pairs no longer stop every placement of the project (critic 4, r3)."""
    out = {}
    for k in list(project.data["pages"]):
        al = (project.page_settings(k) or {}).get("align") or {}
        pr = al.get("pairs") or []
        if len(pr) < 2:
            continue
        dst = _pairs_dst(project, k, al)
        if dst is None:
            continue
        try:
            M = np.array(from_pairs([p["src"] for p in pr], [p["dst"] for p in pr]), dtype=float)
            if M.shape != (3, 3) or not np.all(np.isfinite(M)) or abs(np.linalg.det(M[:2, :2])) < 1e-9:
                raise ValueError("the fit is not a placement")
        except (ValueError, TypeError, KeyError, IndexError, np.linalg.LinAlgError) as e:
            if bad is not None:
                bad[k] = str(e)[:120] or type(e).__name__
            continue
        out[k] = (dst, M)
    return out


def _typed_path(project, key: str, display: str, bad: dict | None = None):
    """A chain of saved matching points from `key` to the sheet shown, each link used as picked or turned around
    (critic R1: the pairs on 3.1 were picked against 3.0; with 3.1 shown, 3.0 sat unmoved on it). Returns
    (matrix, [(from, to, "as picked" | "turned around")]) or None. `bad`: see _links."""
    links = _links(project, bad)
    prev = {key: None}
    q = deque([key])
    while q:
        u = q.popleft()
        if u == display:
            break
        steps = []
        if u in links:
            steps.append((links[u][0], links[u][1], "as picked"))
        for k2, (d2, M2) in links.items():
            if d2 == u:
                try:
                    steps.append((k2, np.linalg.inv(M2), "turned around"))
                except np.linalg.LinAlgError:
                    continue            # _links leaves out a singular fit; this only guards the math
        for v, M, how in steps:
            if v not in prev:
                prev[v] = (u, M, how)
                q.append(v)
    if display not in prev:
        return None
    chain, v = [], display
    while prev[v] is not None:
        u, M, how = prev[v]
        chain.append((u, v, M, how))
        v = u
    chain.reverse()
    total = np.eye(3)
    for _u, _v, M, _how in chain:
        total = M @ total
    return total, [(u, v, how) for u, v, _M, how in chain]


def page_transform(project, key: str, display: str) -> dict:
    """Matrix mapping page `key` coordinates into the display page frame, and where it came from:
      self        the sheet shown itself
      typed       two matching points the operator picked: on this sheet against the sheet shown, or turned
                  around, or through another sheet (the note says which)
      text        the same printed words on both sheets
      identity    the same frame (same size, shared words at the same place, no match line), or no reading yet
                  (unplaced: True)
      unaligned   no way to place it: the compute leaves it out and says so"""
    if key == display:
        return {"matrix": identity(), "source": "self"}
    bad: dict = {}
    path = _typed_path(project, key, display, bad)
    if path is None and key in bad:
        # its own points cannot place it (two picked at one place): said, then the words decide as if none were picked
        res = dict(_sized_by_scales(project, key, display, _by_words(project, key, display)))
        res["note"] = ("its matching points cannot place it (" + bad[key] + "): pick them again, apart; "
                       + str(res.get("note") or res.get("source")))
        return res
    if path is not None:
        M, steps = path
        out = {"matrix": M.tolist(), "source": "typed", **matrix_stats(M)}
        from .project import _sheet_words, scale_txt  # here, not at the top: project.py imports this module
        # two matching points size a sheet by the ratio of the two sheets' scales. Sized otherwise, they were picked on
        # other landmarks or in another order: placed, the other sheet's ground was measured 1.25x - 1,637 CY for 1,072
        # with both sheets reading 1" = 50 ft, said only in the warnings' count (critic 7 r-4, critic 8 r-6). Left out
        # and said instead; with a sheet's scale unknown or its readings at odds, the compute's warning stands as before
        a, b = sheet_scale(project, key), sheet_scale(project, display)
        if a and b and out.get("scale"):
            want = a / b
            if abs(float(out["scale"]) / want - 1.0) > _size_tolerance(project, steps):
                # either side can be wrong: the points, or a sheet's scale reading (a misread print or review) - the words
                # offer both (critic 9, r-4: "pick them again" cannot fix a scale)
                return {"matrix": identity(), "source": "unaligned", "words": [], "sized": out["scale"],
                        "note": f"its matching points size it {out['scale']:.3f}x, but {_sheet_words(project, key)} reads "
                                f"1\" = {scale_txt(a)} ft and {_sheet_words(project, display)} 1\" = {scale_txt(b)} ft "
                                f"({want:.3f}x): pick the points again (the same two landmarks, in the same order on both "
                                f"sheets), or check the two sheets' scales"}
        if len(steps) > 1 or steps[0][2] != "as picked":
            out["note"] = "the matching points " + "; then ".join(
                f"picked on {_sheet_words(project, u if how == 'as picked' else v)}"
                + ("" if how == "as picked" else ", turned around") for u, v, how in steps)
        # points picked on both sheets of a step, each against the other, that do not agree: one set is used, and
        # the operator cannot tell which is wrong (critic 4, m4: +32 one way, -52 back)
        links = _links(project)
        for u, v, _how in steps:
            if u in links and v in links and links[u][0] == v and links[v][0] == u:
                # the points on sheet u: its own picks, and the other sheet's picks against it
                on_u = [p["src"] for p in ((project.page_settings(u) or {}).get("align") or {}).get("pairs") or []] + \
                       [p["dst"] for p in ((project.page_settings(v) or {}).get("align") or {}).get("pairs") or []]
                try:
                    d = _disagree_pt(links[u][1], np.linalg.inv(links[v][1]), on_u)
                except (np.linalg.LinAlgError, ValueError, TypeError, KeyError):
                    continue
                if d > DISAGREE_PT:
                    out["note"] = ((out.get("note") + "; ") if out.get("note") else "") + (
                        f"the points picked on {_sheet_words(project, u)} and on {_sheet_words(project, v)} disagree by "
                        f"{d:.0f} pt: reset one set in Sheet alignment")
        return out
    return _sized_by_scales(project, key, display, _by_words(project, key, display))


def _sized_by_scales(project, key: str, display: str, res: dict) -> dict:
    """A sheet placed by its printed words, or as the same frame, sits on the sheet shown at the size of that fit (1:1 for
    the same frame). When the two sheets' own scales (sheet_scale) say another size beyond 2 %, it is left out and said,
    as a placement by two matching points is: 3.1's review set to 40 from the Scale card with 3.0 shown at 50, and 3.1
    was placed as the same frame and measured at 50, silently (critic 17, RISK-2). Not read, or a scale unknown: as
    before."""
    if res.get("source") not in ("identity", "text") or res.get("unplaced"):
        return res
    a, b = sheet_scale(project, key), sheet_scale(project, display)
    if not (a and b):
        return res
    try:
        size = float(matrix_stats(np.asarray(res.get("matrix") or identity(), dtype=float)).get("scale") or 0)
    except (TypeError, ValueError, IndexError):
        return res
    want = a / b
    if size <= 0 or abs(size / want - 1.0) <= 0.02:
        return res
    from .project import _sheet_words, scale_txt  # here, not at the top: project.py imports this module
    how = "as the same frame" if res.get("source") == "identity" else "by the words the sheets share"
    return {"matrix": identity(), "source": "unaligned", "words": [], "sized": size,
            "note": f"it is placed {how} at {size:.3f}x, but {_sheet_words(project, key)} reads 1\" = {scale_txt(a)} ft and "
                    f"{_sheet_words(project, display)} 1\" = {scale_txt(b)} ft ({want:.3f}x): click two matching points, or "
                    f"check the two sheets' scales"}


def _size_tolerance(project, steps) -> float:
    """How far honest clicks size a placement off the ratio of its two sheets' scales: each click a point or two off
    (CLICK_PT, as DISAGREE_PT) moves the distance between two points on a sheet by up to twice that, so a step's size is
    off by up to 2 x CLICK_PT over that distance on each of its two sheets, summed along the chain (the closest two points
    a step used; more points only tighten a fit). Never under 2 %, for the scale readings' own rounding. A flat 2 % left
    out 40 % of honest fits on landmarks 100 pt apart (critic 9, r-4); the mistake this catches - points on other
    landmarks or in another order - sized a sheet 1.25x."""
    tol = 0.0
    for u, v, how in steps:
        owner = u if how == "as picked" else v      # the sheet whose saved points make this step
        pr = (((project.data["pages"].get(owner) or {}).get("align") or {}).get("pairs")) or []
        for side in ("src", "dst"):
            try:
                pts = [(float(p[side][0]), float(p[side][1])) for p in pr]
            except (TypeError, ValueError, KeyError, IndexError):
                return 1.0                          # unreadable points: no size can be checked
            d = min((float(np.hypot(p[0] - q[0], p[1] - q[1])) for i, p in enumerate(pts) for q in pts[i + 1:]), default=0.0)
            if d <= 0:
                return 1.0
            tol += 2.0 * CLICK_PT / d
    return max(0.02, tol)


# how far off an honest click lands, in points, along each axis
CLICK_PT = 2.0


def _disagree_pt(A, B, pts) -> float:
    """How far two placements of one sheet part, in points, where the operator clicked (the picked points on that
    sheet): the sheet's corners magnify a click's error into a turn (critic 5, m-d)."""
    P = np.array([[x, y, 1.0] for x, y in pts], dtype=float).T
    return float(np.max(np.linalg.norm((np.asarray(A) @ P - np.asarray(B) @ P)[:2], axis=0)))


# where the operator clicked, two honest sets of points part by the clicks' own error: a point or two each (the corner
# measure called 81-97 % of honest sets wrong with a 3 pt bar: critic 5, m-d). Past this, a set was picked on
# another landmark
DISAGREE_PT = 8.0


def _by_words(project, key: str, display: str) -> dict:
    """The placement from the printed words alone (no matching points to go by)."""
    a = project.load_analysis(key)
    b = project.load_analysis(display)
    if not (a and b):
        return {"matrix": identity(), "source": "identity", "note": "analyse both sheets to check alignment", "unplaced": True}
    same = abs(a["width"] - b["width"]) < 1 and abs(a["height"] - b["height"]) < 1
    res = auto_align(a["labels"], b["labels"], same)
    if res.get("source") in ("identity", "text") and res.get("words") and (has_match_line(a["labels"]) or has_match_line(b["labels"])):
        # neighbours along a match line share their whole sheet furniture (a legend's TW / BW / C.B. / R.O.W. at the
        # same place on both): that is no proof of one frame. Subdivision A 3.0 / 3.1 were stacked unmoved on it (critic R1).
        # Nor is a fit on those words a few points apart - the legend placed a little differently on one sheet: it
        # "placed" the neighbour on the legend's shift, silently (critic 6, WRONG-2). Whatever the roles: a role rule
        # stacked NEIGHBOURS whose roles were existing / proposed (critic 5, WRONG-2). Two sheets that truly show the
        # same ground are placed with two clicks (the same spot on both); neighbours stacked are wrong silently
        return {"matrix": identity(), "source": "unaligned", "words": [],
                "note": "the sheets meet at a match line, and the words they share (" + ", ".join(res["words"][:5])
                        + ") do not show where they sit: click two matching points"}
    return res


# a scale the host handed over from a sheet's review (server.py _cutfill_scale_handoff); a scale typed or confirmed in
# the engine carries its own words ("typed", the printed text) and is the operator's
HANDED = ("read in review", "confirmed in review")

# The host's review, asked when a scale is checked: f(project, key) -> the sheet's scale as its review holds it NOW,
# {"ft": ft per inch, "reviewer": True for a reviewer's pick (else the detection's reading), "at": the pick's time,
# epoch seconds} (a bare number is a reading), or None. The host sets it (server.py); run alone the engine has no review
# and nothing hands a scale over. A copy kept on the sheet at hand-over went stale: the reviewer corrected 3.0 to 40, or
# 3.0 was detected again and read 40, and the copy's 50 confirmed the 50 in force (critic 7, WRONG-1).
REVIEW_SCALE = None

# The host's review, written: f(project, key, ft, text) -> True (sheet `key`'s review scale is now `ft`, recorded as the
# review's own Approve records it), False (it could not be recorded), None (the sheet has no review here). The Scale
# card's typing goes through it, so the review and the cut / fill hold one scale per sheet (Koby, 2026-09-29: the
# review wins, and a scale read wrong is typed right in either place). The host sets it; run alone the engine has none
# and a typed scale is its own, as before.
REVIEW_SET = None


def review_scale(project, key: str) -> dict | None:
    """Sheet `key`'s scale in its review now (REVIEW_SCALE) as {"ft", "reviewer", "at", "writable"}, or None when the host
    has none. `writable` False: a review this project reads but may not set (another owner's plan set, linked here)."""
    f = REVIEW_SCALE
    if f is None or not key:
        return None
    try:
        v = f(project, key)
        if isinstance(v, dict):
            ft = float(v.get("ft") or 0)
            return ({"ft": ft, "reviewer": bool(v.get("reviewer")), "at": v.get("at"), "writable": v.get("writable", True) is not False}
                    if ft > 0 else None)
        return {"ft": float(v), "reviewer": False, "at": None, "writable": True} if v and float(v) > 0 else None
    except Exception:            # noqa: BLE001 - no answer is no reading; it is never taken as agreement
        return None


def _agrees(v, fpi: float) -> bool:
    return abs(float(v) - fpi) <= 0.01 * fpi


def _newer(rv: dict | None, t) -> bool:
    """The review holds a reviewer's pick made after time `t` (epoch seconds; none recorded: the pick is newer)."""
    try:
        return bool(rv and rv.get("reviewer") and rv.get("at") and float(rv["at"]) > float(t or 0))
    except (TypeError, ValueError):
        return False


def operator_reading(project, k: str, rv: dict | None) -> float | None:
    """The scale the operator typed or confirmed with sheet `k` on screen (`operator_scale`), unless the review set that
    sheet since: a reviewer's pick newer than the typing wins, as it does at a hand-over (an old typo of 50 refused the
    reviewer's 40 on every later check: critic 8, r-4). A record with no time is older than any pick."""
    ps = project.data["pages"].get(k) or {}
    op = ps.get("operator_scale")
    try:
        op = float(op) if op else None
    except (TypeError, ValueError):
        return None
    if not op or _newer(rv, ps.get("operator_scale_at")):
        return None
    return op


def sheet_scale(project, k: str) -> float | None:
    """Sheet `k`'s own scale: its review's now when it has one - the review wins (Koby, 2026-09-29), and a review that
    corrects the print is exactly its case: sized by "readings that agree" such a sheet had no size at all, and a
    placement as the same frame went unchecked, 3.1 at 40 measured at 50 (critic 18, WRONG-1). Without a review, its
    print and the operator's scale for it when they agree, else None (none reads, or two part beyond 1 %)."""
    rv = review_scale(project, k)
    if rv and rv.get("ft"):
        return float(rv["ft"])
    an = project.load_analysis(k)
    printed = ((an or {}).get("scale_default") or {}).get("ft_per_in")
    vals = [float(v) for v in (printed, operator_reading(project, k, None)) if v]
    if not vals or any(not _agrees(v, vals[0]) for v in vals[1:]):
        return None
    return vals[0]


def foreign_scale(project, key: str | None = None) -> str | None:
    """Why the scale in force must not measure sheet `key` (default: the sheet shown), or None. A scale handed over
    from a sheet's review names that sheet (`scale_page`). Nothing measured on a sheet - volumes, the limit's rows
    and area, the limit offers' lengths, the walls - may use it unless that sheet's own readings say the same:
    - on ANOTHER sheet: its review now (REVIEW_SCALE), the engine's reading of its print, and the scale the operator
      typed or confirmed while it was on screen (`operator_scale`). At least one must read, and none may disagree
      beyond 1 %. 1" = 100 ft read on one sheet stood over a sheet drawn at 40, silently (critic 4, W1); the refusal
      covered the volumes only and the strip was priced 56 % high (critic 6, WRONG-1); one agreeing reading passed a
      sheet whose review or print disagreed (critic 7, WRONG-1 and r-5); an unread sheet shown let the other sheets be
      measured on its frame at their own scale, 1,637 CY for 1,072 (critic 7, r-4).
    - on its OWN sheet: that sheet's review must still read it (it was handed over from there: a correction made in
      review since then is not in it), and a scale merely read in review must agree with the sheet's print - the
      compute's own scale_check, so the limit's rows refuse where the compute does (critic 7, r-5).
    - a scale typed or confirmed in the engine is the operator's word for the sheet that was on screen when it was set
      (`operator_scale` / `operator_scale_at` on that sheet). On that sheet it stands until a reviewer's pick made after
      it disagrees (critic 8, WRONG-2). On any OTHER sheet it is not a word at all, and that sheet's own readings decide
      as above: typed with 3.1 on screen it lifted the reviewer's correction of 3.0 (critic 10, WRONG-1), and with no
      pick on 3.0 it measured 3.0 at 40 for its 50, 36 % low, exported (critic 11, WRONG-1).
    The project has one scale, and it measures the sheet shown: a refusal on another sheet never advises a typing (the
    operator typed 40 there, as advised, and the sheet shown was measured at 40: critic 11). Nothing is dropped or moved
    (critic 5, WRONG-1). The operator's word for a sheet counts only while no newer reviewer's pick holds that sheet
    (operator_reading)."""
    s = project.data["settings"]
    if not s.get("scale_ft_per_in"):
        return None
    shown = display_for(s, project.data["pages"])
    k = key or shown
    if k is None:
        return None
    page = s.get("scale_page")
    src = str(s.get("scale_source") or "")
    fpi = float(s["scale_ft_per_in"])
    from .project import _sheet_words, scale_check, scale_txt  # here, not at the top: project.py imports this module
    kw = _sheet_words(project, k)
    if k == shown:
        advise = f": with {kw} on screen, type its scale in the Scale card and press Enter (volumes move with the square of the scale)."
    else:
        advise = (f": the scale in force measures {_sheet_words(project, shown) if shown else 'the sheet shown'}, the sheet shown, and "
                  f"nothing on {kw} is measured with it - to measure there, make {kw} the sheet shown (volumes move with "
                  f"the square of the scale).")
    rv = review_scale(project, k)
    rv_ft = rv["ft"] if rv else None
    an = project.load_analysis(k)
    d = (an or {}).get("scale_default") or {}
    printed = d.get("ft_per_in")

    def readings_disagree(lead: str) -> str | None:
        """Sheet k's own readings against the scale in force: none may disagree beyond 1 %, and one must read."""
        op = operator_reading(project, k, rv)
        read = [(v, w) for v, w in ((rv_ft, 'reads 1" = {} ft in review'), (printed, 'prints 1" = {} ft'),
                                    (op, 'was set to 1" = {} ft here')) if v]
        off = next(((v, w) for v, w in read if not _agrees(v, fpi)), None)
        if read and off is None:
            return None
        why = (f"but {kw} " + off[1].format(scale_txt(off[0])) if off
               else f"and {kw} prints no scale the engine could read" + (" (it is not read yet)" if an is None else "")
                    + " and its review reads none, so nothing checks it there")
        return f'The scale 1" = {scale_txt(fpi)} ft {lead}, {why}' + advise

    # THE REVIEW WINS (Koby, 2026-09-29): the sheet shown has one scale - its review's - and the cut / fill measures at
    # it. Typing in the Scale card sets the review's scale too (REVIEW_SET), so an operator who finds it read wrong
    # types the right one and every number follows. Any other scale in force measures nothing here, whatever its source
    # or its time: "the later one wins" let door after door export the dirt at the other scale, 20-56 % off (critics
    # 8-16)
    if k == shown and rv_ft and not _agrees(rv_ft, fpi):
        if not rv.get("writable", True):
            # another owner's plan set, linked here: its review is read, never set from this project - typing its own
            # number measures with it; "type the right scale ... it sets the review's scale too" led to two refusals, a
            # dead end (critic 19, WRONG-1)
            # its print named when it disagrees: the operator sent to a wrong review's number measured 36 % low, said
            # only as a warning (critic 20, RISK-2)
            pr = (f' Its print says 1" = {scale_txt(printed)} ft: if the review is wrong, it is fixed there.'
                  if printed and not _agrees(printed, rv_ft) else "")
            return (f'{kw[:1].upper() + kw[1:]}\'s review says 1" = {scale_txt(rv_ft)} ft, but the cut / fill is set to '
                    f'1" = {scale_txt(fpi)} ft: type {scale_txt(rv_ft)} in the Scale card with {kw} on screen and press '
                    f'Enter to measure with it. Its review is another owner\'s plan set, linked here: a different scale is '
                    f'set in that set\'s own review (volumes move with the square of the scale).{pr}')
        again = f", or open Cut / fill again from {kw}" if str(k).startswith("d0p") else ""
        return (f'{kw[:1].upper() + kw[1:]}\'s review says 1" = {scale_txt(rv_ft)} ft, but the cut / fill is set to '
                f'1" = {scale_txt(fpi)} ft: type the right scale in the Scale card with {kw} on screen and press Enter '
                f'(it sets the review\'s scale too){again} (volumes move with the square of the scale).')
    if src not in HANDED:
        ps = project.data["pages"].get(k) or {}
        try:
            said = ps.get("operator_scale_at") if ps.get("operator_scale") and _agrees(ps["operator_scale"], fpi) else None
            worded = bool(ps.get("operator_scale")) and _agrees(ps["operator_scale"], fpi)
        except (TypeError, ValueError):
            said, worded = None, False
        if not worded and len(project.data["pages"]) <= 1:
            # a project with one page: every typing was made with it on screen - a word, of unknown time, so older
            # than any reviewer's pick (exempting it outright let a correction to 40 pass under a typed 50, exported 56 %
            # high: critic 12, WRONG-1)
            worded, said = True, None
        if worded:
            # the operator's word for this sheet: it stands until a reviewer's pick made after it disagrees (a word kept
            # with no time is older than any pick: critic 9, r-1)
            if _newer(rv, said) and not _agrees(rv_ft, fpi):
                # the review's number, named: "type its scale" let the operator type the old 40 again, and it stood
                # (their newer word) - 576 CY beside the page's 50 (critic 15, RISK-1)
                if k == shown:
                    advise = advise.replace("type its scale in the Scale card",
                                            f'type its scale (its review says 1" = {scale_txt(rv_ft)} ft) in the Scale card', 1)
                if said:
                    return (f'The scale 1" = {scale_txt(fpi)} ft was set with {kw} on screen before its review set '
                            f'1" = {scale_txt(rv_ft)} ft' + advise)
                return (f'{kw[:1].upper() + kw[1:]}\'s review says 1" = {scale_txt(rv_ft)} ft, and 1" = {scale_txt(fpi)} ft '
                        f'is not on record as set with {kw} on screen after that' + advise)
            return None
        # no word of the operator's for this sheet at this number: its own readings decide
        return readings_disagree(f"is not on record as set with {kw} on screen")
    if not page:
        return None
    if k == page:
        if rv_ft and not _agrees(rv_ft, fpi):
            again = (f": open Cut / fill from {kw} again, or" + advise[1:]) if k == shown else advise
            return (f'The scale 1" = {scale_txt(fpi)} ft was handed over from {kw}\'s review, which now reads '
                    f'1" = {scale_txt(rv_ft)} ft (a correction or a new detection made since)' + again)
        err, _w = scale_check(fpi, src, page, [(k, kw, printed, d.get("text") or "")])
        return err
    return readings_disagree(f"was read on {_sheet_words(project, page)}")


def scale_refusals(project) -> dict:
    """{sheet: why} for every sheet the scale in force must not measure (foreign_scale): the page blanks its own
    measures there - a limit being drawn, a zone's area, a pad's area, a picked limit's gaps (critic 7, r-1). Sheets
    that take part or have been read only: every unread page of a 40-page set was listed, 10 KB on every answer, and a
    sheet that takes no part said "not checked" in red (critic 8, MINOR-4). Call under the project's lock."""
    out = {}
    for k, v in list(project.data["pages"].items()):
        if (v or {}).get("role") not in ACTIVE_ROLES and not os.path.exists(project.analysis_path(k)):
            continue
        why = foreign_scale(project, k)
        if why:
            out[k] = why
    return out


def review_scales(project) -> dict:
    """{sheet: ft per inch} as each sheet's review holds it now (review_scale), for the sheets scale_refusals lists
    from: the Scale card shows a sheet's own scale there when it is not the sheet shown - typed there it sets that
    sheet's review, not the scale in force, and a box showing the scale in force snapped back to it after the Enter.
    Empty when the host has no review (run alone). Call under the project's lock."""
    out = {}
    if REVIEW_SCALE is None:
        return out
    for k, v in list(project.data["pages"].items()):
        # a sheet handed over from the review counts whatever its role: opened from, it joins as none, and its card
        # showed the scale in force while a typing there set its review (critic 17, WRONG-4)
        if ((v or {}).get("role") not in ACTIVE_ROLES and not os.path.exists(project.analysis_path(k))
                and not (v or {}).get("core_result")):
            continue
        rv = review_scale(project, k)
        if rv:
            out[k] = rv["ft"]
    return out


# What a compute's volumes rest on besides the scale and the sheet shown (checked apart, with their own words): the
# settings it reads; for each sheet taking part its records, its scale as its readings give it (page_transform leaves
# out a sheet sized off its two sheets' scales) and its reading (the analysis file, rewritten by every read); and
# every sheet's matching points (a placement chains through a sheet that takes no part). The compute route keeps it
# with the result; stale_result compares it with now (critic 8, r-7: a new limit drawn after a compute came back fresh
# after a reload, 100 % high; critic 9, WRONG-1: a correction of sheet 3.1's scale in review left standing a result the
# engine could not compute any more, and the approve exported it).
_SIG_SETTINGS = (("the limit", ("region",)), ("the grading zones", ("zones",)), ("the datum", ("datum", "abbrev_base")),
                 ("the layer stack", ("section_ft", "layers")), ("the grid cell", ("cell_ft",)), ("a rule", ("rules",)))
# a sheet's records by name (what changed is said); a record not named here is "its other records"
_SIG_PAGE_FIELDS = (("role", ("role",)), ("matching points", ("align",)), ("line roles", ("family_roles", "roles", "style_roles")),
                    ("typed heights or label fixes", ("typed", "typed_heights", "label_overrides")), ("line edits", ("edits",)),
                    ("building pads", ("pads",)), ("review decisions", ("core_edits",)))
# a sheet's bookkeeping, which no compute reads: the host's hand-over marks and the detection's path (a copied or moved
# store rewrites it; the reading's time already catches a new detection: critic 9, r-2), the ledger of notes, the scales
# (kept apart, as readings)
_SIG_PAGE_SKIP = ("host_seen", "core_sig", "core_redo_at", "core_result", "ledger", "ledger_seen", "operator_scale",
                  "operator_scale_at", "review_scale", "analysis")
# the record's form: a result kept with another form (or none) was computed by an older version, and does not stand
# (3: a sheet's scale kept as a number, compared within 0.1 %)
SIG_V = 3


def _h(v) -> str:
    import hashlib
    import json
    return hashlib.sha1(json.dumps(v, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]


def _filled(v):
    """None for an empty record ({}, [], ""): a record left empty and one never written read the same."""
    return None if v in ({}, [], "") else v


def _unordered(edits):
    """The review's decisions with their lists in one order: the same set handed over in another order is the same
    decision (critic 9, m7). The lines' own points keep theirs."""
    import json
    if not isinstance(edits, dict):
        return edits
    return {k: (sorted(v, key=lambda x: json.dumps(x, sort_keys=True, default=str)) if isinstance(v, list) else v)
            for k, v in edits.items()}


def inputs_sig(project) -> dict:
    """The fingerprint of what a compute reads (see above). Call under the project's lock."""
    s = project.data["settings"]
    out = {name: _h([_filled(s.get(k)) for k in keys]) for name, keys in _SIG_SETTINGS}
    named = {kk for _n, keys in _SIG_PAGE_FIELDS for kk in keys}
    sheets = {}
    for k, v in sorted(project.data["pages"].items()):
        v = v or {}
        if v.get("role") not in ACTIVE_ROLES:
            continue
        try:
            st = os.stat(project.analysis_path(k))
            reading = f"{st.st_mtime_ns}:{st.st_size}"
        except OSError:
            reading = None
        rec = {name: _h([_filled(_unordered(v.get(kk)) if kk == "core_edits" else v.get(kk)) for kk in keys])
               for name, keys in _SIG_PAGE_FIELDS}
        rec["other records"] = _h({kk: vv for kk, vv in v.items()
                                   if kk not in _SIG_PAGE_SKIP and kk not in named and _filled(vv) is not None})
        # the sheet's scale as placement reads it (sheet_scale: its readings when they agree, else none): a reading that
        # changes nothing there - the operator's word for a sheet whose print says the same - changes nothing here. Kept
        # as a number, compared within 0.1 % (_same_scale): subdivision A 3.1's bar reads 49.99994, and the reviewer's typed 50
        # made the result stale (the live check of critic 9's round)
        sc = sheet_scale(project, k)
        rec["scale readings"] = round(float(sc), 6) if sc else None
        sheets[k] = {"records": rec, "reading": reading}
    out["sheets"] = sheets
    out["matching points"] = _h({k: (v or {}).get("align") for k, v in sorted(project.data["pages"].items())
                                 if _filled((v or {}).get("align")) is not None})
    out["v"] = SIG_V
    return out


def _same_scale(x, y) -> bool:
    """Two records of a sheet's scale the volumes cannot tell apart: both none, or within 0.1 % (stale_result's own rule
    for the scale in force)."""
    if x is None or y is None:
        return x is None and y is None
    try:
        x, y = float(x), float(y)
    except (TypeError, ValueError):
        return False
    return abs(x - y) <= 1e-3 * max(abs(x), abs(y))


def _inputs_changed(project, old: dict, new: dict) -> str | None:
    """What changed between two fingerprints, in words, or None."""
    from .project import _sheet_words
    for name, _keys in _SIG_SETTINGS:
        if old.get(name) != new.get(name):
            return f"{name} changed since this compute"
    a, b = old.get("sheets") or {}, new.get("sheets") or {}
    # the sheet shown's own scale reaches the volumes as the scale in force alone - checked apart (stale_result: the
    # scale then and now, and foreign_scale: its review against it). Compared here too, the change of what a sheet's
    # scale IS (the review's, 15c2247) made every result whose sheet shown had a review correcting its print stale once,
    # "scale readings changed", though a new compute gave the same volumes (critic 19, RISK-3)
    shown = display_for(project.data["settings"], project.data["pages"])
    # ... but only while no other sheet takes part: the sheet shown's scale also sizes every other sheet's placement,
    # and a 0.9 % move of its review left 3.1 out of a new compute while the stored 900 CY stood and was exported
    # (critic 20, WRONG-2)
    if any(k2 != shown for k2 in set(a) | set(b)):
        shown = None
    for k in sorted(set(a) | set(b)):
        if k not in a:
            return f"{_sheet_words(project, k)} joined the cut / fill since this compute"
        if k not in b:
            return f"{_sheet_words(project, k)} left the cut / fill since this compute"
        ra, rb = a[k].get("records"), b[k].get("records")
        if isinstance(ra, dict) and isinstance(rb, dict):
            for name in list(ra) + [n for n in rb if n not in ra]:
                if name == "scale readings" and (k == shown or _same_scale(ra.get(name), rb.get(name))):
                    continue
                if ra.get(name) != rb.get(name):
                    return f"{_sheet_words(project, k)}'s {name} changed since this compute"
        elif ra != rb:
            return f"{_sheet_words(project, k)}'s records changed since this compute"
        if a[k].get("reading") != b[k].get("reading"):
            return f"{_sheet_words(project, k)} was read again since this compute"
    if old.get("matching points") != new.get("matching points"):
        return "a sheet's matching points changed since this compute"
    return None


def stale_result(project, res) -> str | None:
    """Why a stored result no longer stands, or None. Its volumes were measured on its sheet shown at its scale: when
    the sheet shown or the scale in force changed since, or that scale is refused on that sheet now, the volumes are
    not the ones a compute makes now. The host filed a result computed at 50 beside the limit's rows at 40, and after
    the scale was refused kept its volumes and dropped the rows, silently (critic 7, WRONG-2 and WRONG-3).
    Call under the project's lock."""
    if not isinstance(res, dict) or res.get("error"):
        return None
    from .project import _sheet_words, scale_txt
    s = project.data["settings"]
    was = res.get("display_page")
    now = display_for(s, project.data["pages"])
    if was and now and was != now:
        return (f"the sheet shown changed since this compute ({_sheet_words(project, was)} then, "
                f"{_sheet_words(project, now)} now)")
    f = res.get("ft_per_in")
    cur = s.get("scale_ft_per_in")
    if not cur and now:
        # no scale set: the compute used the sheet's printed one, and would again
        cur = (((project.load_analysis(now) or {}).get("scale_default")) or {}).get("ft_per_in")
    # a change the volumes feel (0.1 % of the scale moves them 0.2 %): a bar read at 49.99994 against a typed 50 is not
    # a new scale, and said as one it would read "1\" = 50 ft then, 50 ft now"
    if f and (not cur or abs(float(f) - float(cur)) > 1e-3 * float(f)):
        return (f'the scale changed since this compute (1" = {scale_txt(f)} ft then, '
                + (f'{scale_txt(cur)} ft now)' if cur else "none now)"))
    why = foreign_scale(project, was or now)
    if why:
        return why[0].lower() + why[1:].rstrip(".")
    # anything else the volumes rest on. A result from before this record was kept (or kept in an older form) cannot
    # say, and does not stand: every result on disk stood through a limit half the size, 100 % high (critic 9, r-1)
    sig = res.get("inputs")
    if not isinstance(sig, dict) or sig.get("v") != SIG_V:
        return "the result is from an older version of this tool"
    return _inputs_changed(project, sig, inputs_sig(project))


def transforms_for(project) -> dict:
    """{"display", "transforms": {key: page_transform}}: every sheet placed on the sheet shown the way
    the compute places its evidence (the same page_transform). The Overlay places another sheet, its
    image and its contour lines, from this right after two matching points are picked. A sheet that
    cannot be placed yet (no matching points either way, no reading) gets no entry: it sits unmoved."""
    with project._lock:                        # a page added meanwhile must not break the loop (critic M2)
        settings = dict(project.data["settings"])
        pages = {k: dict(v or {}) for k, v in project.data["pages"].items()}
    display = display_for(settings, pages)
    out = {}
    if display is not None:
        for key in pages:
            if key != display and not os.path.exists(project.analysis_path(key)) and _typed_path(project, key, display) is None:
                continue
            tr = dict(page_transform(project, key, display))
            tr.pop("words", None)               # the automatic match's word list: not needed to place a sheet
            if tr.pop("unplaced", False):
                continue
            out[key] = tr
        # the sheet shown's own points, when they cannot make a placement, are named on it: they place nothing
        # (critic 5, m-e: the other sheet was left out with the match-line note only)
        bad: dict = {}
        _links(project, bad)
        if display in bad and display in out:
            out[display] = dict(out[display], note="its own matching points cannot place anything (" + bad[display]
                                + "): pick them again, apart")
    return {"display": display, "transforms": out}
