"""Long-running Rondo Player agent."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from rondo_player import __version__
from rondo_player.api import ApiError, RondoApi
from rondo_player.hardware import Browser, Cec
from rondo_player.setup_screen import SetupScreen
from rondo_player.updater import launch_update, mark_healthy, should_retry, target_version

LOGGER = logging.getLogger(__name__)


def is_active_period(now: datetime, wake_time: str, sleep_time: str) -> bool:
    """Return whether now lies inside a possibly overnight active period."""
    current = now.strftime("%H:%M")
    if wake_time == sleep_time:
        return True
    if wake_time < sleep_time:
        return wake_time <= current < sleep_time
    return current >= wake_time or current < sleep_time


class Agent:
    """Pair, supervise and control one physical player."""

    def __init__(
        self,
        site_url: str,
        state_path: Path,
        browser: Browser | None = None,
        cec: Cec | None = None,
        setup_screen: SetupScreen | None = None,
    ) -> None:
        self.api = RondoApi(site_url)
        self.state_path = state_path
        self.state = self._load_state()
        self.device_id = self.state.get("device_id") or self._device_id()
        self.state["device_id"] = self.device_id
        self.browser = browser or Browser()
        self.cec = cec or Cec()
        self.setup_screen = setup_screen or SetupScreen()
        self.config: dict[str, Any] = self.state.get("config") or {}
        self.last_error = str(self.state.get("update_error", ""))[:300]
        self._last_config_at = 0.0
        self._last_heartbeat_at = 0.0
        self._last_command_at = 0.0
        self._schedule_active: bool | None = None
        self._save_state()

    def run(self) -> None:
        self.setup_screen.start()
        token = self.state.get("token", "")
        if token and self.config.get("display_url"):
            self.browser.show_display(self.config["display_url"], token)
        else:
            self.browser.show(self.setup_screen.url)

        while True:
            try:
                if not self.state.get("token"):
                    self._pair()
                else:
                    self._tick()
                    mark_healthy(__version__)
            except KeyboardInterrupt:
                self.browser.stop()
                return
            except Exception as error:  # Keep the kiosk alive after any isolated failure.
                self.last_error = str(error)[:300]
                LOGGER.exception("Player cycle failed")
            time.sleep(5)

    def _pair(self) -> None:
        registration = self.state.get("registration") or {}
        expires_at = self._parse_server_time(registration.get("expires_at", ""))
        if not registration.get("code") or expires_at <= time.time() + 5:
            registration = self.api.register(self.device_id)
            self.state["registration"] = registration
            self._save_state()

        code = registration["code"]
        self.setup_screen.update(code, "Vul deze code in bij Club TV in Rondo.")
        self.browser.show(self.setup_screen.url)

        try:
            claim = self.api.claim(self.device_id, code)
        except ApiError as error:
            if error.status == 409:
                return
            if error.status in (404, 410):
                self.state.pop("registration", None)
                self._save_state()
                return
            raise

        self.state["token"] = claim["token"]
        self.state["config"] = claim["display"]
        self.state.pop("registration", None)
        self.config = claim["display"]
        self._save_state()
        self.browser.show_display(self.config["display_url"], self.state["token"])
        self._apply_schedule(force=True)

    def _tick(self) -> None:
        now = time.monotonic()
        token = self.state["token"]
        if not self.browser.running and self.config.get("display_url"):
            self.browser.show_display(self.config["display_url"], token)
        try:
            if now - self._last_config_at >= 60:
                self.config = self.api.config(token)
                self.state["config"] = self.config
                self._save_state()
                self._last_config_at = now
                self._maybe_update()
            if now - self._last_heartbeat_at >= 60:
                self.api.heartbeat(token, "playing", __version__, self.last_error)
                self._last_heartbeat_at = now
                if self.state.pop("update_error", None) is not None:
                    self._save_state()
                self.last_error = ""
            if now - self._last_command_at >= 15:
                command = self.api.command(token)
                self._last_command_at = now
                if command:
                    self._execute_command(command)
        except ApiError as error:
            if error.status == 401:
                self._forget_credentials()
                return
            self.last_error = str(error)[:300]
            LOGGER.warning("Rondo API unavailable: %s", error)

        self._apply_schedule()

    def _maybe_update(self) -> None:
        """Launch one approved release update outside the player service."""
        target = target_version(self.config.get("update"), __version__)
        if not target or not should_retry(self.state.get("update_attempt"), target):
            return
        self.state["update_attempt"] = {
            "target_version": target,
            "attempted_at": int(time.time()),
        }
        self._save_state()
        launch_update(target, self.state_path)

    def _execute_command(self, command: dict[str, Any]) -> None:
        command_id = str(command.get("id") or "")
        if command_id and command_id == self.state.get("last_command_id"):
            LOGGER.warning("Ignoring replayed command %s", command_id)
            return
        if command_id:
            # Persist before executing. A reboot command can terminate the
            # process before its acknowledgement reaches Rondo.
            self.state["last_command_id"] = command_id
            self._save_state()

        error = ""
        try:
            actions = {
                "reload": self.browser.reload,
                "restart_browser": self.browser.reload,
                "wake_tv": self.cec.wake,
                "sleep_tv": self.cec.sleep,
                "cec_detect": self.cec.detect,
                "reboot": self._reboot,
                "shutdown": self._shutdown,
            }
            action = actions.get(command.get("name"))
            if not action:
                raise RuntimeError("Onbekend playercommando")
            action()
            status = "completed"
        except Exception as caught:
            status = "failed"
            error = str(caught)[:300]
            self.last_error = error
            LOGGER.exception("Command %s failed", command.get("name"))
        self.api.acknowledge(self.state["token"], command["id"], status, error)

    def _apply_schedule(self, force: bool = False) -> None:
        if not self.config or not self.config.get("cec_enabled", True) or not self.cec.available:
            return
        try:
            zone = ZoneInfo(self.config.get("timezone") or "Europe/Amsterdam")
        except ZoneInfoNotFoundError:
            zone = ZoneInfo("Europe/Amsterdam")
        active = is_active_period(
            datetime.now(zone),
            self.config.get("wake_time", "08:00"),
            self.config.get("sleep_time", "23:00"),
        )
        if not force and active == self._schedule_active:
            return
        if active:
            self.cec.wake()
        else:
            self.cec.sleep()
        self._schedule_active = active

    def _forget_credentials(self) -> None:
        self.state.pop("token", None)
        self.state.pop("registration", None)
        self._save_state()
        self.setup_screen.update("Opnieuw koppelen…", "De toegang is ingetrokken in Rondo.")
        self.browser.show(self.setup_screen.url)

    @staticmethod
    def _reboot() -> None:
        subprocess.run(["sudo", "/usr/bin/systemctl", "reboot"], check=True, timeout=10)

    @staticmethod
    def _shutdown() -> None:
        subprocess.run(["/usr/bin/systemctl", "poweroff"], check=True, timeout=10)

    @staticmethod
    def _parse_server_time(value: str) -> float:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _device_id() -> str:
        machine_id = Path("/etc/machine-id").read_text(encoding="utf-8").strip()
        digest = hashlib.sha256(machine_id.encode("utf-8")).hexdigest()[:20]
        return f"rondo-pi-{digest}"

    def _load_state(self) -> dict[str, Any]:
        try:
            return json.loads(self.state_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        os.chmod(temporary, 0o600)
        temporary.replace(self.state_path)
