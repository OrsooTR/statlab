"""SPI-like composite rating model (FiveThirtyEight-inspired).

Team offence/defence ratings are fitted exactly like the Poisson model but on
"adjusted goals" — a blend of actual goals and the shot-quality xG proxy —
which makes the model less noisy than raw goals and differentiates it from the
pure Poisson member of the ensemble.
"""
from __future__ import annotations

from typing import Optional

from ..features import XG_OTHER_SHOT, XG_SOT
from .poisson import PoissonModel, independent_score_matrix, outcome_probs

GOAL_BLEND = 0.65  # weight on actual goals; remainder on xG proxy


def _adjusted(m: dict, home: bool) -> Optional[float]:
    goals = m["fthg"] if home else m["ftag"]
    shots = m.get("hs") if home else m.get("as_")
    sot = m.get("hst") if home else m.get("ast")
    if goals is None:
        return None
    if shots is None and sot is None:
        return float(goals)
    s = shots or 0
    t = sot if sot is not None else max(0, round(s * 0.33))
    xg = XG_SOT * t + XG_OTHER_SHOT * max(0, s - t)
    return GOAL_BLEND * goals + (1 - GOAL_BLEND) * xg


class SPIModel:
    name = "spi"

    def __init__(self) -> None:
        self.base = PoissonModel(decay_xi=1.4)  # faster decay: closer to current form
        self.fitted = False

    def fit(self, matches: list[dict], as_of=None) -> "SPIModel":
        adjusted = []
        for m in matches:
            if m.get("fthg") is None:
                continue
            ah = _adjusted(m, True)
            aa = _adjusted(m, False)
            if ah is None or aa is None:
                continue
            mm = dict(m)
            mm["fthg"], mm["ftag"] = ah, aa
            adjusted.append(mm)
        self.base.fit(adjusted, as_of=as_of)
        self.fitted = self.base.fitted
        return self

    def expected_goals(self, home: str, away: str) -> tuple[float, float]:
        return self.base.expected_goals(home, away)

    def predict(self, home: str, away: str) -> dict:
        mu_h, mu_a = self.base.expected_goals(home, away)
        mat = independent_score_matrix(mu_h, mu_a)
        ph, pd_, pa = outcome_probs(mat)
        return {"p_home": ph, "p_draw": pd_, "p_away": pa,
                "mu_home": mu_h, "mu_away": mu_a,
                "off_home": self.base.attack.get(home, 1.0),
                "def_home": self.base.defence.get(home, 1.0),
                "off_away": self.base.attack.get(away, 1.0),
                "def_away": self.base.defence.get(away, 1.0)}
