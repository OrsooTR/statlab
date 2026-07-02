"""Goal-margin Elo rating model (Hvattum & Arntzen, 2010 form).

Sequential rating updates with a logarithmic goal-margin multiplier and fixed
home advantage. 1X2 probabilities use the ordinal-logistic construction with a
draw band delta calibrated so the model's average draw probability matches the
league's observed draw rate.
"""
from __future__ import annotations

import math

K = 22.0
HOME_ADV = 62.0
INIT = 1500.0


class EloModel:
    name = "elo"

    def __init__(self) -> None:
        self.ratings: dict[str, float] = {}
        self.delta = 90.0  # draw half-band in rating points
        self.fitted = False

    def _r(self, team: str) -> float:
        return self.ratings.get(team, INIT)

    def fit(self, matches: list[dict], as_of=None) -> "EloModel":
        self.ratings = {}
        draws, total = 0, 0
        for m in matches:
            if m.get("fthg") is None:
                continue
            h, a = m["home"], m["away"]
            rh, ra = self._r(h), self._r(a)
            exp_home = 1.0 / (1.0 + 10 ** (-(rh + HOME_ADV - ra) / 400.0))
            gh, ga = int(m["fthg"]), int(m["ftag"])
            score = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
            margin = abs(gh - ga)
            g_mult = math.log(max(margin, 1) + 1.0)
            change = K * g_mult * (score - exp_home)
            self.ratings[h] = rh + change
            self.ratings[a] = ra - change
            total += 1
            draws += 1 if gh == ga else 0
        if total < 50:
            self.fitted = False
            return self
        # calibrate the draw band to the observed draw rate by bisection
        target = draws / total
        lo, hi = 10.0, 300.0
        for _ in range(40):
            mid = (lo + hi) / 2
            if self._avg_draw_prob(matches, mid) > target:
                hi = mid
            else:
                lo = mid
        self.delta = (lo + hi) / 2
        self.fitted = True
        return self

    def _probs(self, diff: float, delta: float) -> tuple[float, float, float]:
        p_home = 1.0 / (1.0 + 10 ** (-(diff - delta) / 400.0))
        p_home_or_draw = 1.0 / (1.0 + 10 ** (-(diff + delta) / 400.0))
        return p_home, max(1e-6, p_home_or_draw - p_home), 1.0 - p_home_or_draw

    def _avg_draw_prob(self, matches: list[dict], delta: float) -> float:
        # evaluate on final ratings over a sample of pairings — cheap and stable
        total, n = 0.0, 0
        played = [m for m in matches if m.get("fthg") is not None][-500:]
        for m in played:
            diff = self._r(m["home"]) + HOME_ADV - self._r(m["away"])
            total += self._probs(diff, delta)[1]
            n += 1
        return total / max(n, 1)

    def predict(self, home: str, away: str) -> dict:
        diff = self._r(home) + HOME_ADV - self._r(away)
        ph, pd_, pa = self._probs(diff, self.delta)
        return {"p_home": ph, "p_draw": pd_, "p_away": pa,
                "rating_home": self._r(home), "rating_away": self._r(away)}
