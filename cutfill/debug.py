"""Debug renders (PIL) used while developing; not part of the product UI."""
from __future__ import annotations

import io

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .ingest import Document


def page_image(doc: Document, page: int, dpi: float = 72) -> Image.Image:
    return Image.open(io.BytesIO(doc.render(page, dpi=dpi))).convert("RGB")


def draw_chains(img: Image.Image, chains, dpi: float, color_fn=None, width: int = 2, only=None):
    f = dpi / 72.0
    d = ImageDraw.Draw(img)
    for c in chains:
        if only is not None and not only(c):
            continue
        col = color_fn(c) if color_fn else (255, 0, 0)
        pts = [(float(x) * f, float(y) * f) for x, y in c.pts]
        if len(pts) >= 2:
            d.line(pts, fill=col, width=width)
    return img


def draw_points(img: Image.Image, pts, dpi: float, color=(0, 0, 255), r: int = 4, labels=None):
    f = dpi / 72.0
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()
    for i, (x, y) in enumerate(pts):
        X, Y = x * f, y * f
        d.ellipse([X - r, Y - r, X + r, Y + r], outline=color, width=2)
        if labels is not None:
            d.text((X + r + 2, Y - 8), str(labels[i]), fill=color, font=font)
    return img


def draw_boxes(img: Image.Image, boxes, dpi: float, color=(0, 160, 0), labels=None, width: int = 2):
    f = dpi / 72.0
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 13)
    except Exception:
        font = ImageFont.load_default()
    for i, (x0, y0, x1, y1) in enumerate(boxes):
        d.rectangle([x0 * f, y0 * f, x1 * f, y1 * f], outline=color, width=width)
        if labels is not None:
            d.text((x0 * f, y0 * f - 14), str(labels[i]), fill=color, font=font)
    return img
