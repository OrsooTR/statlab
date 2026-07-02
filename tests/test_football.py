"""Football engine tests on synthetic data (no network required)."""
import math
import random
from datetime import date, timedelta

import numpy as np
import pytest

from app.football.features import FEATURE_NAMES, build_dataset
from app.football.models import DixonColesModel, EloModel, MLModels, PoissonModel, SPIModel
from app.football.models.ensemble import blend, fit_weights
from app.football.simulation import simulate_match
from app.football.slips import build_slips


def synthetic_league(n_seasons: int = 4, seed: int = 5) -> list[dict]:
    """Round-robin league with hidden team strengths → realistic Poisson scores."""
    rng = random.Random(seed)
    nprng = np.random.default_rng(seed)
    teams = [f"Team{i:02d}" for i in range(16)]
    strengths = {t: rng.uniform(0.7, 1.4) for t in teams}
    matches = []
    d = date(2021, 8, 1)
    for season in range(n_seasons):
        code = f"{21 + season}{22 + season}"
        pairs = [(h, a) for h in teams for a in teams if h != a]
        rng.shuffle(pairs)
        for i, (h, a) in enumerate(pairs):
            mu_h = 1.45 * strengths[h] / strengths[a]
            mu_a = 1.15 * strengths[a] / strengths[h]
            gh = int(nprng.poisson(mu_h))
            ga = int(nprng.poisson(mu_a))
            md = d + timedelta(days=season * 300 + i)
            matches.append({
                "league": "TEST", "season": code, "date": md.isoformat(),
                "home": h, "away": a, "fthg": gh, "ftag": ga,
                "ftr": "H" if gh > ga else ("D" if gh == ga else "A"),
                "hs": int(nprng.poisson(mu_h * 9)), "as_": int(nprng.poisson(mu_a * 9)),
                "hst": int(nprng.poisson(mu_h * 3.5)), "ast": int(nprng.poisson(mu_a * 3.5)),
                "hc": int(nprng.poisson(5)), "ac": int(nprng.poisson(5)),
                "hy": int(nprng.poisson(1.8)), "ay": int(nprng.poisson(1.8)),
                "hr": 0, "ar": 0,
                "b365h": None, "b365d": None, "b365a": None,
            })
    matches.sort(key=lambda m: m["date"])
    return matches


@pytest.fixture(scope="module")
def league():
    return synthetic_league()


def test_poisson_recovers_strengths(league):
    m = PoissonModel().fit(league)
    assert m.fitted
    p = m.predict("Team00", "Team01")
    total = p["p_home"] + p["p_draw"] + p["p_away"]
    assert abs(total - 1.0) < 1e-6
    assert 1.0 <= m.home_adv <= 1.8


def test_dixon_coles(league):
    m = DixonColesModel().fit(league)
    assert m.fitted
    assert -0.2 <= m.rho <= 0.2
    grid = m.score_matrix("Team02", "Team03")
    assert abs(grid.sum() - 1.0) < 1e-9


def test_elo(league):
    m = EloModel().fit(league)
    assert m.fitted
    p = m.predict("Team00", "Team15")
    assert abs(p["p_home"] + p["p_draw"] + p["p_away"] - 1.0) < 1e-9
    assert 0.05 < p["p_draw"] < 0.45


def test_spi(league):
    m = SPIModel().fit(league)
    assert m.fitted
    p = m.predict("Team04", "Team05")
    assert abs(p["p_home"] + p["p_draw"] + p["p_away"] - 1.0) < 1e-6


def test_features_no_leakage(league):
    X, y, refs, fb = build_dataset(league)
    assert X.shape[1] == len(FEATURE_NAMES)
    assert len(X) == len(y) == len(refs)
    assert len(X) > 500
    assert np.all(np.isfinite(X))


def test_ml_models(league):
    X, y, refs, fb = build_dataset(league)
    ml = MLModels().fit(X, y)
    assert ml.fitted
    probs = ml.predict_proba(X[-1])
    for name, p in probs.items():
        assert abs(p.sum() - 1.0) < 1e-6, name


def test_ensemble_weights_improve_or_match(league):
    X, y, refs, fb = build_dataset(league)
    split = int(len(refs) * 0.8)
    cut = refs[split]["date"]
    train = [m for m in league if m["date"] < cut]
    models = {"poisson": PoissonModel().fit(train), "elo": EloModel().fit(train)}
    stack = []
    for model in models.values():
        P = np.array([[model.predict(m["home"], m["away"])[k]
                       for k in ("p_home", "p_draw", "p_away")] for m in refs[split:]])
        stack.append(P / P.sum(axis=1, keepdims=True))
    w = fit_weights(np.stack(stack), y[split:])
    assert abs(w.sum() - 1.0) < 1e-6

    def ll(P):
        return -np.log(np.clip(P[np.arange(len(P)), y[split:]], 1e-12, 1)).mean()
    blended = w[0] * stack[0] + w[1] * stack[1]
    # the optimised blend must not be materially worse than the best member
    assert ll(blended) <= min(ll(stack[0]), ll(stack[1])) + 1e-3


def test_simulation_markets(league):
    m = DixonColesModel().fit(league)
    grid = m.score_matrix("Team00", "Team01")
    sim = simulate_match(grid, n_sims=20_000, corner_rates=(5.2, 4.8), card_rates=(1.9, 2.1), seed=3)
    assert abs(sim["p_home"] + sim["p_draw"] + sim["p_away"] - 1.0) < 1e-9
    assert 0 < sim["btts"] < 1
    over = sim["over_under"]
    assert over["0.5"]["over"] > over["2.5"]["over"] > over["4.5"]["over"]
    assert sim["corners"]["expected_total"] == pytest.approx(10.0, abs=1.0)
    assert sim["top_scorelines"][0]["probability"] > 0.02


def test_slip_builder():
    candidates = [
        {"match": f"M{i}", "market": "1X2", "selection": "home",
         "probability": 0.5 + i * 0.02, "odds": 2.1 - i * 0.05}
        for i in range(8)
    ]
    slips = build_slips(candidates, 3)
    assert slips
    assert slips[0]["rank"] == 1
    for s in slips:
        assert s["size"] == 3
        prod = math.prod(c["odds"] for c in s["selections"])
        assert s["combined_odds"] == pytest.approx(prod, rel=1e-2)
    evs = [s["expected_value"] for s in slips]
    assert evs == sorted(evs, reverse=True)


def test_slip_builder_rejects_thin_input():
    with pytest.raises(ValueError):
        build_slips([{"match": "A", "selection": "home", "probability": 0.5, "odds": 2.0}], 2)
