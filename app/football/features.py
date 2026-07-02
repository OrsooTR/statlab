"""Feature engineering.

A single pass over a league's chronologically-sorted matches maintains rolling
team state (overall/home/away form, goals, shot quality, corners, discipline,
rest, Elo, head-to-head) and emits a leakage-free feature vector for every
match: each vector is computed strictly from information available BEFORE
kick-off. The same builder provides the "as of today" snapshot used when
predicting upcoming fixtures.

The xG proxy follows the standard shot-quality decomposition: shots on target
carry ~0.30 expected goals, other shots ~0.045 — coefficients consistent with
public xG model averages, applied to football-data's shot statistics.
"""
from __future__ import annotations

import math
from collections import defaultdict, deque
from datetime import date, datetime
from typing import Optional

import numpy as np

XG_SOT = 0.30
XG_OTHER_SHOT = 0.045

ELO_K = 22.0
ELO_HOME_ADV = 62.0
ELO_INIT = 1500.0

FEATURE_NAMES = [
    "h_ppg5", "h_ppg10", "a_ppg5", "a_ppg10",
    "h_home_ppg5", "a_away_ppg5",
    "h_gf5", "h_ga5", "a_gf5", "a_ga5",
    "h_xgf5", "h_xga5", "a_xgf5", "a_xga5",
    "h_sot_f5", "h_sot_a5", "a_sot_f5", "a_sot_a5",
    "h_corners5", "a_corners5", "h_cards5", "a_cards5",
    "h_rest_days", "a_rest_days", "rest_diff",
    "h_elo", "a_elo", "elo_diff", "elo_expected",
    "h2h_h_ppg", "h2h_goal_diff", "h2h_matches",
    "h_momentum", "a_momentum",
    "h_attack_ratio", "h_defense_ratio", "a_attack_ratio", "a_defense_ratio",
    "league_draw_rate", "league_home_win_rate", "league_goals_pg",
    "season_progress",
    "odds_ph", "odds_pd", "odds_pa",
]


def _points(gf: int, ga: int) -> int:
    return 3 if gf > ga else (1 if gf == ga else 0)


def _mean(values, default: float = 0.0) -> float:
    vals = [v for v in values if v is not None]
    return sum(vals) / len(vals) if vals else default


class TeamState:
    def __init__(self) -> None:
        self.recent: deque = deque(maxlen=10)   # dicts of one match from team POV
        self.home_recent: deque = deque(maxlen=5)
        self.away_recent: deque = deque(maxlen=5)
        self.last_date: Optional[date] = None
        self.elo: float = ELO_INIT

    def push(self, rec: dict, is_home: bool) -> None:
        self.recent.append(rec)
        (self.home_recent if is_home else self.away_recent).append(rec)
        self.last_date = rec["date"]


class FeatureBuilder:
    """One instance per league. process() every match in date order."""

    def __init__(self) -> None:
        self.teams: dict[str, TeamState] = defaultdict(TeamState)
        self.h2h: dict[tuple[str, str], deque] = defaultdict(lambda: deque(maxlen=10))
        self.league_results: deque = deque(maxlen=380)
        self.league_goals: deque = deque(maxlen=380)
        self.matches_seen = 0

    # -- state update ---------------------------------------------------------
    def process(self, m: dict) -> Optional[np.ndarray]:
        """Emit the pre-match feature vector, then update state with the result."""
        feats = self.features_for(m["home"], m["away"], _to_date(m["date"]),
                                  m.get("b365h"), m.get("b365d"), m.get("b365a"))
        self.update(m)
        return feats

    def update(self, m: dict) -> None:
        if m.get("fthg") is None or m.get("ftag") is None:
            return
        d = _to_date(m["date"])
        hs, as_ = m.get("hs"), m.get("as_")
        hst, ast = m.get("hst"), m.get("ast")
        h_xg = _xg(hs, hst)
        a_xg = _xg(as_, ast)
        home_rec = {"date": d, "gf": m["fthg"], "ga": m["ftag"],
                    "points": _points(m["fthg"], m["ftag"]),
                    "xgf": h_xg, "xga": a_xg,
                    "sot_f": hst, "sot_a": ast,
                    "corners": m.get("hc"), "cards": _cards(m.get("hy"), m.get("hr"))}
        away_rec = {"date": d, "gf": m["ftag"], "ga": m["fthg"],
                    "points": _points(m["ftag"], m["fthg"]),
                    "xgf": a_xg, "xga": h_xg,
                    "sot_f": ast, "sot_a": hst,
                    "corners": m.get("ac"), "cards": _cards(m.get("ay"), m.get("ar"))}
        th = self.teams[m["home"]]
        ta = self.teams[m["away"]]
        # Elo update with goal-margin multiplier (Hvattum & Arntzen form)
        exp_home = 1.0 / (1.0 + 10 ** (-(th.elo + ELO_HOME_ADV - ta.elo) / 400.0))
        score = 1.0 if m["fthg"] > m["ftag"] else (0.5 if m["fthg"] == m["ftag"] else 0.0)
        margin = abs(m["fthg"] - m["ftag"])
        g_mult = math.log(max(margin, 1) + 1.0) * (2.2 / ((th.elo - ta.elo) * 0.001 * (1 if score == 1 else -1) + 2.2))
        delta = ELO_K * g_mult * (score - exp_home)
        th.elo += delta
        ta.elo -= delta
        th.push(home_rec, True)
        ta.push(away_rec, False)
        key = tuple(sorted((m["home"], m["away"])))
        self.h2h[key].append({"home": m["home"], "away": m["away"],
                              "fthg": m["fthg"], "ftag": m["ftag"]})
        self.league_results.append(m["ftr"] if m.get("ftr") else
                                   ("H" if score == 1 else "D" if score == 0.5 else "A"))
        self.league_goals.append(m["fthg"] + m["ftag"])
        self.matches_seen += 1

    # -- feature vector -------------------------------------------------------
    def features_for(self, home: str, away: str, when: date,
                     b365h: Optional[float] = None, b365d: Optional[float] = None,
                     b365a: Optional[float] = None) -> Optional[np.ndarray]:
        th = self.teams.get(home)
        ta = self.teams.get(away)
        if th is None or ta is None or len(th.recent) < 3 or len(ta.recent) < 3:
            return None  # not enough history for a stable vector

        def last(dq, n):
            return list(dq)[-n:]

        league_gpg = _mean(self.league_goals, 2.6)
        avg_gf = max(0.3, league_gpg / 2)

        h5, h10 = last(th.recent, 5), list(th.recent)
        a5, a10 = last(ta.recent, 5), list(ta.recent)
        h_rest = _rest(th.last_date, when)
        a_rest = _rest(ta.last_date, when)
        elo_exp = 1.0 / (1.0 + 10 ** (-(th.elo + ELO_HOME_ADV - ta.elo) / 400.0))

        key = tuple(sorted((home, away)))
        meetings = list(self.h2h.get(key, []))[-5:]
        if meetings:
            pts, gd = 0.0, 0.0
            for mt in meetings:
                gf = mt["fthg"] if mt["home"] == home else mt["ftag"]
                ga = mt["ftag"] if mt["home"] == home else mt["fthg"]
                pts += _points(gf, ga)
                gd += gf - ga
            h2h_ppg, h2h_gd, h2h_n = pts / len(meetings), gd / len(meetings), len(meetings)
        else:
            h2h_ppg, h2h_gd, h2h_n = 1.3, 0.0, 0

        results = list(self.league_results)
        draw_rate = results.count("D") / len(results) if results else 0.25
        home_rate = results.count("H") / len(results) if results else 0.45

        ph, pd_, pa = _implied(b365h, b365d, b365a, elo_exp, draw_rate)

        vec = [
            _mean([r["points"] for r in h5], 1.3), _mean([r["points"] for r in h10], 1.3),
            _mean([r["points"] for r in a5], 1.3), _mean([r["points"] for r in a10], 1.3),
            _mean([r["points"] for r in th.home_recent], 1.5),
            _mean([r["points"] for r in ta.away_recent], 1.1),
            _mean([r["gf"] for r in h5], 1.3), _mean([r["ga"] for r in h5], 1.3),
            _mean([r["gf"] for r in a5], 1.3), _mean([r["ga"] for r in a5], 1.3),
            _mean([r["xgf"] for r in h5], 1.3), _mean([r["xga"] for r in h5], 1.3),
            _mean([r["xgf"] for r in a5], 1.3), _mean([r["xga"] for r in a5], 1.3),
            _mean([r["sot_f"] for r in h5], 4.0), _mean([r["sot_a"] for r in h5], 4.0),
            _mean([r["sot_f"] for r in a5], 4.0), _mean([r["sot_a"] for r in a5], 4.0),
            _mean([r["corners"] for r in h5], 5.0), _mean([r["corners"] for r in a5], 5.0),
            _mean([r["cards"] for r in h5], 1.8), _mean([r["cards"] for r in a5], 1.8),
            h_rest, a_rest, h_rest - a_rest,
            th.elo, ta.elo, th.elo - ta.elo, elo_exp,
            h2h_ppg, h2h_gd, float(h2h_n),
            _momentum(h10), _momentum(a10),
            _mean([r["gf"] for r in h5], avg_gf) / avg_gf,
            _mean([r["ga"] for r in h5], avg_gf) / avg_gf,
            _mean([r["gf"] for r in a5], avg_gf) / avg_gf,
            _mean([r["ga"] for r in a5], avg_gf) / avg_gf,
            draw_rate, home_rate, league_gpg,
            min(1.0, len([r for r in h10 if r["date"] > date(when.year if when.month >= 7 else when.year - 1, 7, 1)]) / 38 * 4),
            ph, pd_, pa,
        ]
        return np.array(vec, dtype=np.float64)


def _xg(shots: Optional[int], sot: Optional[int]) -> Optional[float]:
    if shots is None and sot is None:
        return None
    s = shots or 0
    t = sot if sot is not None else max(0, round(s * 0.33))
    return XG_SOT * t + XG_OTHER_SHOT * max(0, s - t)


def _cards(yellow: Optional[int], red: Optional[int]) -> Optional[float]:
    if yellow is None and red is None:
        return None
    return (yellow or 0) + 2.0 * (red or 0)


def _rest(last: Optional[date], when: date) -> float:
    if last is None:
        return 7.0
    return float(min(14, max(1, (when - last).days)))


def _momentum(recent: list) -> float:
    if len(recent) < 6:
        return 0.0
    last3 = sum(r["points"] for r in recent[-3:]) / 3
    prev3 = sum(r["points"] for r in recent[-6:-3]) / 3
    return last3 - prev3


def _implied(bh, bd, ba, elo_exp: float, draw_rate: float) -> tuple[float, float, float]:
    """Overround-normalised bookmaker probabilities; Elo-based fallback."""
    if bh and bd and ba and bh > 1 and bd > 1 and ba > 1:
        inv = np.array([1 / bh, 1 / bd, 1 / ba])
        inv = inv / inv.sum()
        return float(inv[0]), float(inv[1]), float(inv[2])
    p_draw = max(0.12, min(0.38, draw_rate * (1 - abs(elo_exp - 0.5))))
    ph = elo_exp * (1 - p_draw)
    pa = (1 - elo_exp) * (1 - p_draw)
    return ph, p_draw, pa


def _to_date(s) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def build_dataset(matches: list[dict]) -> tuple[np.ndarray, np.ndarray, list[dict], FeatureBuilder]:
    """Full-league pass → (X, y, per-row match refs, final builder state)."""
    fb = FeatureBuilder()
    X, y, refs = [], [], []
    for m in matches:
        if m.get("fthg") is None:
            continue
        vec = fb.process(m)
        if vec is not None and m.get("ftr") in ("H", "D", "A"):
            X.append(vec)
            y.append({"H": 0, "D": 1, "A": 2}[m["ftr"]])
            refs.append(m)
    return (np.array(X) if X else np.empty((0, len(FEATURE_NAMES)))), np.array(y), refs, fb
