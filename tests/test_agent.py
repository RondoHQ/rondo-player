from __future__ import annotations

import unittest
from datetime import datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from rondo_player.agent import is_active_period
from rondo_player.hardware import chromium_executable
from rondo_player.setup_screen import SetupScreen


class ScheduleTest(unittest.TestCase):
    def test_daytime_schedule(self) -> None:
        zone = ZoneInfo("Europe/Amsterdam")
        self.assertTrue(is_active_period(datetime(2026, 8, 15, 12, 0, tzinfo=zone), "08:00", "23:00"))
        self.assertFalse(is_active_period(datetime(2026, 8, 15, 23, 30, tzinfo=zone), "08:00", "23:00"))

    def test_overnight_schedule(self) -> None:
        zone = ZoneInfo("Europe/Amsterdam")
        self.assertTrue(is_active_period(datetime(2026, 8, 15, 23, 0, tzinfo=zone), "20:00", "02:00"))
        self.assertTrue(is_active_period(datetime(2026, 8, 16, 1, 0, tzinfo=zone), "20:00", "02:00"))
        self.assertFalse(is_active_period(datetime(2026, 8, 16, 12, 0, tzinfo=zone), "20:00", "02:00"))


class SetupScreenTest(unittest.TestCase):
    def test_escapes_remote_content(self) -> None:
        screen = SetupScreen()
        screen.update("<script>", "<b>unsafe</b>")
        rendered = screen.render()
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("&lt;b&gt;unsafe&lt;/b&gt;", rendered)


class BrowserTest(unittest.TestCase):
    @patch("rondo_player.hardware.os.access", return_value=True)
    @patch("rondo_player.hardware.Path.is_file", return_value=True)
    def test_prefers_direct_debian_binary(self, _is_file, _access) -> None:
        self.assertEqual("/usr/lib/chromium/chromium", chromium_executable())

    @patch("rondo_player.hardware.shutil.which")
    @patch("rondo_player.hardware.Path.is_file", return_value=False)
    def test_falls_back_to_path(self, _is_file, which) -> None:
        which.side_effect = lambda name: "/usr/bin/chromium" if name == "chromium" else None
        self.assertEqual("/usr/bin/chromium", chromium_executable())


if __name__ == "__main__":
    unittest.main()
