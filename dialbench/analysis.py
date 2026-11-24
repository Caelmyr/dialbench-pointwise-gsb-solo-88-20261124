"""Measure a manually chosen ray and inspect image evidence in dial coordinates."""

from math import ceil, cos, radians, sin

import numpy as np

from . import frame, model

ARC_SAMPLES = 109
ARC_FINE_STEP = .5
ARC_SOURCE_COST = 4.0
ARC_SWITCH_COST = .5
ARC_GAP_COST = 1.55


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


def band_geometry(dial, settings):
    inner = dial["radius"] * settings["band_in"]
    outer = dial["radius"] * settings["band_out"]
    radii = np.linspace(inner, outer, max(3, min(81, int(outer - inner) + 1)))
    return radii, (inner + outer) / 2


def band_values(luma, dial, radii, offsets):
    """Sample every band radius at each angular offset of the calibrated sweep."""
    height, width = luma.shape
    bearings = np.radians((dial["start"] + dial["sign"] * offsets) % 360)[:, None]
    x = np.rint(dial["center"][0] + np.cos(bearings) * radii[None, :]).astype(int)
    y = np.rint(dial["center"][1] + np.sin(bearings) * radii[None, :]).astype(int)
    inside = (x >= 0) & (x < width) & (y >= 0) & (y < height)
    patch = np.full(x.shape, np.nan, np.float32)
    patch[inside] = luma[y[inside], x[inside]]
    return patch


def legibility(patch, bright_cut):
    """How strongly a tick survives its arc, and how much of the band is blown out.

    Tick relief is the spread between the pale dial face and the dark ink.
    That spread collapses wherever a highlight floods the band, so spot height
    is unreliable; ``peak`` instead keeps the best surviving relief within one
    major-tick spacing of every reading.
    """
    finite = np.isfinite(patch)
    filled = np.maximum(finite.sum(axis=1), 1)
    usable = finite.any(axis=1)
    face = np.zeros(len(patch))
    ink = np.zeros(len(patch))
    if usable.any():
        rows = patch[usable]
        face[usable] = np.nanpercentile(rows, 95, axis=1)
        ink[usable] = np.nanpercentile(rows, 5, axis=1)
    relief = np.maximum(face - ink, 0)
    wash = np.where(finite, patch >= bright_cut, False).sum(axis=1) / filled
    wash[~usable] = 1
    return relief, wash


def neighbours(values, half, fill):
    padded = np.pad(values, half, mode="edge")
    window = np.lib.stride_tricks.sliding_window_view(padded, 2 * half + 1)
    return getattr(window, fill)(axis=1)


def tick_window(relief, step):
    """Pick a window matching the visible major-tick spacing, bounded and sane."""
    peak = relief if relief.size else np.zeros(1)
    threshold = max(8.0, .35 * float(np.percentile(peak, 90)))
    found = np.flatnonzero(peak >= threshold)
    ridge = [index for index in found
             if (index == 0 or peak[index] >= peak[index - 1])
             and (index == len(peak) - 1 or peak[index] >= peak[index + 1])]
    spacing = float(np.median(np.diff(ridge))) * step if len(ridge) >= 4 else 3 * step
    return max(1, int(round(min(max(spacing, 3.0), 25.0) / step)))


def legibility_curve(luma, dial, settings, radii, offsets):
    patch = band_values(luma, dial, radii, offsets)
    relief, wash = legibility(patch, settings["bright_cut"])
    step = offsets[1] - offsets[0] if len(offsets) > 1 else 1.0
    half = tick_window(relief, step)
    detail = neighbours(relief, half, "max")
    shower = max(1, int(round(3.0 / step)))
    clear = np.clip(1 - neighbours(wash, shower, "mean") / .45, 0, 1)
    return detail, clear


def sample_points(dial):
    steps = np.linspace(0, dial["sweep"], ARC_SAMPLES)
    fine = np.arange(0, dial["sweep"] + ARC_FINE_STEP / 2, ARC_FINE_STEP)
    fine[-1] = dial["sweep"]
    return steps, fine


def source_plan(dial, settings, lumas):
    """Choose one photo per arc sample while keeping stretches continuous.

    A dynamic program trades legibility of the chosen photo against the cost of
    switching photos; a sample goes blank only when neither photo is legible
    enough to beat leaving it empty. Returns the segments as
    ``[first sample, last sample, source index or blank]``.
    """
    radii, _ = band_geometry(dial, settings)
    steps, fine = sample_points(dial)
    curves = [legibility_curve(luma, dial, settings, radii, fine) for luma in lumas]
    reference = max(8.0, float(np.percentile(np.concatenate([curve[0] for curve in curves]), 75)))
    index = np.clip(np.rint(steps / ARC_FINE_STEP).astype(int), 0, len(fine) - 1)
    quality = np.array([[min(1.0, curve[0][i] * curve[1][i] / (.4 * reference)) for i in index]
                        for curve in curves])
    count = len(quality)
    blank = count
    cost = np.stack([(1 - row) * ARC_SOURCE_COST for row in quality]
                    + [np.full(len(steps), ARC_GAP_COST)], axis=1)
    cost[:, 0] -= .06
    transition = np.zeros((count + 1, count + 1))
    transition[:count, :count] = ARC_SWITCH_COST
    np.fill_diagonal(transition[:count, :count], 0)
    transition[:count, blank] = transition[blank, :count] = .3
    score = np.empty((len(steps), count + 1))
    trail = np.zeros((len(steps), count + 1), np.int8)
    score[0] = cost[0]
    for row in range(1, len(steps)):
        for state in range(count + 1):
            previous = score[row - 1] + transition[:, state]
            trail[row, state] = int(np.argmin(previous))
            score[row, state] = previous[trail[row, state]] + cost[row, state]
    chosen = np.empty(len(steps), np.int8)
    chosen[-1] = int(np.argmin(score[-1]))
    for row in range(len(steps) - 1, 0, -1):
        chosen[row - 1] = trail[row, chosen[row]]
    segments = []
    start = 0
    for row in range(1, len(steps) + 1):
        if row == len(steps) or chosen[row] != chosen[start]:
            segments.append([start, row - 1, int(chosen[start])])
            start = row
    return steps, absorb_slivers(segments, quality, blank), quality


def absorb_slivers(segments, quality, blank):
    """Fold one-sample islands into the stronger neighbour unless they are clearly best."""
    def strength(segment):
        return -1.0 if segment[2] == blank else float(np.mean(
            quality[segment[2]][segment[0]:segment[1] + 1]))

    while len(segments) > 1:
        fold = next(((position, segment) for position, segment in enumerate(segments)
                     if segment[1] - segment[0] + 1 < 2
                     and strength(segment) <= max([
                         strength(segments[position - 1]) if position else -2.0,
                         strength(segments[position + 1]) if position + 1 < len(segments) else -2.0])
                     + .15), None)
        if fold is None:
            break
        position, segment = fold
        options = [(position - 1, strength(segments[position - 1]))] if position else []
        if position + 1 < len(segments):
            options.append((position + 1, strength(segments[position + 1])))
        target = max(options, key=lambda option: option[1])[0]
        if target < position:
            segments[target][1] = segment[1]
        else:
            segments[target][0] = segment[0]
        segments.pop(position)
    merged = [segments[0]]
    for segment in segments[1:]:
        if segment[2] == merged[-1][2]:
            merged[-1][1] = segment[1]
        else:
            merged.append(segment)
    return merged


def gap_row(dial, settings, offset):
    bearing = (dial["start"] + dial["sign"] * offset) % 360
    return {"angle_deg": round(float(bearing), 3), "reading": scale_at(dial, settings, bearing),
            "luma": None, "min_luma": None, "samples": 0, "x": None, "y": None,
            "source": None, "gap": True}


def stitched_scan(dial, settings, roles, lumas):
    radii, middle = band_geometry(dial, settings)
    steps, segments, _ = source_plan(dial, settings, lumas)
    rows = []
    missing = []
    for start, end, state in segments:
        name = None if state == len(roles) else roles[state]
        if name is None:
            missing.append({"start": round(float(steps[start]), 3),
                            "end": round(float(steps[end]), 3),
                            "reading_start": scale_at(
                                dial, settings, (dial["start"] + dial["sign"] * steps[start]) % 360),
                            "reading_end": scale_at(
                                dial, settings, (dial["start"] + dial["sign"] * steps[end]) % 360),
                            "samples": end - start + 1})
            rows.extend(gap_row(dial, settings, offset) for offset in steps[start:end + 1])
            continue
        luma = lumas[state]
        for offset in steps[start:end + 1]:
            bearing = (dial["start"] + dial["sign"] * offset) % 360
            pixels = [pixel for pixel in
                      (read_pixel(luma, ray_point(dial, bearing, value)) for value in radii)
                      if pixel is not None]
            x, y = ray_point(dial, bearing, middle)
            rows.append({"angle_deg": round(float(bearing), 3),
                         "reading": scale_at(dial, settings, bearing),
                         "luma": round(float(np.mean(pixels)), 2),
                         "min_luma": round(float(np.min(pixels)), 2),
                         "samples": len(pixels), "x": round(x, 2), "y": round(y, 2),
                         "source": name, "gap": False})
    return rows, segment_rows(segments, steps, roles, dial, settings), missing


def segment_rows(segments, steps, roles, dial, settings):
    entries = []
    for start, end, state in segments:
        first = (dial["start"] + dial["sign"] * steps[start]) % 360
        last = (dial["start"] + dial["sign"] * steps[end]) % 360
        entries.append({
            "source": None if state == len(roles) else roles[state],
            "start": round(float(steps[start]), 3), "end": round(float(steps[end]), 3),
            "start_angle": round(float(first), 3), "end_angle": round(float(last), 3),
            "reading_start": scale_at(dial, settings, first),
            "reading_end": scale_at(dial, settings, last),
            "samples": end - start + 1})
    return entries


def band_scan(dial, settings, lumas):
    """Scan the calibrated band, stitching aligned photos when the split is adopted."""
    roles = [role for role in lumas if lumas[role] is not None]
    if not settings["band_split"] or len(roles) < 2:
        role = settings["source"] if settings["source"] in lumas else (roles[0] if roles else None)
        image = lumas.get(role)
        rows = [] if image is None else sample_arc(dial, settings, image)
        return {"rows": rows, "segments": [], "missing": [], "stitched": False,
                "roles": [role] if role else []}
    rows, segments, missing = stitched_scan(dial, settings, roles, [lumas[role] for role in roles])
    return {"rows": rows, "segments": segments, "missing": missing, "stitched": True, "roles": roles}


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
    radii, middle = band_geometry(dial, settings)
    trace = np.linspace(0, dial["sweep"], ARC_SAMPLES)
    rows = []
    for offset in trace:
        bearing = (dial["start"] + dial["sign"] * offset) % 360
        samples = [read_pixel(image, ray_point(dial, bearing, value)) for value in radii]
        pixels = [value for value in samples if value is not None]
        if pixels:
            x, y = ray_point(dial, bearing, middle)
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


def luma_photos(library, record, settings, roles):
    return {role: frame.luma(frame.pixels(library, record, role)) for role in roles}


def inspect(library, record, settings, mode, planning=False):
    dial = geometry(settings)
    if mode == "reading":
        rows = [pointer_row(dial, settings)]
        return {"mode": mode, "heading": model.MODES[mode], "settings": settings,
                "geometry": dial, "rows": rows}
    if mode == "arc":
        lumas = luma_photos(library, record, settings, list(record["sources"]))
        if planning:
            roles = list(record["sources"])
            rows, segments, missing = stitched_scan(dial, settings, roles,
                                                    [lumas[role] for role in roles])
            return {"mode": mode, "heading": model.MODES[mode], "settings": settings,
                    "geometry": dial, "rows": rows, "segments": segments, "missing": missing,
                    "stitched": True, "roles": roles, "proposal": True}
        result = band_scan(dial, settings, lumas)
        return {"mode": mode, "heading": model.MODES[mode], "settings": settings,
                "geometry": dial, **result}
    image = frame.luma(frame.pixels(library, record, settings["source"]))
    if mode == "radial":
        rows = sample_line(dial, settings, image)
    elif mode == "lighting":
        rows = light_report(image, dial, settings)
    else:
        rows = contrast_report(image, dial)
    return {"mode": mode, "heading": model.MODES[mode], "settings": settings, "geometry": dial, "rows": rows}


def pixel_probe(library, record, settings, point, role=None):
    image = frame.pixels(library, record, role or settings["source"])
    color = [int(value) for value in read_pixel(image, point)]
    bearing = model.angle(settings["center"], point)
    distance = model.distance(settings["center"], point)
    return {"x": point[0], "y": point[1], "rgb": color,
            "luma": round(sum(channel * weight for channel, weight in zip(color, (.2126, .7152, .0722))), 2),
            "angle_deg": round(bearing, 3) if distance >= 3 else None,
            "radius_px": round(distance, 2), "source": role or settings["source"]}
