"""Create the demo accounts, and - only when asked - tidy who owns the
owner-less plan rows.

Run INSIDE the app container, where /data/jobs is the live job store:

    docker compose exec -T app python provision_demo.py
        accounts + roster; the ownership pass is a DRY RUN that prints its diff
    docker compose exec app python provision_demo.py --rewrite-owners
        ...and applies that diff after a y/N (no -T: the prompt needs a terminal)
    docker compose exec -T app python provision_demo.py --rewrite-owners --yes
        unattended: -T has no terminal to answer on, so --yes stands in for it

Add --reissue to reset EVERY listed account back to the shared starting
password and force another change. Without it, accounts that already exist
are left alone.

THE CUSTOMER ROSTER is a file in the job store, not a list in this script
(this script ships in the image): <jobs dir>/provision-accounts.txt, one
e-mail per line, or --accounts PATH. Without it only the staff accounts are
created; see ROSTER_NAME below.

Writes the credential list to /data/jobs/demo-credentials.txt (mode 600) and
prints nothing secret to stdout, so it is safe to run over a shared terminal
or from an automation log.

WHAT THIS TOOL IS NOT. It does not put demo plans on a box: seed-demo.sh
does that, through /api/upload as a staff account, and the upload route
makes a manifest-named staff upload SHARED at upload time (server.py
upload()). This tool never shares anything that is not already shared.

OWNERSHIP - THE RULE, in one sentence: a registry row that has an owner is
never rewritten, whoever the owner is and whatever the row is named.

The rows this tool may touch are the OWNER-LESS ones: "owner": null, or no
owner key at all (rows written before per-user mode; the server writes the
key as null on every save, so the two are one population and both mean
SHARED - see server._load_registry). Of those:

  * a demo card or a shared-set sheet (demo-plans.json, exact names) stays shared;
  * a shared row sitting over a file that a CLIENT row also points at is a
    leak minted by the pre-b528e74 demo scan: called out and left alone,
    and tools/demo_rows_audit.py is the thing that removes it;
  * everything else is a pre-login document that eighteen competing
    contractors can currently open, and it moves to the first STAFF account.

That last bullet is the only change this tool can make to registry.json,
and it is made ONLY under --rewrite-owners. Every run prints the diff (job
id, display name, owner before -> after); without the flag it stops at
"DRY RUN - nothing written" and registry.json is not even re-serialised.
With the flag it asks y/N, and the answer is taken BEFORE anything is
written: a "no", an unanswerable prompt (no terminal - docker compose exec
-T, a pipe, an automation log) or end-of-input all exit 2 with nothing
written, accounts included. --yes stands in for the answer.

HISTORY, because DEPLOY.md tells staff to re-run this on the box. Until
31914a2 (2026-09-02) the block decided on the display name alone and
rewrote every row: a client's "Belle Farms Structural Plans.pdf" was
published to every account and their "anything.pdf" moved to staff, so the
client's own job answered 404. 31914a2 stopped rewriting client rows, but
still rewrote rows owned by the SECOND staff account and moved every
owner-less row on every run, with no flag, no diff and no confirmation.
tests/test_provision_ownership.py pins both.

Restart the app after a --rewrite-owners run. Ownership lives in
registry.json, and the running process holds JOBS in memory, so an edit
here is not visible until it reloads. Account changes need no restart.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Not via __file__: this is sometimes piped in over stdin, where there is no
# __file__ to derive a path from.
sys.path.insert(0, os.getcwd())
from takeoff_engine import users  # noqa: E402

# Our own team (the contact addresses Terms and Privacy already publish).
# Staff see every document on the box and can delete any of them, which is
# how staff-only sets stay reachable for a demo after they stop being shared,
# and how a client's upload gets cleaned off afterwards.
STAFF = ["koby@radiantaisoftware.com", "christian@radiantaisoftware.com"]

# THE CUSTOMER LIST IS NOT IN THIS FILE (2026-10-01). It used to be a list
# here, and this file is COPY'd into the Docker image, so anybody holding the
# image could read who the eighteen competing customers are. It is now read
# at run time from a roster file that lives in the JOB STORE (a volume, never
# in the image or the repo): one e-mail address per line, "#" comments and
# blank lines ignored. Default <jobs dir>/provision-accounts.txt; --accounts
# PATH points elsewhere. No file = staff accounts only, and every account
# already in users.json is left exactly as it is. Accounts are also managed
# in the app (Settings -> Accounts) and with `python -m takeoff_engine.users`.
ROSTER_NAME = "provision-accounts.txt"


def _read_roster(path: Path) -> list[str] | None:
    """The customer addresses in `path`, in file order, lower-cased and
    de-duplicated; None when there is no such file. A line without an "@"
    is refused loudly rather than turned into an account."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    out: list[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip().lower()
        if not line:
            continue
        if "@" not in line or any(c.isspace() for c in line):
            raise SystemExit(f"!! {path}:{n}: not an e-mail address: {line!r} "
                             f"- nothing was written")
        if line not in out:
            out.append(line)
    return out

# Owner-less rows with these EXACT names (case aside) stay shared: the sheets of
# the Loves Lane civil set and Belle Farms as they are named on a seeded store.
# Every other owner-less row is a pre-login document, and eighteen of the
# accounts are competing contractors, so it moves to staff-only.
#
# The source of truth is demo-plans.json `shared_names` (the server's rule,
# server._shared_for_all, reads the same list); this tuple is only the fallback
# when that file cannot be read. They were PREFIXES until 2026-10-01, and a
# prefix also matched an owner-less "C4.0-SITE-GRADING-PLAN for <someone>.pdf"
# (critic bid-content-1, RISK-3). The demo card names are unioned in below.
SHARED_NAMES = (
    "C3.0-Site-Plan-Rev.1.pdf",
    "C4.0-SITE-GRADING-PLAN-Rev.1.pdf",
    "C4.0-SITE-GRADING-PLAN-Rev.1 (1).pdf",
    "C6.0-WATER-MAIN-PLAN-AND-PROFILE-Rev.1.pdf",
    "C8.0-STORM-LAYOUT-Rev.1.pdf",
    "C9.0-ROAD-LAYOUT-Rev.1.pdf",
    "C10.4-TYPICAL-ROAD-SECTIONS-Rev.1.pdf",
    "Belle Farms Structural Plans.pdf",
    "Belle Farms Upper Parcel ROAD GRADING 1 of 2.pdf",
    "Belle Farms Upper Parcel ROAD GRADING 2 of 2.pdf",
)


def _shared_names() -> set[str]:
    """demo-plans.json `shared_names`, case-folded; SHARED_NAMES when it cannot be read."""
    here = Path(__file__).parent if "__file__" in globals() else Path(os.getcwd())
    try:
        raw = json.loads((here / "demo-plans.json").read_text(encoding="utf-8"))
        got = raw.get("shared_names")
    except (OSError, ValueError, AttributeError):
        got = None
    if not isinstance(got, list):
        got = list(SHARED_NAMES)
    return {str(n).strip().casefold() for n in got if str(n).strip()}

# Everyone is issued the SAME starting password and must replace it at first
# sign-in. Twenty distinct random strings had to be transcribed and delivered
# by hand the night before a demo, which is its own kind of insecure; this is
# the standard onboarding trade instead. It only holds because the service
# refuses to serve anything but the change-password page until it is changed,
# and will not accept this word as the replacement.
#
# The residual risk, stated plainly: until a given person signs in, ANYONE who
# knows this word can sign in as them. Eighteen of these accounts belong to
# contractors who compete with each other. Chase stragglers.
DEFAULT_PASSWORD = "WorkOptional"


def _demo_names() -> set[str]:
    """Display names of the demo cards, straight from the manifest.

    Absent or unreadable returns empty, which leaves the prefix list in sole
    charge: this is an ownership tool, and it must not move every owner-less
    document on the box to staff because one JSON file failed to parse."""
    here = Path(__file__).parent if "__file__" in globals() else Path(os.getcwd())
    try:
        raw = json.loads((here / "demo-plans.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    plans = raw.get("plans")
    return set(plans) if isinstance(plans, dict) else set()


def _display(row: dict) -> str:
    """The name the operator sees: uploads are stored as upload_<hex>_<name>."""
    src = Path(row.get("path", "")).name
    return src.split("_", 2)[-1] if src.startswith("upload_") else src


def _resolved(row: dict) -> str:
    try:
        return os.path.normcase(str(Path(row.get("path", "")).resolve()))
    except OSError:
        return os.path.normcase(str(row.get("path", "")))


def _confirm(n: int) -> bool:
    """y/N at the terminal. End-of-input is a no: on Windows the null device
    reports itself as a terminal, so the isatty check in main() is not the
    only line of defence, and it must not be."""
    try:
        ans = input(f"rewrite these {n} row(s) in registry.json? [y/N] ")
    except EOFError:
        print()
        return False
    return ans.strip().lower() in ("y", "yes")


def _parse(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        prog="python provision_demo.py",
        description="Create the demo accounts and write the roster. The "
                    "ownership pass over registry.json is a dry run unless "
                    "--rewrite-owners is given; see the module docstring.")
    ap.add_argument("--reissue", action="store_true",
                    help="reset EVERY account to the issued password and force "
                         "a change at next sign-in (default: existing accounts "
                         "are left alone)")
    ap.add_argument("--rewrite-owners", action="store_true",
                    help="apply the ownership diff: move owner-less rows that "
                         "are not demo cards to the first staff account, after "
                         "a y/N. Without it the diff is printed and nothing in "
                         "registry.json is written.")
    ap.add_argument("--accounts", metavar="PATH", default=None,
                    help="the customer roster: one e-mail per line (default: "
                         "<jobs dir>/" + ROSTER_NAME + "). The list is kept "
                         "out of this file and out of the image on purpose.")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="with --rewrite-owners: skip the y/N. Required when "
                         "there is no terminal to answer on (docker compose "
                         "exec -T, a pipe, an automation log).")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse(sys.argv[1:] if argv is None else argv)
    # Re-issuing is deliberately opt-in. The default path must never silently
    # reset a password somebody is already relying on - that would knock a
    # contractor out of the account mid-demo with no warning.
    reissue, rewrite, yes = args.reissue, args.rewrite_owners, args.yes

    # Checked FIRST, before a single write: a --rewrite-owners run that could
    # never get its answer must not half-run (accounts and roster written,
    # registry silently untouched) and report success. This is the early,
    # well-worded refusal; _confirm's end-of-input is the one that holds
    # where isatty lies (the Windows null device).
    if rewrite and not yes and not sys.stdin.isatty():
        print("!! --rewrite-owners needs a terminal to answer y/N on, or --yes.\n"
              "   (docker compose exec -T has no terminal: drop -T, or add "
              "--yes.)\n"
              "   Nothing was written - not the registry, not the accounts.",
              file=sys.stderr)
        return 2

    jobs_dir = Path(os.environ.get("TAKEOFF_JOBS_DIR") or "/data/jobs")
    if not jobs_dir.is_dir():
        print(f"!! {jobs_dir} is not a directory - run this inside the app "
              f"container", file=sys.stderr)
        return 1

    # --- accounts: decided in memory here, written after the ownership
    # question is settled, so a refused --rewrite-owners run writes nothing.
    roster_path = Path(args.accounts) if args.accounts else jobs_dir / ROSTER_NAME
    clients = _read_roster(roster_path)
    if clients is None:
        print(f"no customer roster at {roster_path} - staff accounts only; "
              f"accounts already on the box are left as they are"
              + (" (--reissue resets staff only)" if reissue else ""))
        clients = []
    else:
        print(f"customer roster: {len(clients)} address(es) from {roster_path}")

    demo_names = _demo_names()
    shared_names = _shared_names() | {n.casefold() for n in demo_names}
    existing = users.load()
    store = dict(existing)
    creds: list[tuple[str, str, bool]] = []

    for name in STAFF + clients:
        name = name.strip().lower()
        if name in store and not reissue:
            # Never silently reset a password somebody is already using.
            creds.append((name, "(unchanged - account already existed)",
                          bool(store[name].get("staff"))))
            continue
        rec = users.hash_password(DEFAULT_PASSWORD)
        rec["must_change"] = True
        # staff is a property of the person, not of the password
        if name in [s.lower() for s in STAFF] or (
                name in store and store[name].get("staff")):
            rec["staff"] = True
        store[name] = rec
        creds.append((name, DEFAULT_PASSWORD, bool(rec.get("staff"))))

    # --- ownership -------------------------------------------------------
    # A ROW THAT HAS AN OWNER IS NEVER REWRITTEN. Client or staff, demo name
    # or not: it is refused, by name, owner and job id. The only rows this
    # pass may change are the owner-less ones, and the only change it can
    # make is owner-less-and-not-a-demo -> the first staff account. The
    # decision is made in full and printed as a diff BEFORE anything is
    # written, and it is written only under --rewrite-owners after a yes.
    reg_path = jobs_dir / "registry.json"
    kept = moved = left = leaks = 0
    changes: list[tuple[str, str, str, str]] = []
    reg: dict = {}
    if reg_path.exists():
        reg = json.loads(reg_path.read_text())
        new_owner = STAFF[0].lower()
        staff_names = {s.lower() for s in STAFF} | {
            n.lower() for n, rec in store.items() if rec.get("staff")}

        def is_client(cur) -> bool:
            return cur is not None and str(cur).strip().lower() not in staff_names

        # A shared (owner None) row over a file that a CLIENT row also
        # points at is a leak minted before the demo scan was fixed, not
        # ours to keep sharing: leave it, say so, and point at the tool
        # that removes it (tools/demo_rows_audit.py, DEPLOY.md).
        client_paths = {_resolved(r) for r in reg.values()
                        if is_client(r.get("owner"))}

        for jid, row in reg.items():
            disp = _display(row)
            cur = row.get("owner")
            if cur is not None:
                left += 1
                print(f"  refused: {disp!r} stays with its owner {cur!r} "
                      f"(job {jid}) - this tool never rewrites a row that "
                      f"has an owner")
                continue
            if _resolved(row) in client_paths:
                leaks += 1
                print(f"  WARNING: shared row {jid} ({disp!r}) sits over a "
                      f"client's file - left as is; run "
                      f"tools/demo_rows_audit.py --store <jobs dir> to purge it")
                continue
            if disp.casefold() in shared_names:
                kept += 1
                continue
            before = "None" if "owner" in row else "(no key)"
            changes.append((jid, disp, before, new_owner))

        print()
        if changes:
            print(f"OWNERSHIP DIFF - registry.json: {len(changes)} row(s) "
                  f"would change")
            for jid, disp, before, after in changes:
                print(f"  job {jid}  {disp!r}  owner {before} -> {after!r}  "
                      f"(owner-less and not a demo card: staff-only)")
        else:
            print("OWNERSHIP DIFF - registry.json: no row would change")
    else:
        print(f"no registry.json under {jobs_dir} - no plan rows to look at")

    wrote = False
    if changes and rewrite:
        if not (yes or _confirm(len(changes))):
            print("!! not confirmed - nothing written, not the registry and "
                  "not the accounts. Answer y at the prompt, or pass --yes "
                  "where there is no terminal to answer on.",
                  file=sys.stderr)
            return 2
        for jid, _disp, _before, after in changes:
            reg[jid]["owner"] = after
        tmp = reg_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(reg, indent=1))
        tmp.replace(reg_path)
        moved = len(changes)
        wrote = True
        print(f"  wrote {reg_path} ({moved} row(s) rewritten)")
    elif changes:
        print("  DRY RUN - nothing written. Re-run with --rewrite-owners "
              "to apply the diff above.")

    users.save(store)

    # --- credential file -------------------------------------------------
    out = jobs_dir / "demo-credentials.txt"
    lines = [
        "CORE Takeoff - https://core.workoptional.studio",
        "",
        f"Everyone signs in with their EMAIL and the password: {DEFAULT_PASSWORD}",
        "They are then required to choose their own before they can do",
        "anything else. Nothing else on the site works until they do.",
        "",
        "Until a person signs in, anyone who knows that word can sign in AS",
        "them, and eighteen of these accounts compete with each other. Chase",
        "anyone who has not logged in.",
        "",
    ]
    w = max(len(c[0]) for c in creds)
    for name, pw, staff in sorted(creds, key=lambda c: (not c[2], c[0])):
        state = "STAFF" if staff else ""
        if pw.startswith("("):
            state = (state + " - password already changed").strip(" -")
        lines.append(f"{name.ljust(w)}  {state}")
    out.write_text("\n".join(lines) + "\n")
    try:
        os.chmod(out, 0o600)
    except OSError:
        pass

    if reissue:
        print("REISSUED every password; everyone must choose a new one at "
              "next sign-in.")
    fresh = sum(1 for c in creds if not c[1].startswith("("))
    print(f"users total      : {len(store)}")
    print(f"  new this run   : {fresh}")
    print(f"  already existed: {len(creds) - fresh} (passwords untouched)")
    print(f"  staff          : {sum(1 for c in creds if c[2])}")
    print(f"plan sets shared (unchanged)    : {kept}")
    print(f"plan sets moved to staff        : {moved}")
    print(f"plan sets left with their owner : {left}")
    print(f"shared rows over a client's file (audit them): {leaks}")
    print(f"issued password  : {DEFAULT_PASSWORD} (each account must change it)")
    print(f"roster           : {out}")
    if wrote:
        print("\nRestart the app so it reloads registry.json:")
        print("  docker compose restart app")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
