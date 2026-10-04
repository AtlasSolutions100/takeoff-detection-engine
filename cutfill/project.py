"""Project state, per-page analysis (cached) and the cut/fill computation."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import pickle
import shutil
import sys
import threading
import time
import uuid
from collections import Counter, defaultdict
from dataclasses import asdict, replace

import numpy as np
import shapely
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

from . import __version__
from .align import identity
from .chains import Chain, build_chains, find_gaps
from .elevations import (Contour, Family, Spot, OPERATOR_HOWS, SWITCH_STYLES, USER_STYLES, build_contours, contour_capable_styles, contour_families, drawn_shares, find_legend_boxes,
                         leave_out_unlisted_pens, find_floor_pads, pad_points,
                         find_profile_boxes, find_table_boxes, infer_intervals, mark_legend_samples, place_spots, propagate_global, propagate_heights,
                         qualify_families, read_legend_roles, roles_by_label_ink, screened_heights_as_points, verify_heights)
from .geom import SheetTooBig, apply_affine, poly_length, polygon_area, resample
from .ingest import Document
from .labels import Label, make_label, resolve_values, find_label_frames, upright_heights
from .readslot import STILL_STOPPING, WAITING, ReadTooLong, read_slot
from .ocr import group_lines, ink_gray, ocr_page_tiles, ocr_text_clusters, tesseract_path
from .glyphs import find_text_clusters, glyph_stroke_ids, merge_clusters_on_baseline
from .scale import pick_default, scale_candidates
from .surface import Evidence, Surface, contour_points, cut_fill_grid, polygonal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# The project store. CUTFILL_OUT lets a host app keep it in its own job store (outside OneDrive).
OUT = os.environ.get("CUTFILL_OUT") or os.path.join(ROOT, "out", "projects")
ENGINE = __version__
OCR_DPI = 200
# The whole-sheet OCR's RGB step (Document.render_rgb_array) holds the pixmap, its samples copy and the array at
# once: 9.02 B a pixel at peak, measured by critic 2 on an 80 million pixel page (critic-mem2/work/ocr_px.log; the
# gray step before it: 3.01 B). 2.0 GB / 9.02 B = 221.7 million pixels: OCR_MAX_PX = 220 million. A 36 x 48 in
# sheet is 69.1 million pixels at 200 dpi (3.2 times under); the largest of the 6,473 pages in the plan corpus,
# 42 x 30 in, is 50.4 million (4.4 times under). A 36 x 120 in roll (172.8 million) still reads.
OCR_BYTES_PER_PX = 9.02
OCR_MAX_PX = 220_000_000
def _reader_source_stamp() -> str:
    """A hash of the reader's own source: a cached reading is reused only when the code that made it
    is the code running now. Until 2026-09-26 the cache was keyed by a hand-bumped number, so a
    project kept serving a reading made by older chaining as if it were current (Koby's rule: a
    re-read always reports what the engine reads now, and says what changed)."""
    h = hashlib.sha1()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("ingest.py", "chains.py", "glyphs.py", "labels.py", "ocr.py", "geom.py", "core_reading.py", "project.py"):
        try:
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:12]


READER = "3+" + _reader_source_stamp()   # the reader's source hash: older cached readings are read again


def _interp_source_stamp() -> str:
    """The interpretation's source hash (heights, families, intervals, verification, records): a
    stored analysis made by other code is stale and is interpreted again before it is shown."""
    h = hashlib.sha1()
    here = os.path.dirname(os.path.abspath(__file__))
    for name in ("elevations.py", "labels.py", "scale.py", "align.py", "surface.py", "core_reading.py", "core_edits.py",
                 "project.py"):
        try:
            with open(os.path.join(here, name), "rb") as f:
                h.update(f.read())
        except OSError:
            h.update(name.encode())
    return h.hexdigest()[:12]


INTERP = _interp_source_stamp()
UL_CHAIN_BASE = 10_000_000      # chain ids of drawn / edited lines (never collide with detected pieces)
KEPT_CHAIN_BASE = 20_000_000    # chain ids of lines drawn from a record whose line the reading no longer has

DEFAULT_RULES = {
    "wall_footing_depth_ft": 0.0,      # BW - d becomes a proposed point at the wall base (0 = use BW itself)
    "untagged_spots": "proposed",      # side for untagged spot grades on grading sheets
}


def _page_key(doc_i: int, page_i: int) -> str:
    return f"d{doc_i}p{page_i}"


def _parse_key(key: str) -> tuple[int, int]:
    d, p = key[1:].split("p")
    return int(d), int(p)


class Project:
    def __init__(self, pid: str):
        self.id = pid
        self.dir = os.path.join(OUT, pid)
        os.makedirs(os.path.join(self.dir, "analysis"), exist_ok=True)
        self.path = os.path.join(self.dir, "project.json")
        self._docs: dict[int, Document] = {}
        self._lock = threading.RLock()
        self.jobs: dict[str, dict] = {}     # page_key -> {"status", "progress", "error", "full", "rerun"}
        self._readings: dict[str, bytes] = {}          # page_key -> pickled sheet reading (vectors, text, lines)
        self._an_cache: dict[str, tuple[float, dict]] = {}   # page_key -> (mtime, analysis json)
        self._closed = False                            # set by close(): nothing is written after it
        self._threads: dict[str, threading.Thread] = {}  # page_key -> its last read's thread (alive while it winds down)
        if os.path.exists(self.path):
            self.data = json.load(open(self.path, encoding="utf-8"))
        else:
            self.data = {"id": pid, "name": "", "created": time.time(), "docs": [], "pages": {},
                         "settings": {"scale_ft_per_in": None, "scale_source": None, "datum": None, "cell_ft": 10.0,
                                      "region": None, "display_page": None, "rules": dict(DEFAULT_RULES)}}
            self.save()

    def close(self) -> None:
        """Let go of everything held in memory for this project: the open
        pymupdf documents (a handle on the job's PDF, which on Windows keeps
        the file from being deleted), the pickled readings and the analysis
        cache. A running analysis finds the flag and writes nothing. The
        host calls this when the job is deleted, reset or opened fresh."""
        with self._lock:
            self._closed = True
            for d in list(self._docs.values()):
                try:
                    d.close()
                except Exception:
                    pass
            self._docs.clear()
            self._readings.clear()
            self._an_cache.clear()

    # ----------------------------------------------------------------- store
    def save(self):
        with self._lock:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=1)
            os.replace(tmp, self.path)

    @staticmethod
    def create(name: str = "", pid: str | None = None) -> "Project":
        """A new project; `pid` lets a host app key the project by its own job id."""
        pid = pid or (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6])
        p = Project(pid)
        p.data["name"] = name
        p.save()
        return p

    @staticmethod
    def list_all() -> list[dict]:
        out = []
        if not os.path.isdir(OUT):
            return out
        for pid in sorted(os.listdir(OUT), reverse=True):
            pj = os.path.join(OUT, pid, "project.json")
            if os.path.exists(pj):
                try:
                    d = json.load(open(pj, encoding="utf-8"))
                    out.append({"id": pid, "name": d.get("name", ""), "created": d.get("created"),
                                "docs": [{"name": x["name"], "n_pages": x["n_pages"]} for x in d.get("docs", [])]})
                except Exception:
                    continue
        return out

    def add_document(self, src_path: str, name: str | None = None, copy: bool = True) -> dict:
        di = len(self.data["docs"])
        name = name or os.path.basename(src_path)
        if copy:
            dst = os.path.join(self.dir, f"doc{di}.pdf")
            shutil.copyfile(src_path, dst)
        else:
            dst = src_path
        doc = Document(dst)
        h = hashlib.sha1()
        with open(dst, "rb") as f:
            h.update(f.read(4 * 1024 * 1024))
        rec = {"id": di, "path": dst, "name": name, "n_pages": len(doc), "sha": h.hexdigest()[:12],
               "pages": [asdict(doc.page_info(i)) for i in range(len(doc))]}
        self.data["docs"].append(rec)
        for i in range(len(doc)):
            self.data["pages"].setdefault(_page_key(di, i), {"role": "none", "family_roles": {}, "align": None,
                                                              "typed_heights": {}, "label_overrides": {}})
        if not self.data["name"]:
            self.data["name"] = os.path.splitext(name)[0]
        self.save()
        self._docs[di] = doc
        return rec

    def doc(self, di: int) -> Document:
        if di not in self._docs:
            self._docs[di] = Document(self.data["docs"][di]["path"])
        return self._docs[di]

    def page_settings(self, key: str) -> dict:
        return self.data["pages"].setdefault(key, {"role": "none", "family_roles": {}, "align": None,
                                                   "typed_heights": {}, "label_overrides": {}})

    def summary(self) -> dict:
        with self._lock:
            d = json.loads(json.dumps(self.data))
        for key in d["pages"]:
            d["pages"][key]["analysis"] = self.analysis_status(key)
        d["engine"] = ENGINE
        d["ocr"] = bool(tesseract_path())
        return d

    # -------------------------------------------------------------- analysis
    def analysis_path(self, key: str) -> str:
        return os.path.join(self.dir, "analysis", key + ".json")

    def analysis_status(self, key: str) -> dict:
        # the job's fields are read together, under the lock they are written under (_run_analysis): a poll never
        # sees "error" with no words (a full-suite run under load caught one between the two writes, 2026-10-01)
        with self._lock:
            j = self.jobs.get(key)
            j = dict(j) if j else None
        if j and j["status"] in ("running", "queued"):
            return {"status": j["status"], "progress": j.get("progress", "")}
        if j and j["status"] == "error":
            return {"status": "error", "error": j.get("error", "")}
        refused = self.refusal(key)
        if refused:
            return {"status": "error", "error": refused}
        if os.path.exists(self.analysis_path(key)):
            return {"status": "done", "fast": bool(j and j.get("fast")), "stale": self.analysis_stale(key)}
        return {"status": "none"}

    # --- a sheet this reader refused as too big (geom.SheetTooBig) is remembered for that sheet, that reader
    # and that detection, so an open does not read it again only to refuse again (critic RISK-4: each open
    # held the server's only read slot for a minute). Re-read (full=1) is the explicit "try again".
    def refusal_path(self, key: str) -> str:
        return os.path.join(self.dir, "analysis", key + ".refused.json")

    def _refusal_sig(self, key: str) -> dict:
        di, _pi = _parse_key(key)
        cr = (self.page_settings(key) or {}).get("core_result")
        try:
            cm = os.path.getmtime(cr) if cr else None
        except OSError:
            cm = None
        return {"reader": READER, "sha": self.data["docs"][di].get("sha", ""), "core": cr or None, "core_mtime": cm}

    def refusal(self, key: str) -> str | None:
        """The remembered refusal's words when it still holds (same reader, PDF and detection), else None."""
        try:
            with open(self.refusal_path(key), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return None
        try:
            sig = self._refusal_sig(key)
        except (IndexError, KeyError, ValueError):
            return None
        return rec.get("error") if all(rec.get(k) == v for k, v in sig.items()) else None

    def remember_refusal(self, key: str, words: str) -> None:
        if self._closed:
            return
        rec = dict(self._refusal_sig(key), error=words, time=time.time())
        try:
            with open(self.refusal_path(key) + ".tmp", "w", encoding="utf-8") as f:
                json.dump(rec, f)
            os.replace(self.refusal_path(key) + ".tmp", self.refusal_path(key))
        except OSError:
            pass

    def forget_refusal(self, key: str) -> None:
        try:
            os.remove(self.refusal_path(key))
        except OSError:
            pass

    def analysis_stale(self, key: str) -> bool:
        """The stored analysis was made by other code (reader or interpretation): it is not shown as
        current; the next request runs again (the reading cache re-reads only if the reader changed)."""
        an = self.load_analysis(key)
        if not an:
            return False
        # the lines' basis is part of the reading too: an analysis made with the product's joins
        # (core_result set) is not current once the operator turned them off, and the other way
        # round (the verifier session cleared core_result and was served the old joins as current)
        core_now = self.core_result(key) is not None
        core_then = an.get("lines_from", "engine") != "engine"
        return (an.get("reader") != READER or an.get("interp") != INTERP or core_now != core_then
                or self.review_stale(key, an))

    def review_stale(self, key: str, an: dict | None = None) -> bool:
        """The reviewer erased or named lines in the product after this sheet was read here: the
        stored analysis still holds the lines as they were."""
        from .core_edits import sig_of
        an = an if an is not None else self.load_analysis(key)
        if not an:
            return False
        return str(an.get("review") or "") != sig_of((self.page_settings(key) or {}).get("core_edits"))

    def load_analysis(self, key: str) -> dict | None:
        p = self.analysis_path(key)
        if not os.path.exists(p):
            return None
        m = os.path.getmtime(p)
        c = self._an_cache.get(key)
        if c and c[0] == m:
            return c[1]
        d = json.load(open(p, encoding="utf-8"))
        self._an_cache[key] = (m, d)
        return d

    # --- the cached reading of a sheet: vectors, printed text, joined lines. Everything
    # the operator can change (roles, heights, drawn lines, label fixes) is applied on
    # top of it in interpret(), so a re-read after an edit takes seconds, not a minute.
    def reading_path(self, key: str) -> str:
        return os.path.join(self.dir, "analysis", key + ".read.pkl")

    def get_reading(self, key: str, sha: str, core: bool = False) -> dict | None:
        b = self._readings.get(key)
        if b is None:
            p = self.reading_path(key)
            if not os.path.exists(p):
                return None
            try:
                with open(p, "rb") as f:
                    b = f.read()
            except OSError:
                return None
        try:
            rec = pickle.loads(b)
        except Exception:
            self._readings.pop(key, None)
            return None
        meta = rec.get("meta") or {}
        if (meta.get("reader") != READER or meta.get("engine") != ENGINE or meta.get("sha") != sha
                or meta.get("ocr") != bool(tesseract_path()) or bool(meta.get("core")) != bool(core)):
            self._readings.pop(key, None)
            return None
        self._readings[key] = b
        return rec["data"]

    def put_reading(self, key: str, sha: str, data: dict, core: bool = False) -> None:
        if self._closed:
            return              # the store was removed under a running read: keep nothing
        rec = {"meta": {"reader": READER, "engine": ENGINE, "sha": sha, "ocr": bool(tesseract_path()),
                        "time": time.time(), "core": bool(core)}, "data": data}
        b = pickle.dumps(rec, protocol=pickle.HIGHEST_PROTOCOL)
        self._readings[key] = b
        p = self.reading_path(key)
        try:
            with open(p + ".tmp", "wb") as f:
                f.write(b)
            os.replace(p + ".tmp", p)
        except OSError:
            pass

    def drop_reading(self, key: str) -> None:
        self._readings.pop(key, None)
        try:
            os.remove(self.reading_path(key))
        except OSError:
            pass

    def start_analysis(self, key: str, force: bool = False, full: bool = False) -> dict:
        """force: interpret again on the cached reading (seconds). full: read the sheet
        again from scratch. A request while a run is going queues one more run."""
        with self._lock:
            j = self.jobs.get(key)
            if j and j["status"] in ("running", "queued") and j.get("stopping"):
                # a read told to stop is still running: nothing new starts beside it (critic 2 RISK-1)
                return {"status": j["status"], "progress": STILL_STOPPING}
            if j and j["status"] in ("running", "queued"):
                if force or full:
                    j["rerun"] = "full" if (full or j.get("rerun") == "full") else "fast"
                return self.analysis_status(key)
            old = self._threads.get(key)
            if old is not None and old.is_alive() and (j or {}).get("stopped"):
                # its slot was freed after the stop grace, but the thread has not ended: never a second read of it
                return {"status": "error", "error": STILL_STOPPING}
            if full:
                self.forget_refusal(key)            # Re-read: the operator's own "try again"
            elif self.refusal(key):
                self.jobs.pop(key, None)            # the remembered refusal answers; nothing is read
                return self.analysis_status(key)
            if os.path.exists(self.analysis_path(key)) and not force and not full and not self.analysis_stale(key):
                return {"status": "done"}
            self.jobs[key] = {"status": "queued", "progress": "queued", "full": bool(full), "rerun": None, "error": ""}
            t = threading.Thread(target=self._run_analysis, args=(key,), daemon=True)
            self._threads[key] = t
        t.start()
        return {"status": "queued"}

    def core_result(self, key: str) -> dict | None:
        """The product's detection for this sheet, when the host handed one over: the page
        setting `core_result` names its JSON (a job's p<N>_result.json). The engine then reads
        its lines from that (core_reading.py) instead of chaining the sheet itself."""
        path = (self.page_settings(key) or {}).get("core_result")
        if not path or not os.path.exists(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                core = json.load(f)
        except (OSError, ValueError):
            return None
        return core if isinstance(core, dict) and core.get("chains") else None

    # --- what the reviewer did in the product (core_edits.py): the lines live in a file beside
    # the analysis, the page settings keep the signature, the counts and the work area
    def core_edits_path(self, key: str) -> str:
        return os.path.join(self.dir, "analysis", key + ".review.json")

    def put_core_edits(self, key: str, edits: dict | None) -> bool:
        """Store the review's decisions for a sheet (core_edits.normalize). True when they changed."""
        from .core_edits import sig_of, summary_of
        with self._lock:
            ps = self.page_settings(key)
            if sig_of(ps.get("core_edits")) == sig_of(edits):
                return False
            path = self.core_edits_path(key)
            if edits:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(edits, f)
                os.replace(path + ".tmp", path)
                ps["core_edits"] = summary_of(edits)
            else:
                try:
                    os.remove(path)
                except OSError:
                    pass
                ps["core_edits"] = None
            return True

    def core_edits(self, key: str) -> dict | None:
        """The review's decisions in force for a sheet; None when there are none, or when the file
        is not the one the settings name (the caller says so: nothing is applied from a stray file)."""
        from .core_edits import sig_of
        want = sig_of((self.page_settings(key) or {}).get("core_edits"))
        if not want:
            return None
        try:
            with open(self.core_edits_path(key), encoding="utf-8") as f:
                ed = json.load(f)
        except (OSError, ValueError):
            return None
        return ed if sig_of(ed) == want else None

    def _run_analysis(self, key: str):
        job = self.jobs[key]
        while True:
            with self._lock:
                job["status"] = "running"
            full = bool(job.get("full"))

            def prog(msg: str):
                # a read waiting for the server's read slot (readslot.py) shows as queued, saying so
                with self._lock:
                    job["status"] = "queued" if msg == WAITING else "running"
                    job["progress"] = msg

            def stopped(words: str):
                # past its time (readslot.Slot): the page is told now; the read stays running - counted by the
                # host and holding its slot - until the thread reaches its next check and ends
                with self._lock:
                    job["stopping"] = True
                    job["stopped"] = True
                    job["progress"] = words
                    job["rerun"] = None

            def abandoned(words: str):
                # the stopped thread did not end within the grace: its slot was freed so other sheets read again;
                # it is no longer counted, but this sheet still refuses a new read until the thread has ended
                with self._lock:
                    job["status"] = "error"
                    job["error"] = words

            try:
                res = analyze_page(self, key, prog, full=full, core=self.core_result(key), on_stop=stopped,
                                   on_abandon=abandoned)
                if self._closed:
                    raise RuntimeError("the project was removed while the sheet was being read")
                with open(self.analysis_path(key) + ".tmp", "w", encoding="utf-8") as f:
                    json.dump(res, f)
                os.replace(self.analysis_path(key) + ".tmp", self.analysis_path(key))
                self._an_cache.pop(key, None)
                self.forget_refusal(key)
                with self._lock:
                    job["fast"] = bool(res.get("timing", {}).get("cached"))
                    job["status"] = "done"
                    job["error"] = ""
            except Exception as e:      # noqa: BLE001
                import traceback
                if isinstance(e, SheetTooBig):
                    self.remember_refusal(key, str(e).split("\n")[0])
                words = f"{e}\n{traceback.format_exc()}"        # built before the status says "error"
                with self._lock:
                    job["status"] = "error"
                    job["error"] = words
                    if isinstance(e, ReadTooLong):
                        job["rerun"] = None           # a read past its time is never started again by itself
            with self._lock:
                job["stopping"] = False
                rerun = None if self._closed else job.get("rerun")
                job["rerun"] = None
                if rerun:
                    job["full"] = rerun == "full"
                    job["status"] = "queued"
                    job["progress"] = "queued again for newer edits"
                    continue
            return

    # --- operator records are anchored to line geometry, not to line ids: a fresh
    # reading may number the pieces differently, and a record must find its line again.
    def anchor_for_chain(self, key: str, cid: int) -> list | None:
        an = self.load_analysis(key)
        if not an:
            return None
        for ch in an.get("chains", []):
            if ch["id"] == int(cid):
                return _anchor_pts(np.asarray(ch["pts"], dtype=float))
        return None

    def anchor_for_contour(self, key: str, contour_id: int) -> tuple[int, list | None] | None:
        """(chain id, anchor points) of the longest drawn piece of a contour."""
        an = self.load_analysis(key)
        if not an:
            return None
        ct = next((c for c in an.get("contours", []) if c["id"] == int(contour_id)), None)
        if not ct or not ct.get("chain_ids"):
            return None
        want = set(int(c) for c in ct["chain_ids"])
        pieces = [ch for ch in an.get("chains", []) if ch["id"] in want]
        if not pieces:
            return int(ct["chain_ids"][0]), None
        ch = max(pieces, key=lambda c: c.get("length", 0))
        return int(ch["id"]), _anchor_pts(np.asarray(ch["pts"], dtype=float))

    def pieces_of(self, key: str, cids) -> list[dict]:
        """Anchor and dense geometry of each piece of a line: enough for a record to find the line
        again after a fresh reading, or to draw it when the reading no longer has it."""
        want = set(int(c) for c in cids)
        an = self.load_analysis(key)
        out = []
        for ch in (an or {}).get("chains", []):
            if ch["id"] in want and len(ch.get("pts") or []) >= 2:
                P = np.asarray(ch["pts"], dtype=float)
                out.append({"chain": int(ch["id"]), "pts": _anchor_pts(P), "geom": _dense_pts(P)})
        return out

    @staticmethod
    def role_of_contour(an: dict | None, ct: dict) -> str | None:
        """The surface a contour is in right now (switched by the operator, or its family's)."""
        if ct.get("switched"):
            return ct["switched"]
        for f in (an or {}).get("families", []):
            if ct.get("style") in (f.get("styles") or [f.get("style")]):
                return f.get("role")
        return None

    def records_on_line(self, an: dict | None, store: dict, cids: set, tol: float = 2.0, role: str | None = None) -> list[str]:
        """The records (typed heights or switches) the operator set on THIS line: a record whose
        chain is one of the line's pieces (the id apply_remap wrote on the last read), or whose OWN
        geometry lies along the line - 70 % of the dense points it carries (pieces[].geom; 80 % of
        the anchor points for a record that has none) within `tol` of the line's pieces. A crossing
        lot line touches two points of a piece, not 70 %; a neighbour 4 pt away touches none at 2 pt.
        A record of one surface is never claimed by a line of the other. Subdivision B: a 559 typed
        over a migrated 560 used to land beside it and the guard split the line; the critic of
        2026-09-26 then showed the first geometry test (60 % of a 12-point anchor within 3 pt) let 35
        other lines claim records - crossing lot lines over 18-pt stubs among them."""
        cids = set(int(c) for c in cids)
        cm = {c["id"]: c for c in (an or {}).get("chains", [])}
        geoms = [LineString(cm[c]["pts"]) for c in cids if c in cm and len(cm[c]["pts"]) >= 2]
        tree = STRtree(geoms) if geoms else None
        out: list[str] = []
        for rid, r in store.items():
            if r.get("chain") is not None and int(r["chain"]) in cids:
                out.append(rid)
                continue
            if tree is None:
                continue
            rr = r.get("role")
            if role in ("existing", "proposed") and rr in ("existing", "proposed") and rr != role:
                continue
            dense = [pc["geom"] for pc in (r.get("pieces") or []) if pc.get("geom") and len(pc["geom"]) >= 2]
            if dense:
                pts, need = [q for g in dense for q in g], 0.7
            else:
                anchors = [pc["pts"] for pc in (r.get("pieces") or []) if pc.get("pts")] or ([r["pts"]] if r.get("pts") else [])
                pts, need = [q for g in anchors for q in g], 0.8
            if len(pts) < 4:
                continue
            near = 0
            for q in pts:
                pt = Point(float(q[0]), float(q[1]))
                if any(geoms[int(gi)].distance(pt) <= tol for gi in tree.query(pt.buffer(tol))):
                    near += 1
            if near >= need * len(pts):
                out.append(rid)
        return out

    def note(self, key: str, text: str) -> None:
        """A line in the page's ledger: what a record did to another record. The next update of
        the sheet shows entries newer than the last read as notes."""
        ps = self.page_settings(key)
        led = ps.setdefault("ledger", [])
        led.append({"t": time.time(), "text": text})
        del led[:-200]

    def set_typed(self, key: str, contour_id: int, z) -> str | None:
        """The operator types a height on a detected line (None clears it). Every earlier typed
        height on the same line - found by geometry, not by id - is replaced, never left beside."""
        with self._lock:
            self.migrate_records(key)
            ps = self.page_settings(key)
            typed = ps.setdefault("typed", {})
            an = self.load_analysis(key)
            ct = next((c for c in (an or {}).get("contours", []) if c["id"] == int(contour_id)), None)
            if not ct:
                raise ValueError("that line is not in the current reading of the sheet")
            cids = set(int(c) for c in ct.get("chain_ids") or [])
            hits = self.records_on_line(an, typed, cids, role=self.role_of_contour(an, ct))
            if z is None:
                for tid in hits:
                    typed.pop(tid, None)
                if hits:
                    self.note(key, f"cleared the typed height on line {ct['id']}")
                self.save()
                return None
            anc = self.anchor_for_contour(key, contour_id)
            cid, pts = anc if anc else (int(ct["chain_ids"][0]), None)
            olds = sorted({float(typed[t]["z"]) for t in hits if typed[t].get("z") is not None and abs(float(typed[t]["z"]) - float(z)) > 0.01})
            tid = hits[0] if hits else "t" + uuid.uuid4().hex[:8]
            for other in hits[1:]:
                typed.pop(other, None)
            # the line's surface and every piece's geometry: enough to draw the line again from the
            # record alone when a later reading of the sheet no longer has it
            typed[tid] = {"z": float(z), "chain": cid, "pts": pts, "role": self.role_of_contour(an, ct),
                          "pieces": self.pieces_of(key, cids), "set": time.time()}
            if olds:
                self.note(key, f"replaced your earlier {', '.join(f'{o:g}' for o in olds)} ft on line {ct['id']} with {float(z):g} ft")
            self.save()
            return tid

    def set_line_role(self, key: str, role, contour_id=None, chain_id=None) -> str | None:
        """The operator switches one line (a contour, or a single piece) to the other surface
        (None clears the switch). Anchored to the line's geometry like every other record."""
        with self._lock:
            self.migrate_records(key)
            ps = self.page_settings(key)
            roles = ps.setdefault("roles", {})
            an = self.load_analysis(key)
            if contour_id is not None:
                ct = next((c for c in (an or {}).get("contours", []) if c["id"] == int(contour_id)), None)
                if not ct or not ct.get("chain_ids"):
                    raise ValueError("that line is not in the current reading of the sheet")
                cids = set(int(c) for c in ct["chain_ids"])
                anc = self.anchor_for_contour(key, contour_id)
                cid, pts = anc if anc else (int(ct["chain_ids"][0]), None)
            else:
                cid = int(chain_id)
                cids = {cid}
                pts = self.anchor_for_chain(key, cid)
                if pts is None:
                    raise ValueError("that piece is not in the current reading of the sheet")
            hits = self.records_on_line(an, roles, cids, role=(self.role_of_contour(an, ct) if contour_id is not None else None))
            if role is None:
                for rid in hits:
                    roles.pop(rid, None)
                if hits:
                    self.note(key, "reset a line to the surface of its line style")
                self.save()
                return None
            if role not in SWITCH_STYLES:
                raise ValueError("a line can be switched to existing or proposed")
            olds = sorted({roles[r].get("role") for r in hits if roles[r].get("role") != role})
            rid = hits[0] if hits else "r" + uuid.uuid4().hex[:8]
            for other in hits[1:]:
                roles.pop(other, None)
            # every piece of the line, each with its own anchor: a switch is for the whole line
            # the operator clicked, not for the one piece under the cursor (subdivision A: a
            # four-piece 574 contour lost three pieces and its height to a one-piece switch)
            pieces = self.pieces_of(key, cids)
            roles[rid] = {"role": role, "chain": cid, "pts": pts, "pieces": pieces, "set": time.time()}
            if olds:
                self.note(key, f"switched a line to {role} that you had switched to {', '.join(str(o) for o in olds)} before")
            self.save()
            return rid

    def migrate_records(self, key: str) -> None:
        """Older records keyed by a line id alone get geometry anchors from the current reading."""
        with self._lock:
            ps = self.page_settings(key)
            changed = False
            th = ps.get("typed_heights")
            if th is not None:
                typed = ps.setdefault("typed", {})
                for cid_s, z in list(th.items()):
                    if z is None:
                        continue
                    anc = self.anchor_for_contour(key, int(cid_s))
                    if anc is None:
                        print(f"[{self.id}/{key}] typed height {z} on contour {cid_s} could not be anchored; dropped",
                              file=sys.stderr)
                        continue
                    cid, pts = anc
                    if any(int(t.get("chain", -1)) == cid for t in typed.values()):
                        continue
                    typed["t" + uuid.uuid4().hex[:8]] = {"z": float(z), "chain": cid, "pts": pts, "set": time.time(),
                                                         "migrated_from": int(cid_s)}
                ps.pop("typed_heights", None)
                changed = True
            ed = ps.get("edits")
            if ed:
                anchors = ed.setdefault("anchors", {})
                for cid in ed.get("deleted") or []:
                    if str(cid) not in anchors:
                        anchors[str(cid)] = self.anchor_for_chain(key, int(cid))
                        changed = True
                for lid, ln in (ed.get("lines") or {}).items():
                    if ln and ln.get("chain") is not None and "chain_pts" not in ln:
                        ln["chain_pts"] = self.anchor_for_chain(key, int(ln["chain"]))
                        changed = True
            for lid, ov in (ps.get("label_overrides") or {}).items():
                if ov and ov.get("attach") is not None and "attach_pts" not in ov:
                    ov["attach_pts"] = self.anchor_for_chain(key, int(ov["attach"]))
                    changed = True
            fr = ps.get("family_roles")
            if fr and self.load_analysis(key) is None:
                fr = None                 # nothing to name the styles by yet: the picks wait for the next reading
            if fr:
                sr = ps.setdefault("style_roles", {})
                for sid, role in list(fr.items()):
                    sig = self.style_sig_for(key, sid)
                    if sig is None:
                        print(f"[{self.id}/{key}] role {role} on style {sid} could not be named; dropped", file=sys.stderr)
                        continue
                    sr[sig] = role
                ps["family_roles"] = {}
                changed = True
            # Records made before 2026-09-26 name ONE piece (a 12-point anchor of the longest piece,
            # median span 70 pt on subdivision B) and cannot say "the whole 440-pt line". The stored
            # analysis is the reading the record's chain id refers to (apply_remap rewrites the id on
            # every read), and it still holds the line as the operator saw it: widen each such record
            # to every piece of that line, with dense geometry, before the next read replaces the file.
            an = self.load_analysis(key)
            if an:
                ct_by_chain = {cid: c for c in an.get("contours", []) for cid in (c.get("chain_ids") or [])}
                cm = {c["id"]: c for c in an.get("chains", [])}
                edited = {int(ln["chain"]) for ln in ((ps.get("edits") or {}).get("lines") or {}).values()
                          if ln and ln.get("chain") is not None}
                n_wide = n_fail = n_edit = 0
                for name in ("typed", "roles"):
                    for rid, rec in (ps.get(name) or {}).items():
                        if not rec or rec.get("pieces") or rec.get("backfill") is not None or rec.get("chain") is None:
                            continue
                        ct = ct_by_chain.get(int(rec["chain"]))
                        ch = cm.get(int(rec["chain"]))
                        pts = rec.get("pts")
                        if int(rec["chain"]) in edited:
                            rec["backfill"] = "none: the piece is replaced by your edited line, which carries its own height"
                            n_edit += 1
                            changed = True
                            continue
                        if ch is None:
                            rec["backfill"] = "none: the stored reading has no piece with this record's id"
                            n_fail += 1
                            changed = True
                            continue
                        if ct is None:
                            rec["backfill"] = "none: the stored reading holds the piece but it is in no contour line"
                            n_fail += 1
                            changed = True
                            continue
                        if not pts or len(pts) < 2 or len(ch.get("pts") or []) < 2:
                            rec["backfill"] = "none: the record carries no anchor to check the piece against"
                            n_fail += 1
                            changed = True
                            continue
                        if not _covers(pts, ch["pts"]):
                            rec["backfill"] = "none: the stored reading's piece is not where this record was set"
                            n_fail += 1
                            changed = True
                            continue
                        rec["pieces"] = self.pieces_of(key, ct.get("chain_ids") or [])
                        if name == "typed" and not rec.get("role"):
                            rec["role"] = self.role_of_contour(an, ct)
                        rec["backfill"] = {"line": int(ct["id"]), "pieces": len(rec["pieces"]),
                                           "length_pt": round(float(ct.get("length") or 0)), "at": time.time()}
                        n_wide += 1
                        changed = True
                if n_wide:
                    self.note(key, f"{n_wide} of your earlier records were widened from one piece to the whole line they were set on")
                if n_fail:
                    self.note(key, f"{n_fail} of your earlier records could not be widened to their line (each record says why); "
                                   f"they still apply to the piece they name")
                if n_edit:
                    self.note(key, f"{n_edit} of your earlier records sit on pieces you replaced with edited lines; the edited lines carry their heights")
            if changed:
                self.save()

    def style_sig_for(self, key: str, style_id) -> str | None:
        """The stable signature of a style id in the current reading of the sheet."""
        an = self.load_analysis(key)
        if not an:
            return None
        for st in an.get("styles", []):
            if st["id"] == int(style_id):
                if st.get("sig"):
                    return st["sig"]
                from .chains import style_sig
                return style_sig(st["color"], st["width"], st.get("dashed", False), st.get("layer", ""))
        for f in an.get("families", []):
            if f["style"] == int(style_id) and f.get("sig"):
                return f["sig"]
        return None

    def set_style_role(self, key: str, style_id, role) -> None:
        """The operator names a line style's surface (role None clears the pick)."""
        with self._lock:
            self.migrate_records(key)
            sig = self.style_sig_for(key, style_id)
            if sig is None:
                raise ValueError("that line style is not in the current reading of the sheet")
            sr = self.page_settings(key).setdefault("style_roles", {})
            if role is None:
                sr.pop(sig, None)
            else:
                sr[sig] = role
            self.save()

    def snapshot_page_settings(self, key: str) -> dict:
        with self._lock:
            self.migrate_records(key)
            return copy.deepcopy(self.page_settings(key))

    def apply_remap(self, key: str, typed_new: dict, edits_new: dict, attach_new: dict,
                    roles_new: dict | None = None, ledger_seen: float | None = None) -> None:
        """After a reading, records whose line ids moved are rewritten (records that still exist only)."""
        with self._lock:
            ps = self.page_settings(key)
            changed = False
            seen = float(ps.get("ledger_seen") or 0)
            if ledger_seen is not None and ledger_seen > seen:
                ps["ledger_seen"] = ledger_seen      # exactly what this read's notes showed, nothing written since
                changed = True
            for name, recs in (("typed", typed_new), ("roles", roles_new or {})):
                store = ps.get(name) or {}
                for rid, rec in recs.items():
                    cur = store.get(rid)
                    if cur is None:
                        continue
                    if cur.get("chain") != rec.get("chain") or bool(cur.get("lost")) != bool(rec.get("lost")):
                        cur["chain"] = rec.get("chain")
                        cur["lost"] = bool(rec.get("lost"))
                        changed = True
                    # kept (drawn from the record), carried (by an edited line), superseded (by an
                    # edit or a removal): what the UI says about the record
                    for flag in ("kept", "carried", "superseded", "fate"):
                        if rec.get(flag) and cur.get(flag) != rec.get(flag):
                            cur[flag] = rec[flag]
                            changed = True
                        elif not rec.get(flag) and flag in cur:
                            cur.pop(flag, None)
                            changed = True
            ed = ps.get("edits")
            if ed:
                for lid, role in (edits_new.get("line_roles") or {}).items():
                    ln = (ed.get("lines") or {}).get(lid)
                    if ln and ln.get("role") != role:
                        ln["role"] = role
                        changed = True
                if sorted(int(x) for x in (ed.get("deleted") or [])) != sorted(edits_new["deleted"]):
                    ed["deleted"] = list(edits_new["deleted"])
                    ed["anchors"] = dict(edits_new["anchors"])
                    changed = True
                for lid, cid in edits_new["line_chains"].items():
                    ln = (ed.get("lines") or {}).get(lid)
                    if ln and ln.get("chain") != cid:
                        ln["chain"] = cid
                        changed = True
            lo = ps.get("label_overrides") or {}
            for lid, cid in attach_new.items():
                ov = lo.get(lid)
                if ov and ov.get("attach") != cid:
                    ov["attach"] = cid
                    changed = True
            if changed:
                self.save()

    # ---------------------------------------------------------------- compute
    def compute(self) -> dict:
        return compute_cutfill(self)


# ===========================================================================
def analyze_page(project: Project, key: str, prog=lambda m: None, full: bool = False, core: dict | None = None,
                 on_stop=lambda words: None, on_abandon=lambda words: None) -> dict:
    """Read the sheet (cached) and interpret it with the operator's records applied.
    `core`: a CORE detector page result (see core_reading.py); its lines replace this reader's
    chaining and the reading is cached apart from the engine's own (`reader` stamp)."""
    di, pi = _parse_key(key)
    doc = project.doc(di)
    info = doc.page_info(pi)
    sha = project.data["docs"][di].get("sha", "")
    # ONE slot for the whole analysis (CUTFILL_MAX_READS at once per server, across its processes: D41): loading the
    # cached reading, the fresh read, the interpretation, the records' fates and the changes card. The read alone was
    # held before, and on Res-A civil C3.0 the interpretation that follows it ran 151 s beside the next sheet's read in
    # the live proof (tree 2,059 -> 2,414 MB; out/verify/2026-10-01/live-memory/VERIFY.md). A re-interpretation on a
    # cached reading takes the slot too: on that sheet it is the heavy half (heights 35-120 s).
    # Past CUTFILL_READ_MAX_SECONDS on_stop is told and the run stops at its next check, keeping its slot until it has
    # ended (at most CUTFILL_READ_STOP_GRACE_SECONDS more: then on_abandon, and the slot is freed).
    with read_slot(prog, alive=lambda: not project._closed, on_expire=on_stop, on_abandon=on_abandon) as slot:
        told = prog

        def prog(msg: str):                    # every progress message of the run is a stop check
            slot.check()
            told(msg)
        t0 = time.time()
        rd = None if full else project.get_reading(key, sha, core=core is not None)
        cached = rd is not None
        if rd is None:
            if full:
                project.drop_reading(key)
            rd = read_sheet(doc, pi, prog, core=core)
            slot.check()                       # a run told to stop stores nothing, not even its reading
            project.put_reading(key, sha, rd, core=core is not None)
        ps = project.snapshot_page_settings(key)
        prev = project.load_analysis(key)          # the previous reading, before this one replaces it
        res = interpret(project, key, doc, info, rd, ps, prog, prev=prev)
        res["timing"]["cached"] = cached
        res["timing"]["total"] = round(time.time() - t0, 1)
        res["reader"] = READER
        res["interp"] = INTERP
        res["records"] = _record_fates(project.page_settings(key))
        res["changes"] = _changes_since(prev, res, project, key)
    return res


def _record_fates(ps: dict) -> dict:
    """What became of the operator's records on this read (the flags apply_remap wrote)."""
    out = {}
    for name in ("typed", "roles"):
        c = {"applied": 0, "short": 0, "ignored": 0, "superseded": 0, "carried": 0, "kept": 0, "lost": 0}
        for rec in (ps.get(name) or {}).values():
            if not rec or (name == "typed" and rec.get("z") is None):
                continue
            if rec.get("kept"):
                c["kept"] += 1
            elif rec.get("superseded"):
                c["superseded"] += 1
            elif rec.get("carried"):
                c["carried"] += 1
            elif rec.get("lost"):
                c["lost"] += 1
            elif rec.get("fate") == "short":
                c["short"] += 1
            elif rec.get("fate") == "ignored style":
                c["ignored"] += 1
            else:
                c["applied"] += 1
        out[name] = c
    ed = ps.get("edits") or {}
    out["lines"] = sum(1 for ln in (ed.get("lines") or {}).values() if ln)
    out["deleted"] = len(ed.get("deleted") or [])
    out["labels"] = sum(1 for ov in (ps.get("label_overrides") or {}).values() if ov)
    return out


def _changes_since(prev: dict | None, res: dict, project: Project, key: str) -> dict:
    """The real difference between the previous reading of this sheet and this one: nothing is
    carried forward or pinned, the read reports what it read and this says what moved (Koby's
    rule 3: a better reader must not quietly move a corrected project)."""
    from .chains import style_sig

    def sig_of(f, sty):
        if isinstance(f.get("style"), int) and f["style"] < 0:
            return f"{f.get('role_reason') or 'your lines'} ({f.get('role')})"     # switched / drawn lines: not a pen
        if f.get("sig"):
            return f["sig"]
        s = sty.get(f["style"]) or {}
        if s.get("sig"):
            return s["sig"]
        src = s if s.get("color") is not None else (f if f.get("color") is not None else None)
        if src is not None:                       # analyses written before styles carried a sig
            try:
                return style_sig(tuple(src.get("color") or (0, 0, 0)), float(src.get("width") or 0), bool(src.get("dashed")), src.get("layer") or "")
            except (TypeError, ValueError):
                pass
        return str(f["style"])

    def pen_of(f, sty):
        """The pen in the Families card's words (check 6A: this card printed the raw signature,
        "0.00,0.00,0.00|w0.4|s|"); None for the operator's own lines (their name is already words) or
        a record that says nothing of its pen."""
        if isinstance(f.get("style"), int) and f["style"] < 0:
            return None
        for src in (f, sty.get(f["style"]) or {}):
            if src.get("width") is not None:
                return _pen_words(src)
        return None

    def fam_stats(an, pens=None):
        out = {}
        sty = {s["id"]: s for s in an.get("styles", [])}
        for f in an.get("families", []):
            sig = sig_of(f, sty)
            if pens is not None and sig not in pens:
                pens[sig] = pen_of(f, sty)
            cts = [c for c in an.get("contours", []) if c["style"] in (f.get("styles") or [f["style"]])]
            out[sig] = {"role": f["role"], "contours": len(cts), "with_z": sum(1 for c in cts if c.get("z") is not None),
                        "length_with_z": round(sum(c["length"] for c in cts if c.get("z") is not None)),
                        "length": round(sum(c["length"] for c in cts))}
        return out

    def how_counts(an):
        """Lines with a height by source: how many, and how much length - joined lines make the
        count fall while the length with a height rises, and the length is the honest measure."""
        c: dict = {}
        for ct in an.get("contours", []):
            if ct.get("z") is None:
                continue
            k = ct.get("how") or "none"
            e = c.setdefault(k, {"n": 0, "pt": 0})
            e["n"] += 1
            e["pt"] += round(float(ct.get("length") or 0))
        return c

    def label_counts(an):
        labs = an.get("labels", [])
        return {"attached": sum(1 for l in labs if l.get("kind") == "contour" and l.get("chain_ids")),
                "near": sum(1 for l in labs if l.get("kind") == "contour" and not l.get("chain_ids") and l.get("near") is not None),
                "spots": len(an.get("spots", []))}
    stamp = lambda an: (f"{an.get('engine')} reader {an.get('reader') or 'unstamped'} interpretation {an.get('interp') or 'unstamped'}") if an else None
    ch: dict = {"first_read": prev is None, "fresh_read": not res["timing"].get("cached"),
                "reader_changed": bool(prev) and prev.get("reader") != res.get("reader"),
                "interp_changed": bool(prev) and prev.get("interp") != res.get("interp"),
                "engine": [stamp(prev), stamp(res)],
                # before 2026-09-26 readings carried a hand-bumped number, so a reading made by older
                # code could be shown as current; the first stamped read of a project says so once
                "unstamped_before": bool(prev) and not prev.get("reader")}
    # the lines' basis (the product's joins on or off) is a reason to re-read, and the operator
    # must see it: a fresh read with a changed number and no reason is a trust problem
    lf_prev, lf_now = (prev or {}).get("lines_from", "engine"), res.get("lines_from", "engine")
    ch["basis"] = [lf_prev if prev else None, lf_now]
    if prev and lf_prev != lf_now:
        ch["basis_changed"] = True
        res.setdefault("notes", []).append(
            "re-read because the lines' basis changed: "
            + ("the product's line joins are on now" if lf_now != "engine" else "the product's line joins are off now")
            + " (the numbers below are on that basis)")
    ch["records"] = res.get("records")
    ch["records_before"] = prev.get("records") if prev else None
    ch["heights"] = {"before": how_counts(prev) if prev else None, "after": how_counts(res)}
    ch["labels"] = {"before": label_counts(prev) if prev else None, "after": label_counts(res)}
    pens: dict = {}                               # sig -> the pen in words (this reading's record first)
    fa = fam_stats(res, pens)
    fb = fam_stats(prev, pens) if prev else {}
    rows = []
    for sig in sorted(set(fa) | set(fb), key=lambda s: -(fa.get(s, fb.get(s, {})).get("length", 0))):
        a, b = fa.get(sig), fb.get(sig)
        if (a or {}).get("role") == "ignore" and (b or {}).get("role", "ignore") == "ignore":
            continue
        # the row says its pen in words; the signature stays for support (the card's tooltip)
        rows.append({"sig": sig, "pen": pens.get(sig), "before": b, "after": a,
                     "role_changed": bool(a and b and a["role"] != b["role"]),
                     "new": b is None and a is not None, "gone": a is None and b is not None})
    ch["families"] = rows
    # line by line, when the reading is the same (pieces keep their ids, so a line is its set of
    # pieces): which lines gained, lost or changed a height since the last read - capped, worst first
    if prev and res["timing"].get("cached"):
        def by_pieces(an):
            return {frozenset(int(c) for c in ct["chain_ids"]): ct for ct in an.get("contours", []) if ct.get("chain_ids") and not ct.get("user")}
        pb, pa = by_pieces(prev), by_pieces(res)
        gained, lost, changed = [], [], []
        for key_, a in pa.items():
            b = pb.get(key_)
            if b is None:
                continue
            za, zb = a.get("z"), b.get("z")
            row = {"id": a["id"], "length": round(a.get("length") or 0), "before": zb, "after": za,
                   "how_before": b.get("how"), "how_after": a.get("how"), "switched": a.get("switched"), "typed": a.get("how") == "typed"}
            if zb is None and za is not None:
                gained.append(row)
            elif zb is not None and za is None:
                lost.append(row)
            elif zb is not None and za is not None and abs(za - zb) > 0.01:
                changed.append(row)
        for lst in (gained, lost, changed):
            lst.sort(key=lambda r: -r["length"])
        ch["lines"] = {"gained": gained[:10], "lost": lost[:10], "changed": changed[:10],
                       "n_gained": len(gained), "n_lost": len(lost), "n_changed": len(changed),
                       "unmatched": sum(1 for k in pa if k not in pb)}
    # the previous compute, so the next Result can be read against it
    try:
        rp = os.path.join(os.path.dirname(os.path.dirname(project.analysis_path(key))), "result.json")
        if os.path.exists(rp):
            with open(rp, encoding="utf-8") as f:
                r = json.load(f)
            t = r.get("totals") or {}
            ch["previous_totals"] = {"cut_cy": t.get("cut_cy"), "fill_cy": t.get("fill_cy"), "area_sf": t.get("area_sf"),
                                     "computed_at": r.get("computed_at")}
    except (OSError, ValueError):
        pass
    return ch


_TOPO_LAYER = re.compile(r"TOPO|CONT|SURFACE|CNTR|GRADE", re.I)


def core_mode_of(rd: dict) -> str:
    return "replace" if rd.get("core") and not rd.get("core_links") and rd.get("core_mode") == "replace" else "link"


def _topo_layer(layer: str) -> bool:
    """Does the CAD layer name say topography (CE-TOPO-MINR, CP-TOPO-MAJR-LOTS, -SURFACE,
    -EXISTING CNTR ...)? Strokes on such a layer are linework, whatever their size."""
    return bool(layer) and _TOPO_LAYER.search(layer.split("|")[-1]) is not None


def read_sheet(doc: Document, pi: int, prog=lambda m: None, core: dict | None = None) -> dict:
    """Everything that does not depend on the operator: vectors, printed text (text layer,
    exploded glyphs, OCR), joined line pieces and their gaps."""
    t0 = time.time()
    timing = {}

    if tesseract_path():
        # the whole-sheet OCR renders the page at OCR_DPI, 9.02 B a pixel at its RGB step: a page past OCR_MAX_PX
        # would take more than 2.0 GB there, so it is refused before anything is read (Res-A civil C3.0,
        # 36 x 24 in, is 34.6 million pixels at 200 dpi)
        r = doc.doc[pi].rect
        px = (r.width * OCR_DPI / 72.0) * (r.height * OCR_DPI / 72.0)
        if px > OCR_MAX_PX:
            raise SheetTooBig(f"reading the printed text: the page is {px / 1e6:,.0f} million pixels at {OCR_DPI} dpi")

    prog("reading vectors")
    strokes = doc.strokes(pi)
    timing["vectors"] = round(time.time() - t0, 1)

    # --- exploded text: glyph clusters (found BEFORE chaining so text never joins a line)
    prog("finding printed text")
    t1 = time.time()
    # A stroke on a CAD layer the drafter named as topography is never a glyph: one civil firm draws contours as
    # strips of thin closed triangles 11-37 pt round, and the glyph clusterer ate 16 of the 43
    # CE-TOPO-MINR triangles in one subdivision A block (104 of 363 CP-TOPO-MINR-ROAD strokes), so the
    # survey contours vanished where the triangles were small. The layer name is the sheet's own word.
    topo_idx = {st.idx for st in strokes if _topo_layer(st.layer)}
    clusters = merge_clusters_on_baseline(find_text_clusters(strokes, exclude_idx=topo_idx))
    glyph_ids = glyph_stroke_ids(clusters)
    timing["glyphs"] = round(time.time() - t1, 1)

    prog("joining lines")
    t1 = time.time()
    core_mode = (core or {}).get("_mode", "link")
    core_chains = {}
    if core is not None and core_mode == "replace":
        # the product's detection AS this engine's lines (core_reading.py) - measured 2026-09-25
        # and kept only as an experiment: CORE has no strip rebuild and drops small triangle
        # strokes, so a third of subdivision A's existing linework went missing
        from .core_reading import chains_from_core
        chains, styles = chains_from_core(core)
        links = {}
    else:
        chains, styles = build_chains(strokes, exclude_idx=glyph_ids)
        links = {}
        core_chains = {}
        if core is not None:
            # the product's detection as JOINS: pieces that lie on one CORE chain are one line
            from .core_reading import core_links
            links = core_links(chains, core)
            wanted = set(links.values())
            core_chains = {int(c["chain_id"]): [[float(x), float(y)] for x, y in c["points"]]
                           for c in (core.get("chains") or []) if int(c.get("chain_id", -1)) in wanted}
    gaps = find_gaps(chains)
    timing["chains"] = round(time.time() - t1, 1)

    # --- text layer -----------------------------------------------------------
    labels: list[Label] = []
    texts = []
    for tl in doc.text_lines(pi):
        # page (y-down) convention like the glyph clusters: measured 2026-09-25 on 108 rotated
        # text-layer labels sitting on lines (subdivision B 71 of 77, A 27 of 31 wanted this sign) while the
        # 55 glyph labels of substation / hotel site kept theirs: the old -dir[1] mirrored every text-layer angle
        ang = math.degrees(math.atan2(tl.dir[1], tl.dir[0]))
        gray = 0.299 * tl.color[0] + 0.587 * tl.color[1] + 0.114 * tl.color[2]
        labels.append(make_label(len(labels), pi, tl.text, tl.bbox, ang, "text", 100.0, gray,
                                 len_along=tl.len_along or None, height=tl.height or None))
        texts.append(tl.text)
    n_text_numbers = sum(1 for l in labels if l.value is not None)
    text_boxes = [l.bbox for l in labels]

    ocr_used = False
    glyph_boxes = []
    if tesseract_path():
        # --- OCR of glyph clusters (rotated crops) ----------------------------
        prog(f"OCR: reading {len(clusters)} printed words")
        t1 = time.time()
        # a word the text layer already holds is not read a second time from its strokes
        cl_sel = [c for c in clusters if not any(_same_word(c.bbox, tb) for tb in text_boxes)]
        ocr_text_clusters(None, 300, cl_sel, strokes=strokes)
        text_numbers = [l for l in labels if l.value is not None]
        for c in cl_sel:
            if not c.text or c.conf < 30:
                continue
            ang = math.degrees(c.angle)
            if c.flipped:
                ang += 180
            lab = make_label(len(labels), pi, c.text, c.bbox, ang, "glyph", c.conf, c.gray,
                             len_along=c.length, height=c.height)
            lab.turned = getattr(c, "alt_text", "") or None        # what it reads from the other side (labels.upright_heights)
            # ... and a number read from strokes that the text layer prints at the same place is
            # that number, once: the strokes' box of a framed height holds the frame, three times
            # the size of the text's own box, and the boxes alone did not say "one word"
            # (subdivision C sheet 3.1: an oval 619 came twice, the second one landed on the
            # easement line beside its contour, and the lot-line pen became the proposed surface)
            if lab.value is not None and any(t.value == lab.value and _inside_box(t.center, (c.bbox[0] - 2, c.bbox[1] - 2, c.bbox[2] + 2, c.bbox[3] + 2))
                                             for t in text_numbers):
                continue
            labels.append(lab)
            texts.append(c.text)
        timing["ocr_glyphs"] = round(time.time() - t1, 1)
        glyph_boxes = [c.bbox for c in cl_sel if c.text]

        # --- whole-page OCR for anything else (raster text, filled fonts) ------
        prog("OCR: reading the rest of the sheet")
        t1 = time.time()
        words = ocr_page_tiles(doc, pi, dpi=OCR_DPI)
        rgb = doc.render_rgb_array(pi, OCR_DPI)
        f = OCR_DPI / 72.0
        for ws in group_lines(words):
            text = " ".join(w.text for w in ws)
            x0 = min(w.bbox[0] for w in ws); y0 = min(w.bbox[1] for w in ws)
            x1 = max(w.bbox[2] for w in ws); y1 = max(w.bbox[3] for w in ws)
            conf = sum(w.conf for w in ws) / len(ws)
            if conf < 55:
                continue
            bb = (x0, y0, x1, y1)
            if any(_overlap(bb, tb) > 0.3 for tb in text_boxes) or any(_overlap(bb, gb) > 0.3 for gb in glyph_boxes):
                continue
            g = ink_gray(rgb, (x0 * f, y0 * f, x1 * f, y1 * f))
            labels.append(make_label(len(labels), pi, text, bb, 0.0, "ocr", conf, g))
            texts.append(text)
        del rgb
        ocr_used = True
        timing["ocr_tiles"] = round(time.time() - t1, 1)
    # the pen framing each printed number (an oval round a proposed height); see find_label_frames
    n_framed = find_label_frames(labels, strokes, glyph_ids)
    timing["frames"] = n_framed
    timing["read"] = round(time.time() - t0, 1)
    return {"strokes": strokes, "glyph_ids": glyph_ids, "n_clusters": len(clusters), "chains": chains,
            "styles": styles, "gaps": gaps, "labels": labels, "texts": texts, "ocr_used": ocr_used, "core": core is not None,
            "core_links": links, "core_mode": core_mode, "core_chains": core_chains,
            "n_text_numbers": n_text_numbers, "timing": timing}


def _anchor_pts(pts, n: int = 12) -> list:
    """A few points along a line: enough to find the same line again after a fresh reading."""
    P = np.asarray(pts, dtype=float)
    if len(P) < 2:
        return [[round(float(x), 1), round(float(y), 1)] for x, y in P]
    L = poly_length(P)
    R = resample(P, max(L / (n - 1), 3.0))
    if len(R) > n:
        R = R[np.linspace(0, len(R) - 1, n).round().astype(int)]
    return [[round(float(x), 1), round(float(y), 1)] for x, y in R]


def _dense_pts(pts, step: float = 6.0, cap: int = 400) -> list:
    """The line itself, every 6 pt (at most `cap` points): a record can draw its line again from
    this when a later reading of the sheet no longer has it."""
    P = np.asarray(pts, dtype=float)
    if len(P) < 2:
        return [[round(float(x), 1), round(float(y), 1)] for x, y in P]
    R = resample(P, max(step, poly_length(P) / (cap - 1)))
    return [[round(float(x), 1), round(float(y), 1)] for x, y in R]


def _covers(a, b, tol: float = 3.0) -> bool:
    """60 % of polyline a's points lie within `tol` of polyline b."""
    A = np.asarray(a, dtype=float)
    B = np.asarray(b, dtype=float)
    if len(A) < 2 or len(B) < 2:
        return False
    g = LineString(B)
    d = np.array([g.distance(Point(float(p[0]), float(p[1]))) for p in A])
    return bool((d <= tol).mean() >= 0.6)


class _Remapper:
    """Find an operator record's line in a (possibly re-numbered) set of pieces."""
    TOL = 3.0

    def __init__(self, chains: list[Chain]):
        self.cmap = {c.id: c for c in chains}
        self.ids = [c.id for c in chains if len(c.pts) >= 2]
        self.geoms = [LineString(self.cmap[i].pts) for i in self.ids]
        self.tree = STRtree(self.geoms) if self.geoms else None
        self.moved = 0
        self.lost = 0

    def _score(self, geom, A: np.ndarray) -> tuple[float, float]:
        d = np.array([geom.distance(Point(p[0], p[1])) for p in A])
        return float((d <= self.TOL).mean()), float(d.mean())

    def match(self, cid, anchor) -> int | None:
        try:
            cid = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid = None
        if not anchor or len(anchor) < 2:
            if cid in self.cmap:
                return cid
            self.lost += 1
            return None
        A = np.asarray(anchor, dtype=float)
        ch = self.cmap.get(cid)
        if ch is not None and len(ch.pts) >= 2:
            frac, _ = self._score(LineString(ch.pts), A)
            if frac >= 0.8:
                return cid
        if self.tree is None:
            self.lost += 1
            return None
        box = shapely.box(A[:, 0].min() - 6, A[:, 1].min() - 6, A[:, 0].max() + 6, A[:, 1].max() + 6)
        best = None
        for gi in self.tree.query(box, predicate="intersects"):
            gi = int(gi)
            frac, mean = self._score(self.geoms[gi], A)
            if frac >= 0.6 and (best is None or (frac, -mean) > (best[0], -best[1])):
                best = (frac, mean, self.ids[gi])
        if best is None:
            self.lost += 1
            return None
        if best[2] != cid:
            self.moved += 1
        return best[2]

    def match_all(self, cid, anchor) -> list[int]:
        """Every current piece the record's line lies on: the one piece it names while that piece
        still covers the anchor, else the pieces a fresh reading split the line into (each holding
        two or more of the anchor's points) as long as together they cover 60 % of it. A record
        anchored to one piece used to be "lost" the moment the reader drew that line in two."""
        one = self.match(cid, anchor)
        if one is not None or not anchor or len(anchor) < 2 or self.tree is None:
            return [one] if one is not None else []
        self.lost -= 1                    # match() counted it lost; the split test may still find it
        A = np.asarray(anchor, dtype=float)
        box = shapely.box(A[:, 0].min() - 6, A[:, 1].min() - 6, A[:, 0].max() + 6, A[:, 1].max() + 6)
        cand = [int(gi) for gi in self.tree.query(box, predicate="intersects")]
        owner: list[int | None] = [None] * len(A)
        best_d = [self.TOL] * len(A)
        for gi in cand:
            g = self.geoms[gi]
            for i, p in enumerate(A):
                d = g.distance(Point(float(p[0]), float(p[1])))
                if d < best_d[i]:
                    best_d[i] = d
                    owner[i] = gi
        counts = Counter(o for o in owner if o is not None)
        keep = [gi for gi, n in counts.items() if n >= 2 or len(A) < 6]
        if sum(counts[gi] for gi in keep) < 0.6 * len(A):
            self.lost += 1
            return []
        self.moved += 1
        keep.sort(key=lambda gi: -counts[gi])
        return [self.ids[gi] for gi in keep]


def _mark_near_line_numbers(labels: list[Label], contours: list[Contour], families: list[Family],
                            chains_all: list[Chain], min_single: float) -> int:
    """A plain integer that touches no line but sits right beside a contour line, with a value
    in that surface's range, is an unattached contour label - not a spot grade. The UI offers
    to attach it to the line."""
    role_of = {}
    for f in families:
        for st in (f.styles or [f.style]):
            role_of[st] = f.role
    cmap = {c.id: c for c in chains_all}
    ids, geoms, owners = [], [], []
    rng: dict[str, tuple[float, float]] = {}
    for ct in contours:
        role = role_of.get(ct.style)
        if role not in ("existing", "proposed") or ct.how == "legend":
            continue
        for cid in ct.chain_ids:
            ch = cmap.get(cid)
            if ch is None or len(ch.pts) < 2:
                continue
            ids.append(cid)
            geoms.append(LineString(ch.pts))
            owners.append(role)
        if ct.z is not None:
            lo, hi = rng.get(role, (ct.z, ct.z))
            rng[role] = (min(lo, ct.z), max(hi, ct.z))
    if not geoms:
        return 0
    tree = STRtree(geoms)
    n = 0
    for lab in labels:
        if lab.kind != "number" or lab.value is None or not lab.used or lab.chain_ids or lab.tag:
            continue
        if lab.value < min_single or not float(lab.value).is_integer():
            continue
        tol = max(2.5 * max(lab.height, 6.0), 14.0)
        cx, cy = lab.center
        pt = Point(cx, cy)
        best = None
        for gi in tree.query(shapely.box(cx - tol, cy - tol, cx + tol, cy + tol), predicate="intersects"):
            gi = int(gi)
            d = geoms[gi].distance(pt)
            if d <= tol and (best is None or d < best[0]):
                best = (d, gi)
        if best is None:
            continue
        r = rng.get(owners[best[1]])
        if r is None or not (r[0] - 20 <= lab.value <= r[1] + 20):
            continue
        lab.kind = "contour"
        lab.near = int(ids[best[1]])
        n += 1
    return n


def interpret(project: Project, key: str, doc: Document, info, rd: dict, ps: dict, prog=lambda m: None,
              prev: dict | None = None) -> dict:
    """Apply the operator's records to a sheet reading and give every line its height. `prev` is the
    sheet's previous analysis (what its cards showed before this read), None on a first read."""
    di, pi = _parse_key(key)
    page_role = ps.get("role", "none")
    role_for_families = "existing" if page_role == "existing" else "grading"
    strokes = rd["strokes"]
    glyph_ids = rd["glyph_ids"]
    chains: list[Chain] = rd["chains"]
    styles = rd["styles"]
    gaps = rd["gaps"]
    labels: list[Label] = rd["labels"]
    texts = list(rd["texts"])
    timing = dict(rd["timing"])
    notes: list[str] = []
    t1 = time.time()
    # what the operator's records did to each other since the last read (Project.note); the read
    # marks as seen only what it shows, so an entry written while it runs shows on the next one
    seen = float(ps.get("ledger_seen") or 0)
    ledger_shown_t = None
    for e in ps.get("ledger") or []:
        if float(e.get("t") or 0) > seen and e.get("text"):
            notes.append(e["text"])
            ledger_shown_t = max(ledger_shown_t or 0, float(e.get("t") or 0))

    # --- the operator's records find their lines (ids may have moved on a fresh reading)
    prog("applying your edits")
    remap = _Remapper(chains)
    edits = ps.get("edits") or {}
    anchors = edits.get("anchors") or {}
    hidden: set[int] = set()
    deleted_new: list[int] = []
    anchors_new: dict[str, list | None] = {}
    for cid in edits.get("deleted") or []:
        found = remap.match_all(cid, anchors.get(str(cid)))
        if not found:
            notes.append("a piece you removed is not in this reading of the sheet (nothing to hide; the record is kept)")
            continue
        for new in found:
            if new in deleted_new:
                continue
            hidden.add(new)
            deleted_new.append(new)
            anchors_new[str(new)] = _anchor_pts(remap.cmap[new].pts)
    user_lines = {lid: ln for lid, ln in (edits.get("lines") or {}).items() if ln}
    line_chains_new: dict[str, int] = {}
    line_hidden: dict[str, list[int]] = {}
    for lid, ln in user_lines.items():
        if ln.get("chain") is None:
            continue
        found = remap.match_all(ln["chain"], ln.get("chain_pts"))
        if not found:
            notes.append("the piece one of your edited lines replaced is not in this reading of the sheet; your line stands on its own")
            continue
        hidden.update(found)
        line_chains_new[lid] = found[0]
        line_hidden[lid] = found

    # operator corrections on labels (value / kind / used / side / tag / attached line)
    lab_by_id = {l.id: l for l in labels}
    attach_new: dict[str, int] = {}
    for lid_s, ov in (ps.get("label_overrides") or {}).items():
        if not ov:
            continue
        try:
            lab = lab_by_id.get(int(lid_s))
        except ValueError:
            lab = None
        if lab is None:
            continue
        _apply_label_override(lab, ov)
        if ov.get("attach") is not None:
            new = remap.match(ov["attach"], ov.get("attach_pts"))
            if new is None or new in hidden:
                lab.z_note = "the line you attached it to was not found again"
                notes.append(f'label "{lab.text}": the line it was attached to was not found again')
                continue
            attach_new[lid_s] = new
            lab.pinned = True
            lab.chain_ids = [new]
            lab.kind = "contour"
    # lines the operator switched to the other surface: their pieces take a synthetic style
    # (so a piece of an ignored style becomes a contour too); the whole joined line follows below
    roles_new: dict[str, dict] = {}
    switch_map: dict[int, str] = {}
    line_role_changes: dict[str, str] = {}
    kept: list[dict] = []            # records whose line is not in this reading: drawn from the record
    typed = ps.get("typed") or {}
    typed_found: dict[str, list[int]] = {}

    def _record_targets(rec: dict) -> list[int]:
        """Every current piece the record's line (and each of its pieces) lies on. Only the record's
        own chain counts towards "found under a new id": its pieces keep the ids of the reading they
        were made in, and counting each of them every read gave a false "213 of your records found
        their line under a new id" on an unchanged reading."""
        out: list[int] = []
        for i, pc in enumerate([{"chain": rec.get("chain"), "pts": rec.get("pts")}] + list(rec.get("pieces") or [])):
            m, l = remap.moved, remap.lost
            for new in remap.match_all(pc.get("chain"), pc.get("pts")):
                if new not in out:
                    out.append(new)
            if i > 0:
                remap.moved, remap.lost = m, l
        return out

    def _record_geoms(rec: dict) -> list[list]:
        """The record's line as it was drawn when the record was made: the dense geometry when the
        record carries it, the anchor points otherwise (records made before 2026-09-26)."""
        gs = [pc.get("geom") or pc.get("pts") for pc in (rec.get("pieces") or [])]
        gs = [g for g in gs if g and len(g) >= 2]
        if not gs:
            g = rec.get("geom") or rec.get("pts")
            gs = [g] if g and len(g) >= 2 else []
        return gs

    for rid, rec in (ps.get("roles") or {}).items():
        r2 = dict(rec)
        role = rec.get("role")
        if role not in SWITCH_STYLES:
            r2["lost"] = True
            roles_new[rid] = r2
            continue
        targets = _record_targets(rec)
        live = [t for t in targets if t not in hidden]
        gone = [t for t in targets if t in hidden]
        for t in live:
            switch_map[t] = role
        # A piece the operator then replaced with an edited line: the switch travels to the edited
        # line, unless the edit is the later record and chose the other surface. Subdivision A:
        # four lines switched by hand, then edited by hand, came back as "not found again" and the
        # edited lines kept whatever surface the editor had offered.
        carried = False
        for t in gone:
            lid = next((k for k, v in line_hidden.items() if t in v), None)
            if lid is None:
                continue                              # removed by the operator: nothing to switch
            ln = user_lines[lid]
            if ln.get("role") != role:
                # the later of the two records wins; an edit made before edits were timed (2026-09-26)
                # stands, since its surface was chosen in the editor's own form
                if not ln.get("set") or float(ln.get("set") or 0) > float(rec.get("set") or 0):
                    notes.append(f"a line you switched to {role} is also an edited line whose form says "
                                 f"{ln.get('role')}: the edit stands (switch the edited line if you meant {role})")
                else:
                    ln["role"] = role
                    line_role_changes[lid] = role
            carried = True
        for k in ("superseded", "carried", "kept"):
            r2.pop(k, None)
        if live:
            r2["chain"] = live[0]
            r2["lost"] = False
        elif carried:
            r2["lost"] = False
            r2["carried"] = True
        elif gone:
            r2["lost"] = False
            r2["superseded"] = "removed"
        else:
            r2["lost"] = True
            geoms = _record_geoms(rec)
            if geoms:
                kept.append({"rid": rid, "role": role, "geoms": geoms, "z": None, "typed_id": None})
                r2["kept"] = True
        roles_new[rid] = r2

    # the operator's typed heights find their pieces the same way (the heights themselves are
    # applied once the lines are joined, below)
    for tid, rec in typed.items():
        if rec.get("z") is None:
            continue
        targets = _record_targets(rec)
        typed_found[tid] = targets
        if targets:
            continue
        geoms = _record_geoms(rec)
        role = rec.get("role")
        if not geoms or role not in SWITCH_STYLES:
            continue                                  # reported below, once the contours exist
        # the same line may already be kept for a switch record: one drawn line carries both
        for k in kept:
            if k["role"] == role and any(_covers(g, kg) for g in geoms for kg in k["geoms"]):
                k["z"] = float(rec["z"])
                k["typed_id"] = tid
                break
        else:
            kept.append({"rid": tid, "role": role, "geoms": geoms, "z": float(rec["z"]), "typed_id": tid})

    # Lines kept from records: a record whose line is not in this reading (a piece the reader no
    # longer draws) is drawn from the record's own geometry, in the surface the record names, so
    # nothing the operator set can disappear from the surface. They join, count and compute like
    # any detected line and are marked "kept" in the UI.
    kept_chains: list[Chain] = []
    next_kid = KEPT_CHAIN_BASE
    for k in kept:
        ids: list[int] = []
        for g in k["geoms"]:
            P = np.asarray(g, dtype=float)
            if len(P) < 2:
                continue
            L = poly_length(P)
            ch = Chain(id=next_kid, style=SWITCH_STYLES[k["role"]], pts=P, members=[], length=L, ink=L,
                       closed=False, candidate=True)
            next_kid += 1
            kept_chains.append(ch)
            switch_map[ch.id] = k["role"]
            ids.append(ch.id)
        k["chain_ids"] = ids
        if k["typed_id"]:
            typed_found[k["typed_id"]] = ids
    if kept:
        notes.append(f"{len(kept)} of your records name a line that is not in this reading of the sheet: "
                     f"drawn from the record{'s' if len(kept) > 1 else ''} (marked kept)")
    # --- what the reviewer did in the product (core_edits.py): a piece lying on a line he erased
    # is left out, unless the operator set something on it HERE (a typed height, a switch)
    from .core_edits import erased_pieces, roles_from_review, sig_of
    review_sig = sig_of(ps.get("core_edits"))
    review = project.core_edits(key) if review_sig else None
    review_did = {"erased": 0, "erased_pt": 0.0, "held": 0, "named": {}}
    core_hidden: set[int] = set()
    if review_sig and review is None:
        notes.append("the lines erased and named in review could not be read back: none of it is applied "
                     "(open Cut / fill from the sheet again)")
    elif review and review.get("erased"):
        held = set(switch_map) | {t for ts in typed_found.values() for t in ts}
        core_hidden, erased_pt, n_held = erased_pieces([c for c in chains if c.id not in hidden], review, keep=held)
        review_did.update(erased=len(core_hidden), erased_pt=round(erased_pt, 1), held=n_held)
        notes.extend(_review_erased_notes(len(core_hidden), erased_pt, n_held))
    chains_b = [(replace(c, style=SWITCH_STYLES[switch_map[c.id]]) if c.id in switch_map else c)
                for c in chains if c.id not in hidden and c.id not in core_hidden] + kept_chains

    # --- contour families and their heights --------------------------------
    prog("giving contour lines their heights")
    # who says which lines are contours: the legend, then the operator (who wins)
    legend_info: dict = {}
    legend_roles = read_legend_roles(labels, strokes, styles, glyph_ids, capable=contour_capable_styles(chains_b),
                                     notes=notes, info=legend_info)
    forced = {sid: (role, why) for sid, (role, why) in legend_roles.items()}
    if review and review.get("named"):
        for sid, (role, why) in roles_from_review(chains_b, styles, review).items():
            if role is None:
                notes.append(f"one pen is {why}: its surface is read from the sheet")
                continue
            was = forced.get(sid)
            if was and was[0] != role:
                notes.append(f"a pen the legend read here calls {was[0]} is {why}: the review's name is used "
                             f"(change it in the Families card if the legend is right)")
            forced[sid] = (role, why)
            review_did["named"][str(sid)] = role
    style_roles = ps.get("style_roles") or {}
    for st in styles:
        if st.sig in style_roles:
            forced[st.id] = (style_roles[st.sig], "set by you")
    for sid, role in (ps.get("family_roles") or {}).items():          # not yet migrated (no analysis to name it by)
        try:
            forced[int(sid)] = (role, "set by you")
        except ValueError:
            continue
    families = contour_families(styles, page_role=role_for_families, forced=forced)
    n_left = len(leave_out_unlisted_pens(families, legend_info))
    if n_left:
        notes.append(f"the legend shows a pen for every contour row: {n_left} other pen{'s' if n_left != 1 else ''} "
                     f"{'are' if n_left != 1 else 'is'} not taken for contours (set one in the Families card if it is)")
    # a surface that moved with what came over from review is said - a volume never moves silently -
    # once the pens' surfaces are final (qualify_families, below); the notes go here, beside the
    # review's other notes
    review_note_at = len(notes)
    # A typed height names a contour line: a piece of an ignored style the operator typed a height
    # on is a contour of the surface the record names (subdivision A: "the line you gave 575 ft is
    # not a contour line now (its style is ignored)" - a hand-set height that did nothing).
    role_of_st = {st: f.role for f in families for st in (f.styles or [f.style])}
    cb_style = {c.id: c.style for c in chains_b}
    promoted: dict[int, str] = {}
    for tid, rec in typed.items():
        role = rec.get("role")
        if rec.get("z") is None or role not in SWITCH_STYLES:
            continue
        for t in typed_found.get(tid, []):
            if t in hidden or t in switch_map or t not in cb_style:
                continue
            if role_of_st.get(cb_style[t]) not in ("existing", "proposed"):
                promoted[t] = role
    if promoted:
        switch_map.update(promoted)
        chains_b = [(replace(c, style=SWITCH_STYLES[promoted[c.id]]) if c.id in promoted else c) for c in chains_b]
    for role in ("existing", "proposed"):
        if role in switch_map.values():
            families.append(Family(style=SWITCH_STYLES[role], role=role, role_reason=f"switched to {role} by you",
                                   styles=[SWITCH_STYLES[role]], forced=True, color=(0.0, 0.0, 0.0), width=1.0))
    legend_boxes = find_legend_boxes(labels, strokes, info.width, info.height)
    profile_boxes = find_profile_boxes(labels, chains_b)
    # legend / table blocks with no LEGEND word: stacked short rules with word captions (the hotel site:
    # the legend's swatch rows became contours carrying the sample numbers as heights)
    table_boxes = find_table_boxes(labels, chains_b)
    legend_boxes = legend_boxes + table_boxes
    n_legend = mark_legend_samples(labels)
    for lab in labels:
        if lab.value is not None and any(_inside_box(lab.center, b) for b in profile_boxes):
            lab.used = False
            lab.z_note = "inside a profile view"
        elif lab.value is not None and any(_inside_box(lab.center, b) for b in table_boxes):
            lab.used = False
            lab.z_note = "inside a legend / table block (stacked rules with captions)"
            n_legend += 1
    if table_boxes:
        notes.append(f"{len(table_boxes)} legend / table block(s) found by their stacked rules and captions: the lines and "
                     f"numbers inside are not the plan")
    # a legend printed vertically is found but not read (its rows sit too close to tell the swatches
    # apart): say so, so nobody thinks the surfaces came from it
    if not legend_roles and any("CONTOUR" in (l.text or "").upper() and len(l.text.split()) <= 5
                                and abs(((l.angle + 180) % 360) - 180) > 20 for l in labels):
        notes.append("a legend printed vertically was found and not read: the surfaces come from the linework "
                     "(colour, dash, layer names) and the printed heights only")
    has_datum = bool(project.data["settings"].get("datum")) or any(l.kind == "datum" for l in labels)
    min_single = 10.0 if has_datum else 100.0
    # the operator's typed heights, found on the current pieces first: a join must never merge two
    # pieces he gave different heights (the counters are restored: the loop below reports them)
    typed_pre: dict[int, float] = {}
    for tid, rec in typed.items():
        if rec.get("z") is None:
            continue
        for new in typed_found.get(tid, []):
            if new not in hidden:
                typed_pre[new] = float(rec["z"])
    # whole numbers read from the wrong side ("999" for a 666 printed along its line), by the
    # sheet's own range of heights: before the heights go to their lines
    turned_ = upright_heights(labels)
    if turned_:
        notes.append(f"{len(turned_)} printed height{'s' if len(turned_) != 1 else ''} had been read upside down "
                     f"(e.g. {turned_[0].z_note.split(':')[0].replace('read ', '')}, which is {turned_[0].text}): "
                     f"turned by the sheet's own range of heights")
    built: dict = {}
    drawn_ = drawn_shares(chains_b, strokes)
    lab_state = [(l.kind, list(l.chain_ids), l.z_note) for l in labels]
    contours = build_contours(chains_b, gaps, families, labels, legend_boxes=legend_boxes + profile_boxes,
                              min_single=min_single, typed_z=typed_pre, core_links=rd.get("core_links"),
                              core_chains=rd.get("core_chains"), drawn=drawn_, stats=built)
    # the printed heights are on their lines now: a pen that is "proposed" on its colour alone and
    # carries the existing ground's screened heights is a pen of the existing ground. The lines are
    # then built once more, each pen on its own surface (which pieces cross their own surface, which
    # ends join, which heights land where: all of it follows the surfaces)
    ink_moved = roles_by_label_ink(families, contours, labels)
    if ink_moved:
        for l, (k_, c_, n_) in zip(labels, lab_state):
            l.kind, l.chain_ids, l.z_note = k_, list(c_), n_
        built = {}
        contours = build_contours(chains_b, gaps, families, labels, legend_boxes=legend_boxes + profile_boxes,
                                  min_single=min_single, typed_z=typed_pre, core_links=rd.get("core_links"),
                                  core_chains=rd.get("core_chains"), drawn=drawn_, stats=built)
        for f_, g_, n_ in ink_moved:
            notes.append(f"a dark pen ({_pen_words(f_)}) carries screened heights like the existing ground's ({g_} of {n_}): "
                         f"it is read as EXISTING (set it in the Families card if it is not)")
    if built.get("drafted"):
        notes.append(f"{built['drafted']} piece{'s' if built['drafted'] != 1 else ''} in a contour pen "
                     f"{'are' if built['drafted'] != 1 else 'is'} drawn like curbs and walls (straight lines and arcs, "
                     f"{built['drafted_pt']:,.0f} pt): not taken for contours (type a height on one that is)")
    if built.get("crossers"):
        n_x = built["crossers"]
        notes.append(f"{n_x} piece{'s' if n_x != 1 else ''} in a contour pen cross{'' if n_x != 1 else 'es'} four or more "
                     f"contour lines of {'their' if n_x != 1 else 'its'} own surface, or run{'' if n_x != 1 else 's'} dead "
                     f"straight across four lines ({built['crossers_pt']:,.0f} pt): not taken for contours "
                     f"(type a height on one that is)")
    kept_rid_of = {cid: k["rid"] for k in kept for cid in k.get("chain_ids", [])}
    for ct in contours:
        sw = next((switch_map[cid] for cid in ct.chain_ids if cid in switch_map), None)
        if sw:
            ct.style = SWITCH_STYLES[sw]
            ct.switched = sw
        kid = next((cid for cid in ct.chain_ids if cid in kept_rid_of), None)
        if kid is not None:
            ct.kept = kept_rid_of[kid]
            ct.z_note = "kept from your record: the sheet reading no longer has this line"
    ct_by_chain = {cid: ct for ct in contours for cid in ct.chain_ids}

    # --- heights the operator typed on detected lines (anchored to the line's geometry)
    typed_new: dict[str, dict] = {}
    joined_more: dict = {}
    overrides: list = []
    ct_len = {c.id: c.length for c in contours}
    role_of_style_now = {st: f.role for f in families for st in (f.styles or [f.style])}
    for tid, rec in typed.items():
        r2 = dict(rec)
        if rec.get("z") is None:
            typed_new[tid] = r2
            continue
        found = typed_found.get(tid, [])
        live = [t for t in found if t not in hidden]
        gone = [t for t in found if t in hidden]
        for k in ("superseded", "kept"):
            r2.pop(k, None)
        if not live and gone:
            # the line is there, but the operator replaced it with an edited line (or removed it):
            # the edited line carries its own height, so the typed one is superseded, not lost
            # (subdivision B: eight of Koby's typed heights were reported lost this way)
            new = gone[0]
            r2["chain"] = new
            r2["lost"] = False
            r2["superseded"] = True
            typed_new[tid] = r2
            lid = next((k for k, v in line_hidden.items() if new in v), None)
            ez = (user_lines.get(lid) or {}).get("z") if lid else None
            if lid and ez is not None and abs(float(ez) - float(rec.get("z", ez))) > 0.01:
                notes.append(f"the height you typed earlier ({rec.get('z'):g} ft) is set aside: you then edited "
                             f"that line and gave it {float(ez):g} ft")
            continue
        if not live:
            r2["lost"] = True
            typed_new[tid] = r2
            notes.append(f"a typed height ({rec.get('z')} ft) names a line that is not in this reading of the sheet, "
                         f"and the record carries no geometry to draw it from (made before 2026-09-26)")
            continue
        if live[0] in kept_rid_of:
            r2["lost"] = True                 # the line is not in the reading: drawn from the record
            r2["kept"] = True
        else:
            r2["chain"] = live[0]
            r2["lost"] = False
        typed_new[tid] = r2
        # every contour the record's pieces ended in takes the height (a line the reader split
        # since the record was made is still the one line the operator typed on) - a contour of a
        # surface, that is: a contour object exists for every family, and a height applied to a
        # line of an ignored pen feeds nothing (two of Koby's subdivision B heights read "applied" that way)
        # how each surface frames its printed heights (plain text, an oval ...): the majority frame of
        # the labels attached to that surface's lines, known once five or more are attached; a sheet
        # that frames nothing (the hotel site: both surfaces plain) gives no majority and the note
        # then counts only labels printed IN a gap of the line (never the one-sided end rule):
        # a false "you override a printed label" is worse than a missing one
        _dbg_labels = {int(x) for x in os.environ.get("CUTFILL_DEBUG_LABEL", "").split(",") if x.strip().isdigit()}
        _frames_by_role: dict = defaultdict(Counter)
        for _c in contours:
            _r = role_of_style_now.get(_c.style)
            if _r in ("existing", "proposed"):
                for _lid in _c.label_ids:
                    if _lid in lab_by_id:
                        _frames_by_role[_r][lab_by_id[_lid].frame] += 1
        usual_frame = {r: cnt.most_common(1)[0][0] for r, cnt in _frames_by_role.items() if sum(cnt.values()) >= 5}
        applied: list[int] = []
        for t in live:
            ct = ct_by_chain.get(t)
            if ct is None or ct.id in applied or role_of_style_now.get(ct.style) not in ("existing", "proposed"):
                continue
            applied.append(ct.id)
            if ct.z is not None and any(lab_by_id[l].pinned for l in ct.label_ids if l in lab_by_id):
                # a label the operator attached to this line says its height: the printed number wins
                if abs(float(ct.z) - float(rec["z"])) > 0.01:
                    notes.append(f"line {ct.id}: the label you attached says {ct.z:g} ft, the height you typed earlier "
                                 f"({rec['z']:g} ft) is set aside")
                continue
            # every printed label attached to one of this line's pieces that says another height (not
            # only the vote that won: a label the vote lost is still printed on the operator's line) -
            # framed the way this surface's labels are framed: a proposed oval that landed on an
            # existing line (subdivision B: L410 on #383, L432 on #295, the proposed pen being ignored) is not
            # a printed height of that line, and an accusation needs a solid label (rule 3)
            line_role = role_of_style_now.get(ct.style)
            usual = usual_frame.get(line_role, "?")          # "?" = no majority known for this surface
            cand = [l for l in labels if l.kind == "contour" and l.value is not None
                    and any(int(c) in ct.chain_ids for c in (l.chain_ids or [])) and abs(float(l.value) - float(rec["z"])) > 0.01]
            printed = sorted({float(l.value) for l in cand if (usual != "?" and l.frame == usual) or (usual == "?" and l.gap_id is not None)})
            if _dbg_labels and any(l.id in _dbg_labels for l in cand):
                print(f"[override] line {ct.id} typed {rec['z']} role {line_role} usual frame {usual!r}: "
                      f"{[(l.id, l.text, l.frame, l.gap_id) for l in cand]} -> printed {printed}", file=sys.stderr)
            if printed:
                overrides.append((ct.id, float(rec["z"]), printed[0]))     # said in one note below (rule 3)
            ct.z = float(rec["z"])
            ct.how = "typed"
            ct.typed_id = tid
            ct.label_ids = []
            ct.basis = []
            ct.candidates = []
        if not applied:
            notes.append(f"the line you gave {rec.get('z')} ft is not a contour line now (its style is ignored)")
            r2["fate"] = "ignored style"
            continue
        tot = sum(c.length for c in contours if c.id in applied)
        # a typed height counts whatever the piece's length (Koby: "if I typed them then they are
        # contours"); the fate says when it sits on a short piece, so it can be seen
        r2["fate"] = "applied" if tot >= 60 else "short"
        # the reading may have joined the line the operator typed on with more linework (an unlabelled
        # continuation); the height covers all of it now, and the read says so rather than hiding it
        own = sum(poly_length(np.asarray(pc["geom"], dtype=float)) for pc in (rec.get("pieces") or [])
                  if pc.get("geom") and len(pc["geom"]) >= 2)
        if own > 0:
            # per LINE, not per record: a line carrying five records must not be charged five times
            # (the critic of 2026-09-26: "100 records, 74,140 pt" was 13,000 pt of real new linework)
            for cid_ in applied:
                acc = joined_more.setdefault(cid_, [ct_len[cid_], 0.0])     # [line length, its records' own geometry]
                acc[1] += own

    # a line the operator switched to a surface and that has no height in this reading is said by
    # name (rule 3: a re-read that drops a height from a line he touched must not hide in a table)
    for rid, rec in roles_new.items():
        if rec.get("lost") or rec.get("carried") or rec.get("superseded") or rec.get("chain") is None:
            continue
        ct = ct_by_chain.get(int(rec["chain"]))
        if ct is not None and ct.z is None:
            notes.append(f"the line you switched to {rec.get('role')} (#{ct.id}, {ct.length:.0f} pt) has no height in this reading"
                         + (": " + ct.candidates[0].get("note", "") if ct.candidates and ct.candidates[0].get("note") else ""))
    gained = [(cid_, L_ - min(L_, o)) for cid_, (L_, o) in joined_more.items()]
    gained = sorted([(c, g) for c, g in gained if g > 60], key=lambda t: -t[1])
    if gained:
        notes.append(f"{len(gained)} line(s) you typed on now also cover {sum(g for _, g in gained):.0f} pt the reading "
                     f"joined to them (e.g. line {gained[0][0]} +{gained[0][1]:.0f} pt)")
    if overrides:
        notes.append(f"{len(overrides)} of your typed heights override a printed label on their own line "
                     f"(e.g. line {overrides[0][0]}: you typed {overrides[0][1]:g} ft, the label says {overrides[0][2]:g} ft)")

    # --- lines the operator drew or edited: real contours of their surface, counted and
    # used like any detected line
    ul_chains: list[Chain] = []
    ul_map: dict[int, str] = {}
    ul_fams: dict[str, Family] = {}
    next_cid = UL_CHAIN_BASE
    for lid in sorted(user_lines):
        ln = user_lines[lid]
        role = ln.get("role")
        pts = ln.get("pts") or []
        if role not in USER_STYLES or len(pts) < 2:
            continue
        P = np.asarray(pts, dtype=float)
        L = poly_length(P)
        ch = Chain(id=next_cid, style=USER_STYLES[role], pts=P, members=[], length=L, ink=L, closed=False, candidate=True)
        next_cid += 1
        ul_chains.append(ch)
        ul_map[ch.id] = lid
        z = ln.get("z")
        ct = Contour(id=len(contours), style=USER_STYLES[role], chain_ids=[ch.id],
                     z=(float(z) if z is not None else None),
                     how=((ln.get("source") or "drawn") if z is not None else ""), length=L, user=lid)
        contours.append(ct)
        if role not in ul_fams:
            ul_fams[role] = Family(style=USER_STYLES[role], role=role, role_reason="drawn / edited by you",
                                   styles=[USER_STYLES[role]], forced=True, color=(0.0, 0.0, 0.0), width=1.0)
    families = list(families) + [ul_fams[r] for r in ("existing", "proposed") if r in ul_fams]
    chains_all = chains + ul_chains + kept_chains

    qualify_families(families, contours, labels=labels)
    # the pens' surfaces are final: every pen whose surface moved with the review's names is said,
    # with what each read before this read (_review_pen_notes)
    review_changed = prev is not None and str(prev.get("review") or "") != review_sig
    if review_did["named"] or review_changed:
        pen_notes, review_did["moved"] = _review_pen_notes(families, prev, review_changed)
        notes[review_note_at:review_note_at] = pen_notes
    fam_styles = {st for f in families for st in (f.styles or [f.style])}
    infer_intervals(contours, chains_all, families, labels)
    prop = propagate_heights(contours, chains_all, families, labels=labels)
    propg = propagate_global(contours, chains_all, families, labels=labels)
    prog("checking heights against their neighbours")
    verify = verify_heights(contours, chains_all, families, labels)
    # a second count after the check: lines it set aside, and pieces it split off their line, are
    # counted again from the neighbours that are left (subdivision A: the 577 piece split off the
    # 579 hairpin had two heights allowed by its rays and takes the one the count gives)
    if (verify["counted_dropped"] or verify["labels_flagged"] or verify.get("pieces_split")) and not prop["budget_hit"]:
        prop2 = propagate_heights(contours, chains_all, families, budget_s=45.0, labels=labels)
        verify2 = verify_heights(contours, chains_all, families, labels, budget_s=30.0)
        verify["counted_dropped"] += verify2["counted_dropped"]
        verify["labels_flagged"] += verify2["labels_flagged"]
        verify["kept_conflicts"] += verify2.get("kept_conflicts", 0)
        prop["budget_hit"] = prop["budget_hit"] or prop2["budget_hit"] or verify2["budget_hit"]
    verify["budget_hit"] = verify.get("budget_hit", False) or prop["budget_hit"]
    if verify.get("pieces_split"):
        n = verify["pieces_split"]
        notes.append(f"{n} piece(s) were split off the line they were joined to: their own neighbours put them at "
                     f"another height (each says so in its note)")
    n_long = sum(1 for c in contours if (c.z_note or "").startswith("split off") and c.z is None and c.length > 300)
    if n_long:
        notes.append(f"{n_long} split-off piece(s) longer than 300 pt have no height yet: check them in the Lines card")
    for fam in families:
        fc = [c for c in contours if c.style in (fam.styles or [fam.style])]
        fam.n_contours = len(fc)
        fam.n_with_z = sum(1 for c in fc if c.z is not None)
        fam.length_with_z = sum(c.length for c in fc if c.z is not None)
        fam.length_total = sum(c.length for c in fc)
    # contours inside a legend box / profile view are not the site
    cm_ = {c.id: c for c in chains_all}
    excl_boxes = legend_boxes + profile_boxes
    for ct in contours:
        if ct.user:
            continue
        # the line is in a block when most of its points are (not only its first piece's middle:
        # a table frame's three pieces straddled a box built from the rows and kept its height)
        if excl_boxes:
            # by LENGTH, not by point count: a table frame is five points over 1,344 pt and its
            # title box a dozen points over 47 pt
            l_in = l_all = 0.0
            for cid in ct.chain_ids:
                P = cm_[cid].pts if cid in cm_ else None
                if P is None or len(P) < 2:
                    continue
                for q0, q1 in zip(P[:-1], P[1:]):
                    seg = float(np.hypot(q1[0] - q0[0], q1[1] - q0[1]))
                    l_all += seg
                    if any(_inside_box(((q0[0] + q1[0]) / 2, (q0[1] + q1[1]) / 2), b) for b in excl_boxes):
                        l_in += seg
            if l_all > 0 and l_in >= 0.5 * l_all:
                ct.z = None
                ct.how = "legend"
    n_near = _mark_near_line_numbers(labels, contours, families, chains_all, min_single)
    timing["heights"] = round(time.time() - t1, 1)

    # --- resolve printed numbers to real elevations -------------------------
    datum = project.data["settings"].get("datum")
    datum_labels = [l for l in labels if l.kind == "datum"]
    abbrev_base = project.data["settings"].get("abbrev_base")
    # the sheet's own word on its short numbers: "7605.25 -> 05.25", "ALL SPOT ELEVATIONS ... ARE CIVIL
    # ELEVATIONS AND DO NOT CORRELATE TO ARCH. ELEVATIONS" (a client's civil sets). Then the
    # architectural datum note printed beside it (ARCH. 100' = CIVIL 5000') is for the structural
    # set, and applying it here put FG 48.07 at 4,948 instead of 5,048.
    from .labels import RE_TRUNCATED_NOTE
    spots_truncated = any(RE_TRUNCATED_NOTE.search(str(t or "")) for t in texts)
    datum_off = False
    if datum is None and datum_labels:
        datum = list(datum_labels[0].datum)
    if datum and spots_truncated and not project.data["settings"].get("datum"):
        datum_off = True
        # the base the short numbers add: the operator's, else the hundreds of the datum note's civil
        # value (100 = 5000 puts the site in the 5000s: FG 48.07 is 5,048.07, TW 35 is 5,035) - a sheet
        # whose contours carry no heights has no range to resolve them from
        if abbrev_base in (None, ""):
            abbrev_base = math.floor(float(datum[1]) / 100.0) * 100.0
            base_note = f"; short numbers add {abbrev_base:g} (the hundreds of that note; set another base in the Datum card)"
        else:
            base_note = f"; short numbers add {float(abbrev_base):g} (the base you set)"
        notes.append("this sheet says its short spot grades are truncated civil elevations, so the printed "
                     f"datum note ({float(datum[0]):g} = {float(datum[1]):g}) is not applied to them" + base_note)
    distinct = sorted({(round(float(l.datum[0]), 2), round(float(l.datum[1]), 2)) for l in datum_labels if l.datum})
    if len(distinct) > 1:
        used = f"{float(datum[0]):g} = {float(datum[1]):g}" if datum else "none"
        notes.append("the sheet prints datum notes that disagree: "
                     + "; ".join(f"{a:g} = {b:g}" for a, b in distinct)
                     + f" (using {used}; a set can carry one per structure: type the one that applies)")
    if datum and not datum_off:
        proj_, real_ = float(datum[0]), float(datum[1])
        for ct in contours:
            if ct.z is not None and abs(ct.z - proj_) <= 60:
                ct.printed = ct.z
                ct.z = ct.z + (real_ - proj_)
                ct.z_note = f"datum {proj_:g} = {real_:g}"
                for b in ct.basis:
                    if abs(float(b.get("z", 0)) - proj_) <= 60:
                        b["z"] = float(b["z"]) + (real_ - proj_)
    # the site's height range, for resolving abbreviated spot grades ("91.70" on a 5980-6001 site):
    # from the two surfaces' lines only, and robust to one stray value (the hotel site C3: an OCR
    # "95.8" on a spot leader in an ignored pen made the range [95.8, 5997], no spot was shifted,
    # and 100 of them fell out silently)
    role_of_st = {st: f.role for f in families for st in (f.styles or [f.style])}
    zs = sorted(c.z for c in contours if c.z is not None and c.how != "legend"
                and role_of_st.get(c.style) in ("existing", "proposed"))
    if len(zs) >= 6:
        contour_range = (zs[1], zs[-2])
    elif len(zs) >= 3:
        contour_range = (zs[0], zs[-1])
    else:
        contour_range = None
    res_summary = resolve_values(labels, tuple(datum) if datum else None, contour_range,
                                 abbrev_base=float(abbrev_base) if abbrev_base not in (None, "") else None,
                                 datum_off=datum_off,
                                 base_is_typed=project.data["settings"].get("abbrev_base") not in (None, ""))
    for lab in labels:
        if lab.kind == "contour" and lab.z is None:
            lab.z = lab.value
        if lab.kind == "contour" and not lab.chain_ids and lab.near is not None and lab.used:
            lab.z_note = (lab.z_note + "; " if lab.z_note else "") + "next to a line but not attached"
    prog("placing spot grades")
    spots = place_spots(labels, strokes, page_default="default")
    # screened contour heights whose line is not drawn: points of the existing ground
    n_scr = len(screened_heights_as_points(spots, labels, families, contours, excl_boxes))
    if n_scr:
        notes.append(f"{n_scr} screened contour height{'s' if n_scr != 1 else ''} stand{'' if n_scr != 1 else 's'} on no line "
                     f"(the existing contours are not drawn there): each is a point of the existing ground where it is printed")
    # spot grades far outside the contoured area are tables / notes, not the site. The area is the
    # two SURFACES' contours (a title-block or detail pen with three "contours" had set a box that
    # dropped 121 of 127 spot grades on one civil sheet)
    site = _site_extent(chains_all, [c for c in contours if role_of_st.get(c.style) in ("existing", "proposed") and c.how != "legend"])
    if site is not None:
        x0, y0, x1, y1 = site
        for sp in spots:
            if not (x0 <= sp.x <= x1 and y0 <= sp.y <= y1):
                sp.side_reason += "; outside the contoured area"
                sp.side = "outside"
    # building pads the sheet prints (a finished-floor height inside a closed outline): offered,
    # never used unless the operator accepts one (surfaces_for)
    prog("looking for building pads")
    pads = find_floor_pads([l for l in labels if not any(_inside_box(l.center, b) for b in excl_boxes)],
                           [c for c in chains if c.id not in hidden and c.id not in core_hidden],
                           info.width, info.height)
    if any(p["polygon"] for p in pads):
        n_pad = sum(1 for p in pads if p["polygon"])
        notes.append(f"{n_pad} printed floor height{'s' if n_pad != 1 else ''} inside a closed outline: "
                     f"offered as {'building pads' if n_pad != 1 else 'a building pad'} in the Building pads card "
                     f"(not used until you turn {'one' if n_pad != 1 else 'it'} on)")

    # --- scale --------------------------------------------------------------
    scands = scale_candidates(labels, texts + [doc.full_text(pi)])

    # records whose line ids moved are written back so the UI and the compute agree
    project.apply_remap(key, typed_new, {"deleted": deleted_new, "anchors": anchors_new,
                                         "line_chains": line_chains_new, "line_roles": line_role_changes},
                        attach_new, roles_new, ledger_seen=ledger_shown_t)
    if remap.moved:
        notes.append(f"{remap.moved} of your records found their line under a new id after re-reading")

    # lines the UI can draw and pick: contour families in full, other line styles that
    # could be picked as contours (long enough chains) at a coarser cut
    pickable = {st.id for st in styles if st.cand_length >= 80 or st.id in fam_styles}
    chains_json = [c.to_json() for c in chains if c.style in pickable and c.length >= (12 if c.style in fam_styles else 30)]
    for ch in ul_chains:
        j = ch.to_json()
        j["user"] = ul_map[ch.id]
        chains_json.append(j)
    for ch in kept_chains:
        j = ch.to_json()
        j["kept"] = kept_rid_of.get(ch.id)
        chains_json.append(j)
    result = {
        "engine": ENGINE, "key": key, "doc": di, "page": pi, "width": info.width, "height": info.height,
        "sheet": info.label, "page_role": page_role, "ocr_used": rd["ocr_used"], "timing": timing,
        "lines_from": ("core" if core_mode_of(rd) == "replace" else "engine+core joins") if rd.get("core") else "engine",
        "styles": [st.to_json() for st in styles if st.n_candidates > 0 or st.n_strokes > 200],
        "families": [f.to_json() for f in families],
        "chains": chains_json,
        "gaps": [g.to_json() for g in gaps if g.style in fam_styles],
        "labels": [l.to_json() for l in labels],
        "contours": [c.to_json() for c in contours],
        "spots": [sp.to_json() for sp in spots],
        "pads": pads,
        "site_extent": site,
        "legend_boxes": legend_boxes,
        "legend_roles": {str(k): list(v) for k, v in legend_roles.items()},
        "profile_boxes": profile_boxes,
        "legend_samples": n_legend,
        "scale_candidates": scands,
        "scale_default": pick_default(scands),
        "datum_candidates": [l.to_json() for l in datum_labels],
        "datum_used": datum,
        "resolution": res_summary,
        "verification": verify,
        "notes": notes,
        # the review's decisions this analysis was made with (Project.review_stale compares it)
        "review": review_sig, "review_applied": review_did, "review_erased": sorted(core_hidden),
        "counts": {"strokes": len(strokes), "chains": len(chains), "gaps": len(gaps), "labels": len(labels),
                   "text_numbers": rd["n_text_numbers"], "glyph_words": rd["n_clusters"], "contours": len(contours),
                   "contours_with_z": sum(1 for c in contours if c.z is not None), "spots": len(spots),
                   "user_lines": len(ul_chains), "kept_lines": len(kept_chains), "hidden_pieces": len(hidden),
                   "near_line_numbers": n_near,
                   "switched_lines": len(switch_map)},
    }
    return result


def _review_erased_notes(n_erased: int, erased_pt: float, n_held: int) -> list:
    """The notes on the pieces lying on lines erased in review: those left out, and those kept
    because the operator set a height or a surface on them here (one piece "carries", check 5A)."""
    out = []
    if n_erased:
        out.append(f"{n_erased} piece{'s' if n_erased != 1 else ''} of linework ({erased_pt:,.0f} pt) "
                   f"{'lie' if n_erased != 1 else 'lies'} on lines erased in review: left out here too")
    if n_held:
        out.append(f"{n_held} piece{'s' if n_held != 1 else ''} erased in review {'carry' if n_held != 1 else 'carries'} "
                   f"a height or a surface you set here: kept (remove the line here if it should go)")
    return out


def _pen_words(f) -> str:
    """A pen the way the Families card names it: "gray 0.0 · solid · w1.9" (and its layer). `f` is a
    family or a style of the reading, or its record in a stored analysis (a dict)."""
    get = f.get if isinstance(f, dict) else (lambda k: getattr(f, k, None))
    parts = [f"gray {float(get('gray') or 0.0):.1f}", "dashed" if get("dashed") else "solid", f"w{float(get('width') or 0.0):g}"]
    lay = str(get("layer") or "")
    if lay:
        parts.append("layer " + (lay if len(lay) <= 16 else lay[:15] + "…"))
    return " · ".join(parts)


def _review_pen_notes(families: list, prev: dict | None, review_changed: bool, cap: int = 6) -> tuple:
    """What the names given in review did to the pens' surfaces, said per group of pens with what
    each pen read BEFORE this read: its surface in the sheet's previous analysis, as the Families
    card showed it (final there - a pen with too few labels had been set aside as "ignore").

    Check 5A: the note said two named pens read "proposed" from the sheet alone - the first guess,
    before the label count set one aside, when that pen had shown "ignore" - and a third pen that
    went ignore -> proposed in the same read (it took the labels of the pen named existing) was never
    said. Now every pen whose surface moved is said: a named pen in any read, any other pen in a read
    where what came over from review changed (a pen the operator set himself is his own word). A
    first reading has no before: its named pens are said without one, never with a guess.
    Returns (notes, the number of named pens whose surface moved)."""
    before = {}
    for f in (prev or {}).get("families") or []:
        try:
            if f.get("sig") and int(f.get("style", -1)) >= 0:
                before[f["sig"]] = f.get("role") or "ignore"
        except (AttributeError, TypeError, ValueError):
            continue
    pens = [f for f in families if f.style >= 0]
    # a previous analysis whose pens this one cannot name (other code named them otherwise) is no before
    known = bool(before) and any(f.sig in before for f in pens)
    groups: dict = {}
    other = []
    for f in pens:
        was = before.get(f.sig, "ignore") if known else None        # a pen not listed before was no contour pen
        if str(f.role_reason).startswith("named in review"):
            if was != f.role:
                groups.setdefault((f.role, str(f.role_reason)), []).append((f, was))
        elif known and review_changed and was != f.role and str(f.role_reason) != "set by you":
            other.append((f, was))
    notes = []

    def listed(items, say):
        out = [say(f, was) for f, was in items[:cap]]
        return "; ".join(out) + (f"; {len(items) - cap} more" if len(items) > cap else "")

    for (role, why), items in list(groups.items())[:4]:
        n = len(items)
        if known:
            what = "before this read " + listed(items, lambda f, was: f"the {_pen_words(f)} pen read {was}")
        else:
            what = f"the pen{'s' if n != 1 else ''} " + listed(items, lambda f, was: _pen_words(f))
        notes.append(f"{n} pen{'s are' if n != 1 else ' is'} {role} here, {why} ({what}): change "
                     f"{'them' if n != 1 else 'it'} in the Families card if the name is wrong")
    if other:
        n = len(other)
        what = listed(other, lambda f, was: f"the {_pen_words(f)} pen read {was} before, {f.role} now")
        notes.append(f"{n} pen{'s' if n != 1 else ''} not named in review changed surface with this read ({what}): "
                     f"check {'them' if n != 1 else 'it'} in the Families card")
    return notes, sum(1 for items in groups.values() for _f, was in items if was is not None)


def _hotspots(g, both, se, sp_, evidence: list, cell_ft: float, min_depth: float = 1.0, top_n: int = 6) -> list:
    """Connected areas of cut (and of fill) deeper than `min_depth`, biggest volume first. Each
    names the lines / labels that feed most of its cells, so a suspicious block of volume can be
    traced to the evidence behind it. An area counts its cells the way the totals do: each by the
    part of it inside the limit (g.weight) - counted whole, an area along the limit's edge held more
    than the limit (check 5A: fill 13,239 CY on 78,400 SF, "102 %", in a 13,027.8 CY / 76,929 SF
    limit)."""
    from collections import Counter
    from scipy import ndimage
    depth = np.where(both, g.fg - g.eg, 0.0)
    wt = np.where(both, g.weight if g.weight is not None else 1.0, 0.0)
    out = []
    ev_by_id = {e.id: e for e in evidence}

    def describe(e):
        ref = e.ref or {}
        if ref.get("user_line"):
            return f"your {e.surface} line at {e.z:g} ft ({ref.get('how', 'drawn')})", f"u:{ref['user_line']}"
        if e.kind == "contour":
            return f"{e.surface} contour #{ref.get('contour')} at {e.z:g} ft ({ref.get('how') or 'no source'})", f"c:{ref.get('contour')}"
        if e.kind == "pad":
            return f"building pad at its printed floor height {e.z:g} ft (turned on by you)", f"l:{ref.get('label')}"
        return f"{e.kind} label #{ref.get('label')} at {e.z:g} ft", f"l:{ref.get('label')}"

    for name, mask in (("cut", depth < -min_depth), ("fill", depth > min_depth)):
        lab, n = ndimage.label(mask)
        if not n:
            continue
        vols = ndimage.sum(np.abs(depth) * wt, lab, index=range(1, n + 1)) * cell_ft * cell_ft / 27.0
        order = np.argsort(-np.asarray(vols))[:top_n]
        for k in order:
            m = lab == (k + 1)
            cells = int(m.sum())
            if cells < 2:
                continue
            jj, ii = np.nonzero(m)
            dm = depth[m]
            wm = wt[m]
            wsum = float(wm.sum())
            peak = int(np.argmax(np.abs(dm)))
            srcs: Counter = Counter()
            names = {}
            for j, i in zip(jj, ii):
                for tri in (se[j * g.nx + i], sp_[j * g.nx + i]):     # simplices are per flattened cell
                    for eid in tri:
                        e = ev_by_id.get(int(eid))
                        if e is None:
                            continue
                        text, key = describe(e)
                        srcs[key] += 1
                        names[key] = text
            total = sum(srcs.values()) or 1
            out.append({
                "kind": name, "cells": cells, "area_sf": round(wsum * cell_ft * cell_ft, 0), "volume_cy": round(float(vols[k]), 1),
                "mean_depth_ft": round(float((dm * wm).sum() / wsum) if wsum > 0 else float(np.mean(dm)), 2),
                "max_depth_ft": round(float(dm[peak]), 2),
                "peak": [int(ii[peak]), int(jj[peak])],
                "centre": [round(float(g.x0 + (ii.mean() + 0.5) * g.cell_pt), 1), round(float(g.y0 + (jj.mean() + 0.5) * g.cell_pt), 1)],
                "existing_ft": [round(float(np.nanmin(g.eg[m])), 1), round(float(np.nanmax(g.eg[m])), 1)],
                "proposed_ft": [round(float(np.nanmin(g.fg[m])), 1), round(float(np.nanmax(g.fg[m])), 1)],
                "sources": [{"text": names[key], "share": round(c / total, 3), "key": key} for key, c in srcs.most_common(6)],
            })
    out.sort(key=lambda h: -h["volume_cy"])
    return out


def _proof(existing: Surface, proposed: Surface, eg_pts, fg_pts, warnings: list) -> dict:
    """How well each surface, built from its contours alone, meets its own spot grades.
    Spot grades are held out: they were also fed into the surface, so the check is
    made against a contour-only surface."""
    out = {}
    for name, pts, surf in (("existing", eg_pts, existing), ("proposed", fg_pts, proposed)):
        cpts = [e for e in pts if e.kind == "contour"]
        spots = [e for e in pts if e.kind in ("spot", "wall", "derived")]
        rec = {"contour_points": len(cpts), "spot_points": len(spots), "checked": 0}
        if len(cpts) >= 3 and spots:
            s2 = Surface(name + "-contours", cpts)
            if s2.ok:
                res = []
                for e in spots:
                    z = s2.z_at(e.x, e.y)
                    if np.isfinite(z):
                        res.append((e.z - z, e))
                if res:
                    arr = np.array([r[0] for r in res])
                    worst = sorted(res, key=lambda r: -abs(r[0]))[:5]
                    rec.update({"checked": len(res), "mean_ft": round(float(arr.mean()), 2),
                                "median_abs_ft": round(float(np.median(np.abs(arr))), 2),
                                "max_abs_ft": round(float(np.abs(arr).max()), 2),
                                "worst": [{"evidence": e.id, "label": e.ref.get("label"), "z": e.z, "surface_z": round(e.z - d, 2),
                                           "diff_ft": round(d, 2), "page": e.page} for d, e in worst]})
                    if rec["median_abs_ft"] > 1.0:
                        warnings.append(f"{name}: the contours and the spot grades disagree by {rec['median_abs_ft']:g} ft "
                                        f"(median of {len(res)} spots). Check the flagged lines before trusting the volume.")
        out[name] = rec
    return out


def _inside_box(p, b) -> bool:
    return b[0] <= p[0] <= b[2] and b[1] <= p[1] <= b[3]


def _site_extent(chains, contours, pad: float = 0.06):
    cmap = {c.id: c for c in chains}
    xs, ys = [], []
    for ct in contours:
        if ct.z is None:
            continue
        for cid in ct.chain_ids:
            ch = cmap.get(cid)
            if ch is None:
                continue
            xs += [float(ch.pts[:, 0].min()), float(ch.pts[:, 0].max())]
            ys += [float(ch.pts[:, 1].min()), float(ch.pts[:, 1].max())]
    if not xs:
        return None
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    return [min(xs) - pad * w - 20, min(ys) - pad * h - 20, max(xs) + pad * w + 20, max(ys) + pad * h + 20]


def _apply_label_override(lab: Label, ov: dict):
    if "used" in ov:
        lab.used = bool(ov["used"])
    if ov.get("value") is not None:
        lab.value = float(ov["value"])
    if ov.get("kind"):
        lab.kind = ov["kind"]
    if "side" in ov:
        lab.side = ov["side"]
    if "tag" in ov:
        lab.tag = ov["tag"]


def _overlap(a, b) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    area = max(1e-6, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / area


def _same_word(cb, tb) -> bool:
    """A word found as strokes (box `cb`) is the word the text layer holds (box `tb`): either box
    mostly inside the other. A word turned on the sheet has a box half empty, and the text
    layer's own box of the same word covered 40 % of it (subdivision C sheet 3.2: a "616"
    came twice, outvoted the oval 617 of the line it lay across, and a 2,033-pt contour had no
    height). Only boxes of a like size: a short word inside a long one's box does not silence
    the long one."""
    if _overlap(cb, tb) > 0.5:
        return True
    a_c = max(1e-6, (cb[2] - cb[0]) * (cb[3] - cb[1]))
    a_t = max(1e-6, (tb[2] - tb[0]) * (tb[3] - tb[1]))
    return a_c <= 3.0 * a_t and _overlap(tb, cb) > 0.5


# ===========================================================================
# Where a sheet sits on the sheet shown lives in placement.py, a module in neither reading stamp: a placement fix
# no longer reads every sheet of every project again (moved there 2026-09-28 with the fix for pairs picked against
# another sheet - the critic's R1)
from .placement import page_transform  # noqa: E402


PAD_FIND_PT = 12.0      # a record finds its pad again within this far of where the floor height was printed


def pads_in_force(an: dict | None, ps: dict) -> tuple[list, list]:
    """The building pads of this read the operator has a record for, and the records that found no
    pad: ([(pad, record key, record)], [(record key, record)]). A record finds its pad by its key,
    else by where its floor height is printed (within PAD_FIND_PT, the same height): a radius
    test - the key rounds the place, so a read that moves the label half a point across a
    rounding edge would lose the record (a proximity test is not a bucket). A record whose
    floor height is not printed there any more finds nothing and is said, never carried."""
    recs = {k: r for k, r in ((ps or {}).get("pads") or {}).items() if isinstance(r, dict)}
    pads = [p for p in ((an or {}).get("pads") or [])]
    found, lost, taken = [], [], set()
    for rk in sorted(recs):
        rec = recs[rk]
        hit = next((p for p in pads if p["key"] == rk and p["key"] not in taken), None)
        if hit is None and rec.get("at") is not None and rec.get("z") is not None:
            near = [(math.hypot(p["at"][0] - float(rec["at"][0]), p["at"][1] - float(rec["at"][1])), p["key"], p)
                    for p in pads if p["key"] not in taken and abs(p["z"] - float(rec["z"])) < 0.005]
            near = [t for t in near if t[0] <= PAD_FIND_PT]
            hit = min(near)[2] if near else None
        if hit is None:
            lost.append((rk, rec))
            continue
        taken.add(hit["key"])
        found.append((hit, rk, rec))
    return found, lost


def _use_pads(pads_used: list, evidence: list, fg_pts: list, warnings: list, ft_per_pt: float) -> None:
    """The accepted building pads enter the proposed surface: flat at the printed floor height
    over the pad's outline. What the proposed surface held inside a pad goes: every point inside
    the outline, and the whole of a piece that lies mostly (60 %) inside it - a line with most of
    its length in a building is the building's own linework carrying a counted height, no contour
    (the hotel site C3: four such pieces). A contour that ends at the wall stays."""
    for pu in pads_used:
        pg = Polygon(pu["polygon"])
        if not pg.is_valid:
            pg = pg.buffer(0)
        if pg.geom_type != "Polygon":
            pg = max(getattr(pg, "geoms", [pg]), key=lambda g_: g_.area)
        inner = pg.buffer(-1.0)
        if inner.is_empty:
            warnings.append(f"building pad {pu['text']!r}: its outline is too thin to stand on: it is not used")
            continue
        prop = [e for e in fg_pts if e.kind != "pad"]
        if prop:
            inside = shapely.contains_xy(inner, np.array([e.x for e in prop]), np.array([e.y for e in prop]))
        else:
            inside = np.zeros(0, dtype=bool)
        by_piece: dict = defaultdict(lambda: [0, 0])
        for e, ins in zip(prop, inside):
            if e.kind == "contour":
                k = (e.page, e.ref.get("chain"), e.ref.get("user_line"))
                by_piece[k][0] += 1
                by_piece[k][1] += int(ins)
        whole = {k for k, (n, n_in) in by_piece.items() if n and n_in >= 0.6 * n and not k[2]}
        drop = set()
        for e, ins in zip(prop, inside):
            if ins or (e.kind == "contour" and (e.page, e.ref.get("chain"), e.ref.get("user_line")) in whole):
                drop.add(e.id)
        if drop:
            fg_pts[:] = [e for e in fg_pts if e.id not in drop]
        area_sf = pg.area * ft_per_pt * ft_per_pt
        step = max(12.0, math.sqrt(pg.area / 3000.0))       # at most about 3,000 points inside a pad
        for x, y in pad_points([list(c) for c in pg.exterior.coords][:-1], step=step):
            ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(pu["z"]), kind="pad", page=pu["page"],
                          ref={"pad": pu["key"], "label": pu["label_id"], "text": pu["text"]}, surface="proposed")
            evidence.append(ev)
            fg_pts.append(ev)
        n_lines = len(whole)
        warnings.append(f"building pad {pu['text']!r}: the proposed surface is flat at {pu['z']:,.2f} ft over its outline "
                        f"({area_sf:,.0f} SF), turned on by you"
                        + (f"; {len(drop)} point{'s' if len(drop) != 1 else ''} the proposed surface held inside it "
                           f"{'are' if len(drop) != 1 else 'is'} left out" if drop else "")
                        + (f" ({n_lines} line{'s' if n_lines != 1 else ''} lying mostly inside the building)" if n_lines else ""))


def surfaces_for(project: Project) -> dict:
    """The two surfaces from every active sheet's evidence, mapped into the display sheet's frame -
    the first half of compute_cutfill, shared with the grade lookup under walls (grade_at) and the
    wall rows so a wall's dig depth rests on exactly the surface the volumes do. Returns {"error"}
    (with sources / warnings) when a surface cannot be built, else the surfaces and everything
    compute needs to carry on."""
    s = project.data["settings"]
    display = s.get("display_page")
    pages = project.data["pages"]
    roles = {k: v.get("role", "none") for k, v in pages.items()}
    active = [k for k, r in roles.items() if r in ("existing", "proposed", "both", "grading")]
    if display is None or roles.get(display, "none") == "none":
        display = next(iter(active), None)
    if display is None:
        return {"error": "Mark at least one sheet as existing / proposed / both."}
    # a sheet whose review's scale readings disagree: no volume until a person chooses one on the sheet (scale verdict
    # S4) - neither the scale handed over before nor this reader's own printed default
    # EVERY sheet in the job, not the display sheet alone (CRITIC-scale-3 MINOR 9): a linked sheet whose readings
    # disagree would be measured at the display sheet's scale - each is refused by name
    from .placement import review_conflict
    _cw = review_conflict(project, display)
    _others = []
    for _k in active:
        if _k == display:
            continue
        if review_conflict(project, _k):
            _nm = _sheet_words(project, _k)
            _others.append(f"{_nm[:1].upper() + _nm[1:]}'s scale readings disagree. Choose one on that sheet.")
    if _cw or _others:
        return {"error": " ".join(([_cw] if _cw else []) + _others)}
    ft_per_in = s.get("scale_ft_per_in")
    warnings = []
    if not ft_per_in:
        a = project.load_analysis(display)
        d = a and a.get("scale_default")
        if d:
            ft_per_in = d["ft_per_in"]
            warnings.append(f"Scale not confirmed: using the printed {d['text']!r}.")
        else:
            return {"error": "No scale. Type the sheet scale (feet per inch) or confirm a printed one."}
    for _k in active:
        if project.review_stale(_k):
            # the sheet and the button named the way the screen names them (check 5A: "page 1" for
            # sheet 3.1, and "(Analyze)" for the Sheets card's "Update"); a read already running
            # (the host's open starts one) names no button: the operator only waits for it
            _nm = _sheet_words(project, _k)
            if (project.jobs.get(_k) or {}).get("status") in ("running", "queued"):
                return {"error": f"{_nm[:1].upper() + _nm[1:]} is being read again with the lines erased or named in "
                                 f"review: compute once it is read.", "warnings": warnings}
            return {"error": f"Lines were erased or named in review after {_nm} was read here: press Update on it "
                             f"in the Sheets card, then compute again.", "warnings": warnings}
    _sheets = []
    for _k in active:
        _a = project.load_analysis(_k)
        _d = (_a or {}).get("scale_default") or {}
        _sheets.append((_k, _sheet_words(project, _k), _d.get("ft_per_in"), _d.get("text") or ""))
    _err, _w = scale_check(ft_per_in, s.get("scale_source"), s.get("scale_page"), _sheets)
    if _err:
        return {"error": _err, "warnings": warnings}
    warnings += _w
    ft_per_pt = ft_per_in / 72.0
    rules = {**DEFAULT_RULES, **(s.get("rules") or {})}
    wall_d = float(rules.get("wall_footing_depth_ft") or 0.0)

    evidence: list[Evidence] = []
    eg_pts: list[Evidence] = []
    fg_pts: list[Evidence] = []
    transforms = {}
    sources_used = []
    pads_used: list[dict] = []
    for key in active:
        an = project.load_analysis(key)
        if not an:
            # the sheet by its name, never the engine's key ("Sheet d0p0 is not analysed yet": check 5A)
            _nm = _sheet_words(project, key)
            warnings.append(f"{_nm[:1].upper() + _nm[1:]} is not read yet (Analyze, in the Sheets card): it is left out.")
            continue
        role = roles[key]
        tr = page_transform(project, key, display)
        transforms[key] = tr
        if tr.get("source") == "unaligned":
            # no matching points either way and nothing that proves one frame: stacking it unmoved put subdivision A 3.0's
            # contours on 3.1 (critic R1). Left out and said, like a sheet not read yet
            _nm = _sheet_words(project, key)
            warnings.append(f"{_nm[:1].upper() + _nm[1:]} is not lined up with the sheet shown ({tr.get('note') or 'no matching points'}): "
                            f"it is left out. Pick two matching points in Sheet alignment.")
            continue
        if tr.get("source") == "typed" and (abs(tr.get("rotation_deg") or 0.0) > 2.0 or abs((tr.get("scale") or 1.0) - 1.0) > 0.02):
            # sheets of one set only slide: a turn or a size change means the two pairs were
            # clicked in a different order or on different landmarks (a job's 3.1 sat turned 177 degrees)
            _nm = _sheet_words(project, key)
            warnings.append(f"{_nm[:1].upper() + _nm[1:]} is turned {abs(tr.get('rotation_deg') or 0.0):.0f}° and sized "
                            f"{(tr.get('scale') or 1.0):.3f}x by its two matching points, but sheets from one set only slide: "
                            f"check them (Sheet alignment: Reset, then the same two landmarks in the same order on both sheets).")
        M = np.array(tr["matrix"], dtype=float)
        ps = project.page_settings(key)
        fam_roles = {}
        for f in an["families"]:
            role_f = ps.get("style_roles", {}).get(f.get("sig"), ps.get("family_roles", {}).get(str(f["style"]), f["role"]))
            for st_ in f.get("styles") or [f["style"]]:
                fam_roles[st_] = role_f
        cmap = {c["id"]: np.array(c["pts"], dtype=float) for c in an["chains"]}
        want_existing = role in ("existing", "both", "grading")
        want_proposed = role in ("proposed", "both", "grading")
        n_e = n_p = 0
        # operator edits: deleted pieces vanish, edited pieces are replaced, drawn lines are added
        edits = ps.get("edits") or {}
        hidden = set(int(x) for x in (edits.get("deleted") or []))
        user_lines = edits.get("lines") or {}
        for ln in user_lines.values():
            if ln.get("chain") is not None:
                hidden.add(int(ln["chain"]))
        for ct in an["contours"]:
            if ct.get("user"):
                continue            # drawn / edited lines are taken from the edits below (always current)
            # a piece under 60 pt is junk when the engine heighted it; when the operator typed the
            # height it is a contour he read ("if I typed them then they are contours"), whatever its
            # length, like his drawn lines
            if ct["z"] is None or ct.get("how") == "legend" or (ct.get("length", 0) < 60 and ct.get("how") not in OPERATOR_HOWS):
                continue
            frole = fam_roles.get(ct["style"], "ignore")
            if frole == "existing" and not want_existing:
                continue
            if frole == "proposed" and not want_proposed:
                continue
            if frole not in ("existing", "proposed"):
                continue
            for cid in ct["chain_ids"]:
                if cid in hidden:
                    continue
                pts = cmap.get(cid)
                if pts is None or len(pts) < 2:
                    continue
                cp = contour_points(pts, ct["z"], step=6.0)
                xy = apply_affine(M, cp[:, :2])
                for (x, y), z in zip(xy, cp[:, 2]):
                    ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(z), kind="contour", page=key,
                                  ref={"contour": ct["id"], "chain": cid, "labels": ct["label_ids"], "how": ct["how"],
                                       "basis": ct.get("basis", [])[:2]}, surface=frole)
                    evidence.append(ev)
                    (eg_pts if frole == "existing" else fg_pts).append(ev)
                    if frole == "existing":
                        n_e += 1
                    else:
                        n_p += 1
        for lid, ln in user_lines.items():
            z = ln.get("z")
            lrole = ln.get("role")
            pts = ln.get("pts") or []
            if z is None or lrole not in ("existing", "proposed") or len(pts) < 2:
                continue
            if (lrole == "existing" and not want_existing) or (lrole == "proposed" and not want_proposed):
                continue
            cp = contour_points(np.array(pts, dtype=float), float(z), step=6.0)
            xy = apply_affine(M, cp[:, :2])
            for (x, y), zz in zip(xy, cp[:, 2]):
                ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(zz), kind="contour", page=key,
                              ref={"user_line": lid, "how": ln.get("source", "drawn"), "chain": ln.get("chain"),
                                   "labels": [], "basis": []}, surface=lrole)
                evidence.append(ev)
                (eg_pts if lrole == "existing" else fg_pts).append(ev)
                if lrole == "existing":
                    n_e += 1
                else:
                    n_p += 1
        untagged_default = "existing" if role == "existing" else rules.get("untagged_spots", "proposed")
        for sp in an["spots"]:
            side = sp["side"]
            if side == "default":
                side = untagged_default
            if side == "outside":
                continue
            x, y = apply_affine(M, np.array([sp["x"], sp["y"]]))
            if side == "existing" and want_existing:
                ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(sp["z"]), kind="spot", page=key,
                              ref={"spot": sp["id"], "label": sp["label_id"], "tag": sp["tag"]}, surface="existing")
                evidence.append(ev); eg_pts.append(ev); n_e += 1
            elif side == "proposed" and want_proposed:
                ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(sp["z"]), kind="spot", page=key,
                              ref={"spot": sp["id"], "label": sp["label_id"], "tag": sp["tag"]}, surface="proposed")
                evidence.append(ev); fg_pts.append(ev); n_p += 1
            elif side == "wall" and want_proposed:
                tag = (sp["tag"] or "").upper()
                if tag in ("BW", "BOW", "B/W") and wall_d > 0:
                    ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(sp["z"]) - wall_d, kind="derived",
                                  page=key, ref={"spot": sp["id"], "label": sp["label_id"], "tag": tag,
                                                 "formula": f"{tag} - {wall_d:g} ft",
                                                 "inputs": [{"label": sp["label_id"], "page": key, "text": f"{tag} {sp['z']:g}"},
                                                            {"rule": "wall_footing_depth_ft", "value": wall_d,
                                                             "text": f"-{wall_d:g} ft (wall footing depth)"}]},
                                  surface="proposed")
                else:
                    ev = Evidence(id=len(evidence), x=float(x), y=float(y), z=float(sp["z"]), kind="wall", page=key,
                                  ref={"spot": sp["id"], "label": sp["label_id"], "tag": tag}, surface="proposed")
                evidence.append(ev); fg_pts.append(ev); n_p += 1
        # the building pads the operator accepted on this sheet (none = nothing changes)
        found, lost = pads_in_force(an, ps)
        for pad, _rk, rec in found:
            if not rec.get("use") or not want_proposed:
                continue
            _nm = _sheet_words(project, key)
            if not pad.get("polygon"):
                warnings.append(f"building pad {pad['text']!r} on {_nm}: no closed outline round its floor height "
                                f"on this read: it is not used")
                continue
            was = rec.get("area_pt2")
            if was and pad.get("area_pt2") and abs(pad["area_pt2"] - float(was)) > 0.02 * float(was):
                warnings.append(f"building pad {pad['text']!r} on {_nm}: its outline reads "
                                f"{pad['area_pt2'] * ft_per_pt * ft_per_pt:,.0f} SF now, "
                                f"{float(was) * ft_per_pt * ft_per_pt:,.0f} SF when you turned it on: check it")
            pads_used.append({"key": pad["key"], "label_id": pad["label_id"], "text": pad["text"], "z": float(pad["z"]),
                              "page": key, "polygon": apply_affine(M, np.array(pad["polygon"], dtype=float)).tolist()})
        for _rk, rec in lost:
            if rec.get("use") and want_proposed:
                warnings.append(f"a building pad you turned on ({rec.get('text') or 'floor height'}) is not printed there "
                                f"on this read of {_sheet_words(project, key)}: it is not used")
        sources_used.append({"page": key, "role": role, "existing_points": n_e, "proposed_points": n_p,
                             "transform": tr.get("source"), "sheet": an.get("sheet", "")})

    if pads_used:
        _use_pads(pads_used, evidence, fg_pts, warnings, ft_per_pt)
        for src in sources_used:
            src["proposed_points"] = sum(1 for e in fg_pts if e.page == src["page"])

    # sanity: a height hundreds of feet from the rest of its surface is a misread, not the site
    for name, pts in (("existing", eg_pts), ("proposed", fg_pts)):
        if len(pts) >= 5:
            zs = np.array([e.z for e in pts])
            med = float(np.median(zs))
            mad = float(np.median(np.abs(zs - med)))
            tol = max(8.0 * mad, 60.0)
            bad = [e for e in pts if abs(e.z - med) > tol]
            if bad:
                warnings.append(f"{name}: {len(bad)} point(s) with heights far from the rest were left out "
                                f"(e.g. {bad[0].z:g} ft vs typical {med:g} ft).")
                keep = {id(e) for e in pts if abs(e.z - med) <= tol}
                pts[:] = [e for e in pts if id(e) in keep]
    if len(eg_pts) < 3 or len(fg_pts) < 3:
        return {"error": "Not enough evidence: need contours or spot grades for both existing and proposed.",
                "sources": sources_used, "warnings": warnings}
    # sanity: both surfaces must be in the same datum. Abbreviated heights on one side (91.70 for
    # 5,991.70) and full heights on the other put them thousands of feet apart; that is a misread,
    # not a site, and the volume it would give is nonsense (23 million CY on a 2.5-acre pad).
    med_e = float(np.median([e.z for e in eg_pts])); med_p = float(np.median([e.z for e in fg_pts]))
    if abs(med_e - med_p) > 300.0:
        return {"error": f"The existing surface reads about {med_e:,.0f} ft and the proposed about {med_p:,.0f} ft. "
                         f"They cannot be {abs(med_e - med_p):,.0f} ft apart: one side is in a different datum "
                         f"(abbreviated heights such as 91.70 for 5,991.70). Set the datum in the Rules card, "
                         f"for example 100 = 6000, or type the heights, then compute again.",
                "sources": sources_used, "warnings": warnings}
    existing = Surface("existing", eg_pts)
    proposed = Surface("proposed", fg_pts)
    proof = _proof(existing, proposed, eg_pts, fg_pts, warnings)
    if not existing.ok or not proposed.ok:
        return {"error": "Could not triangulate one of the surfaces (points may be collinear).",
                "sources": sources_used, "warnings": warnings}
    return {"error": None, "existing": existing, "proposed": proposed, "proof": proof, "display": display,
            "ft_per_in": ft_per_in, "ft_per_pt": ft_per_pt, "rules": rules, "wall_d": wall_d,
            "evidence": evidence, "sources": sources_used, "transforms": transforms, "warnings": warnings}


def grade_at(project: Project, page: str, pts, surface: str = "existing") -> dict:
    """The height of one surface under points given on `page` (page points): the points are mapped
    into the display sheet's frame through the sheet alignment and the surface is sampled there.
    Heights are None where the surface does not reach. The story per point: which sheet the surface
    was shown on and how this sheet was mapped onto it."""
    S = surfaces_for(project)
    if S.get("error"):
        return {"error": S["error"], "warnings": S.get("warnings", [])}
    surf = S["existing"] if surface != "proposed" else S["proposed"]
    P = np.asarray([[float(p[0]), float(p[1])] for p in (pts or [])], dtype=float).reshape(-1, 2)
    if not len(P):
        return {"z": [], "display_page": S["display"], "transform": "self"}
    tr = page_transform(project, page, S["display"]) if page != S["display"] else {"matrix": identity(), "source": "self"}
    if tr.get("source") == "unaligned":
        # this sheet cannot be placed on the one the surface was built on: no ground is read under it
        return {"z": [None] * len(P), "display_page": S["display"], "transform": "unaligned",
                "transform_note": tr.get("note", ""), "surface": None, "n_found": 0, "n": int(len(P))}
    xy = apply_affine(np.array(tr["matrix"], dtype=float), P)
    z = surf.sample(xy[:, 0], xy[:, 1])
    return {"z": [None if not np.isfinite(v) else round(float(v), 3) for v in z], "display_page": S["display"],
            "transform": tr.get("source", "?"), "transform_note": tr.get("note", ""), "surface": surf.name,
            "n_found": int(np.isfinite(z).sum()), "n": int(len(z))}


def _zone_geoms(zones: list) -> tuple[list, list]:
    """The grading zones as polygons, in the order they were drawn: [(zone, geom)], and the zones
    that are no polygon: [(zone, why)]. A zone drawn crossing itself is repaired (its polygonal part,
    the rule the limit follows) and says so: [(zone, geom, repaired)]."""
    ok, bad = [], []
    for z in zones or []:
        poly = z.get("polygon") or []
        if len(poly) < 3:
            bad.append((z, "a zone needs at least 3 corners"))
            continue
        repaired = False
        try:
            g_ = Polygon(poly)
            if not g_.is_valid:
                fixed = shapely.make_valid(g_)
                polys = [p for p in getattr(fixed, "geoms", [fixed]) if p.geom_type in ("Polygon", "MultiPolygon") and p.area > 0]
                g_ = shapely.union_all(polys) if polys else g_.buffer(0)
                repaired = True
        except Exception as e:      # noqa: BLE001 - a bad zone must not kill the whole compute
            bad.append((z, f"could not read the zone: {e}"))
            continue
        if g_.is_empty or g_.area <= 0:
            bad.append((z, "the zone has no area"))
            continue
        ok.append((z, g_, repaired))
    return ok, bad


def _zone_name(z: dict, i: int | None = None) -> str:
    return str(z.get("name") or "").strip() or f"zone {z.get('id', (i or 0) + 1)}"


def zone_section(z: dict, stack_ft: float) -> float:
    """A zone's depth under finished grade: its own when one was typed (0 = finished grade), else the
    limit's layer stack (a zone left blank digs like the ground around it)."""
    v = z.get("section_ft")
    return float(stack_ft or 0.0) if v is None or v == "" else float(v)


ZONE_SLIVER_FT = 2.0         # a strip two zones share that is narrower than this is tracing noise
ZONE_OVERLAP_MIN_SF = 100.0  # what two zones share WIDER than that strip, from this much up (10 x 10 ft), is a real overlap


def _zone_overlaps(zones_in: list, ft_per_pt: float, stack_ft: float = 0.0) -> tuple[list, list, float, float]:
    """How the zones overlap: (real overlaps [(name, name, SF, kind)], zones inside another [(inner, outer)],
    SF of slivers along shared edges, the CY those slivers decide). kind "overlap", or "twice" for one
    area drawn twice. A zone (almost) wholly inside another - a pad in a lot - is not an overlap: the
    smaller zone is dug to its own depth there. A strip under ZONE_SLIVER_FT wide along an edge (hand
    tracing) is not one either: it counts once, in the smaller zone, and the note says how many yards
    that choice moves (SF x the two depths' difference / 27, an upper bound). Anything else is: the
    zones are then not itemized. The strip is judged by its WIDTH, never by a share of the area (a
    5 x 520 ft band once passed as 2 % of two big zones) nor by an average width (a 20 x 20 ft corner
    joined to a 0.5 ft strip along the edge averages 1.7 ft wide): what survives shrinking the shared
    ground by half the strip width and growing it back is wider than a strip, and from
    ZONE_OVERLAP_MIN_SF up it is a real overlap (hand-traced edges crossing leave none on subdivision A).
    Mitre joins keep a square's corners exactly; on triangular teeth just over the strip width they
    keep more than round joins would, so there the rule refuses a little earlier (CRITIC-3 P2-3)."""
    sf = ft_per_pt * ft_per_pt
    half = ZONE_SLIVER_FT / 2.0 / max(ft_per_pt, 1e-9)
    real, inside, sliver, sliver_cy = [], [], 0.0, 0.0
    for i in range(len(zones_in)):
        for j in range(i + 1, len(zones_in)):
            (za, ga, _), (zb, gb, _) = zones_in[i], zones_in[j]
            if not ga.intersects(gb):
                continue
            try:
                ov = polygonal(ga.intersection(gb))
            except Exception:      # noqa: BLE001
                continue
            if ov is None or ov.area <= 0:
                continue
            small, big = (i, j) if ga.area <= gb.area else (j, i)
            if ov.area >= 0.98 * min(ga.area, gb.area):
                # one zone (almost) wholly inside the other: a pad in a lot - unless the two are nearly
                # the same outline (a zone traced twice), where "the smaller wins" would hand one of
                # them the whole area without a word
                if min(ga.area, gb.area) < 0.9 * max(ga.area, gb.area):
                    inside.append((_zone_name(zones_in[small][0], small), _zone_name(zones_in[big][0], big)))
                else:
                    real.append((_zone_name(zones_in[small][0], small), _zone_name(zones_in[big][0], big), ov.area * sf, "twice",
                                 min(ga.area, gb.area) / max(ga.area, gb.area)))
                continue
            # the shared ground wider than a strip, piece by piece (a real 20 ft corner overlap once
            # passed as a "sliver" because tracing along the rest of the edge made it thin on average)
            wide = 0.0
            with np.errstate(all="ignore"):      # a lens a hair over the strip width warns inside GEOS
                for part in shapely.get_parts(ov):
                    if part.geom_type != "Polygon" or part.area <= 0:
                        continue
                    try:
                        wide += part.buffer(-half, join_style="mitre").buffer(half, join_style="mitre").intersection(part).area
                    except Exception:      # noqa: BLE001 - a piece that cannot be judged is not waved through
                        wide += part.area
            if wide * sf >= ZONE_OVERLAP_MIN_SF:
                real.append((_zone_name(za, i), _zone_name(zb, j), ov.area * sf, "overlap", None))
            else:
                sliver += ov.area * sf
                sliver_cy += ov.area * sf * abs(zone_section(za, stack_ft) - zone_section(zb, stack_ft)) / 27.0
    return real, inside, sliver, sliver_cy


def _itemized_zone_rows(zones_in: list, zone_bad: list, g, stack_ft: float) -> list[dict]:
    """The zones' rows from the limit's own grid (cut_fill_grid(zones=...)): they add up to it."""
    out = []
    for i, ((z, _, repaired), part) in enumerate(zip(zones_in, g.parts or [])):
        out.append({"id": z.get("id", i + 1), "name": _zone_name(z, i), "section_ft": zone_section(z, stack_ft),
                    "section_typed": z.get("section_ft") not in (None, ""),
                    "cut_cy": round(part["cut_cy"], 1) + 0.0, "fill_cy": round(part["fill_cy"], 1) + 0.0,
                    "net_cy": round(part["fill_cy"] - part["cut_cy"], 1) + 0.0, "area_sf": round(part["area_sf"], 0),
                    "coverage": round(part["coverage"], 4), "cells": part["cells"], "repaired": repaired, "itemized": True})
    for z, why in zone_bad:
        out.append({"id": z.get("id"), "name": _zone_name(z), "section_ft": zone_section(z, stack_ft), "error": why})
    return out


def _zone_rows(existing: Surface, proposed: Surface, zones: list, cell_ft: float, ft_per_pt: float, stack_ft: float = 0.0) -> list[dict]:
    """Grading zones: the same integrator as the limit, one polygon at a time, each with its own
    section (the pavement stack under a street, 0 under lots), so a sheet splits the way a bid form
    does (ROW / lots / drainage). Zones are the operator's polygons; nothing is inferred."""
    out = []
    for i, z in enumerate(zones or []):
        poly = z.get("polygon") or []
        row = {"id": z.get("id", i + 1), "name": z.get("name") or f"zone {i + 1}", "section_ft": zone_section(z, stack_ft)}
        if len(poly) < 3:
            out.append(dict(row, error="a zone needs at least 3 corners"))
            continue
        try:
            g = cut_fill_grid(existing, proposed, Polygon(poly), cell_ft, ft_per_pt, section_ft=row["section_ft"])
        except Exception as e:      # noqa: BLE001 - a bad polygon must not kill the whole compute
            out.append(dict(row, error=f"could not compute: {e}"))
            continue
        out.append(dict(row, cut_cy=round(g.cut_cy, 1) + 0.0, fill_cy=round(g.fill_cy, 1) + 0.0, net_cy=round(g.fill_cy - g.cut_cy, 1) + 0.0,
                        area_sf=round(g.area_sf, 0), coverage=round(g.coverage, 4), cells=g.n_both, repaired=g.repaired))
    return out


def limit_in_force(project: Project, region_pts, region_src: str, ft_per_pt: float) -> dict | None:
    """The limit's own numbers (area, perimeter, where it came from) for the bid rows."""
    if not region_pts or len(region_pts) < 3:
        return None
    from .takeoff import ring_metrics
    m = ring_metrics(region_pts, ft_per_pt)
    if not m:
        return None
    meta = project.data["settings"].get("limit") or {}
    src = meta.get("source") or ("drawn" if region_src == "drawn" else region_src)
    return dict(m, source=src, family=meta.get("family"), page=meta.get("page"), n_lines=meta.get("n_lines"),
                joins=meta.get("joins"), leftover=meta.get("leftover"))


def _scale_in_force(project: Project, key: str | None) -> tuple[float | None, str]:
    """(ft per pt, where it came from): the confirmed scale, else the sheet's printed default - but NOTHING on a sheet
    whose review's scale readings disagree with no person's record (scale verdict S4): no fallback to this reader's
    own caption there, the words say why."""
    from .placement import review_conflict
    _cw = review_conflict(project, key)
    if _cw:
        return None, _cw
    s = project.data["settings"]
    if s.get("scale_ft_per_in"):
        return float(s["scale_ft_per_in"]) / 72.0, "confirmed"
    a = project.load_analysis(key) if key else None
    d = a and a.get("scale_default")
    if d and d.get("ft_per_in"):
        return float(d["ft_per_in"]) / 72.0, f"printed {d.get('text', '')!r} (unconfirmed)"
    return None, "none"


def _display_key(project: Project) -> str | None:
    s = project.data["settings"]
    display = s.get("display_page")
    roles = {k: v.get("role", "none") for k, v in project.data["pages"].items()}
    if display and roles.get(display, "none") != "none":
        return display
    for k, r in roles.items():
        if r in ("existing", "proposed", "both", "grading"):
            return k
    return display


def _ink_lines(project: Project, key: str) -> list[dict]:
    """The sheet's own ink, stroke by stroke with its CAD layer, from the cached reading: a limit
    drawn as a patterned line is thousands of short dashes this engine's chaining keeps as dots, and
    the product's detection may not exist for the sheet. Only strokes on a named layer (the family
    a click stitches). Cached per sheet, whatever reader stamped the reading (only the document
    must match)."""
    cache = getattr(project, "_ink_cache", None)
    if cache is None:
        cache = project._ink_cache = {}
    if key in cache:
        return cache[key]
    out: list[dict] = []
    try:
        di, pi = _parse_key(key)
        sha = project.data["docs"][di].get("sha", "")
        rd = None
        rp = project.reading_path(key)
        if os.path.exists(rp):
            with open(rp, "rb") as f:
                rec = pickle.load(f)
            if (rec.get("meta") or {}).get("sha") == sha:
                rd = rec.get("data")
        for st in (rd or {}).get("strokes") or []:
            lay = str(getattr(st, "layer", "") or "")
            if not lay:
                continue
            pts = np.asarray(st.pts, dtype=float)
            if len(pts) < 2:
                continue
            L = float(np.sqrt((np.diff(pts, axis=0) ** 2).sum(axis=1)).sum())
            if L < 0.5:
                continue
            out.append({"id": f"ink:{st.idx}", "src": "ink", "name": lay, "fam": lay, "pts": pts, "length": L})
    except Exception:      # noqa: BLE001 - no reading, or an older one: nothing to add
        out = []
    cache[key] = out
    return out


def _lines_for(project: Project, key: str) -> list[dict]:
    from .takeoff import page_lines
    return page_lines(project.load_analysis(key), project.core_result(key)) + _ink_lines(project, key)


def _seg_index(project: Project, key: str, lines: list[dict]):
    """The click index for a sheet's lines, cached with the lines it was built from."""
    from .takeoff import SegIndex
    cache = getattr(project, "_seg_cache", None)
    if cache is None:
        cache = project._seg_cache = {}
    ent = cache.get(key)
    if ent is None or ent[0] != len(lines) or ent[1] is not lines[0] if lines else True:
        ent = (len(lines), lines[0] if lines else None, SegIndex(lines))
        cache[key] = ent
    return ent[2]


def limit_candidates_for(project: Project, key: str) -> dict:
    """The families on a sheet that name the limit line, and the erosion-control ones, with their
    lengths - the one-click offer in the Limits card. Works from the product's detection when the
    sheet has one (its chains carry the layer), else from this reader's analysis."""
    from .takeoff import erosion_families, limit_candidates
    lines = _lines_for(project, key)
    fpp, src = _scale_in_force(project, key)
    lim = limit_candidates(lines)
    ero = erosion_families(lines)
    for d in lim + ero:
        d.pop("ids", None)
        d["length_ft"] = round(d["length_pt"] * fpp, 1) if fpp else None
    has_core = project.core_result(key) is not None
    has_an = project.load_analysis(key) is not None
    return {"page": key, "limit": lim, "erosion": ero, "scale": src,
            "source": "core" if has_core else ("engine" if has_an else "none"), "n_lines": len(lines)}


def pick_limit(project: Project, key: str, x=None, y=None, family=None, src=None, tol=None) -> dict:
    """The limit from the sheet's own line. A click (x, y) takes the nearest line within `tol`
    page points; a family name takes its longest line as the seed. The seed's family is stitched
    end to end (takeoff.stitch_ring) and the ring becomes the limit, with the record of how."""
    from .takeoff import lines_of_family, ring_metrics, stitch_ring, stitch_status
    if not key or not project.page_settings(key):
        raise ValueError("no such sheet")
    lines = _lines_for(project, key)
    if not lines:
        raise ValueError("this sheet has no lines yet: detect it in the product or read it here first")
    seed = None
    if family:
        fam = (lines_of_family(lines, src or "core", family) or lines_of_family(lines, "engine", family)
               or lines_of_family(lines, "ink", family))
        if not fam:
            raise ValueError(f"no lines named {family!r} on this sheet")
        seed = max(fam, key=lambda ln: ln["length"])
    else:
        if x is None or y is None:
            raise ValueError("click a line on the sheet, or name a family")
        seed = _seg_index(project, key, lines).nearest(float(x), float(y), float(tol or 8.0))
        if seed is None:
            raise ValueError("no line there: zoom in and click on the line itself")
    st = stitch_ring(lines, seed["id"])
    if len(st["pts"]) < 3:
        raise ValueError("that line does not enclose an area")
    fpp, _ = _scale_in_force(project, key)
    m = ring_metrics(st["pts"], fpp) if fpp else None
    s = project.data["settings"]
    with project._lock:
        s["region"] = st["pts"]
        s["display_page"] = key
        s["limit"] = {"source": "picked", "family": seed["name"] or seed["fam"], "src": seed["src"], "page": key,
                      "seed": seed["id"], "n_lines": st["n"], "joins": st["joins"][:50], "leftover": st["leftover"],
                      "gap_pt": st["gap"], "picked_at": time.time()}
        project.save()
    return {"limit": s["limit"], "region": st["pts"], "metrics": m, "status": stitch_status(st, fpp), "n_points": len(st["pts"])}


def takeoff_preview(project: Project) -> dict:
    """The bid rows as they stand without a compute: the limit's rows and the strip need only the
    limit and the scale; the cut / fill rows come from the last computed result when it exists."""
    from .takeoff import erosion_families, takeoff_rows
    s = project.data["settings"]
    key = _display_key(project)
    fpp, scale_src = _scale_in_force(project, key)
    if not fpp:
        # the readings-disagree words when that is why (scale verdict S4), else the no-scale words as before
        _why = scale_src if str(scale_src).startswith("This sheet's scale readings disagree") \
            else "No scale: type the sheet scale or confirm a printed one."
        return {"rows": [], "notes": [_why], "scale": scale_src, "page": key}
    erosion = []
    if key:
        try:
            erosion = erosion_families(_lines_for(project, key))
            for f in erosion:
                f["length_ft"] = round(f["length_pt"] * fpp, 1)
        except Exception:      # noqa: BLE001
            erosion = []
    limit = limit_in_force(project, s.get("region"), "drawn", fpp)
    totals = None
    res = None
    rp = os.path.join(project.dir, "result.json")
    if os.path.exists(rp):
        try:
            res = json.load(open(rp, encoding="utf-8"))
            if res.get("error"):
                res = None
            else:
                totals = res.get("totals")
        except (OSError, ValueError):
            res = None
    out = takeoff_rows(totals, limit, erosion, s.get("takeoff"), fpp, parts=_result_parts(res))
    if res:
        # the rows compute derives from its own result (walls, zones) ride along unchanged
        _append_result_rows(out, res.get("walls"), res.get("zones"), rest=res.get("zones_rest"), itemized=bool(res.get("zones_itemized")))
    out["page"] = key
    out["scale"] = scale_src
    out["computed"] = totals is not None
    return out


def _result_parts(res: dict | None) -> list | None:
    """The itemized zones of a stored result (and the rest of its limit) as takeoff parts."""
    if not res or not res.get("zones_itemized"):
        return None
    parts = [{"name": z.get("name") or "zone", "section_ft": z.get("section_ft") or 0.0, "cut_cy": z["cut_cy"], "fill_cy": z["fill_cy"],
              "area_sf": z.get("area_sf")} for z in (res.get("zones") or []) if "cut_cy" in z]
    if res.get("zones_rest"):
        parts.append(dict(res["zones_rest"]))
    return parts or None


def _outline(geom) -> list:
    """The outline the screen and the crew map draw for the area computed: the polygon's ring, the
    largest part's when the area is several zones (the zones themselves are drawn one by one)."""
    polys = [geom] if geom.geom_type == "Polygon" else [p for p in getattr(geom, "geoms", []) if p.geom_type == "Polygon"]
    if not polys:
        return []
    return list(map(list, max(polys, key=lambda p: p.area).exterior.coords))


def _append_result_rows(takeoff: dict, walls: dict | None, zones: list | None, rest: dict | None = None, itemized: bool = False) -> None:
    """The wall face row and the zones' total, from a compute's own walls / zones. Itemized zones
    (and the rest of a drawn limit) ARE the totals: the rows file them one by one."""
    if walls and walls.get("face_sf"):
        n_len = sum(1 for w in walls["walls"] if w.get("face_sf") is not None)
        takeoff["rows"].append({"label": "Site walls face", "qty": round(walls["face_sf"], 1), "unit": "SF",
                                "basis": f"{n_len} wall{'s' if n_len != 1 else ''}, {walls['lf']:,.0f} ft x (TW - BW + {walls['add_ft']:g} ft)"
                                         + (f"; {walls['n_no_length']} more with no length yet" if walls.get("n_no_length") else ""),
                                "kind": "walls"})
    if zones:
        zt = [z for z in zones if "cut_cy" in z]
        takeoff["zones_total"] = {"cut_cy": round(sum(z["cut_cy"] for z in zt), 1), "fill_cy": round(sum(z["fill_cy"] for z in zt), 1),
                                  "area_sf": round(sum(z["area_sf"] for z in zt), 0), "n": len(zt)}
        if itemized:
            takeoff["zones_total"]["itemized"] = True
            if rest:
                takeoff["zones_total"]["rest"] = {k: rest.get(k) for k in ("cut_cy", "fill_cy", "area_sf", "section_ft")}


def walls_for(project: Project, key: str) -> dict:
    """The site walls on a sheet, with dig depths when the surfaces can be built."""
    from .walls import wall_rows
    an = project.load_analysis(key)
    if not an:
        return {"walls": [], "incomplete": [], "lf": 0.0, "face_sf": 0.0, "note": "read the sheet first"}
    fpp, _ = _scale_in_force(project, key)
    if not fpp:
        return {"walls": [], "incomplete": [], "lf": 0.0, "face_sf": 0.0, "note": "no scale: type the sheet scale first"}
    rules = {**DEFAULT_RULES, **(project.data["settings"].get("rules") or {})}
    S = surfaces_for(project)
    existing, M = None, None
    if not S.get("error"):
        existing = S["existing"]
        tr = page_transform(project, key, S["display"]) if key != S["display"] else {"matrix": identity()}
        M = tr["matrix"]
        if tr.get("source") == "unaligned":
            existing, M = None, None       # not lined up with the sheet the surface was built on: no ground under the walls
    out = wall_rows(an, fpp, add_ft=float(rules.get("wall_add_ft", 1.0) or 0.0), existing=existing, M=M,
                    wall_d=float(rules.get("wall_footing_depth_ft") or 0.0), wall_lengths=rules.get("wall_lengths"))
    out["page"] = key
    out["dig_from"] = "the existing surface (compute's)" if existing is not None else ("no existing surface yet: " + str(S.get("error") or ""))
    return out


def crew_map(project: Project, res: dict, block_ft: float = 30.0) -> bytes:
    """The printable crew map for a computed result (takeoff.crew_map_png)."""
    from .takeoff import crew_map_png
    key = res["display_page"]
    di, pi = _parse_key(key)
    doc = project.doc(di)

    def render_clip(x0, y0, x1, y1, dpi):
        return doc.render(pi, dpi=dpi, clip=(x0, y0, x1, y1))
    name = project.data.get("name") or ""
    # only the zones of the sheet the map is drawn on (a zone of another sheet is in that sheet's frame)
    disp = res.get("display_page")
    zs = [z for z in (project.data["settings"].get("zones") or []) if z.get("page") in (None, "", disp)]
    return crew_map_png(render_clip, res, block_ft=block_ft, zones=zs,
                        title=f"Cut / fill map, {name[:50]}" if name else "Cut / fill map",
                        scale_text=f"1\" = {scale_txt(res.get('ft_per_in'))} ft")


def scale_txt(v) -> str:
    """A scale for a sentence: two decimals at most (a bar measured at 40.016655966932305 ft per
    inch prints as 40.02). Only the print is rounded, never the number in force."""
    try:
        return f"{float(v):.2f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "?"


def _page_words(key: str) -> str:
    """The words for a sheet with no printed number: its page (d0p2 is page 3), never the
    engine's key."""
    try:
        di, pi = _parse_key(key)
    except Exception:       # noqa: BLE001
        return str(key)
    return f"page {pi + 1}" if not di else f"page {pi + 1} of document {di + 1}"


def _sheet_words(project: Project, key: str) -> str:
    """A sheet the way the engine's screen names it: its printed number ("sheet 3.1": the one read
    with the sheet, else the page's own title block, for a sheet not read yet), else its page
    ("page 1") - never the engine's key."""
    nm = ""
    try:
        nm = str((project.load_analysis(key) or {}).get("sheet") or "").strip()
        if not nm:
            di, pi = _parse_key(key)
            nm = str(((project.data["docs"][di].get("pages") or [])[pi] or {}).get("label") or "").strip()
    except Exception:       # noqa: BLE001 - no such page: the page words below
        nm = ""
    return f"sheet {nm}" if nm else _page_words(key)


def scale_check(ft_per_in: float, source: str | None, scale_page: str | None, sheets: list) -> tuple:
    """The scale in force against what each active sheet prints. `sheets` = [(key, name,
    printed ft_per_in or None, printed text)]. Volume moves with the SQUARE of the scale, so:
    a scale handed over from ANOTHER sheet, or merely read (not confirmed by anyone), that
    disagrees with a sheet's own printed scale is refused; a scale the operator confirmed or typed
    that disagrees is said, loudly. Returns (error or None, warnings)."""
    src = str(source or "")
    handed = src in ("read in review", "confirmed in review")
    warns = []
    for key, name, printed, text in sheets:
        if not printed or not ft_per_in:
            continue
        if abs(float(printed) - float(ft_per_in)) <= 0.01 * float(ft_per_in):
            continue
        label = str(name) if str(name).startswith(("sheet ", "page ")) else f"sheet {name}"
        what = (f'the scale in force is 1" = {scale_txt(ft_per_in)} ft ({src or "unconfirmed"}) but {label} '
                f'prints \u201c{str(text).strip()}\u201d (1" = {scale_txt(printed)} ft)')
        if handed and scale_page not in (None, key):
            return (what + ": it came from another sheet. Confirm this sheet's scale in the Scale card "
                    "(volumes move with the square of the scale).", warns)
        # a set scale with no source is the operator's own (the Scale card shows it as typed)
        if src == "read in review":
            return (what + ": two readings of this sheet disagree and nobody confirmed either. Confirm "
                    "the scale in the Scale card.", warns)
        warns.append(what + ": volumes move with the square of the scale - check it")
    return None, warns


def _open_region(project: Project, display: str, existing: Surface, proposed: Surface, ft_per_pt: float) -> dict:
    """The area computed when no limit is drawn: where both surfaces reach, inside the review's work
    area when one was drawn (its largest part). {"region", "source", "notes"}, or {"error", "notes"}."""
    notes: list = []
    # the area part only: two hulls that touch along a line share no ground (a LineString has no parts)
    region = polygonal(existing.hull.intersection(proposed.hull))
    src = "overlap of the two surfaces (draw a limit to change)"
    if region is None or region.is_empty or region.area <= 0:
        return {"error": "The existing and proposed evidence do not overlap on the sheet.", "notes": notes}
    # the work area drawn in review is the limit when none is drawn here (where both surfaces
    # reach); several separate parts: the largest, and the others are named
    from .core_edits import work_area
    wa = work_area(project.core_edits(display))
    if wa is not None:
        inside = region.intersection(wa)
        parts = [g_ for g_ in getattr(inside, "geoms", [inside]) if g_.geom_type == "Polygon" and g_.area > 1.0]
        if parts:
            parts.sort(key=lambda g_: -g_.area)
            region = parts[0]
            src = "the work area drawn in review, where both surfaces reach (draw a limit to change)"
            if len(parts) > 1:
                rest = sum(g_.area for g_ in parts[1:]) * ft_per_pt * ft_per_pt
                notes.append(f"the work area has {len(parts)} separate parts: the largest is the limit, "
                             f"{rest:,.0f} SF in the other{'s' if len(parts) > 2 else ''} is left out "
                             f"(draw a limit or zones to count it)")
        else:
            notes.append("the work area drawn in review does not reach the two surfaces: "
                         "the limit is their overlap")
    if region.geom_type != "Polygon":
        region = max(region.geoms, key=lambda g_: g_.area)
    return {"region": region, "source": src, "notes": notes}


def compute_cutfill(project: Project) -> dict:
    S = surfaces_for(project)
    if S.get("error"):
        return {k: v for k, v in S.items() if k in ("error", "sources", "warnings")}
    s = project.data["settings"]
    existing, proposed, proof = S["existing"], S["proposed"], S["proof"]
    display, ft_per_in, ft_per_pt, rules = S["display"], S["ft_per_in"], S["ft_per_pt"], S["rules"]
    evidence, sources_used, transforms, warnings = S["evidence"], S["sources"], S["transforms"], S["warnings"]

    region_pts = s.get("region")
    region_src = "drawn"
    # grading zones that can be itemized: each is dug to its own section on the limit's grid, so
    # the zones (and the rest of a drawn limit) add up to the totals and file as their own rows.
    # A zone lives in the frame of the sheet it was drawn on: only the zones of the sheet shown are
    # in (a zone read in another sheet's frame lands somewhere else - one job turned an import of
    # 3,797 CY into an export of 6,978 when the sheet shown changed)
    all_zones = s.get("zones") or []
    here = [z for z in all_zones if z.get("page") in (None, "", display)]
    for z in all_zones:
        if z not in here:
            warnings.append(f"zone '{_zone_name(z)}' was drawn on {_sheet_words(project, z.get('page'))}, not on the sheet shown "
                            f"({_sheet_words(project, display)}): it is left out. Draw it on {_sheet_words(project, display)} (over the ghost) to count it.")
    zones_in, zone_bad = _zone_geoms(here)
    for i, (z, _, rep) in enumerate(zones_in):
        if rep:
            warnings.append(f"zone '{_zone_name(z, i)}' crosses itself; it was repaired before the volumes. Check its corners.")
    drawn_limit = bool(region_pts and len(region_pts) >= 3)
    cell_ft = float(s.get("cell_ft") or 10.0)
    section_ft = float(s.get("section_ft") or 0.0)
    real, inside_z, sliver_sf, sliver_cy = (_zone_overlaps(zones_in, ft_per_pt, section_ft) if len(zones_in) > 1
                                            else ([], [], 0.0, 0.0))
    if real:
        a, b_, sf, kind, ratio = real[0]
        more = f" (and {len(real) - 1} more pair{'s' if len(real) > 2 else ''})" if len(real) > 1 else ""
        whole = "the whole limit" if drawn_limit else "the whole area"
        if kind == "twice":
            # a zone (almost) wholly inside another that covers most of it reads as one area drawn twice:
            # "a zone drawn wholly inside another is fine" would tell the operator the refused shape is fine
            warnings.append(f"zone '{a}' covers {100 * ratio:.0f} % of zone '{b_}' ({sf:,.0f} SF): that reads as one area "
                            f"drawn twice{more}, so the zones are not itemized and the takeoff keeps one row pair for {whole}. "
                            f"Delete one of them, or redraw '{b_}' so '{a}' is clearly smaller.")
        else:
            warnings.append(f"zones '{a}' and '{b_}' overlap by {sf:,.0f} SF{more}: they are not itemized, the takeoff keeps one "
                            f"row pair for {whole}. Make the zones meet edge to edge (a small zone wholly inside a bigger "
                            f"one, like a pad in a lot, is fine).")
    itemize = bool(zones_in) and not real
    # the notes about the zones are said only once the zones were itemized (a fallback must not leave
    # "the pad is dug to its own depth" standing beside totals that did not itemize anything)
    zone_notes = []
    if itemize:
        for inner, outer in inside_z:
            zone_notes.append(f"zone '{inner}' lies inside zone '{outer}': it is dug to its own depth there, '{outer}' around it.")
        if sliver_sf >= 1.0:
            zone_notes.append(f"the zones share {sliver_sf:,.0f} SF where their edges meet (thin strips and small overlaps): "
                              f"it counts once, in the smaller zone"
                              + (f", and that choice moves up to {sliver_cy:,.0f} CY" if sliver_cy >= 0.5 else ""))
    open_reg = None
    if drawn_limit:
        region = Polygon(region_pts)
    else:
        # where both surfaces reach, inside the review's work area when one was drawn: the area with
        # no limit drawn and no zones, and what zones with no limit are measured against
        open_reg = _open_region(project, display, existing, proposed, ft_per_pt)
        if open_reg.get("error") and not itemize:
            return {"error": open_reg["error"], "sources": sources_used, "warnings": warnings}
        if itemize:
            # no limit drawn: the zones ARE the area, so the totals, the map and the rows are the zones'
            region = shapely.union_all([g_ for _, g_, _ in zones_in])
            region_src = "the grading zones (no limit drawn: the zones are the area)"
        else:
            region, region_src = open_reg["region"], open_reg["source"]
            warnings.extend(open_reg["notes"])
    zin = None
    if itemize:
        # a zone left blank digs to the limit's layer stack; the smallest zone takes a shared strip
        # or a pad inside a lot (zone_order), so the order the zones were drawn in changes nothing
        zin = [{"geom": g_, "section_ft": zone_section(z, section_ft)} for z, g_, _ in zones_in]
        zone_order = sorted(range(len(zones_in)), key=lambda k: (zones_in[k][1].area, k))
        if drawn_limit:
            lim = region if region.is_valid else (polygonal(shapely.make_valid(region)) or region.buffer(0))
            for i, (z, g_, _) in enumerate(zones_in):
                try:
                    out_sf = g_.difference(lim).area * ft_per_pt * ft_per_pt
                except Exception:      # noqa: BLE001
                    out_sf = 0.0
                if out_sf > max(1.0, 0.005 * g_.area * ft_per_pt * ft_per_pt):
                    zone_notes.append(f"zone '{_zone_name(z, i)}': {out_sf:,.0f} SF of it lies outside the limit and is not counted")
    try:
        g = cut_fill_grid(existing, proposed, region, cell_ft, ft_per_pt, section_ft=section_ft, zones=zin,
                          zone_order=zone_order if itemize else None)
    except Exception as e:      # noqa: BLE001 - the zones must never stop the limit's own volumes
        if not itemize:
            raise
        itemize = False
        zone_notes = []
        if not drawn_limit:
            if open_reg.get("error"):
                return {"error": open_reg["error"], "sources": sources_used, "warnings": warnings}
            region, region_src = open_reg["region"], open_reg["source"]
            warnings.extend(open_reg["notes"])
        warnings.append(f"the grading zones could not be itemized ({e}): the totals are "
                        f"{'the limit' if drawn_limit else 'the area'}'s, the zones are reported beside them")
        g = cut_fill_grid(existing, proposed, region, cell_ft, ft_per_pt, section_ft=section_ft)
    warnings.extend(zone_notes)
    if itemize and not drawn_limit and open_reg and not open_reg.get("error"):
        # say what the zones leave out of where the job would have been computed without them, in SF
        # and in yards (a zone drawn to check one box used to replace the whole job without a word)
        try:
            left = polygonal(open_reg["region"].difference(region))
        except Exception:      # noqa: BLE001
            left = None
        if left is not None and left.area > 0.01 * region.area:
            msg = (f"no limit drawn, so only the grading zones are computed ({region.area * ft_per_pt * ft_per_pt:,.0f} SF): "
                   f"{left.area * ft_per_pt * ft_per_pt:,.0f} SF more has both surfaces")
            try:
                gl = cut_fill_grid(existing, proposed, left, cell_ft, ft_per_pt, section_ft=section_ft)
                msg += f" (cut {gl.cut_cy:,.0f} / fill {gl.fill_cy:,.0f} CY there)"
            except Exception:      # noqa: BLE001
                pass
            warnings.append(msg + " and is left out. Draw a limit around the whole job to count it as 'rest of the limit'.")
    if section_ft and itemize:
        # what the layer stack did, from what happened: where it applied, and which zones kept their own depth
        blank = [z for z, _, _ in zones_in if z.get("section_ft") in (None, "")]
        typed = [z for z, _, _ in zones_in if z.get("section_ft") not in (None, "")]
        # a typed depth within a hundredth of an inch of the stack is the stack's (7 in typed under a 7 in layer)
        own = [z for z in typed if abs(float(z["section_ft"]) - section_ft) > 0.01 / 12.0]
        rest_there = drawn_limit and g.rest and g.rest["area_sf"] >= 1.0
        where = [w_ for w_ in (("the rest of the limit" if rest_there else ""),
                               (f"the zone{'s' if len(blank) > 1 else ''} left blank ({', '.join(_zone_name(z) for z in blank[:4])}"
                                f"{' ...' if len(blank) > 4 else ''})" if blank else "")) if w_]

        def _names(zs):
            def _depth(z):
                d = float(z["section_ft"])
                return "finished grade" if d == 0 else f"{d * 12:g} in"
            return ", ".join(f"{_zone_name(z)} ({_depth(z)})" for z in zs[:4]) + (" ..." if len(zs) > 4 else "")
        if where:
            warnings.append(f"the layer stack lowers the proposed surface {section_ft:g} ft in {' and '.join(where)}"
                            + (f"; {_names(own)} keep{'s' if len(own) == 1 else ''} {'its' if len(own) == 1 else 'their'} own depth" if own else "")
                            + ": the volumes are to subgrade")
        elif own:
            warnings.append(f"the layer stack ({section_ft:g} ft) is used nowhere: every zone has its own depth ({_names(typed)}). "
                            f"Clear a zone's depth to dig it to the stack.")
        else:
            warnings.append(f"every zone has its own depth ({_names(typed)}), the same as the layer stack ({section_ft:g} ft): "
                            f"the volumes are to subgrade")
    elif section_ft:
        warnings.append(f"the proposed surface is lowered {section_ft:g} ft inside the limit (pavement section): "
                        f"the volumes are to subgrade")
    if g.repaired:
        warnings.append("The limit crosses or touches itself; it was repaired before the volumes. Check its corners.")
    if g.unclipped:
        warnings.append(f"{g.unclipped} boundary cells could not be clipped to the limit and count by their centres.")
    # a part of the limit with no surface under it contributes nothing: say how much (one job
    # counted 114.6 of 189 SF while the host called its surfaces "supplied")
    if g.n_inside and g.coverage < 0.95:
        warnings.append(f"the surfaces cover {g.coverage * 100:.0f} % of the limit: the other {100 - g.coverage * 100:.0f} % "
                        f"has no existing or no proposed surface under it and is left out of the volumes")
    xs = g.x0 + (np.arange(g.nx) + 0.5) * g.cell_pt
    ys = g.y0 + (np.arange(g.ny) + 0.5) * g.cell_pt
    X, Y = np.meshgrid(xs, ys)
    se = existing.simplices(X, Y)
    sp_ = proposed.simplices(X, Y)
    both = g.inside & np.isfinite(g.eg) & np.isfinite(g.fg)

    def _arr(a):
        return [None if not np.isfinite(v) else round(float(v), 3) for v in a.ravel()]

    ev_json = [e.to_json() for e in evidence]
    area_sf = g.area_sf                      # boundary cells by their fraction inside the limit
    hotspots = _hotspots(g, both, se, sp_, evidence, cell_ft)
    # + 0.0: a sum over no cut cells is -0.0 and printed "Cut -0.0 CY" (check 5A); the value is the same
    totals = {"cut_cy": round(g.cut_cy, 1) + 0.0, "fill_cy": round(g.fill_cy, 1) + 0.0, "net_cy": round(g.fill_cy - g.cut_cy, 1) + 0.0,
              "area_sf": round(area_sf, 0), "area_ac": round(area_sf / 43560.0, 2), "coverage": round(g.coverage, 4),
              "cells_inside": g.n_inside, "cells_both": g.n_both,
              "max_cut_ft": round(max(0.0, float(np.nanmax(np.where(both, g.eg - g.fg, np.nan)))) if both.any() else 0.0, 2),
              "max_fill_ft": round(max(0.0, float(np.nanmax(np.where(both, g.fg - g.eg, np.nan)))) if both.any() else 0.0, 2)}
    # grading zones (each with its own section), the bid rows and the walls: all arithmetic on
    # what was just computed, every row with its basis (cutfill/takeoff.py, cutfill/walls.py).
    # Itemized zones come off the limit's own grid and add up to the totals; zones that overlap
    # for real are computed one by one and only reported beside the totals, as before
    rest = None
    if itemize:
        zones = _itemized_zone_rows(zones_in, zone_bad, g, section_ft)
        if drawn_limit and g.rest and g.rest["area_sf"] >= 1.0:
            rest = {"id": "rest", "name": "rest of the limit", "section_ft": section_ft,
                    "cut_cy": round(g.rest["cut_cy"], 1) + 0.0, "fill_cy": round(g.rest["fill_cy"], 1) + 0.0,
                    "net_cy": round(g.rest["fill_cy"] - g.rest["cut_cy"], 1) + 0.0, "area_sf": round(g.rest["area_sf"], 0),
                    "coverage": round(g.rest["coverage"], 4), "cells": g.rest["cells"]}
    else:
        zones = _zone_rows(existing, proposed, here, cell_ft, ft_per_pt, stack_ft=section_ft)
    from .takeoff import erosion_families, page_lines, takeoff_rows
    from .walls import wall_rows
    an_d = project.load_analysis(display)
    try:
        erosion = erosion_families(page_lines(an_d, project.core_result(display)))
        for f in erosion:
            f["length_ft"] = round(f["length_pt"] * ft_per_pt, 1)
    except Exception as e:      # noqa: BLE001
        erosion = []
        warnings.append(f"could not read the erosion-control lines: {e}")
    limit = limit_in_force(project, region_pts, region_src, ft_per_pt)
    walls = None
    if an_d:
        try:
            walls = wall_rows(an_d, ft_per_pt, add_ft=float(rules.get("wall_add_ft", 1.0) or 0.0), existing=existing,
                              M=identity(), wall_d=float(rules.get("wall_footing_depth_ft") or 0.0), wall_lengths=rules.get("wall_lengths"))
        except Exception as e:      # noqa: BLE001
            walls = {"walls": [], "incomplete": [], "lf": 0.0, "face_sf": 0.0, "note": f"walls not read: {e}"}
    parts = None
    if itemize:
        parts = [{"name": z["name"], "section_ft": z["section_ft"], "cut_cy": z["cut_cy"], "fill_cy": z["fill_cy"], "area_sf": z["area_sf"]}
                 for z in zones if "cut_cy" in z] + ([dict(rest)] if rest else [])
    takeoff = takeoff_rows(totals, limit, erosion, s.get("takeoff"), ft_per_pt, parts=parts)
    _append_result_rows(takeoff, walls, zones, rest=rest, itemized=itemize)
    grid = {"x0": g.x0, "y0": g.y0, "nx": g.nx, "ny": g.ny, "cell_pt": g.cell_pt,
            "eg": _arr(np.where(both, g.eg, np.nan)), "fg": _arr(np.where(both, g.fg, np.nan)),
            "inside": g.inside.ravel().astype(int).tolist(),
            # fg is the surface the volumes were taken to (finished grade less section_ft);
            # w is the part of each cell inside the limit (a cell holds depth x cell x cell x w)
            "section_ft": section_ft, "w": [round(float(v), 4) for v in g.weight.ravel()],
            "eg_tri": se.tolist(), "fg_tri": sp_.tolist()}
    if itemize and g.zone_idx is not None:
        # which zone holds most of each cell (-1 = the rest of the limit) and the section that
        # cell's fg was lowered by: the map can show one zone alone, the cell story its depth. A
        # cell split between zones holds each part at its own depth: cutc / fillc are each cell's
        # own cut / fill (area-weighted ft, every part summed), so the cell CSV adds up to the totals
        grid["zone"] = g.zone_idx.ravel().astype(int).tolist()
        grid["zsec"] = [round(float(v), 4) for v in g.sec_cell.ravel()]
        grid["zone_ids"] = [z.get("id") for z, _, _ in zones_in]
        grid["cutc"] = [round(float(v), 4) for v in g.cutc.ravel()]
        grid["fillc"] = [round(float(v), 4) for v in g.fillc.ravel()]
    return {
        "display_page": display, "ft_per_in": ft_per_in, "ft_per_pt": ft_per_pt, "cell_ft": cell_ft, "section_ft": section_ft,
        "region": _outline(region), "region_source": region_src,
        "grid": grid,
        "totals": totals,
        "zones": zones, "zones_rest": rest, "zones_itemized": itemize, "takeoff": takeoff, "walls": walls, "limit": limit,
        "evidence": ev_json,
        "hotspots": hotspots,
        "proof": proof,
        "sources": sources_used, "transforms": transforms, "warnings": warnings,
        "rules": rules, "computed_at": time.time(),
    }
