"""Launch StatLab in a native desktop window (Edge WebView2 via pywebview).

The FastAPI backend runs on a background thread; the window hosts the SPA.
Falls back to the default browser if no WebView runtime is available.
"""
from __future__ import annotations

import multiprocessing
import os
import sys
import threading
import time

from app.core.config import APP_NAME, DATA_DIR, HOST, PORT

# In a windowed (no-console) PyInstaller build sys.stdout/stderr are None and
# uvicorn's logging handlers crash on startup. Route them to a log file in the
# per-user data directory (Program Files is not writable for normal users).
if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        _log = open(DATA_DIR / "statlab.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        _log = open(os.devnull, "w")  # never let logging setup crash startup
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log

import httpx
import uvicorn

URL = f"http://{HOST}:{PORT}"


def _serve() -> None:
    from app.main import app  # direct app object: reliable inside frozen bundles
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")


def _wait_ready(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(f"{URL}/api/health", timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            time.sleep(0.25)
    return False


def main() -> None:
    threading.Thread(target=_serve, daemon=True).start()
    if not _wait_ready():
        raise SystemExit("backend failed to start")
    try:
        import webview
        window = webview.create_window(
            APP_NAME, URL, width=1500, height=950, min_size=(1100, 700),
            background_color="#0d0e1a")
        webview.start()
    except Exception:
        import webbrowser
        webbrowser.open(URL)
        while True:  # keep the backend alive while the browser is used
            time.sleep(3600)


if __name__ == "__main__":
    multiprocessing.freeze_support()  # required for the Monte Carlo process pool in the exe
    main()
