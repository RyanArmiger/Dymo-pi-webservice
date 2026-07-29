# Lab Label Printer - Command Cheat Sheet

Commands marked **Mac** run in Terminal on the Mac. Commands marked **Pi**
run after logging in to the Raspberry Pi through serial or SSH.

## Connect to the Pi over the serial cable

### Find the cable on the Mac

**Mac:**

```bash
ls /dev/cu.*
```

Look for a name containing `usbserial` or `usbmodem`.

### Open the serial console

**Mac:**

```bash
screen /dev/cu.usbserial-XXXX 115200
```

Replace `/dev/cu.usbserial-XXXX` with the exact name reported by `ls`.

To leave `screen`, press `Ctrl+A`, then `\`, then confirm with `y`.

If the terminal is blank, press Enter. If the Pi is still booting, wait for
the login prompt.

## Connect over the hotspot

Connect the computer or phone to:

```text
Wi-Fi: Lab Label Printer
Web:   http://10.42.0.1:8000
```

**Mac:**

```bash
ssh pi-user@192.0.2.1
```

## Switch the Pi temporarily to normal Wi-Fi

The Pi cannot download updates while it is providing the standalone hotspot.

**Pi:**

```bash
sudo systemctl stop label-hotspot
sudo nmtui
```

In `nmtui`, choose **Activate a connection** and select the normal Wi-Fi
network.

Return to hotspot mode when finished:

```bash
sudo systemctl start label-hotspot
```

Starting the hotspot disconnects any SSH session using the normal Wi-Fi.
The serial console remains connected.

## Update the application from GitHub

First connect the Pi to normal Wi-Fi as described above.

**Pi:**

```bash
cd ~/Dymo-pi-webservice
git pull --rebase
cd label-service
sudo ./install.sh
sudo systemctl start label-hotspot
```

The installer preserves `/opt/label-service/.env` and the label database.

## Set or change the shared application password

Generate a password hash without putting the password in shell history:

**Pi:**

```bash
/opt/label-service/.venv/bin/python -c \
'from getpass import getpass; from werkzeug.security import generate_password_hash; p=getpass("Password: "); q=getpass("Confirm: "); assert p == q, "Passwords did not match"; print(generate_password_hash(p))'
```

Copy the complete output, then edit:

```bash
sudo nano /opt/label-service/.env
```

Paste the hash after:

```text
ADMIN_PASSWORD_HASH=
```

In `nano`, save with `Ctrl+O`, press Enter, then exit with `Ctrl+X`.

Restart the application:

```bash
sudo systemctl restart label-service
```

## Check the services

**Pi:**

```bash
sudo systemctl status label-service --no-pager -l
sudo systemctl status label-hotspot --no-pager -l
sudo systemctl status cups --no-pager -l
```

Show recent application or hotspot logs:

```bash
sudo journalctl -u label-service -e --no-pager
sudo journalctl -u label-hotspot -e --no-pager
```

Follow new log messages live:

```bash
sudo journalctl -u label-service -f
```

Press `Ctrl+C` to stop following logs.

## Check the web service locally on the Pi

**Pi:**

```bash
curl -I http://127.0.0.1:8000/
sudo ss -ltnp | grep ':8000'
```

The listener should show `0.0.0.0:8000`.

## Check the DYMO printer

**Pi:**

```bash
lpstat -p DYMO_LabelWriter_450
lpstat -o DYMO_LabelWriter_450
```

Check all detected printers and the default destination:

```bash
lpstat -p -d
```

## Restart or shut down safely

**Pi:**

```bash
sudo reboot
```

or:

```bash
sudo poweroff
```

Wait for shutdown to complete before removing power.
