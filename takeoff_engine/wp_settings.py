"""THE ACCOUNT'S WALL PANEL RATES (2026-10-03), one record per account, beside users.json.

Everything the wall-panel estimate (takeoff_engine/wp_job.py) prices and writes with that is the
installer's own and not a job's: the labor rate, when overtime starts and what it costs, travel a
mile, a hotel night, the profit rule (a flat amount under so many sheets, otherwise so much a day),
the sheet sizes the account buys, its molding types, its price items (a sheet, or a square foot),
the sentence its proposal prints (a template with named blanks) and its standard notes.

NOTHING HERE HAS A DEFAULT. `blank()` is every value NOT SET, and a line whose rate is not set holds
the proposal (wp_job.compute). No installer's number lives in this code: an account's numbers arrive
by being typed in Settings, or loaded from a JSON file by staff (`load` below, the same record).

ONE SHAPE. The record the store keeps, the record the Settings page shows and posts, and the JSON
file staff load are the same keys with the same values: money as dollars with two decimals
("12.50"), hours and counts as numbers, sizes as "4x8". `clean` is the only way in: it names every
problem in a sentence and never guesses (an unknown key is a problem, never dropped - a typo in a
file must not read as "not set").

WHERE IT LIVES. `wallpanels.json` in the job store: {"install": rec, "accounts": {user: rec}}, the
same two scopes as branding.py (an account's own record; `install` for a box with no accounts).
Each record carries `rev`; a save names the rev it read and a stale one is refused.

THE STORE IS ONE FILE FOR EVERY ACCOUNT, so it is kept safe here (critic wp-1, MAJOR-3):
  * ONE WRITER AT A TIME: a save reads the file, changes ONE record and replaces the file under one
    lock - a thread lock in this process and an OS lock on `wallpanels.json.lock` across processes
    (the terminal loader below, a second server process). Two accounts saving at the same moment
    each keep their save.
  * A SAVE TOUCHES ONE RECORD: every other account's record goes back exactly as it was read,
    never through `clean` (which would write a value it can not read back as "not set").
  * THE REPLACE IS ATOMIC and retried a moment on Windows, where a reader holding the file open
    makes it fail; a reader retries the same way.
  * A FILE THAT IS THERE AND CAN NOT BE READ IS NEVER "NOTHING SET". `load` raises `Damaged`;
    `state` answers every value NOT KNOWN with the sentence to show; `save` refuses, leaves the file
    as it is and keeps a copy of it beside it under another name. Only a MISSING file means that
    nothing was ever set.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import secrets
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Optional

V = 1
# dollars: 0 is a decision ("we never charge that"), blank is not set
MONEY_KEYS = ("labor_rate", "overtime_rate", "travel_per_mile", "hotel_nightly", "profit_flat", "profit_per_day")
RATE_KEYS = MONEY_KEYS + ("overtime_after_hours", "profit_flat_under_sheets")
LIST_KEYS = ("sizes", "molding_types", "price_items", "notes")
KEYS = RATE_KEYS + LIST_KEYS + ("scope_template", "about")
# what each rate is, in the words the Settings page and every refusal use
WORDS = {
    "labor_rate": "labor, dollars an hour",
    "overtime_after_hours": "overtime starts after this many hours a day",
    "overtime_rate": "overtime, dollars an overtime hour",
    "travel_per_mile": "travel, dollars a mile",
    "hotel_nightly": "hotel, dollars a night",
    "profit_flat": "profit, the flat amount",
    "profit_per_day": "profit, dollars a day",
    "profit_flat_under_sheets": "the flat profit applies to a job with fewer sheets than this",
}
MAX_MONEY_CENTS = 10 ** 9
MAX_SIZES, MAX_MOLDINGS, MAX_ITEMS, MAX_NOTES = 20, 20, 500, 30
TEMPLATE_MAX, NOTE_MAX, ABOUT_MAX = 600, 300, 400
BLANK_NAME_MAX = 40
BLANK_RX = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# NAMES A BLANK OF THE SENTENCE MAY NOT CARRY: the figures a job computes or prices with. No rule fills a blank by
# one of these names (none is the proposal's to show but the price, which it prints by itself, below the sentence),
# so it could only be typed by hand - and a figure typed by hand does not follow the job (critic wp-1, BLOCKER-1).
RESERVED_BLANKS = frozenset(("total", "total_words", "price", "amount", "days", "hours", "miles", "nights", "freight", "profit",
                             "labor", "overtime", "travel", "hotel", "rate", "rates", "sheets_total", "wall_lf", "wall_length",
                             "linear_feet") + RATE_KEYS)
_SIZE_RX = re.compile(r"^\s*(\d+(?:\.\d{1,2})?)\s*(?:x|X|×|by)\s*(\d+(?:\.\d{1,2})?)\s*$")
_ID_RX = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
_GROUPED = re.compile(r"^\d{1,3}(,\d{3})+(\.\d*)?$")


class Stale(Exception):
    """A save over a record that changed since it was read."""


class Damaged(Exception):
    """The settings store is there and can not be read as what it is. NEVER the same as "nothing
    set": nothing is taken from it and nothing is written over it until a person mends it.
    `why`: what is wrong with it, in words; `kept`: the name of the copy `save` kept beside it."""

    def __init__(self, why: str, kept: Optional[str] = None):
        super().__init__(why)
        self.why, self.kept = why, kept


class Busy(Exception):
    """The store could not be locked or replaced in time (another process holds it). Nothing was written."""


def store_path() -> Path:
    root = os.environ.get("TAKEOFF_JOBS_DIR")
    base = Path(root).expanduser() if root else Path(__file__).parent.parent / "out" / "jobs"
    return base / "wallpanels.json"


def blank() -> dict:
    """Every value not set."""
    rec = {k: None for k in RATE_KEYS}
    rec.update(sizes=[], molding_types=[], price_items=[], notes=[], scope_template=None, about=None, v=V, rev=0, saved=None)
    return rec


# ---- values ------------------------------------------------------------------------------
def _dec(v) -> Optional[Decimal]:
    """A finite Decimal from a number or its string ("1,250.00" as a well-formed group); None otherwise."""
    if v is None or isinstance(v, bool):
        return None
    try:
        if isinstance(v, float):
            d = Decimal(repr(v))
        else:
            s = str(v).strip().lstrip("$").strip()
            if "," in s:
                if not _GROUPED.match(s):
                    return None
                s = s.replace(",", "")
            d = Decimal(s)
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() else None


def money_cents(v, places: int = 2):
    """(whole cents, None) from typed dollars - 0 or more, at most `places` decimals, never rounded -
    or (None, why). With places 4 (a price a square foot) the answer is ten-thousandths of a dollar."""
    d = _dec(v)
    if d is None:
        return None, f'"{str(v)[:30]}" is not an amount: type dollars and cents, e.g. 12.50'
    if d < 0:
        return None, "an amount is 0 or more"
    # THE CAP FIRST (critic wp-1, minor 5): thirty 9s, 1e30 or a JSON 1e308 made the quantize below raise
    # decimal.InvalidOperation, which no route catches as a refusal - a 500 where a sentence belongs
    cap = Decimal(MAX_MONEY_CENTS).scaleb(-2)
    if d > cap:
        return None, f'"{str(v)[:30]}" is too large: an amount here is at most {cap:,.2f}'
    unit = Decimal(1).scaleb(-places)
    if d != d.quantize(unit):
        return None, f'"{str(v)[:30]}" has more than {places} decimals'
    return int(d.scaleb(places)), None


def money_str(c: Optional[int]) -> Optional[str]:
    """Stored dollars: 1250 -> "12.50"; None stays None."""
    if c is None:
        return None
    return f"{int(c) // 100}.{int(c) % 100:02d}"


def cents_of(rec: dict, key: str) -> Optional[int]:
    """A money value of a cleaned record as whole cents; None when it is not set."""
    v = (rec or {}).get(key)
    if v is None:
        return None
    c, _why = money_cents(v)
    return c


def _num(d: Decimal) -> str:
    s = format(d.normalize(), "f")
    return s if s not in ("-0", "") else "0"


def size_dims(size) -> Optional[tuple]:
    """("4x8") -> (canonical name, width ft, length ft) as Fractions. THE WIDTH IS THE SMALLER NUMBER
    of the size and the length the larger - an INFERENCE from the size's own name (a sheet stands on
    its short side), said wherever a count is suggested from it. None when it is not a size."""
    m = _SIZE_RX.match(str(size if size is not None else ""))
    if not m:
        return None
    a, b = Decimal(m.group(1)), Decimal(m.group(2))
    if not (0 < a <= 100 and 0 < b <= 100):
        return None
    return f"{_num(a)}x{_num(b)}", Fraction(min(a, b)), Fraction(max(a, b))


def template_blanks(tpl) -> list:
    """The blank names a template carries, in order, each once. A NAME IS READ WHATEVER ITS CASE
    ({Sheets} is {sheets}: critic wp-1, minor 11), so the names come back in small letters."""
    out = []
    for m in BLANK_RX.finditer(str(tpl or "")):
        if m.group(1).lower() not in out:
            out.append(m.group(1).lower())
    return out


def _text(v, cap: int) -> str:
    s = re.sub(r"[\x00-\x1f\x7f]", " ", str(v if v is not None else ""))
    return " ".join(s.split())[:cap]


def new_item_id() -> str:
    return "pi" + secrets.token_hex(4)


# ---- the one way in ----------------------------------------------------------------------
def clean(raw, strict: bool = True) -> tuple:
    """(record, problems). `problems` is every reason the input is not a settings record, each a
    sentence naming its key; the record is what the input says where it is sound and NOT SET where
    it is not. A caller that writes must write only when `problems` is empty."""
    rec = blank()
    bad = []
    if raw is None:
        return rec, bad
    if not isinstance(raw, dict):
        return rec, ["wall panel settings are one JSON object of named values"]
    if strict:
        for k in raw:
            if k not in KEYS and k not in ("v", "rev", "saved", "unreadable"):
                bad.append(f'"{str(k)[:40]}" is not a wall panel setting (the settings are: {", ".join(KEYS)})')
    for k in MONEY_KEYS:
        v = raw.get(k)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        c, why = money_cents(v)
        if c is None:
            bad.append(f"{k} ({WORDS[k]}): {why}")
        else:
            rec[k] = money_str(c)
    v = raw.get("overtime_after_hours")
    if not (v is None or (isinstance(v, str) and not v.strip())):
        d = _dec(v)
        if d is None or d < 0 or d > 24 or d != d.quantize(Decimal("0.01")):
            bad.append(f'overtime_after_hours ({WORDS["overtime_after_hours"]}): "{str(v)[:30]}" is not hours a day (0 to 24, at most 2 decimals)')
        else:
            rec["overtime_after_hours"] = _num(d)
    v = raw.get("profit_flat_under_sheets")
    if not (v is None or (isinstance(v, str) and not v.strip())):
        d = _dec(v)
        if d is None or d < 0 or d > 100000 or d != d.to_integral_value():
            bad.append(f'profit_flat_under_sheets ({WORDS["profit_flat_under_sheets"]}): "{str(v)[:30]}" is not a whole number of sheets')
        else:
            rec["profit_flat_under_sheets"] = int(d)
    sizes = raw.get("sizes")
    if sizes is not None:
        if not isinstance(sizes, list) or len(sizes) > MAX_SIZES:
            bad.append(f"sizes: a list of at most {MAX_SIZES} sheet sizes, each written like 4x8")
        else:
            for s in sizes:
                d = size_dims(s)
                if d is None:
                    bad.append(f'sizes: "{str(s)[:30]}" is not a sheet size - write it width x length in feet, like 4x8')
                elif d[0] in rec["sizes"]:
                    bad.append(f"sizes: {d[0]} is listed twice")
                else:
                    # one sheet named two ways (4x8 and 8x4) is one sheet: a room would be offered it twice
                    same = next((x for x in rec["sizes"] if size_dims(x)[1:] == d[1:]), None)
                    if same:
                        bad.append(f"sizes: {d[0]} is the same sheet as {same} (a size's smaller number is its width): list it once")
                    else:
                        rec["sizes"].append(d[0])
    mt = raw.get("molding_types")
    if mt is not None:
        if not isinstance(mt, list) or len(mt) > MAX_MOLDINGS:
            bad.append(f"molding_types: a list of at most {MAX_MOLDINGS} names")
        else:
            for m in mt:
                t = _text(m, 40)
                if t and t.casefold() not in [x.casefold() for x in rec["molding_types"]]:
                    rec["molding_types"].append(t)
    items = raw.get("price_items")
    if items is not None:
        if not isinstance(items, list) or len(items) > MAX_ITEMS:
            bad.append(f"price_items: a list of at most {MAX_ITEMS} items")
        else:
            seen = set()
            for i, it in enumerate(items):
                where = f"price_items, item {i + 1}"
                if not isinstance(it, dict):
                    bad.append(f"{where}: an item is a name, a price and what the price is for (a sheet, or a square foot)")
                    continue
                extra = [k for k in it if k not in ("id", "name", "code", "per", "price")]
                if strict and extra:
                    bad.append(f'{where}: "{str(extra[0])[:30]}" is not part of a price item (id, name, code, per, price)')
                name = _text(it.get("name"), 80)
                per = it.get("per")
                if not name:
                    bad.append(f"{where}: it has no name")
                if per not in ("sheet", "sqft"):
                    bad.append(f'{where}: "per" is "sheet" or "sqft" (the price is for one sheet, or for one square foot)')
                    continue
                c, why = money_cents(it.get("price"), 2 if per == "sheet" else 4)
                if c is None or c <= 0:
                    bad.append(f"{where} ({name or 'no name'}): " + (why or "its price must be more than 0"))
                    continue
                iid = str(it.get("id") or "")
                if not _ID_RX.match(iid) or iid in seen:
                    iid = new_item_id()
                seen.add(iid)
                d = Decimal(c).scaleb(-(2 if per == "sheet" else 4))
                rec["price_items"].append({"id": iid, "name": name, "code": _text(it.get("code"), 40), "per": per,
                                           "price": f"{d:.2f}" if per == "sheet" else _price4(d)})
    notes = raw.get("notes")
    if notes is not None:
        if not isinstance(notes, list) or len(notes) > MAX_NOTES:
            bad.append(f"notes: a list of at most {MAX_NOTES} lines")
        else:
            for n in notes:
                if len(str(n or "")) > NOTE_MAX:
                    bad.append(f"notes: a line of {len(str(n))} characters - at most {NOTE_MAX} (nothing was cut)")
                elif _text(n, NOTE_MAX):
                    rec["notes"].append(_text(n, NOTE_MAX))
    tpl = raw.get("scope_template")
    if tpl is not None and str(tpl).strip():
        t = _text(tpl, TEMPLATE_MAX + 1)
        if len(t) > TEMPLATE_MAX:
            bad.append(f"scope_template: more than {TEMPLATE_MAX} characters (nothing was cut)")
        elif "{" in BLANK_RX.sub("", t) or "}" in BLANK_RX.sub("", t):
            bad.append("scope_template: a blank is a name in braces, like {thickness} - a brace is left over")
        else:
            # a blank whose name is longer than a job can type holds every proposal for good (critic wp-1, minor 11)
            long_ = [m.group(1) for m in BLANK_RX.finditer(t) if len(m.group(1)) > BLANK_NAME_MAX]
            fig = [n for n in template_blanks(t) if n in RESERVED_BLANKS]
            if long_:
                bad.append(f"scope_template: the blank {{{long_[0][:20]}...}} has a name of {len(long_[0])} letters - "
                           f"at most {BLANK_NAME_MAX}")
            elif fig:
                bad.append(f"scope_template: {{{fig[0]}}} is a figure of the job itself (its price, a quantity or a rate). A blank by "
                           "that name would have to be typed by hand and would not follow the job: remove it or give the blank "
                           "another name (the proposal prints its price by itself, below the sentence)")
            else:
                rec["scope_template"] = t
    ab = raw.get("about")
    if ab is not None and str(ab).strip():
        rec["about"] = _text(ab, ABOUT_MAX)
    try:
        rec["rev"] = max(0, int(raw.get("rev") or 0))
    except (TypeError, ValueError):
        rec["rev"] = 0
    sv = raw.get("saved")
    if isinstance(sv, dict):
        rec["saved"] = {"by": _text(sv.get("by"), 120), "at": _text(sv.get("at"), 40), "how": _text(sv.get("how"), 40)}
    return rec, bad


def _price4(d: Decimal) -> str:
    """A price a square foot as it prints: at least 2 decimals, up to 4 ("1.85", "1.2345")."""
    s = f"{d:.4f}"
    while s.endswith("0") and len(s.split(".")[1]) > 2:
        s = s[:-1]
    return s


def is_set(rec: dict) -> bool:
    """Has anything been set in this record?"""
    return any(rec.get(k) is not None for k in RATE_KEYS) or any(rec.get(k) for k in LIST_KEYS) or bool(rec.get("scope_template"))


# ---- the store ---------------------------------------------------------------------------
_LOCK = threading.Lock()          # this process: one reader or writer of the file at a time
LOCK_WAIT_S = 30.0                # how long a save waits for another process before it answers Busy


def _read_raw() -> Optional[dict]:
    """The store as it is on disk. None when there is NO file: nothing was ever set. `Damaged` when
    there is one and it is not the store: unreadable bytes, not JSON, or not its shape."""
    f = store_path()
    b = None
    for i in range(20):
        try:
            b = f.read_bytes()
            break
        except FileNotFoundError:
            return None
        except PermissionError:                 # Windows: another process is replacing it this instant
            if i == 19:
                raise Damaged("it could not be opened (it is held by another program)")
            time.sleep(0.05)
        except OSError as e:
            raise Damaged(f"it could not be opened ({type(e).__name__})")
    try:
        raw = json.loads(b.decode("utf-8-sig"))
    except ValueError:                          # not UTF-8, or not JSON (an empty file is not JSON either)
        raise Damaged("it is not readable JSON" if b.strip() else "it is empty")
    if not isinstance(raw, dict):
        raise Damaged("it is not the settings store (one object of accounts)")
    if not isinstance(raw.get("accounts", {}), dict) or not isinstance(raw.get("install") or {}, dict):
        raise Damaged("it is not the settings store (its accounts are not a list of records)")
    for k, v in raw.get("accounts", {}).items():
        if not isinstance(v, dict):
            raise Damaged(f"the record of {str(k)[:60]} in it is not a record")
    return raw


def _record(v) -> dict:
    """A stored record as every reader sees it. A value in it that `clean` refuses (the file was changed by
    hand) reads NOT SET, and `unreadable` says which: it is never passed off as a value that was never set."""
    rec, bad = clean(v, strict=False)
    if bad:
        rec["unreadable"] = bad
    return rec


def load() -> dict:
    """{"install": rec, "accounts": {user: rec}}. A MISSING file is the empty store. A file that is there
    and can not be read raises `Damaged`: it is never read as "nothing set"."""
    with _LOCK:
        raw = _read_raw()
    out = {"install": blank(), "accounts": {}}
    if raw is None:
        return out
    out["install"] = _record(raw.get("install"))
    for k, v in raw.get("accounts", {}).items():
        if isinstance(k, str) and k:
            out["accounts"][k] = _record(v)
    return out


def resolve(user: Optional[str]) -> tuple:
    """(the record that applies to `user`, its scope). An account has ITS OWN record or none: it never
    inherits the install's (one installer's rates must not price another's job). `user` None is the
    box with no accounts: the install record. Raises `Damaged` when the store can not be read."""
    d = load()
    if user:
        if user in d["accounts"]:
            return d["accounts"][user], "account"
        return blank(), "none"
    return d["install"], ("install" if is_set(d["install"]) else "none")


def state(user: Optional[str]) -> tuple:
    """(record, scope, problem): `resolve` for a caller that must answer either way. `problem` is None,
    or what is wrong with the store in words; the record is then every value NOT KNOWN (returned as the
    blank one under the scope "damaged") - nothing may be priced from it and nothing saved over it."""
    try:
        rec, scope = resolve(user)
    except Damaged as e:
        return blank(), "damaged", e.why
    return rec, scope, None


@contextlib.contextmanager
def _store_lock():
    """ONE WRITER AT A TIME: this process's lock, and an OS lock on a file beside the store for every
    other process on the machine (the terminal loader, a second server process). The OS drops its lock
    when the process that holds it ends, so the store is never left locked."""
    with _LOCK:
        f = store_path()
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            fh = open(f.with_name(f.name + ".lock"), "a+b")
        except OSError as e:                    # a store that refuses the file: said, never a crash
            raise Busy(f"the wall panel settings can not be written on this server ({type(e).__name__}): nothing was saved")
        try:
            t0 = time.time()
            while True:
                try:
                    fh.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.time() - t0 > LOCK_WAIT_S:
                        raise Busy("the wall panel settings are being saved by another program: try again in a moment")
                    time.sleep(0.02)
            try:
                yield
            finally:
                try:
                    fh.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
        finally:
            fh.close()


def _replace_retry(tmp: Path, f: Path) -> None:
    """os.replace, retried a moment: on Windows a reader holding the file open makes it fail with
    PermissionError, and a save must not be lost to that (server._replace_retry, for the same reason)."""
    for i in range(40):
        try:
            os.replace(tmp, f)
            return
        except PermissionError:
            if i == 39:
                try:
                    tmp.unlink()
                except OSError:
                    pass
                raise Busy("the wall panel settings could not be written just now (the file is held open): try again")
            time.sleep(0.05)


def _keep_aside(f: Path) -> Optional[str]:
    """A copy of the damaged store beside it under another name, once per content: a mend gone wrong can
    not lose what a person could still read. -> the copy's name, or None when even that failed."""
    try:
        b = f.read_bytes()
    except OSError:
        return None
    name = f"{f.name}.damaged-{hashlib.sha1(b).hexdigest()[:10]}"
    dst = f.with_name(name)
    try:
        if not dst.exists():
            tmp = f.with_name(f"{name}.{uuid.uuid4().hex[:8]}.tmp")
            tmp.write_bytes(b)
            os.replace(tmp, dst)
    except OSError:
        return None
    return name


def save(user: Optional[str], raw, rev=None, by: str = "", at: str = "", how: str = "typed") -> dict:
    """Write one account's record (the install's when `user` is None) and return it. `raw` is held to
    `clean`: any problem raises ValueError with every sentence, and nothing is written. `rev`, when
    given, must be the stored record's rev (`Stale` otherwise). Read, change and replace happen under
    one lock; every other record goes back as it was read. A store that can not be read is NOT written
    over: `Damaged`, with the name of the copy kept beside it."""
    rec, bad = clean(raw)
    if bad:
        raise ValueError("\n".join(bad))
    with _store_lock():
        f = store_path()
        try:
            store = _read_raw()
        except Damaged as e:
            e.kept = _keep_aside(f)
            raise
        if store is None:
            store = {}
        if not isinstance(store.get("accounts"), dict):
            store["accounts"] = {}
        cur = store["accounts"].get(user) if user else store.get("install")
        cur_rev = int(clean(cur, strict=False)[0].get("rev") or 0) if isinstance(cur, dict) else 0
        if rev is not None and int(rev) != cur_rev:
            raise Stale(cur_rev)
        rec["rev"] = cur_rev + 1
        rec["saved"] = {"by": _text(by, 120), "at": _text(at, 40), "how": _text(how, 40)}
        if user:
            store["accounts"][user] = rec
        else:
            store["install"] = rec
        tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.tmp")
        try:
            tmp.write_text(json.dumps(store, indent=1, sort_keys=True), encoding="utf-8")
            _replace_retry(tmp, f)
        except OSError as e:                    # a full disk, a folder that refuses the file: the store is as it was
            try:
                tmp.unlink()
            except OSError:
                pass
            raise Busy(f"the wall panel settings could not be written ({type(e).__name__}): nothing was saved")
    return rec


# ---- what a job takes with it ------------------------------------------------------------
SNAP_KEYS = RATE_KEYS + ("sizes", "molding_types", "notes", "scope_template")


def snapshot(rec: dict) -> dict:
    """The part of a record a job prices and writes with (no price items: a price is taken into its
    sheet row when it is picked)."""
    rec = clean(rec, strict=False)[0]
    return {k: (list(rec[k]) if isinstance(rec[k], list) else rec[k]) for k in SNAP_KEYS}


SNAP_WORDS = dict(WORDS, sizes="sheet sizes", molding_types="molding types", scope_template="the proposal's scope sentence",
                  notes="the proposal's notes")


def unset(v) -> bool:
    """Is a value of a record NOT SET? None, or an empty list or text. 0 and "0.00" are SET: a decision."""
    return v is None or v == [] or v == ""


def standing(own: Optional[dict], live: dict) -> tuple:
    """(the rates a job stands on, the keys it FOLLOWS the account's settings for) - critic wp-1, minor 2.
    A value the job's own copy holds is the job's: it never moves by itself. A value the copy does NOT
    hold - the job was started before the account set it - is the account's as it is now, until the job
    is next written and takes it (`take_unset`). A job that was never written (`own` None) stands on the
    settings as they are now and follows nothing: it has no copy yet."""
    now = snapshot(live)
    if own is None:
        return now, []
    mine = snapshot(own)
    follows = [k for k in SNAP_KEYS if unset(mine[k]) and not unset(now[k])]
    for k in follows:
        mine[k] = list(now[k]) if isinstance(now[k], list) else now[k]
    return mine, follows


def follow_words(keys: list, rates: dict) -> list:
    """What a job follows the settings for, each a sentence with the value it has now."""
    out = []
    for k in keys:
        v = rates.get(k)
        if k in ("sizes", "molding_types"):
            said = ", ".join(str(x) for x in v or [])
        elif k in ("scope_template", "notes"):
            said = ""                       # printed in full where the proposal is shown
        else:
            said = str(v)
        out.append(SNAP_WORDS[k] + (f": {said}" if said else ""))
    return out


WORD_KEYS = ("scope_template", "notes")       # the proposal's words; every other key of a snapshot prices or lists


def differing(was: dict, now: dict) -> list:
    """The keys whose value differs between a job's rates and the account's rates now."""
    a, b = snapshot(was), snapshot(now)
    return [k for k in SNAP_KEYS if a[k] != b[k]]


def differences(was: dict, now: dict) -> list:
    """What differs between a job's rates and the account's rates now, each a sentence."""
    a, b = snapshot(was), snapshot(now)
    said = lambda v: "not set" if v in (None, "", []) else (", ".join(str(x) for x in v) if isinstance(v, list) else str(v))
    out = []
    for k in RATE_KEYS:
        if a[k] != b[k]:
            out.append(f"{WORDS[k]}: {said(a[k])} here, {said(b[k])} in your settings now")
    for k, w in (("sizes", "sheet sizes"), ("molding_types", "molding types"), ("scope_template", "the proposal's scope sentence"),
                 ("notes", "the proposal's notes")):
        if a[k] != b[k]:
            out.append(f"{w}: changed in your settings since" if k in ("scope_template", "notes") else
                       f"{w}: {said(a[k])} here, {said(b[k])} in your settings now")
    return out


# ---- staff at a terminal -----------------------------------------------------------------
def _cli() -> int:
    import argparse
    import time

    from takeoff_engine import users as _users

    ap = argparse.ArgumentParser(prog="python -m takeoff_engine.wp_settings",
                                 description="Load or show one account's wall panel rates (TAKEOFF_JOBS_DIR names the store).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ld = sub.add_parser("load", help="replace one account's wall panel settings with a JSON file's")
    ld.add_argument("file")
    sh = sub.add_parser("show", help="print one account's wall panel settings")
    for p in (ld, sh):
        who = p.add_mutually_exclusive_group(required=True)
        who.add_argument("--account", help="the account's sign-in name")
        who.add_argument("--install", action="store_true", help="the record of a box with no accounts")
    args = ap.parse_args()
    user = None if args.install else _users.find(args.account)
    if not args.install and user is None:
        print(f"no such account: {args.account} (nothing was written)")
        return 1
    if args.cmd == "show":
        rec, scope, problem = state(user)
        if problem:
            print(f"the settings store {store_path()} could not be read: {problem}. It is NOT read as 'nothing set': mend the file.")
            return 1
        print(json.dumps(rec, indent=1))
        print(f"\nscope: {scope}; store: {store_path()}")
        return 0
    try:
        raw = json.loads(Path(args.file).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        print(f"could not read {args.file}: {e} (nothing was written)")
        return 1
    try:
        rec = save(user, raw, by="staff at the terminal", at=time.strftime("%Y-%m-%d %H:%M"), how="loaded from a file")
    except Damaged as e:
        print(f"refused, nothing was written: the settings store {store_path()} could not be read: {e.why}. It was left as it is"
              + (f"; a copy of it was kept beside it as {e.kept}." if e.kept else "."))
        return 1
    except Busy as e:
        print(f"refused, nothing was written: {e}")
        return 1
    except ValueError as e:
        print("refused, nothing was written:")
        for ln in str(e).split("\n"):
            print("  - " + ln)
        return 1
    print(f"loaded for {user or 'this install'} (rev {rec['rev']}) -> {store_path()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
