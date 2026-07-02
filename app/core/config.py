"""Application-wide configuration and filesystem layout.

Every path used by the application is derived from PROJECT_ROOT so the app can be
moved or packaged without code changes.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

if getattr(sys, "frozen", False):
    # PyInstaller bundle: code+assets live in _MEIPASS, user data next to the exe
    _BUNDLE = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    APP_DIR = _BUNDLE / "app"
else:
    PROJECT_ROOT = Path(__file__).resolve().parents[2]
    APP_DIR = PROJECT_ROOT / "app"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = PROJECT_ROOT / "data"
CACHE_DIR = DATA_DIR / "cache"
EXPORT_DIR = DATA_DIR / "exports"
DB_PATH = DATA_DIR / "statlab.db"

HOST = os.environ.get("STATLAB_HOST", "127.0.0.1")
PORT = int(os.environ.get("STATLAB_PORT", "8765"))

APP_NAME = "StatLab"
APP_VERSION = "1.2.0"

# Maximum number of worker processes used by simulation fan-out.
MAX_WORKERS = max(1, (os.cpu_count() or 4) - 1)

# Curves stored/returned to the UI are downsampled to at most this many points.
CURVE_POINTS = 2000


def ensure_dirs() -> None:
    for d in (DATA_DIR, CACHE_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
