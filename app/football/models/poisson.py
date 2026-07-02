"""Time-decayed maximum-likelihood Poisson model (Maher, 1982).

Each team gets multiplicative attack and defence strengths; a global home
advantage multiplies home expected goals:
    mu_home = league_mean * attack_h * defence_a * home_adv
    mu_away = league_mean * attack_a * defence_h
Strengths are fitted by iterative proportional fitting on exponentially
time-decayed match weights (half-life ≈ 8 months), which is the closed-form
coordinate ascent for the Poisson likelihood.
"""
from __future__ import annotations

import math
from datetime import date, datetime
from typing import Optional

import numpy as np

DECAY_XI = 1.0  # per-year decay rate; w = exp(-xi * years_ago)
MAX_GOALS = 10


def _to_date(s) -> date:
    if isinstance(s, date):
        return s
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


class PoissonModel:
    name = "poisson"

    def __init__(self, decay_xi: float = DECAY_XI) -> None:
        self.decay_xi = decay_xi
        self.attack: dict[str, float] = {}
        self.defence: dict[str, float] = {}
        self.home_adv = 1.25
        self.league_mean = 1.35
        self.fitted = False

    def fit(self, matches: list[dict], as_of: Optional[date] = None) -> "PoissonModel":
        played = [m for m in matches if m.get("fthg") is not None]
        if len(played) < 50:
            self.fitted = False
            return self
        ref = as_of or max(_to_date(m["date"]) for m in played)
        teams = sorted({m["home"] for m in played} | {m["away"] for m in played})
        t_idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        h = np.array([t_idx[m["home"]] for m in played])
        a = np.array([t_idx[m["away"]] for m in played])
        gh = np.array([m["fthg"] for m in played], dtype=np.float64)
        ga = np.array([m["ftag"] for m in played], dtype=np.float64)
        w = np.array([math.exp(-self.decay_xi * (ref - _to_date(m["date"])).days / 365.25)
                      for m in played])

        atk = np.ones(n)
        dfn = np.ones(n)
        home_adv = 1.25
        mean = float((w * (gh + ga)).sum() / (2 * w.sum()))
        for _ in range(25):
            mu_h = mean * atk[h] * dfn[a] * home_adv
            mu_a = mean * atk[a] * dfn[h]
            # attack updates: scored totals vs expected
            num = np.bincount(h, weights=w * gh, minlength=n) + np.bincount(a, weights=w * ga, minlength=n)
            den = np.bincount(h, weights=w * mu_h, minlength=n) + np.bincount(a, weights=w * mu_a, minlength=n)
            atk *= np.where(den > 0, num / np.maximum(den, 1e-9), 1.0)
            atk /= atk.mean()
            mu_h = mean * atk[h] * dfn[a] * home_adv
            mu_a = mean * atk[a] * dfn[h]
            num = np.bincount(a, weights=w * gh, minlength=n) + np.bincount(h, weights=w * ga, minlength=n)
            den = np.bincount(a, weights=w * mu_h, minlength=n) + np.bincount(h, weights=w * mu_a, minlength=n)
            dfn *= np.where(den > 0, num / np.maximum(den, 1e-9), 1.0)
            dfn /= dfn.mean()
            mu_h = mean * atk[h] * dfn[a] * home_adv
            home_adv *= float((w * gh).sum() / max((w * mu_h).sum(), 1e-9))
            home_adv = min(max(home_adv, 1.0), 1.8)
        self.attack = {t: float(atk[i]) for t, i in t_idx.items()}
        self.defence = {t: float(dfn[i]) for t, i in t_idx.items()}
        self.home_adv = float(home_adv)
        self.league_mean = mean
        self.fitted = True
        return self

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        ah = self.attack.get(home, 1.0)
        dh = self.defence.get(home, 1.0)
        aa = self.attack.get(away, 1.0)
        da = self.defence.get(away, 1.0)
        mu_h = self.league_mean * ah * da * self.home_adv
        mu_a = self.league_mean * aa * dh
        return max(0.05, min(mu_h, 6.0)), max(0.05, min(mu_a, 6.0))

    def score_matrix(self, home: str, away: str) -> np.ndarray:
        mu_h, mu_a = self.expected_goals(home, away)
        return independent_score_matrix(mu_h, mu_a)

    def predict(self, home: str, away: str) -> dict:
        mu_h, mu_a = self.expected_goals(home, away)
        mat = independent_score_matrix(mu_h, mu_a)
        ph, pd_, pa = outcome_probs(mat)
        return {"p_home": ph, "p_draw": pd_, "p_away": pa,
                "mu_home": mu_h, "mu_away": mu_a}


def independent_score_matrix(mu_h: float, mu_a: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    gh = np.arange(max_goals + 1)
    ph = np.exp(-mu_h) * mu_h ** gh / _factorials(max_goals)
    pa = np.exp(-mu_a) * mu_a ** gh / _factorials(max_goals)
    mat = np.outer(ph, pa)
    return mat / mat.sum()


def _factorials(n: int) -> np.ndarray:
    out = np.ones(n + 1)
    for i in range(2, n + 1):
        out[i] = out[i - 1] * i
    return out


def outcome_probs(mat: np.ndarray) -> tuple[float, float, float]:
    ph = float(np.tril(mat, -1).sum())   # home goals > away goals
    pd_ = float(np.trace(mat))
    pa = float(np.triu(mat, 1).sum())
    s = ph + pd_ + pa
    return ph / s, pd_ / s, pa / s
