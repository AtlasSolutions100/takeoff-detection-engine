# Bid-grade cut and fill — the spec

Written 2026-09-17 on branch `dev` @ `4e99430`. Every number below was
measured on this tree today; the scripts are named so each can be re-run.
Read `CLAUDE.md` §2 (the accuracy contract) and §4 (domain guardrails)
first. The method and the truth inventory are a skill:
`.claude/skills/earthwork-accuracy/SKILL.md`.

**Goal.** A cut/fill number an enterprise estimator can put in a bid and
defend: accurate on a well-drawn sheet, *consistent* run to run, and
**disclosed** — it arrives with what it was computed from, what was
assumed, and how far it can be wrong. Everything the software cannot know
is typed by the estimator, in one place, and everything it assumed can be
overridden.

---

## 0. The finding that sets the whole direction

**The volume math is not the problem. The evidence fed into it is.**

Measured on four analytic surfaces with exact closed-form volumes
(`out/verify/2026-08-31/cutfill-contour-method/synth_out.json`, re-read
today):

| surface | linear TIN | clamped smooth |
|---|---|---|
| 2% plane | **0.00%** | −0.06% |
| cone (peak) | −0.03% | −0.02% |
| bowl (pit) | +0.01% | 0.00% |
| saddle | +0.24% | −0.02% |
| dome, 5 ft contour interval | **−3.2%** | −3.1% |
| dome, 10 ft contour interval | **−12.1%** | −12.7% |

The kernel is exact where the evidence is complete, and the error is a
function of **how much elevation evidence the drawing gives** — the dome
rows are the same surface read at half the label density. And the grid it
integrates on is not a source of error either: the same rows at cell sizes
1, 2, 3, 5 and 8 ft move the cut by **±0.4%** on Oncor C-04 and Barn C3.0
(`probe_enterprise_audit.py` A).

So: **do not rebuild the volume kernel.** Spend everything on evidence
completeness, evidence correctness, and proof per job.

## 1. Where we stand, scored

| sheet | reference | today | gap |
|---|---|---|---|
| Stice p0, bounded | the quantities the engineer PRINTED on his own sheet: raw cut 2,236,137 / fill 1,631,080 CY | cut −14% / fill −10% | was −24% / −29% (2026-08-30) |
| Oncor C-04 | the offline analyst's ~2,002 CY cut | cut **+3%** | was +14% before FINDINGS 39 |
| Barn C3.0, scored recipe | client Estimate #853: 5,551 / 17,123 CY | cut +14% / fill −22% | 290 of 350 contour lines carry no height |

## 2. The error budget, measured, biggest first

| # | source | measured | fix |
|---|---|---|---|
| 1 | **lines with no height** | Barn C3.0 60 of 350 lines have one; C-04 170 of 339; C2.1 25 of 82 | stage 1 |
| 2 | **surface does not match the sheet's own spots** | C-04: all 7 match-existing spots sit **+0.91 ft above** our existing surface (median 0.78, p90 1.58, max 1.78). Over that 100,570 SF that bias alone is ≈3,400 CY — more than the whole cut | stage 2 + 3 |
| 3 | **region counted** | Stice unbounded fill +65%, bounded −10%; Barn area 168,776 SF against the client's 224,058 | stage 4 (already: limit of work, zones) |
| 4 | **assignment errors** | fixed: utility lines as contours (FINDINGS 26), spot grades as contour heights (FINDINGS 39, 12 rows on one sheet); open: existing vs proposed side | stage 3 (confirm surface) |
| 5 | **shaping lines discarded** | C-04 throws away 3 grade breaks + 3 swales (1,403 ft); Barn C3.0 22 retaining walls + 7 swales (1,718 ft) | stage 2 |
| 6 | **peaks and pits truncated** | the dome rows above: −3.2% at a 5 ft interval, −12.1% at 10 ft | stage 2 |
| 7 | **bid adjustments unset** | strip, shrink/swell, subsidence, over-ex, subgrade stack — if blank the number answers a different question than the bid | stage 4 |
| 8 | volume kernel + grid | ≤0.5% on analytic truth, ±0.4% across cell sizes | nothing |

## 3. What the established tools do, and which of it we need

General practice in AGTEK, Trimble Business Center, Carlson Takeoff and
InSite SiteWork (stated as industry practice, not verified against those
products here — the measurements above are what justify each item):

| their practice | do we need it | why (measured) |
|---|---|---|
| surface built from ALL evidence: contours, spots, pads, **breaklines** | **yes** | #5, #2 — a TIN with no breaklines bridges a swale or a wall |
| breakline elevations derived where contours cross them | **yes** | the swales and walls we discard carry no elevation of their own |
| peak/pit closure (an elevation inside the innermost ring) | **yes** | #6 |
| prismatic TIN-to-TIN volume instead of a grid | **no** | grid moves the answer ±0.4%; this would be work for nothing |
| grid/section reporting by depth band | **yes** | production rates differ by depth; a bid needs it |
| per-area design targets (pad FFE, paving subgrade) | have it | grading zones + layer stacks |
| shrink/swell, subsidence, over-ex as separate bid items | partly | defaults + citations already researched in `docs/earthwork-defaults-research.json` (Cat Performance Handbook); the intake is stage 4 |
| mass haul / balance point | partly | balance rows exist; haul distance does not |
| cross-sections to eyeball the surface | **yes** | cheapest human check on a wrong surface |
| the estimator confirms the boundary, the datum and the soil factors | have it | keep |
| **residual check against the sheet's own spot elevations** | **yes, and first** | #2 found a 0.9 ft systematic bias nobody knew about |

Two things we already do that those tools do not, and which the enterprise
case rests on: **every elevation carries its provenance** (printed / OCR /
counted / typed), and **every refusal is stated on screen**.

## 4. Architecture

```
evidence            surface                 volume              bid
--------            -------                 ------              ---
contour lines  ─┐                      ┌─ grid dz, cell 2 ft ─┐
spot grades    ─┤                      │  (kernel: exact)    │
breaklines     ─┼─ TIN per side ───────┤                     ├─ strip / stack
pad + zone     ─┤   + breaklines       │                     │  shrink / swell
typed values   ─┘   + peak closure     └─ region: limit of   │  subsidence
                    ▲                     work, zones        │  over-ex
                    │                                        ▼
                 QA GATES (per job, shown, blocking on red)  quantities by
                 · coverage: lines with a height / all        zone + depth band
                 · spot residual: median, bias, worst          + haul / balance
                 · two methods agree within X%                 + provenance
                 · region stated: what area, bounded by what
```

Every box above already exists except **breaklines**, **peak closure**,
**the QA gates**, and **depth-band reporting**.

## 5. The manual-input contract

**Never assumed — the estimator types it** (the software cannot know, and
guessing it would be the confident-wrong-number failure):
limit of work; datum and scale confirmation; strip depth; shrink/swell per
material; subsidence and over-excavation; rock and groundwater; pad FFEs
and subgrade/pavement thickness; haul destinations and distances; which
surface is existing when the sheet names neither; the elevation inside a
closed peak or pit when nothing prints one; an unlabelled contour's value
when counting cannot reach it.

**Assumed, and overridable** (the assumption and its basis are shown):
existing vs proposed side per family (legend → ink → stroke weight);
spot ground vs built per tag; the surface model (label sparsity); the
contour interval; a boundary picked from a drawn line; a breakline's
elevation derived from the contours it crosses; the counted elevation of
an unlabelled contour.

Rule for both lists: **abstain over guess.** A row with no defensible
elevation arrives on skip, says why, and contributes nothing until the
estimator accepts it.

## 6. Build order, with acceptance criteria

Each stage is scored against the references in §1 and the synthetic set,
and lands with tests + headed proof + a FINDINGS entry.

**Stage 1 — every line gets a height or a stated reason.** REWRITTEN
2026-09-17 after the ordering solver was measured and stopped (FINDINGS
42): adjacency means "one contour interval apart" only 33-51% of the time
raw, 80% on C-04 with a cleaned candidate set, and on Barn C3.0 the
sheet's 17 printed labels produce NO same-family anchor-to-anchor
adjacency at all. A solver propagates along chains, so a one-in-five wrong
step compounds and mints heights that look printed. The sheet does not
carry the information; the spec's own manual-input contract applies.
  a. coverage reported by LENGTH inside the limit of work (Barn 41%, not
     the 17% the line count reads), every line without a height carrying a
     stated reason;
  b. the cleaned candidate set for the counting that already runs - same
     family, twins merged, anchors on the family's own interval grid
     (27% -> 80% trustworthy on C-04);
  c. FAN FILL for the estimator: pick the fan, give one height and a
     direction, the software counts the run and fills it - every value
     flagged operator-counted, the count shown and editable.
*Accept when:* the coverage statement is length-based and bounded, every
unelevated line names its reason, and a fan an estimator fills takes one
gesture and is provenance-flagged. **Stage 2 now runs first.**

**Stage 2 — surface fidelity.** Breaklines (grade break, swale, ridge,
toe/top of slope, wall, curb, pond) with elevations derived where contours
cross them, entered into the TIN as constraints; peak/pit closure with a
stated assumption.
*Accept when:* the C-04 spot-residual bias falls from +0.91 ft to ≤0.15 ft
and the median |residual| to ≤0.25 ft; the dome-at-10 ft synthetic case
improves; no scored sheet regresses.

**Stage 3 — the proof panel (do this alongside stage 1, it is cheap).**
Per job: coverage, spot residual (median / bias / worst, with the worst
clickable), two independent methods and their spread, the region
statement, and one verdict — green only when all four pass. Red blocks
nothing but is stated on the export and the report.
*Accept when:* every scored sheet shows its own numbers, and a deliberately
broken input (a wall spot forced onto a contour) turns the verdict red.

**Stage 4 — bid completeness.** Quantities by zone and by depth band;
strip / shrink / swell / subsidence / over-ex / subgrade as first-class
inputs with the researched defaults and their citations; soils-report
intake (the three DOT boring logs in `OptionallyWorking/Earthwork` are the
only fixture we have until the client sends his); haul and balance.
*Accept when:* the Barn run reproduces the client's bid-sheet lines in his
own units and order, with every factor shown and overridable.

**Stage 5 — the enterprise guarantee.** A scored-corpus harness run on
every build: the three references plus the synthetic set, reporting cut and
fill error per sheet, coverage, residuals and the two-method spread, with a
recorded history so a regression is visible the day it lands. Plus the
**suggest-row baseline** both gates are missing today (FINDINGS 39: a
12-row defect passed every gate for weeks).
*Accept when:* the harness fails the build if any scored sheet moves more
than 2 points without an accepted reason.

**Target, stated as a target and not a promise:** every scored reference
within ±10% on cut and fill with the QA verdict green, then ±5% as
coverage and breaklines land. Today: C-04 +3%, Stice −14% / −10%, Barn
+14% / −22%.

## 7. Traps carried forward

- A volume is only comparable against a run with **the same rows and the
  same surface model**: the same Stice rows read 2.13M CY under linear and
  2.77M under the smooth model.
- The model auto-rule is scored and correct on 3 of 4 cases; it looks wrong
  on a raw sheet only because the row set is sparse — fix coverage, not the
  rule (FINDINGS 40).
- Both gates replay a fixed row set. Row suggestion is untested until
  stage 5.
- A spot grade is a point, a contour is a line (FINDINGS 39). A "noncontour"
  legend row is not junk — several are breaklines (stage 2).
