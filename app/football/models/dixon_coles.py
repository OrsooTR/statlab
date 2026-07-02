"""Dixon-Coles (1997) bivariate correction on the Poisson model.

Low-scoring outcomes (0-0, 1-0, 0-1, 1-1) are dependence-adjusted by tau(rho);
rho is estimated by maximising the time-decay-weighted log likelihood over a
grid, reusing the fitted Poisson strengths (profile likelihood).
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from .poisson import PoissonModel, _to_date, outcome_probs


def tau_matrix(mu_h: float, mu_a: float, rho: float, max_goals: int) -> np.ndarray:
    """Multiplicative DC adjustment over the score grid."""
    t = np.ones((max_goals + 1, max_goals + 1))
    t[0, 0] = 1 - mu_h * mu_a * rho
    t[0, 1] = 1 + mu_h * rho
    t[1, 0] = 1 + mu_a * rho
    t[1, 1] = 1 - rho
    return np.clip(t, 1e-6, None)


class DixonColesModel:
    name = "dixon_coles"

    def __init__(self, decay_xi: float = 1.0) -> None:
        self.base = PoissonModel(decay_xi=decay_xi)
        self.rho = -0.05
        self.fitted = False

    def fit(self, matches: list[dict], as_of=None) -> "DixonColesModel":
        self.base.fit(matches, as_of=as_of)
        if not self.base.fitted:
            self.fitted = False
            return self
        played = [m for m in matches if m.get("fthg") is not None]
        ref = as_of or max(_to_date(m["date"]) for m in played)
        # profile-likelihood grid search over rho on low-score matches
        rows = []
        for m in played:
            gh, ga = int(m["fthg"]), int(m["ftag"])
            if gh > 1 or ga > 1:
                continue
            mu_h, mu_a = self.base.expected_goals(m["home"], m["away"])
            w = math.exp(-self.base.decay_xi * (ref - _to_date(m["date"])).days / 365.25)
            rows.append((gh, ga, mu_h, mu_a, w))
        if not rows:
            self.fitted = True
            return self
        best_rho, best_ll = 0.0, -np.inf
        for rho in np.linspace(-0.2, 0.2, 81):
            ll = 0.0
            ok = True
            for gh, ga, mu_h, mu_a, w in rows:
                if gh == 0 and ga == 0:
                    t = 1 - mu_h * mu_a * rho
                elif gh == 0 and ga == 1:
                    t = 1 + mu_h * rho
                elif gh == 1 and ga == 0:
                    t = 1 + mu_a * rho
                else:
                    t = 1 - rho
                if t <= 0:
                    ok = False
                    break
                ll += w * math.log(t)
            if ok and ll > best_ll:
                best_ll, best_rho = ll, float(rho)
        self.rho = best_rho
        self.fitted = True
        return self

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        return self.base.expected_goals(home, away)

    def score_matrix(self, home: str, away: str, max_goals: int = 10) -> np.ndarray:
        from .poisson import independent_score_matrix
        mu_h, mu_a = self.base.expected_goals(home, away)
        mat = independent_score_matrix(mu_h, mu_a, max_goals)
        mat = mat * tau_matrix(mu_h, mu_a, self.rho, max_goals)
        return mat / mat.sum()

    def predict(self, home: str, away: str) -> dict:
        mat = self.score_matrix(home, away)
        ph, pd_, pa = outcome_probs(mat)
        mu_h, mu_a = self.base.expected_goals(home, away)
        return {"p_home": ph, "p_draw": pd_, "p_away": pa,
                "mu_home": mu_h, "mu_away": mu_a}
