"""WHAT THIS NUMBER STILL NEEDS BEFORE IT CAN CARRY A BID (2026-09-17).

The contract Koby set: manual entry is fine, but once every required input
is supplied the number has to be right - which only means something if the
software says what it still needs. `bid_readiness` answers that from the
SAME body the volume was computed from, so the list cannot describe
different inputs than the ones that produced the answer.

Every item is on the list because it was measured to move a scored sheet
(scratchpad/probe_complete_input.py, 2026-09-17):

  elevations  Oncor C-04 re-run on random halves of its own rows moves the
              fill -24%; Barn C3.0 swings 89-144% ACROSS DRAWS at one row
              count. Coverage by length: Barn 41%, C-04 67%, C2.1 71%.
  limit of work  Barn runs over 168,776 SF against the client's 224,058.
  strip       6 in of topsoil off C-04: cut -30%, fill +13%.
  dig depth   12 in of pavement section: cut +88%.

THE RULE THIS FILE EXISTS TO PIN: a default is not an answer. export_factor
1.0 is what the software does when nobody said, so it reads ASSUMED until
the estimator confirms it - and "ready" requires every required item
SUPPLIED, never merely assumed.

    python tests/test_bid_readiness.py   (fails before bid_readiness existed)
"""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import takeoff_engine.earthwork as ew  # noqa: E402

FAILS = []


def check(ok, msg):
    print(("ok: " if ok else "FAIL: ") + msg)
    if not ok:
        FAILS.append(msg)


class Body:
    """Only the attributes bid_readiness reads - the same names
    EarthworkBody carries, so the two cannot drift apart silently."""

    def __init__(self, **kw):
        self.existing = [{"chain_id": 1, "z": 100.0}, {"chain_id": 2, "z": 101.0}]
        self.proposed = [{"chain_id": 3, "z": 103.0}, {"chain_id": 4, "z": 104.0}]
        self.boundary = None
        self.boundary_is_limit = False
        self.existing_layers = []
        self.proposed_layers = []
        self.export_factor = 1.0
        self.import_factor = 1.0
        self.cut_reuse_pct = 100.0
        self.confirmed = []
        self.__dict__.update(kw)


RESULT = {"area_sf": 100540.0, "cut_yd3": 2056.0, "fill_yd3": 9727.0}
SCALE = {"ft_per_paper_inch": 30.0, "source": "graphic bar"}
# Oncor C-04's own coverage block
COV = {"contour_chains": 339, "with_elev": 170,
       "contour_len_pt": 130918.9, "with_elev_len_pt": 88250.1}
FULL = {"contour_chains": 339, "with_elev": 339,
        "contour_len_pt": 130918.9, "with_elev_len_pt": 130918.9}


def by_id(rd):
    return {i["id"]: i for i in rd["items"]}


# --- 1. the bare panel: what an estimator gets before he says anything ----
print("\n1. nothing supplied yet")
rd = ew.bid_readiness(Body(), RESULT, coverage=COV, scale=SCALE)
it = by_id(rd)
check(rd["ready"] is False, "a volume with nothing supplied is not ready to bid")
check(it["scale"]["state"] == "supplied" and "30" in str(it["scale"]["value"]),
      "scale is supplied (the route refuses without one) and is stated  (%s)"
      % it["scale"]["value"])
check(it["limit_of_work"]["state"] == "missing"
      and "overlap" in it["limit_of_work"]["why"],
      "no limit of work is MISSING, and says what the region became instead")
check(it["elevations"]["state"] == "missing"
      and "169" in it["elevations"]["why"] and "67%" in str(it["elevations"]["value"]),
      "elevations count the lines that have none, by length  (%s)"
      % it["elevations"]["value"])
check(it["strip"]["state"] == "missing" and it["dig_depth"]["state"] == "missing",
      "an empty layer stack is silence, not a zero")
check(it["swell"]["state"] == "assumed" and it["reuse"]["state"] == "assumed",
      "a software default reads ASSUMED, never supplied  (%s / %s)"
      % (it["swell"]["state"], it["reuse"]["state"]))
check(rd["n_missing"] == 4 and rd["n_assumed"] == 2
      and "4 required inputs missing" in rd["sentence"],
      "the sentence counts both kinds: %r" % rd["sentence"])

# --- 2. a default becomes an answer only when someone says so -------------
print("\n2. confirming is how a default becomes an answer")
rd2 = ew.bid_readiness(Body(confirmed=["strip", "swell", "reuse", "dig_depth"]),
                       RESULT, coverage=COV, scale=SCALE)
it2 = by_id(rd2)
check(all(it2[k]["state"] == "supplied" for k in ("strip", "dig_depth", "swell", "reuse")),
      "the four confirmable items go supplied once the estimator confirms them")
check("confirmed by the estimator" in it2["strip"]["why"],
      "...and the record says WHO said so, not that the software knew  (%s)"
      % it2["strip"]["why"])
check(rd2["ready"] is False and rd2["n_missing"] == 2,
      "confirming does not paper over what is still genuinely missing  (%s)"
      % rd2["sentence"])

# --- 3. the whole contract satisfied --------------------------------------
print("\n3. everything supplied")
full = Body(boundary=[[0, 0], [10, 0], [10, 10]], boundary_is_limit=True,
            existing_layers=[{"name": "Topsoil", "thickness_in": 6,
                              "reuse_pct": 0, "respread_pct": 100}],
            proposed_layers=[{"name": "Pavement section", "thickness_in": 12}],
            export_factor=1.25, import_factor=1.1)
rd3 = ew.bid_readiness(full, RESULT, coverage=FULL, scale=SCALE)
it3 = by_id(rd3)
check(rd3["ready"] is True and rd3["n_missing"] == 0 and rd3["n_assumed"] == 0,
      "with every required input supplied it reads ready  (%s)" % rd3["sentence"])
check("every input this number depends on" in rd3["sentence"],
      "...and says exactly that, without promising the answer is correct")
check(it3["strip"]["value"] and "6 in" in it3["strip"]["value"],
      "the supplied values are shown, not just ticked  (%s)" % it3["strip"]["value"])
check(all(i["state"] == "not built" for i in rd3["items"] if i["id"] in
          ("over_excavation", "rock_water")),
      "over-ex, subsidence, rock and groundwater are NAMED as not built")
check(all(not i["required"] for i in rd3["items"] if i["state"] == "not built"),
      "...and a thing the software cannot take is not held against the job")

# --- 4. the cases that must not read as fine ------------------------------
print("\n4. the ways a job can look complete and not be")
rd4 = ew.bid_readiness(Body(boundary=[[0, 0], [10, 0], [10, 10]],
                            boundary_is_limit=False), RESULT, coverage=COV, scale=SCALE)
check(by_id(rd4)["limit_of_work"]["state"] == "assumed"
      and "crop" in by_id(rd4)["limit_of_work"]["why"],
      "a work-area box is a crop, not the limits line, and says so")
one = Body(existing=[{"chain_id": 1, "z": 100.0}, {"chain_id": 2, "z": 100.0}])
rd5 = ew.bid_readiness(one, RESULT, coverage=COV, scale=SCALE)
check(by_id(rd5)["surfaces"]["state"] == "missing"
      and "fewer than two" in by_id(rd5)["surfaces"]["why"],
      "two rows at ONE elevation is not a surface  (%s)"
      % by_id(rd5)["surfaces"]["why"][:60])
rd6 = ew.bid_readiness(Body(), RESULT, coverage=None, scale=SCALE)
check(by_id(rd6)["elevations"]["state"] == "assumed"
      and "not measured" in by_id(rd6)["elevations"]["why"],
      "with no coverage measured it says so instead of claiming a number")
check(by_id(rd6)["elevations"]["value"] is None,
      "...and shows no figure it cannot stand behind")
rd7 = ew.bid_readiness(Body(), {"area_sf": None}, coverage=FULL, scale=None)
check(by_id(rd7)["scale"]["state"] == "missing",
      "no scale is missing - volume moves with the SQUARE of scale")

print("\n%d FAILED" % len(FAILS) if FAILS else "\nall passed")
sys.exit(1 if FAILS else 0)
