"""Draw a labeled synthetic reference panel with a known pointer direction."""

from math import cos, radians, sin

import cv2
import numpy as np
from PIL import Image


def endpoint(center, radius, bearing):
    radian = radians(bearing)
    return [int(round(center[0] + radius * cos(radian))), int(round(center[1] + radius * sin(radian)))]


def photo(glare=False):
    width, height = 920, 680
    center, radius = [460, 328], 252
    cy, cx = np.ogrid[:height, :width]
    field = np.hypot((cx - 440) * .95, cy - 294) / 550
    rng = np.random.default_rng(102)
    grain = rng.normal(0, 1.5, (height, width))
    panel = np.clip(62 - 28 * field + grain, 12, 90)
    image = np.stack([panel * .86, panel, panel * 1.02], axis=-1).astype(np.uint8)
    cv2.rectangle(image, (14, 14), (width - 15, height - 15), (77, 86, 87), 2, cv2.LINE_AA)
    for x, y in ((52, 55), (width - 52, 55), (52, height - 55), (width - 52, height - 55)):
        cv2.circle(image, (x, y), 13, (31, 40, 42), -1, cv2.LINE_AA)
        cv2.circle(image, (x, y), 10, (99, 109, 102), 2, cv2.LINE_AA)
        cv2.line(image, (x - 6, y + 3), (x + 6, y - 3), (133, 141, 130), 1, cv2.LINE_AA)
    cv2.circle(image, center, radius + 28, (13, 18, 19), -1, cv2.LINE_AA)
    cv2.circle(image, center, radius + 17, (93, 102, 94), -1, cv2.LINE_AA)
    cv2.circle(image, center, radius + 14, (24, 32, 32), -1, cv2.LINE_AA)
    disc = np.hypot(cx - center[0], cy - center[1]) <= radius
    shade = 231 - 11 * (cy / height) - 13 * field + grain * .65
    image[disc] = np.stack([shade * 1.035, shade * 1.015, shade * .94], axis=-1)[disc].astype(np.uint8)
    ink = (41, 48, 45)
    cv2.ellipse(image, center, (225, 225), 0, 135, 405, ink, 1, cv2.LINE_AA)
    for mark in range(51):
        bearing = 135 + mark * 5.4
        major = mark % 5 == 0
        inside = endpoint(center, 205 if major else 218, bearing)
        outside = endpoint(center, 234, bearing)
        cv2.line(image, inside, outside, ink, 3 if major else 1, cv2.LINE_AA)
        if major:
            caption = str(mark * 2)
            x, y = endpoint(center, 176, bearing)
            size, _ = cv2.getTextSize(caption, cv2.FONT_HERSHEY_DUPLEX, .57, 1)
            cv2.putText(image, caption, (x - size[0] // 2, y + size[1] // 2),
                        cv2.FONT_HERSHEY_DUPLEX, .57, ink, 1, cv2.LINE_AA)
    cv2.putText(image, "PRESSURE", (400, 410), cv2.FONT_HERSHEY_DUPLEX, .65, ink, 1, cv2.LINE_AA)
    cv2.putText(image, "kPa", (442, 440), cv2.FONT_HERSHEY_DUPLEX, .58, ink, 1, cv2.LINE_AA)
    cv2.putText(image, "DB / 100", (426, 529), cv2.FONT_HERSHEY_PLAIN, 1.0, ink, 1, cv2.LINE_AA)
    cv2.putText(image, "SYNTHETIC BENCH SAMPLE", (39, 629), cv2.FONT_HERSHEY_PLAIN,
                1.0, (154, 170, 153), 1, cv2.LINE_AA)
    bearing = 135 + 270 * .63
    ends = [endpoint(center, 200, bearing), endpoint(center, 8, bearing + 90),
            endpoint(center, 28, bearing + 180), endpoint(center, 8, bearing - 90)]
    cv2.fillConvexPoly(image, np.array(ends, dtype=np.int32), (138, 64, 40), cv2.LINE_AA)
    cv2.circle(image, center, 15, (64, 68, 57), -1, cv2.LINE_AA)
    cv2.circle(image, center, 9, (126, 130, 111), -1, cv2.LINE_AA)
    if glare:
        reflection = np.exp(-((cx - 595) / 85 + (cy - 213) / 100) ** 2 * 3
                            - ((cy - 213) / 102) ** 2 * 2)
        alpha = np.where(disc, reflection * .84, 0)[..., None]
        image = (image * (1 - alpha) + np.array([255, 252, 242]) * alpha).astype(np.uint8)
    return Image.fromarray(image, "RGB")


def create(library, model):
    primary, alternate = photo(), photo(glare=True)
    config = model.defaults(*primary.size)
    config.update(center=[460, 328], low_mark=endpoint([460, 328], 229, 135),
                  high_mark=endpoint([460, 328], 229, 45),
                  pointer=endpoint([460, 328], 182, 305.1))
    return library.create("Reference pressure dial", {"a": (primary, "bench-shaded.png"),
                          "b": (alternate, "bench-unshaded.png")}, config, synthetic=True)
