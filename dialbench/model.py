"""Validate dial marks in original-image coordinates and their units."""

from math import atan2, degrees, hypot, isfinite

from image_lab.validation import Invalid

MAX_PIXELS = 4_000_000
MAX_UPLOAD = 14 * 1024 * 1024

MODES = {
    "reading": "Pointer reading",
    "radial": "Radial profile",
    "arc": "Scale band scan",
    "lighting": "Exposure check",
    "contrast": "Face and surround",
}
TOOLS = {
    "pointer": "Pointer",
    "center": "Dial center",
    "low_mark": "Low tick",
    "high_mark": "High tick",
    "probe": "Pixel probe",
}


def document(value):
    if not isinstance(value, dict):
        raise Invalid("Send an object with the inspection settings.")
    return value


def text(value, default="", maximum=120):
    if value is None:
        return default
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise Invalid(f"Text must be at most {maximum} characters.")
    return value.strip() or default


def number(value, name, low, high):
    if isinstance(value, bool):
        raise Invalid(f"{name} must be a number.")
    try:
        value = float(value)
    except (TypeError, ValueError):
        raise Invalid(f"{name} must be a number.") from None
    if not isfinite(value) or not low <= value <= high:
        raise Invalid(f"{name} must be between {low:g} and {high:g}.")
    return value


def point(value, record):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise Invalid("Choose a point in the photo.")
    x = number(value[0], "Horizontal coordinate", 0, record["width"] - 1)
    y = number(value[1], "Vertical coordinate", 0, record["height"] - 1)
    return [round(x, 2), round(y, 2)]


def angle(origin, target):
    return degrees(atan2(target[1] - origin[1], target[0] - origin[0])) % 360


def distance(origin, target):
    return hypot(target[0] - origin[0], target[1] - origin[1])


def defaults(width, height):
    x = (width - 1) / 2
    y = (height - 1) / 2
    radius = min(width, height) * .36
    bounds = {"width": width, "height": height}
    return {
        "source": "a", "center": point([x, y], bounds),
        "low_mark": point([x - .7 * radius, y + .7 * radius], bounds),
        "high_mark": point([x + .7 * radius, y + .7 * radius], bounds),
        "pointer": None,
        "low_value": 0, "high_value": 100, "unit": "kPa", "clockwise": True,
        "band_in": .79, "band_out": .97, "dark_cut": 25, "bright_cut": 235,
    }


def settings(value, record):
    override = document(value) if value is not None else {}
    selected = {**defaults(record["width"], record["height"]), **record["settings"], **override}
    role = selected["source"]
    if not isinstance(role, str) or role not in record["sources"]:
        raise Invalid("Choose an available photo.")
    coords = {name: point(selected[name], record) for name in ("center", "low_mark", "high_mark")}
    direction = selected["clockwise"]
    if not isinstance(direction, bool):
        raise Invalid("Choose a scale direction.")
    low_piece, high_piece = (distance(coords["center"], coords[name]) for name in ("low_mark", "high_mark"))
    if min(low_piece, high_piece) < 3:
        raise Invalid("The scale marks must be away from the dial center.")
    start, end = (angle(coords["center"], coords[name]) for name in ("low_mark", "high_mark"))
    sweep = ((end - start) if direction else (start - end)) % 360
    if not 8 <= sweep <= 352:
        raise Invalid("The scale marks are too close together.")
    low = number(selected["low_value"], "Low reading", -1e9, 1e9)
    high = number(selected["high_value"], "High reading", -1e9, 1e9)
    if low >= high:
        raise Invalid("The high reading must exceed the low reading.")
    band_in = number(selected["band_in"], "Inner scan radius", .08, 1.4)
    band_out = number(selected["band_out"], "Outer scan radius", .1, 1.6)
    dark = number(selected["dark_cut"], "Shadow threshold", 0, 254)
    bright = number(selected["bright_cut"], "Highlight threshold", 1, 255)
    if band_out - band_in < .02 or dark >= bright:
        raise Invalid("The upper scan radius and highlight threshold must be higher.")
    pointer = selected["pointer"]
    return {
        **coords, "source": role, "pointer": point(pointer, record) if pointer is not None else None,
        "clockwise": direction, "low_value": low, "high_value": high,
        "unit": text(selected["unit"], "units", 20), "band_in": band_in,
        "band_out": band_out, "dark_cut": dark, "bright_cut": bright,
    }


def mode(value):
    if not isinstance(value, str) or value not in MODES:
        raise Invalid("Choose an inspection mode.")
    return value
