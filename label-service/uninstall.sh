#!/usr/bin/env bash
# uninstall.sh - remove label-service from this machine.
#
#   sudo ./uninstall.sh                # prompts before deleting data
#   sudo ./uninstall.sh --keep-data    # never deletes /var/lib/label-service
#   sudo ./uninstall.sh --purge-user   # also removes the labelsvc system user
#
# Safe to run even if a previous install partially failed or was already
# partially removed - every step checks existence before acting.
set -euo pipefail

INSTALL_DIR="${INSTALL_DIR:-/opt/label-service}"
DATA_DIR="/var/lib/label-service"
SERVICE_NAME="label-service"
SERVICE_USER="labelsvc"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

KEEP_DATA=0
PURGE_USER=0

for arg in "$@"; do
    case "$arg" in
        --keep-data) KEEP_DATA=1 ;;
        --purge-user) PURGE_USER=1 ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Usage: $0 [--keep-data] [--purge-user]" >&2
            exit 1
            ;;
    esac
done

log()  { printf '\n==> %s\n' "$*"; }

if [[ "${EUID}" -ne 0 ]]; then
    echo "This script must be run as root (try: sudo ./uninstall.sh)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Stop + disable + remove the systemd service
# ---------------------------------------------------------------------------
if systemctl list-unit-files "${SERVICE_NAME}.service" >/dev/null 2>&1 && \
   systemctl list-unit-files "${SERVICE_NAME}.service" | grep -q "${SERVICE_NAME}.service"; then
    log "Stopping ${SERVICE_NAME}"
    systemctl stop "${SERVICE_NAME}" || true
    log "Disabling ${SERVICE_NAME}"
    systemctl disable "${SERVICE_NAME}" || true
else
    log "${SERVICE_NAME} service not registered with systemd, skipping stop/disable"
fi

if [[ -f "${UNIT_PATH}" ]]; then
    log "Removing unit file ${UNIT_PATH}"
    rm -f "${UNIT_PATH}"
else
    log "Unit file ${UNIT_PATH} not present, skipping"
fi

log "Reloading systemd"
systemctl daemon-reload || true

# ---------------------------------------------------------------------------
# Remove install directory
# ---------------------------------------------------------------------------
if [[ -d "${INSTALL_DIR}" ]]; then
    read -r -p "Delete install directory ${INSTALL_DIR}? [y/N] " reply
    case "${reply}" in
        [yY]|[yY][eE][sS])
            log "Removing ${INSTALL_DIR}"
            rm -rf "${INSTALL_DIR}"
            ;;
        *)
            log "Leaving ${INSTALL_DIR} in place"
            ;;
    esac
else
    log "${INSTALL_DIR} does not exist, skipping"
fi

# ---------------------------------------------------------------------------
# Remove data directory (the database!) unless --keep-data
# ---------------------------------------------------------------------------
if [[ "${KEEP_DATA}" -eq 1 ]]; then
    log "--keep-data given, leaving ${DATA_DIR} untouched"
elif [[ -d "${DATA_DIR}" ]]; then
    read -r -p "Delete data directory ${DATA_DIR}? This includes the label database! [y/N] " reply
    case "${reply}" in
        [yY]|[yY][eE][sS])
            log "Removing ${DATA_DIR}"
            rm -rf "${DATA_DIR}"
            ;;
        *)
            log "Leaving ${DATA_DIR} in place"
            ;;
    esac
else
    log "${DATA_DIR} does not exist, skipping"
fi

# ---------------------------------------------------------------------------
# Remove the labelsvc system user, only if --purge-user was given
# ---------------------------------------------------------------------------
if [[ "${PURGE_USER}" -eq 1 ]]; then
    if id "${SERVICE_USER}" >/dev/null 2>&1; then
        log "Removing system user '${SERVICE_USER}'"
        userdel "${SERVICE_USER}" || true
    else
        log "System user '${SERVICE_USER}' does not exist, skipping"
    fi
else
    log "Leaving system user '${SERVICE_USER}' in place (pass --purge-user to remove it)"
fi

log "Uninstall complete"
