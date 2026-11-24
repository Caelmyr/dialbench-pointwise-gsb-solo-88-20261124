"""Render an inspection overlay or export the readings captured in a snapshot."""

from csv import DictWriter
from io import StringIO
from math import ceil

import numpy as np
from PIL import Image, ImageDraw

from . import analysis, frame

YELLOW = (242, 180, 82, 240)
TEAL = (101, 224, 209, 240)
DARK = (19, 29, 27, 230)


def tables(report):
    rows = report["rows"]
    title = StringIO(newline="")
    columns = list(dict.fromkeys(key for row in rows for key in row))
    common = ["inspection", "source", "center_x", "center_y"]
    writer = DictWriter(title, fieldnames=[*common, *columns])
    writer.writeheader()
    settings = report["settings"]
    context = {"inspection": report["heading"], "source": settings["source"],
               "center_x": settings["center"][0], "center_y": settings["center"][1]}
    writer.writerows([{**context, **row} for row in rows])
    return title.getvalue()


def outline_arc(draw, dial, radius, color, width):
    marks = ceil(dial["sweep"])
    coords = [tuple(analysis.ray_point(dial, dial["start"] + dial["sign"] * dial["sweep"] * i / marks, radius))
              for i in range(marks + 1)]
    draw.line(coords, fill=color, width=width)


def overlay(library, record, report):
    settings, dial = report["settings"], report["geometry"]
    data = frame.pixels(library, record, settings["source"])
    picture = Image.fromarray(data, "RGB").convert("RGBA")
    mode = report["mode"]
    layer = Image.new("RGBA", picture.size)
    if mode == "lighting":
        luma = frame.luma(data)
        colors = np.zeros((*luma.shape, 4), dtype=np.uint8)
        colors[luma >= settings["bright_cut"]] = (255, 170, 61, 135)
        colors[luma <= settings["dark_cut"]] = (65, 174, 213, 115)
        layer = Image.fromarray(colors, "RGBA")
    draw = ImageDraw.Draw(layer)
    stroke = max(2, min(picture.size) // 270)
    dial_radius = dial["radius"]
    if mode == "arc":
        for fraction in ("band_in", "band_out"):
            outline_arc(draw, dial, dial_radius * settings[fraction], YELLOW, stroke)
    else:
        outline_arc(draw, dial, dial_radius, TEAL, stroke)
    if mode in {"reading", "radial"} and settings["pointer"] is not None:
        draw.line([tuple(dial["center"]), tuple(settings["pointer"])], fill=YELLOW, width=stroke + 1)
    for key, label in (("low_mark", "LOW"), ("high_mark", "HIGH"), ("center", "+")):
        x, y = settings[key]
        r = stroke * 3
        draw.ellipse((x - r, y - r, x + r, y + r), fill=DARK, outline=TEAL, width=stroke)
        draw.text((x + r + 3, y - r), label, fill=TEAL, stroke_fill=DARK, stroke_width=1)
    return frame.encode(Image.alpha_composite(picture, layer).convert("RGB"))
