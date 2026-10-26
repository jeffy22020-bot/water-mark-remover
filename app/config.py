"""Paths and persisted user settings."""

from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

DEFAULT_SETTINGS = {
    # "ai" uses the LaMa model, "fast" uses classic OpenCV inpainting.
    "image_quality": "ai",
    # "auto" picks AI when a GPU is available, otherwise Fast.
    "video_quality": "auto",
    # "auto" tries the GPU (DirectML/CUDA) first, "cpu" forces the CPU.
    "device": "auto",
    # x264 constant rate factor for processed videos (lower = better quality).
    "video_crf": 18,
    # Folder where results are saved when no save dialog is used.
    "output_dir": "",
    # Browser to borrow cookies from for sites that require a login (yt-dlp).
    "cookies_browser": "",
}

_lock = threading.Lock()


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def base_dir() -> Path:
    """Folder holding the bundled resources (app/web, models, samples)."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent.parent


def web_dir() -> Path:
    return base_dir() / "app" / "web"


def models_dir() -> Path:
    override = os.environ.get("WMR_MODELS_DIR")
    if override:
        return Path(override)
    return base_dir() / "models"


def samples_dir() -> Path:
    return base_dir() / "samples"


def data_dir() -> Path:
    override = os.environ.get("WMR_DATA_DIR")
    if override:
        path = Path(override)
    elif sys.platform == "win32":
        path = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "WatermarkRemover"
    elif sys.platform == "darwin":
        path = Path.home() / "Library" / "Application Support" / "WatermarkRemover"
    else:
        path = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "watermark-remover"
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_output_dir() -> Path:
    pictures = Path.home() / "Downloads"
    return pictures if pictures.is_dir() else Path.home()


def _settings_path() -> Path:
    return data_dir() / "settings.json"


def load_settings() -> dict:
    settings = dict(DEFAULT_SETTINGS)
    try:
        with open(_settings_path(), encoding="utf-8") as fh:
            stored = json.load(fh)
        settings.update({k: v for k, v in stored.items() if k in DEFAULT_SETTINGS})
    except (OSError, ValueError):
        pass
    return settings


def save_settings(changes: dict) -> dict:
    with _lock:
        settings = load_settings()
        settings.update({k: v for k, v in changes.items() if k in DEFAULT_SETTINGS})
        tmp = _settings_path().with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(settings, fh, indent=2)
        os.replace(tmp, _settings_path())
    return settings
