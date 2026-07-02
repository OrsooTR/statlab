"""Match-data acquisition from football-data.co.uk.

Responsibilities:
  * derive the season list per competition (first_season .. current, automatic);
  * download season CSVs with on-disk caching (finished seasons are immutable,
    the current season re-downloads after `cache_hours_current_season`);
  * parse and normalise the many historical CSV layouts into one match schema;
  * download the upcoming-fixtures feed (with bookmaker odds) for daily views.
"""
from __future__ import annotations

import io
import json
import time
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path
from typing import Optional

import httpx
import pandas as pd

from ..core.config import CACHE_DIR, ensure_dirs

COMPETITIONS_PATH = Path(__file__).with_name("competitions.json")

HEADERS = {"User-Agent": "StatLab/1.0 (research; local desktop app)"}
TIMEOUT = httpx.Timeout(30.0)

# football-data column → our schema (extra columns are preserved in extra_json)
COLUMN_MAP = {
    "Date": "date", "HomeTeam": "home", "AwayTeam": "away",
    "FTHG": "fthg", "FTAG": "ftag", "FTR": "ftr",
    "HTHG": "hthg", "HTAG": "htag",
    "HS": "hs", "AS": "as_", "HST": "hst", "AST": "ast",
    "HC": "hc", "AC": "ac", "HY": "hy", "AY": "ay", "HR": "hr", "AR": "ar",
    "HF": "hf", "AF": "af",
    "B365H": "b365h", "B365D": "b365d", "B365A": "b365a",
    "B365CH": "b365ch", "B365CD": "b365cd", "B365CA": "b365ca",
    "Avg>2.5": "avg_over25", "Avg<2.5": "avg_under25",
}
INT_COLS = ["fthg", "ftag", "hthg", "htag", "hs", "as_", "hst", "ast",
            "hc", "ac", "hy", "ay", "hr", "ar", "hf", "af"]
FLOAT_COLS = ["b365h", "b365d", "b365a", "b365ch", "b365cd", "b365ca",
              "avg_over25", "avg_under25"]


@lru_cache(maxsize=1)
def load_competitions() -> dict:
    with open(COMPETITIONS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def competition(code: str) -> Optional[dict]:
    for c in load_competitions()["competitions"]:
        if c["code"] == code:
            return c
    return None


def current_season_code(today: Optional[date] = None) -> str:
    """football-data codes seasons as e.g. '2526' for 2025/26; seasons start in July."""
    d = today or date.today()
    start = d.year if d.month >= 7 else d.year - 1
    return f"{start % 100:02d}{(start + 1) % 100:02d}"


def season_codes(first_season: str, today: Optional[date] = None) -> list[str]:
    d = today or date.today()
    first_start = 2000 + int(first_season[:2])
    cur_start = d.year if d.month >= 7 else d.year - 1
    return [f"{y % 100:02d}{(y + 1) % 100:02d}" for y in range(first_start, cur_start + 1)]


def season_label(code: str) -> str:
    return f"20{code[:2]}/{code[2:]}"


# ------------------------------------------------------------------ downloading
def _cache_path(code: str, season: str) -> Path:
    return CACHE_DIR / f"{code}-{season}.csv"


def _is_fresh(path: Path, max_age_hours: float) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    if max_age_hours <= 0:
        return True
    return (time.time() - path.stat().st_mtime) < max_age_hours * 3600


def fetch_season_csv(code: str, season: str, client: Optional[httpx.Client] = None) -> Optional[Path]:
    """Return the on-disk CSV for a (league, season), downloading if needed."""
    ensure_dirs()
    cfg = load_competitions()["source"]
    path = _cache_path(code, season)
    is_current = season == current_season_code()
    max_age = cfg["cache_hours_current_season"] if is_current else 0
    if _is_fresh(path, max_age):
        return path
    url = cfg["results_url"].format(season=season, code=code)
    close = False
    if client is None:
        client = httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
        close = True
    try:
        resp = client.get(url)
        if resp.status_code != 200 or len(resp.content) < 200:
            return path if path.exists() else None
        path.write_bytes(resp.content)
        return path
    except httpx.HTTPError:
        return path if path.exists() else None
    finally:
        if close:
            client.close()


def parse_season_csv(path: Path, code: str, season: str) -> list[dict]:
    """Normalise one season CSV into match dicts matching the fb_matches schema."""
    try:
        df = pd.read_csv(path, encoding="utf-8", encoding_errors="replace",
                         on_bad_lines="skip")
    except Exception:
        return []
    if "Date" not in df.columns or "HomeTeam" not in df.columns:
        return []
    df = df.dropna(subset=["Date", "HomeTeam", "AwayTeam"])
    known = {src: dst for src, dst in COLUMN_MAP.items() if src in df.columns}
    extra_cols = [c for c in df.columns
                  if c not in COLUMN_MAP and c not in ("Div", "Time", "Referee")]
    out: list[dict] = []
    for _, row in df.iterrows():
        rec: dict = {"league": code, "season": season}
        for src, dst in known.items():
            v = row[src]
            rec[dst] = None if pd.isna(v) else v
        d = _parse_date(str(rec.get("date", "")))
        if d is None or not rec.get("home") or not rec.get("away"):
            continue
        rec["date"] = d.isoformat()
        rec["home"] = str(rec["home"]).strip()
        rec["away"] = str(rec["away"]).strip()
        for c in INT_COLS:
            if rec.get(c) is not None:
                try:
                    rec[c] = int(rec[c])
                except (TypeError, ValueError):
                    rec[c] = None
        for c in FLOAT_COLS:
            if rec.get(c) is not None:
                try:
                    rec[c] = float(rec[c])
                except (TypeError, ValueError):
                    rec[c] = None
        extras = {}
        for c in extra_cols:
            v = row[c]
            if not pd.isna(v):
                extras[c] = v.item() if hasattr(v, "item") else v
        rec["extra_json"] = json.dumps(extras, default=str) if extras else None
        out.append(rec)
    return out


def _parse_date(s: str) -> Optional[date]:
    s = s.strip()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def fetch_fixtures(client: Optional[httpx.Client] = None) -> list[dict]:
    """Upcoming fixtures (all supported leagues) with bookmaker odds."""
    ensure_dirs()
    cfg = load_competitions()["source"]
    path = CACHE_DIR / "fixtures.csv"
    if not _is_fresh(path, 6):
        close = False
        if client is None:
            client = httpx.Client(timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
            close = True
        try:
            resp = client.get(cfg["fixtures_url"])
            if resp.status_code == 200 and len(resp.content) > 100:
                path.write_bytes(resp.content)
        except httpx.HTTPError:
            pass
        finally:
            if close:
                client.close()
    if not path.exists():
        return []
    try:
        df = pd.read_csv(path, encoding="utf-8", encoding_errors="replace",
                         on_bad_lines="skip")
    except Exception:
        return []
    codes = {c["code"] for c in load_competitions()["competitions"]}
    out = []
    for _, row in df.iterrows():
        div = str(row.get("Div", ""))
        if div not in codes:
            continue
        d = _parse_date(str(row.get("Date", "")))
        if d is None:
            continue
        out.append({
            "league": div, "date": d.isoformat(),
            "time": str(row.get("Time", "")) if not pd.isna(row.get("Time")) else "",
            "home": str(row.get("HomeTeam", "")).strip(),
            "away": str(row.get("AwayTeam", "")).strip(),
            "b365h": _f(row.get("B365H")), "b365d": _f(row.get("B365D")),
            "b365a": _f(row.get("B365A")),
        })
    return out


def _f(v) -> Optional[float]:
    try:
        if v is None or pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None
