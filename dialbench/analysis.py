"""Measure a manually chosen ray and inspect image evidence in dial coordinates."""

from math import cos, radians, sin

import numpy as np

from . import frame, model


def geometry(settings):
    center = settings["center"]
    start = model.angle(center, settings["low_mark"])
    end = model.angle(center, settings["high_mark"])
    sign = 1 if settings["clockwise"] else -1
    return {
        "center": center, "start": start, "sign": sign, "sweep": ((end - start) * sign) % 360,
        "radius": (model.distance(center, settings["low_mark"]) +
                   model.distance(center, settings["high_mark"])) / 2,
    }


def ray_point(dial, bearing, radius):
    radian = radians(bearing)
    return [dial["center"][0] + radius * cos(radian), dial["center"][1] + radius * sin(radian)]


def scale_at(dial, settings, bearing):
    offset = ((bearing - dial["start"]) * dial["sign"]) % 360
    percent = offset / dial["sweep"]
    if percent > 1 + 1e-8:
        return None
    return round(settings["low_value"] + percent * (settings["high_value"] - settings["low_value"]), 4)


def pointer_row(dial, settings):
    marker = settings["pointer"]
    if not marker:
        return {"state": "choose_pointer", "angle_deg": None, "reading": None, "unit": settings["unit"]}
    span = model.distance(dial["center"], marker)
    if span < 3:
        return {"state": "at_pivot", "angle_deg": None, "reading": None, "unit": settings["unit"]}
    bearing = model.angle(dial["center"], marker)
    reading = scale_at(dial, settings, bearing)
    return {"state": "in_range" if reading is not None else "outside_scale",
            "angle_deg": round(bearing, 4), "reading": reading, "unit": settings["unit"],
            "pointer_x": marker[0], "pointer_y": marker[1], "distance_px": round(span, 3)}


def read_pixel(data, value):
    x, y = map(lambda coord: int(round(coord)), value)
    if not 0 <= x < data.shape[1] or not 0 <= y < data.shape[0]:
        return None
    return data[y, x]


def sample_line(dial, settings, image):
    if settings["pointer"] is None or model.distance(dial["center"], settings["pointer"]) < 3:
        return []
    bearing = model.angle(dial["center"], settings["pointer"])
    distances = np.linspace(0, 1.12 * dial["radius"], 65)
    rows = []
    for distance in distances:
        x, y = ray_point(dial, bearing, distance)
        sample = read_pixel(image, [x, y])
        if sample is not None:
            rows.append({"radius_px": round(float(distance), 2), "luma": round(float(sample), 1),
                         "x": round(x, 2), "y": round(y, 2)})
    return rows


def sample_arc(dial, settings, image):
    inner = dial["radius"] * settings["band_in"]
    outer = dial["radius"] * settings["band_out"]
    trace = np.linspace(0, dial["sweep"], 109)
    radii = np.linspace(inner, outer, max(3, min(81, int(outer - inner) + 1)))
    rows = []
    for offset in trace:
        bearing = (dial["start"] + dial["sign"] * offset) % 360
        samples = [read_pixel(image, ray_point(dial, bearing, value)) for value in radii]
        pixels = [value for value in samples if value is not None]
        if pixels:
            x, y = ray_point(dial, bearing, (inner + outer) / 2)
            rows.append({"angle_deg": round(float(bearing), 3),
                         "reading": scale_at(dial, settings, bearing),
                         "luma": round(float(np.mean(pixels)), 2),
                         "min_luma": round(float(np.min(pixels)), 2),
                         "samples": len(pixels), "x": round(x, 2), "y": round(y, 2)})
    return rows


def zones(image, dial):
    y, x = np.ogrid[:image.shape[0], :image.shape[1]]
    center_x, center_y = dial["center"]
    radius = dial["radius"]
    square = (x - center_x) ** 2 + (y - center_y) ** 2
    return {
        "Dial face": square <= (radius * .78) ** 2,
        "Scale and rim": (square > (radius * .78) ** 2) & (square <= (radius * 1.10) ** 2),
        "Surround": square > (radius * 1.10) ** 2,
    }


def light_report(image, dial, settings):
    rows = []
    for name, mask in zones(image, dial).items():
        values = image[mask]
        if values.size:
            rows.append({"zone": name, "pixels": int(values.size),
                         "shadow_pct": round(float(np.mean(values <= settings["dark_cut"]) * 100), 3),
                         "highlight_pct": round(float(np.mean(values >= settings["bright_cut"]) * 100), 3)})
    return rows


def contrast_report(image, dial):
    rows = []
    for name, mask in zones(image, dial).items():
        values = image[mask]
        if values.size:
            p10, median, p90 = np.percentile(values, (10, 50, 90))
            rows.append({"zone": name, "pixels": int(values.size), "p10": round(float(p10), 2),
                         "median": round(float(median), 2), "p90": round(float(p90), 2),
                         "spread": round(float(p90 - p10), 2)})
    return rows


def inspect(library, record, settings, mode):
    dial = geometry(settings)
    if mode == "reading":
        rows = [pointer_row(dial, settings)]
    else:
        image = frame.luma(frame.pixels(library, record, settings["source"]))
        if mode == "radial":
            rows = sample_line(dial, settings, image)
        elif mode == "arc":
            rows = sample_arc(dial, settings, image)
        elif mode == "lighting":
            rows = light_report(image, dial, settings)
        else:
            rows = contrast_report(image, dial)
    return {"mode": mode, "heading": model.MODES[mode], "settings": settings, "geometry": dial, "rows": rows}


def pixel_probe(library, record, settings, point):
    image = frame.pixels(library, record, settings["source"])
    color = [int(value) for value in read_pixel(image, point)]
    bearing = model.angle(settings["center"], point)
    distance = model.distance(settings["center"], point)
    return {"x": point[0], "y": point[1], "rgb": color,
            "luma": round(sum(channel * weight for channel, weight in zip(color, (.2126, .7152, .0722))), 2),
            "angle_deg": round(bearing, 3) if distance >= 3 else None,
            "radius_px": round(distance, 2), "source": settings["source"]}
