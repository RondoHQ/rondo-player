from __future__ import annotations

import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch
from zoneinfo import ZoneInfo

from rondo_player.agent import Agent, is_active_period
from rondo_player.api import RondoApi
from rondo_player.hardware import Cec, chromium_executable, connected_cec_adapter
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


class CecTest(unittest.TestCase):
    @patch("rondo_player.hardware.subprocess.run")
    @patch("rondo_player.hardware.shutil.which", return_value="/usr/bin/cec-ctl")
    @patch("rondo_player.hardware.Path.glob")
    def test_selects_adapter_with_physical_address(self, glob, _which, run) -> None:
        glob.return_value = [Path("/dev/cec0"), Path("/dev/cec1")]
        run.side_effect = [
            Mock(stdout="Physical Address           : f.f.f.f", stderr=""),
            Mock(stdout="Physical Address           : 4.0.0.0", stderr=""),
        ]

        self.assertEqual("/dev/cec1", connected_cec_adapter())

    @patch("rondo_player.hardware.connected_cec_adapter", return_value="/dev/cec1")
    @patch("rondo_player.hardware.subprocess.run")
    @patch("rondo_player.hardware.shutil.which", return_value="/usr/bin/cec-client")
    def test_wake_uses_connected_adapter(self, _which, run, _adapter) -> None:
        run.return_value = Mock(returncode=0, stdout="", stderr="")

        Cec().wake()

        self.assertEqual(
            call(
                ["/usr/bin/cec-client", "-s", "-d", "1", "/dev/cec1"],
                input="on 0\nas\n",
                text=True,
                capture_output=True,
                timeout=12,
                check=False,
            ),
            run.call_args,
        )


class ApiTest(unittest.TestCase):
    @patch("rondo_player.api.time.time_ns", return_value=12345)
    def test_command_request_bypasses_http_caches(self, _time) -> None:
        api = RondoApi("https://example.test")
        api._request = Mock(return_value={"command": None})

        api.command("token")

        api._request.assert_called_once_with(
            "GET",
            "/devices/me/commands?rondo_cache_buster=12345",
            token="token",
        )


class CommandReplayTest(unittest.TestCase):
    @patch.object(Agent, "_device_id", return_value="test-device")
    def test_replayed_command_is_not_executed_twice(self, _device_id) -> None:
        with TemporaryDirectory() as directory:
            cec = Mock()
            agent = Agent(
                "https://example.test",
                Path(directory) / "state.json",
                browser=Mock(),
                cec=cec,
                setup_screen=Mock(),
            )
            agent.state["token"] = "token"
            agent.api = Mock()
            command = {"id": "command-1", "name": "wake_tv"}

            agent._execute_command(command)
            agent._execute_command(command)

            cec.wake.assert_called_once_with()
            agent.api.acknowledge.assert_called_once_with(
                "token", "command-1", "completed", ""
            )


if __name__ == "__main__":
    unittest.main()
