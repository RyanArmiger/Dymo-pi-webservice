# label-service

A small Flask web app (plus a CLI prototype) for printing sample/experiment
labels on a DYMO LabelWriter 450 connected by USB, using `w72h72` two-up
25mm x 13mm label stock. It generates a QR code + text label, tracks issued
label IDs in a local SQLite database, and prints via CUPS. It's designed to
run continuously on a Raspberry Pi Zero W on the local lab network.

The application is in the `label-service` directory. After cloning the
repository, enter it before running the commands below:

```bash
cd label-service
```

For frequently used Pi, hotspot, serial-console and maintenance commands, see
[PI_COMMANDS.md](PI_COMMANDS.md).

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Web app (Flask development server - development only, not for production)
python run.py

# CLI prototype
python print_labels.py --help
```

The dev server binds to `HOST`/`PORT` from `config.py` (defaults
`0.0.0.0:8000`). For anything beyond local testing, use the systemd + waitress
setup below instead of `python run.py`.

The production dependency set is suitable for the original 32-bit ARMv6 Pi
Zero: Excel parsing uses openpyxl directly, without pandas/NumPy, and the Pi
installer reuses the operating system's compiled Pillow/openpyxl packages.

## Raspberry Pi deployment

On the original Pi Zero W, use **Raspberry Pi OS Legacy (Bookworm) Lite,
32-bit**, with the project copied or cloned onto it:

```bash
sudo ./install.sh
```

This will (see `install.sh` for full detail):

- install Python, the CUPS server/client and Debian's `printer-driver-dymo`
  package, then enable CUPS;
- create a dedicated, unprivileged `labelsvc` system user;
- copy the project into `/opt/label-service` (override with
  `INSTALL_DIR=/some/path sudo -E ./install.sh`);
- create a virtualenv and install `requirements.txt`;
- create `/var/lib/label-service` for the database and generated files, and
  generate a `.env` (from `.env.example`) with a random `SECRET_KEY` if one
  doesn't already exist;
- initialise the SQLite schema via `app.database.init_db()`;
- install and start the `label-service` systemd unit;
- preserve the existing `.env` and database across upgrades;
- reuse an existing CUPS queue named `PRINTER_NAME`, or create it when both
  the connected USB LabelWriter 450 and its installed driver are detected;
- generate the systemd unit for the selected `INSTALL_DIR` and start the
  app using the configured `HOST` and `PORT`.

After install, review/edit `/opt/label-service/.env` (in particular set
`ADMIN_PASSWORD_HASH` - see below) and restart:

```bash
sudo systemctl restart label-service
```

Re-running `sudo ./install.sh` at any time (e.g. after pulling new code) is
safe - it will re-sync files, reinstall dependencies, and restart the
service without touching your existing `.env` or database.

### Shared application password

The whole application is password protected by default. `ADMIN_PASSWORD_HASH`
must be a **werkzeug password hash**, not a plaintext password:

```bash
/opt/label-service/.venv/bin/python -c \
  'from getpass import getpass; from werkzeug.security import generate_password_hash; p=getpass("Password: "); q=getpass("Confirm: "); assert p == q, "Passwords did not match"; print(generate_password_hash(p))'
```

Paste the output into `ADMIN_PASSWORD_HASH` in `.env` and restart the
service.

## Calibration

The current defaults were checked on 28 July 2026 with a USB-connected DYMO
LabelWriter 450 and 25mm x 13mm two-up stock. The working setup uses a
`Custom.72x72` CUPS page, 300 DPI metadata and `print-scaling=none`. The
10mm calibration ruler printed at approximately 10mm and the A/B split
aligned with the sticker gap.

The observed output retained roughly 2mm blank at the left and 1mm at the
bottom. These are treated as empirical safe zones; their underlying cause
(driver/orientation/print origin rather than necessarily a hardware limit)
was not established. Sticker B content has a separate approximately 1mm
shift toward the centre gap. The compact content layout and enlarged QR were
accepted after physical test print job 231.

If the printer, driver or stock changes:

1. Print a calibration/alignment page using either:
   - the "calibration print" button in the `/admin` web page, or
   - `python print_labels.py --calibration --preview` (add `--preview` to
     inspect the generated image without sending it to the printer, drop it
     to actually print).
2. Compare the printed grid against the physical label stock.
3. If the alignment, gap, or rotation is off, adjust the corresponding
   `*_PX` / `OUTPUT_ROTATION_DEGREES` values in `.env` (or `config.py` for a
   permanent change) and repeat until the printed grid lines up with the
   physical labels.

## Hostname / mDNS (.local) access

To reach the Pi as `http://lab-labels.local:8000` on the lab network, set its
hostname:

```bash
sudo hostnamectl set-hostname lab-labels
# or: sudo raspi-config -> System Options -> Hostname
```

Raspberry Pi OS normally ships with Avahi (`avahi-daemon`) preinstalled and
running, which provides `.local` mDNS resolution automatically - no extra
configuration is done by `install.sh`, and none should be needed on a stock
Pi OS image. If your network doesn't support mDNS (some corporate/institutional
networks block it), fall back to the Pi's IP address:

```bash
hostname -I
```

and browse to `http://<that-ip>:8000` instead.

Opening firewall ports (if a firewall is enabled on the Pi or network) is
also not handled automatically - open port 8000 (or whichever `PORT` you
configured) manually if needed.

## Standalone Wi-Fi hotspot

For direct use without an existing Wi-Fi network, run:

```bash
sudo ./scripts/setup-hotspot.sh
```

The script prompts twice for a WPA2 password and creates a persistent
`label-hotspot`. On the original Pi Zero W it uses a minimal hostapd and
dnsmasq configuration because protected NetworkManager hotspots fail during
association on that Wi-Fi chipset. Other supported hardware continues to use
NetworkManager's hotspot mode. Because `wlan0` changes networks, an SSH
session using Wi-Fi may disconnect while the script activates the hotspot.

Users then connect to:

```text
Wi-Fi: Lab Label Printer
Open:  http://10.42.0.1:8000
```

Administration remains available after joining the hotspot:

```bash
ssh USERNAME@10.42.0.1
```

The hotspot starts automatically after reboot. It is standalone: the setup
does not add network fallback, internet sharing or a captive portal.

On hardware using the NetworkManager hotspot, use `sudo nmtui` and choose
**Activate a connection** to switch temporarily to another saved Wi-Fi.

On an original Pi Zero W, first stop the dedicated hotspot service, then use
NetworkManager:

```bash
sudo systemctl stop label-hotspot
sudo nmtui
```

Return to the label-printer hotspot with:

```bash
sudo systemctl start label-hotspot
```

It also starts automatically on the next reboot.

## Uninstalling

```bash
sudo ./uninstall.sh                # prompts before deleting install dir / data dir
sudo ./uninstall.sh --keep-data    # never touches /var/lib/label-service (the database)
sudo ./uninstall.sh --purge-user   # also removes the labelsvc system user
```

Safe to re-run even if a previous install only partially completed.

## Logs, history, and admin

- Service logs: `journalctl -u label-service -f` (or `-e` for the tail of
  past logs).
- Print/label history: the `/history` page and individual `/labels/<id>`
  pages in the web app.
- Batch upload template and batch printing: `/batch`, `/batch/upload`,
  `/batch/preview`, `/batch/print`, `/template.xlsx`.
- Original Excel workbooks for confirmed batches are archived under
  `BATCH_UPLOAD_DIR`, recorded with size and SHA-256 metadata, and can be
  downloaded again from History. Invalid uploads are not archived; abandoned
  previews expire from temporary storage.
- Admin area (counter reset, calibration print): `/admin` (password
  protected via `ADMIN_PASSWORD_HASH`).
- Health check: `/status`.

## Security notes

This app is intended for a local lab network and is not hardened for exposure
to the public internet. A shared password protects every application route,
sessions expire, and repeated incorrect logins are temporarily locked out.
This is not per-user authentication or an audit of individual identity.

The default deployment uses HTTP, so set `SESSION_COOKIE_SECURE=1` only after
putting the service behind HTTPS. Do not port-forward it; use a restricted
network, VPN or university SSO if access is needed beyond the lab.

### Future university SSO

When individual university account login is added, derive `requestor_id`
from the authenticated account rather than asking the user to type it. The
server-side identity should be authoritative for both single-label and batch
workflows, with any display-name-to-requestor-ID mapping configured centrally.

## Third-party assets

The bundled Roboto font files are copyright Google and are redistributed
under the Apache License 2.0. See
[`app/static/fonts/LICENSE-APACHE-2.0.txt`](app/static/fonts/LICENSE-APACHE-2.0.txt).

## Licence

The label service is released under the MIT License. See
[`../LICENSE`](../LICENSE). Third-party assets remain subject to their own
licence terms.
