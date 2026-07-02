"""National-team data source (open, no API key).

Source: martj42/international_results — every men's senior international match
since 1872 (results.csv) plus the goalscorer log (goalscorers.csv, with penalty
and minute). Public dataset, downloaded once and cached on disk (refreshed every
24 h), then parsed into memory.
"""
from __future__ import annotations

import csv
import io
import threading
import time
from datetime import date, datetime
from functools import lru_cache
from typing import Optional

import httpx

from ...core.config import CACHE_DIR, ensure_dirs

BASE = "https://raw.githubusercontent.com/martj42/international_results/master"
RESULTS_URL = f"{BASE}/results.csv"
GOALS_URL = f"{BASE}/goalscorers.csv"
HEADERS = {"User-Agent": "StatLab/1.0 (research; local desktop app)"}
CACHE_HOURS = 24

_lock = threading.Lock()
_cache: dict[str, tuple[float, list]] = {}


def _download(url: str, fname: str) -> str:
    ensure_dirs()
    path = CACHE_DIR / fname
    fresh = path.exists() and (time.time() - path.stat().st_mtime) < CACHE_HOURS * 3600
    if not fresh:
        try:
            r = httpx.get(url, headers=HEADERS, timeout=40, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 1000:
                path.write_bytes(r.content)
        except httpx.HTTPError:
            pass
    if path.exists():
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _parse_date(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def load_results() -> list[dict]:
    """All played international matches, normalised to the engine schema."""
    with _lock:
        hit = _cache.get("results")
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    text = _download(RESULTS_URL, "intl_results.csv")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        d = _parse_date(row.get("date", ""))
        hs, as_ = row.get("home_score"), row.get("away_score")
        if d is None or not row.get("home_team") or not row.get("away_team"):
            continue
        if hs in (None, "", "NA") or as_ in (None, "", "NA"):
            continue
        try:
            hg, ag = int(hs), int(as_)
        except ValueError:
            continue
        out.append({
            "date": d.isoformat(),
            "home": row["home_team"].strip(),
            "away": row["away_team"].strip(),
            "fthg": hg, "ftag": ag,
            "ftr": "H" if hg > ag else ("D" if hg == ag else "A"),
            "tournament": (row.get("tournament") or "").strip(),
            "neutral": str(row.get("neutral", "")).strip().upper() == "TRUE",
        })
    with _lock:
        _cache["results"] = (time.time(), out)
    return out


def load_goals() -> list[dict]:
    """Goalscorer log: date, teams, scoring team, scorer, minute, penalty, own_goal."""
    with _lock:
        hit = _cache.get("goals")
        if hit and time.time() - hit[0] < 3600:
            return hit[1]
    text = _download(GOALS_URL, "intl_goalscorers.csv")
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        d = _parse_date(row.get("date", ""))
        if d is None or not row.get("scorer"):
            continue
        minute = row.get("minute", "")
        out.append({
            "date": d.isoformat(),
            "home": (row.get("home_team") or "").strip(),
            "away": (row.get("away_team") or "").strip(),
            "team": (row.get("team") or "").strip(),
            "scorer": row["scorer"].strip(),
            "minute": int(minute) if str(minute).isdigit() else None,
            "penalty": str(row.get("penalty", "")).strip().upper() == "TRUE",
            "own_goal": str(row.get("own_goal", "")).strip().upper() == "TRUE",
        })
    with _lock:
        _cache["goals"] = (time.time(), out)
    return out


@lru_cache(maxsize=1)
def all_nations() -> list[str]:
    teams = set()
    for m in load_results():
        teams.add(m["home"])
        teams.add(m["away"])
    return sorted(teams)


def upcoming_fixtures() -> list[dict]:
    """Scheduled internationals (score = NA) for fixture context."""
    text = _download(RESULTS_URL, "intl_results.csv")
    today = date.today().isoformat()
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        if row.get("home_score") in ("NA", "") and row.get("date", "") >= today:
            out.append({"date": row["date"], "home": row["home_team"],
                        "away": row["away_team"], "tournament": row.get("tournament", ""),
                        "neutral": str(row.get("neutral", "")).upper() == "TRUE"})
    return out
