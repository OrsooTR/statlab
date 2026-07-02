"""Player-market model for national-team matches.

Computed from the open goalscorer dataset (real international goals, penalties and
minutes per player):

  * Anytime / 2+ / first goalscorer — a player's share of his team's goals over a
    recent window scales the team's model-expected goals into a personal Poisson
    rate: lambda_p = mu_team * share; P(anytime) = 1 - exp(-lambda_p).
  * First goalscorer — Poisson-race approximation:
    P(first) ≈ (lambda_p / Lambda) * P(at least one goal in the match).
  * Penalty goal — the player's international penalty goals per match.
  * To be booked — a POSITIONAL ESTIMATE (no per-player international card log
    exists without a paid feed): a position base rate times a mild team factor.
    Clearly flagged as an estimate in the output.

Every scoring/penalty figure is real and computed; card markets are labelled
estimates. Player names from the live lineup are matched to the dataset by
accent-folded full-name / surname within the same nation.
"""
from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from . import data as intl_data

WINDOW_YEARS = 4
PEN_CONVERSION = 0.78
# positional yellow-card base rates per match (league-typical priors)
CARD_BASE = {"G": 0.05, "D": 0.16, "M": 0.14, "F": 0.09, "S": 0.10, "": 0.11}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def _cutoff() -> str:
    return (date.today() - timedelta(days=int(WINDOW_YEARS * 365.25))).isoformat()


class TeamScoring:
    """Recent-window scoring profile for one national team."""

    def __init__(self, team: str) -> None:
        self.team = team
        cutoff = _cutoff()
        goals = [g for g in intl_data.load_goals()
                 if g["team"] == team and g["date"] >= cutoff and not g["own_goal"]]
        results = [m for m in intl_data.load_results()
                   if (m["home"] == team or m["away"] == team) and m["date"] >= cutoff]
        self.matches = max(1, len(results))
        self.total_goals = len(goals)
        self.by_scorer: dict[str, dict] = defaultdict(
            lambda: {"goals": 0, "pens": 0, "minutes": []})
        for g in goals:
            rec = self.by_scorer[g["scorer"]]
            rec["goals"] += 1
            if g["penalty"]:
                rec["pens"] += 1
            if g["minute"]:
                rec["minutes"].append(g["minute"])
        self._folded = {_fold(name): name for name in self.by_scorer}
        self.team_pens = sum(r["pens"] for r in self.by_scorer.values())

    def match_scorer(self, feed_name: str) -> Optional[str]:
        f = _fold(feed_name)
        if f in self._folded:
            return self._folded[f]
        # surname match within the team
        surname = f.split()[-1] if f else ""
        cands = [orig for fold, orig in self._folded.items()
                 if surname and fold.split()[-1] == surname]
        if len(cands) == 1:
            return cands[0]
        # token-overlap fallback
        want = set(f.split())
        best, best_ov = None, 0
        for fold, orig in self._folded.items():
            ov = len(want & set(fold.split()))
            if ov > best_ov:
                best, best_ov = orig, ov
        return best if best_ov >= 1 else None

    def profile(self, name: str) -> dict:
        rec = self.by_scorer.get(name, {"goals": 0, "pens": 0, "minutes": []})
        share = rec["goals"] / self.total_goals if self.total_goals else 0.0
        return {"goals": rec["goals"], "penalties": rec["pens"],
                "goal_share": round(share, 4),
                "goals_per_match": round(rec["goals"] / self.matches, 3),
                "avg_minute": round(sum(rec["minutes"]) / len(rec["minutes"]), 1)
                if rec["minutes"] else None,
                "window_years": WINDOW_YEARS}


def _poisson_anytime(lmbda: float) -> float:
    import math
    return 1.0 - math.exp(-max(lmbda, 0.0))


def _poisson_two_plus(lmbda: float) -> float:
    import math
    return 1.0 - math.exp(-lmbda) * (1.0 + lmbda)


def player_markets(home: str, away: str, mu_home: float, mu_away: float,
                   lineup_home: list[dict], lineup_away: list[dict]) -> dict:
    """Full per-player market block for both sides."""
    total_rate = max(1e-6, mu_home + mu_away)
    p_any_goal = 1.0 - _poisson_zero(mu_home + mu_away)

    def side(team: str, mu_team: float, lineup: list[dict]) -> list[dict]:
        ts = TeamScoring(team)
        out = []
        for pl in lineup:
            name = pl.get("name") or ""
            pos = (pl.get("pos") or "").upper()[:1]
            matched = ts.match_scorer(name)
            prof = ts.profile(matched) if matched else {
                "goals": 0, "penalties": 0, "goal_share": 0.0,
                "goals_per_match": 0.0, "avg_minute": None, "window_years": WINDOW_YEARS}
            lam = mu_team * prof["goal_share"]
            anytime = _poisson_anytime(lam)
            two_plus = _poisson_two_plus(lam)
            first = (lam / total_rate) * p_any_goal
            pen_per_match = (ts.by_scorer.get(matched, {}).get("pens", 0) / ts.matches
                             if matched else 0.0)
            pen_goal = min(0.25, pen_per_match)
            card_est = CARD_BASE.get(pos, CARD_BASE[""])
            out.append({
                "name": name, "position": pos, "matched": matched,
                "analysis": prof,
                "markets": {
                    "anytime_scorer": round(anytime, 4),
                    "two_plus_scorer": round(two_plus, 4),
                    "first_scorer": round(first, 4),
                    "penalty_goal": round(pen_goal, 4),
                    "to_be_booked_est": round(card_est, 4),
                },
            })
        # rank by anytime scorer probability
        out.sort(key=lambda p: p["markets"]["anytime_scorer"], reverse=True)
        return out

    return {
        "home": {"team": home, "players": side(home, mu_home, lineup_home)},
        "away": {"team": away, "players": side(away, mu_away, lineup_away)},
        "notes": {
            "scoring_markets": "computed from real international goals (last "
                               f"{WINDOW_YEARS} years): a player's share of his team's "
                               "goals scales the model-expected team goals into a "
                               "personal Poisson rate.",
            "card_markets": "POSITIONAL ESTIMATE only — no per-player international "
                            "card log is available without a paid feed.",
        },
    }


def _poisson_zero(lmbda: float) -> float:
    import math
    return math.exp(-max(lmbda, 0.0))
