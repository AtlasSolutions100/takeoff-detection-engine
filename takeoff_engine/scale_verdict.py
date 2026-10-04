"""A page's scale is a VERDICT FROM WITNESSES, not a reading.

The contract (docs/fable/TOOLS-MISSION.md Part 1, decided 2026-10-02):

  witness kinds   caption       a printed caption, from the text layer OR
                                from OCR - one kind, never two witnesses
                  bar           a drawn scale bar measured on the page
                  dimensions    the printed-dimension check's consensus
                  profile_axis  the profile graph's station-label fit
                  person        typed, two-point calibrated, or a Confirm
                                click (passed as `person=`, see below)

  CONFIRMED  a person set or confirmed it; OR at least two witnesses of
             DIFFERENT kinds agree within 2 % and no witness disagrees.
  CONFLICT   witnesses of different kinds disagree by more than 2 % - even
             when two others agree. There is NO value: the page behaves as
             a page with no scale and offers each reading as a choice.
  UNCHECKED  exactly one kind of witness. Its value is used, and everything
             measured with it says "scale not confirmed".
  NONE       no witness.

The cardinal sin is a wrong value in state CONFIRMED; everything else is
visible to the person.

This module is PURE: `verdict()` does no I/O and reads nothing but its
arguments. `witnesses_from_app()` turns what the app ALREADY computes into
the witness list - it adds no reading logic of its own.

THE RULES THIS FILE ADDS, each one a choice the contract leaves to the code:

1. Agreement is a relative-difference test between two values,
   |a - b| / min(a, b) <= 2 %, never a bucket (CLAUDE.md section 5, first
   shape). The smaller value is the denominator so the test is symmetric
   (a vs b gives the same answer as b vs a) and is the stricter of the two
   symmetric choices: 100 vs 102 agrees (exactly 2 %), 100 vs 98 does not
   (2.04 %). A 1e-9 slack absorbs float noise at the exact boundary.

2. Several witnesses of ONE kind are one witness (two caption readings, a
   text caption and its OCR twin). Within a kind:
     * the reading the app has IN FORCE (`in_force=True`, the reader's
       promoted pick) speaks for the kind; readings of that kind that agree
       with it fold into it; readings that disagree are `other_printed` -
       the other scales a multi-view sheet prints. They are named, they do
       not vote. (A plan + profile + detail sheet is not a conflict by
       itself; it is UNCHECKED unless the governing value has a second
       witness of another kind.)
     * with no in-force reading, readings that all agree are one witness
       (the median of them stands for the kind - an explicit rule, not
       whichever arrived first);
     * with no in-force reading and readings that DISAGREE, or with only
       `offered` readings (candidates the reader refused to promote: OCR
       rescues, a caption a bar contradicted, a join artefact), the kind is
       UNRESOLVED. An unresolved kind never supplies a value and never
       confirms anything (promoting OCR-rescued captions is FACTS A5 "do not
       repeat"). It DOES count as a disagreement against a voting witness
       when none of its values agrees with that witness - that is how a
       caption the reader refused because a drawn bar contradicts it makes
       the page CONFLICT, not a quiet bar-only UNCHECKED.

3. The value of a CONFIRMED or UNCHECKED verdict is the voting witness of
   the highest-ranked kind: caption, then bar, then dimensions, then
   profile_axis (VALUE_RANK). A caption prints an exact number, the others
   are measured; all agree within 2 % by then anyway, and with this order
   the value equals the number the app uses today wherever the app has one.

4. A value is a witness only if it is a real, finite number above zero.
   0.0 is a MEASURED zero, not a missing value (CLAUDE.md section 5, "0.0
   is not None"), and it is still no scale: it is dropped into `ignored`
   with the reason, never silently. None, negatives, NaN, infinities,
   booleans and strings go the same way.

5. A person's value always wins (the existing rule "the review record
   wins"): state CONFIRMED with the person's value, even when every witness
   disagrees with it. The witnesses are still sorted into `agree` /
   `disagree` against it so the screen can say so.
"""
from __future__ import annotations

import math

# --- the contract's constants -------------------------------------------------
AGREE_FRAC = 0.02           # two readings agree when within 2 % (rule 1)
_SLACK = 1e-9               # float noise at the exact boundary only

KINDS = ("caption", "bar", "dimensions", "profile_axis")
VALUE_RANK = KINDS          # rule 3: which agreeing kind's number is used
# How a person set the scale (CRITIC-scale-2 M1, coordinator ruling 2026-10-02):
#   confirmed  the person MEASURED something on the sheet and it agreed (within 2 %) with the value in force
#   accepted   the person pressed "Accept as read" without measuring: the value is used, but it is NOT confirmed
#   typed / calibrated / picked  the person entered (or measured) a value, or chose one of the sheet's readings
PERSON_HOW = ("typed", "calibrated", "picked", "confirmed", "accepted")
# A kind that, ALONE, is offered and not used (coordinator ruling 3,
# 2026-10-02): the printed-dimension consensus is the least-tested witness, so
# a page whose only witness it is stays NONE with that reading offered.
OFFER_ONLY_ALONE = ("dimensions",)
ROLES = ("reading", "offered")

CONFIRMED = "CONFIRMED"
CONFLICT = "CONFLICT"
UNCHECKED = "UNCHECKED"
NONE = "NONE"
STATES = (CONFIRMED, UNCHECKED, CONFLICT, NONE)


def rel_diff(a: float, b: float) -> float:
    """|a - b| / min(a, b) for two positive numbers (rule 1)."""
    return abs(a - b) / min(a, b)


def agree(a: float, b: float) -> bool:
    """True when two positive scale values are within AGREE_FRAC (rule 1)."""
    return rel_diff(a, b) <= AGREE_FRAC + _SLACK


def _bad_value(v) -> str | None:
    """Why `v` is not a usable scale value, or None when it is (rule 4)."""
    if v is None:
        return "no value"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return f"not a number ({type(v).__name__})"
    if math.isnan(v) or math.isinf(v):
        return "not a finite number"
    if v == 0:
        return "zero (a measured zero is not a scale)"
    if v < 0:
        return "negative"
    return None


def _w(kind, value, source="", note="", in_force=False, role="reading"):
    """A witness record, in the shape `verdict` reads. For callers' brevity."""
    return {"kind": kind, "value": value, "source": source, "note": note,
            "in_force": in_force, "role": role}


def _public(w: dict) -> dict:
    """A witness as the verdict reports it (never the caller's own dict)."""
    return {"kind": w["kind"], "value": w["value"],
            "source": w.get("source") or "", "note": w.get("note") or ""}


def _collapse(kind: str, ws: list) -> tuple[dict | None, list, list]:
    """One kind's witnesses -> (voting witness or None, other_printed, offered).

    Rule 2. `offered` non-empty and voting None = the kind is unresolved.
    """
    readings = [w for w in ws if w.get("role", "reading") == "reading"]
    offered = [w for w in ws if w.get("role", "reading") == "offered"]
    forced = [w for w in readings if w.get("in_force")]
    if len(forced) > 1 and not all(agree(forced[0]["value"], f["value"])
                                   for f in forced[1:]):
        raise ValueError(f"two {kind} readings in force with different values "
                         f"({[f['value'] for f in forced]}): the app has one "
                         f"scale in force per page")
    if forced:
        rep = forced[0]
        others = [w for w in readings if w is not rep]
        other_printed = [w for w in others if not agree(w["value"], rep["value"])]
        return rep, other_printed, offered
    if readings:
        if all(agree(a["value"], b["value"])
               for i, a in enumerate(readings) for b in readings[i + 1:]):
            ordered = sorted(readings, key=lambda w: w["value"])
            return ordered[(len(ordered) - 1) // 2], [], offered
        # disagreeing readings and none in force: nothing speaks for the kind
        return None, [], offered + readings
    return None, [], offered


def verdict(witnesses: list, person: dict | None = None) -> dict:
    """The page's scale state from its witnesses (and a person's record).

    witnesses: dicts with `kind` (one of KINDS) and `value` (ft per paper
      inch), optional `source` / `note` (words for the screen), `in_force`
      (the app's promoted reading of that kind) and `role` ("reading", the
      default, or "offered" for a candidate the reader would not promote).
    person: None, or {"value", "how" (typed / calibrated / confirmed),
      "who", "when"} - the review record.

    Returns {state, value, agree, disagree, why, choices, other_printed,
    offered, ignored, person}. `value` is None unless state is CONFIRMED
    or UNCHECKED. In CONFLICT, `choices` lists every reading in dispute
    (each one click away); `agree` / `disagree` are relative to `value`
    (in CONFLICT: agree is empty and disagree lists every reading in dispute).
    """
    ignored = []
    by_kind: dict = {k: [] for k in KINDS}
    for w in witnesses or []:
        if not isinstance(w, dict):
            raise ValueError(f"a witness must be a dict, got {type(w).__name__}")
        kind = w.get("kind")
        if kind == "person":
            raise ValueError("pass a person's record as person=..., not as a "
                             "witness: the review record is one value")
        if kind not in KINDS:
            raise ValueError(f"unknown witness kind {kind!r}; kinds are {KINDS}")
        role = w.get("role", "reading")
        if role not in ROLES:
            raise ValueError(f"unknown witness role {role!r}; roles are {ROLES}")
        why_bad = _bad_value(w.get("value"))
        if why_bad is not None:
            ignored.append({**_public(w), "value": w.get("value"),
                            "reason": why_bad})
            continue
        by_kind[kind].append(w)

    voting, other_printed, unresolved = [], [], {}
    for kind in KINDS:
        if not by_kind[kind]:
            continue
        rep, others, offered = _collapse(kind, by_kind[kind])
        other_printed += [_public(w) for w in others]
        if rep is not None:
            voting.append(rep)
            # an offered value beside an in-force reading of its own kind is
            # named like the other printed scales; it never votes
            other_printed += [_public(w) for w in offered
                              if not agree(w["value"], rep["value"])]
        elif offered:
            unresolved[kind] = offered
    offered_raw = [w for k in KINDS for w in unresolved.get(k, [])]
    offered_pub = [_public(w) for w in offered_raw]

    # ---- a person's record wins -------------------------------------------
    p_out = None
    if person is not None:
        how = person.get("how")
        if how not in PERSON_HOW:
            raise ValueError(f"person.how must be one of {PERSON_HOW}, got {how!r}")
        p_bad = _bad_value(person.get("value"))
        p_out = {"value": person.get("value"), "how": how,
                 "who": person.get("who"), "when": person.get("when")}
        for k in ("measured", "measure_disagreed"):
            if person.get(k) is not None:
                p_out[k] = person.get(k)
        # THE PERSON'S OWN MEASURE IS EVIDENCE TOO (CRITIC-scale-3 M1): {ft, at} of the measure recorded with the
        # record, and whether it agrees (2 %) with the value kept
        mz = person.get("measure")
        if mz is None and isinstance(person.get("measure_disagreed"), dict):
            mz = person["measure_disagreed"]
        if mz is None and _num(person.get("measured")) is not None:
            mz = {"ft": person.get("measured"), "at": person.get("when")}
        mft = _num((mz or {}).get("ft")) if isinstance(mz, dict) else None
        if isinstance(mft, float) and mft > 0 and isinstance(_num(person.get("value")), float):
            p_out["measure"] = {"ft": mft, "at": mz.get("at")}
            p_out["measure_agrees"] = agree(mft, _num(person.get("value")))
        if p_bad is not None:
            ignored.append({"kind": "person", "value": person.get("value"),
                            "source": how, "note": "", "reason": p_bad})
            p_out = None
    if p_out is not None:
        pv = p_out["value"]
        ag = [_public(w) for w in voting if agree(w["value"], pv)]
        dis = [_public(w) for w in voting if not agree(w["value"], pv)]
        dis += [_public(w) for w in offered_raw if not agree(w["value"], pv)]
        if dis and ag:
            tail = (f"; {len(ag)} witness(es) agree and {len(dis)} disagree "
                    f"with it")
        elif dis:
            tail = f"; it differs from every witness on the page ({len(dis)})"
        elif ag:
            tail = f"; {len(ag)} witness(es) agree with it"
        else:
            tail = "; nothing on the page checks it"
        if how == "accepted" and p_out.get("measure_agrees") is False:
            # KEPT OVER THE PERSON'S OWN DISAGREEING MEASURE (CRITIC-scale-3 M1): that measure dominates the witnesses -
            # not confirmed, whatever the caption and the bar say, until a later measure agrees or another value is set
            mz = p_out["measure"]
            return _out(UNCHECKED, pv, ag, dis,
                        f"accepted by a person at {_fmt(pv)} ft per inch; their own measure"
                        f"{' on ' + str(mz.get('at'))[:10] if mz.get('at') else ''} said {_fmt(mz['ft'])} ft per inch"
                        f"{tail}", [], other_printed, offered_pub, ignored, p_out)
        if how == "accepted" and p_out.get("measure_agrees") is True:
            # a measure that agrees confirms it
            return _out(CONFIRMED, pv, ag, dis,
                        f"accepted by a person at {_fmt(pv)} ft per inch, and their measure agrees{tail}",
                        [], other_printed, offered_pub, ignored, p_out)
        if how == "accepted":
            # A CLICK IS NOT A MEASUREMENT (CRITIC-scale-2 M1): the person's value is used, but the page is confirmed
            # only if two kinds of witness already agree on that very value; otherwise it stays not confirmed.
            wv = verdict(witnesses, None)
            st = CONFIRMED if (wv["state"] == CONFIRMED and wv["value"] is not None
                               and agree(wv["value"], pv)) else UNCHECKED
            return _out(st, pv, ag, dis,
                        f"accepted by a person without measuring at {_fmt(pv)} ft per inch{tail}",
                        [], other_printed, offered_pub, ignored, p_out)
        return _out(CONFIRMED, pv, ag, dis,
                    f"set by a person ({how}) at {_fmt(pv)} ft per inch{tail}",
                    [], other_printed, offered_pub, ignored, p_out)

    # ---- no witness ----------------------------------------------------------
    if not voting:
        why = "no witness to the scale on this page"
        if offered_pub:
            why = (f"no witness the app will use; {len(offered_pub)} reading(s) "
                   f"offered but not trusted: "
                   + ", ".join(_fmt(w["value"]) for w in offered_pub))
        return _out(NONE, None, [], [], why, [], other_printed, offered_pub,
                    ignored, None)

    # ---- disagreement between kinds -----------------------------------------
    clashes = []
    for i, a in enumerate(voting):
        for b in voting[i + 1:]:
            if not agree(a["value"], b["value"]):
                clashes.append((a, b))
    for kind, offs in unresolved.items():
        for v in voting:
            if not any(agree(o["value"], v["value"]) for o in offs):
                clashes.append((v, offs[0]))
    if clashes:
        # Every reading is a one-click choice: the voting witnesses and every
        # offered value. The other scales a multi-view sheet prints stay in
        # `other_printed` (named, not offered as the page scale).
        pub = [_public(w) for w in sorted(voting + offered_raw,
                                          key=lambda w: (KINDS.index(w["kind"]),
                                                         w["value"]))]
        a, b = clashes[0]
        why = (f"the witnesses disagree: {a['kind']} says {_fmt(a['value'])}, "
               f"{b['kind']} says {_fmt(b['value'])} ft per inch "
               f"({rel_diff(a['value'], b['value']) * 100:.1f} % apart)")
        return _out(CONFLICT, None, [], pub, why, pub, other_printed,
                    offered_pub, ignored, None)

    # ---- agreement -------------------------------------------------------------
    voting.sort(key=lambda w: VALUE_RANK.index(w["kind"]))
    if len(voting) == 1 and voting[0]["kind"] in OFFER_ONLY_ALONE:
        lone = voting[0]
        why = (f"no witness the app will use: the {lone['kind']} alone suggest "
               f"{_fmt(lone['value'])} ft per inch - offered as a choice, not "
               f"applied, because nothing else on the page checks it")
        return _out(NONE, None, [], [], why, [], other_printed,
                    [_public(lone)] + offered_pub, ignored, None)
    value = voting[0]["value"]
    pub = [_public(w) for w in voting]
    names = " and ".join(w["kind"] for w in voting)
    if len(voting) >= 2:
        why = (f"{names} agree within {AGREE_FRAC * 100:g} % at "
               f"{_fmt(value)} ft per inch")
        return _out(CONFIRMED, value, pub, [], why, [], other_printed,
                    offered_pub, ignored, None)
    why = f"one witness only ({names}): {_fmt(value)} ft per inch, not confirmed"
    if other_printed:
        why += ("; the sheet also prints "
                + ", ".join(_fmt(w["value"]) for w in other_printed))
    return _out(UNCHECKED, value, pub, [], why, [], other_printed, offered_pub,
                ignored, None)


def _fmt(v) -> str:
    try:
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "?"


def _out(state, value, ag, dis, why, choices, other_printed, offered, ignored,
         person) -> dict:
    return {"state": state, "value": value, "agree": ag, "disagree": dis,
            "why": why, "choices": choices, "other_printed": other_printed,
            "offered": offered, "ignored": ignored, "person": person}


# =============================================================================
# THE ADAPTER: today's app fields -> witnesses. No reading logic here.
# =============================================================================
# Which fields, and where the app computes them (core-cutfill dev @ ade3738):
#
#   scale        result["scale"] - ScaleResult.to_dict() of a reading
#                (takeoff_engine/scale_detect.py:359-370), set by
#                detect_page.py:361-365 from detect_scale(pg)
#                (scale_detect.py:2222). Fields used: ft_per_paper_inch, text,
#                source ("text" | "ocr" | "graphic-bar"), candidates,
#                bar_ft_per_paper_inch, pick.why / pick.bar_source.
#                `refused` (join artefacts, scale_detect.py:2074-2086) is NOT
#                used: those are welds, not printed captions
#                (scale_detect.py:2050-2057).
#   suggestions  result["scale_suggestions"] - the ABSTAINED to_dict
#                (scale_detect.py:340-358, detect_page.py:363-364). Every
#                candidate is an `offered` caption; its bar_ft_per_paper_inch
#                (set on abstained-ocr-single, scale_detect.py:2217-2218) is a
#                bar reading.
#   dimcheck     dimension_check(...) output (takeoff_engine/dimcheck.py:282),
#                as GET /api/job/{id}/page/{p}/scale/dimensions serves it
#                (server.py:4414-4453). The consensus value
#                `witness_consensus_ft_per_in` (dimcheck.py:367) is a witness
#                only where dimcheck's own verdict rule could speak: at least
#                MIN_WITNESSES (3, dimcheck.py:57) paired words and the
#                consensus cluster holding VERDICT_SHARE (70 %, dimcheck.py:59)
#                of them (the rule at dimcheck.py:382-393).
#   profiles     the `profiles` list of GET /api/job/{id}/page/{p}/profile
#                (server.py:6866-6916; ProfileRead.to_dict profile.py:760-786).
#                `h_ft_per_in` is a witness when both of its axes are
#                trustworthy: max_resid_ft <= MAX_RESID_FT (profile.py:136,
#                AxisFit.trustworthy profile.py:160-162).
#                NOTE: that is the PROFILE VIEW's horizontal scale. It is the
#                plan's scale only where the sheet draws both at one
#                horizontal scale - see DESIGN.md.
#   person       the review record as _page_scale returns it
#                (server.py:3354-3545): set_by == "reviewer" with
#                ft_per_paper_inch, set_at; basis == "engine-suggestion" when
#                the person accepted one of the engine's offered readings.
#                Use `person_from_review()`.

_GRAPHIC_BAR_TEXT = "graphic bar"   # ScaleCandidate.text of a bar, scale_detect.py:1768-1769


def _num(v):
    """A JSON number as float, or the raw value for verdict() to refuse."""
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


def _is_bar_cand(c: dict) -> bool:
    return str(c.get("text") or "").startswith(_GRAPHIC_BAR_TEXT)


def witnesses_from_app(scale: dict | None = None,
                       suggestions: dict | None = None,
                       dimcheck: dict | None = None,
                       profiles: list | None = None,
                       use_profile: bool = False) -> list:
    """The witness list from the fields the app already computes. Pure.

    The profile fit is NOT a witness unless `use_profile` (coordinator ruling
    2, 2026-10-02): it measures the profile VIEW's horizontal scale, which is
    the plan's only where the sheet draws both alike - it caught a half-size
    print's 2x and made a false CONFLICT on a plan-and-profile sheet whose
    profile is drawn at another scale (DESIGN.md section 4). The path is kept
    for the day plan and profile views are told apart."""
    from takeoff_engine.dimcheck import MIN_WITNESSES, VERDICT_SHARE
    from takeoff_engine.profile import MAX_RESID_FT

    out = []
    if scale is not None and scale.get("abstained"):
        # an abstained dict passed as `scale` is a caller mistake detect_page
        # never makes (it routes it to scale_suggestions); treat it as one
        suggestions, scale = scale, None
    if scale is not None:
        src = scale.get("source") or ""
        best = _num(scale.get("ft_per_paper_inch"))
        pick = scale.get("pick") or {}
        if src in ("text", "ocr"):
            out.append(_w("caption", best, f"{src} caption",
                          str(scale.get("text") or ""), in_force=True))
            for c in scale.get("candidates") or []:
                if _is_bar_cand(c):
                    continue
                out.append(_w("caption", _num(c.get("ft_per_paper_inch")),
                              f"{src} caption", str(c.get("text") or "")))
            bar = scale.get("bar_ft_per_paper_inch")
            if bar is not None:
                # WHICH bar reading this is (CRITIC-scale-1 MINOR 1/4): the text path's corroboration reads the bar
                # with no page (scale_detect.py:2092, :2105), so off its LABEL CENTRES with no drawn-tick check, and
                # a reading near a standard value is snapped to it. It still votes (DESIGN risk 6; S6 measures it),
                # but the witness says what it is.
                bs = pick.get("bar_source") or ""
                ticks = bs.endswith("ticks")
                out.append(_w("bar", _num(bar),
                              "drawn bar, " + ("measured on its drawn ticks" if ticks
                                               else "read off its label centres (ticks not measured)"),
                              f"bar_source={bs or 'none'}"))
        elif src == "graphic-bar":
            out.append(_w("bar", best, "drawn bar"
                          + (f" ({pick['bar_source']})" if pick.get("bar_source")
                             else ""),
                          str(scale.get("text") or ""), in_force=True))
        elif best is not None:
            # a source this adapter does not know: say so rather than guess a kind
            raise ValueError(f"unknown scale source {src!r}")
    if suggestions is not None:
        src = suggestions.get("source") or ""
        why = (suggestions.get("pick") or {}).get("why") or "abstained"
        for c in suggestions.get("candidates") or []:
            if _is_bar_cand(c):
                continue
            out.append(_w("caption", _num(c.get("ft_per_paper_inch")),
                          f"{src or 'text'} caption, not promoted ({why})",
                          str(c.get("text") or ""), role="offered"))
        bar = suggestions.get("bar_ft_per_paper_inch")
        if bar is not None:
            bs = (suggestions.get("pick") or {}).get("bar_source")
            out.append(_w("bar", _num(bar),
                          "drawn bar" + (f" ({bs})" if bs else "")))
    if dimcheck is not None:
        ws = dimcheck.get("witnesses")
        n = len(ws) if isinstance(ws, list) else dimcheck.get("n_witnesses")
        cn = dimcheck.get("witness_consensus_n")
        cv = dimcheck.get("witness_consensus_ft_per_in")
        if (n is not None and cn is not None and cv is not None
                and n >= MIN_WITNESSES and cn >= VERDICT_SHARE * n):
            out.append(_w("dimensions", _num(cv), "printed dimensions",
                          f"{cn} of {n} printed dimensions agree with each other"))
    for p in (profiles or []) if use_profile else []:
        st, el = p.get("station") or {}, p.get("elevation") or {}
        sr, er = st.get("max_resid_ft"), el.get("max_resid_ft")
        h = p.get("h_ft_per_in")
        if (h is not None and sr is not None and er is not None
                and sr <= MAX_RESID_FT and er <= MAX_RESID_FT):
            out.append(_w("profile_axis", _num(h), "profile station labels",
                          str(p.get("title") or "")))
    return out


# The text the Measure tool's "should be ___ ft, set scale" stores with the
# number (webui/app.js:18458, applyTypedScale(ft, `${trueFt}' over a measured
# line`)); the approve copies it into the review record (server.py:3419).
_CALIBRATED_SUFFIX = "over a measured line"


def person_from_review(det: dict | None) -> dict | None:
    """The `person` record from what _page_scale returns (server.py:3354).

    A record written by this build carries `how` and `who` (_page_scale
    returns them as `set_how` / `set_by_user`). An OLDER pick has neither, so
    HOW is recovered from what it does hold, in this order - and never as
    "confirmed", which no old record can prove (coordinator ruling 10):
      * basis "engine-suggestion" (an offered reading accepted) -> picked
      * text ending "over a measured line" (the Measure tool) -> calibrated
      * the value is one of the engine's own printed candidates (the picker)
        -> picked
      * anything else -> typed.
    A tombstone is never a person (_page_scale reads it as no pick)."""
    if not det or det.get("set_by") != "reviewer":
        return None
    v = _num(det.get("ft_per_paper_inch"))
    if det.get("set_how") in PERSON_HOW:
        how = det["set_how"]
    elif det.get("basis") == "engine-suggestion":
        how = "picked"
    elif str(det.get("text") or "").strip().endswith(_CALIBRATED_SUFFIX):
        how = "calibrated"
    elif isinstance(v, float) and any(
            isinstance(c.get("ft_per_paper_inch"), (int, float))
            and abs(float(c["ft_per_paper_inch"]) - v) < 1e-6
            for c in det.get("candidates") or [] if not _is_bar_cand(c)):
        how = "picked"
    else:
        how = "typed"
    out = {"value": v, "how": how, "who": det.get("set_by_user"), "when": det.get("set_at")}
    # the measure behind a confirmation, or the measure that disagreed with an acceptance (CRITIC-scale-2 M1)
    if det.get("set_measured") is not None:
        out["measured"] = det.get("set_measured")
    if det.get("set_measure_disagreed") is not None:
        out["measure_disagreed"] = det.get("set_measure_disagreed")
    if isinstance(det.get("set_measure"), dict):
        out["measure"] = det.get("set_measure")
    return out


def verdict_from_app(scale=None, suggestions=None, dimcheck=None,
                     profiles=None, review=None, use_profile=False) -> dict:
    """verdict() over witnesses_from_app(), with the person from the review
    record. `review` is _page_scale's return value (or None)."""
    ws = witnesses_from_app(scale, suggestions, dimcheck, profiles, use_profile)
    return verdict(ws, person_from_review(review))
