"""Suggest a dial centre and a scale reference circle from a straight-on photo.

The suggestion trusts the graduated scale, not the most complete circle in the
photo.  Radial tick strokes vote on the centre and the tick ends describe the
scale band, so a protective bezel that is more complete but off-centre cannot
pull the centre away from the printed scale.  Ticks may be partly hidden: every
visible stroke still contributes, and the amount of confirmed arc is reported.

The result is only a proposal.  The two scale marks and their readings are
returned unchanged; adopting a proposal keeps them and only rewrites the centre,
the reference radius and the scan band derived from it.
"""

from math import atan2, cos, hypot, pi, radians

import cv2
import numpy as np

MAX_SIDE = 1400
RADIAL_TOLERANCE_DEG = 18.0
MIN_STROKES = 6
SECTORS = 36
BAND_INSET = .008


def _coverage(angles, sectors=SECTORS):
    if not len(angles):
        return 0.0
    flags = np.zeros(sectors, dtype=bool)
    for value in np.asarray(angles, dtype=float):
        flags[int(np.floor((value % (2 * pi)) / (2 * pi) * sectors)) % sectors] = True
    return float(flags.mean())


def _weighted_percentile(values, weights, quantile):
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    usable = np.isfinite(values) & (weights > 0)
    values, weights = values[usable], weights[usable]
    if values.size < 3:
        return None
    order = np.argsort(values)
    values, weights = values[order], weights[order]
    cumulative = np.cumsum(weights)
    if cumulative[-1] <= 0:
        return None
    return float(np.interp(quantile, cumulative / cumulative[-1], values))


def _prepare(luma, hint, radius):
    """Crop around the hint and shrink only as far as MAX_SIDE allows."""
    luma = np.asarray(luma, dtype=np.float32)
    height, width = luma.shape
    half = max(12, int(round(radius * 1.9)))
    left, top = max(0, int(round(hint[0] - half))), max(0, int(round(hint[1] - half)))
    right, bottom = min(width, left + 2 * half), min(height, top + 2 * half)
    crop = np.ascontiguousarray(luma[top:bottom, left:right], dtype=np.float32)
    if not crop.size:
        return crop, 1.0, np.array([left, top], dtype=float)
    scale = min(1.0, MAX_SIDE / float(max(crop.shape)))
    if scale < 1.0:
        crop = cv2.resize(crop, (max(8, int(round(crop.shape[1] * scale))),
                                 max(8, int(round(crop.shape[0] * scale)))),
                          interpolation=cv2.INTER_AREA)
    return crop, scale, np.array([left, top], dtype=float)


def _segments(gray, minimum):
    detector = cv2.createLineSegmentDetector(cv2.LSD_REFINE_STD)
    found = detector.detect(np.clip(gray, 0, 255).astype(np.uint8))[0]
    strokes = []
    if found is None:
        return strokes
    for x1, y1, x2, y2 in found.reshape(-1, 4):
        start = np.array([x1, y1], dtype=float)
        end = np.array([x2, y2], dtype=float)
        vector = end - start
        length = float(hypot(*vector))
        if length < minimum:
            continue
        strokes.append({"start": start, "end": end, "vector": vector / length,
                        "length": length, "middle": (start + end) / 2})
    return strokes


def _radial_strokes(strokes, center, radius, tolerance=RADIAL_TOLERANCE_DEG):
    low, high = .40 * radius, 1.45 * radius
    kept = []
    for stroke in strokes:
        near = min(hypot(*(stroke["start"] - center)), hypot(*(stroke["end"] - center)))
        far = max(hypot(*(stroke["start"] - center)), hypot(*(stroke["end"] - center)))
        if far < low or near > high or far > high:
            continue
        if stroke["length"] > .30 * radius:
            continue
        offset = stroke["middle"] - center
        distance = float(hypot(*offset))
        if distance < 1e-6:
            continue
        alignment = abs(float(np.dot(stroke["vector"], offset / distance)))
        if alignment < cos(radians(tolerance)):
            continue
        kept.append((stroke, near, far, alignment))
    return kept


def _solve_normal(rows, targets, weights):
    weighted = rows * weights[:, None]
    normal = weighted.T @ rows + np.eye(2) * 1e-9
    return np.linalg.solve(normal, weighted.T @ targets)


def _line_center(strokes, seed, cap=36.0, scale=2.0, rounds=12):
    """Robust convergence point of the radial tick strokes."""
    if len(strokes) < 2:
        return np.array(seed, dtype=float), None, 0
    normals = np.array([[-stroke["vector"][1], stroke["vector"][0]] for stroke, _, _, _ in strokes])
    targets = np.array([float(np.dot([-stroke["vector"][1], stroke["vector"][0]], stroke["start"]))
                        for stroke, _, _, _ in strokes])
    base = np.array([min(stroke["length"], cap) * alignment for stroke, _, _, alignment in strokes])
    base = base / max(float(np.median(base)), 1e-9)
    center = np.array(seed, dtype=float)
    weights = base.copy()
    for _ in range(rounds):
        if weights.sum() <= 0:
            break
        updated = _solve_normal(normals, targets, weights)
        residual = normals @ updated - targets
        spread = 1.4826 * float(np.median(np.abs(residual))) + 1e-6
        ratio = residual / (scale * max(spread, .5))
        weights = base / (1.0 + ratio * ratio)
        settled = float(np.hypot(*(updated - center))) < 1e-3
        center = updated
        if settled:
            break
    residual = normals @ center - targets
    spread = 1.4826 * float(np.median(np.abs(residual)))
    keep = np.abs(residual) <= max(1.0, 3.0 * spread)
    if keep.sum() < MIN_STROKES:
        return center, _uncertainty(normals, residual, base), int(keep.sum())
    trimmed = base * keep
    if trimmed.sum() > 0:
        refined = _solve_normal(normals, targets, trimmed)
        residual = normals @ refined - targets
        settled = 1.4826 * float(np.median(np.abs(residual[keep])))
        keep = np.abs(residual) <= max(1.0, 3.0 * settled)
        if keep.sum() >= MIN_STROKES:
            center = refined
    accepted = np.abs(residual) <= max(1.0, 3.0 * 1.4826 * float(np.median(np.abs(residual))))
    return center, _uncertainty(normals, residual, base * accepted), int(accepted.sum())


def _ring_centers(rings, center, agreement, floor):
    """Fuse tick-end circles that agree with each other and with the strokes."""
    usable = [ring for ring in rings
              if ring["points"] >= MIN_STROKES and ring["coverage"] >= .15
              and float(hypot(*(ring["center"] - center))) <= max(agreement, 2.0 * floor)]
    if len(usable) < 2:
        return None
    total = np.zeros((2, 2))
    right = np.zeros(2)
    for ring in usable:
        angles = ring["angles"]
        cos, sin = np.cos(angles), np.sin(angles)
        normal = np.array([[np.sum(cos * cos), np.sum(cos * sin)],
                           [np.sum(cos * sin), np.sum(sin * sin)]])
        sigma = max(.35, ring["rms"])
        weight = np.linalg.inv(normal + np.eye(2) * 1e-6) / (sigma * sigma)
        total += weight
        right += weight @ ring["center"]
    if not np.all(np.isfinite(total)) or abs(np.linalg.det(total)) < 1e-12:
        return None
    fused = np.linalg.solve(total, right)
    if not np.all(np.isfinite(fused)):
        return None
    if float(hypot(*(fused - center))) > max(agreement, 2.0 * floor):
        return None
    return fused


def _uncertainty(normals, residual, weights):
    """Largest 1-sigma axis of the centre fit, in working pixels."""
    if weights.sum() <= 1e-9:
        return None
    matrix = (normals * weights[:, None]).T @ normals
    try:
        covariance = np.linalg.inv(matrix + np.eye(2) * 1e-9)
    except np.linalg.LinAlgError:
        return None
    degrees = max(1, int((weights > 0).sum()) - 2)
    variance = float(np.sum(weights * residual ** 2) / degrees)
    return float(np.sqrt(max(variance * float(np.linalg.eigvalsh(covariance)[-1]), 0.0)))


def _fit_circle(points, weights):
    points = np.asarray(points, dtype=float)
    weights = np.asarray(weights, dtype=float)
    if len(points) < 3:
        return None
    rows = np.c_[points[:, 0], points[:, 1], np.ones(len(points))]
    targets = -(points[:, 0] ** 2 + points[:, 1] ** 2)
    active = weights.copy()
    solution = None
    for _ in range(15):
        root = np.sqrt(np.maximum(active, 0))
        peak = float(root.max())
        if peak < 1e-12:
            break
        root = root / peak
        solution, *_ = np.linalg.lstsq(rows * root[:, None], targets * root, rcond=None)
        cx, cy = -solution[0] / 2, -solution[1] / 2
        radius = float(np.sqrt(max(cx * cx + cy * cy - solution[2], 1e-9)))
        residual = np.hypot(points[:, 0] - cx, points[:, 1] - cy) - radius
        sigma = max(.3, 1.4826 * float(np.median(np.abs(residual))))
        ratio = residual / (4.685 * sigma)
        active = weights * np.where(np.abs(ratio) < 1, (1 - ratio * ratio) ** 2, 0.0)
        if active.sum() < 1e-9:
            break
    if solution is None:
        return None
    cx, cy = -solution[0] / 2, -solution[1] / 2
    radius = float(np.sqrt(max(cx * cx + cy * cy - solution[2], 1e-9)))
    if not (np.isfinite(cx) and np.isfinite(cy) and np.isfinite(radius)) or radius < 1:
        return None
    residual = np.hypot(points[:, 0] - cx, points[:, 1] - cy) - radius
    sigma = max(.35, 1.4826 * float(np.median(np.abs(residual))))
    inliers = np.abs(residual) <= max(.6, 3.0 * sigma)
    if inliers.sum() < 3:
        return None
    rms = float(np.sqrt(np.mean(residual[inliers] ** 2)))
    return {"center": np.array([cx, cy]), "radius": radius, "inliers": inliers, "rms": rms}


def _radius_peaks(radii, weights, bandwidth, share=.06):
    radii = np.asarray(radii, dtype=float)
    weights = np.asarray(weights, dtype=float)
    usable = np.isfinite(radii) & (weights > 0)
    radii, weights = radii[usable], weights[usable]
    if radii.size < 4:
        return []
    low, high = float(radii.min() - bandwidth), float(radii.max() + bandwidth)
    if high - low < 4 * bandwidth:
        high = low + 4 * bandwidth
    edges = np.arange(low, high + bandwidth, bandwidth)
    histogram, _ = np.histogram(radii, bins=edges, weights=weights)
    smooth = np.convolve(histogram, [1, 2, 3, 2, 1], mode="same")
    need = max(2.0, share * float(weights.sum()))
    found = [[(edges[i] + edges[i + 1]) / 2, float(smooth[i])] for i in range(1, len(smooth) - 1)
             if smooth[i] >= smooth[i - 1] and smooth[i] > smooth[i + 1] and smooth[i] >= need]
    merged = []
    for center, weight in sorted(found):
        if merged and center - merged[-1][0] < 2.5 * bandwidth:
            previous = merged[-1]
            total = previous[1] + weight
            previous[0] = (previous[0] * previous[1] + center * weight) / total
            previous[1] = total
        else:
            merged.append([center, weight])
    merged.sort(key=lambda peak: -peak[1])
    return merged


def _sample(image, center, low, high, angles=720, step=.5):
    """Polar strip around a centre, clamped to the crop so it stays rectangular."""
    height, width = image.shape
    corners = np.array([[0, 0], [width - 1, 0], [0, height - 1], [width - 1, height - 1]])
    reachable = float(np.min(np.linalg.norm(corners - center, axis=1)))
    high = max(low + step * 4, min(float(high), reachable - step))
    radii = np.arange(low, high + step * .5, step, dtype=np.float32)
    bearings = np.linspace(0, 2 * pi, angles, endpoint=False, dtype=np.float32)
    sample_x = (center[0] + np.cos(bearings)[:, None] * radii[None, :]).astype(np.float32)
    sample_y = (center[1] + np.sin(bearings)[:, None] * radii[None, :]).astype(np.float32)
    valid = cv2.remap(np.ones_like(image), sample_x, sample_y, cv2.INTER_NEAREST,
                      borderMode=cv2.BORDER_CONSTANT, borderValue=0) > .5
    sampled = cv2.remap(image, sample_x, sample_y, cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REPLICATE)
    return sampled, radii, bearings, valid


def _fit_edge_circle(image, center, radius, band=2.0, angles=1080):
    sampled, radii, bearings, valid = _sample(image, center, radius - band, radius + band, angles)
    usable = valid.sum(axis=1) >= max(5, len(radii) - 2)
    if usable.sum() < 12:
        return None
    strip = cv2.GaussianBlur(sampled, (1, 3), 0)
    gradient = np.abs(np.gradient(strip, axis=1))
    gradient = np.where(valid, gradient, 0.0)
    noise = np.median(gradient[valid]) + 1e-6
    strongest = np.argmax(gradient, axis=1)
    strength = gradient[np.arange(gradient.shape[0]), strongest]
    chosen = usable & (strength > max(2.5 * noise, .2 * float(np.percentile(strength[usable], 90))))
    if chosen.sum() < 12:
        return None
    clipped = np.clip(strongest, 1, len(radii) - 2)
    before = gradient[np.arange(gradient.shape[0]), clipped - 1]
    middle = gradient[np.arange(gradient.shape[0]), clipped]
    after = gradient[np.arange(gradient.shape[0]), clipped + 1]
    curvature = before - 2 * middle + after
    shift = np.where(np.abs(curvature) > 1e-9,
                     .5 * (before - after) / np.where(curvature == 0, 1.0, curvature), 0.0)
    refined = radii[strongest] + np.clip(shift, -1, 1) * float(radii[1] - radii[0])
    points = np.c_[center[0] + refined * np.cos(bearings), center[1] + refined * np.sin(bearings)]
    fitted = _fit_circle(points[chosen], np.ones(int(chosen.sum())))
    if fitted is None or fitted["inliers"].sum() < 12:
        return None
    inside = fitted["inliers"]
    angles_found = bearings[chosen][inside]
    fitted.update({"angles": angles_found, "coverage": _coverage(angles_found),
                   "points": int(inside.sum()),
                   "strength": float(np.mean(strength[chosen][inside]))})
    return fitted


def _tick_ends(strokes, center):
    far, near = [], []
    for stroke, _, _, _ in strokes:
        first = float(hypot(*(stroke["start"] - center)))
        second = float(hypot(*(stroke["end"] - center)))
        far.append(stroke["start"] if first >= second else stroke["end"])
        near.append(stroke["end"] if first >= second else stroke["start"])
    return np.array(far), np.array(near)


def _end_rings(strokes, center, radius, weights):
    far, near = _tick_ends(strokes, center)
    rings = []
    bandwidth = max(.8, .008 * radius)
    for points, label in ((far, "outer"), (near, "inner")):
        distances = np.linalg.norm(points - center, axis=1)
        for peak, _ in _radius_peaks(distances, weights, bandwidth)[:3]:
            selected = np.abs(distances - peak) <= max(1.2, .015 * peak)
            if selected.sum() < MIN_STROKES:
                continue
            fitted = _fit_circle(points[selected], weights[selected])
            if fitted is None or fitted["inliers"].sum() < MIN_STROKES:
                continue
            inside = fitted["inliers"]
            found = points[selected][inside]
            angles = np.arctan2(found[:, 1] - fitted["center"][1], found[:, 0] - fitted["center"][0])
            fitted.update({"kind": label, "angles": angles, "coverage": _coverage(angles),
                           "points": int(inside.sum())})
            rings.append(fitted)
    return rings


def _scale_band(strokes, center, radius, weights, rings):
    """Known tick ink extends from the innermost to the outermost tick end.

    Circles confirmed from tick ends set the limits; the weighted spread of all
    ends is only a fallback, because one stray stroke must not widen the band.
    """
    far, near = _tick_ends(strokes, center)
    ends = np.vstack((far, near))
    distances = np.linalg.norm(ends - center, axis=1)
    both = np.concatenate([weights, weights])
    low = _weighted_percentile(distances, both, .04)
    high = _weighted_percentile(distances, both, .96)
    if low is None or high is None:
        return None
    limit = max(1.2, .008 * radius)
    confirmed = [ring for ring in rings
                 if ring["points"] >= 8 and ring["coverage"] >= .18
                 and ring["rms"] <= limit
                 and .45 * radius <= ring["radius"] <= 1.45 * radius]
    inner_rings = [ring["radius"] for ring in confirmed if ring["kind"] == "inner"]
    outer_rings = [ring["radius"] for ring in confirmed if ring["kind"] == "outer"]
    inner = min(inner_rings) if inner_rings else low
    outer = max(outer_rings) if outer_rings else high
    if outer - inner < max(2.0, .01 * radius):
        inner, outer = low, high
    if outer - inner < max(2.0, .01 * radius):
        return None
    inset = max(.5, BAND_INSET * radius)
    return {"inner": float(inner - inset), "outer": float(outer + inset),
            "tick_inner": float(inner), "tick_outer": float(outer)}


def _off_centre_circles(image, center, reference, outer, agreement):
    """Complete-looking circles that do not share the tick centre.

    They are only reported.  The proposal never moves onto one of them, however
    much of its arc is visible.
    """
    height, width = image.shape
    smallest = int(round(outer + 2))
    largest = int(round(min(2.2 * reference, min(height, width) / 2 - 2)))
    if largest - smallest < 6:
        return []
    circles = cv2.HoughCircles(np.clip(image, 0, 255).astype(np.uint8), cv2.HOUGH_GRADIENT,
                               dp=1.2, minDist=max(12.0, .08 * reference), param1=100,
                               param2=28, minRadius=smallest, maxRadius=largest)
    if circles is None:
        return []
    # A real ring refits to a tight circle; texture and text do not.
    limit = max(.8, .004 * reference)
    found = []
    for x, y, radius in circles[0][:14]:
        fitted = _fit_edge_circle(image, np.array([x, y], dtype=float), float(radius),
                                  band=3.0, angles=900)
        if fitted is None or fitted["rms"] > limit or fitted["coverage"] < .55:
            continue
        offset = float(hypot(*(fitted["center"] - center)))
        if offset <= max(agreement, .02 * reference):
            continue
        if any(hypot(*(fitted["center"] - other["center"])) <= 2.0
               and abs(fitted["radius"] - other["radius"]) <= 2.0 for other in found):
            continue
        found.append({"center": fitted["center"], "radius": fitted["radius"],
                      "offset": offset, "coverage": fitted["coverage"],
                      "rms": fitted["rms"]})
    found.sort(key=lambda circle: (-circle["coverage"], -circle["radius"]))
    return found[:2]


def suggest(luma, settings):
    """Propose a centre, reference radius and scan band; never move the marks."""
    hint = np.array(settings["center"], dtype=float)
    marks = [np.array(settings[name], dtype=float) for name in ("low_mark", "high_mark")]
    radius_hint = float(np.mean([hypot(*(mark - hint)) for mark in marks]))
    if radius_hint < 8:
        return {"status": "unavailable",
                "message": "The two scale marks are too close to the centre to suggest a reference.",
                "reasons": ["Move the low and high marks out onto the printed scale."]}
    crop, scale, origin = _prepare(luma, hint, radius_hint)
    local_hint = (hint - origin) * scale
    local_radius = radius_hint * scale
    if min(crop.shape) < 24:
        return {"status": "unavailable", "message": "The crop around the dial is too small.",
                "reasons": []}
    strokes = _segments(crop, minimum=max(3.0, .006 * local_radius))
    radial = _radial_strokes(strokes, local_hint, local_radius)
    if len(radial) < MIN_STROKES:
        return {"status": "unavailable",
                "message": "Too few radial tick strokes were visible to place a reference.",
                "reasons": ["A complete bezel is not used on its own; check that the two scale "
                            "marks sit on printed ticks."],
                "strokes": len(radial)}
    center, uncertainty, accepted = _line_center(radial, local_hint)
    if accepted < MIN_STROKES:
        return {"status": "unavailable",
                "message": "The visible tick strokes do not converge on one centre.",
                "reasons": ["Check that the marks are on the printed scale, then try again."],
                "strokes": accepted}
    weights = np.array([min(stroke["length"], 36.0) * alignment
                        for stroke, _, _, alignment in radial])
    rings = _end_rings(radial, center, local_radius, weights)
    line_center = center
    fused = _ring_centers(rings, line_center, agreement=max(1.5, .008 * local_radius),
                          floor=uncertainty or 0.0)
    if fused is not None:
        center = fused
        rings = _end_rings(radial, center, local_radius, weights)
    band = _scale_band(radial, center, local_radius, weights, rings)
    if band is None:
        return {"status": "unavailable",
                "message": "The tick ends do not agree on one usable scale band.",
                "reasons": ["The visible ticks may be split between inner and outer marks."],
                "strokes": accepted}
    agreement = max(1.5, .008 * local_radius)
    ignored = _off_centre_circles(crop, center, band["tick_outer"], band["outer"], agreement)
    return _compose(origin, scale, hint, local_radius, center, line_center,
                    uncertainty, accepted, radial, rings, band, ignored, marks)


def _compose(origin, scale, hint, local_radius, center, line_center,
             uncertainty, accepted, radial, rings, band, ignored, marks):
    def back(point):
        return np.array([origin[0] + point[0] / scale, origin[1] + point[1] / scale])

    center_original = back(center)
    inner, outer = band["inner"] / scale, band["outer"] / scale
    reference = (inner + outer) / 2
    agreement_px = max(1.5, .008 * local_radius) / scale
    shift = float(hypot(*(center_original - hint)))
    fused_px = float(hypot(*(center - line_center))) / scale
    uncertainty_px = None if uncertainty is None else float(uncertainty / scale)
    angles = np.array([atan2(*(stroke["middle"] - center)[::-1]) for stroke, _, _, _ in radial])
    coverage = _coverage(angles)
    ring_rows = []
    for ring in rings:
        ring_rows.append({"kind": ring["kind"], "radius_px": round(ring["radius"] / scale, 2),
                          "center": [round(float(value), 2) for value in back(ring["center"])],
                          "offset_px": round(float(hypot(*(ring["center"] - center))) / scale, 2),
                          "coverage": round(ring["coverage"], 3), "points": ring["points"],
                          "rms_px": round(ring["rms"] / scale, 3)})
    tightest = min((row["offset_px"] for row in ring_rows
                    if row["points"] >= MIN_STROKES and row["coverage"] >= .15), default=None)
    mark_rows = []
    for name, mark in zip(("low_mark", "high_mark"), marks):
        distance = float(hypot(*(mark - center_original)))
        mark_rows.append({"name": name, "distance_px": round(distance, 2),
                          "offset_from_reference_px": round(distance - reference, 2)})
    reasons = []
    score = 0
    if accepted >= 16:
        score += 2
    elif accepted >= 8:
        score += 1
    else:
        reasons.append("Only a few radial tick strokes were found.")
    if coverage >= .65:
        score += 2
    elif coverage >= .4:
        score += 1
    else:
        reasons.append("The visible tick arc is short; check the suggestion carefully.")
    if tightest is None:
        reasons.append("No tick-end circle could be confirmed against the stroke centre.")
    elif tightest <= agreement_px:
        score += 2
    elif tightest <= 2 * agreement_px:
        score += 1
    else:
        reasons.append("The tick-end circles disagree with the stroke centre.")
    if uncertainty_px is not None:
        if uncertainty_px <= max(1.5, .01 * local_radius / scale):
            score += 1
        elif uncertainty_px > max(3.0, .03 * local_radius / scale):
            reasons.append("The visible arc constrains the centre only loosely.")
    if shift > .35 * local_radius / scale:
        score -= 1
        reasons.append("The suggested centre is far from the current one; check the photo.")
    if ignored:
        score -= 1
        reasons.append("A more complete ring does not share the tick centre; it was ignored.")
    confidence = "high" if score >= 5 else "medium" if score >= 3 else "low"
    # A short visible arc never earns full confidence, whatever the other checks say.
    if confidence == "high" and coverage < .45:
        confidence = "medium"
    if confidence != "low" and (coverage < .2 or accepted < 10):
        confidence = "low"
    notes = [f"Centre from {accepted} radial tick strokes; {coverage * 100:.0f}% of the "
             f"scale circle is visible."]
    notes.append(f"Tick ends run {band['tick_inner'] / scale:.1f}–{band['tick_outer'] / scale:.1f} px; "
                 f"reference radius {reference:.1f} px.")
    if fused_px >= .05:
        notes.append(f"Tick-end circles shift the stroke centre by {fused_px:.1f} px.")
    if tightest is not None:
        notes.append(f"Tick-end circles agree with the centre to {tightest:.1f} px.")
    if uncertainty_px is not None:
        notes.append(f"Centre uncertainty ±{uncertainty_px:.1f} px.")
    if shift >= .5:
        notes.append(f"Moves the centre {shift:.1f} px from the current position.")
    else:
        notes.append("The current centre already agrees with the printed scale.")
    mark_spread = max(abs(row["offset_from_reference_px"]) for row in mark_rows)
    if mark_spread > max(4.0, .35 * (outer - inner)):
        notes.append("The clicked marks sit near a tick end; the reference follows the tick "
                     "ends, not the clicks.")
    for circle in ignored:
        notes.append(f"A complete circle of radius {circle['radius'] / scale:.1f} px is "
                     f"{circle['offset'] / scale:.1f} px off this centre and was not used.")
    band_in = float(np.clip(inner / reference, .08, 1.4))
    band_out = float(np.clip(outer / reference, band_in + .02, 1.6))
    proposal = {"center": [round(float(value), 2) for value in center_original],
                "ref_radius": round(float(reference), 2),
                "band_in": round(band_in, 4), "band_out": round(band_out, 4)}
    return {
        "status": "ok",
        "confidence": confidence,
        "message": reasons[0] if reasons else "Suggestion ready to review on the original photo.",
        "reasons": reasons,
        "notes": notes,
        "center": [round(float(value), 2) for value in center_original],
        "shift_px": round(shift, 2),
        "uncertainty_px": None if uncertainty_px is None else round(uncertainty_px, 2),
        "coverage": round(coverage, 3),
        "strokes": accepted,
        "band": {"inner_px": round(inner, 2), "outer_px": round(outer, 2),
                 "reference_px": round(reference, 2),
                 "tick_inner_px": round(band["tick_inner"] / scale, 2),
                 "tick_outer_px": round(band["tick_outer"] / scale, 2)},
        "marks": mark_rows,
        "rings": ring_rows,
        "ignored_rings": [{"center": [round(float(value), 2) for value in back(circle["center"])],
                           "radius_px": round(circle["radius"] / scale, 2),
                           "offset_px": round(circle["offset"] / scale, 2),
                           "coverage": round(circle["coverage"], 3)} for circle in ignored],
        "proposal": proposal,
        "preview": {
            "center": [round(float(value), 2) for value in center_original],
            "reference_px": round(reference, 2),
            "inner_px": round(inner, 2),
            "outer_px": round(outer, 2),
            "rays": [[[round(float(value), 2) for value in center_original],
                      [round(float(value), 2) for value in mark]] for mark in marks],
        },
    }
