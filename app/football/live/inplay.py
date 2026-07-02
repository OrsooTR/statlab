"""In-play (live) probability model.

Standard in-play Poisson framework:
  * pre-match expected goals (from the league engine when both teams exist in
    the historical store, otherwise league-average priors);
  * live re-estimation: the pre-match rate is blended with the observed live
    shot-quality rate (xG proxy per minute projected to 90'), with the blend
    weight shifting toward observed play as the match progresses;
  * red cards scale the short-handed team's rate down and the opponent's up
    (literature-standard ~0.72 / 1.10 factors);
  * remaining-goals Poisson grid on the time left (with stoppage allowance)
    combined with the current score → full outcome distribution.

Outputs: live 1X2 probabilities, expected final score, next-goal probabilities,
live over/under lines and the most likely final scorelines.
"""
from __future__ import annotations

from typing import Optional

import numpy as np

from ..features import XG_OTHER_SHOT, XG_SOT

MATCH_LENGTH = 93.0        # effective minutes incl. average stoppage
MAX_REMAINING_GOALS = 8
RED_CARD_SELF = 0.72
RED_CARD_OPP = 1.10


def _prematch_mus(league_hint: Optional[str], home: str, away: str) -> tuple[float, float]:
    """League-engine expected goals when available, else league-average priors."""
    try:
        from ..predict import _engines
        for eng in _engines.values():
            dc = eng.models.get("dixon_coles")
            if dc and dc.fitted and home in dc.base.attack and away in dc.base.attack:
                return dc.expected_goals(home, away)
    except Exception:
        pass
    return 1.45, 1.15


def _live_xg_rate(stats: dict, minute: float) -> Optional[tuple[float, float]]:
    """xG proxy accumulated so far per side, from live shot statistics."""
    if not stats or minute < 10:
        return None
    out = []
    for side in ("home", "away"):
        s = stats.get(side, {})
        shots = _num(s.get("total_shots"))
        sot = _num(s.get("shots_on_goal"))
        if shots is None and sot is None:
            return None
        shots = shots or 0
        sot = sot if sot is not None else round(shots * 0.35)
        out.append(XG_SOT * sot + XG_OTHER_SHOT * max(0, shots - sot))
    return out[0], out[1]


def _num(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(str(v).replace("%", ""))
    except ValueError:
        return None


def predict_inplay(info: dict, stats: dict, events: list[dict],
                   league_hint: Optional[str] = None) -> dict:
    minute = float(info.get("minute") or 0)
    sh = int(info.get("score_home") or 0)
    sa = int(info.get("score_away") or 0)
    mu_h0, mu_a0 = _prematch_mus(league_hint, info["home"], info["away"])

    # blend pre-match rates with observed live shot quality
    live = _live_xg_rate(stats, minute)
    if live is not None and minute > 0:
        proj_h = live[0] / minute * 90.0
        proj_a = live[1] / minute * 90.0
        w_live = min(0.45, minute / 90.0 * 0.55)
        mu_h = (1 - w_live) * mu_h0 + w_live * proj_h
        mu_a = (1 - w_live) * mu_a0 + w_live * proj_a
    else:
        mu_h, mu_a = mu_h0, mu_a0

    # red cards
    reds_h = sum(1 for e in events if e["type"] == "card" and "Red" in e.get("detail", "")
                 and e["side"] == "home")
    reds_a = sum(1 for e in events if e["type"] == "card" and "Red" in e.get("detail", "")
                 and e["side"] == "away")
    mu_h *= RED_CARD_SELF ** reds_h * RED_CARD_OPP ** reds_a
    mu_a *= RED_CARD_SELF ** reds_a * RED_CARD_OPP ** reds_h

    # remaining time
    status = info.get("status", "")
    finished = info.get("finished") or status in ("FT", "AET", "PEN")
    frac = 0.0 if finished else max(0.0, (MATCH_LENGTH - minute) / MATCH_LENGTH)
    lam_h = max(1e-6, mu_h * frac)
    lam_a = max(1e-6, mu_a * frac)

    # remaining-goal grid
    k = np.arange(MAX_REMAINING_GOALS + 1)
    fact = np.cumprod(np.concatenate(([1.0], np.arange(1, MAX_REMAINING_GOALS + 1))))
    ph = np.exp(-lam_h) * lam_h ** k / fact
    pa = np.exp(-lam_a) * lam_a ** k / fact
    grid = np.outer(ph, pa)
    grid /= grid.sum()

    p_home = p_draw = p_away = 0.0
    score_probs: list[tuple[str, float]] = []
    for i in range(len(k)):
        for j in range(len(k)):
            fh, fa = sh + i, sa + j
            p = float(grid[i, j])
            if fh > fa:
                p_home += p
            elif fh == fa:
                p_draw += p
            else:
                p_away += p
            score_probs.append((f"{fh}-{fa}", p))
    score_probs.sort(key=lambda x: x[1], reverse=True)

    total_lam = lam_h + lam_a
    p_no_goal = float(np.exp(-total_lam))
    exp_total = sh + sa + total_lam

    ou = {}
    for line in (1.5, 2.5, 3.5, 4.5):
        p_over = 0.0
        for i in range(len(k)):
            for j in range(len(k)):
                if sh + sa + i + j > line:
                    p_over += float(grid[i, j])
        ou[str(line)] = round(p_over, 4)

    return {
        "minute": minute,
        "score": f"{sh}-{sa}",
        "probabilities": {"home": round(p_home, 4), "draw": round(p_draw, 4),
                          "away": round(p_away, 4)},
        "expected_final": {"home": round(sh + lam_h, 2), "away": round(sa + lam_a, 2)},
        "expected_total_goals": round(exp_total, 2),
        "next_goal": {
            "home": round(float(lam_h / total_lam * (1 - p_no_goal)), 4) if total_lam > 0 else 0,
            "away": round(float(lam_a / total_lam * (1 - p_no_goal)), 4) if total_lam > 0 else 0,
            "none": round(p_no_goal, 4),
        },
        "over_probabilities": ou,
        "top_scorelines": [{"score": s, "probability": round(p, 4)}
                           for s, p in score_probs[:6]],
        "model_inputs": {
            "prematch_mu": [round(mu_h0, 2), round(mu_a0, 2)],
            "live_adjusted_mu": [round(mu_h, 2), round(mu_a, 2)],
            "red_cards": [reds_h, reds_a],
            "remaining_fraction": round(frac, 3),
        },
        "disclaimer": "Live probability estimate from a Poisson in-play model — not a certainty.",
    }


def momentum_series(events: list[dict], minute: float) -> dict:
    """Rolling attacking-pressure index per team for the momentum chart.

    Impact weights: goal 3.0, shot on target 1.6, other shot 0.9, corner 0.6,
    card counts against the carded team (they concede momentum).
    """
    upto = int(minute or 0)
    xs = list(range(0, upto + 1, 3)) or [0]
    series = {"minutes": xs, "home": [], "away": []}
    for side in ("home", "away"):
        vals = []
        for m in xs:
            w = 0.0
            for e in events:
                if e["side"] != side:
                    continue
                dt = m - e["minute"]
                if 0 <= dt <= 9:                       # 9-minute decay window
                    decay = 1.0 - dt / 10.0
                    if e["type"] == "goal":
                        w += 3.0 * decay
                    elif e["type"] == "shot":
                        w += (1.6 if "on target" in e.get("detail", "") else 0.9) * decay
                    elif e["type"] == "corner":
                        w += 0.6 * decay
            vals.append(round(w, 2))
        series[side] = vals
    return series
