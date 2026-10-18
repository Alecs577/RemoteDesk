"""Test cattura, whitelist e monitor (no Tk)."""

from __future__ import annotations

import sys
import unittest

from remotedesk.capture import QUALITY_PRESETS, list_monitors, quality_capture_params
from remotedesk.grants import remote_allowed


class QualityPresetTests(unittest.TestCase):
    def test_fluid_preset(self) -> None:
        long_side, jpeg_q, interval = quality_capture_params("fluid")
        self.assertEqual((long_side, jpeg_q, interval), QUALITY_PRESETS["fluid"])
        self.assertEqual(long_side, 960)
        self.assertEqual(jpeg_q, 40)
        self.assertAlmostEqual(interval, 1.0 / 15.0)

    def test_balanced_preset(self) -> None:
        long_side, jpeg_q, interval = quality_capture_params("balanced")
        self.assertEqual(long_side, 1280)
        self.assertEqual(jpeg_q, 60)
        self.assertAlmostEqual(interval, 1.0 / 8.0)

    def test_sharp_preset(self) -> None:
        long_side, jpeg_q, interval = quality_capture_params("sharp")
        self.assertEqual(long_side, 1920)
        self.assertEqual(jpeg_q, 80)
        self.assertAlmostEqual(interval, 1.0 / 6.0)

    def test_unknown_quality_defaults_balanced(self) -> None:
        self.assertEqual(quality_capture_params("unknown"), QUALITY_PRESETS["balanced"])


class RemoteAllowedTests(unittest.TestCase):
    def test_empty_whitelist_allows(self) -> None:
        self.assertTrue(remote_allowed("123456789", [], False))

    def test_whitelist_allows_only_listed(self) -> None:
        wl = ["123456789"]
        self.assertTrue(remote_allowed("123456789", wl, False))
        self.assertFalse(remote_allowed("987654321", wl, False))

    def test_invite_ok_allows_off_list(self) -> None:
        wl = ["123456789"]
        self.assertTrue(remote_allowed("987654321", wl, True))


class ListMonitorsTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "EnumDisplayMonitors solo su Windows")
    def test_list_monitors_non_empty(self) -> None:
        rects = list_monitors()
        self.assertTrue(rects)
        left, top, width, height = rects[0]
        self.assertGreater(width, 0)
        self.assertGreater(height, 0)
        self.assertIsInstance(left, int)
        self.assertIsInstance(top, int)


if __name__ == "__main__":
    unittest.main()
