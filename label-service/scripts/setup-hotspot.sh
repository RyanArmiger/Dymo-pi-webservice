#!/usr/bin/env bash
# Configure a persistent standalone Wi-Fi hotspot.
set -euo pipefail

CONNECTION_NAME="label-hotspot"
SSID="Lab Label Printer"
WIFI_INTERFACE="wlan0"
HOTSPOT_ADDRESS="10.42.0.1/24"
WEB_ADDRESS="http://10.42.0.1:8000"
SERVICE_ENV="${LABEL_SERVICE_ENV:-/opt/label-service/.env}"
HOTSPOT_COUNTRY="${HOTSPOT_COUNTRY:-GB}"

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

configure_label_service() {
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
}

prompt_for_password() {
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
}

print_completion() {
    printf '\nHotspot setup complete.\n'
    printf 'SSID: %s\n' "${SSID}"
    printf 'Web:  %s\n' "${WEB_ADDRESS}"
    printf 'SSH:  ssh USERNAME@10.42.0.1\n'
}

setup_pi_zero_hotspot() {
    local hotspot_psk
    local hostapd_bin
    local dnsmasq_bin
    local ip_bin
    local nmcli_bin

    log "Installing the Pi Zero W hotspot components"
    apt-get update -y
    apt-get install -y network-manager hostapd dnsmasq-base wpasupplicant

    systemctl enable --now NetworkManager.service
    systemctl disable --now hostapd.service 2>/dev/null || true

    hostapd_bin="$(command -v hostapd)"
    dnsmasq_bin="$(command -v dnsmasq)"
    ip_bin="$(command -v ip)"
    nmcli_bin="$(command -v nmcli)"

    hotspot_psk="$(
        printf '%s\n' "${HOTSPOT_PASSWORD}" |
            wpa_passphrase "${SSID}" |
            sed -n 's/^[[:space:]]*psk=\([0-9a-f]\{64\}\)$/\1/p'
    )"
    unset HOTSPOT_PASSWORD
    [[ -n "${hotspot_psk}" ]] || fail "Could not derive the WPA2 key."

    log "Writing the Pi Zero W hotspot configuration"
    install -d -m 0755 /etc/hostapd /etc/dnsmasq.d

    {
        printf 'interface=%s\n' "${WIFI_INTERFACE}"
        printf 'driver=nl80211\n'
        printf 'ssid=%s\n' "${SSID}"
        printf 'country_code=%s\n' "${HOTSPOT_COUNTRY}"
        printf 'hw_mode=g\n'
        printf 'channel=6\n'
        printf 'ieee80211d=1\n'
        printf 'wmm_enabled=0\n'
        printf 'auth_algs=1\n'
        printf 'wpa=2\n'
        printf 'wpa_psk=%s\n' "${hotspot_psk}"
        printf 'wpa_key_mgmt=WPA-PSK\n'
        printf 'rsn_pairwise=CCMP\n'
        printf 'ieee80211w=0\n'
    } > /etc/hostapd/label-hotspot.conf
    chmod 0600 /etc/hostapd/label-hotspot.conf
    unset hotspot_psk

    {
        printf 'interface=%s\n' "${WIFI_INTERFACE}"
        printf 'bind-interfaces\n'
        printf 'dhcp-range=10.42.0.10,10.42.0.100,255.255.255.0,12h\n'
        printf 'dhcp-option=3,10.42.0.1\n'
        printf 'dhcp-option=6,10.42.0.1\n'
        printf 'domain-needed\n'
        printf 'bogus-priv\n'
    } > /etc/dnsmasq.d/label-hotspot.conf
    chmod 0644 /etc/dnsmasq.d/label-hotspot.conf

    # Stop the known NetworkManager AP profiles fighting hostapd for wlan0.
    for profile in "${CONNECTION_NAME}" "${CONNECTION_NAME}-test"; do
        if nmcli -g connection.id connection show "${profile}" >/dev/null 2>&1; then
            nmcli connection modify "${profile}" connection.autoconnect no
            nmcli connection down "${profile}" 2>/dev/null || true
        fi
    done

    cat > /etc/systemd/system/label-hotspot.service <<EOF
[Unit]
Description=Standalone label-printer hotspot (Pi Zero W)
Wants=NetworkManager.service
After=NetworkManager.service
Before=label-service.service

[Service]
Type=simple
ExecStartPre=-${nmcli_bin} connection down ${CONNECTION_NAME}
ExecStartPre=-${nmcli_bin} connection down ${CONNECTION_NAME}-test
ExecStartPre=${nmcli_bin} device set ${WIFI_INTERFACE} managed no
ExecStartPre=-${ip_bin} address flush dev ${WIFI_INTERFACE}
ExecStartPre=${ip_bin} address add ${HOTSPOT_ADDRESS} dev ${WIFI_INTERFACE}
ExecStartPre=${ip_bin} link set ${WIFI_INTERFACE} up
ExecStart=/bin/sh -c '${dnsmasq_bin} --keep-in-foreground --conf-file=/etc/dnsmasq.d/label-hotspot.conf & exec ${hostapd_bin} /etc/hostapd/label-hotspot.conf'
ExecStopPost=-${ip_bin} address flush dev ${WIFI_INTERFACE}
ExecStopPost=-${nmcli_bin} device set ${WIFI_INTERFACE} managed yes
Restart=on-failure
RestartSec=5
KillMode=control-group

[Install]
WantedBy=multi-user.target
EOF

    configure_label_service

    printf '\nWARNING: Starting the hotspot will disconnect Wi-Fi and any SSH session using it.\n'
    printf 'Reconnect to Wi-Fi "%s", then use ssh USERNAME@10.42.0.1.\n' "${SSID}"

    log "Starting the Pi Zero W hotspot"
    systemctl daemon-reload
    systemctl enable --now label-hotspot.service
}

setup_networkmanager_hotspot() {
    if ! command -v nmcli >/dev/null 2>&1; then
        log "Installing NetworkManager"
        apt-get update -y
        apt-get install -y network-manager
    fi

    log "Enabling NetworkManager"
    systemctl enable --now NetworkManager.service
    nmcli radio wifi on
    nmcli device set "${WIFI_INTERFACE}" managed yes

    if nmcli -g connection.id connection show "${CONNECTION_NAME}" >/dev/null 2>&1; then
        local connection_type
        connection_type="$(nmcli -g connection.type connection show "${CONNECTION_NAME}")"
        [[ "${connection_type}" == "802-11-wireless" ]] ||
            fail "Existing connection ${CONNECTION_NAME} is not a Wi-Fi profile."
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
    unset HOTSPOT_PASSWORD

    configure_label_service

    printf '\nWARNING: Activating the hotspot may disconnect an SSH session using %s.\n' "${WIFI_INTERFACE}"
    printf 'Reconnect to Wi-Fi "%s", then use ssh USERNAME@10.42.0.1.\n' "${SSID}"

    log "Activating ${CONNECTION_NAME}"
    nmcli connection up "${CONNECTION_NAME}" ifname "${WIFI_INTERFACE}"
}

if [[ "${EUID}" -ne 0 ]]; then
    fail "Run this script with sudo: sudo ./scripts/setup-hotspot.sh"
fi

if [[ ! -d "/sys/class/net/${WIFI_INTERFACE}" ]]; then
    fail "Wi-Fi interface ${WIFI_INTERFACE} was not found."
fi

PI_MODEL="$(tr -d '\0' </proc/device-tree/model 2>/dev/null || true)"
IS_ORIGINAL_PI_ZERO_W=false
if [[ "${PI_MODEL}" == *"Raspberry Pi Zero W"* && "${PI_MODEL}" != *"Zero 2 W"* ]]; then
    IS_ORIGINAL_PI_ZERO_W=true
fi

if systemctl cat ssh.service >/dev/null 2>&1; then
    log "Ensuring SSH remains enabled"
    systemctl enable --now ssh.service
fi

prompt_for_password

if [[ "${IS_ORIGINAL_PI_ZERO_W}" == true ]]; then
    log "Original Pi Zero W detected; using the hostapd fallback"
    setup_pi_zero_hotspot
else
    setup_networkmanager_hotspot
fi

print_completion
