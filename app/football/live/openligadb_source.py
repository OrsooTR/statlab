"""OpenLigaDB adapter (community-maintained open API, no key).

Covers German competitions (Bundesliga, 2. Bundesliga, DFB-Pokal) with current
matchday fixtures, live-updated scores and goal lists. No lineups/shot stats —
in the aggregator it serves as a corroborating source that gets merged with
richer feeds for the same fixture.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Optional

import httpx

from .provider import _cached

BASE = "https://api.openligadb.de"
TIMEOUT = httpx.Timeout(12.0)
SOURCES_PATH = Path(__file__).with_name("sources.json")


def _load_leagues() -> list[dict]:
    with open(SOURCES_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    for s in cfg["sources"]:
        if s["id"] == "openligadb":
            return s.get("leagues", [])
    return []


class OpenLigaDBSource:
    name = "openligadb"
    is_demo = False

    def __init__(self) -> None:
        self.leagues = _load_leagues()

    def status(self) -> dict:
        return {"provider": self.name, "demo": False,
                "leagues": len(self.leagues), "auth": "none"}

    def _matchday(self, code: str) -> list[dict]:
        def fetch():
            r = httpx.get(f"{BASE}/getmatchdata/{code}", timeout=TIMEOUT)
            r.raise_for_status()
            return r.json()
        try:
            return _cached(f"oldb:{code}", 90, fetch)
        except Exception:
            return []

    def all_matches(self) -> list[dict]:
        out = []
        for lg in self.leagues:
            for m in self._matchday(lg["code"]) or []:
                n = self._norm(m, lg)
                if n:
                    out.append(n)
        return out

    def live_matches(self) -> list[dict]:
        return [m for m in self.all_matches() if m["live"]]

    def today_matches(self) -> list[dict]:
        today = date.today().isoformat()
        return [m for m in self.all_matches() if m["kickoff"][:10] == today or m["live"]]

    def match_detail(self, fixture_id: str) -> dict:
        _, code, mid = fixture_id.split(":", 2)
        match = next((m for m in self._matchday(code) or []
                      if str(m.get("matchID")) == mid), None)
        if match is None:
            raise KeyError("match not found in current OpenLigaDB matchday")
        lg = next((l for l in self.leagues if l["code"] == code), {"name": code})
        info = self._norm(match, lg)
        events = []
        for g in match.get("goals", []) or []:
            side = "home" if (g.get("scoreTeam1", 0) or 0) > (g.get("scoreTeam2", 0) or 0) else "away"
            # side inference: compare running score before/after is unreliable;
            # OpenLigaDB marks the scorer's team only via score progression, so
            # attribute by which score increased.
            events.append({"minute": g.get("matchMinute") or 0, "extra": None,
                           "side": side, "type": "goal",
                           "detail": "Own Goal" if g.get("isOwnGoal") else
                                     ("Penalty" if g.get("isPenalty") else "Goal"),
                           "player": g.get("goalGetterName", "") or "", "assist": ""})
        # fix attribution by score progression
        prev_h = prev_a = 0
        for g, e in zip(match.get("goals", []) or [], events):
            h, a = g.get("scoreTeam1") or 0, g.get("scoreTeam2") or 0
            e["side"] = "home" if h > prev_h else "away"
            prev_h, prev_a = h, a
        events.sort(key=lambda e: e["minute"])
        return {"info": info, "events": events, "lineups": None,
                "stats": {"home": {}, "away": {}}}

    def _norm(self, m: dict, lg: dict) -> Optional[dict]:
        t1 = (m.get("team1") or {}).get("teamName")
        t2 = (m.get("team2") or {}).get("teamName")
        if not t1 or not t2:
            return None
        results = m.get("matchResults") or []
        final = next((r for r in results if r.get("resultTypeID") == 2), None)
        half = next((r for r in results if r.get("resultTypeID") == 1), None)
        finished = bool(m.get("matchIsFinished"))
        goals = m.get("goals") or []
        started = bool(results or goals)
        score = final or half
        live = started and not finished
        return {
            "id": f"oldb:{lg['code']}:{m.get('matchID')}",
            "league": lg.get("name", lg["code"]),
            "league_code": lg["code"],
            "category": lg.get("category", "domestic"),
            "country": lg.get("country", "Germany"),
            "flag": lg.get("flag", "🇩🇪"),
            "fd_code": lg.get("fd_code"),
            "kickoff": (m.get("matchDateTimeUTC") or m.get("matchDateTime") or "")[:19],
            "status": "FT" if finished else ("LIVE" if live else "NS"),
            "minute": (max((g.get("matchMinute") or 0) for g in goals) if goals else 1) if live else (90 if finished else None),
            "live": live,
            "finished": finished,
            "home": t1, "away": t2,
            "score_home": (score or {}).get("pointsTeam1") if (started or finished) else None,
            "score_away": (score or {}).get("pointsTeam2") if (started or finished) else None,
            "sources": ["openligadb"],
        }
