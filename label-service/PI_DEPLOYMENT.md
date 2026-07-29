# Raspberry Pi deployment checklist

Target: original single-core, 32-bit Raspberry Pi Zero/Zero W running
Raspberry Pi OS Bookworm, with a DYMO LabelWriter 450 connected by USB.

The deployment deliberately avoids pandas/NumPy and reuses Raspberry Pi OS's
compiled Pillow/openpyxl packages. Waitress is limited to two threads to keep
memory use modest on the 512 MB device.

## Before installation

- Connect the Pi to the lab network and attach the printer by USB.
- Copy the `label-service` folder to the Pi.
- Confirm the Pi has internet access for Debian and Python packages.

## Install

From the copied project directory:

```bash
sudo ./install.sh
```

The installer sets up CUPS, the Debian DYMO driver, the application
environment, persistent data directory, systemd service and (when the USB
device is detected) the `DYMO_LabelWriter_450` queue.

## Set the shared password

Generate a password hash on the Pi:

```bash
/opt/label-service/.venv/bin/python -c \
  'from getpass import getpass; from werkzeug.security import generate_password_hash; p=getpass("Password: "); q=getpass("Confirm: "); assert p == q, "Passwords did not match"; print(generate_password_hash(p))'
```

Paste the complete result after `ADMIN_PASSWORD_HASH=` in
`/opt/label-service/.env`, then restart:

```bash
sudo systemctl restart label-service
```

Do not reuse the temporary Mac test password.

## Optional hostname

```bash
sudo hostnamectl set-hostname lab-labels
```

After rebooting, try `http://lab-labels.local:8000`. If `.local` resolution
is unavailable, use the address reported by `hostname -I`.

## Optional standalone hotspot

To operate without an existing Wi-Fi network:

```bash
sudo ./scripts/setup-hotspot.sh
```

Choose a WPA2 password when prompted. Connect clients to
`Lab Label Printer`, then open `http://10.42.0.1:8000`. SSH remains available
at `ssh USERNAME@10.42.0.1`.

On the original Pi Zero W, the script uses a minimal hostapd/dnsmasq fallback
because protected NetworkManager hotspots fail during client association on
that hardware. To switch temporarily to another saved network, run
`sudo systemctl stop label-hotspot`, then `sudo nmtui`. Return to hotspot mode
with `sudo systemctl start label-hotspot` or reboot. Other hardware uses the
NetworkManager hotspot directly and can switch connections with `sudo nmtui`.

## Verification

```bash
systemctl is-active cups
systemctl is-active label-service
lpstat -p DYMO_LabelWriter_450
curl -I http://127.0.0.1:8000/
```

The HTTP response should redirect to the sign-in page.

From a phone on the permitted network:

1. Sign in.
2. Print Label 1 with Label 2 blank; confirm one ID is allocated.
3. Print two independent labels; confirm two IDs are allocated.
4. Upload an odd-sized Excel batch; confirm the final position is blank and
   consumes no ID.
5. Check History and reprint one label.
6. Reboot the Pi and confirm the service, database history and printer queue
   persist.

Back up the whole `/var/lib/label-service` directory, not only `labels.db`;
it also contains the original workbooks archived for confirmed batches.

## Diagnostics

```bash
journalctl -u label-service -e
lpstat -t
lpinfo -v
lpinfo -m | grep -i dymo
```
