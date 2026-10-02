"""Annotate the redesign's live captures and set them beside the mockups.

    .venv/Scripts/python.exe tools/redesign_annotate.py <capture-set-dir>

Reads <dir>/capture-log.json (written by tools/redesign_capture.py: every
shot with the boxes of the elements its claims are about, read from the
live page at capture time) and writes:

  <dir>/annotated/<shot>.png     numbered boxes + a notes column
                                  green = checked and working
                                  amber = a deliberate difference from the mockup
                                  red   = a defect still open
  <dir>/compare/<shot>.png       the mockup it was built to | the annotated capture
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
MOCK = ROOT.parent / "design-mockups" / "2026-09-25"
COL = {"ok": (47, 181, 162), "diff": (229, 163, 61), "defect": (229, 83, 61)}
KIND_WORD = {"ok": "checked", "diff": "different on purpose", "defect": "OPEN DEFECT"}
# which mockup each shot was built to (round 3, steel set + the slate
# cut/fill line-review screen)
MOCKUP_FOR = {
    "01-projects": "8-multitrade-steel/78-projects.png",
    "03-lines-detected": "8-multitrade-steel/75-lines.png",
    "04-plans": "8-multitrade-steel/76-project.png",
    "05-summary": "8-multitrade-steel/77-summary.png",
    "06-trenching": "8-multitrade-steel/73-trenching.png",
    "07-areas": "8-multitrade-steel/74-areas.png",
    "08-counts": "8-multitrade-steel/72-counts.png",
    "09-earthwork-cutfill": "8-multitrade-steel/71-earthwork.png",
    "10-3d": "8-multitrade-steel/80-3d.png",
}


def font(size, bold=False):
    for name in (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def annotate(img_path: Path, marks: list, out: Path):
    im = Image.open(img_path).convert("RGB")
    W, H = im.size
    panel_w = 460
    canvas = Image.new("RGB", (W + panel_w, H), (12, 16, 22))
    canvas.paste(im, (0, 0))
    d = ImageDraw.Draw(canvas)
    f_num, f_txt, f_head = font(17, True), font(16), font(18, True)
    y = 18
    d.text((W + 20, y), "What this capture shows", font=f_head, fill=(230, 234, 238))
    y += 34
    n = 0
    for m in marks:
        r = m.get("rect")
        col = COL.get(m["kind"], COL["ok"])
        n += 1
        # an element scrolled (partly) out of the capture: clip it to the
        # image, and treat one that is wholly outside as not on screen
        if r:
            rx, ry, rw, rh = r
            if rx >= W or ry >= H or rx + rw <= 0 or ry + rh <= 0:
                r = None
        if r:
            rx, ry, rw, rh = r
            x0, y0 = max(2, rx - 4), max(2, ry - 4)
            x1, y1 = min(W - 3, rx + rw + 4), min(H - 3, ry + rh + 4)
            for k in range(3):
                d.rounded_rectangle([x0 - k, y0 - k, x1 + k, y1 + k], radius=8, outline=col)
            bx, by = min(max(x0 - 14, 2), W - 30), max(y0 - 14, 2)
            d.ellipse([bx, by, bx + 28, by + 28], fill=col)
            tw = d.textlength(str(n), font=f_num)
            d.text((bx + 14 - tw / 2, by + 4), str(n), font=f_num, fill=(10, 14, 18))
        # the note
        d.ellipse([W + 20, y, W + 44, y + 24], fill=col)
        tw = d.textlength(str(n), font=f_num)
        d.text((W + 32 - tw / 2, y + 2), str(n), font=f_num, fill=(10, 14, 18))
        d.text((W + 54, y + 2), KIND_WORD.get(m["kind"], ""), font=font(12, True), fill=col)
        y += 22
        for line in textwrap.wrap(m["note"] + ("" if r else "  (element not on screen)"), 44):
            d.text((W + 54, y), line, font=f_txt, fill=(210, 216, 222))
            y += 21
        y += 12
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def compare(mock_path: Path, ann_path: Path, out: Path, title: str):
    a = Image.open(mock_path).convert("RGB")
    b = Image.open(ann_path).convert("RGB")
    H = 900
    a = a.resize((round(a.width * H / a.height), H), Image.LANCZOS)
    b = b.resize((round(b.width * H / b.height), H), Image.LANCZOS)
    top = 56
    canvas = Image.new("RGB", (a.width + b.width + 24, H + top), (8, 12, 18))
    canvas.paste(a, (0, top))
    canvas.paste(b, (a.width + 24, top))
    d = ImageDraw.Draw(canvas)
    d.text((14, 16), f"MOCKUP  ·  {mock_path.parent.name}/{mock_path.name}", font=font(20, True), fill=(154, 168, 182))
    d.text((a.width + 38, 16), f"LIVE APP on :8600  ·  {title}", font=font(20, True), fill=(92, 203, 146))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out


def main():
    d = Path(sys.argv[1]).resolve()
    log = json.loads((d / "capture-log.json").read_text())
    for s in log["shots"]:
        stem = Path(s["file"]).stem
        src = d / s["file"]
        marks = s.get("marks") or []
        ann = annotate(src, marks, d / "annotated" / f"{stem}.png") if marks else src
        mk = MOCKUP_FOR.get(stem)
        if mk and (MOCK / mk).exists():
            compare(MOCK / mk, ann, d / "compare" / f"{stem}.png", stem)
        print("done", stem, len(marks), "marks")


if __name__ == "__main__":
    main()
