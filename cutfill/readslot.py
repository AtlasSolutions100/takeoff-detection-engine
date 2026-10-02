"""At most N sheet analyses at a time per server, across every process that shares the store (D41).
Since 2026-10-01 (live proof P6) a slot holds the WHOLE analysis of a sheet (project.analyze_page: the reading, fresh
or cached, and its interpretation): on Res-A civil C3.0 the interpretation alone ran 150 s, and holding only the read
let the next sheet's read run beside it (2,414 MB). "Read" below means that whole run.

A fresh read of a heavy civil sheet holds 1-2 GB while it runs (Res-A civil C3.0, 2026-10-01: 1.7 GB at
its peak after the D41 fix). Nothing limited how many ran at once: every Analyze started its own thread,
and the host's own queue counted only the sheets of its own process. This module hands out N slots
(env CUTFILL_MAX_READS, default 1) as OS file locks on files in the project store's own folder
(<CUTFILL_OUT>/.read-slots/slot-<i>.lock).

Why OS locks and not a lock file that is created and removed: the operating system lets go of a lock
when the process that held it ends, however it ends (killed, out of memory, crashed). A dead read can
never hold a slot, so there is no stale lock to judge. Each wait opens its own handle, so two threads of
one process exclude each other the same way two processes do. Where the store cannot lock at all (an
error other than "held elsewhere"), the slots are counted in this process only, and the log says so once.

A LIVE read cannot hold a slot for ever either (critic RISK-1: one stuck read froze cut / fill for every
user). Past CUTFILL_READ_MAX_SECONDS (default 900 s) the read is told to stop: its page says so at once, and
the read ends at its next check (every progress message, every OCR batch, every line style) without storing
anything. A Python thread cannot be killed, so until it has ended it KEEPS its slot and stays counted: a
stopped read and a new one never run at once (critic 2 RISK-1). Only if it has still not ended
CUTFILL_READ_STOP_GRACE_SECONDS (default 900 s) later is the slot freed anyway, so no thread can block every
other read for ever; the sheet itself still refuses a new read until that thread has ended.

A read that waits says so in plain words (WAITING) through the caller's progress callback, and goes on
waiting until a slot frees or the caller says to stop (`alive()` false: the project was closed). Waiters
are not served in order (critic RISK-5, still open). A re-interpretation on a cached reading waits in the same line:
seconds on most sheets, but the heavy half on Res-A civil C3.0 (heights 35-120 s), so it is never let past.
"""
from __future__ import annotations

import contextlib
import errno
import os
import sys
import threading
import time

# what a read waiting for a slot shows, on the engine page, its Sheets card and the host's cut / fill bar (Koby,
# 2026-10-01: "the screen should say it is waiting in line"). Waiting reads are NOT served in order (RISK-5), so it
# promises no place in the line
WAITING = "Waiting in line. Another sheet is being read first."
POLL_S = 0.5
# A lock call failing with one of these means "another read holds it"; any other error means locks do not
# work on this store (critic RISK-2: ENOLCK / EOPNOTSUPP were taken as "busy" and every read waited for ever).
# Windows msvcrt.locking reports a held lock as EACCES (measured here), flock as EWOULDBLOCK / EAGAIN.
_BUSY = {errno.EACCES, errno.EAGAIN, getattr(errno, "EWOULDBLOCK", errno.EAGAIN), getattr(errno, "EDEADLOCK", -1),
         getattr(errno, "EDEADLK", -1)}

DEFAULT_MAX_SECONDS = 900.0


class LocksUnsupported(OSError):
    """The store's file system cannot lock: the slots are counted in this process instead."""


class ReadTooLong(RuntimeError):
    """The read ran past max_seconds(): it was told to stop and stops at its next check."""


if os.name == "nt":
    import msvcrt

    def _lock_call(fd: int) -> None:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _unlock(fd: int) -> None:
        try:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        except OSError:
            pass
else:
    import fcntl

    def _lock_call(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)      # per open file: threads exclude each other too

    def _unlock(fd: int) -> None:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass


def _try_lock(fd: int) -> bool:
    """True: locked. False: held by another read. LocksUnsupported: this store cannot lock."""
    try:
        _lock_call(fd)
        return True
    except OSError as e:
        if e.errno in _BUSY:
            return False
        raise LocksUnsupported(e.errno, f"file locks do not work here: {e}") from e


def max_reads() -> int:
    """CUTFILL_MAX_READS, read on every call; unset, empty, not a number or below 1 means 1."""
    try:
        n = int(str(os.environ.get("CUTFILL_MAX_READS", "")).strip() or 1)
    except ValueError:
        n = 1
    return max(1, n)


def max_seconds() -> float:
    """CUTFILL_READ_MAX_SECONDS, read on every call: how long one fresh read may hold its slot. Unset, not a
    number, or not above 0 means DEFAULT_MAX_SECONDS: 900 s, about 10 times the slowest honest fresh read
    measured (Res-A civil C3.0: 92 s read, 162 s with its interpretation, 2026-10-01) and the same as the
    detection's own page budget (TAKEOFF_PAGE_BUDGET_S)."""
    try:
        v = float(str(os.environ.get("CUTFILL_READ_MAX_SECONDS", "")).strip() or DEFAULT_MAX_SECONDS)
    except ValueError:
        v = DEFAULT_MAX_SECONDS
    return v if (v == v and 0 < v < float("inf")) else DEFAULT_MAX_SECONDS


DEFAULT_STOP_GRACE_SECONDS = 900.0


def stop_grace_seconds() -> float:
    """CUTFILL_READ_STOP_GRACE_SECONDS, read on every call: how long a read told to stop may keep its slot while it
    winds down (default 900 s). Within it the slot stays taken, so a stopped read and a new one never run at once
    (critic 2 RISK-1). Past it the slot is freed whatever the stopped thread is doing, so a thread stuck for good
    cannot block every other read for ever (critic 1 RISK-1). What a winding-down read can still take before its
    next check: one tesseract run (killed at CUTFILL_OCR_TIMEOUT_S, 300 s), or one line style's joining or gap
    finding (bounded in pairs; 110 s measured for 20.6 million gap pairs). 900 s is three times the longest."""
    try:
        v = float(str(os.environ.get("CUTFILL_READ_STOP_GRACE_SECONDS", "")).strip() or DEFAULT_STOP_GRACE_SECONDS)
    except ValueError:
        v = DEFAULT_STOP_GRACE_SECONDS
    return v if (v == v and 0 < v < float("inf")) else DEFAULT_STOP_GRACE_SECONDS


def _span(secs: float) -> str:
    if secs >= 120:
        return f"{secs / 60.0:g} minutes"
    return "1 second" if secs == 1 else f"{secs:g} seconds"


def too_long_words(secs: float) -> str:
    return f"This sheet took more than {_span(secs)} to read and was stopped. Press Re-read to try again."


def stopping_words(secs: float) -> str:
    return f"This sheet took more than {_span(secs)} to read and is being stopped."


STILL_STOPPING = "The last read of this sheet is still stopping. Try again in a minute."


def slot_dir() -> str:
    """The lock folder inside the cut / fill store (CUTFILL_OUT, the host's <jobs>/cutfill), read at call time."""
    from . import project
    return os.path.join(os.environ.get("CUTFILL_OUT") or project.OUT, ".read-slots")


def _grab(d: str, n: int):
    for i in range(n):
        path = os.path.join(d, f"slot-{i}.lock")
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            got = _try_lock(fd)
        except LocksUnsupported:
            os.close(fd)
            raise
        if got:
            try:
                os.ftruncate(fd, 0)
                os.write(fd, f"pid {os.getpid()} since {time.strftime('%Y-%m-%d %H:%M:%S')}\n".encode())
            except OSError:
                pass
            return fd
        os.close(fd)
    return None


# where the store cannot lock, the slots are counted in this process only (said once in the log)
_LOCAL = {"mu": threading.Lock(), "held": 0, "warned": False}


def _grab_local(n: int) -> bool:
    with _LOCAL["mu"]:
        if _LOCAL["held"] < n:
            _LOCAL["held"] += 1
            return True
        return False


def _free_local() -> None:
    with _LOCAL["mu"]:
        _LOCAL["held"] = max(0, _LOCAL["held"] - 1)


def _warn_once(e: Exception, d: str) -> None:
    with _LOCAL["mu"]:
        if _LOCAL["warned"]:
            return
        _LOCAL["warned"] = True
    print(f"cutfill: file locks do not work in {d} ({e}); sheet reads are limited in this process only",
          file=sys.stderr, flush=True)


class Slot:
    """A held slot. After max_s a timer tells its read to stop (check() raises from then on) but the slot stays
    taken until the read has really ended; after max_s + grace it is freed whatever the thread is doing."""

    def __init__(self, release, max_s: float, on_expire, grace_s: float | None = None, on_abandon=lambda m: None):
        self._release = release
        self._mu = threading.Lock()
        self.released = False
        self.expired = False
        self.abandoned = False          # freed while its thread had not ended (a thread stuck for good)
        self.max_s = max_s
        self.grace_s = stop_grace_seconds() if grace_s is None else grace_s
        self._on_expire = on_expire
        self._on_abandon = on_abandon
        self._timer = threading.Timer(max_s, self._expire)
        self._timer.daemon = True
        self._timer.start()
        self._hard = threading.Timer(max_s + self.grace_s, self._abandon)
        self._hard.daemon = True
        self._hard.start()

    def _expire(self) -> None:
        with self._mu:
            if self.released:
                return
            self.expired = True         # the slot stays taken: the read is still running until it reaches a check
        try:
            self._on_expire(stopping_words(self.max_s))
        except Exception:               # noqa: BLE001 - a status callback never takes the timer down
            pass

    def _abandon(self) -> None:
        with self._mu:
            if self.released:
                return
            self.expired = True
            self.abandoned = True
            self.released = True
            self._release()             # the next read may start now: never a block for ever
        print(f"cutfill: a sheet read told to stop {self.grace_s:g} s ago has not ended; its read slot is freed",
              file=sys.stderr, flush=True)
        try:
            self._on_abandon(too_long_words(self.max_s))
        except Exception:               # noqa: BLE001
            pass

    def check(self) -> None:
        if self.expired:
            raise ReadTooLong(too_long_words(self.max_s))

    def close(self) -> None:
        self._timer.cancel()
        self._hard.cancel()
        with self._mu:
            if not self.released:
                self.released = True
                self._release()


_CURRENT = threading.local()


def check_current() -> None:
    """Raise ReadTooLong when the read running in THIS thread was told to stop (called between OCR batches and
    line styles, so a stopped read winds down within one batch or one style)."""
    s = getattr(_CURRENT, "slot", None)
    if s is not None:
        s.check()


@contextlib.contextmanager
def read_slot(prog=lambda m: None, alive=lambda: True, poll_s: float = POLL_S, on_expire=lambda m: None,
              on_abandon=lambda m: None):
    """Hold one of the server's read slots for the body; yields the Slot. Waits (saying WAITING once) while all
    are taken; raises RuntimeError when `alive()` turns false while waiting, so a closed project's read never
    starts. Past max_seconds() the read is told to stop: on_expire(words) is told then, slot.check() and
    check_current() raise ReadTooLong from then on, and leaving the body raises it too, so a read past its time
    never counts as done. The slot is held until the body ends, or at most stop_grace_seconds() longer."""
    d = slot_dir()
    os.makedirs(d, exist_ok=True)
    n = max_reads()
    mode = {"local": False}

    def take():
        if not mode["local"]:
            try:
                return _grab(d, n)
            except OSError as e:            # LocksUnsupported, or the lock file cannot even be opened
                _warn_once(e, d)
                mode["local"] = True
        return "local" if _grab_local(n) else None

    got = take()
    said = False
    while got is None:
        if not alive():
            raise RuntimeError("the sheet was not read: the project was closed while it waited")
        if not said:
            prog(WAITING)
            said = True
        time.sleep(poll_s)
        got = take()
    if got == "local":
        release = _free_local
    else:
        fd = got

        def release():
            _unlock(fd)
            os.close(fd)
    slot = Slot(release, max_seconds(), on_expire, on_abandon=on_abandon)
    prev = getattr(_CURRENT, "slot", None)
    _CURRENT.slot = slot
    try:
        yield slot
    finally:
        _CURRENT.slot = prev
        slot.close()
    slot.check()
