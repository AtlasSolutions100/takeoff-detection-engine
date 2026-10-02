// JOIN JOINS; THE WORK AREA REACHES EARTHWORK (2026-09-09, both reported by
// Koby from the live app).
//
//   1. JOIN (J): select a piece of a broken contour, press J, click the other
//      piece ANYWHERE - the two become one line end to end. From 330d7e9 a
//      click on the other piece's body teed into it instead (the selected
//      line grew a connector to the click point; two lines stayed two), which
//      is "it just extends". mergeClick now always goes to mergeEnds; the tee
//      lives in Extend.
//   2. WORK AREA: the boxes drawn with the work-area tool mask every other
//      quantity on the sheet but the earthwork panel ignored them. Now a
//      contour with none of its length inside the boxes is out of the list
//      and out of the surfaces, spot grades outside are not sent, one box is
//      the volume's limit of work when none is set, and the panel says how
//      many it left out.
//
// run: node tests/test_join_and_workarea.js
const fs = require("fs");
const path = require("path");
const js = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
function fnSrc(name) {
  const start = js.indexOf("function " + name + "(");
  if (start < 0) return "";
  let depth = 0;
  for (let j = js.indexOf("{", start); j < js.length; j++) {
    if (js[j] === "{") depth++;
    else if (js[j] === "}" && --depth === 0) return js.slice(start, j + 1);
  }
  return "";
}

// ---- 1. join ---------------------------------------------------------------
const mc = fnSrc("mergeClick");
check(!!mc, "mergeClick exists");
check(!/connectToPoint\(/.test(mc), "JOIN: a click on the other piece's body no longer tees (connectToPoint is not called from mergeClick)");
check(/mergeEnds\(sel, tgt\);/.test(mc), "JOIN: every hit goes to mergeEnds - end to end");
check(/chainHitAt\(px, py\)/.test(mc), "JOIN: a click anywhere on the other line finds it (not only within snap of an end)");
check(/tgt\.chain_id === sel\.chain_id/.test(mc), "JOIN: clicking the selected line itself is refused, not merged with itself");
// run it: a target hit off-end must still end in mergeEnds
{
  const calls = [];
  const scope = {
    state: { selected: 1 },
    chainById: (id) => ({ 1: { chain_id: 1 }, 2: { chain_id: 2 } })[id] || null,
    snapToChain: () => ({ chain_id: 2, isEnd: false, point: [5, 5] }),   // body of the other piece
    chainHitAt: () => null,
    mergeEnds: (a, b) => calls.push(["mergeEnds", a.chain_id, b.chain_id]),
    connectToPoint: (a, h) => calls.push(["connectToPoint", a.chain_id, h.chain_id]),
    cancelMerge: () => calls.push(["cancel"]),
    setStatus: (t) => calls.push(["status", t]),
  };
  const names = Object.keys(scope);
  new Function(...names, mc + "\nreturn mergeClick;")(...names.map(n => scope[n]))(5, 5);
  check(JSON.stringify(calls) === JSON.stringify([["mergeEnds", 1, 2]]),
    "JOIN, run: a body hit on the other piece merges end to end  (" + JSON.stringify(calls) + ")");
}
check(/extendClick|snapWeld/.test(fnSrc("extendTo")) || /snapWeld/.test(fnSrc("extendTo")),
  "the tee is still available through Extend (snap-weld), where it belongs");
check(/join: click the other piece of this line\. The two pieces/.test(js),
  "the J prompt says what will happen: the two pieces become one line");

if (fails) { console.error("\n" + fails + " check(s) failed"); process.exit(1); }
console.log("\nall join checks passed");
