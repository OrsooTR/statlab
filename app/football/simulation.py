"""Monte Carlo match simulator.

Scorelines are sampled from the Dixon-Coles-adjusted score grid (the exact
model distribution), so the simulation converges to the analytic probabilities
while providing full distributions for every derived market. Corners and cards
are simulated from negative-binomial / Poisson models parameterised by the
teams' rolling rates.
"""
from __future__ import annotations

import numpy as np

DEFAULT_SIMS = 10_000


def simulate_match(score_matrix: np.ndarray, n_sims: int = DEFAULT_SIMS,
                   corner_rates: tuple[float, float] | None = None,
                   card_rates: tuple[float, float] | None = None,
                   seed: int | None = None) -> dict:
    rng = np.random.default_rng(seed)
    size = score_matrix.shape[0]
    flat = score_matrix.ravel()
    flat = flat / flat.sum()
    draws = rng.choice(len(flat), size=n_sims, p=flat)
    gh = draws // size
    ga = draws % size
    total = gh + ga

    home_wins = float((gh > ga).mean())
    draws_p = float((gh == ga).mean())
    away_wins = float((gh < ga).mean())

    over_under = {}
    for line in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5):
        over_under[str(line)] = {"over": float((total > line).mean()),
                                 "under": float((total < line).mean())}

    goal_dist_home = np.bincount(gh, minlength=7)[:7] / n_sims
    goal_dist_away = np.bincount(ga, minlength=7)[:7] / n_sims
    goal_dist_total = np.bincount(total, minlength=9)[:9] / n_sims

    # top scorelines straight from the analytic grid (no MC noise)
    idx = np.argsort(flat)[::-1][:8]
    top_scores = [{"score": f"{i // size}-{i % size}", "probability": float(flat[i])}
                  for i in idx]

    out = {
        "n_sims": n_sims,
        "p_home": home_wins, "p_draw": draws_p, "p_away": away_wins,
        "expected_goals_home": float(gh.mean()),
        "expected_goals_away": float(ga.mean()),
        "btts": float(((gh > 0) & (ga > 0)).mean()),
        "clean_sheet_home": float((ga == 0).mean()),
        "clean_sheet_away": float((gh == 0).mean()),
        "over_under": over_under,
        "goal_distribution": {
            "home": goal_dist_home.round(5).tolist(),
            "away": goal_dist_away.round(5).tolist(),
            "total": goal_dist_total.round(5).tolist(),
        },
        "top_scorelines": top_scores,
    }

    if corner_rates is not None:
        ch, ca = max(0.5, corner_rates[0]), max(0.5, corner_rates[1])
        total_c = _neg_binomial(rng, ch, n_sims) + _neg_binomial(rng, ca, n_sims)
        out["corners"] = {
            "expected_total": float(total_c.mean()),
            "over_8_5": float((total_c > 8.5).mean()),
            "over_9_5": float((total_c > 9.5).mean()),
            "over_10_5": float((total_c > 10.5).mean()),
        }
    if card_rates is not None:
        kh, ka = max(0.2, card_rates[0]), max(0.2, card_rates[1])
        total_k = rng.poisson(kh, n_sims) + rng.poisson(ka, n_sims)
        out["cards"] = {
            "expected_total": float(total_k.mean()),
            "over_2_5": float((total_k > 2.5).mean()),
            "over_3_5": float((total_k > 3.5).mean()),
            "over_4_5": float((total_k > 4.5).mean()),
        }
    return out


def _neg_binomial(rng: np.random.Generator, mean: float, n: int, dispersion: float = 9.0) -> np.ndarray:
    """Negative binomial with target mean and moderate over-dispersion."""
    r = dispersion
    p = r / (r + mean)
    return rng.negative_binomial(r, p, n)
