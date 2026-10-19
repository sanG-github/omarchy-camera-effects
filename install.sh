#!/bin/bash
# Install Camera Effects: virtual camera module, Python runtime, models,
# user service, and the Omarchy bar widget.
set -euo pipefail

here="$(cd "$(dirname "$0")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}/camera-effects"
plugin_id="sang.camera-effects"

if [[ ! -e /dev/video42 ]] || ! grep -q '^v4l2loopback' /etc/modules-load.d/camera-effects.conf 2>/dev/null; then
  echo "==> Installing the v4l2loopback module (needs root)"
  if [[ -t 0 ]]; then sudo bash "$here/share/install-root.sh"; else pkexec bash "$here/share/install-root.sh"; fi
fi

echo "==> Python runtime"
[[ -x "$data/venv/bin/python" ]] || python3 -m venv "$data/venv"
"$data/venv/bin/pip" install -q --upgrade onnxruntime opencv-python-headless numpy

echo "==> Models"
mkdir -p "$data/models"
fetch() { [[ -s "$data/models/$1" ]] || curl -fsSL -o "$data/models/$1" "$2"; }
fetch face_detection_yunet_2023mar.onnx \
  https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx
fetch rvm_mobilenetv3_fp32.onnx \
  https://github.com/PeterL1n/RobustVideoMatting/releases/download/v1.0.0/rvm_mobilenetv3_fp32.onnx

echo "==> Command and service"
mkdir -p "$HOME/.local/bin" "$HOME/.config/systemd/user"
ln -sf "$here/bin/camera-effects" "$HOME/.local/bin/camera-effects"
install -m644 "$here/share/camera-effects.service" "$HOME/.config/systemd/user/camera-effects.service"
systemctl --user daemon-reload
systemctl --user enable camera-effects.service
systemctl --user restart camera-effects.service

echo "==> Bar widget"
plugins="$HOME/.config/omarchy/plugins"
mkdir -p "$plugins"
# Installed with `omarchy plugin add`, the checkout already is the plugin.
if [[ "$(realpath "$here")" != "$(realpath -m "$plugins/$plugin_id")" ]]; then
  [[ -e "$plugins/$plugin_id" ]] || ln -s "$here" "$plugins/$plugin_id"
fi
if command -v omarchy >/dev/null; then
  omarchy-shell shell rescanPlugins >/dev/null 2>&1 || true
  omarchy plugin enable "$plugin_id" >/dev/null 2>&1 || true
fi

echo "Done. Pick \"FaceTime HD Camera (Effects)\" as the camera in your call app."
