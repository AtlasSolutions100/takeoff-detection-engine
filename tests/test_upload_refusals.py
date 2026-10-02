"""WHAT AN UPLOAD REFUSES (2026-09-26; audit D35).

A damaged PDF used to answer 500 from inside pymupdf and leave its bytes
behind as an orphan upload_<hash>_<name>; a .txt was accepted as a job.
Now the bytes are checked BEFORE anything is written: not a .pdf name,
not PDF bytes, a file pymupdf cannot open, or no pages - each a 400 with
the reason, and the store is exactly as it was. A real PDF still lands.

    python tests/test_upload_refusals.py     (fails on the commit before it)
"""
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
JOBS = Path(tempfile.mkdtemp(prefix="upr_")).resolve()
os.environ["TAKEOFF_JOBS_DIR"] = str(JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="upr_lg_")

import pymupdf  # noqa: E402
from starlette.datastructures import UploadFile  # noqa: E402

import server  # noqa: E402

fails = 0


def check(ok, msg):
    global fails
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        fails += 1


class _Req:
    def __init__(self, user=None, staff=False):
        self.scope = {"tk_user": user, "tk_staff": staff}


def upload(raw: bytes, name: str):
    f = UploadFile(io.BytesIO(raw), filename=name, size=len(raw))
    try:
        return 200, asyncio.run(server.upload(f, _Req(), classify=0))
    except Exception as e:      # noqa: BLE001
        return getattr(e, "status_code", 500), getattr(e, "detail", str(e))


def uploads_on_disk():
    return sorted(p.name for p in JOBS.iterdir() if p.name.startswith("upload_"))


good = pymupdf.open()
good.new_page(width=400, height=600)
good_bytes = good.tobytes()
good.close()

cases = [
    (b"%PDF-1.4\nsmoke audit: truncated, not a real PDF\n", "damaged.pdf", "damaged"),
    (b"", "empty.pdf", "empty"),
    (b"hello, not a pdf", "note.pdf", "not PDF bytes"),
    (good_bytes, "plans.txt", "a PDF named .txt"),
]
before = uploads_on_disk()
for raw, name, what in cases:
    st, body = upload(raw, name)
    check(st == 400, f"{what}: answers 400, not 500 ({st}: {str(body)[:70]})")
    check(isinstance(body, str) and body, f"{what}: says why in words")
check(uploads_on_disk() == before, f"nothing was written for a refused upload ({uploads_on_disk()})")
check(not [p for p in JOBS.iterdir() if p.is_dir() and p.name != "cutfill"], "no orphan job folder either")

st, body = upload(good_bytes, "plans.pdf")
check(st == 200 and isinstance(body, dict) and body.get("pages") == 1, f"a real PDF still lands ({st}, {body})")
check(len(uploads_on_disk()) == 1 and uploads_on_disk()[0].endswith("_plans.pdf"), "and is written under its content hash")
st2, body2 = upload(good_bytes, "PLANS.PDF")
check(st2 == 200, "the extension test ignores case")

if fails:
    print(f"{fails} failure(s)")
    sys.exit(1)
print("all upload-refusal checks passed")
