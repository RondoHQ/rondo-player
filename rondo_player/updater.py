"""Signed, atomic Rondo Player updates with automatic rollback."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import ssl
import subprocess
import tarfile
import tempfile
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

RELEASE_BASE_URL = "https://github.com/RondoHQ/rondo-player/releases/download"
VERSION_PATTERN = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
MAX_MANIFEST_BYTES = 16 * 1024
MAX_SIGNATURE_BYTES = 1024
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_EXTRACTED_BYTES = 20 * 1024 * 1024
RETRY_SECONDS = 6 * 60 * 60
HEALTH_TIMEOUT_SECONDS = 120


class UpdateError(RuntimeError):
    """A release could not be safely installed."""


def parse_version(value: str) -> tuple[int, int, int]:
    """Parse a strict three-part semantic version."""
    match = VERSION_PATTERN.fullmatch(value)
    if not match:
        raise UpdateError("Ongeldige player-versie")
    return tuple(int(part) for part in match.groups())


def target_version(update: Any, current_version: str) -> str | None:
    """Return an approved newer version from a device configuration."""
    if not isinstance(update, dict) or update.get("channel") == "off":
        return None
    target = str(update.get("target_version") or "")
    if not target:
        return None
    return target if parse_version(target) > parse_version(current_version) else None


def should_retry(attempt: Any, target: str, now: float | None = None) -> bool:
    """Throttle repeated attempts for the same failed release."""
    if not isinstance(attempt, dict) or attempt.get("target_version") != target:
        return True
    attempted_at = float(attempt.get("attempted_at") or 0)
    return (now if now is not None else time.time()) - attempted_at >= RETRY_SECONDS


def launch_update(target: str, state_path: Path, install_root: Path | None = None) -> None:
    """Start the updater outside the player service's systemd cgroup."""
    parse_version(target)
    root = install_root or Path.home() / ".local/share/rondo-player"
    unit = f"rondo-player-update-{target.replace('.', '-')}"
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--user",
            "--collect",
            f"--unit={unit}",
            "/usr/bin/python3",
            str(Path(__file__).resolve()),
            "apply",
            "--target-version",
            target,
            "--install-root",
            str(root),
            "--state",
            str(state_path),
        ],
        check=True,
        timeout=15,
    )


def mark_healthy(version: str, install_root: Path | None = None) -> None:
    """Mark a newly started release healthy for the rollback guard."""
    root = install_root or Path.home() / ".local/share/rondo-player"
    status_path = root / "update-status.json"
    with _update_lock(root):
        status = _read_json(status_path)
        if status.get("status") != "pending" or status.get("target_version") != version:
            return
        if status.get("healthy_version") == version:
            return
        status["healthy_version"] = version
        status["healthy_at"] = int(time.time())
        _write_json(status_path, status)


def apply_update(target: str, install_root: Path, state_path: Path) -> None:
    """Download, verify and atomically activate one signed release."""
    parse_version(target)
    install_root.mkdir(parents=True, exist_ok=True)
    with _update_lock(install_root):
        current_link = install_root / "current"
        previous_release = _current_release(current_link, install_root)
        release_dir = install_root / "releases" / target
        release_dir.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory(prefix="rondo-update-", dir=install_root) as download_dir:
            temporary = Path(download_dir)
            manifest_bytes = _download(_release_url(target, "manifest.json"), MAX_MANIFEST_BYTES)
            signature = _download(_release_url(target, "manifest.sig"), MAX_SIGNATURE_BYTES)
            _verify_signature(manifest_bytes, signature, Path(__file__).with_name("release-public.pem"), temporary)
            manifest = _validate_manifest(manifest_bytes, target)
            archive = _download(_release_url(target, manifest["artifact"]), MAX_ARCHIVE_BYTES)
            if not hashlib.sha256(archive).hexdigest() == manifest["sha256"]:
                raise UpdateError("Checksum van het releasepakket klopt niet")

            staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=release_dir.parent))
            try:
                archive_path = temporary / manifest["artifact"]
                archive_path.write_bytes(archive)
                _extract_archive(archive_path, staging)
                _validate_release(staging)
                if release_dir.exists():
                    shutil.rmtree(release_dir)
                os.replace(staging, release_dir)
            except Exception:
                shutil.rmtree(staging, ignore_errors=True)
                raise

        status = {
            "status": "pending",
            "target_version": target,
            "previous_release": str(previous_release),
            "started_at": int(time.time()),
        }
        _write_json(install_root / "update-status.json", status)
        _replace_symlink(install_root / "previous", previous_release)
        _replace_symlink(current_link, release_dir)

        try:
            _schedule_guard(target, release_dir, install_root, state_path)
        except Exception:
            _replace_symlink(current_link, previous_release)
            raise

    _restart_player()


def verify_health(target: str, install_root: Path, state_path: Path) -> None:
    """Keep a healthy release or roll back one that did not start."""
    parse_version(target)
    with _update_lock(install_root):
        status_path = install_root / "update-status.json"
        status = _read_json(status_path)
        if status.get("status") != "pending" or status.get("target_version") != target:
            return
        if status.get("healthy_version") == target:
            status["status"] = "completed"
            status["completed_at"] = int(time.time())
            _write_json(status_path, status)
            return

        previous = Path(str(status.get("previous_release") or "")).resolve()
        releases_root = (install_root / "releases").resolve()
        if not previous.is_dir() or releases_root not in previous.parents:
            raise UpdateError("Vorige release voor rollback ontbreekt")
        _replace_symlink(install_root / "current", previous)
        status["status"] = "rolled_back"
        status["rolled_back_at"] = int(time.time())
        status["error"] = f"Versie {target} startte niet gezond; rollback uitgevoerd"
        _write_json(status_path, status)
        _record_update_error(state_path, status["error"])
    _restart_player()


def _release_url(version: str, filename: str) -> str:
    return f"{RELEASE_BASE_URL}/v{version}/{filename}"


def _download(url: str, maximum: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "RondoPlayerUpdater/1"})
    with urllib.request.urlopen(request, timeout=30, context=ssl.create_default_context()) as response:
        final = urlparse(response.geturl())
        if final.scheme != "https" or not (
            final.hostname == "github.com"
            or (final.hostname or "").endswith(".githubusercontent.com")
        ):
            raise UpdateError("Release-download werd naar een onbekende host doorgestuurd")
        payload = response.read(maximum + 1)
    if len(payload) > maximum:
        raise UpdateError("Releasebestand is onverwacht groot")
    return payload


def _verify_signature(manifest: bytes, signature: bytes, public_key: Path, temporary: Path) -> None:
    manifest_path = temporary / "manifest.json"
    signature_path = temporary / "manifest.sig"
    manifest_path.write_bytes(manifest)
    signature_path.write_bytes(signature)
    result = subprocess.run(
        [
            "/usr/bin/openssl",
            "pkeyutl",
            "-verify",
            "-pubin",
            "-inkey",
            str(public_key),
            "-rawin",
            "-in",
            str(manifest_path),
            "-sigfile",
            str(signature_path),
        ],
        capture_output=True,
        check=False,
        timeout=15,
    )
    if result.returncode != 0:
        raise UpdateError("Handtekening van de playerrelease is ongeldig")


def _validate_manifest(raw: bytes, target: str) -> dict[str, str]:
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise UpdateError("Releasemanifest is ongeldig") from error
    artifact = f"rondo-player-{target}.tar.gz"
    if not isinstance(manifest, dict) or manifest.get("version") != target or manifest.get("artifact") != artifact:
        raise UpdateError("Releasemanifest hoort niet bij de aangevraagde versie")
    checksum = str(manifest.get("sha256") or "")
    if not re.fullmatch(r"[a-f0-9]{64}", checksum):
        raise UpdateError("Releasemanifest bevat geen geldige checksum")
    return {"version": target, "artifact": artifact, "sha256": checksum}


def _extract_archive(archive: Path, destination: Path) -> None:
    extracted_size = 0
    with tarfile.open(archive, "r:gz") as bundle:
        members = bundle.getmembers()
        for member in members:
            path = Path(member.name)
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "rondo_player":
                raise UpdateError("Releasepakket bevat een onveilig pad")
            if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                raise UpdateError("Releasepakket bevat een niet-toegestaan bestand")
            extracted_size += member.size
            if extracted_size > MAX_EXTRACTED_BYTES:
                raise UpdateError("Uitgepakte release is onverwacht groot")
        # Every path and member type is validated above before extraction.
        bundle.extractall(destination, members=members)


def _validate_release(release_dir: Path) -> None:
    package = release_dir / "rondo_player"
    required = [package / "__init__.py", package / "__main__.py", package / "updater.py", package / "release-public.pem"]
    if not all(path.is_file() for path in required):
        raise UpdateError("Releasepakket mist verplichte playerbestanden")
    for source in package.rglob("*.py"):
        try:
            compile(source.read_text(encoding="utf-8"), str(source), "exec")
        except (OSError, SyntaxError, UnicodeDecodeError) as error:
            raise UpdateError(f"Playerbestand {source.name} is ongeldig") from error


def _current_release(current_link: Path, install_root: Path) -> Path:
    if not current_link.is_symlink():
        raise UpdateError("Actieve playerrelease is niet atomisch geïnstalleerd")
    current = current_link.resolve()
    releases_root = (install_root / "releases").resolve()
    if not current.is_dir() or releases_root not in current.parents:
        raise UpdateError("Actieve playerrelease staat buiten de releasemap")
    return current


def _replace_symlink(link: Path, target: Path) -> None:
    temporary = link.with_name(f".{link.name}.tmp")
    temporary.unlink(missing_ok=True)
    os.symlink(target, temporary)
    os.replace(temporary, link)


def _schedule_guard(target: str, release_dir: Path, install_root: Path, state_path: Path) -> None:
    unit = f"rondo-player-update-guard-{target.replace('.', '-')}"
    subprocess.run(
        [
            "/usr/bin/systemd-run",
            "--user",
            "--collect",
            f"--unit={unit}",
            f"--on-active={HEALTH_TIMEOUT_SECONDS}s",
            "/usr/bin/python3",
            str(release_dir / "rondo_player/updater.py"),
            "guard",
            "--target-version",
            target,
            "--install-root",
            str(install_root),
            "--state",
            str(state_path),
        ],
        check=True,
        timeout=15,
    )


def _restart_player() -> None:
    subprocess.run(
        ["/usr/bin/systemctl", "--user", "restart", "rondo-player.service"],
        check=True,
        timeout=15,
    )


def _record_update_error(state_path: Path, message: str) -> None:
    state = _read_json(state_path)
    state["update_error"] = message[:300]
    _write_json(state_path, state, mode=0o600)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return {}


def _write_json(path: Path, value: dict[str, Any], mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


@contextmanager
def _update_lock(install_root: Path) -> Iterator[None]:
    install_root.mkdir(parents=True, exist_ok=True)
    lock_path = install_root / "update.lock"
    with lock_path.open("a", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def main() -> None:
    parser = argparse.ArgumentParser(description="Rondo Player updater")
    parser.add_argument("action", choices=("apply", "guard"))
    parser.add_argument("--target-version", required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.action == "apply":
            apply_update(args.target_version, args.install_root, args.state)
        else:
            verify_health(args.target_version, args.install_root, args.state)
    except Exception as error:
        _record_update_error(args.state, f"Player-update mislukt: {error}")
        raise


if __name__ == "__main__":
    main()
