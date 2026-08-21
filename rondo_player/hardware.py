"""Chromium kiosk and HDMI-CEC adapters."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

LOGGER = logging.getLogger(__name__)


def chromium_executable() -> str | None:
    """Find Chromium without Raspberry Pi OS' incompatible launcher flags."""
    direct_binary = Path("/usr/lib/chromium/chromium")
    if direct_binary.is_file() and os.access(direct_binary, os.X_OK):
        return str(direct_binary)
    return shutil.which("chromium") or shutil.which("chromium-browser")


class Browser:
    """Own one Chromium kiosk process."""

    def __init__(self) -> None:
        self.process: subprocess.Popen[bytes] | None = None
        self.url = ""

    @property
    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def show(self, url: str) -> None:
        if self.running and self.url == url:
            return
        self.stop()
        executable = chromium_executable()
        if not executable:
            raise RuntimeError("Chromium is niet geïnstalleerd")
        self.url = url
        self.process = subprocess.Popen(
            [
                executable,
                "--kiosk",
                "--no-first-run",
                "--noerrdialogs",
                "--disable-infobars",
                "--disable-session-crashed-bubble",
                f"--user-data-dir={Path.home() / '.config/rondo-player/chromium'}",
                "--password-store=basic",
                "--force-device-scale-factor=1",
                "--disable-pinch",
                "--use-angle=gles",
                "--autoplay-policy=no-user-gesture-required",
                url,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def show_display(self, display_url: str, token: str) -> None:
        self.show(f"{display_url}#{urlencode({'token': token})}")

    def reload(self) -> None:
        # Restarting Chromium is deterministic across X11 and Wayland and keeps
        # the player independent of browser-automation packages.
        url = self.url
        self.stop()
        self.show(url)

    def stop(self) -> None:
        if not self.process or self.process.poll() is not None:
            self.process = None
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=3)
        self.process = None


def connected_cec_adapter() -> str | None:
    """Return the CEC adapter whose HDMI connector has a physical address."""
    devices = sorted(Path("/dev").glob("cec[0-9]*"))
    if not devices:
        return None

    cec_ctl = shutil.which("cec-ctl")
    if cec_ctl:
        for device in devices:
            result = subprocess.run(
                [cec_ctl, "-d", str(device), "--show-topology"],
                text=True,
                capture_output=True,
                timeout=5,
                check=False,
            )
            output = f"{result.stdout}\n{result.stderr}"
            for line in output.splitlines():
                if "Physical Address" not in line:
                    continue
                address = line.rsplit(":", 1)[-1].strip().lower()
                if address and address != "f.f.f.f":
                    return str(device)

    # Older cec-utils versions may not expose connector topology. Preserve the
    # previous single-port behaviour as a safe fallback.
    return str(devices[0])


class Cec:
    """Bounded HDMI-CEC commands; no remote shell surface."""

    def __init__(self) -> None:
        self.executable = shutil.which("cec-client")

    @property
    def available(self) -> bool:
        return bool(self.executable)

    def wake(self) -> None:
        self._send("on 0\nas\n")

    def sleep(self) -> None:
        self._send("standby 0\n")

    def detect(self) -> None:
        self._send("scan\n", timeout=20)

    def _send(self, commands: str, timeout: int = 12) -> None:
        if not self.executable:
            raise RuntimeError("cec-client is niet geïnstalleerd")
        adapter = connected_cec_adapter()
        arguments = [self.executable, "-s", "-d", "1"]
        if adapter:
            arguments.append(adapter)
        result = subprocess.run(
            arguments,
            input=commands,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()[-300:]
            raise RuntimeError(f"HDMI-CEC mislukt: {detail or result.returncode}")
        LOGGER.info("HDMI-CEC command completed via %s", adapter or "default adapter")
