# Changelog

## [0.2.1] - 2026-08-21

### Fixed

- Use the active desktop session's verified power policy for remote shutdown, so updating an existing player does not require a new sudoers rule.

## [0.2.0] - 2026-08-21

### Added

- Allow administrators to shut down the Raspberry Pi through the predefined remote command channel.

## [0.1.2] - 2026-08-21

### Fixed

- Select the HDMI-CEC adapter whose HDMI port is actually connected on dual-port Raspberry Pis.
- Prevent cached or replayed remote commands from executing more than once.
- Keep Chromium in a dedicated profile and disable accidental pinch scaling.

## [0.1.1] - 2026-08-20

### Fixed

- Prevent Chromium's keyring password dialog from blocking kiosk startup.
- Bypass an incompatible Raspberry Pi OS Chromium launcher flag on 16 KB kernels.

## [0.1.0] - 2026-08-15

### Added

- Initial Raspberry Pi narrowcasting player with pairing, heartbeat, HDMI-CEC and kiosk display.
