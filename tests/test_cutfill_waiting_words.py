"""A CUT / FILL READ WAITING FOR ANOTHER SAYS IT IS WAITING IN LINE (Koby, 2026-10-01: "When a client's cut / fill is
waiting for another one, the screen should say it is waiting in line.").

The status payload the engine page and the host page poll (GET /cutfill/api/projects/<pid>/pages/<key>/analysis,
cutfill/server.py `analysis`) carries the words; tests/test_cutfill_waiting_words_ui.js pins how both pages show them.
Pinned here (the route function is called directly: the venv has no httpx for a TestClient):
  1. a read waiting for another process's read: {"status": "queued", "progress": "Waiting in line. Another sheet is being
     read first."}, the whole time it waits (no time-out turns it into an error), then done
  2. the host's own queue (several sheets opened at once) uses the same words
  3. the two sibling states say something plain and different: "... is being stopped." and "The last read of this sheet
     is still stopping. Try again in a minute."

    python tests/test_cutfill_waiting_words.py     (fails on the commit before it)
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="cfwait_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="cfwait_lg_")
os.environ.pop("CUTFILL_OUT", None)
for v in ("CUTFILL_MAX_READS", "CUTFILL_READ_MAX_SECONDS", "CUTFILL_READ_STOP_GRACE_SECONDS"):
    os.environ.pop(v, None)

import pymupdf  # noqa: E402

import server  # noqa: E402  (sets CUTFILL_OUT to <jobs>/cutfill, as the product does)
import cutfill.project as P  # noqa: E402
import cutfill.readslot as RS  # noqa: E402
import cutfill.server as CS  # noqa: E402

fails = 0
WORDS = "Waiting in line. Another sheet is being read first."


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg, flush=True)
    if not ok:
        fails += 1


def wait_for(cond, secs=120.0):
    t0 = time.time()
    while time.time() - t0 < secs:
        if cond():
            return True
        time.sleep(0.05)
    return cond()


check(RS.WAITING == WORDS, f"the engine's words for a read waiting for a slot: {RS.WAITING!r}")
check(server._CF_WAIT == WORDS, f"the host's queue says the same: {server._CF_WAIT!r}")
check(RS.stopping_words(900) == "This sheet took more than 15 minutes to read and is being stopped."
      and RS.STILL_STOPPING == "The last read of this sheet is still stopping. Try again in a minute."
      and len({WORDS, RS.stopping_words(900), RS.STILL_STOPPING}) == 3,
      "the sibling states say something plain and different (being stopped / still stopping)")

HOLDER = r'''
import os, sys, time
sys.path.insert(0, sys.argv[1])
os.environ["CUTFILL_OUT"] = sys.argv[2]
from cutfill.readslot import read_slot
with read_slot():
    open(sys.argv[3], "w").write("held")
    time.sleep(600)
'''

doc = pymupdf.open()
pg = doc.new_page(width=612, height=792)
pg.insert_text((72, 72), "GRADING PLAN 100", fontsize=8)
pg.draw_line((100, 100), (300, 100))
pdf = JOBS / "sheet.pdf"
doc.save(str(pdf))
doc.close()
proj = P.Project.create("waiting", pid="waittest")
proj.add_document(str(pdf))
proj.page_settings("d0p0")["role"] = "both"
proj.save()
CS._projects["waittest"] = proj
host_ok, CS.PROJECT_OK = CS.PROJECT_OK, None       # the engine alone: the host's registry knows no such job

flag = JOBS / "held"
child = subprocess.Popen([sys.executable, "-c", HOLDER, str(ROOT), os.environ["CUTFILL_OUT"], str(flag)])
try:
    wait_for(flag.exists, 300)
    r = CS.analyze("waittest", "d0p0")
    wait_for(lambda: CS.analysis("waittest", "d0p0").get("progress") == WORDS)
    got = CS.analysis("waittest", "d0p0")
    check(got == {"status": "queued", "progress": WORDS},
          f"GET .../analysis of a read waiting for another: {got}")
    time.sleep(3.0)                                   # it may wait for minutes: the words stay, nothing times out
    got2 = CS.analysis("waittest", "d0p0")
    check(got2 == {"status": "queued", "progress": WORDS}, f"still the same words while it waits: {got2}")
finally:
    child.kill()
    child.wait()
wait_for(lambda: CS.analysis("waittest", "d0p0")["status"] not in ("queued", "running"), 360)
got = CS.analysis("waittest", "d0p0")
check(got["status"] == "done", f"once the other read ends, the sheet is read: {got['status']}")
proj.close()
CS._projects.pop("waittest", None)
CS.PROJECT_OK = host_ok

print(f"\n{'FAILED' if fails else 'passed'}: {fails} failure(s)")
sys.exit(1 if fails else 0)
