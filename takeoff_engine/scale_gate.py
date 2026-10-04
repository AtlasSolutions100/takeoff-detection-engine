"""The scale gate: the app's scale call + the verdict, scored against truth.

PASS = zero pages in state CONFIRMED with a wrong value. Everything else is
visible to the person (docs/fable/TOOLS-MISSION.md Part 1); this gate exists
for the one failure that is not.

For every truth row it runs, on that page, what the app runs:
  * detect_page(pdf, page)       -> result["scale"] / result["scale_suggestions"]
                                    (detect_page.py:361-365, detect_scale with OCR on)
  * dimension_check(words, result chains, scale in force)
                                    exactly as GET .../scale/dimensions does
                                    (server.py:4414-4453; no review record here,
                                    so the scale in force is the engine's)
  * find_profiles + read_profile as GET .../profile does (server.py:6866-6916)
then takeoff_engine.scale_verdict over those fields as the product runs it (the
profile witness OFF, coordinator ruling 2: it measures the PROFILE view's
horizontal scale), and again with it ON in a reference column. PASS needs zero
CONFIRMED-wrong in both columns.

A value is RIGHT when it is within 2 % of the truth (scale_verdict.agree).

Truth files:
  ground-truth/scales.json   rows a PERSON confirmed: {document, sha256,
                             page_index, ft_per_paper_inch, source,
                             confirmed_by_human}. `document` is a file name,
                             found under --plans-dir and checked by sha256.
  --proposed <path>          a proposed table (the scale census's
                             scales-proposed.json shape: document as a path
                             relative to --root or absolute, page_index,
                             proposed_ft_per_paper_inch). Reported in its own
                             table labelled "proposed, not human-confirmed";
                             it NEVER fails the gate.
A row whose PDF cannot be found (or whose hash differs) is a SKIP, named in
the output - never dropped. With --require a SKIP or ERROR among the
confirmed rows fails the gate.

Run:
  python -m takeoff_engine.scale_gate --plans-dir <dir> [--plans-dir <dir>]
         [--root <dir>] [--proposed <file>] [--workers N (<= 4)]
         [--json <out.json>] [--require]
Exit 0 = pass, 1 = a CONFIRMED-wrong page (or, with --require, a missing
confirmed page), 2 = usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import math
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DEFAULT_TRUTH = REPO / "ground-truth" / "scales.json"
MAX_WORKERS = 4


# ---------------------------------------------------------------- truth rows
def _truth_problem(v) -> "str | None":
    """Why a truth value is no truth (CRITIC-scale-1 MINOR 5), or None when it is a finite number above zero."""
    if v is None:
        return "no truth value"
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return f"truth is not a number ({v!r})"
    if not math.isfinite(v) or v <= 0:
        return f"truth is not a usable scale ({v!r})"
    return None


def load_truth(path: Path, proposed: bool) -> list[dict]:
    """The truth rows. A human-confirmed file takes `ft_per_paper_inch` ONLY (never a proposed value), and a row whose
    truth is missing, not a number, or not above zero is kept with `bad_truth` - it is an ERROR that fails the gate,
    never a quiet NO-TRUTH (CRITIC-scale-1 MINOR 5). A missing file reads as no rows (main() fails it under --require)."""
    path = Path(path)
    if not path.exists():
        return []
    d = json.loads(path.read_text(encoding="utf-8"))
    raw = d.get("rows") if isinstance(d, dict) and "rows" in d else \
        d.get("entries") if isinstance(d, dict) else d
    rows = []
    for r in raw or []:
        truth = r.get("ft_per_paper_inch") if not proposed else (
            r.get("ft_per_paper_inch") if r.get("ft_per_paper_inch") is not None
            else r.get("proposed_ft_per_paper_inch"))
        prob = _truth_problem(truth)
        if not proposed and prob is None:
            # a human-confirmed row must name its file's content and say so with a JSON boolean (CRITIC-scale-2 MINOR 9)
            if not isinstance(r.get("sha256"), str) or len(r.get("sha256") or "") != 64:
                prob = "no sha256 for its file"
            elif not isinstance(r.get("confirmed_by_human"), bool):
                prob = f"confirmed_by_human {r.get('confirmed_by_human')!r} is not a JSON boolean"
        # page_index must be a JSON integer, 0 or more (CRITIC-scale-3 MINOR 6): "1" or -1 (pymupdf's last page) would
        # score another page; that it lies inside the document is checked when the file is opened (run_page)
        pi = r.get("page_index")
        page_ok = isinstance(pi, int) and not isinstance(pi, bool) and pi >= 0
        rows.append({"document": r["document"], "page": pi if page_ok else -1,
                     "bad_page": None if page_ok else f"page_index {pi!r} is not a page number (an integer, 0 or more)",
                     "truth": None if prob else float(truth), "sha256": r.get("sha256"),
                     "confirmed": r.get("confirmed_by_human") is True or (
                         not proposed and not isinstance(r.get("confirmed_by_human"), bool)),
                     "proposed": proposed,
                     # a human-confirmed row without a usable truth is broken; a proposed one simply has none
                     "bad_truth": prob if (prob and not proposed) else None})
    if not proposed:
        bad = [r for r in rows if not r["confirmed"]]
        if bad:
            raise SystemExit(f"{path}: {len(bad)} row(s) are not confirmed_by_human; "
                             f"proposed rows belong in a --proposed file")
    return rows


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _find_by_name(name: str, dirs: list[Path]) -> list[Path]:
    out = []
    for base in dirs:
        for dp, dn, fn in os.walk(base):
            dn[:] = [x for x in dn if not x.startswith(".") and x != "node_modules"]
            if name in fn:
                out.append(Path(dp) / name)
    return out


def resolve(row: dict, root: Path, plans_dirs: list[Path], cache: dict) -> tuple:
    """(path or None, why-not)."""
    doc = row["document"]
    # keyed by name AND hash (CRITIC-scale-1 MINOR 4): two rows naming one file with different hashes are judged apart
    ck = (doc, row.get("sha256"))
    if ck in cache:
        return cache[ck]
    p = Path(doc)
    cands = []
    if p.is_absolute():
        cands = [p] if p.exists() else []
    elif len(p.parts) > 1:
        cands = [root / p] if (root / p).exists() else []
    else:
        cands = _find_by_name(doc, plans_dirs)
    if not cands:
        out = (None, "PDF not found")
    elif row.get("sha256"):
        ok = [c for c in cands if _sha256(c) == row["sha256"]]
        out = (ok[0], "") if ok else (None, f"found {len(cands)} file(s) by name, none with the recorded sha256")
    else:
        out = (cands[0], "")
    cache[ck] = out
    return out


# ------------------------------------------------------------- one page, worker
def run_page(pdf: str, page: int) -> dict:
    """The app's scale fields for one page. Runs in a worker process."""
    import pymupdf
    from takeoff_engine.detect_page import detect_page
    from takeoff_engine.dimcheck import dimension_check
    from takeoff_engine.extract import page_words, extract_strokes
    from takeoff_engine import profile as _profile

    t0 = time.time()
    try:
        _d = pymupdf.open(pdf)
        try:
            n_pages = _d.page_count
        finally:
            _d.close()
        if not (0 <= page < n_pages):
            return {"ok": False, "error": f"page_index {page} is outside the document ({n_pages} pages)",
                    "s": round(time.time() - t0, 1)}
        res = detect_page(pdf, page)
        scale = res.get("scale")
        sugg = res.get("scale_suggestions")
        ft = None
        if scale:
            try:
                ft = float(scale.get("ft_per_paper_inch") or 0) or None
            except (TypeError, ValueError):
                ft = None
        doc = pymupdf.open(pdf)
        try:
            pg = doc[page]
            dim = dimension_check(page_words(pg), res.get("chains") or [], ft)
            profiles, pnotes = [], []
            units = extract_strokes(pg)
            for r in _profile.find_profiles(pg, units):
                if r.box[2] <= r.box[0]:
                    continue
                rd = _profile.read_profile(pg, r, units)
                if rd is not None:
                    profiles.append(rd.to_dict())
        finally:
            doc.close()
        dim_slim = {k: dim.get(k) for k in ("verdict", "witness_consensus_ft_per_in",
                                             "witness_consensus_n", "why")}
        dim_slim["n_witnesses"] = len(dim.get("witnesses") or [])
        prof_slim = [{"h_ft_per_in": p.get("h_ft_per_in"), "title": p.get("title"),
                      "station": {"max_resid_ft": (p.get("station") or {}).get("max_resid_ft")}
                      if p.get("station") else None,
                      "elevation": {"max_resid_ft": (p.get("elevation") or {}).get("max_resid_ft")}
                      if p.get("elevation") else None} for p in profiles]
        return {"ok": True, "scale": scale, "suggestions": sugg, "dimcheck": dim_slim,
                "profiles": prof_slim, "s": round(time.time() - t0, 1)}
    except Exception as e:  # reported as ERROR, never dropped
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300],
                "trace": traceback.format_exc()[-1200:], "s": round(time.time() - t0, 1)}


# --------------------------------------------------------------------- scoring
def judge(row: dict, app: dict | None, skip: str) -> dict:
    from takeoff_engine.scale_verdict import agree, verdict_from_app
    out = {"document": row["document"], "page": row["page"], "truth": row["truth"]}
    if row.get("bad_page"):
        return {**out, "result": "ERROR", "why": "this row's " + row["bad_page"]}
    if row.get("bad_truth"):
        return {**out, "result": "ERROR", "why": "this human-confirmed row has " + row["bad_truth"]}
    if skip:
        return {**out, "result": "SKIP", "why": skip}
    if not app["ok"]:
        return {**out, "result": "ERROR", "why": app["error"]}
    try:
        return _judge_app(row, app, out)
    except Exception as e:        # noqa: BLE001 - one row's unreadable app answer is that row's ERROR, not the gate's
        return {**out, "result": "ERROR", "why": f"the verdict could not be worked out: {type(e).__name__}: {e}"[:300]}


def _judge_app(row: dict, app: dict, out: dict) -> dict:
    from takeoff_engine.scale_verdict import agree, verdict_from_app
    sc = app["scale"]
    out["app_value"] = None if not sc else sc.get("ft_per_paper_inch")
    # the product's verdict (profile witness OFF, coordinator ruling 2), and the same with it ON for reference
    for tag, up in (("", False), ("_profile", True)):
        v = verdict_from_app(app["scale"], app["suggestions"], app["dimcheck"], app["profiles"], use_profile=up)
        out["state" + tag] = v["state"]
        out["value" + tag] = v["value"]
        out["why" + tag] = v["why"]
        t = row["truth"]
        if t is None:
            out["result" + tag] = "NO-TRUTH"
        elif v["value"] is None:
            out["result" + tag] = v["state"]
            pool = v["choices"] or v["offered"]
            out["truth_offered" + tag] = any(agree(c["value"], t) for c in pool)
        else:
            out["result" + tag] = f'{v["state"]}-{"right" if agree(v["value"], t) else "wrong"}'
    return out


ORDER = ("CONFIRMED-right", "CONFIRMED-wrong", "UNCHECKED-right", "UNCHECKED-wrong",
         "CONFLICT", "NONE", "NO-TRUTH", "SKIP", "ERROR")


def totals(judged: list, tag: str = "") -> dict:
    t = {k: 0 for k in ORDER}
    for j in judged:
        k = j.get("result" + tag) or j["result"]
        t[k] = t.get(k, 0) + 1
    return t


def _f(v) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return str(v)


def table(title: str, judged: list) -> str:
    lines = [title, "",
             "| document | page | truth | app value today | state | value | result | "
             "with profile witness (off in the product) | why |",
             "|---|---|---|---|---|---|---|---|---|"]
    for j in judged:
        name = Path(j["document"]).name if "/" in j["document"] or "\\" in j["document"] else j["document"]
        if j.get("result") in ("SKIP", "ERROR"):
            lines.append(f"| {name[:70]} | {j['page']} | {_f(j['truth'])} | - | - | - | "
                         f"{j['result']} | - | {j['why'][:90]} |")
            continue
        r = j["result"] + (" (truth offered)" if j.get("truth_offered") else "")
        r2 = j["result_profile"] + (" (truth offered)" if j.get("truth_offered_profile") else "")
        lines.append(f"| {name[:70]} | {j['page']} | {_f(j['truth'])} | {_f(j.get('app_value'))} | "
                     f"{j['state']} | {_f(j['value'])} | {r} | {r2} | {j['why'][:90]} |")
    for tag, label in (("", "the product: profile witness off"), ("_profile", "profile witness on, for reference")):
        t = totals(judged, tag)
        lines.append("")
        lines.append(f"Totals ({label}): " + ", ".join(f"{k} {t[k]}" for k in ORDER)
                     + f" (rows {len(judged)})")
    return "\n".join(lines)


# ------------------------------------------------------------------------ main
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--truth", default=str(DEFAULT_TRUTH))
    ap.add_argument("--proposed", default=None)
    ap.add_argument("--plans-dir", action="append", default=[],
                    help="folder searched (recursively) for a truth row's file name")
    ap.add_argument("--root", default=str(REPO.parent),
                    help="base for a proposed row's relative document path")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--json", default=None)
    ap.add_argument("--require", action="store_true")
    a = ap.parse_args(argv)
    if a.workers < 1:
        print("--workers must be at least 1", file=sys.stderr)
        return 2
    workers = min(a.workers, MAX_WORKERS)
    if workers != a.workers:
        print(f"--workers capped at {MAX_WORKERS}", file=sys.stderr)

    confirmed = load_truth(Path(a.truth), proposed=False)
    proposed = load_truth(Path(a.proposed), proposed=True) if a.proposed else []
    root = Path(a.root)
    pdirs = [Path(x) for x in a.plans_dir]
    cache: dict = {}
    jobs, where = {}, []
    for row in confirmed + proposed:
        path, why = resolve(row, root, pdirs, cache)
        key = None if path is None else (str(path), row["page"])
        where.append((row, key, why))
        if key is not None:
            jobs[key] = None

    t0 = time.time()
    print(f"scale gate: {len(confirmed)} confirmed row(s), {len(proposed)} proposed row(s), "
          f"{len(jobs)} distinct page(s) to run, {workers} worker(s)", flush=True)
    if workers == 1:
        for k in jobs:
            jobs[k] = run_page(*k)
            print(f"  {Path(k[0]).name[:60]} p{k[1]}: {jobs[k]['s']} s", flush=True)
    else:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futs = {ex.submit(run_page, *k): k for k in jobs}
            for f in as_completed(futs):
                k = futs[f]
                try:
                    jobs[k] = f.result()
                except Exception as e:     # a worker that died: ERROR, named
                    jobs[k] = {"ok": False, "error": f"worker failed: {e}"[:300], "s": None}
                print(f"  {Path(k[0]).name[:60]} p{k[1]}: {jobs[k]['s']} s", flush=True)
    wall = round(time.time() - t0, 1)

    jc = [judge(r, jobs.get(k), why) for r, k, why in where if not r["proposed"]]
    jp = [judge(r, jobs.get(k), why) for r, k, why in where if r["proposed"]]
    print()
    print(table(f"## HUMAN-CONFIRMED truth ({a.truth})", jc))
    if a.proposed:
        print()
        print(table(f"## PROPOSED, NOT HUMAN-CONFIRMED ({a.proposed}) - reported only, never fails the gate",
                    jp))
    print(f"\nwall {wall} s")
    if a.json:
        Path(a.json).write_text(json.dumps({"confirmed": jc, "proposed": jp, "wall_s": wall,
                                            "apps": {f"{Path(k[0]).name}|{k[1]}": v
                                                     for k, v in jobs.items()}},
                                           indent=1, default=str), encoding="utf-8")
    bad = sum(1 for j in jc for tag in ("", "_profile")
              if j.get("result" + tag) == "CONFIRMED-wrong")
    missing = sum(1 for j in jc if j["result"] in ("SKIP", "ERROR"))
    broken = [j for j in jc if j["result"] == "ERROR" and "human-confirmed row has" in (j.get("why") or "")]
    if broken:
        print(f"FAIL: {len(broken)} human-confirmed row(s) carry no usable truth (named above)")
        return 1
    if a.require and not jc:
        print(f"FAIL (--require): no human-confirmed rows in {a.truth} (missing or empty)")
        return 1
    if bad:
        print(f"FAIL: {bad} CONFIRMED-wrong verdict(s) on human-confirmed pages")
        return 1
    if a.require and missing:
        print(f"FAIL (--require): {missing} confirmed row(s) skipped or errored")
        return 1
    print("PASS: 0 CONFIRMED-wrong on human-confirmed pages"
          + (f" ({missing} confirmed row(s) SKIP / ERROR, named above)" if missing else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
