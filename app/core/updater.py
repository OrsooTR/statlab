"""In-app updater against public GitHub releases.

Checks the repository's latest release, compares its tag to the running version,
and (on a packaged Windows build) downloads the installer and launches it so the
app updates itself in place — no manual re-download. Falls back to opening the
release page when running from source.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import httpx

from .config import APP_VERSION, DATA_DIR, ensure_dirs

REPO = os.environ.get("STATLAB_REPO", "OrsooTR/statlab")
RELEASES_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"
HEADERS = {"Accept": "application/vnd.github+json", "User-Agent": "StatLab-updater"}


def _parse_version(tag: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", tag or "")
    return tuple(int(n) for n in nums[:3]) or (0,)


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def current_version() -> str:
    return APP_VERSION


def check_update(timeout: float = 12.0) -> dict:
    """Query the latest release and compare versions."""
    try:
        r = httpx.get(RELEASES_API, headers=HEADERS, timeout=timeout, follow_redirects=True)
        if r.status_code == 404:
            return {"available": False, "current": APP_VERSION,
                    "message": "No public releases found for the repository yet."}
        r.raise_for_status()
        rel = r.json()
    except httpx.HTTPError as exc:
        return {"available": False, "current": APP_VERSION,
                "error": f"could not reach GitHub: {exc}"}
    tag = rel.get("tag_name", "")
    latest = _parse_version(tag)
    available = latest > _parse_version(APP_VERSION)
    asset = _pick_asset(rel.get("assets", []))
    return {
        "available": available,
        "current": APP_VERSION,
        "latest": tag,
        "notes": (rel.get("body") or "").strip()[:4000],
        "published_at": rel.get("published_at"),
        "asset_name": asset.get("name") if asset else None,
        "asset_url": asset.get("browser_download_url") if asset else None,
        "asset_size": asset.get("size") if asset else None,
        "release_page": rel.get("html_url", RELEASES_PAGE),
        "can_auto_install": bool(available and asset and is_frozen()
                                 and str(asset.get("name", "")).lower().endswith(".exe")),
    }


def _pick_asset(assets: list[dict]) -> Optional[dict]:
    """Prefer the installer (.exe), else the zip."""
    exes = [a for a in assets if str(a.get("name", "")).lower().endswith(".exe")]
    if exes:
        return exes[0]
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    return zips[0] if zips else None


def download_asset(url: str, name: str, progress=None) -> Path:
    ensure_dirs()
    dest = DATA_DIR / "updates"
    dest.mkdir(parents=True, exist_ok=True)
    out = dest / name
    with httpx.stream("GET", url, headers={"User-Agent": "StatLab-updater"},
                      follow_redirects=True, timeout=60.0) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done = 0
        with open(out, "wb") as f:
            for chunk in r.iter_bytes(chunk_size=262144):
                f.write(chunk)
                done += len(chunk)
                if progress and total:
                    progress(done / total, f"downloading update… {done // 1_048_576} MB")
    return out


def apply_update(progress=None) -> dict:
    """Download the latest installer and launch it, then quit the app."""
    info = check_update()
    if not info.get("available"):
        return {"status": "up_to_date", "current": APP_VERSION}
    if not info.get("asset_url"):
        return {"status": "manual", "release_page": info["release_page"],
                "message": "No downloadable asset; opening the release page."}
    if progress:
        progress(0.05, "downloading update…")
    path = download_asset(info["asset_url"], info["asset_name"], progress)
    name = path.name.lower()
    if name.endswith(".exe") and is_frozen():
        if progress:
            progress(0.98, "launching installer…")
        # start the installer, then exit so it can replace the running files
        subprocess.Popen([str(path), "/SILENT", "/NORESTART"],
                         creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
        threading.Thread(target=_delayed_exit, daemon=True).start()
        return {"status": "installing",
                "message": "Installer launched. The app will close and reopen updated."}
    # not frozen or a zip: reveal the download
    try:
        os.startfile(str(path.parent))  # noqa: S606 - open the folder for the user
    except Exception:
        pass
    return {"status": "downloaded", "path": str(path),
            "message": f"Update downloaded to {path}. Run it to install."}


def _delayed_exit() -> None:
    time.sleep(2.5)
    os._exit(0)
