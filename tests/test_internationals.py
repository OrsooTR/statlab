"""Tests for the national-team engine, player markets and nation resolver.

All tests are network-free: the data layer is monkeypatched with small synthetic
datasets so they run deterministically in CI.
"""
import math
import random
from datetime import date, timedelta

import pytest

from app.football.internationals import data as intl_data
from app.football.internationals import players, resolve
from app.football.internationals.engine import NationalEngine


def _synthetic_intl(seed=3):
    rng = random.Random(seed)
    import numpy as np
    nprng = np.random.default_rng(seed)
    nations = ["Spain", "France", "Brazil", "Argentina", "Germany",
               "Japan", "Nigeria", "Wales", "Peru", "Qatar"]
    strength = {t: rng.uniform(0.8, 1.5) for t in nations}
    out = []
    d = date(2019, 1, 1)
    for k in range(1200):
        h, a = rng.sample(nations, 2)
        neutral = rng.random() < 0.4
        mu_h = 1.4 * strength[h] / strength[a] * (1.0 if neutral else 1.25)
        mu_a = 1.2 * strength[a] / strength[h]
        gh, ga = int(nprng.poisson(mu_h)), int(nprng.poisson(mu_a))
        d = d + timedelta(days=3)
        out.append({"date": d.isoformat(), "home": h, "away": a,
                    "fthg": gh, "ftag": ga,
                    "ftr": "H" if gh > ga else ("D" if gh == ga else "A"),
                    "tournament": "Friendly" if rng.random() < 0.5 else "FIFA World Cup",
                    "neutral": neutral})
    return out


@pytest.fixture(scope="module")
def engine():
    return NationalEngine(_synthetic_intl())


def test_engine_fits(engine):
    assert 1.0 <= engine.home_adv <= 1.42
    assert -0.2 <= engine.rho <= 0.1
    assert engine.known("Spain") and engine.known("Brazil")


def test_prediction_sums_to_one(engine):
    p = engine.predict("Spain", "France", neutral=True, n_sims=5000)
    pr = p["probabilities"]
    assert pr["home"] + pr["draw"] + pr["away"] == pytest.approx(1.0, abs=1e-3)
    assert p["expected_goals"]["home"] > 0
    assert 0 < p["markets"]["btts"] < 1


def test_home_advantage_matters(engine):
    neu = engine.predict("Spain", "France", neutral=True, n_sims=8000)["probabilities"]["home"]
    home = engine.predict("Spain", "France", neutral=False, n_sims=8000)["probabilities"]["home"]
    assert home > neu  # playing at home lifts the home win probability


def test_stronger_team_favoured(engine):
    # the team with the higher Elo should be favoured on neutral ground
    a, b = "Spain", "Qatar"
    p = engine.predict(a, b, neutral=True, n_sims=8000)
    if engine.elo_of(a) > engine.elo_of(b):
        assert p["probabilities"]["home"] > p["probabilities"]["away"]


# ------------------------------------------------------------------- players
def _patch_player_data(monkeypatch):
    today = date.today()
    def d(days):
        return (today - timedelta(days=days)).isoformat()
    goals = (
        [{"date": d(30 * i), "home": "Spain", "away": "X", "team": "Spain",
          "scorer": "Alvaro Morata", "minute": 40, "penalty": i % 4 == 0,
          "own_goal": False} for i in range(12)]
        + [{"date": d(30 * i), "home": "Spain", "away": "X", "team": "Spain",
            "scorer": "Dani Olmo", "minute": 55, "penalty": False, "own_goal": False}
           for i in range(6)]
    )
    results = [{"date": d(20 * i), "home": "Spain", "away": "X",
                "fthg": 2, "ftag": 0, "ftr": "H", "tournament": "Friendly",
                "neutral": False} for i in range(30)]
    monkeypatch.setattr(intl_data, "load_goals", lambda: goals)
    monkeypatch.setattr(intl_data, "load_results", lambda: results)


def test_player_markets_from_real_share(monkeypatch):
    _patch_player_data(monkeypatch)
    lineup_home = [{"name": "Alvaro Morata", "pos": "F"},
                   {"name": "Dani Olmo", "pos": "M"},
                   {"name": "Unknown Guy", "pos": "D"}]
    lineup_away = [{"name": "Someone Else", "pos": "F"}]
    out = players.player_markets("Spain", "France", 1.6, 1.0, lineup_home, lineup_away)
    home = out["home"]["players"]
    morata = next(p for p in home if p["name"] == "Alvaro Morata")
    # Morata has 12/18 of Spain's goals -> highest anytime prob, and a penalty rate
    assert morata["markets"]["anytime_scorer"] > 0
    assert morata["analysis"]["goals"] == 12
    assert morata["markets"]["penalty_goal"] > 0
    assert home[0]["name"] == "Alvaro Morata"           # ranked top
    # anytime = 1 - exp(-mu*share) sanity
    share = morata["analysis"]["goal_share"]
    expected = 1 - math.exp(-1.6 * share)
    assert morata["markets"]["anytime_scorer"] == pytest.approx(expected, abs=1e-3)
    # unknown player with no data -> zero scoring prob but a positional card estimate
    unk = next(p for p in home if p["name"] == "Unknown Guy")
    assert unk["markets"]["anytime_scorer"] == 0
    assert unk["markets"]["to_be_booked_est"] > 0


def test_name_matching_accents(monkeypatch):
    _patch_player_data(monkeypatch)
    ts = players.TeamScoring("Spain")
    assert ts.match_scorer("Álvaro Morata") == "Alvaro Morata"   # accent-folded
    assert ts.match_scorer("Morata") == "Alvaro Morata"          # surname fallback


# ------------------------------------------------------------------- resolver
def test_nation_resolver(monkeypatch):
    monkeypatch.setattr(intl_data, "all_nations",
                        lambda: ["United States", "South Korea", "Iran", "Turkey",
                                 "Czech Republic", "Spain", "Ivory Coast"])
    assert resolve.resolve_nation("USA") == "United States"
    assert resolve.resolve_nation("IR Iran") == "Iran"
    assert resolve.resolve_nation("Turkiye") == "Turkey"
    assert resolve.resolve_nation("Czechia") == "Czech Republic"
    assert resolve.resolve_nation("Spain") == "Spain"
    assert resolve.resolve_nation("Cote d'Ivoire") == "Ivory Coast"
    assert resolve.resolve_nation("Zzzzland") is None
