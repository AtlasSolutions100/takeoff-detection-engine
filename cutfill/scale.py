"""Sheet scale: feet per inch of paper.

Candidates come from printed text ("SCALE: 1\" = 20'", "1 inch = 20 ft.",
"GRAPHIC SCALE 30'"). Every candidate is kept and shown; the engine only
picks a default. A wrong scale moves volumes with the SQUARE of the error,
so the UI asks the operator to confirm it.
"""
from __future__ import annotations

import re
from collections import Counter

from .labels import Label, RE_SCALE, RE_SCALE_RATIO

RE_GRAPHIC = re.compile(r"GRAPHIC\s+SCALE\s*[:\-]?\s*(\d+)\s*'", re.I)


def scale_candidates(labels: list[Label], texts: list[str] | None = None) -> list[dict]:
    cands: list[dict] = []
    for lab in labels:
        if lab.kind == "scale" and lab.scale:
            cands.append({"ft_per_in": lab.scale, "text": lab.text, "label_id": lab.id, "source": lab.source,
                          "bbox": list(lab.bbox), "confidence": "printed"})
    for t in texts or []:
        for m in RE_GRAPHIC.finditer(t):
            cands.append({"ft_per_in": float(m.group(1)), "text": m.group(0), "label_id": None, "source": "text",
                          "bbox": None, "confidence": "graphic-bar caption (inference)"})
    # de-duplicate identical values
    seen = Counter(round(c["ft_per_in"], 4) for c in cands)
    out = []
    used = set()
    for c in cands:
        k = round(c["ft_per_in"], 4)
        if k in used:
            continue
        used.add(k)
        c["votes"] = seen[k]
        out.append(c)
    out.sort(key=lambda c: (-c["votes"], c["ft_per_in"]))
    return out


def pick_default(cands: list[dict]) -> dict | None:
    if not cands:
        return None
    printed = [c for c in cands if c["confidence"] == "printed"]
    return (printed or cands)[0]
