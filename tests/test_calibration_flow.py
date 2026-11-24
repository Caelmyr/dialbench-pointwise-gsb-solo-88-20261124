"""End-to-end checks for suggesting, reviewing and adopting a calibration.

The point of the feature is what adopting does *not* change, so these tests
submit calibration as it stands and assert the clicked marks and values survive.
"""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dialbench.__main__ import application  # noqa: E402

from tests.test_calibrate import dial  # noqa: E402

MARKS = {"low_mark": [298, 490], "high_mark": [630, 490]}


class CalibrationFlowTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="dialbench-test-"))
        self.client = application(self.root).test_client()
        self.panel = self.client.post("/dial/demo", json={}).get_json()
        self.manual = {**self.panel["settings"], "center": [469, 322], **MARKS,
                       "low_value": 3, "high_value": 87}

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def suggest(self, settings=None):
        response = self.client.post(f"/dial/panels/{self.panel['id']}/calibrate",
                                    json={"settings": settings or self.manual})
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        return response.get_json()

    def adopt(self, proposal, settings=None, revision=None):
        return self.client.post(
            f"/dial/panels/{self.panel['id']}/calibrate/adopt",
            json={"revision": revision if revision is not None else self.panel["revision"],
                  "settings": settings or self.manual,
                  "center": proposal["center"], "ref_radius": proposal["ref_radius"],
                  "band_in": proposal["band_in"], "band_out": proposal["band_out"]})

    def test_suggestion_is_available_for_the_sample(self):
        result = self.suggest()
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["proposal"])
        self.assertTrue(result["preview"]["rays"])

    def test_adopting_keeps_marks_readings_and_direction(self):
        result = self.suggest()
        response = self.adopt(result["proposal"])
        self.assertEqual(response.status_code, 200, response.get_data(as_text=True))
        settings = response.get_json()["settings"]
        self.assertEqual(settings["low_mark"], MARKS["low_mark"])
        self.assertEqual(settings["high_mark"], MARKS["high_mark"])
        self.assertEqual(settings["low_value"], 3)
        self.assertEqual(settings["high_value"], 87)
        self.assertTrue(settings["clockwise"])
        self.assertEqual(settings["center"], result["proposal"]["center"])
        self.assertEqual(settings["scale_radius"], result["proposal"]["ref_radius"])
        self.assertEqual(settings["band_in"], result["proposal"]["band_in"])
        self.assertEqual(settings["band_out"], result["proposal"]["band_out"])

    def test_scale_scan_uses_the_adopted_reference_radius(self):
        result = self.suggest()
        settings = self.adopt(result["proposal"]).get_json()["settings"]
        preview = self.client.post(f"/dial/panels/{self.panel['id']}/preview",
                                   json={"mode": "arc", "settings": settings}).get_json()
        self.assertAlmostEqual(preview["geometry"]["radius"], settings["scale_radius"], places=2)
        self.assertEqual(preview["rows"][0]["reading"], 3)
        self.assertEqual(preview["rows"][-1]["reading"], 87)

    def test_pointer_direction_follows_the_new_centre(self):
        result = self.suggest()
        settings = self.adopt(result["proposal"]).get_json()["settings"]
        pointer = {"pointer": [565, 179]}
        preview = self.client.post(f"/dial/panels/{self.panel['id']}/preview",
                                   json={"mode": "reading",
                                         "settings": {**settings, **pointer}}).get_json()
        row = preview["rows"][0]
        self.assertEqual(row["state"], "in_range")
        self.assertGreater(row["reading"], 3)
        self.assertLess(row["reading"], 87)

    def test_manual_calibration_survives_dismissing_a_suggestion(self):
        saved = self.client.put(f"/dial/panels/{self.panel['id']}/settings",
                                json={"revision": self.panel["revision"],
                                      "settings": self.manual}).get_json()
        self.suggest(saved["settings"])
        panel = self.client.get(f"/dial/panels/{self.panel['id']}").get_json()
        self.assertEqual(panel["settings"]["center"], self.manual["center"])
        self.assertEqual(panel["settings"]["low_mark"], self.manual["low_mark"])
        self.assertEqual(panel["revision"], saved["revision"])
        self.assertNotIn("scale_radius", panel["settings"])

    def test_adopting_a_stale_suggestion_is_rejected(self):
        result = self.suggest()
        self.adopt(result["proposal"])
        response = self.adopt(result["proposal"])
        self.assertEqual(response.status_code, 409)

    def test_bad_proposal_geometry_is_rejected(self):
        result = self.suggest()
        broken = dict(result["proposal"], band_in=1.2, band_out=.9)
        self.assertEqual(self.adopt(broken).status_code, 400)

    def test_switching_between_sources_keeps_the_marks(self):
        second = self.suggest({**self.manual, "source": "b"})
        self.assertEqual(second["status"], "ok")
        settings = self.adopt(second["proposal"], {**self.manual, "source": "b"}).get_json()["settings"]
        self.assertEqual(settings["source"], "b")
        self.assertEqual(settings["low_mark"], MARKS["low_mark"])

    def test_saved_snapshot_keeps_the_adopted_geometry(self):
        result = self.suggest()
        adopted = self.adopt(result["proposal"]).get_json()
        response = self.client.post(f"/dial/panels/{self.panel['id']}/reports",
                                    json={"revision": adopted["revision"], "mode": "arc",
                                          "settings": adopted["settings"]})
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        report = response.get_json()["report"]
        self.assertEqual(report["settings"]["low_mark"], MARKS["low_mark"])
        self.assertAlmostEqual(report["geometry"]["radius"], adopted["settings"]["scale_radius"],
                               places=2)
        overlay = self.client.get(
            f"/dial/panels/{self.panel['id']}/reports/{report['id']}/overlay.png")
        self.assertEqual(overlay.status_code, 200)
        self.assertEqual(overlay.mimetype, "image/png")

    def test_suggestion_does_not_write_to_the_panel(self):
        self.suggest()
        self.suggest()
        panel = self.client.get(f"/dial/panels/{self.panel['id']}").get_json()
        self.assertEqual(panel["revision"], self.panel["revision"])

    def test_clearing_the_reference_returns_to_the_clicked_marks(self):
        result = self.suggest()
        adopted = self.adopt(result["proposal"]).get_json()
        cleared = self.client.put(f"/dial/panels/{self.panel['id']}/settings",
                                  json={"revision": adopted["revision"],
                                        "settings": adopted["settings"]}).get_json()
        self.assertEqual(cleared["settings"]["scale_radius"], result["proposal"]["ref_radius"])
        cleared = self.client.put(f"/dial/panels/{self.panel['id']}/settings",
                                  json={"revision": cleared["revision"],
                                        "settings": {**adopted["settings"], "scale_radius": None}}
                                  ).get_json()
        self.assertNotIn("scale_radius", cleared["settings"])
        preview = self.client.post(f"/dial/panels/{self.panel['id']}/preview",
                                   json={"mode": "arc", "settings": cleared["settings"]}).get_json()
        marks = cleared["settings"]
        expected = (float(np.hypot(marks["low_mark"][0] - marks["center"][0],
                                   marks["low_mark"][1] - marks["center"][1])) +
                    float(np.hypot(marks["high_mark"][0] - marks["center"][0],
                                   marks["high_mark"][1] - marks["center"][1]))) / 2
        self.assertAlmostEqual(preview["geometry"]["radius"], expected, places=2)


class UploadSuggestionTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="dialbench-test-"))
        self.client = application(self.root).test_client()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_uploaded_dial_can_be_suggested(self):
        from io import BytesIO
        import numpy as np
        from PIL import Image

        painted = np.asarray(dial(eccentric=(12, -9)), dtype=np.uint8)
        buffer = BytesIO()
        Image.fromarray(painted, "RGB").save(buffer, format="PNG")
        buffer.seek(0)
        response = self.client.post("/dial/panels", data={"a": (buffer, "dial.png")},
                                    content_type="multipart/form-data")
        self.assertEqual(response.status_code, 201, response.get_data(as_text=True))
        panel = response.get_json()
        settings = {**panel["settings"], "center": [469, 322],
                    "low_mark": [309, 482], "high_mark": [629, 482]}
        result = self.client.post(f"/dial/panels/{panel['id']}/calibrate",
                                  json={"settings": settings}).get_json()
        self.assertEqual(result["status"], "ok")
        self.assertLess(float(np.hypot(*(np.array(result["center"]) - [460, 328]))), 2.5)
        self.assertTrue(result["ignored_rings"])


if __name__ == "__main__":
    unittest.main()
