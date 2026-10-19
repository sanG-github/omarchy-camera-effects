"""Shared settings and paths. Stdlib only: the CLI imports this without the venv."""

import json
import os
from pathlib import Path

APP = "camera-effects"
CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP
CONFIG_FILE = CONFIG_DIR / "config.json"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / APP
MODELS_DIR = DATA_DIR / "models"
VENV_DIR = DATA_DIR / "venv"
RUNTIME_DIR = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")) / APP
STATUS_FILE = RUNTIME_DIR / "status.json"
BACKGROUNDS_DIR = CONFIG_DIR / "backgrounds"

# Loopback device created by /etc/modprobe.d/camera-effects.conf
VIRTUAL_DEVICE = "/dev/video42"

DEFAULTS = {
    "centerStage": True,
    "centerStageZoom": 0.6,
    "mirror": False,
    "portrait": False,
    "portraitStrength": 0.6,
    "studioLight": False,
    "studioLightStrength": 0.5,
    "backgroundImage": "",
    "width": 1280,
    "height": 720,
    "fps": 30,
    "camera": "auto",
}

# Keys the CLI accepts, with the type each is coerced to.
TYPES = {
    "centerStage": bool,
    "centerStageZoom": float,
    "mirror": bool,
    "portrait": bool,
    "portraitStrength": float,
    "studioLight": bool,
    "studioLightStrength": float,
    "backgroundImage": str,
    "width": int,
    "height": int,
    "fps": int,
    "camera": str,
}


def load():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text()))
    except (OSError, ValueError):
        pass
    return cfg


def save(cfg):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, indent=2) + "\n")
    tmp.replace(CONFIG_FILE)


def write_status(status):
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(status))
    tmp.replace(STATUS_FILE)


def read_status():
    try:
        return json.loads(STATUS_FILE.read_text())
    except (OSError, ValueError):
        return {"state": "stopped"}
