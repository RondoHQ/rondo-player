#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID} -eq 0 ]]; then
  echo "Run dit script als de gewone desktopgebruiker, niet met sudo." >&2
  exit 1
fi

SITE_URL=${1:-}
if [[ ! ${SITE_URL} =~ ^https://[^/]+ ]]; then
  echo "Gebruik: ./install.sh https://jouw-rondo-site.nl" >&2
  exit 1
fi

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
INSTALL_DIR="${HOME}/.local/share/rondo-player"
CONFIG_DIR="${HOME}/.config/rondo-player"
SERVICE_DIR="${HOME}/.config/systemd/user"
VERSION=$(python3 - "${SCRIPT_DIR}/rondo_player/__init__.py" <<'PY'
import re
import sys

contents = open(sys.argv[1], encoding="utf-8").read()
match = re.search(r'^__version__ = "([0-9]+\.[0-9]+\.[0-9]+)"$', contents, re.MULTILINE)
if not match:
    raise SystemExit("Player-versie ontbreekt")
print(match.group(1))
PY
)
RELEASE_DIR="${INSTALL_DIR}/releases/${VERSION}"

sudo apt-get update
sudo apt-get install -y cec-utils

if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
  echo "Chromium ontbreekt. Installeer Raspberry Pi OS met desktop en voer dit script opnieuw uit." >&2
  exit 1
fi

mkdir -p "${INSTALL_DIR}/releases" "${CONFIG_DIR}" "${SERVICE_DIR}"
rm -rf "${RELEASE_DIR}"
mkdir -p "${RELEASE_DIR}"
cp -R "${SCRIPT_DIR}/rondo_player" "${RELEASE_DIR}/"
ln -sfn "${RELEASE_DIR}" "${INSTALL_DIR}/current.new"
mv -Tf "${INSTALL_DIR}/current.new" "${INSTALL_DIR}/current"

python3 - "${CONFIG_DIR}/config.json" "${SITE_URL%/}" <<'PY'
import json
import os
import sys

path, site_url = sys.argv[1:]
with open(path, "w", encoding="utf-8") as config_file:
    json.dump({"site_url": site_url}, config_file, indent=2)
    config_file.write("\n")
os.chmod(path, 0o600)
PY

install -m 0644 "${SCRIPT_DIR}/systemd/rondo-player.service" "${SERVICE_DIR}/rondo-player.service"

SUDOERS_FILE=/etc/sudoers.d/rondo-player-reboot
echo "${USER} ALL=(root) NOPASSWD: /usr/bin/systemctl reboot" | sudo tee "${SUDOERS_FILE}" >/dev/null
sudo chmod 0440 "${SUDOERS_FILE}"
sudo visudo -cf "${SUDOERS_FILE}" >/dev/null

systemctl --user daemon-reload
systemctl --user enable --now rondo-player.service

echo
echo "Rondo Player draait. De activatiecode verschijnt op de tv."
echo "Status bekijken: systemctl --user status rondo-player"
