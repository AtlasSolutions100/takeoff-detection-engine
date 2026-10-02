"""Site walls from the sheet's TW / BW grades (the estimator's "boulder walls SF (+1 ft)" line).

A wall on a grading sheet is a drawn line with top-of-wall (TW) and bottom-of-wall (BW) grades
printed along it; at one wall STATION the two are printed together and point at the same spot
(leaders to a dot on the wall), and an EW (end of wall) grade marks where the wall runs out at
grade. Per wall run this gives, with nothing guessed:
  * the stations: every TW paired with the BW it sits with (nearest, within `PAIR_FT`, and a TW
    is never paired with a BW above it); an EW grade is a station of zero height,
  * the wall's length (the drawn run, feet) and its height at every station (TW - BW; between
    stations the two grades are interpolated along the run, held flat beyond the last one),
  * the wall face = integral of (height + `add_ft`) along the run, `add_ft` typed (1 ft default),
  * the dig depth under the wall = existing ground - (BW - wall footing depth), where the existing
    surface is known (compute's surface, sampled along the run).
A grade that pairs with nothing is listed (`unpaired`, with why) and never given a height.
The grades come from the sheet's own text ("TW 569.6"), placed by the reader at the dot their
leader points to; a wall line is a chain on a layer named like a wall (WALL / BOULDER / RETAIN)
or the line a station sits on. Every wall lists its stations with their label ids.
"""
from __future__ import annotations

import re

import numpy as np

from .geom import cum_length, nearest_on_polyline, apply_affine

WALL_LAYER_RE = re.compile(r"WALL|BOULDER|RETAIN", re.I)
TOP_TAGS = {"TW", "TOW", "T/W"}
BOT_TAGS = {"BW", "BOW", "B/W"}
END_TAGS = {"EW"}
PAIR_FT = 40.0         # a TW and its BW point at one station; further apart than this they are two
REACH_PT = 30.0        # a station sits on its wall; further than this it is another line's
JOIN_PT = 12.0         # pieces of one wall line meet end to end within this


def _pts(ch) -> np.ndarray:
    return np.asarray(ch["pts"], dtype=float)


def _runs(chains: list[dict]) -> list[list[dict]]:
    """Pieces of one style joined end to end (gap <= JOIN_PT) into wall runs, longest seed first."""
    free = {ch["id"]: ch for ch in chains}
    runs = []
    while free:
        seed = max(free.values(), key=lambda c: c.get("length", 0.0))
        free.pop(seed["id"])
        run = [seed]
        pts = [_pts(seed)]
        while True:
            head, tail = pts[0][0], pts[-1][-1]
            best = None
            for cid, ch in free.items():
                if ch["style"] != seed["style"]:
                    continue
                P = _pts(ch)
                for end_pt, at_tail in ((tail, True), (head, False)):
                    for rev in (False, True):
                        q = (P[-1] if rev else P[0]) if at_tail else (P[0] if rev else P[-1])
                        d = float(np.hypot(*(end_pt - q)))
                        if d <= JOIN_PT and (best is None or d < best[0]):
                            best = (d, cid, rev, at_tail)
            if best is None:
                break
            _, cid, rev, at_tail = best
            ch = free.pop(cid)
            P = _pts(ch)[::-1] if rev else _pts(ch)
            if at_tail:
                pts.append(P)
                run.append(ch)
            else:
                pts.insert(0, P)
                run.insert(0, ch)
        runs.append(run)
    return runs


TWIN_PT = 16.0         # the two faces of one drawn wall lie within this of each other
WALL_GAP_FT = 150.0    # on an unnamed pen, grades further apart than this along the line are two walls


def _merge_twin_runs(runs: list[list[dict]], att: dict) -> list[tuple[list[dict], list[dict]]]:
    """(run, stations) per wall, the faces of a double-line wall folded into the longer face: a
    run whose samples (every 8 pt) lie within TWIN_PT of a longer run for 80 % of its length gives
    that run its stations and is dropped."""
    geoms = []
    for run in runs:
        P = np.vstack([_pts(ch) if k == 0 else _pts(ch)[1:] for k, ch in enumerate(run)]) if len(run) > 1 else _pts(run[0])
        L = float(cum_length(P)[-1])
        geoms.append((L, P))
    order = sorted(range(len(runs)), key=lambda i: -geoms[i][0])
    owner = {i: i for i in range(len(runs))}
    for a_i, i in enumerate(order):
        for j in order[a_i + 1:]:
            if owner[j] != j:
                continue
            Lj, Pj = geoms[j]
            # a quick box test first: faces of one wall overlap in extent
            Pi_box = geoms[i][1]
            if (Pj[:, 0].max() < Pi_box[:, 0].min() - TWIN_PT or Pj[:, 0].min() > Pi_box[:, 0].max() + TWIN_PT
                    or Pj[:, 1].max() < Pi_box[:, 1].min() - TWIN_PT or Pj[:, 1].min() > Pi_box[:, 1].max() + TWIN_PT):
                continue
            n = max(2, int(Lj / 8.0) + 1)
            cl = cum_length(Pj)
            sd = np.linspace(0.0, Lj, n)
            samples = np.column_stack([np.interp(sd, cl, Pj[:, 0]), np.interp(sd, cl, Pj[:, 1])])
            Pi = geoms[i][1]
            near = 0
            for q in samples:
                d, _q, _k = nearest_on_polyline(q, Pi)
                if d <= TWIN_PT:
                    near += 1
            if near >= 0.8 * n:
                owner[j] = owner[i]
    out = []
    for i in order:
        if owner[i] != i:
            continue
        sts = []
        for j in range(len(runs)):
            if owner[j] == i:
                for ch in runs[j]:
                    sts.extend(att.get(ch["id"], []))
        out.append((runs[i], sts))
    return out


def _interp(stations: list[tuple[float, float]], s: float) -> float:
    """Linear between stations, flat beyond the ends (a wall grade holds to the wall's end)."""
    st = sorted(stations)
    if s <= st[0][0]:
        return st[0][1]
    if s >= st[-1][0]:
        return st[-1][1]
    for (s0, z0), (s1, z1) in zip(st, st[1:]):
        if s0 <= s <= s1:
            return z0 if s1 == s0 else z0 + (z1 - z0) * (s - s0) / (s1 - s0)
    return st[-1][1]


def pair_stations(spots: list[dict], pair_pt: float) -> tuple[list[dict], list[dict]]:
    """TW / BW grades into stations: the closest TW-BW pairs first (a TW never with a BW above
    it, never further than pair_pt), each grade in one station; EW grades are zero-height
    stations of their own. Returns (stations, unpaired)."""
    tops = [s for s in spots if (s.get("tag") or "").upper() in TOP_TAGS]
    bots = [s for s in spots if (s.get("tag") or "").upper() in BOT_TAGS]
    ends = [s for s in spots if (s.get("tag") or "").upper() in END_TAGS]
    cands = []
    for i, t in enumerate(tops):
        for j, b in enumerate(bots):
            d = float(np.hypot(t["x"] - b["x"], t["y"] - b["y"]))
            if d <= pair_pt and float(t["z"]) >= float(b["z"]):
                cands.append((d, i, j))
    cands.sort()
    used_t: set = set()
    used_b: set = set()
    stations = []
    for d, i, j in cands:
        if i in used_t or j in used_b:
            continue
        used_t.add(i)
        used_b.add(j)
        t, b = tops[i], bots[j]
        stations.append({"x": 0.5 * (t["x"] + b["x"]), "y": 0.5 * (t["y"] + b["y"]), "tw": float(t["z"]), "bw": float(b["z"]),
                         "h": round(float(t["z"]) - float(b["z"]), 2), "kind": "pair", "apart_pt": round(d, 1),
                         "labels": [t.get("label_id"), b.get("label_id")], "spots": [t.get("id"), b.get("id")]})
    for e in ends:
        stations.append({"x": float(e["x"]), "y": float(e["y"]), "tw": float(e["z"]), "bw": float(e["z"]), "h": 0.0, "kind": "end",
                         "apart_pt": 0.0, "labels": [e.get("label_id")], "spots": [e.get("id")]})
    unpaired = []
    for i, t in enumerate(tops):
        if i in used_t:
            continue
        near = [(float(np.hypot(t["x"] - b["x"], t["y"] - b["y"])), b) for b in bots]
        why = "no BW grade nearby"
        if near:
            d, b = min(near, key=lambda kv: kv[0])
            why = (f"the nearest BW ({b['z']:g}) is above this TW" if float(b["z"]) > float(t["z"]) and d <= pair_pt
                   else f"the nearest BW is {d:.0f} pt away" if d > pair_pt else "its BW paired with another TW")
        unpaired.append({"tag": "TW", "z": float(t["z"]), "label": t.get("label_id"), "spot": t.get("id"), "x": t["x"], "y": t["y"], "why": why})
    for j, b in enumerate(bots):
        if j in used_b:
            continue
        near = [(float(np.hypot(t["x"] - b["x"], t["y"] - b["y"])), t) for t in tops]
        why = "no TW grade nearby"
        if near:
            d, t = min(near, key=lambda kv: kv[0])
            why = (f"the nearest TW ({t['z']:g}) is below this BW" if float(t["z"]) < float(b["z"]) and d <= pair_pt
                   else f"the nearest TW is {d:.0f} pt away" if d > pair_pt else "its TW paired with another BW")
        unpaired.append({"tag": "BW", "z": float(b["z"]), "label": b.get("label_id"), "spot": b.get("id"), "x": b["x"], "y": b["y"], "why": why})
    return stations, unpaired


def wall_rows(an: dict, ft_per_pt: float, add_ft: float = 1.0, existing=None, M=None,
              wall_d: float = 0.0, reach_pt: float = REACH_PT, wall_lengths: dict | None = None) -> dict:
    """Walls on one sheet. `existing` is compute's existing Surface and `M` the sheet's matrix into
    the display frame (both optional: without them there is no dig depth, and the row says so)."""
    spots = [s for s in (an.get("spots") or [])
             if s.get("side") == "wall" and (s.get("tag") or "").upper() in TOP_TAGS | BOT_TAGS | END_TAGS and s.get("z") is not None]
    out = {"walls": [], "unpaired": [], "lf": 0.0, "face_sf": 0.0, "add_ft": float(add_ft), "n_grades": len(spots),
           "n_stations": 0, "n_attached": 0, "wall_footing_depth_ft": float(wall_d or 0.0), "note": "",
           "rule": f"a TW pairs with the BW it points at (within {PAIR_FT:g} ft, never one above it); EW is the wall's end at grade"}
    if not spots:
        out["note"] = "no TW / BW wall grades read on this sheet"
        return out
    pair_pt = PAIR_FT / ft_per_pt if ft_per_pt else 120.0
    stations, unpaired = pair_stations(spots, pair_pt)
    out["unpaired"] = unpaired
    out["n_stations"] = len(stations)
    if not stations:
        out["note"] = f"{len(spots)} wall grades read, none pairs into a station (a TW with a BW within {PAIR_FT:g} ft)"
        return out
    styles = an.get("styles") or []
    wall_styles = {i for i, st in enumerate(styles) if WALL_LAYER_RE.search(str(st.get("layer") or ""))}
    chains = [ch for ch in (an.get("chains") or []) if ch.get("user") is None and len(ch.get("pts") or []) >= 2 and ch.get("length", 0) >= 6]
    if not chains:
        out["note"] = "no lines to hang the wall grades on"
        return out
    # each station goes to the nearest line within reach (a wall-layer line wins a tie)
    att: dict[int, list] = {}
    for st in stations:
        p = np.array([st["x"], st["y"]], dtype=float)
        best = None
        for ch in chains:
            P = _pts(ch)
            if p[0] < P[:, 0].min() - reach_pt or p[0] > P[:, 0].max() + reach_pt or p[1] < P[:, 1].min() - reach_pt or p[1] > P[:, 1].max() + reach_pt:
                continue
            d, q, i = nearest_on_polyline(p, P)
            if d > reach_pt:
                continue
            score = d - (8.0 if ch["style"] in wall_styles else 0.0)
            if best is None or score < best[0]:
                best = (score, ch, d)
        if best is None:
            st["why"] = f"no line within {reach_pt:g} pt"
            continue
        att.setdefault(best[1]["id"], []).append(st)
        out["n_attached"] += 1
    if not att:
        out["note"] = f"{len(stations)} stations, none within {reach_pt:g} pt of a line"
        return out
    # the wall line is the whole run the stations sit on: the station pieces grown end to end
    # through the pieces of the same pen (a wall drawn in 25-ft pieces is one wall)
    station_styles = {ch["style"] for ch in chains if ch["id"] in att}
    lines = [ch for ch in chains if ch["style"] in station_styles]
    runs = [r for r in _runs(lines) if any(ch["id"] in att for ch in r)]
    # a wall drawn as two faces (a double line): the stations land on either face, so a run that
    # lies alongside a longer one within a wall's thickness is the same wall and joins it
    merged = _merge_twin_runs(runs, att)
    wid = 0
    out["ends_only"] = []
    out["n_no_length"] = 0
    typed = {}
    for k, v in (wall_lengths or {}).items():
        try:
            fv = float(v)
            if fv > 0:
                typed[str(k)] = fv
        except (TypeError, ValueError):
            pass
    gap_pt = WALL_GAP_FT / ft_per_pt
    _trap = getattr(np, "trapezoid", None) or getattr(np, "trapz")
    for run, sts in merged:
        if not sts:
            continue
        P = np.vstack([_pts(ch) if k == 0 else _pts(ch)[1:] for k, ch in enumerate(run)]) if len(run) > 1 else _pts(run[0])
        cl = cum_length(P)
        L_pt = float(cl[-1])
        recs = []
        for st in sts:
            q0 = np.array([st["x"], st["y"]], dtype=float)
            d, q, i = nearest_on_polyline(q0, P)
            sv = float(cl[i] + np.hypot(*(q - P[i])))
            recs.append((sv, st))
        recs.sort(key=lambda r: r[0])
        named = run[0]["style"] in wall_styles
        layer = str((styles[run[0]["style"]].get("layer") if 0 <= run[0]["style"] < len(styles) else "") or "")
        chain_ids = [ch["id"] for ch in run]
        # THE WALL'S EXTENT depends on the pen. On a layer named as a wall the drawn run is the
        # wall (an EW grade beyond the pairs says where it stops). On an unnamed pen the line runs
        # on into curbs and outlines, so only the stretch between a wall's own grades is known,
        # grades further apart than WALL_GAP_FT are two walls, and a wall with one station has no
        # length until the operator types it.
        if named:
            groups = [recs]
        else:
            groups = []
            cur = [recs[0]]
            for prev, r in zip(recs, recs[1:]):
                if r[0] - prev[0] > gap_pt:
                    groups.append(cur)
                    cur = [r]
                else:
                    cur.append(r)
            groups.append(cur)
        for grp in groups:
            pairs = [r for r in grp if r[1]["kind"] == "pair"]
            ends = [r for r in grp if r[1]["kind"] == "end"]
            if not pairs:
                # end-of-wall grades alone say where a wall stops, not how high it is
                out["ends_only"].append({"chain_ids": chain_ids, "lf": round(L_pt * ft_per_pt, 1),
                                         "grades": [{"tag": "EW", "z": r[1]["tw"], "labels": r[1]["labels"]} for r in ends]})
                continue
            wid += 1
            key = "w:" + ",".join(sorted(str(l) for r in grp for l in r[1]["labels"] if l is not None))
            tops = [(sv, st["tw"]) for sv, st in grp]
            bots = [(sv, st["bw"]) for sv, st in grp]
            s_pairs = [sv for sv, _ in pairs]
            s_ends = [sv for sv, _ in ends]
            if named:
                lo = min(s_ends) if s_ends and min(s_ends) < min(s_pairs) else 0.0
                hi = max(s_ends) if s_ends and max(s_ends) > max(s_pairs) else L_pt
                extent = "the whole line of its wall layer" + (", ending at its EW grade" if (hi - lo) < L_pt - 1e-6 else "")
                lf_known = True
            else:
                lo = min(sv for sv, _ in grp)
                hi = max(sv for sv, _ in grp)
                lf_known = (hi - lo) > 1e-6
                extent = ("between its own grades (an unnamed pen: the drawn line runs on past them)" if lf_known
                          else "one station on an unnamed pen: no length until you type it")
            W_pt = max(hi - lo, 0.0)
            lf_typed = typed.get(key)
            lf_ft = lf_typed if lf_typed else (round(W_pt * ft_per_pt, 1) if lf_known else None)
            if W_pt > 1e-6:
                n = max(2, int(W_pt * ft_per_pt) + 1)
                ss = np.linspace(lo, hi, n)
                h = np.array([_interp(tops, sv) - _interp(bots, sv) for sv in ss])
                ds = (ss[1] - ss[0]) * ft_per_pt
                face_measured = float(_trap(np.maximum(h, 0.0) + float(add_ft), dx=ds))
            else:
                h = np.array([st["h"] for _, st in pairs], dtype=float)
                face_measured = None
            h_mean = float(np.maximum(h, 0.0).mean())
            if lf_typed:
                face = (h_mean + float(add_ft)) * lf_typed
                face_note = f"{lf_typed:,.0f} ft typed x (wall height {h_mean:.2f} ft avg + {float(add_ft):g} ft)"
            elif lf_known:
                face = face_measured
                face_note = f"{W_pt * ft_per_pt:,.0f} ft x (wall height {h_mean:.2f} ft avg + {float(add_ft):g} ft)"
            else:
                face = None
                face_note = f"wall height {h_mean:.2f} ft; no length (one station): type the wall length for its face"
                out["n_no_length"] += 1
            rows = [{"station_ft": round((sv - (lo if W_pt > 1e-6 else sv)) * ft_per_pt, 1), "tw": st["tw"], "bw": st["bw"], "h": st["h"], "kind": st["kind"],
                     "labels": st["labels"], "spots": st["spots"], "x": round(st["x"], 1), "y": round(st["y"], 1), "apart_pt": st["apart_pt"]}
                    for sv, st in grp]
            dig = None
            if existing is not None and getattr(existing, "ok", False):
                if W_pt > 1e-6:
                    m = max(2, int(W_pt * ft_per_pt / 5.0) + 1)
                    sd = np.linspace(lo, hi, m)
                else:
                    sd = np.array([sv for sv, _ in pairs], dtype=float)
                    m = len(sd)
                xy = np.column_stack([np.interp(sd, cl, P[:, 0]), np.interp(sd, cl, P[:, 1])])
                if M is not None:
                    xy = apply_affine(np.asarray(M, dtype=float), xy)
                E = existing.sample(xy[:, 0], xy[:, 1])
                bw = np.array([_interp(bots, sv) for sv in sd])
                dep = E - (bw - float(wall_d or 0.0))
                okm = np.isfinite(dep)
                if okm.any():
                    dig = {"mean_ft": round(float(dep[okm].mean()), 2), "max_ft": round(float(dep[okm].max()), 2),
                           "min_ft": round(float(dep[okm].min()), 2), "n": int(okm.sum()), "of": int(m),
                           "existing_mean_ft": round(float(E[okm].mean()), 2),
                           "basis": f"existing ground - (BW{f' - {wall_d:g} ft footing' if wall_d else ''}), {int(okm.sum())} samples along the wall"}
            row = {"id": wid, "key": key, "chain_ids": chain_ids, "layer": layer, "named": named,
                   "lf": lf_ft, "lf_known": lf_known, "lf_typed": lf_typed, "lf_drawn": round(L_pt * ft_per_pt, 1),
                   "lf_between_stations": round((max(s_pairs) - min(s_pairs)) * ft_per_pt, 1), "n_stations": len(rows),
                   "h_min": round(float(h.min()), 2), "h_max": round(float(h.max()), 2), "h_mean": round(h_mean, 2),
                   "add_ft": float(add_ft), "face_sf": round(face, 1) if face is not None else None, "stations": rows, "dig": dig,
                   "extent": extent, "basis": face_note + f"; {len(rows)} station{'s' if len(rows) != 1 else ''}; length: {extent}"}
            out["walls"].append(row)
            if lf_ft:
                out["lf"] += lf_ft
            if face is not None:
                out["face_sf"] += face
    out["lf"] = round(out["lf"], 1)
    out["face_sf"] = round(out["face_sf"], 1)
    if not out["walls"]:
        out["note"] = "no wall line carries a station"
    elif out["n_no_length"]:
        out["note"] = f"{out['n_no_length']} wall{'s' if out['n_no_length'] != 1 else ''} with one station and no length: type the length for its face"
    return out
