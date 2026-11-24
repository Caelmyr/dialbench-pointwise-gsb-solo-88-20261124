"""Checks for the suggested dial centre and scale reference circle.

The fixtures are synthetic straight-on dials with a known centre, so an error
in the suggestion is measurable rather than judged by eye.
"""

import sys
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dialbench import calibrate, demo  # noqa: E402

TRUE_CENTER = np.array([460.0, 328.0])
SEED = (469, 322)


def luma(image):
    colors = np.asarray(image, dtype=np.float32)
    if colors.ndim == 3:
        return colors @ np.array([.2126, .7152, .0722], np.float32)
    return colors


def dial(center=(460, 328), face_radius=250, size=(920, 680), dark_ticks=True,
         tick_in=.84, tick_out=.97, major_every=5, count=51, step=5.4, start=135,
         thick=1, eccentric=(0, 0), occlude=None, glare=None, noise=2.0, blur=0.0,
         tick_dir="outward", inner_ring=True, seed=7):
    """Draw a straight-on dial: a face, graduated ticks and an optional bezel."""
    from PIL import Image, ImageDraw

    rng = np.random.default_rng(seed)
    width, height = size
    yy, xx = np.mgrid[0:height, 0:width]
    panel = np.clip(42.0 + rng.normal(0, noise, (height, width)), 0, 255).astype(np.uint8)
    image = Image.fromarray(panel, "L").convert("RGB")
    draw = ImageDraw.Draw(image)
    cx, cy = center
    ink = (40, 40, 40) if dark_ticks else (225, 225, 225)
    face = 216 if dark_ticks else 46
    draw.ellipse((cx - face_radius * 1.12, cy - face_radius * 1.12,
                  cx + face_radius * 1.12, cy + face_radius * 1.12), fill=(18, 18, 18))
    draw.ellipse((cx - face_radius * 1.08, cy - face_radius * 1.08,
                  cx + face_radius * 1.08, cy + face_radius * 1.08), fill=(95, 95, 95))
    draw.ellipse((cx - face_radius * 1.055, cy - face_radius * 1.055,
                  cx + face_radius * 1.055, cy + face_radius * 1.055), fill=(24, 24, 24))
    draw.ellipse((cx - face_radius, cy - face_radius, cx + face_radius, cy + face_radius),
                 fill=(face, face, face))
    ri, ro = tick_in * face_radius, tick_out * face_radius
    if inner_ring:
        draw.ellipse((cx - ri, cy - ri, cx + ri, cy + ri), outline=ink, width=1)
    for index in range(count):
        bearing = np.deg2rad(start + index * step)
        major = index % major_every == 0
        if tick_dir == "inward":
            inner = ri if major else ri + (ro - ri) * .38
        else:
            inner = ri
        outer = ro if major else ri + (ro - ri) * .62
        draw.line((cx + inner * np.cos(bearing), cy + inner * np.sin(bearing),
                   cx + outer * np.cos(bearing), cy + outer * np.sin(bearing)),
                  fill=ink, width=thick if major else 1)
    if eccentric != (0, 0):
        ox, oy = cx + eccentric[0], cy + eccentric[1]
        draw.ellipse((ox - face_radius * 1.19, oy - face_radius * 1.19,
                      ox + face_radius * 1.19, oy + face_radius * 1.19),
                     outline=(18, 18, 18), width=int(face_radius * .11))
        draw.ellipse((ox - face_radius * 1.13, oy - face_radius * 1.13,
                      ox + face_radius * 1.13, oy + face_radius * 1.13),
                     outline=(95, 95, 95), width=int(face_radius * .11))
        draw.ellipse((ox - face_radius * 1.075, oy - face_radius * 1.075,
                      ox + face_radius * 1.075, oy + face_radius * 1.075),
                     outline=(70, 70, 70), width=int(face_radius * .10))
    if occlude is not None:
        start_angle, end_angle = occlude
        draw.pieslice((cx - face_radius * 1.3, cy - face_radius * 1.3,
                       cx + face_radius * 1.3, cy + face_radius * 1.3),
                      start_angle, end_angle, fill=(28, 28, 28))
    if glare is not None:
        gx, gy, radius = glare
        overlay = Image.new("RGB", image.size, (252, 252, 252))
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).ellipse((gx - radius, gy - radius, gx + radius, gy + radius),
                                     fill=120)
        image = Image.composite(overlay, image, mask)
    if blur > 0:
        from PIL import ImageFilter
        image = image.filter(ImageFilter.GaussianBlur(blur))
    return image


def settings(center=SEED, marks=None, values=(0, 100)):
    marks = marks or ((center[0] - 160, center[1] + 160), (center[0] + 160, center[1] + 160))
    return {"center": list(center), "low_mark": list(marks[0]), "high_mark": list(marks[1]),
            "low_value": values[0], "high_value": values[1], "unit": "kPa", "clockwise": True}


class CenterSuggestionTests(unittest.TestCase):
    def assertCenterClose(self, image, config, tolerance, label=""):
        result = calibrate.suggest(luma(image), config)
        self.assertEqual(result["status"], "ok", result.get("message"))
        offset = float(np.hypot(*(np.array(result["center"]) - TRUE_CENTER)))
        self.assertLess(offset, tolerance, f"{label} centre off by {offset:.2f} px: {result}")
        return result

    def test_seed_bias_is_removed(self):
        result = self.assertCenterClose(dial(), settings(), 1.5, "plain")
        self.assertLess(np.hypot(*(np.array(result["center"]) - np.array(SEED))), 15)

    def test_other_seeds_converge_to_same_centre(self):
        for seed in ((430, 360), (490, 300), (410, 290), (510, 370)):
            self.assertCenterClose(dial(seed=1), settings(seed), 1.5, f"seed {seed}")

    def test_inward_ticks(self):
        self.assertCenterClose(dial(tick_dir="inward"), settings(), 1.5, "inward")

    def test_light_face_dark_ticks_inverted(self):
        self.assertCenterClose(dial(dark_ticks=False), settings(), 1.5, "light face")

    def test_thick_ticks(self):
        self.assertCenterClose(dial(thick=4), settings(), 2.5, "thick")

    def test_glare(self):
        self.assertCenterClose(dial(glare=(600, 200, 80)), settings(), 2.0, "glare")

    def test_blur(self):
        self.assertCenterClose(dial(blur=1.6), settings(), 2.5, "blur")


class PartialScaleTests(unittest.TestCase):
    def test_occluded_arcs_still_place_the_centre(self):
        for span in ((200, 290), (60, 200), (300, 30), (150, 400), (0, 120)):
            result = calibrate.suggest(luma(dial(occlude=span)), settings())
            self.assertEqual(result["status"], "ok", result.get("message"))
            offset = float(np.hypot(*(np.array(result["center"]) - TRUE_CENTER)))
            self.assertLess(offset, 3.0, f"occlusion {span}: {result['center']}")
            self.assertLess(result["coverage"], .9, "a partly hidden arc must be reported as such")

    def test_heavy_occlusion_reports_low_coverage(self):
        result = calibrate.suggest(luma(dial(occlude=(150, 400))), settings())
        self.assertEqual(result["status"], "ok")
        self.assertLess(result["coverage"], .4)
        self.assertTrue(any("arc is short" in reason for reason in result["reasons"]))


class OffCenterRingTests(unittest.TestCase):
    def test_complete_bezel_never_moves_the_centre(self):
        result = calibrate.suggest(luma(dial(eccentric=(30, 20))), settings())
        self.assertEqual(result["status"], "ok")
        offset = float(np.hypot(*(np.array(result["center"]) - TRUE_CENTER)))
        self.assertLess(offset, 2.0)
        self.assertTrue(result["ignored_rings"], "the off-centre bezel should be reported")

    def test_off_center_bezel_is_reported_with_its_offset(self):
        result = calibrate.suggest(luma(dial(eccentric=(8, -5))), settings())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["ignored_rings"])
        worst = max(result["ignored_rings"], key=lambda ring: ring["coverage"])
        self.assertGreater(worst["coverage"], .55)
        self.assertGreater(worst["offset_px"], 4.0)


class MarkEndsTests(unittest.TestCase):
    def test_marks_on_tick_ends_do_not_stretch_the_band(self):
        image = dial()
        # Click each mark at the outer end of a thick tick: the band must follow the
        # tick ends, not those clicks.
        ends = ((309, 482), (629, 482))
        result = calibrate.suggest(luma(image), settings(marks=ends))
        band = result["band"]
        self.assertLess(abs(band["tick_outer_px"] - 242), 4, band)
        self.assertLess(abs(band["tick_inner_px"] - 212), 4, band)

    def test_reference_radius_sits_between_the_tick_ends(self):
        result = calibrate.suggest(luma(dial()), settings())
        band = result["band"]
        self.assertLess(band["inner_px"], band["reference_px"])
        self.assertLess(band["reference_px"], band["outer_px"])
        proposal = result["proposal"]
        self.assertAlmostEqual(proposal["ref_radius"], band["reference_px"], places=2)


class SuggestionContractTests(unittest.TestCase):
    def test_unavailable_without_scale_marks(self):
        config = settings(marks=((469, 322), (470, 323)))
        result = calibrate.suggest(luma(dial()), config)
        self.assertEqual(result["status"], "unavailable")
        self.assertIn("too close", result["message"])

    def test_unavailable_without_ticks(self):
        blank = np.full((400, 400), 120, np.float32)
        result = calibrate.suggest(blank, {"center": [200, 200], "low_mark": [320, 320],
                                           "high_mark": [80, 320], "low_value": 0,
                                           "high_value": 100, "unit": "kPa",
                                           "clockwise": True})
        self.assertEqual(result["status"], "unavailable")

    def test_proposal_carries_only_geometry(self):
        result = calibrate.suggest(luma(dial()), settings(values=(5, 55)))
        proposal = result["proposal"]
        self.assertEqual(set(proposal), {"center", "ref_radius", "band_in", "band_out"})
        self.assertGreater(proposal["band_in"], 0)
        self.assertLess(proposal["band_in"], proposal["band_out"])

    def test_preview_covers_both_clicked_marks(self):
        config = settings()
        result = calibrate.suggest(luma(dial()), config)
        preview = result["preview"]
        self.assertEqual(len(preview["rays"]), 2)
        self.assertEqual(preview["rays"][0][1], config["low_mark"])
        self.assertEqual(preview["rays"][1][1], config["high_mark"])

    def test_demo_sample_is_supported(self):
        result = calibrate.suggest(luma(demo.photo()), settings(marks=((298, 490), (630, 490))))
        self.assertEqual(result["status"], "ok")
        self.assertIn(result["confidence"], {"high", "medium"})


if __name__ == "__main__":
    unittest.main()
