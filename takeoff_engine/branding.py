"""The operator's own company name, for the branded takeoff PDF.

WHY THIS IS SERVER-SIDE AND NOT ANOTHER localStorage KEY. Every other
Settings value in this app is a preference of one browser: a theme, a
folded panel, the standing trade default. This one is not. The thing it
brands is `report_pdf` in server.py, which renders on the server, from the
approved elements on the server, and hands back a file. A company name kept
in the browser could only reach that PDF by being posted along with the
request - which means the same job exported from a second seat, from a
phone, or by a colleague comes back unbranded or branded with somebody
else's name, and nobody would find out until a client had the wrong PDF.
The deliverable is generated in one place, so the brand on it is stored in
that place.

WHERE IT LIVES. `branding.json`, beside `users.json` in the job store, for
the same reason users.py gives: it survives a redeploy on the same volume.

WHOSE NAME. Two scopes, because this box is not single-tenant:

  accounts[<user>]  the record for one signed-in account. Present-key wins,
                    even when blank - an account that deliberately clears
                    its company must not silently inherit the install's.
  install           the record used when no accounts are configured (the
                    laptop / single-password case), and as the seed for an
                    account that has never saved one.

Stamping one install-wide name would put one client's company on another's takeoff
the first time two clients share a server, which is exactly the leak
users.py exists to prevent.

INERT BY DEFAULT. With no file, `resolve()` returns a blank record and
scope "none", and report_pdf stamps nothing - byte-for-byte the PDF it
produced before this module existed.
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

# Caps, not validation. These are self-declared strings; there is no such
# thing as a wrong company name. The only thing that can go wrong is a
# string long enough to run off the page, so it is cut to what the header
# band can print at its font size and the UI says so before you type it.
COMPANY_MAX = 80
CONTACT_MAX = 120


def store_path() -> Path:
    """Beside the user file, in the job store."""
    root = os.environ.get("TAKEOFF_JOBS_DIR")
    base = (Path(root).expanduser() if root
            else Path(__file__).parent.parent / "out" / "jobs")
    return base / "branding.json"


def blank() -> dict:
    return {"company": "", "contact": ""}


def clean(rec) -> dict:
    """One record, normalised. Never raises and never returns None: a
    malformed record has to read as "no brand", because the alternative is
    a 500 on the endpoint that generates the product's only deliverable.

    Newlines are flattened to spaces. A PDF header is one insert_text call
    at one baseline, so an embedded newline does not wrap - it prints as a
    box glyph or vanishes, and either way the operator sees a name they did
    not type on a document they are about to send a client.
    """
    if not isinstance(rec, dict):
        return blank()

    def one(key: str, cap: int) -> str:
        v = rec.get(key)
        if not isinstance(v, str):
            return ""
        for ch in ("\r", "\n", "\t"):
            v = v.replace(ch, " ")
        return " ".join(v.split())[:cap]

    return {"company": one("company", COMPANY_MAX),
            "contact": one("contact", CONTACT_MAX)}


def load() -> dict:
    """{"install": rec, "accounts": {user: rec}}.

    Empty-but-valid on a missing, unreadable or malformed file - the same
    fail-open discipline as users.load(). A truncated branding file must
    cost the report its letterhead, never its numbers.
    """
    out = {"install": blank(), "accounts": {}}
    try:
        raw = json.loads(store_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if not isinstance(raw, dict):
        return out
    out["install"] = clean(raw.get("install"))
    accts = raw.get("accounts")
    if isinstance(accts, dict):
        for k, v in accts.items():
            if isinstance(k, str) and k:
                out["accounts"][k] = clean(v)
    return out


def resolve(user: str | None) -> tuple[dict, str]:
    """The record that applies to `user`, and where it came from.

    Scope is returned, not inferred by the caller, because the UI has to be
    able to say "this is your account's" versus "this is the one this box
    was set up with" in plain words. A blank record with scope "none" is
    the honest answer for a box nobody has branded.
    """
    d = load()
    if user and user in d["accounts"]:
        # present-key wins even when blank: an explicit clear is a decision
        return d["accounts"][user], "account"
    inst = d["install"]
    if inst["company"] or inst["contact"]:
        return inst, "install"
    return blank(), "none"


def save(user: str | None, rec) -> tuple[dict, str]:
    """Write one record and return it with the scope it was written to.

    Atomic via a uniquely-named tmp + os.replace, the same shape
    _calcstate_put uses in server.py: a crash mid-write must leave the
    previous branding intact rather than publish half a JSON file that
    load() would then read as "no brand at all".
    """
    rec = clean(rec)
    d = load()
    scope = "account" if user else "install"
    if user:
        d["accounts"][user] = rec
    else:
        d["install"] = rec
    f = store_path()
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f"{f.name}.{uuid.uuid4().hex[:8]}.tmp")
    tmp.write_text(json.dumps(d, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, f)
    return rec, scope
