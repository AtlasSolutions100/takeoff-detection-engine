"""The demo tab reaches a hosted box - the two things that stopped it.

Both of these shipped together and either one alone empties the Demo tab, so
both get pinned here.

1. THE MANIFEST HAS TO BE IN THE IMAGE. /api/demo-plans renders from
   demo-plans.json and nothing else. The Dockerfile copied server.py and
   webui/ and not that file, so on the box _load_demo_plans took OSError,
   DEMO_PLANS was {} and the endpoint answered with zero cards - on a box that
   would have had every plan uploaded. Nothing failed loudly; the tab was just
   empty.

2. A SEEDED ROW HAS TO BE SHARED. _demo_source only considers rows with
   owner None, but /api/upload stamps the uploader as owner, so seeding while
   signed in produced seven rows the demo resolver skips and seven cards that
   read "not on this box". Staff uploading a plan the manifest already names
   is now what makes a shared row.

The second one is a PUBLISHING rule, so its edges are the point of the test:
only staff, and only an exact manifest name.

    python tests/test_demo_seed.py
"""

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="demo_seed_test_")

import server  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


class _Req:
    def __init__(self, user=None, staff=False):
        self.scope = {"tk_user": user, "tk_staff": staff}


class _Upload:
    """Enough of starlette's UploadFile for the route: a name and bytes."""

    def __init__(self, filename, data):
        self.filename = filename
        self._data = data

    async def read(self):
        return self._data


# A one-page PDF, built here so the test needs no fixture on disk.
def _pdf():
    import pymupdf
    d = pymupdf.open()
    d.new_page(width=612, height=792)
    return d.tobytes()


def upload_as(name, req, data):
    return asyncio.run(server.upload(_Upload(name, data), req, classify=0))


# --- 1. the manifest ships --------------------------------------------------
docker = (REPO / "Dockerfile").read_text(encoding="utf-8")
copied = set()
for line in docker.splitlines():
    m = re.match(r"\s*COPY\s+(\S+)", line)
    if m:
        copied.add(m.group(1))
ok("demo-plans.json" in copied,
   "Dockerfile COPYs demo-plans.json (without it the Demo tab is empty)",
   sorted(copied))

manifest = json.loads((REPO / "demo-plans.json").read_text(encoding="utf-8"))["plans"]
ok(server.DEMO_PLANS and len(server.DEMO_PLANS) == len(manifest),
   "server loads every manifest plan",
   "%d loaded / %d in file" % (len(server.DEMO_PLANS), len(manifest)))

# --- 2. who gets a shared row ----------------------------------------------
demo_name = sorted(manifest)[0]
blob = _pdf()

r = upload_as(demo_name, _Req(user="koby@x.com", staff=True), blob)
ok(server.JOBS[r["job_id"]].get("owner") is None,
   "staff uploading a manifest plan makes a SHARED row (the seeding case)",
   server.JOBS[r["job_id"]].get("owner"))
ok(server._demo_source(demo_name) is not None,
   "and _demo_source can then find it, so the card goes live")

r = upload_as("some-client-bid.pdf", _Req(user="koby@x.com", staff=True), blob + b"%x")
ok(server.JOBS[r["job_id"]].get("owner") == "koby@x.com",
   "staff uploading anything NOT in the manifest keeps it private",
   server.JOBS[r["job_id"]].get("owner"))

r = upload_as(demo_name, _Req(user="rival@x.com", staff=False), blob + b"%y")
ok(server.JOBS[r["job_id"]].get("owner") == "rival@x.com",
   "a NON-staff upload named after a demo is never published",
   server.JOBS[r["job_id"]].get("owner"))

r = upload_as("plans.pdf", _Req(user=None, staff=False), blob + b"%z")
ok(server.JOBS[r["job_id"]].get("owner") is None,
   "single-password mode is unchanged (no user, so no owner)",
   server.JOBS[r["job_id"]].get("owner"))

# --- 3. the provisioner agrees with the manifest ---------------------------
sys.path.insert(0, str(REPO))
import provision_demo  # noqa: E402

shared = provision_demo._demo_names()
missed = sorted(n for n in manifest if n not in shared
                and n.casefold() not in provision_demo._shared_names())
ok(not missed,
   "provision_demo shares every demo card (it used to hand 3 of 7 to staff)",
   missed)

# --- report -----------------------------------------------------------------
bad = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print("  %s %s%s" % ("PASS" if good else "FAIL", msg,
                         "" if good or got is None else "\n        got: %r" % (got,)))
print("\n%d/%d passed" % (len(CHECKS) - len(bad), len(CHECKS)))
sys.exit(1 if bad else 0)
