"""Live match-data providers.

Two interchangeable backends behind one interface:

* **ApiFootballProvider** — real-time data from API-Football (api-sports.io):
  live fixtures, daily fixtures, per-match events, lineups (formations,
  starters, substitutes) and statistics. Requires a free API key
  (https://www.api-football.com — free tier: 100 requests/day), entered in the
  Live Center settings. Responses are cached on disk/memory with short TTLs
  and a daily request budget is tracked so the free quota is never burned by
  the auto-refresh loop.

* **DemoProvider** — a deterministic live-match simulator (clearly labelled
  DEMO in the UI). Matches kick off on a rolling schedule anchored to the real
  clock; events, statistics and lineups are generated from a per-match seeded
  RNG so successive polls are consistent, goals arrive at realistic Poisson
  rates, and the whole Live Center is fully exercisable offline.

The provider choice + API key live in data/settings.json.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import numpy as np

from ...core.config import DATA_DIR, ensure_dirs

SETTINGS_PATH = DATA_DIR / "settings.json"
API_BASE = "https://v3.football.api-sports.io"

_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()
_settings_lock = threading.Lock()


# ------------------------------------------------------------------- settings
def load_settings() -> dict:
    ensure_dirs()
    with _settings_lock:
        if SETTINGS_PATH.exists():
            try:
                return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"provider": "auto", "api_key": "", "requests_today": 0,
                "requests_date": ""}


def save_settings(s: dict) -> None:
    ensure_dirs()
    with _settings_lock:
        SETTINGS_PATH.write_text(json.dumps(s, indent=2), encoding="utf-8")


def get_provider():
    s = load_settings()
    choice = s.get("provider", "auto")
    if choice == "api_football" and s.get("api_key"):
        return ApiFootballProvider(s["api_key"])
    if choice == "demo":
        return DemoProvider()
    from .aggregator import AutoAggregator  # local import: avoids a cycle
    return AutoAggregator()


# --------------------------------------------------------------- cache helper
def _cached(key: str, ttl: float, fn):
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
    val = fn()
    with _cache_lock:
        _cache[key] = (now, val)
        if len(_cache) > 300:
            for k in sorted(_cache, key=lambda k: _cache[k][0])[:100]:
                _cache.pop(k, None)
    return val


# ============================================================== API-Football
class ApiFootballProvider:
    name = "api_football"
    is_demo = False

    def __init__(self, api_key: str) -> None:
        self.key = api_key

    # -- quota tracking -------------------------------------------------------
    def _budget_ok(self) -> bool:
        s = load_settings()
        today = date.today().isoformat()
        if s.get("requests_date") != today:
            s["requests_date"] = today
            s["requests_today"] = 0
            save_settings(s)
        return s.get("requests_today", 0) < 95  # free tier: 100/day, keep margin

    def _count_request(self) -> None:
        s = load_settings()
        s["requests_today"] = s.get("requests_today", 0) + 1
        s["requests_date"] = date.today().isoformat()
        save_settings(s)

    def _get(self, path: str, params: dict, ttl: float) -> Optional[list]:
        key = "af:" + path + ":" + json.dumps(params, sort_keys=True)

        def fetch():
            if not self._budget_ok():
                raise RuntimeError("API-Football daily request budget exhausted "
                                   "(free tier: 100/day). Data will refresh tomorrow.")
            r = httpx.get(f"{API_BASE}{path}", params=params,
                          headers={"x-apisports-key": self.key}, timeout=20)
            self._count_request()
            r.raise_for_status()
            body = r.json()
            if body.get("errors") and any(body["errors"].values() if isinstance(body["errors"], dict) else body["errors"]):
                raise RuntimeError(f"API-Football error: {body['errors']}")
            return body.get("response", [])

        return _cached(key, ttl, fetch)

    # -- interface ------------------------------------------------------------
    def status(self) -> dict:
        s = load_settings()
        return {"provider": self.name, "demo": False,
                "requests_today": s.get("requests_today", 0), "daily_budget": 95}

    def live_matches(self) -> list[dict]:
        rows = self._get("/fixtures", {"live": "all"}, ttl=55)
        return [self._norm_fixture(r) for r in rows]

    def today_matches(self) -> list[dict]:
        rows = self._get("/fixtures", {"date": date.today().isoformat()}, ttl=600)
        return [self._norm_fixture(r) for r in rows]

    def match_detail(self, fixture_id: str) -> dict:
        fid = int(fixture_id)
        fx = self._get("/fixtures", {"id": fid}, ttl=50)
        if not fx:
            raise KeyError("fixture not found")
        info = self._norm_fixture(fx[0])
        events = self._get("/fixtures/events", {"fixture": fid}, ttl=50) or []
        lineups = self._get("/fixtures/lineups", {"fixture": fid}, ttl=900) or []
        stats = self._get("/fixtures/statistics", {"fixture": fid}, ttl=90) or []
        return {
            "info": info,
            "events": [self._norm_event(e, info) for e in events],
            "lineups": self._norm_lineups(lineups),
            "stats": self._norm_stats(stats, info),
        }

    # -- normalisers ----------------------------------------------------------
    @staticmethod
    def _norm_fixture(r: dict) -> dict:
        fx, lg, tm, goals = r["fixture"], r["league"], r["teams"], r["goals"]
        st = fx.get("status", {})
        country = lg.get("country", "") or ""
        is_cup = country.lower() in ("world", "europe", "") or "cup" in str(lg.get("name", "")).lower()
        return {
            "id": str(fx["id"]),
            "league": lg.get("name", ""),
            "league_code": str(lg.get("id", "")),
            "category": "international" if country.lower() == "world"
                        else ("continental" if is_cup else "domestic"),
            "country": country,
            "flag": "",
            "fd_code": None,
            "kickoff": fx.get("date", ""),
            "status": st.get("short", ""),
            "minute": st.get("elapsed"),
            "live": st.get("short") in ("1H", "2H", "ET", "BT", "P", "HT", "LIVE"),
            "finished": st.get("short") in ("FT", "AET", "PEN"),
            "home": tm["home"]["name"], "away": tm["away"]["name"],
            "score_home": goals.get("home"), "score_away": goals.get("away"),
        }

    @staticmethod
    def _norm_event(e: dict, info: dict) -> dict:
        side = "home" if e.get("team", {}).get("name") == info["home"] else "away"
        t = e.get("type", "")
        detail = e.get("detail", "")
        return {
            "minute": (e.get("time", {}).get("elapsed") or 0),
            "extra": e.get("time", {}).get("extra"),
            "side": side,
            "type": t.lower(),          # goal | card | subst | var
            "detail": detail,
            "player": (e.get("player") or {}).get("name") or "",
            "assist": (e.get("assist") or {}).get("name") or "",
        }

    @staticmethod
    def _norm_lineups(rows: list) -> Optional[dict]:
        if len(rows) < 2:
            return None
        out = {}
        for i, side in enumerate(("home", "away")):
            r = rows[i]
            out[side] = {
                "team": r.get("team", {}).get("name", ""),
                "formation": r.get("formation", ""),
                "coach": (r.get("coach") or {}).get("name", ""),
                "starters": [{"number": p["player"].get("number"),
                              "name": p["player"].get("name"),
                              "pos": p["player"].get("pos")}
                             for p in r.get("startXI", [])],
                "substitutes": [{"number": p["player"].get("number"),
                                 "name": p["player"].get("name"),
                                 "pos": p["player"].get("pos")}
                                for p in r.get("substitutes", [])],
            }
        return out

    @staticmethod
    def _norm_stats(rows: list, info: dict) -> dict:
        out = {"home": {}, "away": {}}
        for r in rows:
            side = "home" if r.get("team", {}).get("name") == info["home"] else "away"
            for s in r.get("statistics", []):
                key = str(s.get("type", "")).lower().replace(" ", "_")
                out[side][key] = s.get("value")
        return out


# ==================================================================== DEMO
DEMO_TEAMS = [
    ("Milan", "Inter"), ("Real Madrid", "Barcelona"), ("Arsenal", "Liverpool"),
    ("Bayern", "Dortmund"), ("PSG", "Marseille"), ("Ajax", "PSV"),
]
DEMO_LEAGUES = ["Serie A", "La Liga", "Premier League", "Bundesliga",
                "Ligue 1", "Eredivisie"]
DEMO_META = [
    {"country": "Italy", "flag": "🇮🇹", "fd_code": "I1"},
    {"country": "Spain", "flag": "🇪🇸", "fd_code": "SP1"},
    {"country": "England", "flag": "🏴", "fd_code": "E0"},
    {"country": "Germany", "flag": "🇩🇪", "fd_code": "D1"},
    {"country": "France", "flag": "🇫🇷", "fd_code": "F1"},
    {"country": "Netherlands", "flag": "🇳🇱", "fd_code": "N1"},
]
POS_ORDER = ["G", "D", "D", "D", "D", "M", "M", "M", "F", "F", "F"]
FIRST = ["Luca", "Marco", "Andrea", "Paolo", "Diego", "Karim", "Leo", "Kylian",
         "Erling", "Jude", "Pedri", "Bruno", "Nico", "Sandro", "Rafa", "Theo",
         "Jan", "Sven", "Ivan", "Milan", "Dusan", "Viktor"]
LAST = ["Rossi", "Bianchi", "Martinez", "Silva", "Costa", "Muller", "Diaz",
        "Fernandez", "Lopez", "Moretti", "Kovac", "Novak", "Petrov", "Jansen",
        "Vos", "Berg", "Kimura", "Sato", "Okafor", "Traore", "Keita", "Mendy"]


class DemoProvider:
    """Deterministic streaming simulation of live football (labelled DEMO)."""

    name = "demo"
    is_demo = True

    CYCLE = 130 * 60  # a full "matchday" every 130 real minutes

    def status(self) -> dict:
        return {"provider": self.name, "demo": True,
                "note": "Built-in simulation. Add a free API-Football key in "
                        "settings for real live data."}

    # every match m kicks off at a staggered offset within the cycle; the match
    # clock runs at 2x real time so a full match lasts 45 real minutes.
    def _matches_state(self) -> list[dict]:
        now = time.time()
        cycle_start = now - (now % self.CYCLE)
        out = []
        for i, (h, a) in enumerate(DEMO_TEAMS):
            offset = i * 15 * 60
            kick = cycle_start + offset
            elapsed_real = now - kick
            match_id = f"demo-{int(cycle_start)}-{i}"
            if elapsed_real < 0:
                minute, status = None, "NS"
            else:
                m = int(elapsed_real / 60 * 2)  # 2x speed
                if m >= 90:
                    minute, status = 90, "FT"
                elif 45 <= m < 48:
                    minute, status = 45, "HT"
                else:
                    minute = min(m if m < 45 else m - 3, 90)
                    status = "1H" if m < 45 else "2H"
            out.append({"id": match_id, "index": i, "kick": kick,
                        "minute": minute, "status": status,
                        "home": h, "away": a, "league": DEMO_LEAGUES[i]})
        return out

    def _rng(self, match_id: str, salt: str = "") -> np.random.Generator:
        seed = int(hashlib.sha256((match_id + salt).encode()).hexdigest()[:12], 16)
        return np.random.default_rng(seed)

    def _timeline(self, st: dict) -> list[dict]:
        """Full 90' event script for a match, generated once per match id."""
        rng = self._rng(st["id"], "timeline")
        mu_h, mu_a = 1.6, 1.2
        events = []
        for side, mu in (("home", mu_h), ("away", mu_a)):
            team = st[side]
            for _ in range(int(rng.poisson(mu))):
                events.append({"minute": int(rng.integers(2, 90)), "side": side,
                               "type": "goal", "detail": "Normal Goal",
                               "player": _name(rng), "assist": _name(rng)})
            for _ in range(int(rng.poisson(5.5))):
                on = rng.random() < 0.4
                events.append({"minute": int(rng.integers(1, 90)), "side": side,
                               "type": "shot",
                               "detail": "Shot on target" if on else "Shot off target",
                               "player": _name(rng), "assist": ""})
            for _ in range(int(rng.poisson(2.0))):
                events.append({"minute": int(rng.integers(8, 90)), "side": side,
                               "type": "card", "detail": "Yellow Card",
                               "player": _name(rng), "assist": ""})
            if rng.random() < 0.06:
                events.append({"minute": int(rng.integers(30, 88)), "side": side,
                               "type": "card", "detail": "Red Card",
                               "player": _name(rng), "assist": ""})
            for m in sorted(rng.choice(np.arange(46, 88), size=3, replace=False)):
                out_p, in_p = _name(rng), _name(rng)
                events.append({"minute": int(m), "side": side, "type": "subst",
                               "detail": f"{in_p} in, {out_p} out",
                               "player": in_p, "assist": out_p})
            for _ in range(int(rng.poisson(5))):
                events.append({"minute": int(rng.integers(1, 90)), "side": side,
                               "type": "corner", "detail": "Corner", "player": "", "assist": ""})
        events.sort(key=lambda e: e["minute"])
        return events

    def _visible(self, st: dict) -> list[dict]:
        if st["minute"] is None:
            return []
        cutoff = st["minute"] if st["status"] != "FT" else 90
        return [e for e in self._timeline(st) if e["minute"] <= cutoff]

    def _score(self, st: dict) -> tuple[Optional[int], Optional[int]]:
        if st["minute"] is None:
            return None, None
        ev = self._visible(st)
        return (sum(1 for e in ev if e["type"] == "goal" and e["side"] == "home"),
                sum(1 for e in ev if e["type"] == "goal" and e["side"] == "away"))

    def _norm(self, st: dict) -> dict:
        sh, sa = self._score(st)
        meta = DEMO_META[st["index"]]
        return {"id": st["id"], "league": st["league"] + " (DEMO)",
                "league_code": f"demo{st['index']}",
                "category": "domestic", "country": meta["country"],
                "flag": meta["flag"], "fd_code": meta["fd_code"],
                "kickoff": datetime.fromtimestamp(st["kick"], tz=timezone.utc).isoformat(),
                "status": st["status"], "minute": st["minute"],
                "live": st["status"] in ("1H", "2H", "HT"),
                "finished": st["status"] == "FT",
                "home": st["home"], "away": st["away"],
                "score_home": sh, "score_away": sa,
                "sources": ["demo"]}

    def live_matches(self) -> list[dict]:
        return [self._norm(s) for s in self._matches_state()
                if s["status"] in ("1H", "2H", "HT")]

    def today_matches(self) -> list[dict]:
        return [self._norm(s) for s in self._matches_state()]

    def match_detail(self, fixture_id: str) -> dict:
        st = next((s for s in self._matches_state() if s["id"] == fixture_id), None)
        if st is None:
            raise KeyError("demo match not found (a new matchday cycle started)")
        info = self._norm(st)
        events = self._visible(st)
        rng = self._rng(st["id"], "lineup")
        lineups = {}
        for side in ("home", "away"):
            formation = str(rng.choice(["4-3-3", "4-2-3-1", "3-5-2", "4-4-2"]))
            lineups[side] = {
                "team": st[side], "formation": formation, "coach": _name(rng),
                "starters": [{"number": i + 1, "name": _name(rng), "pos": POS_ORDER[i]}
                             for i in range(11)],
                "substitutes": [{"number": 12 + i, "name": _name(rng), "pos": "S"}
                                for i in range(7)],
            }
        stats = {"home": {}, "away": {}}
        for side in ("home", "away"):
            ev = [e for e in events if e["side"] == side]
            shots = sum(1 for e in ev if e["type"] in ("shot", "goal"))
            sot = sum(1 for e in ev if e["type"] == "goal" or e["detail"] == "Shot on target")
            stats[side] = {
                "total_shots": shots, "shots_on_goal": sot,
                "corner_kicks": sum(1 for e in ev if e["type"] == "corner"),
                "yellow_cards": sum(1 for e in ev if e["detail"] == "Yellow Card"),
                "red_cards": sum(1 for e in ev if e["detail"] == "Red Card"),
                "fouls": (st["minute"] or 0) // 8,
            }
        tot = (stats["home"]["total_shots"] + stats["away"]["total_shots"]) or 1
        base = 50 + round((stats["home"]["total_shots"] - stats["away"]["total_shots"]) / tot * 18)
        stats["home"]["ball_possession"] = f"{base}%"
        stats["away"]["ball_possession"] = f"{100 - base}%"
        return {"info": info,
                "events": [e for e in events if e["type"] != "corner"],
                "lineups": lineups, "stats": stats}


def _name(rng: np.random.Generator) -> str:
    return f"{FIRST[int(rng.integers(0, len(FIRST)))][0]}. {LAST[int(rng.integers(0, len(LAST)))]}"
