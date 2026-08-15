from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from rondo_player.agent import is_active_period
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


if __name__ == "__main__":
    unittest.main()
