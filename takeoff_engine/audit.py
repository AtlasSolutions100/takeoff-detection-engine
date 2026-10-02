"""Audit log: which account did what, and when.

WHY A SECOND LOG. The training ledger (ledger.py) records operator decisions
as TRAINING DATA and by design carries no account names - a model must not
learn who clicked. A team needs the opposite question answered: who uploaded,
detected, approved, reset or deleted this project, and who changed whose
account. That record lives here, and the ledger is left exactly as it is.

WHAT IT NEVER HOLDS. A password (chosen or temporary), a session cookie, a
file's contents or a filesystem path. write() keeps only the seven fields
below, each a short string or an int, and truncates every string - so a
caller that hands it the wrong thing stores a fragment of one field, never a
request body.

BEST EFFORT. A write that fails costs that one line, never the request it
rides on: write() catches everything and says so once on stderr.

Layout: JOBS_DIR/audit/audit-YYYY-MM.jsonl, one file per UTC month, UTF-8
with LF line endings, one JSON object per line:

    {"ts": "2026-09-25T14:03:11Z", "user": "a@example.test" | null,
     "action": "sheet_approve", "job": "<job id>" | null,
     "name": "<document or account name>" | null, "page": 3 | null,
     "detail": "12 lines, 4 items" | null}

`user` is the ACTOR, and is null in single-password mode, where there is no
account to name. For account events `name` is the TARGET account.

READS ARE BOUNDED. Only the current and the previous month's files are read,
and only the last TAIL_BYTES of each: the team view and a project's history
are "what happened lately", and neither may cost a request more than a few
megabytes of reading however long the box has been up.
"""

from __future__ import annotations

import calendar
import json
import os
import sys
import threading
import time
from pathlib import Path

FIELDS = ("ts", "user", "action", "job", "name", "page", "detail")
READ_MAX = 500                  # the most events one read() returns
TAIL_BYTES = 8 * 1024 * 1024    # read at most this much of each month file
_CAPS = {"user": 200, "action": 40, "job": 64, "name": 200, "detail": 200}

_LOCK = threading.Lock()
_WARNED = False


def audit_dir() -> Path:
    """Beside the job store, like users.json and the job registry, so it
    survives a redeploy on the same volume and a throwaway store gets a
    throwaway log."""
    root = os.environ.get("TAKEOFF_JOBS_DIR")
    base = Path(root).expanduser() if root else Path(__file__).parent.parent / "out" / "jobs"
    return base / "audit"


def _short(v, cap: int):
    if v is None:
        return None
    s = str(v).strip()
    return s[:cap] if s else None


def _int_or_none(v):
    if v is None or isinstance(v, bool):
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def write(event: dict) -> bool:
    """Append one event. NEVER raises. Returns True when the line hit disk.

    `event` may carry any of FIELDS except `ts`, which is always the time of
    writing (UTC) unless a number - epoch seconds - is passed for it."""
    global _WARNED
    try:
        t = event.get("ts")
        t = float(t) if isinstance(t, (int, float)) and not isinstance(t, bool) else time.time()
        g = time.gmtime(t)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", g)}
        for k in ("user", "action", "job", "name"):
            rec[k] = _short(event.get(k), _CAPS[k])
        rec["page"] = _int_or_none(event.get("page"))
        rec["detail"] = _short(event.get("detail"), _CAPS["detail"])
        if not rec["action"]:
            return False
        line = json.dumps({k: rec[k] for k in FIELDS}, ensure_ascii=False)
        d = audit_dir()
        d.mkdir(parents=True, exist_ok=True)
        f = d / time.strftime("audit-%Y-%m.jsonl", g)
        with _LOCK:
            # append-binary so the line ending is LF on every platform
            with open(f, "ab") as fh:
                fh.write(line.encode("utf-8") + b"\n")
        return True
    except Exception as e:                          # noqa: BLE001 - by design
        if not _WARNED:
            _WARNED = True
            try:
                print(f"[audit] write failed ({e!r}); further failures will "
                      f"be silent. The request itself is unaffected.",
                      file=sys.stderr)
            except Exception:
                pass
        return False


def _this_and_last_month(t: time.struct_time) -> list:
    y, m = t.tm_year, t.tm_mon
    return [(y, m), (y - 1, 12) if m == 1 else (y, m - 1)]


def _tail_lines(path: Path) -> list:
    """The file's lines (bytes), oldest first - only the last TAIL_BYTES of
    it, with the partial line a mid-file seek lands in dropped."""
    try:
        size = path.stat().st_size
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
                raw = fh.read()
                raw = raw.split(b"\n", 1)[1] if b"\n" in raw else b""
            else:
                raw = fh.read()
    except OSError:
        return []
    return [ln for ln in raw.split(b"\n") if ln.strip()]


def read(limit: int = 50, job: str | None = None) -> list:
    """Newest first, at most `limit` (capped at READ_MAX) events, optionally
    only those for one job. Append order IS time order (one writer, one
    lock), so a file is walked backwards rather than sorted."""
    try:
        limit = max(1, min(int(limit), READ_MAX))
    except (TypeError, ValueError):
        limit = 50
    want = job.encode("utf-8") if job else None
    out: list = []
    for y, m in _this_and_last_month(time.gmtime()):
        for ln in reversed(_tail_lines(audit_dir() / f"audit-{y:04d}-{m:02d}.jsonl")):
            if want is not None and want not in ln:
                continue                    # cheap: most lines are other jobs
            try:
                e = json.loads(ln.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue                    # a torn line must not hide the rest
            if not isinstance(e, dict) or (job and e.get("job") != job):
                continue
            out.append({k: e.get(k) for k in FIELDS})
            if len(out) >= limit:
                return out
    return out


# ---------------------------------------------------------------------------
# A PROJECT'S HISTORY: this log's events for one job, merged with the training
# ledger's. The ledger holds the calculator and scale decisions that never
# pass through an audited endpoint (a trench drawn, a structure edited, a
# scale typed at approve, cut / fill rows put into the takeoff), so the
# history would be missing most of the work without it. It is READ here, the
# way /api/ledger/stats reads it - outside the detection and calculation path,
# so the ledger's write-only invariant for the pipeline holds - and never
# written. Ledger events carry no account name, by design, so their `user` is
# always null.

# Ledger event -> the action name the UI phrases. Anything not listed keeps
# its own name (scale_set, approve_export, cutfill_rows, trench_set,
# trench_cleared, structure_set, boundary_set, boundary_cleared,
# workzones_set, check_done already are those names).
_LEDGER_ACTION = {"structure_add": "structure_set",
                  "structure_edit": "structure_set",
                  "structure_delete": "structure_set",
                  "structure_defaults_edit": "structure_set"}

# The per-row parts of ONE approve: a label per style, a colour per chain, an
# erasure per chain (up to 2,000 of them). approve_export is that approve's
# one-line summary, so a history listing these would drown in a single click.
_LEDGER_SKIP = {"style_label", "style_color", "chain_color", "chain_relabel",
                "chain_excluded"}

# The ledger events an audited endpoint ALSO writes, keyed to that audit
# action: the same moment recorded twice. The audit line wins (it names who),
# and the ledger twin is shown only when no audit line pairs with it - i.e.
# for history from before this log existed.
_LEDGER_TWIN = {"approve_export": "sheet_approve", "demo_opened": "demo_open",
                "job_reset": "project_reset", "job_deleted": "project_delete"}
_TWIN_WINDOW_S = 10


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _ledger_detail(e: dict):
    """A short plain line for one ledger event, or None. Built from counts
    and the reviewer's own words only - nothing here is a secret, and the
    ledger never held one."""
    ev = e.get("event")
    a = e.get("after") if isinstance(e.get("after"), dict) else {}
    b = e.get("before") if isinstance(e.get("before"), dict) else {}
    c = e.get("context") if isinstance(e.get("context"), dict) else {}
    try:
        if ev == "scale_set":
            if a.get("text"):
                return str(a["text"])
            f = a.get("ft_per_paper_inch")
            return f"{float(f):g} ft per paper inch" if f is not None else None
        if ev == "scale_pick_retired":
            return "back to the engine's reading"
        if ev == "approve_export":
            parts = []
            if isinstance(a.get("n_elements"), int):
                parts.append(_plural(a["n_elements"], "line"))
            if isinstance(a.get("items"), list):
                parts.append(_plural(len(a["items"]), "item"))
            return ", ".join(parts) or None
        if ev == "cutfill_rows":
            return ("added to the takeoff" if a.get("added")
                    else "taken out of the takeoff")
        if ev in ("structure_add", "structure_edit", "structure_delete"):
            verb = {"structure_add": "added", "structure_edit": "edited",
                    "structure_delete": "removed"}[ev]
            return f"{c['name']} {verb}" if c.get("name") else verb
        if ev == "structure_set" and isinstance(e.get("after"), list):
            return _plural(len(e["after"]), "structure")
        if ev == "structure_defaults_edit":
            return "default depth, layback or footing changed"
        if ev == "workzones_set" and isinstance(c.get("n_zones"), int):
            return _plural(c["n_zones"], "work area")
        if ev in ("check_done", "check_undone"):
            cid = (a or b).get("id")
            return str(cid) if cid is not None else None
    except (TypeError, ValueError, KeyError):
        return None
    return None


def _ledger_for_job(job: str, limit: int) -> list:
    """The job's recent ledger events, newest first, as (epoch, event) pairs.
    The ledger names its month files and stamps ts_iso in LOCAL time, so the
    two month files are the local current and previous ones."""
    from takeoff_engine import ledger as _ledger

    want = job.encode("utf-8")
    out: list = []
    for y, m in _this_and_last_month(time.localtime()):
        f = _ledger.ledger_dir() / f"events-{y:04d}-{m:02d}.jsonl"
        for ln in reversed(_tail_lines(f)):
            if want not in ln:
                continue
            try:
                e = json.loads(ln.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue
            if not isinstance(e, dict) or e.get("job") != job:
                continue
            if e.get("event") in _LEDGER_SKIP:
                continue
            try:
                epoch = time.mktime(time.strptime(str(e.get("ts_iso")),
                                                  "%Y-%m-%dT%H:%M:%S"))
            except (ValueError, OverflowError):
                continue
            out.append((epoch, e))
            if len(out) >= limit:
                return out
    return out


def _audit_epoch(ts) -> float | None:
    try:
        return float(calendar.timegm(time.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def job_activity(job: str, limit: int = 30, with_user: bool = False) -> list:
    """One project's history, newest first: [{ts, action, page, detail}] plus
    `user` when with_user (admins only - the caller decides). `ts` is UTC
    ISO with a Z on every row, the ledger's local stamps converted, so the
    two sources sort as one list."""
    try:
        limit = max(1, min(int(limit), 200))
    except (TypeError, ValueError):
        limit = 30
    rows = []                            # (epoch, order, row)
    audited = []                         # [epoch, action, page, used]
    for e in read(limit, job=job):
        ep = _audit_epoch(e.get("ts"))
        if ep is None:
            continue
        row = {"ts": e.get("ts"), "action": e.get("action"),
               "page": e.get("page"), "detail": e.get("detail")}
        if with_user:
            row["user"] = e.get("user")
        rows.append((ep, 0, row))
        audited.append([ep, e.get("action"), e.get("page"), False])
    # twice the limit: some of these are twins and will be dropped
    for ep, e in _ledger_for_job(job, limit * 2):
        ev = str(e.get("event") or "?")
        page = e.get("page_index")
        twin = _LEDGER_TWIN.get(ev)
        if twin is not None:
            hit = next((a for a in audited if not a[3] and a[1] == twin
                        and a[2] == page and abs(a[0] - ep) <= _TWIN_WINDOW_S),
                       None)
            if hit is not None:
                hit[3] = True
                continue
        row = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ep)),
               "action": _LEDGER_ACTION.get(ev, ev),
               "page": page if isinstance(page, int) else None,
               "detail": _ledger_detail(e)}
        if with_user:
            row["user"] = None          # the ledger never names anyone
        rows.append((ep, 1, row))
    rows.sort(key=lambda r: (-r[0], r[1]))
    return [r[2] for r in rows[:limit]]
