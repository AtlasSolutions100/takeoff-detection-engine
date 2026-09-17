// Earthwork material defaults - RESEARCHED, CITED, OVERRIDABLE.
// Koby's amendment (EXCAVATION-PLAN.md): conventions may be invented for the
// demo state only as research + citation + default + override. Every number
// here carries its source; the full research record with per-source URLs and
// the disagreement notes lives in docs/earthwork-defaults-research.json.
// NOTHING here applies silently: the app's factors stay 1.0 (the evidenced
// #853 default - their estimate applies no factor anywhere) until the
// operator picks a preset, and the boxes stay editable after.
//
// bank_to_loose: 1 bank (in-ground) CY becomes this many loose CY in a truck.
// bank_to_compacted: 1 bank CY becomes this many compacted-in-place CY.
const EW_RESEARCH = {
  // Stripping depth over the disturbed area. National band 4-8 in (NRCS
  // Illinois Urban Manual spec 752 "4-to-6-inch... is common"; MS DEQ
  // "normal stripping depth is 4-6 in"; sitework guides "4 to 12 in").
  // LOCAL PRIOR BEATS NATIONAL: Beaumont Estimate #853's own arithmetic is
  // 5,730 CY strip x 27 / 224,058 SF clear-and-grub = 8.29 in.
  strip_depth_in: { value: 8, national: [4, 8],
    local: "8.29 in from a scored bid's own arithmetic (5,730 CY over 224,058 SF)" },
  materials: {
    "topsoil / loam": { b2l: 1.43, b2c: 0.74,
      src: "Cat Performance Handbook Ed.50 p.27-6 load factor 0.70 (43% swell); shrink -26% Church(1981)/FHWA(2022) via WisDOT 0092-22-05. Sources span 43-56% swell - topsoil is intrinsically wide." },
    "clay": { b2l: 1.30, b2c: 0.90,
      src: "Consensus midpoint: Cat 22-25%, Church 35-40%, FHWA 50-67%, DOT survey (Crooks/Auburn 2013) 30-50% swell; shrink -10% Church/FHWA. LOW CONFIDENCE - moisture and plasticity move this 2-3x; override from the soils report." },
    "sand & gravel": { b2l: 1.12, b2c: 0.92,
      src: "Cat PHB Ed.50: dry sand LF 0.89 (12% swell); compacted 0.92 assumes DAMP material (Burch 1997: dry sand compacts to 0.72-0.78x bank)." },
    "common earth": { b2l: 1.25, b2c: 0.88,
      src: "Cat PHB Ed.50 earth-loam LF ~0.80; Church/FHWA common earth swell 25%, shrink -12% via WisDOT 0092-22-05." },
    "rock (blasted)": { b2l: 1.60, b2c: 1.30,
      src: "Church(1981)/FHWA via WisDOT 0092-22-05: blasted rock swells 60% and NEVER recompacts to bank - placed fill stays ~1.3x its bank volume." },
  },
  // Over-excavation of unsuitables: carried as its own bid item with the
  // replacement backfill measured to the SAME neat-line CY (TxDOT Item 400;
  // #853 itself carries three exactly-equal dig/backfill pairs:
  // 3,919/3,919, 411/411, 770/770 CYD). Not yet wired to UI - follow-up.
  over_excavation: { paired_equal_quantities: true },
};
