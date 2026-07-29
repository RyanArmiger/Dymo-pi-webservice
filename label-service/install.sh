#!/usr/bin/env bash
# install.sh - install/upgrade label-service on a Raspberry Pi (Debian-based).
#
# Idempotent: safe to re-run. Must be run as root, from within the project
# directory you want installed (e.g. after cloning/copying it onto the Pi).
#
#   sudo ./install.sh
#
# Override the install location with INSTALL_DIR=/some/path sudo -E ./install.sh
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/label-service}"
DATA_DIR="/var/lib/label-service"
SERVICE_NAME="label-service"
SERVICE_USER="labelsvc"
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log()  { printf '\n==> %s\n' "$*"; }
warn() { printf '\nWARNING: %s\n' "$*" >&2; }

# ---------------------------------------------------------------------------
# 1. Preconditions
# ---------------------------------------------------------------------------
if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (try: sudo ./install.sh)" >&2
    exit 1
fi

if ! command -v apt-get >/dev/null 2>&1; then
    echo "This script requires a Debian/Raspberry Pi OS host (apt-get not found)." >&2
    exit 1
fi

if [[ ! "${INSTALL_DIR}" =~ ^/[A-Za-z0-9._/-]+$ ]]; then
    echo "INSTALL_DIR must be an absolute path without spaces or shell metacharacters." >&2
    exit 1
fi

log "Installing on: $(uname -a)"
log "Source directory: ${SOURCE_DIR}"
log "Install directory: ${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# 2. System dependencies
# ---------------------------------------------------------------------------
log "Installing system dependencies (Python, CUPS, DYMO driver)"
apt-get update -y
apt-get install -y \
    python3-venv \
    python3-pip \
    python3-pil \
    python3-openpyxl \
    cups \
    cups-client \
    printer-driver-dymo

log "Enabling the local CUPS print service"
systemctl enable --now cups

# ---------------------------------------------------------------------------
# 3. Dedicated system user/group
# ---------------------------------------------------------------------------
if id "${SERVICE_USER}" >/dev/null 2>&1; then
    log "System user '${SERVICE_USER}' already exists, skipping creation"
else
    log "Creating system user/group '${SERVICE_USER}'"
    useradd --system --no-create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

# ---------------------------------------------------------------------------
# 4. Install directory + project files
# ---------------------------------------------------------------------------
log "Copying project files into ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude '.venv' \
        --exclude 'generated' \
        --exclude 'instance' \
        --exclude '.env' \
        --exclude '.git' \
        --exclude '__pycache__' \
        "${SOURCE_DIR}/" "${INSTALL_DIR}/"
else
    # Fallback if rsync isn't available: plain copy, still skipping the
    # directories we don't want to bring across.
    find "${SOURCE_DIR}" -mindepth 1 -maxdepth 1 \
        ! -name '.venv' ! -name 'generated' ! -name 'instance' \
        ! -name '.env' ! -name '.git' ! -name '__pycache__' \
        -exec cp -a {} "${INSTALL_DIR}/" \;
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# 5. Python virtualenv + dependencies
# ---------------------------------------------------------------------------
if [[ -x "${INSTALL_DIR}/.venv/bin/python" ]]; then
    log "Virtualenv already present at ${INSTALL_DIR}/.venv"
else
    log "Creating virtualenv at ${INSTALL_DIR}/.venv"
    # Reuse Raspberry Pi OS's compiled Pillow/openpyxl packages. This avoids
    # expensive ARMv6 source builds on the original 512 MB Pi Zero.
    sudo -u "${SERVICE_USER}" python3 -m venv --system-site-packages "${INSTALL_DIR}/.venv"
fi

log "Installing Python dependencies from requirements.txt"
sudo -u "${SERVICE_USER}" "${INSTALL_DIR}/.venv/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${INSTALL_DIR}/.venv/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

# ---------------------------------------------------------------------------
# 6. Data directory + .env (must exist before init_db, since DATABASE_PATH /
#    GENERATED_DIR in .env point here)
# ---------------------------------------------------------------------------
log "Creating data directory ${DATA_DIR}"
mkdir -p "${DATA_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${DATA_DIR}"

if [[ -f "${INSTALL_DIR}/.env" ]]; then
    log ".env already exists at ${INSTALL_DIR}/.env, leaving it untouched"
else
    log "Creating ${INSTALL_DIR}/.env from .env.example"
    cp "${INSTALL_DIR}/.env.example" "${INSTALL_DIR}/.env"
    GENERATED_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
    # Substitute the blank SECRET_KEY= line with a freshly generated value.
    sed -i "s|^SECRET_KEY=.*|SECRET_KEY=${GENERATED_SECRET}|" "${INSTALL_DIR}/.env"
    chown "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_DIR}/.env"
    chmod 640 "${INSTALL_DIR}/.env"
    warn "ADMIN_PASSWORD_HASH is blank in ${INSTALL_DIR}/.env. Generate one with:"
    warn "  ${INSTALL_DIR}/.venv/bin/python -c \"from werkzeug.security import generate_password_hash; print(generate_password_hash('your-chosen-password'))\""
    warn "then set ADMIN_PASSWORD_HASH in ${INSTALL_DIR}/.env and restart the service."
fi

# ---------------------------------------------------------------------------
# 7. Initialise the SQLite database (idempotent - init_db only creates
#    tables/rows that don't already exist)
# ---------------------------------------------------------------------------
log "Initialising database"
# Load .env only after dropping privileges: it is intentionally writable by
# the service account and must never be sourced by the root shell.
sudo -u "${SERVICE_USER}" bash -c '
    set -a
    # shellcheck disable=SC1090
    source "$1"
    set +a
    cd "$2"
    exec "$2/.venv/bin/python" -c "from app import database; database.init_db()"
' _ "${INSTALL_DIR}/.env" "${INSTALL_DIR}"

# ---------------------------------------------------------------------------
# 8. systemd unit
# ---------------------------------------------------------------------------
log "Installing systemd unit"
sed "s|@@INSTALL_DIR@@|${INSTALL_DIR}|g" \
    "${INSTALL_DIR}/label-service.service" \
    > "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}"
# A service that is already running keeps the old Python process after files
# are copied into INSTALL_DIR. Restart on every install/upgrade so the newly
# deployed code is actually loaded; restart also starts an inactive service.
systemctl restart "${SERVICE_NAME}"

sleep 1
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "${SERVICE_NAME} is active"
else
    warn "${SERVICE_NAME} does not appear to be active. Check: journalctl -u ${SERVICE_NAME} -e"
fi

# ---------------------------------------------------------------------------
# 9. Configure/check DYMO CUPS queue
# ---------------------------------------------------------------------------
PRINTER_NAME_TO_CHECK="$(
    awk -F= '/^PRINTER_NAME=/{sub(/^[^=]*=/, ""); print; exit}' "${INSTALL_DIR}/.env"
)"
PRINTER_NAME_TO_CHECK="${PRINTER_NAME_TO_CHECK:-DYMO_LabelWriter_450}"
log "Checking CUPS queue '${PRINTER_NAME_TO_CHECK}'"
if lpstat -p "${PRINTER_NAME_TO_CHECK}" >/dev/null 2>&1; then
    log "CUPS queue '${PRINTER_NAME_TO_CHECK}' found"
else
    DEVICE_URI="$(lpinfo -v 2>/dev/null | awk 'tolower($0) ~ /usb:\\/\\/dymo\\/labelwriter(%20|[[:space:]])450/ {print $2; exit}')"
    DRIVER_MODEL="$(lpinfo -m 2>/dev/null | awk 'tolower($0) ~ /dymo.*labelwriter 450([^0-9]|$)/ {print $1; exit}')"

    if [[ -n "${DEVICE_URI}" && -n "${DRIVER_MODEL}" ]]; then
        log "Creating queue from detected device '${DEVICE_URI}'"
        lpadmin \
            -p "${PRINTER_NAME_TO_CHECK}" \
            -E \
            -v "${DEVICE_URI}" \
            -m "${DRIVER_MODEL}" \
            -o printer-is-shared=false
        log "CUPS queue '${PRINTER_NAME_TO_CHECK}' created and enabled"
    else
        warn "Could not automatically create '${PRINTER_NAME_TO_CHECK}'."
        warn "Detected device URI: ${DEVICE_URI:-none}"
        warn "Detected DYMO driver: ${DRIVER_MODEL:-none}"
        warn "Connect the printer, then inspect 'lpinfo -v' and 'lpinfo -m'."
        warn "You can also add it through http://localhost:631/admin."
    fi
fi

# ---------------------------------------------------------------------------
# 10. Summary
# ---------------------------------------------------------------------------
PORT_TO_SHOW="$(
    awk -F= '/^PORT=/{sub(/^[^=]*=/, ""); print; exit}' "${INSTALL_DIR}/.env"
)"
PORT_TO_SHOW="${PORT_TO_SHOW:-8000}"
HOSTNAME_TO_SHOW="$(hostname)"

cat <<EOF

============================================================
 label-service install complete
============================================================
 Service status : systemctl status ${SERVICE_NAME}
 Live logs      : journalctl -u ${SERVICE_NAME} -f
 Web UI         : http://${HOSTNAME_TO_SHOW}.local:${PORT_TO_SHOW}
                  (fallback: http://<pi-ip-address>:${PORT_TO_SHOW}, see 'hostname -I')

 Reminders:
  - Edit ${INSTALL_DIR}/.env to set ADMIN_PASSWORD_HASH, then
    'sudo systemctl restart ${SERVICE_NAME}'.
  - Run the calibration print from /admin before trusting label
    alignment on the physical DYMO 450 stock.
  - .local (mDNS/Avahi) hostname resolution and any firewall port
    opening are NOT handled by this script - see README.md.
============================================================
EOF
