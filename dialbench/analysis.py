"""Measure a manually chosen ray and inspect image evidence in dial coordinates."""

from math import atan2, cos, degrees, radians, sin

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


def scan_window(dial, settings):
    radius = dial["radius"]
    inner = max(0.12 * radius, 10.0)
    outer = min(0.74 * radius, settings["band_in"] * radius - 3.0)
    return inner, outer


def dark_runs(mask):
    edges = np.diff(mask.astype(np.int8))
    starts = list(np.flatnonzero(edges == 1) + 1)
    ends = list(np.flatnonzero(edges == -1) + 1)
    if mask[0]:
        starts.insert(0, 0)
    if mask[-1]:
        ends.append(len(mask))
    return list(zip(starts, ends))


def polar_scan(image, center, inner, outer, bearings):
    radii = np.arange(inner, outer, 1.0)
    angles = np.radians(bearings)
    xs = center[0] + np.outer(np.cos(angles), radii)
    ys = center[1] + np.outer(np.sin(angles), radii)
    height, width = image.shape
    inside = (xs >= 0) & (xs <= width - 1) & (ys >= 0) & (ys <= height - 1)
    columns = np.clip(np.round(xs), 0, width - 1).astype(np.intp)
    rows = np.clip(np.round(ys), 0, height - 1).astype(np.intp)
    return radii, np.where(inside, image[rows, columns], np.inf)


def ray_values(image, center, bearing, inner, outer, step=0.5):
    distances = np.arange(inner, outer, step)
    radian = radians(bearing)
    xs = center[0] + distances * cos(radian)
    ys = center[1] + distances * sin(radian)
    height, width = image.shape
    inside = (xs >= 0) & (xs <= width - 1) & (ys >= 0) & (ys <= height - 1)
    columns = np.clip(np.round(xs), 0, width - 1).astype(np.intp)
    rows = np.clip(np.round(ys), 0, height - 1).astype(np.intp)
    return distances, np.where(inside, image[rows, columns], np.inf)


def needle_sectors(candidate, support, reach, bearings):
    if not candidate.any() or candidate.all():
        return []
    count = len(candidate)
    start = int(np.flatnonzero(~candidate)[0])
    groups, current = [], []
    for step in range(count):
        index = (start + step) % count
        if candidate[index]:
            current.append(index)
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    sectors = []
    for group in groups:
        weights = support[group]
        angles = np.radians(bearings[group])
        mean = degrees(atan2(float((weights * np.sin(angles)).sum()),
                             float((weights * np.cos(angles)).sum()))) % 360
        sectors.append({"angle": mean, "peak": float(support[group].max()),
                        "reach": float(reach[group].max())})
    sectors.sort(key=lambda sector: sector["peak"], reverse=True)
    return sectors


def resolve_opposites(sectors, window):
    kept = list(sectors)
    changed = True
    while changed:
        changed = False
        for first in kept:
            for second in kept:
                if first is second:
                    continue
                separation = abs((first["angle"] - second["angle"] + 180) % 360 - 180)
                if separation >= 150 and first["reach"] >= second["reach"] + 0.15 * window:
                    kept = [sector for sector in kept if sector is not second]
                    changed = True
                    break
            if changed:
                break
    return kept


def suggest_pointer(dial, settings, image):
    center = dial["center"]
    inner, outer = scan_window(dial, settings)
    window = outer - inner
    if window < 20:
        return {"state": "unavailable"}
    bearings = np.arange(0.0, 360.0, 0.5)
    radii, grid = polar_scan(image, center, inner, outer, bearings)
    finite = grid[np.isfinite(grid)]
    if not finite.size:
        return {"state": "unavailable"}
    median = float(np.median(finite))
    spread = float(np.median(np.abs(finite - median)))
    cut = median - max(10.0, 4 * 1.4826 * spread)
    dark = grid < cut
    support = np.zeros(len(bearings))
    reach = np.zeros(len(bearings))
    for index in range(len(bearings)):
        spans = dark_runs(dark[index])
        if spans:
            support[index] = (sum(stop - start for start, stop in spans) +
                              max(stop - start for start, stop in spans))
            solid = [radii[stop - 1] for start, stop in spans if stop - start >= 8]
            reach[index] = max(solid) if solid else 0.0
    gated = (support > 0) & (reach >= inner + 0.3 * window)
    if not gated.any():
        return {"state": "no_pointer"}
    threshold = max(24.0, 0.4 * float(support[gated].max()))
    sectors = needle_sectors(gated & (support >= threshold), support, reach, bearings)
    if not sectors or sectors[0]["peak"] < max(48.0, 0.38 * window):
        return {"state": "no_pointer"}
    sectors = resolve_opposites(sectors, window)
    top = sectors[0]
    rivals = [sector for sector in sectors[1:] if sector["peak"] > 0.62 * top["peak"]]
    if rivals:
        notable = sorted([top, *rivals], key=lambda sector: sector["peak"], reverse=True)
        return {"state": "uncertain",
                "candidates": [{"angle_deg": round(sector["angle"], 1)} for sector in notable[:4]]}
    if top["reach"] < max(inner + 0.3 * window, 0.42 * dial["radius"]):
        return {"state": "no_pointer"}
    bearing = top["angle"]
    distances, values = ray_values(image, center, bearing, inner, outer)
    spans = [[float(distances[start]), float(distances[stop - 1])]
             for start, stop in dark_runs(values < cut)]
    merged = []
    for span in spans:
        if merged and span[0] - merged[-1][1] <= 2.0:
            merged[-1][1] = span[1]
        else:
            merged.append(span)
    spans = [span for span in merged if span[1] - span[0] >= 4.0]
    visible = sum(span[1] - span[0] for span in spans)
    substantial = [span for span in spans if span[1] - span[0] >= 8.0]
    if (not substantial or visible < max(12.0, 0.12 * window)
            or substantial[-1][1] < max(inner + 0.3 * window, 0.42 * dial["radius"])
            or spans[-1][1] - spans[0][0] < 0.28 * window):
        return {"state": "no_pointer"}
    radian = radians(bearing)
    segments = [{"r1": round(span[0], 1), "r2": round(span[1], 1),
                 "x1": round(center[0] + span[0] * cos(radian), 2),
                 "y1": round(center[1] + span[0] * sin(radian), 2),
                 "x2": round(center[0] + span[1] * cos(radian), 2),
                 "y2": round(center[1] + span[1] * sin(radian), 2)} for span in spans]
    chosen = substantial[-1]
    point = [round(center[0] + chosen[1] * cos(radian), 2),
             round(center[1] + chosen[1] * sin(radian), 2)]
    return {"state": "ready", "angle_deg": round(bearing, 2),
            "reading": scale_at(dial, settings, bearing), "point": point,
            "segments": segments, "visible_px": round(visible, 1),
            "window_px": round(window, 1), "cut_luma": round(cut, 1)}


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
    image = frame.luma(frame.pixels(library, record, settings["source"]))
    result = {"mode": mode, "heading": model.MODES[mode], "settings": settings, "geometry": dial}
    if mode == "reading":
        result["rows"] = [pointer_row(dial, settings)]
        result["suggestion"] = suggest_pointer(dial, settings, image)
    elif mode == "radial":
        result["rows"] = sample_line(dial, settings, image)
    elif mode == "arc":
        result["rows"] = sample_arc(dial, settings, image)
    elif mode == "lighting":
        result["rows"] = light_report(image, dial, settings)
    else:
        result["rows"] = contrast_report(image, dial)
    return result


def pixel_probe(library, record, settings, point):
    image = frame.pixels(library, record, settings["source"])
    color = [int(value) for value in read_pixel(image, point)]
    bearing = model.angle(settings["center"], point)
    distance = model.distance(settings["center"], point)
    return {"x": point[0], "y": point[1], "rgb": color,
            "luma": round(sum(channel * weight for channel, weight in zip(color, (.2126, .7152, .0722))), 2),
            "angle_deg": round(bearing, 3) if distance >= 3 else None,
            "radius_px": round(distance, 2), "source": settings["source"]}
