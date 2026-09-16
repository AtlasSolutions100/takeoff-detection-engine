// DRY-UTILITY BUCKETS (2026-09-16): Beaumont bids "Gas Trench only" and
// "Elec/Phone/TV Trench" as their own lines (bid sheet items 17-18; call
// 2026-09-15, 11:12-11:32, 58:44). Until now gas / electric / telecom fell to
// UNASSIGNED on purpose; now they have "Gas" and "Electric/Comm" buckets,
// with G and E as label-only lettered-run codes like W.
//
// run: node tests/test_bid_dry_utilities.js   (fails on 92f2d84)
"use strict";
const fs = require("fs");
const path = require("path");
const src = fs.readFileSync(path.join(__dirname, "..", "webui", "app.js"), "utf8");
let fails = 0;
const check = (ok, msg) => { if (ok) console.log("ok: " + msg); else { fails++; console.error("FAIL: " + msg); } };
const a = src.indexOf("// BIDVIEW-RULES-BEGIN"), b = src.indexOf("// BIDVIEW-RULES-END");
check(a >= 0 && b > a, "BIDVIEW-RULES markers present");
const T = new Function(src.slice(a, b) + "\nreturn { BID_BUCKETS, BID_UNASSIGNED, BID_LABEL_ONLY, bidBucketFor };")();
const bucket = (label, layer) => T.bidBucketFor(label, layer || "").bucket;

check(T.BID_BUCKETS.includes("Gas") && T.BID_BUCKETS.includes("Electric/Comm"), "the two dry buckets exist");
check(T.BID_BUCKETS.indexOf("Gas") === T.BID_BUCKETS.indexOf("Water") + 1 && T.BID_BUCKETS.indexOf("Electric/Comm") === T.BID_BUCKETS.indexOf("Gas") + 1,
  "they sit right after Water in the display order");
check(T.BID_BUCKETS[T.BID_BUCKETS.length - 1] === T.BID_UNASSIGNED, "UNASSIGNED is still last");
check(JSON.stringify(T.BID_LABEL_ONLY) === JSON.stringify(["W", "G", "E"]), "G and E are label-only codes beside W");

// gas
check(bucket("GAS", "") === "Gas", "GAS -> Gas");
check(bucket("PROPOSED GAS SERVICE", "") === "Gas", "gas service -> Gas");
check(bucket("18,000 GAL BURIED PROPANE TANK", "") === "Gas", "propane -> Gas");
check(bucket("", "C-UTIL-GAS") === "Gas", "a GAS layer -> Gas");
check(bucket("G", "") === "Gas", "the lettered run G (label) -> Gas");
check(bucket("Unnamed d12", "P-UT-G") !== "Gas", "a lone G in a LAYER path is not gas (label-only)");
// electric / comm
check(bucket("P-UT-ELEC-UG", "") === "Electric/Comm", "P-UT-ELEC-UG -> Electric/Comm (was UNASSIGNED on purpose before)");
check(bucket("PROPOSED UNDERGROUND ELECTRIC", "") === "Electric/Comm", "underground electric -> Electric/Comm");
check(bucket("ELEC/PHONE/TV (1 COMMON TRENCH)", "") === "Electric/Comm", "his own bid line name -> Electric/Comm");
check(bucket("TELEPHONE PEDESTAL", "") === "Electric/Comm", "telephone -> Electric/Comm");
check(bucket("FIBER OPTIC", "") === "Electric/Comm", "fiber -> Electric/Comm");
check(bucket("E", "") === "Electric/Comm", "the lettered run E (label) -> Electric/Comm ('the E is for electric')");
check(bucket("SELECTED FILL", "") !== "Electric/Comm", "SELECTED is not electric (ELEC is whole-word)");
// wet systems keep winning where they matched before
check(bucket("WATER MAIN", "") === "Water" && bucket("STORM SEWER", "") === "Storm Drainage" && bucket("8\" SS", "") === "Sanitary Sewer", "water, storm and sanitary unchanged");
check(bucket("8\" W", "") === "Water", "the W run code still reads as water");
check(bucket("", "") === T.BID_UNASSIGNED, "an empty row is still UNASSIGNED");

if (fails) { console.error(fails + " failure(s)"); process.exit(1); }
console.log("all dry-utility bucket checks passed");
