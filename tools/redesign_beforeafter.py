"""Before | after pairs of the live app: the fork's UI (before/) beside the
redesign (after/annotated or after/), same demo plan, same viewport.

    .venv/Scripts/python.exe tools/redesign_beforeafter.py <redesign-verify-dir>
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PAIRS = [
    ("01-projects", "01-projects", "Projects"),
    ("04-sheet-detected", "03-lines-detected", "The workspace, one detected sheet"),
    ("05-tools-menu", "11-tools-menu", "The Tools menu"),
    ("06-cutfill-embed", "09-earthwork-cutfill", "Cut / fill, embedded"),
    ("02-settings", "12-settings", "Settings"),
]


def font(size, bold=False):
    for name in (["segoeuib.ttf", "arialbd.ttf"] if bold else ["segoeui.ttf", "arial.ttf"]):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def main():
    d = Path(sys.argv[1]).resolve()
    out = d / "before-after"
    out.mkdir(exist_ok=True)
    for b, a, title in PAIRS:
        bp = d / "before" / f"{b}.png"
        ap = d / "after" / f"{a}.png"
        if not (bp.exists() and ap.exists()):
            print("skip", b, a)
            continue
        B = Image.open(bp).convert("RGB")
        A = Image.open(ap).convert("RGB")
        H = 810
        B = B.resize((round(B.width * H / B.height), H), Image.LANCZOS)
        A = A.resize((round(A.width * H / A.height), H), Image.LANCZOS)
        top = 60
        c = Image.new("RGB", (B.width + A.width + 24, H + top), (8, 12, 18))
        c.paste(B, (0, top))
        c.paste(A, (B.width + 24, top))
        dr = ImageDraw.Draw(c)
        dr.text((14, 18), f"BEFORE  ·  core-cutfill UI @ 5f6fdc2  ·  {title}", font=font(21, True), fill=(154, 168, 182))
        dr.text((B.width + 38, 18), "AFTER  ·  core-redesign, live on :8600", font=font(21, True), fill=(92, 203, 146))
        p = out / f"{a}.png"
        c.save(p)
        print("wrote", p.relative_to(d))


if __name__ == "__main__":
    main()
