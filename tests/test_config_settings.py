"""Test campi impostazioni in AppConfig (no Tk)."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from remotedesk.config import AppConfig, normalize_default_quality, normalize_invite_minutes


class ConfigNormalizeTests(unittest.TestCase):
    def test_invite_minutes_invalid_becomes_two(self) -> None:
        self.assertEqual(normalize_invite_minutes(7), 2)
        self.assertEqual(normalize_invite_minutes("x"), 2)

    def test_default_quality_invalid_becomes_balanced(self) -> None:
        self.assertEqual(normalize_default_quality("ultra"), "balanced")
        self.assertEqual(normalize_default_quality("fluid"), "fluid")


class AppConfigLoadSaveTests(unittest.TestCase):
    def test_legacy_json_gets_defaults(self) -> None:
        legacy = {
            "desk_id": "123456789",
            "relay": "",
            "alias": "",
            "contacts": [],
            "recent": [],
            "whitelist": [],
            "host_secret": "abc",
            "trusted": {},
            "access_password_hash": "",
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(legacy), encoding="utf-8")
            with patch("remotedesk.config.config_path", return_value=path):
                cfg = AppConfig.load()
            self.assertTrue(cfg.show_banner)
            self.assertTrue(cfg.interactive_access)
            self.assertTrue(cfg.confirm_incoming_files)
            self.assertEqual(cfg.default_quality, "balanced")
            self.assertTrue(cfg.show_remote_cursor)
            self.assertFalse(cfg.transmit_audio)
            self.assertEqual(cfg.invite_minutes, 2)
            self.assertEqual(cfg.download_dir, "")
            self.assertEqual(cfg.record_dir, "")
            self.assertFalse(cfg.start_with_windows)

    def test_load_normalizes_quality_and_invite(self) -> None:
        raw = {
            "desk_id": "123456789",
            "host_secret": "abc",
            "default_quality": "ultra",
            "invite_minutes": 7,
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with patch("remotedesk.config.config_path", return_value=path):
                cfg = AppConfig.load()
            self.assertEqual(cfg.default_quality, "balanced")
            self.assertEqual(cfg.invite_minutes, 2)

    def test_save_load_preserves_new_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            with patch("remotedesk.config.config_path", return_value=path):
                cfg = AppConfig(
                    desk_id="987654321",
                    host_secret="secret",
                    start_with_windows=True,
                    download_dir=r"C:\Downloads\RD",
                )
                cfg.save()
                again = AppConfig.load()
            self.assertTrue(again.start_with_windows)
            self.assertEqual(again.download_dir, r"C:\Downloads\RD")


if __name__ == "__main__":
    unittest.main()
