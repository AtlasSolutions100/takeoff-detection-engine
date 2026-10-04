"""WHERE A PICK LEAKS, AND WHAT CLOSES IT (critic 4, M3, 2026-10-02).

Critic 4's four picks on a kitchen plan leaked to the sheet edge with "show the wall style, or draw
the room outline instead" and nothing to say which style or where. Now a leaking pick names the
unticked line type that closes the room when one does, and returns the places where the fill gets
out (marked on the sheet). A fill bounded only by the sheet's drawing frame is a leak, not a room
(the same kitchen plan, frame ticked: 2,964,187 pt2 taken as one room). FAILS on 8116831.

    python tests/test_rooms_leak.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from takeoff_engine import rooms  # noqa: E402

fails = 0


def check(ok, msg, got=None):
    global fails
    print(("  ok   " if ok else "  FAIL ") + msg + ("" if ok or got is None else f"  (got {got})"))
    if not ok:
        fails += 1


def seg(cid, sid, a, b):
    return {"chain_id": cid, "style_id": sid, "points": [a, b], "length_pt": abs(b[0] - a[0]) + abs(b[1] - a[1])}


# a room 288 x 144 pt: three walls in line type 1, the fourth (the east side, x = 388) in line type 2 - a
# storefront's glazing drawn in its own type; line type 3 is furniture inside
chains = [seg(1, 1, [100, 100], [388, 100]), seg(2, 1, [100, 244], [388, 244]), seg(3, 1, [100, 100], [100, 244]),
          seg(4, 2, [388, 100], [388, 244]), seg(5, 3, [150, 150], [200, 150])]
res = {"page": {"width_pt": 612, "height_pt": 792}, "chains": chains, "openings": [],
       "styles": [{"style_id": 1, "class": "solid", "width": 1.4}, {"style_id": 2, "class": "dash1", "width": 0.5},
                  {"style_id": 3, "class": "solid", "width": 0.5}],
       "scale": {"ft_per_paper_inch": 4.0}}
out = rooms.pick_room(res, [], 240, 170, {1}, 100)
check(out["ok"] is False and out["why"].startswith("The fill reached the sheet edge. Tick the line type that closes this room, or draw a wall."),
      "the words that help", out.get("why"))
check(out.get("closes_with") == 2 and "Line type 2 (dash1, 0.5 pt) closes it" in out["why"], "the line type that closes it is named", out.get("why"))
pts = out.get("leak") or []
check(pts and all(abs(p[0] - 388) <= 4 and 100 <= p[1] <= 244 for p in pts), "the leak is marked on the east side, where the fill gets out", pts)
ok = rooms.pick_room(res, [], 240, 170, {1, 2}, 100)
check(ok["ok"] and abs(ok["area_pt2"] - 288 * 144) / (288 * 144) < 0.03, "line type 2 ticked: the room closes (288 x 144 pt)", ok.get("area_pt2"))
# no line type closes it: a wall that is not drawn at all - the neck the fill gets out through is marked
res2 = dict(res, chains=[c for c in chains if c["chain_id"] != 4] + [seg(6, 1, [388, 100], [388, 160]), seg(7, 1, [388, 184], [388, 244])])
out2 = rooms.pick_room(res2, [], 240, 170, {1}, 100)
check(out2["ok"] is False and out2.get("closes_with") is None and (out2.get("leak") or [])
      and all(abs(p[0] - 388) <= 12 and 150 <= p[1] <= 194 for p in out2["leak"]),
      "a 24 pt gap no line type closes: no type named, the gap marked", (out2.get("why"), out2.get("leak")))
# the sheet's drawing frame is not a room's wall
frame = [seg(10, 9, [10, 10], [602, 10]), seg(11, 9, [602, 10], [602, 782]), seg(12, 9, [602, 782], [10, 782]), seg(13, 9, [10, 782], [10, 10])]
res3 = dict(res, chains=chains + frame, styles=res["styles"] + [{"style_id": 9, "class": "solid", "width": 4.1}])
out3 = rooms.pick_room(res3, [], 240, 170, {1, 9}, 100)
check(out3["ok"] is False and out3.get("closes_with") == 2, "the frame ticked: still a leak (not a room of the whole sheet), type 2 named", out3.get("why"))
print(f"\n{fails} FAILED" if fails else "\nall passed")
sys.exit(1 if fails else 0)
