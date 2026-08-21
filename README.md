# Rondo Player

Rondo Player turns a Raspberry Pi into a subscription-free Club TV player for
[Rondo Club](https://github.com/RondoHQ/rondo-club). It starts Chromium in kiosk
mode, pairs with Rondo using a short activation code, reports basic health, and
uses HDMI-CEC to switch the connected television on and off.

## What the pilot supports

- activation code on the television, approved by a Rondo administrator;
- a device credential stored only on the Pi (Rondo stores its HMAC hash);
- full-screen Chromium display with cached offline content;
- heartbeat and player-version reporting;
- scheduled and manual HDMI-CEC wake/sleep;
- predefined reload, browser restart, reboot, shutdown and CEC-test commands;
- automatic restart through a user-level systemd service.

The agent deliberately has no arbitrary remote-shell endpoint.

## Raspberry Pi installation

Use a Raspberry Pi 5 with **Raspberry Pi OS 64-bit with desktop**. Configure the
Wi-Fi network, hostname, user, password and SSH in Raspberry Pi Imager, then boot
the Pi while it is connected to the television over HDMI.

Clone this repository on the Pi and run:

```bash
chmod +x install.sh
./install.sh https://your-rondo-site.example
```

The installer adds `cec-utils`, installs the agent for the current desktop user,
grants only the fixed reboot and shutdown commands through `sudo`, and enables
its systemd user service. The TV then shows an activation code. In
Rondo, open **Club TV**, enter the code, name the screen and choose its
wake/sleep times.

Useful diagnostics:

```bash
systemctl --user status rondo-player
journalctl --user -u rondo-player -f
echo scan | cec-client -s -d 1
```

On Raspberry Pis with two micro-HDMI ports, the player automatically selects
the CEC adapter whose connector reports a physical HDMI address. To test a
specific port manually, append its device, for example:

```bash
printf 'pow 0\n' | cec-client -s -d 1 /dev/cec1
```

HDMI-CEC must also be enabled in the television's settings. Manufacturers use
names such as Anynet+, Bravia Sync, Simplink, VIERA Link and EasyLink.

## Development

The runtime uses only the Python standard library. Run the unit tests with:

```bash
python3 -m unittest discover -s tests -v
```
