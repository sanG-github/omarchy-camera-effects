#!/bin/bash
# Remove Camera Effects: service, command, runtime and the virtual camera
# module config. Settings and uploaded backgrounds stay unless --purge.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}/camera-effects"
conf="${XDG_CONFIG_HOME:-$HOME/.config}/camera-effects"

echo "==> Service"
systemctl --user disable --now camera-effects.service 2>/dev/null || true
rm -f "$HOME/.config/systemd/user/camera-effects.service"
systemctl --user daemon-reload

echo "==> Command and runtime"
[[ -L $HOME/.local/bin/camera-effects ]] && rm -f "$HOME/.local/bin/camera-effects"
rm -rf "$data"
[[ ${1:-} == --purge ]] && rm -rf "$conf"

if [[ -e /etc/modprobe.d/camera-effects.conf || -e /etc/modules-load.d/camera-effects.conf ]]; then
  echo "==> Virtual camera module config (needs root)"
  cmd='rm -f /etc/modprobe.d/camera-effects.conf /etc/modules-load.d/camera-effects.conf; modprobe -r v4l2loopback 2>/dev/null || true'
  if [[ -t 0 ]]; then sudo bash -c "$cmd"; else pkexec bash -c "$cmd"; fi
fi

echo "Done. The v4l2loopback-dkms package is left installed; remove it with: sudo pacman -Rns v4l2loopback-dkms"
echo "Remove the bar widget with: omarchy plugin remove camera-effects"
