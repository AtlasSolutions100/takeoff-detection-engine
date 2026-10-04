"""THE E-MAIL LINE: an e-mailed set detects by itself and never costs a person a worker (ruling 6, 2026-10-03).

A project that arrived by e-mail queues its pages in a line of its own (server._SCHED_BG). What is pinned here, on
the REAL scheduler (server._sched_fill_locked, the watcher thread) with stand-in children instead of detections:

  1. ONE e-mailed page runs at a time, whatever the worker count - so one e-mailed project at a time, in order.
  2. A PERSON'S PAGE NEVER WAITS BEHIND AN E-MAILED ONE. With every worker busy and one of them on an e-mailed
     page, a person's Detect stops that page and starts at once: measured as "the person's page is running when
     _enqueue_detect returns", at 1, 2 and 4 workers. The stopped page goes back to the FRONT of its line and
     starts over when the people's pages are done.
  3. An e-mailed page starts only when no person's page is waiting.
  4. A person who presses Detect on a page waiting in the e-mail line moves it to the people's line (the /detect
     route), and a page that is already running stops being one that gets stopped.
  5. Deleting a project takes its pages out of the line and stops its running e-mailed page; and a queued page
     whose project is gone no longer ends the watcher thread (it raised KeyError inside the handler).
  6. The status route says a page is in the e-mail line (`email`), so the page can say "press Detect to do it now".

    python tests/test_inbound_sched.py
"""
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="inb_sched_")
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="inb_sched_lg_")
os.environ.pop("TAKEOFF_UI_PASSWORD", None)
os.environ.pop("TAKEOFF_HOSTED", None)

import pymupdf  # noqa: E402

import _inbound_harness as H  # noqa: E402
import server  # noqa: E402

fails = 0
checks = 0


def check(ok, msg, got=None):
    global fails, checks
    checks += 1
    print(("ok: " if ok else "FAIL: ") + msg + ("" if ok or got is None else f"  (got {got!r})"))
    if not ok:
        fails += 1


def run(fn):
    try:
        fn()
    except Exception as e:                           # noqa: BLE001 - on the commit before the feature every test lands here
        check(False, f"{fn.__name__}: raised {type(e).__name__}: {e}")


H.install_fake_children(server)
PDF = Path(server.JOBS_DIR) / "sched.pdf"
_d = pymupdf.open()
for _ in range(8):
    _d.new_page(width=612, height=792)
_d.save(str(PDF))
_d.close()


def job(jid: str, owner=None) -> str:
    server.JOBS[jid] = {"path": str(PDF), "pages": 8, "status": {}, "owner": owner,
                        "mtime": round(PDF.stat().st_mtime, 2), "sizes": [[612.0, 792.0]] * 8}
    server._job_dir(jid)
    return jid


def reset(workers: int) -> None:
    H.drain(server)
    with server._SCHED:
        server._SCHED_QUEUE.clear()
        server._SCHED_BG.clear()
    server.TAKEOFF_WORKERS = workers
    H.FakeProc.made.clear()
    for j in server.JOBS.values():
        j["status"] = {}
    server._SCHED_PREEMPTED[0] = 0


def bg_running() -> list:
    return [k for k, v in H.running(server).items() if v.get("bg")]


def people_running() -> list:
    return [k for k, v in H.running(server).items() if not v.get("bg")]


A, B, P, Q = job("aaaaaaaaaaa1"), job("bbbbbbbbbbb1"), job("ccccccccccc1"), job("ddddddddddd1")


def t1_one_at_a_time():
    for workers in (1, 4):
        reset(workers)
        for p in range(3):
            server._enqueue_detect_bg(A, p)
        for p in range(2):
            server._enqueue_detect_bg(B, p)
        check(bg_running() == [(A, 0)] and len(H.running(server)) == 1,
              f"1: {workers} worker(s), five e-mailed pages queued: exactly one runs, the first", H.running(server).keys())
        order = []
        for _ in range(5):
            order += bg_running()
            H.finish_running(server)
            H.wait_until(lambda: len(bg_running()) <= 1)
            time.sleep(0.4)
        check(order == [(A, 0), (A, 1), (A, 2), (B, 0), (B, 1)],
              f"1: {workers} worker(s): they run one after another, a whole project before the next", order)
        H.drain(server)
        check(all(server.JOBS[j]["status"].get(p) == "done" for j, n in ((A, 3), (B, 2)) for p in range(n)),
              f"1: {workers} worker(s): every e-mailed page ends detected by itself",
              {j: server.JOBS[j]["status"] for j in (A, B)})


def t2_a_person_never_waits():
    for workers in (1, 2, 4):
        reset(workers)
        for p in range(4):
            server._enqueue_detect_bg(A, p)
        # every OTHER worker busy with people's pages, so the e-mailed page holds the last one
        for p in range(workers - 1):
            server._enqueue_detect(Q, p)
        check(len(H.running(server)) == workers and bg_running() == [(A, 0)],
              f"2: {workers} worker(s): all busy, one of them on an e-mailed page", H.running(server).keys())
        first = H.running(server)[(A, 0)]["proc"]
        t0 = time.time()
        server._enqueue_detect(P, 0)                 # a person presses Detect
        waited = time.time() - t0
        run_now = H.running(server)
        check((P, 0) in run_now and not run_now[(P, 0)].get("bg"),
              f"2: {workers} worker(s): the person's page is RUNNING when the press returns ({waited * 1000:.0f} ms)",
              list(run_now))
        check(first.stopped and (A, 0) not in run_now and not bg_running(),
              f"2: {workers} worker(s): the e-mailed page was stopped to make room", (first.stopped, list(run_now)))
        with server._SCHED:
            line = list(server._SCHED_BG)
            waiting = list(server._SCHED_QUEUE)
        check(line[:2] == [(A, 0), (A, 1)] and not waiting,
              f"2: {workers} worker(s): it went back to the FRONT of its line; no person's page is waiting", (line, waiting))
        check(server.JOBS[A]["status"].get(0) == "running" and server._SCHED_PREEMPTED[0] == 1,
              f"2: {workers} worker(s): the stopped page still reads as waiting, and the stop was counted",
              (server.JOBS[A]["status"].get(0), server._SCHED_PREEMPTED[0]))
        # the people's pages finish: the e-mailed page starts over by itself
        H.finish_running(server, only_bg=False)
        ok = H.wait_until(lambda: bg_running() == [(A, 0)])
        again = H.running(server).get((A, 0), {}).get("proc")
        check(ok and again is not None and again is not first,
              f"2: {workers} worker(s): once the people's pages are done the e-mailed page starts over", list(H.running(server)))
        H.drain(server)


def t3_people_first():
    reset(2)
    server._enqueue_detect(P, 0)
    server._enqueue_detect(P, 1)
    server._enqueue_detect(P, 2)                     # waits: both workers are on people's pages
    server._enqueue_detect_bg(A, 0)
    check(not bg_running() and len(people_running()) == 2,
          "3: with a person's page waiting, no e-mailed page starts", H.running(server).keys())
    done = [p for p in (H.running(server)[(P, 0)]["proc"],)]
    done[0].finish()
    H.wait_until(lambda: (P, 2) in H.running(server))
    check((P, 2) in H.running(server) and not bg_running(),
          "3: a freed worker goes to the waiting person's page, not to the e-mailed one", H.running(server).keys())
    H.finish_running(server, only_bg=False)
    check(H.wait_until(lambda: bg_running() == [(A, 0)]), "3: the e-mailed page runs when nobody's page waits",
          H.running(server).keys())
    H.drain(server)


def t4_a_person_asks_for_an_emailed_page():
    reset(1)
    for p in range(3):
        server._enqueue_detect_bg(A, p)
    owner_free = H.call(server, "GET", f"/api/job/{A}/page/2/status")
    check(owner_free[1].get("status") == "running" and owner_free[1].get("queued") is True
          and owner_free[1].get("email") is True, "6: the status route says a waiting e-mailed page is in the e-mail line",
          owner_free)
    st, j = H.call(server, "POST", f"/api/job/{A}/page/2/detect")
    with server._SCHED:
        line, queue_ = list(server._SCHED_BG), list(server._SCHED_QUEUE)
    run_now = H.running(server)
    check(st == 200 and (A, 2) in run_now and not run_now[(A, 2)].get("bg") and (A, 2) not in line,
          "4: pressing Detect on a page waiting in the e-mail line runs it as a person's page, at once", (st, j, list(run_now), line))
    check(line[0] == (A, 0) and not queue_, "4: the e-mailed page it displaced went back to the front of the line", (line, queue_))
    st, j = H.call(server, "GET", f"/api/job/{A}/page/2/status")
    check(j.get("email") is not True, "6: once a person asked for it the page is no longer an e-mail-line page", j)
    # a page RUNNING in the e-mail line that a person asks for is theirs from then on: not stopped for another person
    H.drain(server)
    reset(1)
    server._enqueue_detect_bg(A, 5)
    H.call(server, "POST", f"/api/job/{A}/page/5/detect")
    mine = H.running(server)[(A, 5)]["proc"]
    server._enqueue_detect(P, 5)
    check(not mine.stopped and (A, 5) in H.running(server) and (P, 5) not in H.running(server),
          "4: a running e-mailed page a person asked for is not stopped for someone else's page (they queue as people do)",
          (mine.stopped, list(H.running(server))))
    H.drain(server)


def t5_deleted_projects():
    reset(1)
    gone = job("eeeeeeeeeee1")
    for p in range(3):
        server._enqueue_detect_bg(gone, p)
    child = H.running(server)[(gone, 0)]["proc"]
    server._sched_drop_bg(gone)
    with server._SCHED:
        line = list(server._SCHED_BG)
    check(child.stopped and not H.running(server) and not line,
          "5: deleting a project stops its running e-mailed page and empties its part of the line", (child.stopped, line))
    # the route does the same, before it empties the folder
    gone2 = job("fffffffffff1")
    for p in range(2):
        server._enqueue_detect_bg(gone2, p)
    child2 = H.running(server)[(gone2, 0)]["proc"]
    st, _j = H.call(server, "DELETE", f"/api/job/{gone2}")
    with server._SCHED:
        line = list(server._SCHED_BG)
    check(st == 200 and child2.stopped and not line and not (Path(server.JOBS_DIR) / gone2).exists(),
          "5: DELETE /api/job stops the e-mailed page first: nothing is written into the folder after it is gone", (st, line))
    # a page whose project vanished while it waited: dropped, and the watcher thread lives on
    reset(1)
    server._enqueue_detect(P, 0)                     # holds the one worker
    ghost = job("0a0a0a0a0a01")
    server._enqueue_detect(ghost, 0)                 # a person's page, waiting
    server._enqueue_detect_bg(ghost, 1)              # and an e-mailed one
    server.JOBS.pop(ghost)
    H.finish_running(server)
    time.sleep(0.6)
    alive = bool(server._SCHED_THREAD) and server._SCHED_THREAD[0].is_alive()
    server._enqueue_detect(Q, 0)
    check(alive and (Q, 0) in H.running(server),
          "5: a queued page whose project was deleted is dropped; the watcher thread is alive and the next page starts",
          (alive, list(H.running(server))))
    H.drain(server)


def t6_real_children_are_stopped():
    """The stop itself, on a REAL child process: terminate() ends a detection that is in the middle of its work."""
    import multiprocessing
    ctx = multiprocessing.get_context("spawn")
    p = ctx.Process(target=time.sleep, args=(60,), daemon=True)
    p.start()
    reset(1)
    key = (A, 7)
    with server._SCHED:
        server.JOBS[A]["status"][7] = "running"
        server._SCHED_RUNNING[key] = {"proc": p, "t0": time.time(), "bg": True}
    t0 = time.time()
    server._enqueue_detect(P, 7)
    took = time.time() - t0
    alive = True
    try:
        alive = p.is_alive()
    except ValueError:                               # the scheduler closed it: it is gone
        alive = False
    check(not alive and (P, 7) in H.running(server) and took < 6.0,
          f"2: a real child process holding the worker is ended and the person's page starts ({took * 1000:.0f} ms)",
          (alive, took))
    H.drain(server)
    with server._SCHED:
        server._SCHED_BG.clear()


for t in (t1_one_at_a_time, t2_a_person_never_waits, t3_people_first, t4_a_person_asks_for_an_emailed_page,
          t5_deleted_projects, t6_real_children_are_stopped):
    run(t)

print(f"\n{checks} checks, {fails} failure(s)")
sys.exit(1 if fails else 0)
