"""Edit pipeline: webm take + cut marks -> captioned, carded mp4.

Usage: python edit.py <take.webm> <title> <subtitle> <out.mp4>
Cut marks (cut_start/cut_end pairs in <take>.marks.json) are removed; a
title card opens and a brand card closes the video. Everything re-encodes
to uniform H.264 1600x900 30fps so the concat is seamless.
"""
import json
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FF = "ffmpeg"
W, H = 1600, 900
ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
       "-pix_fmt", "yuv420p", "-r", "30", "-an", "-movflags", "+faststart"]


def run(args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr[-1500:])


def card(path, title, sub, brand="CORE TAKEOFF · CORE SOFTWARE"):
    im = Image.new("RGB", (W, H), (13, 16, 20))
    d = ImageDraw.Draw(im)
    big = ImageFont.truetype("C:/Windows/Fonts/segoeuib.ttf", 64)
    med = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 30)
    sml = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 22)
    d.rectangle([0, H - 6, W, H], fill=(43, 160, 90))
    tw = d.textlength(title, font=big)
    d.text(((W - tw) / 2, H / 2 - 90), title, font=big, fill=(240, 244, 248))
    sw = d.textlength(sub, font=med)
    d.text(((W - sw) / 2, H / 2 + 8), sub, font=med, fill=(150, 200, 170))
    bw = d.textlength(brand, font=sml)
    d.text(((W - bw) / 2, H - 70), brand, font=sml, fill=(110, 120, 130))
    im.save(path)


def dur(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    return float(r.stdout.strip())


def main(take, title, sub, out):
    take = Path(take)
    work = take.parent / (take.stem + "_edit")
    work.mkdir(exist_ok=True)
    marks = json.loads(
        (take.parent / f"{take.stem}.marks.json").read_text())["marks"]

    total = dur(take)
    cuts, stack = [], None
    for m in marks:
        if m["kind"] == "cut_start":
            stack = m["t"]
        elif m["kind"] == "cut_end" and stack is not None:
            cuts.append((stack, m["t"]))
            stack = None
    keep, pos = [], 0.0
    for a, b in cuts:
        if a > pos:
            keep.append((pos, a))
        pos = b
    if pos < total:
        keep.append((pos, total))

    parts = []
    for i, (a, b) in enumerate(keep):
        p = work / f"seg{i}.mp4"
        run([FF, "-y", "-ss", f"{a:.2f}", "-to", f"{b:.2f}", "-i", str(take),
             "-vf", f"scale={W}:{H}", *ENC, str(p)])
        parts.append(p)

    tc = work / "title.png"
    card(tc, title, sub)
    tclip = work / "title.mp4"
    run([FF, "-y", "-loop", "1", "-t", "2.4", "-i", str(tc),
         "-vf", f"scale={W}:{H}", *ENC, str(tclip)])
    ec = work / "end.png"
    card(ec, "CORE Takeoff", "core.coresoftware.tech")
    eclip = work / "end.mp4"
    run([FF, "-y", "-loop", "1", "-t", "2.6", "-i", str(ec),
         "-vf", f"scale={W}:{H}", *ENC, str(eclip)])

    lst = work / "list.txt"
    lines = [f"file '{tclip.resolve().as_posix()}'"]
    lines += [f"file '{p.resolve().as_posix()}'" for p in parts]
    lines += [f"file '{eclip.resolve().as_posix()}'"]
    lst.write_text("\n".join(lines))
    run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
         "-c", "copy", str(out)])
    print(f"{out}: {dur(out):.1f}s from {total:.1f}s raw, {len(cuts)} cut(s)")


if __name__ == "__main__":
    main(*sys.argv[1:5])
