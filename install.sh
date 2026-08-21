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

sudo apt-get update
sudo apt-get install -y cec-utils

if ! command -v chromium >/dev/null 2>&1 && ! command -v chromium-browser >/dev/null 2>&1; then
  echo "Chromium ontbreekt. Installeer Raspberry Pi OS met desktop en voer dit script opnieuw uit." >&2
  exit 1
fi

mkdir -p "${INSTALL_DIR}/app" "${CONFIG_DIR}" "${SERVICE_DIR}"
cp -R "${SCRIPT_DIR}/rondo_player" "${INSTALL_DIR}/app/"

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

SUDOERS_FILE=/etc/sudoers.d/rondo-player-power
echo "${USER} ALL=(root) NOPASSWD: /usr/bin/systemctl reboot, /usr/bin/systemctl poweroff" | sudo tee "${SUDOERS_FILE}" >/dev/null
sudo chmod 0440 "${SUDOERS_FILE}"
sudo visudo -cf "${SUDOERS_FILE}" >/dev/null

LEGACY_SUDOERS_FILE=/etc/sudoers.d/rondo-player-reboot
if [[ -f ${LEGACY_SUDOERS_FILE} ]]; then
  sudo rm "${LEGACY_SUDOERS_FILE}"
fi

systemctl --user daemon-reload
systemctl --user enable --now rondo-player.service

echo
echo "Rondo Player draait. De activatiecode verschijnt op de tv."
echo "Status bekijken: systemctl --user status rondo-player"
