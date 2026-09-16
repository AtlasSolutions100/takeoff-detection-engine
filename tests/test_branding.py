"""The company name that brands the report PDF, pinned.

Same import technique as tests/test_projects_api.py: TAKEOFF_JOBS_DIR is
pointed at a disposable temp dir BEFORE server import, then the route
functions are called directly with a stub Request (all they read is
request.scope).

What must not regress:

  * an UNBRANDED box is unchanged. resolve() on a missing file returns a
    blank record and scope "none", and report_pdf's letterhead draws
    nothing - the PDF is the one the endpoint made before branding existed.
  * an OLD CLIENT still works. Both body fields are optional, so a POST of
    {} or of {"company": ...} alone is accepted and the missing field reads
    as "".
  * one account's brand never leaks onto another account's report. That is
    the whole reason this is not a single install-wide string.
  * a blank record SAVED by an account is a decision ("stamp nothing") and
    outranks the install default, rather than falling through to it.
  * a corrupt branding.json costs the letterhead, never the numbers.

Dependency-free beyond the venv:  python tests/test_branding.py
"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(prefix="branding_test_")

import server  # noqa: E402
from takeoff_engine import branding  # noqa: E402

CHECKS = []


def ok(cond, msg, got=None):
    CHECKS.append((bool(cond), msg, got))


class _Req:
    """The routes read request.scope and nothing else."""

    def __init__(self, user=None, staff=False):
        self.scope = {"tk_user": user, "tk_staff": staff}


class _Body:
    """Stands in for BrandingBody; the route only reads the two attributes."""

    def __init__(self, company=None, contact=None):
        self.company = company
        self.contact = contact


def wipe():
    p = branding.store_path()
    if p.exists():
        p.unlink()


# --------------------------------------------------------------- clean()
ok(branding.clean(None) == {"company": "", "contact": ""},
   "clean: a non-dict is a blank record, not an exception")
ok(branding.clean({"company": 7, "contact": ["x"]})
   == {"company": "", "contact": ""},
   "clean: non-string fields read as empty")
ok(branding.clean({"company": "  Ryno   Construction \n LLC  "})["company"]
   == "Ryno Construction LLC",
   "clean: newlines and runs of whitespace collapse to single spaces",
   branding.clean({"company": "  Ryno   Construction \n LLC  "})["company"])
_long = branding.clean({"company": "R" * 500, "contact": "C" * 500})
ok(len(_long["company"]) == branding.COMPANY_MAX
   and len(_long["contact"]) == branding.CONTACT_MAX,
   "clean: both fields are capped at their documented maximum")
ok("\n" not in branding.clean({"contact": "a\r\nb"})["contact"],
   "clean: a CRLF in the contact line cannot reach a PDF insert_text call")

# ---------------------------------------------------- the unbranded box
wipe()
rec, scope = branding.resolve(None)
ok(rec == {"company": "", "contact": ""} and scope == "none",
   "unbranded: no file -> blank record, scope 'none'", (rec, scope))
rec, scope = branding.resolve("bill")
ok(rec["company"] == "" and scope == "none",
   "unbranded: no file -> blank for a named account too")
got = server.get_branding(_Req())
ok(got["company"] == "" and got["contact"] == "" and got["scope"] == "none",
   "unbranded: GET /api/branding answers blank rather than 404ing", got)
ok(got["seat"] == "install",
   "unbranded: with no user, a save would go to the install record")
ok(got["company_max"] == branding.COMPANY_MAX
   and got["contact_max"] == branding.CONTACT_MAX,
   "GET reports the caps, so the UI does not hard-code them")

# --------------------------------------------- single-password (install)
wipe()
out = server.post_branding(_Body(company="Work Optional", contact="512-555"),
                           _Req())
ok(out["scope"] == "install" and out["company"] == "Work Optional",
   "install: with no signed-in user the record is written install-wide", out)
ok(server.get_branding(_Req())["company"] == "Work Optional",
   "install: and reads back")
ok(server.get_branding(_Req())["scope"] == "install",
   "install: scope says where it came from")

# ------------------------------------------------------- per account
wipe()
server.post_branding(_Body(company="House Default"), _Req())
ok(server.get_branding(_Req(user="bill"))["company"] == "House Default",
   "seed: an account that has never saved inherits the install record")
ok(server.get_branding(_Req(user="bill"))["scope"] == "install",
   "seed: ...and is TOLD it is inherited, not told it is its own")

server.post_branding(_Body(company="Bill Excavating"), _Req(user="bill"))
ok(server.get_branding(_Req(user="bill"))["company"] == "Bill Excavating",
   "account: a saved record wins over the install default")
ok(server.get_branding(_Req(user="bill"))["scope"] == "account",
   "account: scope says 'account'")
ok(server.get_branding(_Req(user="jeff"))["company"] == "House Default",
   "ISOLATION: bill's company never appears on jeff's seat")
ok(server.get_branding(_Req())["company"] == "House Default",
   "ISOLATION: nor on the install record")

server.post_branding(_Body(company="ECI Foundations"), _Req(user="jeff"))
ok(server.get_branding(_Req(user="bill"))["company"] == "Bill Excavating"
   and server.get_branding(_Req(user="jeff"))["company"] == "ECI Foundations",
   "ISOLATION: two accounts hold two different companies at once")

# a blank SAVED by an account means "stamp nothing", not "fall back"
server.post_branding(_Body(company="", contact=""), _Req(user="bill"))
got = server.get_branding(_Req(user="bill"))
ok(got["company"] == "" and got["scope"] == "account",
   "clear: an explicitly blank account record outranks the install default",
   got)
ok(server.get_branding(_Req(user="jeff"))["company"] == "ECI Foundations",
   "clear: and clearing one account does not touch another")

# ------------------------------------------------- the old-client contract
wipe()
server.post_branding(_Body(company="Old Client Co", contact="x@y.z"), _Req())
out = server.post_branding(_Body(company="Only Company"), _Req())
ok(out["company"] == "Only Company" and out["contact"] == "",
   "old client: a payload with no `contact` is accepted; the field reads ''",
   out)
out = server.post_branding(_Body(), _Req())
ok(out["company"] == "" and out["contact"] == "",
   "old client: an EMPTY payload is accepted and clears, never 422s", out)

# ------------------------------------------------------- corrupt on disk
wipe()
branding.store_path().write_text("{ this is not json", encoding="utf-8")
rec, scope = branding.resolve("bill")
ok(rec == {"company": "", "contact": ""} and scope == "none",
   "corrupt: an unreadable branding.json costs the letterhead, not a 500")
branding.store_path().write_text('["a list"]', encoding="utf-8")
ok(branding.resolve(None)[1] == "none",
   "corrupt: a JSON value of the wrong shape is the same")
branding.store_path().write_text(
    json.dumps({"install": {"company": "Fine"},
                "accounts": {"bill": "not a record", "": {"company": "x"}}}),
    encoding="utf-8")
ok(branding.resolve("bill")[0]["company"] == "",
   "corrupt: a malformed ACCOUNT record reads as an explicit blank, not as "
   "the install default (the key is present, so the account has a record)")
ok(branding.resolve("jeff")[0]["company"] == "Fine",
   "corrupt: one bad account record does not poison the others")
ok("" not in branding.load()["accounts"],
   "corrupt: an empty username key is dropped rather than shadowing None")

# ---------------------------------------------------------- atomic write
wipe()
server.post_branding(_Body(company="A"), _Req(user="bill"))
server.post_branding(_Body(company="B"), _Req(user="bill"))
leftovers = list(branding.store_path().parent.glob("branding.json.*.tmp"))
ok(not leftovers, "atomic: no tmp file is left behind", leftovers)
ok(json.loads(branding.store_path().read_text())["accounts"]["bill"]["company"]
   == "B", "atomic: the second write replaced the first cleanly")

# --------------------------------------------- report_pdf takes a Request
import inspect  # noqa: E402

_sig = inspect.signature(server.report_pdf)
ok("request" in _sig.parameters,
   "report_pdf takes the Request it needs to resolve WHOSE company to stamp")
_src = inspect.getsource(server.report_pdf)
ok("_letterhead" in _src and _src.count("_letterhead(tp)") == 6,
   "report_pdf: every text page it creates gets the letterhead call "
   "(4, plus the bid-sheet page and its overflow since 2026-09-16)",
   _src.count("_letterhead(tp)"))
ok("if not _bco:" in _src,
   "report_pdf: a blank company draws nothing at all")
ok("BETA_LONG" in _src,
   "report_pdf: the beta band is still stamped - branding did not displace it")

# ---------------------------------------------------------------- report
fails = [c for c in CHECKS if not c[0]]
for good, msg, got in CHECKS:
    print(("ok:   " if good else "FAIL: ") + msg
          + ("" if good or got is None else f"   got={got!r}"))
print(f"\n{len(CHECKS) - len(fails)}/{len(CHECKS)} checks passed")
if fails:
    sys.exit(1)
