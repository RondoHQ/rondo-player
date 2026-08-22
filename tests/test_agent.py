from __future__ import annotations

import unittest
import hashlib
import io
import json
import tarfile
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, call, patch
from zoneinfo import ZoneInfo

from rondo_player.agent import Agent, is_active_period
from rondo_player.api import RondoApi
from rondo_player.hardware import Cec, chromium_executable, connected_cec_adapter
from rondo_player.setup_screen import SetupScreen
from rondo_player.updater import (
    UpdateError,
    _extract_archive,
    apply_update,
    should_retry,
    target_version,
    verify_health,
)


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

    @patch("rondo_player.agent.subprocess.run")
    def test_shutdown_uses_bounded_systemctl_command(self, run) -> None:
        Agent._shutdown()

        run.assert_called_once_with(
            ["/usr/bin/systemctl", "poweroff"], check=True, timeout=10
        )

    @patch.object(Agent, "_device_id", return_value="test-device")
    def test_shutdown_command_is_executed_and_acknowledged(self, _device_id) -> None:
        with TemporaryDirectory() as directory:
            agent = Agent(
                "https://example.test",
                Path(directory) / "state.json",
                browser=Mock(),
                cec=Mock(),
                setup_screen=Mock(),
            )
            agent.state["token"] = "token"
            agent.api = Mock()
            agent._shutdown = Mock()

            agent._execute_command({"id": "command-2", "name": "shutdown"})

            agent._shutdown.assert_called_once_with()
            agent.api.acknowledge.assert_called_once_with(
                "token", "command-2", "completed", ""
            )


class UpdaterTest(unittest.TestCase):
    def test_only_approved_newer_versions_are_selected(self) -> None:
        self.assertEqual(
            "0.3.0",
            target_version({"channel": "stable", "target_version": "0.3.0"}, "0.2.1"),
        )
        self.assertIsNone(target_version({"channel": "off", "target_version": "9.0.0"}, "0.2.1"))
        self.assertIsNone(target_version({"channel": "stable", "target_version": "0.2.1"}, "0.2.1"))
        with self.assertRaises(UpdateError):
            target_version({"channel": "stable", "target_version": "latest"}, "0.2.1")

    def test_failed_release_is_throttled_for_six_hours(self) -> None:
        attempt = {"target_version": "0.3.0", "attempted_at": 1_000}
        self.assertFalse(should_retry(attempt, "0.3.0", now=1_001))
        self.assertTrue(should_retry(attempt, "0.3.0", now=1_000 + 6 * 60 * 60))
        self.assertTrue(should_retry(attempt, "0.3.1", now=1_001))

    def test_archive_rejects_path_traversal(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            archive = root / "release.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                payload = b"unsafe"
                info = tarfile.TarInfo("../escape")
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
            with self.assertRaises(UpdateError):
                _extract_archive(archive, root / "output")

    @patch("rondo_player.updater._restart_player")
    @patch("rondo_player.updater._schedule_guard")
    @patch("rondo_player.updater._verify_signature")
    @patch("rondo_player.updater._download")
    def test_signed_release_is_activated_atomically(self, download, _verify, guard, restart) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old_release = root / "releases/0.2.1"
            old_release.mkdir(parents=True)
            (root / "current").symlink_to(old_release)
            archive = self._release_archive("0.3.0")
            manifest = json.dumps(
                {
                    "version": "0.3.0",
                    "artifact": "rondo-player-0.3.0.tar.gz",
                    "sha256": hashlib.sha256(archive).hexdigest(),
                }
            ).encode()
            download.side_effect = [manifest, b"signature", archive]

            apply_update("0.3.0", root, root / "state.json")

            self.assertEqual((root / "releases/0.3.0").resolve(), (root / "current").resolve())
            self.assertEqual(old_release.resolve(), (root / "previous").resolve())
            guard.assert_called_once()
            restart.assert_called_once_with()

    @patch("rondo_player.updater._restart_player")
    def test_unhealthy_release_rolls_back(self, restart) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            old_release = root / "releases/0.2.1"
            new_release = root / "releases/0.3.0"
            old_release.mkdir(parents=True)
            new_release.mkdir(parents=True)
            (root / "current").symlink_to(new_release)
            (root / "update-status.json").write_text(
                json.dumps(
                    {
                        "status": "pending",
                        "target_version": "0.3.0",
                        "previous_release": str(old_release),
                    }
                ),
                encoding="utf-8",
            )

            verify_health("0.3.0", root, root / "state.json")

            self.assertEqual(old_release.resolve(), (root / "current").resolve())
            self.assertEqual("rolled_back", json.loads((root / "update-status.json").read_text())["status"])
            restart.assert_called_once_with()

    @staticmethod
    def _release_archive(version: str) -> bytes:
        output = io.BytesIO()
        files = {
            "rondo_player/__init__.py": f'__version__ = "{version}"\n'.encode(),
            "rondo_player/__main__.py": b"pass\n",
            "rondo_player/updater.py": b"pass\n",
            "rondo_player/release-public.pem": b"public key\n",
        }
        with tarfile.open(fileobj=output, mode="w:gz") as bundle:
            for name, payload in files.items():
                info = tarfile.TarInfo(name)
                info.size = len(payload)
                bundle.addfile(info, io.BytesIO(payload))
        return output.getvalue()


if __name__ == "__main__":
    unittest.main()
