# UI audit of the review app, 2026-09-09 — headed (Edge) on :8100, throwaway store

Method: the `audit-ai-design-slop` / `no-ai-design-slop` workflow from the
agent-skills folder (evidence first, removal test, keep the product's own
direction), against captures of the app's main states (`ui_survey.py`:
login, Projects, a loaded sheet, Tools menu, a selected style, the
earthwork panel, the paper theme, a 1180 px window). Mobbin references
for dense professional tool chrome (Figma, Rive, Ditto layer panels) were
used for hierarchy and density only, not copied.

## Verdict

The interface has one visual thesis (dark instrument, mono data, one green
accent, prose disclosures) and the audit found no decorative stacking or
template repetition to remove. What it found were density and duplication
defects: the working list in the earthwork panel spent most of its height
on wrapped provenance prose, and two surfaces repeated a sentence the
screen already carried. Removal-first fixes; no new colour, font, section
or effect was added.

## Checked scope

Projects page (Your projects tab), Oncor Cloudcrest engine page index 3 =
printed C-04 with the styles panel, the Tools menu, the earthwork panel's
rows, both themes, viewports 1500x940 and 1180x800.

## Findings

| Priority | Class | Pattern | Evidence | Harm | Fix |
|---|---|---|---|---|---|
| P1 | Quality defect | Working list buried by wrapped prose | Earthwork rows 4-5 lines each (the provenance sentence wrapped beside the buttons in a ~200 px column); 3 rows visible per screen of 150 (`U6`, `U8`) | The review sweep the panel exists for is a scroll marathon | Two-line row: number · "line 177 · w 0.2" · buttons, then badge + provenance on its own full-width line (`.ew-src`). Measured after: 57 px a row (75 for the longer "off interval" sentence), 4 rows visible where 3 were, identity never clipped (`A3b`) |
| P2 | Duplicate text | Status bar repeats the sheet-head chip | `589 lines in 65 styles · scale graphic bar 0-60 ft over 144.0pt (1" = 30', ticks) (graphic-bar)` on the bar, the same sentence in the chip 60 px above; clipped to "(grap…" at 1180 px (`U8`) | Two copies of one fact, one of them unreadable | `scaleShort`: the bar says `scale 1" = 30' (graphic-bar)`; the chip keeps the provenance. Not clipped at 1180 (`A4`, DOM: scrollWidth ≤ clientWidth) |
| P2 | Duplicate text | Sheet card prints the document name under the top bar | `.sh-name` "Oncor Cloudcrest Substation Civils - 20251212.pdf_sa…" directly under the bar's "Oncor Cloudcrest Substation Civils - 20251212.pd…" (`U2`) | A row of ellipsised text saying nothing new | Removed; the full name is the chips row's tooltip (`A2`) |
| P3 | Copy | "not opened yet" under "all detected" | Projects cards for jobs registered by API (`U1`) | Reads as a contradiction | "no preview yet", which is what is true |
| – | Checked, clean | Paper theme | `theme_probe.json`: header 244,245,241 / rail 255 / stage 220,223,216 in light; every chrome surface switches | An earlier capture at 700 ms caught a mid-transition frame - not a defect | none |
| – | Checked, kept | CHECK FIRST block, disclosure block, Tools menu descriptions | Long prose, but each sentence carries a product decision the team recorded (scale trust, the volume's limits) | Removing would lose stated meaning | left as is; the earthwork disclosure was folded on 09-09 (ocr-progress/VERIFY.md) |

## Unknowns

- "Projects 22" beside tabs reading 7 + 17: the head counts server rows,
  the tabs count per-tab membership, and a seeded demo the same account
  uploaded sits in both. Not changed; a reader may still read the three
  numbers as contradictory.
- The earthwork rows still sit under the grading-zone and limit-of-work
  blocks (noted 09-09); not moved.

Captures: `U*-raw.png` before, `A*-raw.png` after, `*-annotated.png` with
the marks; `survey.json`, `after.json`, `theme_probe.json` are the DOM
readbacks.
