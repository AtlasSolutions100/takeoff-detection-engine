"""FastAPI service + static review UI for the cut/fill engine."""
from __future__ import annotations

import csv
import io
import json
import math
import os
import re
import tempfile
import time

from fastapi import FastAPI, HTTPException, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .project import OUT, Project, crew_map, grade_at, limit_candidates_for, pick_limit, takeoff_preview, walls_for
from . import placement as _placement
from .placement import ACTIVE_ROLES, HANDED, display_for, foreign_scale, inputs_sig, review_conflicts, review_scales, review_stamps, scale_refusals, stale_result
from .takeoff import ZONE_ROW_SEP

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI_DIR = os.environ.get("CUTFILL_UI_DIR") or os.path.join(ROOT, "ui")
# On a hosted box (the host app sets TAKEOFF_HOSTED) nothing may read a client-supplied path.
HOSTED = os.environ.get("TAKEOFF_HOSTED", "").strip().lower() in ("1", "true", "yes")


def _local_only():
    if HOSTED:
        raise HTTPException(403, "not available on a hosted box")
BROWSE_ROOTS = [p for p in [
    os.environ.get("CUTFILL_PLANS_DIR"),
    os.path.join(os.path.dirname(ROOT), "TakeoffPro AI"),
    os.path.expanduser("~"),
] if p and os.path.isdir(p)]

# no generated API docs: built from docstrings and served to every account (critic bid-content-1, RISK-4)
app = FastAPI(title="cutfill-engine", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
_projects: dict[str, Project] = {}
_results: dict[str, dict] = {}
# A host app that embeds this engine sets PROJECT_OK(pid) -> bool to say which
# projects exist as far as it is concerned (CORE: the job is in its registry).
# A project the host disowns is neither listed nor opened, so a folder left
# behind on disk can never come back as a live project.
PROJECT_OK = None
# A host app sets EXPORT_NOTICE() -> str: one CSV row (the accuracy flag) the export ends with, after a blank row.
# CORE: server._csv_flag_line (the server's own notice, BETA_LONG). None: no row (the engine run on its own).
EXPORT_NOTICE = None


def _project_visible(pid: str) -> bool:
    try:
        return PROJECT_OK is None or bool(PROJECT_OK(pid))
    except Exception:
        return False


def get_project(pid: str) -> Project:
    if not _project_visible(pid):
        _projects.pop(pid, None)
        _results.pop(pid, None)
        raise HTTPException(404, "no such project")
    if pid not in _projects:
        if not os.path.exists(os.path.join(OUT, pid, "project.json")):
            raise HTTPException(404, "no such project")
        _projects[pid] = Project(pid)
    return _projects[pid]


def _summary(p: Project) -> dict:
    """The project as the page reads it, with the sheets the scale in force must not measure
    (`scale_refused`, placement.scale_refusals): the page's own measures there - a limit being drawn, a zone's
    or a pad's area, a picked limit's gaps - say so instead of a number (critic 7, r-1)."""
    out = p.summary()
    with p._lock:
        out["scale_refused"] = scale_refusals(p)
        out["review_scales"] = review_scales(p)
        # the stamp of each sheet's review record: the Scale card sends it back with a write (scale verdict S4)
        out["review_stamps"] = review_stamps(p)
        out["review_conflicts"] = review_conflicts(p)
    return out


def _stored_result(pid: str, p: Project) -> dict | None:
    """The last compute's answer (a refusal too), or None."""
    if pid in _results:
        return _results[pid]
    rp = os.path.join(p.dir, "result.json")
    if os.path.exists(rp):
        try:
            res = json.load(open(rp, encoding="utf-8"))
        except (OSError, ValueError):
            return None
        _results.setdefault(pid, res)        # read once: the takeoff asks on every change
        return _results[pid]
    return None


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(UI_DIR, "index.html"), encoding="utf-8") as f:
        return f.read()


@app.get("/api/health")
def health():
    from .ocr import tesseract_path
    return {"ok": True, "engine": __version__, "tesseract": tesseract_path()}


# ------------------------------------------------------------------ projects
@app.get("/api/projects")
def list_projects():
    return [r for r in Project.list_all() if _project_visible(r["id"])]


@app.post("/api/projects")
async def create_project(req: Request):
    body = await req.json()
    p = Project.create(body.get("name", ""))
    _projects[p.id] = p
    path = body.get("path")
    if path:
        if not os.path.exists(path):
            raise HTTPException(400, f"file not found: {path}")
        p.add_document(path)
    return _summary(p)


@app.post("/api/projects/{pid}/docs")
async def upload_doc(pid: str, file: UploadFile = File(...)):
    p = get_project(pid)
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
    tmp.write(await file.read())
    tmp.close()
    rec = p.add_document(tmp.name, name=file.filename)
    os.unlink(tmp.name)
    return {"doc": rec, "project": _summary(p)}


@app.post("/api/projects/{pid}/docs/path")
async def add_doc_path(pid: str, req: Request):
    _local_only()
    p = get_project(pid)
    body = await req.json()
    path = body.get("path", "")
    if not os.path.exists(path):
        raise HTTPException(400, f"file not found: {path}")
    rec = p.add_document(path)
    return {"doc": rec, "project": _summary(p)}


@app.get("/api/projects/{pid}")
def get_project_summary(pid: str):
    return _summary(get_project(pid))


@app.patch("/api/projects/{pid}/settings")
async def patch_settings(pid: str, req: Request):
    p = get_project(pid)
    body = await req.json()
    s = p.data["settings"]
    if isinstance(body.get("zones"), list):
        # a zone's id keys its map cells ("Show only") and its card: two zones with one id would swap them
        ids = [str(z.get("id")) for z in body["zones"] if isinstance(z, dict) and z.get("id") is not None]
        dup = next((i for i in ids if ids.count(i) > 1), None)
        if dup is not None:
            raise HTTPException(409, f"two grading zones share the id {dup}: every zone needs its own id")
    # THE REVIEW WINS (Koby, 2026-09-29): a scale typed or confirmed here, with a sheet on screen that came from the
    # host's review, IS that sheet's review scale - written there (placement.REVIEW_SET), so the page, the cut / fill
    # and the export hold one scale per sheet. Typed with the sheet shown on screen it is the scale in force too, and
    # stays the operator's own word ("typed" or the print confirmed, kept on the sheet with its time), as before: marked
    # "confirmed in review" the compute's own check refused it wherever another sheet prints another scale, and typing
    # again could never lift that (the live check of round 16). Typed with another sheet on screen it sets that sheet's
    # review alone (the one scale in force measures the sheet shown). Run alone (no review), as before
    if body.get("scale_ft_per_in") and str(body.get("scale_source") or "") not in HANDED and _placement.REVIEW_SET is not None:
        k_on = body.get("scale_sheet") if body.get("scale_sheet") in p.data["pages"] else display_for(s, p.data["pages"])
        if k_on:
            try:
                ft_on = float(body["scale_ft_per_in"])
            except (TypeError, ValueError):
                raise HTTPException(422, "the scale must be a number of feet per inch")
            src_on = str(body.get("scale_source") or "")
            text_on = src_on if src_on and src_on != "typed" else f'1" = {ft_on:g}\''
            shown_after = display_for({**s, **({"display_page": body["display_page"]} if "display_page" in body else {})},
                                      p.data["pages"])
            from .project import _sheet_words
            if k_on != shown_after and (p.data["pages"].get(k_on) or {}).get("role") not in ACTIVE_ROLES:
                # a sheet that takes no part: its scale is not the cut / fill's to set (typed there, it rewrote that
                # sheet's review - a utility sheet's lengths doubled, the status said "Scale set": critic 17, WRONG-4)
                w = _sheet_words(p, k_on)
                raise HTTPException(409, f"{w[:1].upper() + w[1:]} takes no part in the cut / fill, so its scale is not set "
                                         f"here: set it in its own review, or mark the sheet existing / proposed first. "
                                         f"Nothing was changed.")
            # a printed reading chosen in the card is not a typed number: the review records how (CRITIC-scale-1 M2)
            _tok = _placement.REVIEW_SET_CHOSEN.set(bool(src_on) and src_on != "typed")
            # the stamp of the review record this card saw (scale verdict S4): a stale card writes nothing
            # (an HTTP write with no stamp sends "" - refused like a stale one, CRITIC-scale-3 MINOR 4; None is in-process)
            _tok2 = _placement.REVIEW_SET_SEEN.set(str(body["scale_seen"]) if body.get("scale_seen") else "")
            try:
                got = _placement.REVIEW_SET(p, k_on, ft_on, text_on)
            finally:
                _placement.REVIEW_SET_SEEN.reset(_tok2)
                _placement.REVIEW_SET_CHOSEN.reset(_tok)
            if got is False:
                raise HTTPException(503, "the scale could not be recorded in the review: type it again")
            if not got and k_on != shown_after:
                # another sheet with no review here: the scale in force is the sheet shown's, and there is no record of
                # this sheet's own to set
                w = _sheet_words(p, k_on)
                raise HTTPException(409, f"{w[:1].upper() + w[1:]} is not the sheet shown and has no review on record here "
                                         f"(not detected in review yet, or not a plan set from the review), so its scale is "
                                         f"not set here. The scale in force is set with {_sheet_words(p, shown_after)} (the "
                                         f"sheet shown) on screen. Nothing was changed.")
            if got and k_on != shown_after:
                body = {kk: vv for kk, vv in body.items() if kk not in ("scale_ft_per_in", "scale_source", "scale_page")}
                # the review holds this sheet's scale now: a word typed for it before (kept apart, as a reading) is
                # superseded - left, it refused the sheet whenever the review kept an older pick of the same number
                ps_on = p.page_settings(k_on)
                ps_on.pop("operator_scale", None)
                ps_on.pop("operator_scale_at", None)
            elif got:
                # a read-only review's own number is put in force exactly: the typed one within its 1 % measured the
                # volume a couple of per cent either way, and the compute then refused it (critic 20, MINOR-1 / MINOR-2)
                rv_on = _placement.review_scale(p, k_on)
                if rv_on and rv_on.get("writable") is False:
                    body = dict(body, scale_ft_per_in=rv_on["ft"])
                body = dict(body, scale_page=k_on)     # the sheet whose review holds it (the operator's word, below)
    for k, v in body.items():
        if k == "scale_sheet":
            continue                     # the sheet the operator looked at when setting a scale (below), not a setting
        if k == "rules" and isinstance(v, dict):
            s.setdefault("rules", {}).update(v)
        elif k == "takeoff" and isinstance(v, dict):
            s.setdefault("takeoff", {}).update(v)
        elif k == "layers":
            # the named layer stack under the proposed grade (asphalt, base...): its total is the
            # section compute lowers the proposed surface by; a section typed without layers stands
            layers = [{"name": str(l.get("name") or "")[:40], "thickness_in": float(l.get("thickness_in") or 0.0)}
                      for l in (v or []) if isinstance(l, dict)]
            s["layers"] = layers
            s["section_ft"] = round(sum(l["thickness_in"] for l in layers) / 12.0, 4)
        else:
            s[k] = v
    if "region" in body and "limit" not in body:
        # a limit set here was drawn (or cleared); a picked line's record comes through /limit/pick
        s["limit"] = {"source": "drawn"} if body.get("region") else None
    if body.get("scale_ft_per_in") and str(s.get("scale_source") or "") not in HANDED:
        # the operator's scale, with its time: a reviewer's pick made after it outranks it (placement.foreign_scale;
        # critic 8, WRONG-2), and it is kept on the sheet the operator was looking at (the page names it; else the
        # sheet shown): a later open that hands over a scale read on another sheet is checked against it there, so a
        # round trip through that sheet no longer asks for it again (critic 7, r-2; critic 8, r-8)
        now = time.time()
        s["scale_set_at"] = now
        k = body.get("scale_sheet") if body.get("scale_sheet") in p.data["pages"] else display_for(s, p.data["pages"])
        if k:
            try:
                p.page_settings(k)["operator_scale"] = float(body["scale_ft_per_in"])
                p.page_settings(k)["operator_scale_at"] = now
            except (TypeError, ValueError):
                pass
    elif "scale_ft_per_in" in body and not body.get("scale_ft_per_in"):
        # the scale cleared: the operator's word for a sheet goes with it, on every sheet it was kept on (typed looking at
        # 3.1, the clear left 3.1's 50 and its time: critic 9, m1; critic 8, MINOR-6), and the time it was set
        s.pop("scale_set_at", None)
        for v in p.data["pages"].values():
            if isinstance(v, dict):
                v.pop("operator_scale", None)
                v.pop("operator_scale_at", None)
    p.save()
    return _summary(p)


# a sheet's records only the host writes (its hand-over of a detection, its marks) and the words the settings route keeps
_HOST_PAGE_KEYS = ("core_result", "core_sig", "core_redo_at", "host_seen", "operator_scale", "operator_scale_at",
                   "review_scale")


@app.patch("/api/projects/{pid}/pages/{key}")
async def patch_page(pid: str, key: str, req: Request):
    """Operator records for a sheet. Records that name a line (typed heights, removed or
    replaced pieces, labels attached to a line) are anchored to the line's geometry so
    they survive a fresh reading of the sheet."""
    p = get_project(pid)
    body = await req.json()
    # the host's own records of a sheet are the host's to write: a `core_result` sent here pointed a sheet at another
    # client's job, and the Scale card then set that job's review scale (critic 17, WRONG-2); the operator's scale words
    # are kept by the settings route with their time
    host_owned = sorted(k for k in (body or {}) if k in _HOST_PAGE_KEYS)
    if host_owned:
        raise HTTPException(403, f"{', '.join(host_owned)}: set by the host, not here")
    with p._lock:
        p.migrate_records(key)
        ps = p.page_settings(key)
        for k, v in body.items():
            if k == "typed_heights" and isinstance(v, dict):
                for cid, z in v.items():
                    try:
                        p.set_typed(key, int(cid), z)
                    except ValueError as e:
                        raise HTTPException(409, str(e))
            elif k in ("line_roles", "piece_roles") and isinstance(v, dict):
                # one line switched to the other surface: {contour id | chain id: "existing" | "proposed" | null}
                for cid, role in v.items():
                    try:
                        if k == "line_roles":
                            p.set_line_role(key, role, contour_id=int(cid))
                        else:
                            p.set_line_role(key, role, chain_id=int(cid))
                    except ValueError as e:
                        raise HTTPException(409, str(e))
            elif k == "family_roles" and isinstance(v, dict):
                for sid, role in v.items():
                    try:
                        p.set_style_role(key, int(sid), role)
                    except ValueError as e:
                        raise HTTPException(409, str(e))
            elif k == "label_overrides" and isinstance(v, dict):
                d = ps.setdefault(k, {})
                for kk, vv in v.items():
                    if vv is None:
                        d.pop(str(kk), None)
                        continue
                    if k == "label_overrides" and isinstance(vv, dict) and vv.get("attach") is not None:
                        vv = dict(vv)
                        vv["attach_pts"] = p.anchor_for_chain(key, int(vv["attach"]))
                    d[str(kk)] = vv
            elif k == "pads" and isinstance(v, dict):
                # building pads: {pad key: {"use": true | false} | null}. The record keeps where the
                # floor height is printed and what it says, so it finds its pad again after a
                # re-read; a record an older key found is replaced, never doubled
                from .project import pads_in_force
                an = p.load_analysis(key) or {}
                offered = {pd["key"]: pd for pd in (an.get("pads") or [])}
                held = {pd["key"]: rk for pd, rk, _rec in pads_in_force(an, ps)[0]}
                d = ps.setdefault("pads", {})
                for pk, rec in v.items():
                    pk = str(pk)
                    if rec is None:
                        d.pop(held.get(pk, pk), None)
                        d.pop(pk, None)
                        continue
                    pd = offered.get(pk)
                    if pd is None:
                        raise HTTPException(409, "That building pad is not on this read of the sheet: read the sheet again.")
                    if rec.get("use") and not pd.get("polygon"):
                        raise HTTPException(409, "No closed outline is drawn round that floor height: draw the pad as a line instead.")
                    d.pop(held.get(pk, pk), None)
                    d[pk] = {"use": bool(rec.get("use")), "at": pd["at"], "z": pd["z"], "text": pd["text"],
                             "area_pt2": pd.get("area_pt2"), "set": time.time()}
            elif k == "edits" and isinstance(v, dict):
                # operator line edits: {"lines": {id: line | null}, "deleted": [chain ids]}
                ed = ps.setdefault("edits", {})
                ed.setdefault("lines", {})
                ed.setdefault("deleted", [])
                ed.setdefault("anchors", {})
                for lid, ln in (v.get("lines") or {}).items():
                    if ln is None:
                        ed["lines"].pop(str(lid), None)
                        continue
                    ln = dict(ln)
                    if ln.get("chain") is not None and not ln.get("chain_pts"):
                        ln["chain_pts"] = p.anchor_for_chain(key, int(ln["chain"]))
                    ln["set"] = time.time()           # the later of an edit and a switch on one line wins
                    ed["lines"][str(lid)] = ln
                if "deleted" in v and isinstance(v["deleted"], list):
                    ed["deleted"] = [int(x) for x in v["deleted"]]
                    ed["anchors"] = {str(c): (ed["anchors"].get(str(c)) or p.anchor_for_chain(key, c))
                                     for c in ed["deleted"]}
            else:
                ps[k] = v
        p.save()
    return _summary(p)


# --------------------------------------------------------------------- pages
# What one page render may cost: the page rasterises in this server's own process, so a request decides how
# much memory it takes (D41 / trap B8: `/clip` took any w, h and dpi; `?w=5000&h=5000&dpi=2400` asks for tens
# of GB). The page asks for the whole sheet at 100 dpi and for the zoomed view at up to 400 dpi with a margin
# (webui/cutfill/app.js imageUrl / maybeFetchClip): about 1.5 screens of pixels, 19 million on a 4K screen.
RENDER_DPI = (20.0, 400.0)
RENDER_MAX_PX = 40_000_000           # about 120 MB of RGB before the PNG; a bigger ask is drawn at a lower dpi
CLIP_MAX_PT = 400.0                  # a label thumbnail (/clip) is 80 x 40 pt by default
# Every render says the dpi it was really drawn at (X-Render-DPI): a caller that places the picture by its own
# dpi must use that one (critic WRONG-2: the Overlay drew a lowered page shrunk and out of place). The engine's
# page draws every picture into its page rectangle, whatever its pixel size.
RENDER_DPI_HEADER = "X-Render-DPI"


def _finite(*vals) -> None:
    for v in vals:
        if v is not None and not math.isfinite(v):
            raise HTTPException(422, "not a number")


def _render_dpi(doc, pi: int, dpi: float, clip) -> int:
    """dpi clamped to RENDER_DPI, then lowered until the picture (the clip inside the page, or the page) holds
    at most RENDER_MAX_PX pixels. The page draws every image into its own rectangle, so a lower dpi only
    makes a giant ask blurrier, never misplaced. Whole dpi: Document.render draws at a whole dpi, and the
    caller is told that one (X-Render-DPI); a lowered dpi is rounded down so the picture stays in budget."""
    dpi = max(RENDER_DPI[0], min(float(dpi), RENDER_DPI[1]))
    r = doc.doc[pi].rect
    w, h = float(r.width), float(r.height)
    if clip is not None:
        w = max(0.0, min(clip[2], r.x1) - max(clip[0], r.x0))
        h = max(0.0, min(clip[3], r.y1) - max(clip[1], r.y0))
    px = (w * dpi / 72.0) * (h * dpi / 72.0)
    if px > RENDER_MAX_PX:
        return max(1, int(math.floor(72.0 * math.sqrt(RENDER_MAX_PX / (w * h)))))
    return int(round(dpi))


@app.get("/api/projects/{pid}/pages/{key}/image")
def page_image(pid: str, key: str, dpi: float = 100, x0: float | None = None, y0: float | None = None,
               x1: float | None = None, y1: float | None = None):
    _finite(dpi, x0, y0, x1, y1)
    p = get_project(pid)
    di, pi = int(key[1:].split("p")[0]), int(key.split("p")[1])
    doc = p.doc(di)
    clip = None
    if None not in (x0, y0, x1, y1):
        clip = (x0, y0, x1, y1)
    used = _render_dpi(doc, pi, dpi, clip)
    png = doc.render(pi, dpi=used, clip=clip)
    return Response(png, media_type="image/png", headers={"Cache-Control": "max-age=3600", RENDER_DPI_HEADER: str(used)})


@app.post("/api/projects/{pid}/pages/{key}/analyze")
def analyze(pid: str, key: str, force: int = 0, full: int = 0):
    """force=1: interpret the cached reading again (seconds). full=1: read the sheet from scratch."""
    p = get_project(pid)
    return p.start_analysis(key, force=bool(force), full=bool(full))


@app.get("/api/projects/{pid}/pages/{key}/analysis")
def analysis(pid: str, key: str):
    p = get_project(pid)
    st = p.analysis_status(key)
    if st["status"] == "done":
        data = p.load_analysis(key)
        if data and data.get("pads"):
            # which of the offered building pads the operator accepted, by the engine's own
            # matching (the one the volumes use), so the card and the compute cannot disagree
            from .project import pads_in_force
            held = {pd["key"]: bool(rec.get("use")) for pd, _rk, rec in pads_in_force(data, p.page_settings(key))[0]}
            for pd in data["pads"]:
                pd["use"] = held.get(pd["key"], False)
        return {"status": "done", "data": data}
    return st


@app.get("/api/projects/{pid}/pages/{key}/clip")
def clip_image(pid: str, key: str, x: float, y: float, w: float = 80, h: float = 40, dpi: float = 300):
    """A crop around a point (label evidence thumbnails). It took any w, h and dpi (trap B8); now a size past
    CLIP_MAX_PT or a dpi outside RENDER_DPI is refused (422), never drawn as something else than asked. Nothing
    in the page asks for it today (critic CRITIC-mem-1)."""
    _finite(x, y, w, h, dpi)
    if not (0 < w <= CLIP_MAX_PT and 0 < h <= CLIP_MAX_PT):
        raise HTTPException(422, f"A crop is at most {CLIP_MAX_PT:.0f} x {CLIP_MAX_PT:.0f} pt")
    if not (RENDER_DPI[0] <= dpi <= RENDER_DPI[1]):
        raise HTTPException(422, f"A crop is drawn at {RENDER_DPI[0]:.0f} to {RENDER_DPI[1]:.0f} dpi")
    p = get_project(pid)
    di, pi = int(key[1:].split("p")[0]), int(key.split("p")[1])
    doc = p.doc(di)
    clip = (x - w / 2, y - h / 2, x + w / 2, y + h / 2)
    used = _render_dpi(doc, pi, dpi, clip)       # 400 pt at 400 dpi is 4.9 million pixels: never lowered
    png = doc.render(pi, dpi=used, clip=clip)
    return Response(png, media_type="image/png", headers={RENDER_DPI_HEADER: str(used)})


# ------------------------------------------------------------------- compute
@app.post("/api/projects/{pid}/compute")
def compute(pid: str):
    p = get_project(pid)
    t0 = time.time()
    # a scale read on another sheet that the sheet shown's own readings do not confirm (or contradict), or one its own
    # review has corrected since: the compute refuses until the sheet's scale is confirmed (placement.foreign_scale;
    # critics 4 to 7). An unread sheet shown is not passed through any more: the other sheets were measured on its
    # frame at their own scale (critic 7, r-4)
    with p._lock:
        why = foreign_scale(p)
        # what this compute reads, taken before it runs (an input changed during it makes the result stale, never
        # fresh): stale_result compares it with now (critic 8, r-7)
        sig = None if why else inputs_sig(p)
    res = {"error": why, "warnings": []} if why else p.compute()
    if sig is not None and not res.get("error"):
        res["inputs"] = sig
    res["seconds"] = round(time.time() - t0, 2)
    _results[pid] = res
    try:
        with open(os.path.join(p.dir, "result.json"), "w", encoding="utf-8") as f:
            json.dump(res, f)
    except Exception:
        pass
    return res


@app.get("/api/projects/{pid}/transforms")
def transforms(pid: str):
    """Where each sheet sits on the sheet shown (the same page_transform the compute uses), with no
    volumes computed: the Overlay places another sheet and its contour lines right after alignment."""
    from .placement import transforms_for
    return transforms_for(get_project(pid))


@app.get("/api/projects/{pid}/result")
def result(pid: str):
    """The last compute's answer. One whose sheet shown or scale changed since, or whose scale is refused on its sheet
    now, says why in `stale_why` (placement.stale_result): the page marks it stale and the host takes its rows out
    (critic 7, WRONG-2: a reload filed its volumes as fresh)."""
    p = get_project(pid)
    res = _stored_result(pid, p)
    if res is None:
        return {"error": "not computed"}
    with p._lock:
        gone = stale_result(p, res)
    return dict(res, stale_why=gone) if gone else res


# ------------------------------------------- the limit line, the bid rows, zones, walls, the map
@app.get("/api/projects/{pid}/pages/{key}/limit_candidates")
def limit_candidates_route(pid: str, key: str):
    """Families on this sheet whose name says limit line, and the erosion-control families."""
    p = get_project(pid)
    out = limit_candidates_for(p, key)
    why = _scale_refused(p, key)
    if why:                              # the families stand; no length is measured at an unchecked scale
        for d in (out.get("limit") or []) + (out.get("erosion") or []):
            d["length_ft"] = None
        # its own key: `scale` always says where the scale came from ("confirmed"), and the page read that as a refusal
        # - "No length is measured here: confirmed", in red, on every sheet (the live check of critic 8's round)
        out["scale_refused"] = why
    return out


def _scale_refused(p, key=None) -> str | None:
    """placement.foreign_scale under the project's lock: a scale read on another sheet, not confirmed on this one,
    measures nothing here (critic 6, WRONG-1: the limit's rows, its area, the offers' lengths and the walls used it)."""
    with p._lock:
        return foreign_scale(p, key)


@app.post("/api/projects/{pid}/limit/pick")
async def limit_pick_route(pid: str, req: Request):
    """The limit from the sheet's own line: {page, x, y} (a click on a line) or {page, family, src}
    (a named family). The seed's family is stitched end to end into the ring; the record says how."""
    p = get_project(pid)
    body = await req.json()
    try:
        res = pick_limit(p, str(body.get("page") or ""), x=body.get("x"), y=body.get("y"),
                         family=body.get("family"), src=body.get("src"), tol=body.get("tol"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    why = _scale_refused(p, str(body.get("page") or "") or None)
    if why:
        # the ring stands; nothing on it is measured at an unchecked scale - its area, and the gaps the status names
        # in points, not feet (critic 7, r-1: "largest 20 ft; the ends are 20 ft apart", then the refusal)
        from .takeoff import stitch_status
        lm = res.get("limit") or {}
        res["metrics"] = None
        res["status"] = (stitch_status({"n": lm.get("n_lines") or 0, "joins": lm.get("joins") or [],
                                        "leftover": lm.get("leftover"), "gap": lm.get("gap_pt") or 0}, None)
                         + f". {why}")
    return res


@app.get("/api/projects/{pid}/takeoff")
def takeoff_route(pid: str):
    """The bid rows as they stand: the limit's rows and the strip need no volumes; the cut / fill
    rows use the last computed result when it still stands. None while the scale in force is not checked for the
    sheet shown (_scale_refused). A stored result that no longer stands (placement.stale_result: its sheet shown or
    scale changed, or its scale is refused now) gives no rows here, and `result_stale` says why: the page marks it
    stale and the host takes its volumes out (critic 7, WRONG-2: they were filed as fresh after the refusal; WRONG-3:
    volumes at 50 were listed beside the limit's rows at 40)."""
    p = get_project(pid)
    stored = _stored_result(pid, p)
    with p._lock:
        why = foreign_scale(p)
        gone = stale_result(p, stored)
    if why:
        out = {"rows": [], "notes": [why], "scale": "not checked for the sheet shown",
               "page": display_for(p.data["settings"], p.data["pages"])}
    else:
        out = takeoff_preview(p)
        if gone:
            out["rows"] = [r for r in out.get("rows") or [] if not _RESULT_ROW.match(str(r.get("label") or ""))]
            out.pop("zones_total", None)
            out["computed"] = False
            out["notes"] = [f"No cut / fill rows: {gone}. Press Compute."] + list(out.get("notes") or [])
    if gone:
        out["result_stale"] = gone
    # the result this answer judged: an answer about an earlier result (a request that crossed a compute) says nothing
    # about the one the page holds now (critic 8, r-3: a fresh result was marked stale by a late answer)
    out["result_at"] = (stored or {}).get("computed_at")
    # the job's last compute was refused: a page still holding the good result before it takes the refusal in (two
    # tabs: it posted its volumes as fresh beside the limit's rows: critic 9, r-5)
    out["result_refused"] = bool((stored or {}).get("error"))
    return out


# the rows a compute's result makes (takeoff_rows' volumes and net, project._append_result_rows' walls): the page's
# RESULT_ROWS and the host's rows read the same labels
_RESULT_ROW = re.compile(r"^(Site Cut|Site Fill)(" + re.escape(ZONE_ROW_SEP) + r".+)?$|^(Export Cut|Import Structural|Site walls face)$")


@app.post("/api/projects/{pid}/grade_at")
async def grade_at_route(pid: str, req: Request):
    """Heights of a surface under points on a sheet: {page, pts: [[x, y], ...], surface}."""
    p = get_project(pid)
    body = await req.json()
    pts = body.get("pts") or []
    if len(pts) > 20000:
        raise HTTPException(400, "too many points")
    return grade_at(p, str(body.get("page") or ""), pts, str(body.get("surface") or "existing"))


@app.get("/api/projects/{pid}/pages/{key}/walls")
def walls_route(pid: str, key: str):
    """The site walls on a sheet from its TW / BW grades (height, face SF, dig depth where the
    existing surface is known)."""
    p = get_project(pid)
    why = _scale_refused(p, key)
    if why:
        return {"walls": [], "incomplete": [], "lf": 0.0, "face_sf": 0.0, "note": why}
    return walls_for(p, key)


@app.get("/api/projects/{pid}/crewmap.png")
def crewmap_route(pid: str, block_ft: float = 30.0):
    """The printable crew map of the last computed result (red cut / blue fill, one printed
    depth per block)."""
    p = get_project(pid)
    res = result(pid)
    if "error" in res or not res.get("grid"):
        raise HTTPException(400, res.get("error") or "not computed")
    if res.get("stale_why"):             # its inputs moved on: the crew never gets depths no compute makes now
        raise HTTPException(409, f"the last compute is stale: {res['stale_why']}: press Compute")
    try:
        png = crew_map(p, res, block_ft=max(5.0, min(float(block_ft or 30.0), 500.0)))
    except Exception as e:      # noqa: BLE001
        raise HTTPException(500, f"could not draw the map: {e}")
    return Response(png, media_type="image/png", headers={"Content-Disposition": "inline; filename=crew-map.png"})


@app.get("/api/projects/{pid}/export.csv")
def export_csv(pid: str):
    res = result(pid)
    if "error" in res:
        raise HTTPException(400, res["error"])
    if res.get("stale_why"):
        raise HTTPException(409, f"the last compute is stale: {res['stale_why']}: press Compute")
    g = res["grid"]
    from .takeoff import grid_section, grid_sub
    sub, sec = grid_sub(res), grid_section(res)      # an older result's grid is at finished grade
    wts = g.get("w")
    # itemized grading zones: every cell has its own depth (zsec) and its own cut / fill (cutc /
    # fillc, each part of a split cell at its own depth), so the rows still add up to the totals
    zsec, cutc, fillc = g.get("zsec"), g.get("cutc"), g.get("fillc")
    # keyed on the result being itemized, never on a zone being deeper than 0: zones at 0 under a
    # layer stack printed the stack's datum in every cell and missed the totals by 38 CY
    zoned = bool(res.get("zones_itemized")) and bool(zsec)
    t = res["totals"]
    num = lambda v: float(v or 0.0)                  # noqa: E731 - a zero prints as 0, never -0
    buf = io.StringIO()
    w = csv.writer(buf)
    # The file says what it is in the lines above its column names, one line to a cell: the totals
    # sit there and never in the data columns (a TOTAL row under the cells put the area in the
    # cut_cy column, and a spreadsheet's sum of it gave 499,492 CY for 31,233 - check 5A).
    w.writerow([f"cut / fill cells. The rows below add up to: cut {num(t['cut_cy']):,.1f} CY, fill {num(t['fill_cy']):,.1f} CY, "
                f"net {num(t['net_cy']):+,.1f} CY (+ fill), on {num(t['area_sf']):,.0f} SF; the surfaces cover "
                f"{100 * num(t['coverage']):.1f} % of it. A cell on the limit's edge holds only its part inside (the inside column)."])
    # under a layer stack the finished grade and the subgrade have a column each (the proposed column
    # used to hold the subgrade): the depth and the volumes are to the subgrade, like the totals
    if zoned:
        w.writerow(["proposed_ft is the finished grade the sheet prints; subgrade_ft is the cell's own zone depth below it "
                    "(zone_depth_ft; blank zones and the rest of the limit at the layer stack): the depth and the volumes are to "
                    "the subgrade." + (" A cell two zones share holds each part at its own depth." if cutc and fillc else "")])
    elif sec:
        w.writerow([f"proposed_ft is the finished grade the sheet prints; subgrade_ft is {sec:g} ft below it "
                    f"(the layer stack): the depth and the volumes are to the subgrade."])
    has_sg = bool(sec) or zoned
    w.writerow(["row", "col", "x_pt", "y_pt", "existing_ft", "proposed_ft"] + (["subgrade_ft"] if has_sg else [])
               + (["zone_depth_ft"] if zoned else []) + ["depth_ft(+fill/-cut)", "cut_cy", "fill_cy", "inside"])
    cell = res["cell_ft"]
    for j in range(g["ny"]):
        for i in range(g["nx"]):
            k = j * g["nx"] + i
            eg, fg = g["eg"][k], g["fg"][k]
            if eg is None or fg is None:
                continue
            sg = round(fg - sub, 3)          # the surface the volumes were taken to
            dk = float(zsec[k]) if zoned and zsec[k] is not None else sec
            fin = round(sg + dk, 3)          # the finished grade the sheet prints
            d = sg - eg
            wt = 1.0 if not wts or wts[k] is None else float(wts[k])
            if zoned and cutc and fillc:
                cv = round(float(cutc[k] or 0.0) * cell * cell / 27.0, 3) + 0.0
                fv = round(float(fillc[k] or 0.0) * cell * cell / 27.0, 3) + 0.0
            else:
                vol = abs(d) * cell * cell * wt / 27.0
                cv, fv = (round(vol, 3) if d < 0 else 0), (round(vol, 3) if d > 0 else 0)
            w.writerow([j, i, round(g["x0"] + (i + 0.5) * g["cell_pt"], 1), round(g["y0"] + (j + 0.5) * g["cell_pt"], 1),
                        eg, fin] + ([sg] if has_sg else []) + ([round(dk, 4)] if zoned else [])
                       + [round(d, 3), cv, fv, round(wt, 4)])
    text = buf.getvalue()
    if EXPORT_NOTICE is not None:
        text = text.rstrip("\n") + "\n\n" + str(EXPORT_NOTICE()) + "\n"
    return Response(text, media_type="text/csv", headers={"Content-Disposition": "attachment; filename=cutfill.csv"})


# --------------------------------------------------------------------- files
@app.get("/api/files")
def files(dir: str = ""):
    """Browse local folders for PDFs (local tool; limited to the plan roots)."""
    _local_only()
    if not dir:
        return {"dir": "", "entries": [{"name": r, "path": r, "dir": True} for r in BROWSE_ROOTS]}
    d = os.path.abspath(dir)
    if not any(d.startswith(os.path.abspath(r)) for r in BROWSE_ROOTS):
        raise HTTPException(403, "outside the allowed folders")
    if not os.path.isdir(d):
        raise HTTPException(404, "no such folder")
    entries = []
    try:
        for name in sorted(os.listdir(d)):
            full = os.path.join(d, name)
            if os.path.isdir(full):
                entries.append({"name": name, "path": full, "dir": True})
            elif name.lower().endswith(".pdf"):
                entries.append({"name": name, "path": full, "dir": False, "size": os.path.getsize(full)})
    except PermissionError:
        pass
    return {"dir": d, "parent": os.path.dirname(d), "entries": entries}


@app.post("/api/verify/capture")
async def verify_capture(req: Request, topic: str = "ui", name: str = "capture"):
    """Save a PNG posted by the review UI (canvas.toBlob) under out/verify/<date>/<topic>/."""
    _local_only()
    import re as _re
    body = await req.body()
    day = time.strftime("%Y-%m-%d")
    safe = lambda x: _re.sub(r"[^A-Za-z0-9_.-]+", "_", x)[:80]
    d = os.path.join(ROOT, "out", "verify", day, safe(topic))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, safe(name) + ".png")
    with open(path, "wb") as f:
        f.write(body)
    return {"saved": path, "bytes": len(body)}


app.mount("/ui", StaticFiles(directory=UI_DIR), name="ui")
