#!/bin/bash
# The privileged half of install.sh: the loopback kernel module.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
pacman -S --needed --noconfirm v4l2loopback-dkms ffmpeg v4l-utils mpv
install -Dm644 "$here/modprobe.conf" /etc/modprobe.d/camera-effects.conf
echo v4l2loopback > /etc/modules-load.d/camera-effects.conf
modprobe -r v4l2loopback 2>/dev/null || true
modprobe v4l2loopback
