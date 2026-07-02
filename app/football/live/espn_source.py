"""ESPN public JSON feed adapter.

ESPN exposes unauthenticated JSON endpoints per competition:
  scoreboard: site.api.espn.com/apis/site/v2/sports/soccer/{league}/scoreboard
  summary:    .../summary?event={id}   (boxscore stats, rosters, key events)

No API key, no fixed quota — but we still cache aggressively (60 s live /
10 min pre-match) and fetch competitions concurrently so a refresh stays fast.
Match ids are namespaced "espn:{league}:{event_id}" so the aggregator can route
detail requests back here.
"""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Optional

import httpx

from .provider import _cached

BASE = "https://site.api.espn.com/apis/site/v2/sports/soccer"
HEADERS = {"User-Agent": "Mozilla/5.0 (StatLab local desktop analytics)"}
TIMEOUT = httpx.Timeout(12.0)

SOURCES_PATH = Path(__file__).with_name("sources.json")

STAT_MAP = {
    "possessionPct": "ball_possession",
    "totalShots": "total_shots",
    "shotsOnTarget": "shots_on_goal",
    "wonCorners": "corner_kicks",
    "foulsCommitted": "fouls",
    "yellowCards": "yellow_cards",
    "redCards": "red_cards",
    "offsides": "offsides",
    "saves": "saves",
}


def _load_leagues() -> list[dict]:
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for s in cfg["sources"]:
        if s["id"] == "espn":
            return s.get("leagues", [])
    return []


class ESPNSource:
    name = "espn"
    is_demo = False

    def __init__(self) -> None:
        self.leagues = _load_leagues()

    def status(self) -> dict:
        return {"provider": self.name, "demo": False,
                "leagues": len(self.leagues), "auth": "none"}

    # ------------------------------------------------------------------ lists
    def _scoreboard(self, code: str) -> list[dict]:
        def fetch():
            r = httpx.get(f"{BASE}/{code}/scoreboard", headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json().get("events", [])
        try:
            return _cached(f"espn:sb:{code}", 60, fetch)
        except Exception:
            return []  # one broken competition never kills the whole refresh

    def all_matches(self) -> list[dict]:
        out: list[dict] = []
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._scoreboard, lg["code"]): lg for lg in self.leagues}
            for fut in as_completed(futures):
                lg = futures[fut]
                for ev in fut.result() or []:
                    m = self._norm_event(ev, lg)
                    if m:
                        out.append(m)
        return out

    def live_matches(self) -> list[dict]:
        return [m for m in self.all_matches() if m["live"]]

    def today_matches(self) -> list[dict]:
        return self.all_matches()

    # ----------------------------------------------------------------- detail
    def match_detail(self, fixture_id: str) -> dict:
        _, code, event_id = fixture_id.split(":", 2)

        def fetch():
            r = httpx.get(f"{BASE}/{code}/summary", params={"event": event_id},
                          headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()

        data = _cached(f"espn:sum:{code}:{event_id}", 45, fetch)
        header = data.get("header", {})
        comp = (header.get("competitions") or [{}])[0]
        lg = next((l for l in self.leagues if l["code"] == code), {"name": code})
        info = self._norm_competition(comp, header, lg, event_id)
        side_by_team = {}
        for c in comp.get("competitors", []):
            side_by_team[str(c.get("id"))] = c.get("homeAway", "home")
        return {
            "info": info,
            "events": self._events(data, side_by_team),
            "lineups": self._lineups(data),
            "stats": self._stats(data),
        }

    # ------------------------------------------------------------ normalisers
    def _norm_event(self, ev: dict, lg: dict) -> Optional[dict]:
        comp = (ev.get("competitions") or [{}])[0]
        header = {"competitions": [comp]}
        try:
            return self._norm_competition(comp, {"id": ev.get("id"), **header},
                                          lg, str(ev.get("id")))
        except Exception:
            return None

    def _norm_competition(self, comp: dict, header: dict, lg: dict, event_id: str) -> dict:
        competitors = comp.get("competitors", [])
        home = next((c for c in competitors if c.get("homeAway") == "home"), {})
        away = next((c for c in competitors if c.get("homeAway") == "away"), {})
        status = comp.get("status") or header.get("status") or {}
        stype = status.get("type", {})
        state = stype.get("state", "pre")           # pre | in | post
        minute = self._minute(status)
        return {
            "id": f"espn:{lg['code']}:{event_id}",
            "league": lg.get("name", lg["code"]),
            "league_code": lg["code"],
            "category": lg.get("category", "domestic"),
            "country": lg.get("country", ""),
            "flag": lg.get("flag", ""),
            "fd_code": lg.get("fd_code"),
            "kickoff": comp.get("date", ""),
            "status": stype.get("shortDetail") or stype.get("description", ""),
            "minute": minute if state == "in" else (90 if state == "post" else None),
            "live": state == "in",
            "finished": state == "post",
            "home": (home.get("team") or {}).get("displayName", "?"),
            "away": (away.get("team") or {}).get("displayName", "?"),
            "score_home": _int(home.get("score")) if state != "pre" else None,
            "score_away": _int(away.get("score")) if state != "pre" else None,
            "sources": ["espn"],
        }

    @staticmethod
    def _minute(status: dict) -> Optional[int]:
        clock = status.get("displayClock") or ""
        m = re.match(r"(\d+)", str(clock))
        if m:
            return int(m.group(1))
        v = status.get("clock")
        if isinstance(v, (int, float)) and v > 0:
            return int(v / 60)
        return None

    def _events(self, data: dict, side_by_team: dict) -> list[dict]:
        out = []
        for e in data.get("keyEvents") or []:
            text = (e.get("type") or {}).get("text", "")
            low = text.lower()
            if "goal" in low:
                etype = "goal"
            elif "card" in low:
                etype = "card"
            elif "substitution" in low:
                etype = "subst"
            elif "shot" in low or "attempt" in low:
                etype = "shot"
            else:
                etype = "info"
            team_id = str((e.get("team") or {}).get("id", ""))
            participants = e.get("participants") or []
            player = ""
            assist = ""
            if participants:
                player = ((participants[0].get("athlete") or {}).get("displayName")) or ""
                if len(participants) > 1:
                    assist = ((participants[1].get("athlete") or {}).get("displayName")) or ""
            minute = 0
            clock = (e.get("clock") or {}).get("displayValue", "")
            m = re.match(r"(\d+)", clock)
            if m:
                minute = int(m.group(1))
            out.append({"minute": minute, "extra": None,
                        "side": side_by_team.get(team_id, "home"),
                        "type": etype, "detail": text,
                        "player": player, "assist": assist})
        out.sort(key=lambda e: e["minute"])
        return out

    def _lineups(self, data: dict) -> Optional[dict]:
        rosters = data.get("rosters") or []
        if len(rosters) < 2:
            return None
        out = {}
        for r in rosters:
            side = r.get("homeAway", "home")
            starters, subs = [], []
            for p in r.get("roster", []):
                item = {"number": _int(p.get("jersey")),
                        "name": (p.get("athlete") or {}).get("displayName", ""),
                        "pos": (p.get("position") or {}).get("abbreviation", "")}
                (starters if p.get("starter") else subs).append(item)
            if not starters and not subs:
                return None
            out[side] = {"team": (r.get("team") or {}).get("displayName", ""),
                         "formation": r.get("formation", "") or "",
                         "coach": "", "starters": starters, "substitutes": subs}
        return out if "home" in out and "away" in out else None

    def _stats(self, data: dict) -> dict:
        out = {"home": {}, "away": {}}
        teams = (data.get("boxscore") or {}).get("teams") or []
        comp = ((data.get("header") or {}).get("competitions") or [{}])[0]
        side_by_team = {str((c.get("team") or c).get("id")): c.get("homeAway", "home")
                        for c in comp.get("competitors", [])}
        for t in teams:
            side = side_by_team.get(str((t.get("team") or {}).get("id", "")), "home")
            for s in t.get("statistics", []):
                key = STAT_MAP.get(s.get("name", ""))
                if key:
                    out[side][key] = s.get("displayValue")
        return out


def _int(v: Any) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
