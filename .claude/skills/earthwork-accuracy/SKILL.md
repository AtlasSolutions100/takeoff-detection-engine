---
name: Earthwork accuracy analysis
description: Diagnose and SCORE this engine's elevation reading, surface split, contour coverage and cut/fill against the ground-truth records we hold. Use when asked why a volume is wrong, to improve contour or spot-grade reading, or to judge whether a proposed filter/tuning change actually pays - it names the truth records, the probe pattern, the scoring references and the gate sequence.
---

# Earthwork accuracy: how to diagnose, score and land a change

Read `CLAUDE.md` first - this does not replace it. Sections 2 (the accuracy
contract), 3 (diagnose first, stop, report) and 5 (the four code shapes that
produce silent wrong numbers) bind here.

## 1. Pick the reference before you touch anything

No accuracy claim without **ground-truth source, expected, detected**. The
records we hold, with what each one IS and is NOT:

| reference | what it is | what it is NOT |
|---|---|---|
| Oncor C-04 cut ~2,002 CY (offline analyst) + his 41-row transect key | a human's own take-off of that sheet | not a CAD truth; his row set is partly inferred |
| Barn C3.0: Beaumont Estimate #853 - cut 5,551 / fill 17,123 CY over 224,058 SF (`ground-truth/swr-barn-civil_p2.json`) | the client's PRICED bid | CLIENT-ASSERTED; his SF came from a line we have not identified |
| Barn S1-1.3: 19 Polylength annots, 704.4 ft footing | the client's own measured lengths, in his marked PDF | structural, not earthwork |
| Stice p0: 1,487 operator-verified rows (`out/verify/2026-08-30/stice-recipe/`) | row-level elevation truth at scale | one operator, one pass |
| synthetic DEM sheets (`out/verify/2026-08-31/cutfill-contour-method/`, `proto_synthetic.py`) | a surface with a KNOWN volume by construction | synthetic geometry, no drafting noise |
| `tests/baselines/earthwork_*.json` | regression pins on a FIXED row set | NOT a check on row suggestion - see §5 |

State which one answers the question, and say when none does (then the
honest output is a diagnosis, not a score).

## 2. Diagnose with a stage table, on the real route

Write the probe in the scratchpad, never in the repo, and drive
`server.earthwork_suggest` / `server.earthwork_calc` themselves - a
reimplementation of the route measures the reimplementation.

- **Never write to the real job store.** Copy the job to a temp store:
  `os.environ["TAKEOFF_JOBS_DIR"] = tempfile.mkdtemp(...)`, copy
  `registry.json` + the job folder, then `import server`. The dev store
  `demo-jobs3` is throwaway but still shared with the live service - copy
  it too when the probe writes.
- Report a STAGE TABLE with measured values per stage (lines -> candidates
  -> rows -> sided rows -> volume), not a single number.
- Then ONE highest-confidence root cause, and stop if the fix is not
  trivial (CLAUDE.md §3).

Field-name trap that has already cost a false diagnosis: the suggest
reply's rows carry **`label_tag`**; only the UI renames it to `tag`. A probe
reading `row["tag"]` sees None everywhere and "proves" there is no problem.

## 3. Score every candidate change as variants, side by side

Build the variants by FILTERING the real route's own output (or by
wrapping the function under test), and print rows / cut / fill / area for
each against the reference. A change is worth landing only if it moves the
scored sheet and moves nothing it should not:

- landed: refusing built-point spot tags as contour seeds - C-04 cut
  +14% -> +3%, Barn C3.0 unchanged (FINDINGS 39);
- rejected: dropping anchors off the label grid - +4 lines on Barn C3.0 but
  Oncor C-04's proposed infill 7 rows -> 0 (FINDINGS 38 §6);
- rejected: refusing every off-interval value - strips 37 rows on C-04 and
  deletes all 42 interval-infill rows on Barn C3.0.

Always run at least one sheet that should NOT move.

## 4. The rules this domain keeps breaking

- **A point is not a line.** A spot grade (FG/TW/BW/RIM/INV...) is a POINT
  the design sets; a contour is a LINE at one height. `is_built_spot_tag`
  refuses the built ones as contour seeds; EG/ME are the existing
  surface's own points and are kept.
- **Refuse in the candidate pool, not at the end.** Filtering rows late
  leaves the inline pass, the nearest-label pass and the inheritance pass
  each able to reintroduce the value.
- **A sheet labels a minority of its contours.** The rest come from
  counting between labelled lines (`infill_intervals`, with a minor
  interval = step/(k+1)). Coverage is the denominator: `contour_coverage`
  gives lines with / without a height.
- **Abstain, never guess.** Where the evidence is lossy, keep the
  candidates visible and assign nothing (CLAUDE.md §5).
- **Say what you refused.** Every filter reports into `diag`, the route
  puts it in the reply, and the panel prints it (`#ewDropped`). A silent
  filter reads as "this sheet has nothing".

## 5. Landing it: gates, fingerprint, proof

1. Tests first-class: every fix needs a test that FAILS on the pre-fix
   commit. Check that in a temp worktree:
   `git worktree add --detach <temp> HEAD`, copy the test in, run it,
   `git worktree remove --force`. Never `git checkout` in this checkout.
2. Any edit to `server.py`, `users.py` or `takeoff_engine/*.py` moves the
   earthwork gate's fingerprint. Re-accept the three baselines AFTER every
   edit is final, prove only the signature moved
   (`scratchpad/prove_baselines.py` - "0 value field(s) differ"), then run
   both gates: `python -m takeoff_engine.regress --check --require`.
3. **Know the blind spot:** the C-04 gate cases replay a fixed row set, so
   a change to row SUGGESTION passes the gate unchanged even when it moves
   the sheet's cut by hundreds of CY. Score such a change with a probe and
   a test, not with the gate.
4. Headed proof in `out/verify/<date>/<topic>/` with annotated captures,
   a source crop off the PDF for any claim about the drawing, a
   `readback.json` and a `VERIFY.md` (CLAUDE.md §1). `git add -f` - `out/`
   is ignored. Commit code and captures separately; a >8 MB capture push
   has been refused with HTTP 408.
5. Append a FINDINGS section: what moved, measured, with the reference
   named and the rejected alternatives recorded.

## 6. Where the domain facts live

The client conventions (Beaumont's hand method, Jeff's footing/wall
method), the build waves and what CORE still gets wrong are in the
project memory `client-takeoff-methods-2026-09-15`; the UI wording rules in
`ui-copy-rules`. Soil/boring documents for the Wave 3 soils intake:
`OptionallyWorking/Earthwork` holds three real DOT boring logs (strata
depths, SPT counts, RQD) - the only soils-report fixture we have until the
client sends his.
