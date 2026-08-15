"""Small standard-library client for the Rondo player API."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass
class ApiError(Exception):
    """An HTTP or transport error returned by Rondo."""

    status: int
    message: str
    code: str = ""

    def __str__(self) -> str:
        return self.message


class RondoApi:
    """Typed calls used by the player agent."""

    def __init__(self, site_url: str, timeout: int = 15) -> None:
        self.base_url = site_url.rstrip("/") + "/wp-json/rondo/v1/narrowcasting"
        self.timeout = timeout
        self.ssl_context = ssl.create_default_context()

    def register(self, device_id: str) -> dict[str, Any]:
        return self._request("POST", "/devices/register", {"device_id": device_id})

    def claim(self, device_id: str, code: str) -> dict[str, Any]:
        return self._request("POST", "/devices/claim", {"device_id": device_id, "code": code})

    def config(self, token: str) -> dict[str, Any]:
        return self._request("GET", "/devices/me/config", token=token)

    def heartbeat(self, token: str, state: str, version: str, error: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/devices/me/heartbeat",
            {"state": state, "version": version, "error": error},
            token,
        )

    def command(self, token: str) -> dict[str, Any] | None:
        return self._request("GET", "/devices/me/commands", token=token).get("command")

    def acknowledge(self, token: str, command_id: str, status: str, error: str = "") -> dict[str, Any]:
        return self._request(
            "POST",
            "/devices/me/commands/ack",
            {"command_id": command_id, "status": status, "error": error},
            token,
        )

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        token: str = "",
    ) -> dict[str, Any]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Accept": "application/json", "User-Agent": "RondoPlayer/0.1"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["X-Rondo-Device-Token"] = token

        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout,
                context=self.ssl_context,
            ) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            try:
                payload = json.loads(error.read().decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                payload = {}
            raise ApiError(
                error.code,
                payload.get("message", f"Rondo antwoordde met HTTP {error.code}"),
                payload.get("code", ""),
            ) from error
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
            raise ApiError(0, f"Rondo is niet bereikbaar: {error}") from error
