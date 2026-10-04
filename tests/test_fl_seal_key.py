"""THE SEAL'S KEY (critic 6, M1, 2026-10-02) AND THE WORDS WHEN A SEAL FAILS (critic 6, minor 1).

An emptied or truncated `fl_seal.key` was used as the HMAC key: an empty key makes a seal anyone can
compute. Now a key file under 32 bytes is never used - nothing is sealed, every pick is held with "the
store's seal key is damaged: ask us", and one line is logged. A missing key is created atomically, and
processes creating it at once end with one key. A failing seal names its cause: the drawing changed,
the store's key changed, or the stored figures were changed. FAILS on 7c29456.

    python tests/test_fl_seal_key.py
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_JOBS = Path(tempfile.mkdtemp(prefix="flkey_"))
os.environ["TAKEOFF_JOBS_DIR"] = str(_JOBS)
os.environ["TAKEOFF_LEDGER_DIR"] = tempfile.mkdtemp(prefix="flkey_lg_")
os.environ.pop("TAKEOFF_UI_PASSWORD", None)

import pymupdf  # noqa: E402

J = "flk000000001"
d = pymupdf.open()
d.new_page(width=612, height=792)
pdf = _JOBS / "upload_00000000000b_one-sheet.pdf"
d.save(str(pdf))
d.close()
(_JOBS / J).mkdir()
(_JOBS / J / "p0_result.json").write_text(json.dumps({"page": {"width_pt": 612, "height_pt": 792}, "styles": [], "chains": [],
                                                       "openings": [], "scale": {"ft_per_paper_inch": 4.0}}))
(_JOBS / "registry.json").write_text(json.dumps({J: {"path": str(pdf), "pages": 1, "mtime": round(pdf.stat().st_mtime, 2),
                                                     "kinds": None, "sizes": [[612, 792]], "owner": None, "bid": None}}))

import server  # noqa: E402
from takeoff_engine import fl_job as _fj  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {str(got)[:400]})"))
    if not ok:
        fails += 1


KEY = _JOBS / "fl_seal.key"


def fresh():
    server._FL_SEAL_KEY.clear()


ROOM = _fj.clean_room({"uid": "r1", "source": "click", "pts": [[100, 100], [388, 100], [388, 244], [100, 244]], "area_pt2": 41000.0,
                       "scale": {"how": "sheet", "ft": 4.0}})

print("a missing key is created, atomically, and used")
fresh()
check(not KEY.exists(), "no key yet")
k1 = server._fl_seal_key()
check(KEY.exists() and len(KEY.read_bytes().strip()) == 64 and k1 == KEY.read_bytes().strip(), "created: 64 hex characters, the one in use")
check(not list(_JOBS.glob("fl_seal.key.*.tmp")), "no temp file left behind")

print("a damaged key file is never used")
for content, what in ((b"", "an empty file"), (b"0123456789", "a 10-byte file")):
    KEY.write_bytes(content)
    fresh()
    try:
        server._fl_seal_key()
        got = "used"
    except server.FlSealKeyDamaged:
        got = "refused"
    check(got == "refused", f"{what}: refused as a key", got)
    fresh()
    check(server._fl_verify(J, "acct", dict(ROOM, seal="x"), 0) == _fj.KEY_DAMAGED_WHY,
          f"{what}: a sealed room reads 'the store's seal key is damaged: ask us'")
    doc = {"v": _fj.DOC_V, "est": _fj.default_est(True), "sheets": {"0": {"rooms": [dict(ROOM)], "aids": {}, "seq": 2}}, "migrated": None}
    fresh()
    server._fl_doc_write(J, "acct", 1, doc)
    st = json.loads(server._fl_est_file(J, "acct").read_text(encoding="utf-8"))
    check(st["doc"]["sheets"]["0"]["rooms"][0]["seal"] is None, f"{what}: nothing is sealed with it (the room is written unsealed)")
    check(KEY.read_bytes() == content, f"{what}: the file is left as it was (never replaced silently)")
    fresh()
    g = server._fl_read_doc(J, "acct", st["doc"])
    check(g["sheets"]["0"]["rooms"][0]["unverified"] == _fj.KEY_DAMAGED_WHY, f"{what}: on read every pick is held, said")

print("processes creating the key at once end with one key")
KEY.unlink()
code = ("import os,sys;sys.path.insert(0,r'%s');os.environ['TAKEOFF_JOBS_DIR']=r'%s';import server;"
        "sys.stdout.write(server._fl_seal_key().decode())") % (ROOT, _JOBS)
procs = [subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) for _ in range(4)]
keys = [p.communicate(timeout=300)[0].decode().strip()[-64:] for p in procs]
check(len(set(keys)) == 1 and keys[0] == KEY.read_bytes().strip().decode() and len(keys[0]) == 64,
      "four first creates at once: one key, the one on file", keys)

print("a failing seal names its cause")
fresh()
doc = {"v": _fj.DOC_V, "est": _fj.default_est(True), "sheets": {"0": {"rooms": [dict(ROOM)], "aids": {}, "seq": 2}}, "migrated": None}
server._fl_doc_write(J, "acct", 2, doc)
st = json.loads(server._fl_est_file(J, "acct").read_text(encoding="utf-8"))
r = st["doc"]["sheets"]["0"]["rooms"][0]
check(server._fl_verify(J, "acct", _fj.clean_room(r), 0) is True, "sealed now: it verifies")
r2 = dict(r, area_pt2=r["area_pt2"] * 10)
check(server._fl_verify(J, "acct", _fj.clean_room(r2), 0) is False, "its figures changed on disk: the stored-figures words (False)")
with open(pdf, "ab") as fh:
    fh.write(b"\n% a changed drawing\n")
server._FL_PDF_HASH.clear()
check(server._fl_verify(J, "acct", _fj.clean_room(r), 0) == _fj.PDF_WHY, "the PDF changed: 'the drawing behind this job changed'")
d = pymupdf.open()
d.new_page(width=612, height=792)
d.save(str(pdf))
d.close()
server._FL_PDF_HASH.clear()
KEY.write_bytes(b"f" * 64)
fresh()
got = server._fl_verify(J, "acct", _fj.clean_room(r), 0)
check(got in (_fj.KEY_WHY, _fj.PDF_WHY), "a new key: 'this store's seal key changed' (or the drawing, re-saved here)", got)

print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
