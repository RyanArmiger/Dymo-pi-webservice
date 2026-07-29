#!/usr/bin/env bash
# Configure a persistent standalone Wi-Fi hotspot using NetworkManager.
set -euo pipefail

CONNECTION_NAME="label-hotspot"
SSID="Lab Label Printer"
WIFI_INTERFACE="wlan0"
HOTSPOT_ADDRESS="10.42.0.1/24"
WEB_ADDRESS="http://10.42.0.1:8000"
SERVICE_ENV="${LABEL_SERVICE_ENV:-/opt/label-service/.env}"

log() {
    printf '\n==> %s\n' "$*"
}

fail() {
    printf 'ERROR: %s\n' "$*" >&2
    exit 1
}

set_env_value() {
    local file="$1"
    local key="$2"
    local value="$3"

    if grep -q "^${key}=" "${file}"; then
        sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
    else
        printf '\n%s=%s\n' "${key}" "${value}" >> "${file}"
    fi
}

if [[ "${EUID}" -ne 0 ]]; then
    fail "Run this script with sudo: sudo ./scripts/setup-hotspot.sh"
fi

if [[ ! -d "/sys/class/net/${WIFI_INTERFACE}" ]]; then
    fail "Wi-Fi interface ${WIFI_INTERFACE} was not found."
fi

PI_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
OS_CODENAME=""
if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    OS_CODENAME="${VERSION_CODENAME:-}"
fi

if [[ "${PI_MODEL}" == *"Raspberry Pi Zero W"* && "${PI_MODEL}" != *"Zero 2 W"* && "${OS_CODENAME}" == "trixie" ]]; then
    fail "Protected NetworkManager hotspots are currently broken on the original Pi Zero W with Raspberry Pi OS Trixie. Install Raspberry Pi OS Legacy (Bookworm) Lite 32-bit."
fi

if ! command -v nmcli >/dev/null 2>&1; then
    log "Installing NetworkManager"
    apt-get update -y
    apt-get install -y network-manager
fi

log "Enabling NetworkManager"
systemctl enable --now NetworkManager.service
nmcli radio wifi on
nmcli device set "${WIFI_INTERFACE}" managed yes

if systemctl cat ssh.service >/dev/null 2>&1; then
    log "Ensuring SSH remains enabled"
    systemctl enable --now ssh.service
fi

while true; do
    read -r -s -p "Hotspot WPA2 password (8-63 characters): " HOTSPOT_PASSWORD
    printf '\n'
    if (( ${#HOTSPOT_PASSWORD} < 8 || ${#HOTSPOT_PASSWORD} > 63 )); then
        printf 'Password must contain between 8 and 63 characters.\n' >&2
        continue
    fi

    read -r -s -p "Confirm hotspot password: " HOTSPOT_PASSWORD_CONFIRM
    printf '\n'
    if [[ "${HOTSPOT_PASSWORD}" != "${HOTSPOT_PASSWORD_CONFIRM}" ]]; then
        printf 'Passwords did not match; try again.\n' >&2
        continue
    fi
    unset HOTSPOT_PASSWORD_CONFIRM
    break
done

if nmcli -g connection.id connection show "${CONNECTION_NAME}" >/dev/null 2>&1; then
    CONNECTION_TYPE="$(nmcli -g connection.type connection show "${CONNECTION_NAME}")"
    if [[ "${CONNECTION_TYPE}" != "802-11-wireless" ]]; then
        fail "Existing connection ${CONNECTION_NAME} is not a Wi-Fi profile."
    fi
    log "Updating NetworkManager connection ${CONNECTION_NAME}"
else
    log "Creating NetworkManager connection ${CONNECTION_NAME}"
    nmcli connection add \
        type wifi \
        ifname "${WIFI_INTERFACE}" \
        con-name "${CONNECTION_NAME}" \
        ssid "${SSID}"
fi

nmcli connection modify "${CONNECTION_NAME}" \
    connection.interface-name "${WIFI_INTERFACE}" \
    connection.autoconnect yes \
    connection.autoconnect-priority 100 \
    connection.autoconnect-retries 0 \
    802-11-wireless.ssid "${SSID}" \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel 6 \
    802-11-wireless.powersave 2 \
    802-11-wireless-security.key-mgmt wpa-psk \
    802-11-wireless-security.proto rsn \
    802-11-wireless-security.pairwise ccmp \
    802-11-wireless-security.group ccmp \
    802-11-wireless-security.pmf 1 \
    802-11-wireless-security.wps-method 1 \
    802-11-wireless-security.psk "${HOTSPOT_PASSWORD}" \
    ipv4.method shared \
    ipv4.addresses "${HOTSPOT_ADDRESS}" \
    ipv4.never-default yes \
    ipv6.method disabled

# NetworkManager's shared IPv4 mode supplies DHCP/DNS to hotspot clients.
# This setup creates no fallback or upstream internet connection.
unset HOTSPOT_PASSWORD

if [[ -f "${SERVICE_ENV}" ]]; then
    log "Configuring the label service to listen on 0.0.0.0:8000"
    set_env_value "${SERVICE_ENV}" HOST "0.0.0.0"
    set_env_value "${SERVICE_ENV}" PORT "8000"
    if systemctl is-active --quiet label-service.service; then
        systemctl restart label-service.service
    fi
else
    log "Label service environment not found at ${SERVICE_ENV}; built-in defaults already use 0.0.0.0:8000"
fi

printf '\nWARNING: Activating the hotspot may disconnect an SSH session using %s.\n' "${WIFI_INTERFACE}"
printf 'Reconnect to Wi-Fi "%s", then use ssh USERNAME@10.42.0.1.\n' "${SSID}"

log "Activating ${CONNECTION_NAME}"
nmcli connection up "${CONNECTION_NAME}" ifname "${WIFI_INTERFACE}"

printf '\nHotspot setup complete.\n'
printf 'SSID: %s\n' "${SSID}"
printf 'Web:  %s\n' "${WEB_ADDRESS}"
printf 'SSH:  ssh USERNAME@10.42.0.1\n'
