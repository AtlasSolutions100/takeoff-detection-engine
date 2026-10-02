"""provision_demo.py never rewrites a registry row that has an owner, and it
rewrites the rows that ARE its own - the owner-less ones - only when asked
to with --rewrite-owners, after printing every row it would change and
getting a yes.

TWO HOLES, in order.

1. THE NAME-ONLY REWRITE (b528e74 and earlier, closed by 31914a2 on
   2026-09-02). The ownership block decided on the display name alone: a row
   named like a demo card or a SHARED_PREFIX got owner None, every other row
   moved to the first staff account, and nobody looked at who uploaded it. On
   a copy of a store holding only alice's "Belle Farms Structural Plans.pdf"
   it printed "plan sets shared : 1" and published her private bid set to
   every account on the box; her "anything.pdf" went to staff and her own
   job answered 404. test_1 and test_4 hold those two rows.

2. WHAT 31914a2 LEFT OPEN (closed on feat/provision-scoped). Its guard was
   "owner None or a STAFF account", so a row owned by the SECOND staff
   account was still rewritten - its scratch set moved to STAFF[0], its
   manifest-named upload shared - and every owner-less row was moved on
   every run, with no flag, no diff and no confirmation. DEPLOY.md tells
   staff to re-run the tool on the box, so a re-run that silently changes
   attribution is the same shape of bug at a smaller scale. Measured on
   dev@150358a with the fixture below, from a run with no arguments:
   christian's two rows came back koby / None and the two pre-login rows
   moved to koby.

THE RULE NOW. A row with a non-None owner - client or staff - is never
touched. The tool's rows are the owner-less ones (the SHARED marker; the
server writes the key as null on every save, so "no key" and "null" are the
same population). Of those, a demo-card or shared-prefix name stays shared,
a leak over a client's file is called out and left, and everything else is
listed in a diff and moved to staff ONLY under --rewrite-owners, after a
y/N on a terminal or --yes off one. Without the flag registry.json is not
rewritten at all - not even re-serialised.

Runs the real script as a subprocess against a THROWAWAY store (the script
reads TAKEOFF_JOBS_DIR), the way it is run on the box, with stdin closed so
the confirmation prompt can never be answered by accident, and reads
registry.json back.

Dependency-free:  python tests/test_provision_ownership.py
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import provision_demo  # noqa: E402

STAFF0 = provision_demo.STAFF[0].lower()
STAFF1 = provision_demo.STAFF[1].lower()
assert STAFF0 != STAFF1, provision_demo.STAFF
ALICE = "alice_synth@example.test"          # not on the roster: a client
manifest = json.loads((ROOT / "demo-plans.json").read_text(encoding="utf-8"))
NAMES = sorted(manifest["plans"])
NAME = ("Belle Farms Structural Plans.pdf"
        if "Belle Farms Structural Plans.pdf" in NAMES else NAMES[0])
# a shared-set sheet's exact name (prefixes until 2026-10-01: bid-content-1, RISK-3)
PREFIX_NAME = provision_demo.SHARED_NAMES[0]
NOKEY = "<no owner key>"     # sentinel: the row has no "owner" key at all

A1, A2, A3 = "a1a1a1a1a1a1", "a2a2a2a2a2a2", "a3a3a3a3a3a3"
C1, C2, S1 = "c1c1c1c1c1c1", "c2c2c2c2c2c2", "5f5f5f5f5f5f"
N_DEMO, N_OLD, N_OLDER, N_DEMO_NOKEY = ("6a6a6a6a6a6a", "6b6b6b6b6b6b",
                                        "6c6c6c6c6c6c", "6d6d6d6d6d6d")
LEAK = "7b7b7b7b7b7b"


def row(store, name, owner, hexid, *, no_owner_key=False):
    r = {"path": str(store / f"upload_{hexid}_{name}"), "pages": 1,
         "mtime": 1.0, "kinds": None, "sizes": [[612.0, 792.0]]}
    if not no_owner_key:
        r["owner"] = owner
    return r


def fixture():
    """A store as a hosted box really has it, plus the registry text as
    written, so a test can assert the file was not even re-serialised."""
    store = Path(tempfile.mkdtemp(prefix="provision_owner_test_"))
    reg = {
        # the two rows the task names: a client's upload wearing a demo
        # card's name, and a client's upload named like nothing at all
        A1: row(store, NAME, ALICE, "0a0a0a0a0a0a"),
        A3: row(store, "anything.pdf", ALICE, "0c0c0c0c0c0c"),
        # a client's upload wearing a shared prefix
        A2: row(store, PREFIX_NAME, ALICE, "0b0b0b0b0b0b"),
        # the SECOND staff account's rows: an owner that is not None and not
        # STAFF[0]. dev@150358a rewrote both (moved / shared).
        C1: row(store, "Christian Scratch Set.pdf", STAFF1, "0d0d0d0d0d0d"),
        C2: row(store, NAME, STAFF1, "0e0e0e0e0e0e"),
        # the first staff account's ordinary upload
        S1: row(store, "Staff Scratch Set.pdf", STAFF0, "0f0f0f0f0f0f"),
        # shared rows from before per-user mode: a demo card (stays shared),
        # two ordinary documents (the tool's one real job: staff-only), and
        # a demo card from before the owner key existed at all
        N_DEMO: row(store, NAME, None, "1a1a1a1a1a1a"),
        N_OLD: row(store, "Old Pre-Login Upload.pdf", None, "1b1b1b1b1b1b"),
        N_OLDER: row(store, "Older Pre-Login Upload.pdf", None,
                     "1c1c1c1c1c1c", no_owner_key=True),
        N_DEMO_NOKEY: row(store, NAME, None, "1d1d1d1d1d1d",
                          no_owner_key=True),
        # a LEAK: a shared row minted over alice's own file (same path as
        # A1) by the pre-fix demo scan. Not ours to keep sharing, not this
        # tool's to delete: it must be left alone and called out.
        LEAK: row(store, NAME, None, "0a0a0a0a0a0a"),
    }
    assert reg[LEAK]["path"] == reg[A1]["path"]
    assert "owner" not in reg[N_OLDER] and "owner" not in reg[N_DEMO_NOKEY]
    text = json.dumps(reg, indent=1)
    (store / "registry.json").write_text(text)
    return store, reg, text


def run_tool(store, *args, expect=0):
    env = dict(os.environ, TAKEOFF_JOBS_DIR=str(store),
               TAKEOFF_LEDGER_DIR=str(store / "ledger"))
    r = subprocess.run([sys.executable, "provision_demo.py", *args],
                       cwd=str(ROOT), env=env, capture_output=True, text=True,
                       timeout=600, stdin=subprocess.DEVNULL)
    assert r.returncode == expect, (r.returncode, r.stdout, r.stderr)
    return r.stdout + r.stderr


def owners(store):
    after = json.loads((store / "registry.json").read_text())
    return {jid: r.get("owner", NOKEY) for jid, r in after.items()}, after


def has_line(out, *needles):
    return any(all(n in ln for n in needles) for ln in out.splitlines())


def test_1_without_the_flag_the_registry_is_not_touched():
    store, reg, text = fixture()
    out = run_tool(store)
    o, _ = owners(store)
    # the two rows the task names (safe since 31914a2)
    assert o[A1] == ALICE, (o, out)
    assert o[A3] == ALICE, (o, out)
    # the rows dev@150358a still rewrote on a bare run
    assert o[C1] == STAFF1, \
        "the second staff account's row was moved: %r\n%s" % (o, out)
    assert o[C2] == STAFF1, \
        "the second staff account's row was shared: %r\n%s" % (o, out)
    assert o[N_OLD] is None, \
        "an owner-less row moved without --rewrite-owners: %r\n%s" % (o, out)
    assert o[N_OLDER] == NOKEY, (o, out)
    # not merely the same owners: the file was not rewritten at all
    assert (store / "registry.json").read_text() == text, \
        "registry.json was re-serialised by a run without --rewrite-owners"
    # it said what a --rewrite-owners run would do, and to which rows
    assert "DRY RUN" in out, out
    assert has_line(out, N_OLD, repr("Old Pre-Login Upload.pdf"), "->",
                    repr(STAFF0)), out
    assert has_line(out, N_OLDER, repr("Older Pre-Login Upload.pdf"), "->",
                    repr(STAFF0)), out
    assert "2 row(s) would change" in out, out
    # the flag gates ownership only: accounts and the roster still happen
    assert (store / "users.json").is_file(), out
    assert (store / "demo-credentials.txt").is_file(), out


def test_2_rewrite_owners_moves_only_owner_less_non_demo_rows():
    store, reg, text = fixture()
    out = run_tool(store, "--rewrite-owners", "--yes")
    o, after = owners(store)
    # never a row with an owner - client or staff, whatever it is named
    assert o[A1] == ALICE, (o, out)
    assert o[A2] == ALICE, (o, out)
    assert o[A3] == ALICE, (o, out)
    assert o[C1] == STAFF1, (o, out)
    assert o[C2] == STAFF1, (o, out)
    assert o[S1] == STAFF0, (o, out)
    # owner-less demo cards stay shared; the one with no key gains none
    assert o[N_DEMO] is None, (o, out)
    assert o[N_DEMO_NOKEY] == NOKEY, (o, out)
    # the tool's one real job
    assert o[N_OLD] == STAFF0, (o, out)
    assert o[N_OLDER] == STAFF0, (o, out)
    # the leak is left and called out
    assert o[LEAK] is None, (o, out)
    assert "WARNING: shared row %s" % LEAK in out, out
    # every owned row was refused by display name, owner and job id
    for jid, disp, who in ((A1, NAME, ALICE), (A2, PREFIX_NAME, ALICE),
                           (A3, "anything.pdf", ALICE),
                           (C1, "Christian Scratch Set.pdf", STAFF1),
                           (C2, NAME, STAFF1),
                           (S1, "Staff Scratch Set.pdf", STAFF0)):
        assert has_line(out, "refused", repr(disp), repr(who), jid), \
            "no refusal printed for %r owned by %r:\n%s" % (disp, who, out)
    # the diff named exactly the two rows that moved, and it was applied
    assert "2 row(s) would change" in out, out
    assert "DRY RUN" not in out, out
    assert "plan sets shared (unchanged)    : 2" in out, out
    assert "plan sets moved to staff        : 2" in out, out
    assert "plan sets left with their owner : 6" in out, out
    assert "shared rows over a client's file (audit them): 1" in out, out
    # untouched rows are identical dicts; moved rows changed only in owner
    for jid in (A1, A2, A3, C1, C2, S1, N_DEMO, N_DEMO_NOKEY, LEAK):
        assert after[jid] == reg[jid], (jid, reg[jid], after[jid])
    for jid in (N_OLD, N_OLDER):
        a, b = dict(reg[jid]), dict(after[jid])
        a.pop("owner", None), b.pop("owner", None)
        assert a == b, (jid, a, b)


def test_3_rewrite_owners_off_a_terminal_needs_yes_and_writes_nothing():
    store, reg, text = fixture()
    out = run_tool(store, "--rewrite-owners", expect=2)
    assert (store / "registry.json").read_text() == text, out
    # refused BEFORE any write: no accounts, no roster
    assert not (store / "users.json").exists(), out
    assert not (store / "demo-credentials.txt").exists(), out
    assert "--yes" in out, out


def test_4_the_two_named_rows_alone_keep_their_owner_in_both_modes():
    """The task's fixture verbatim: one client row named after a demo card,
    one named 'anything.pdf'. The owner assertion has held on dev since
    31914a2 (only the summary wording below is this branch's); kept as the
    plainest statement of the rule, in both modes."""
    for args in ((), ("--rewrite-owners", "--yes")):
        store = Path(tempfile.mkdtemp(prefix="provision_owner_test_"))
        reg = {A1: row(store, "Belle Farms Structural Plans.pdf", ALICE,
                       "0a0a0a0a0a0a"),
               A3: row(store, "anything.pdf", ALICE, "0c0c0c0c0c0c")}
        (store / "registry.json").write_text(json.dumps(reg, indent=1))
        out = run_tool(store, *args)
        o, _ = owners(store)
        assert o == {A1: ALICE, A3: ALICE}, (args, o, out)
        assert "plan sets left with their owner : 2" in out, (args, out)
        assert "no row would change" in out, (args, out)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} tests passed")
