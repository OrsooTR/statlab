"""Crazy Time engine tests: wheel calibration, bonus games, strategies, metrics."""
import numpy as np
import pytest

from app.crazytime import engine
from app.crazytime.bonus_games import BONUS_RESOLVERS
from app.crazytime.metrics import aggregate_runs
from app.crazytime.outcomes import generate
from app.crazytime.strategies import REGISTRY, create
from app.crazytime.wheel import SPOT_KEYS, Wheel, load_config


@pytest.fixture(scope="module")
def wheel():
    return Wheel()


def test_wheel_layout(wheel):
    assert wheel.total_segments == 54
    assert wheel.counts.tolist() == [21, 13, 7, 4, 4, 2, 2, 1]
    assert abs(wheel.probs.sum() - 1.0) < 1e-12


def test_top_slot_distribution_valid(wheel):
    q = wheel.ts_target_probs
    assert np.all(q > 0)
    assert abs(q.sum() - 1.0) < 1e-9


def test_rtp_matches_published_targets(wheel):
    desc = wheel.describe()
    targets = load_config()["rtp_targets"]
    for s in desc["spots"]:
        assert s["rtp"] == pytest.approx(targets[s["key"]], abs=0.004), s["key"]


def test_bonus_resolvers_positive(wheel):
    rng = np.random.default_rng(1)
    ts = np.ones(10_000)
    for key, fn in BONUS_RESOLVERS.items():
        out = fn(rng, wheel.config, ts)
        assert np.all(out >= 2), key            # minimum bonus multiplier is 2x
        assert np.all(out <= 25_000), key


def test_outcomes_generation(wheel):
    rng = np.random.default_rng(2)
    out = generate(wheel, 200_000, rng)
    freq = np.bincount(out.result, minlength=8) / len(out)
    assert np.allclose(freq, wheel.probs, atol=0.006)
    assert np.all(out.pay >= 0)
    # number payouts without Top Slot must equal base paytable
    for i, base in [(0, 1.0), (1, 2.0), (2, 5.0), (3, 10.0)]:
        mask = (out.result == i) & (out.ts_spot != i)
        assert np.allclose(out.pay[mask], base)


def test_every_strategy_runs(wheel):
    for name in REGISTRY:
        r = engine.run_single(name, {}, 2_000, 500, 1.0, seed_entropy=123)
        assert r["spins_played"] > 0, name
        assert r["final_balance"] >= 0, name
        assert 0 <= r["max_drawdown"] <= 1, name


def test_martingale_progression(wheel):
    s = create("martingale", {"target": "1", "factor": 2, "max_steps": 5}, 1.0, 500, wheel)
    assert s.next_bets()[0][1] == 1.0
    s.observe(False, -1.0, 5, 0.0)
    assert s.next_bets()[0][1] == 2.0
    s.observe(False, -2.0, 5, 0.0)
    assert s.next_bets()[0][1] == 4.0
    s.observe(True, 4.0, 0, 1.0)
    assert s.next_bets()[0][1] == 1.0


def test_flat_rtp_long_run(wheel):
    r = engine.run_single("flat", {"target": "1", "units": 1}, 400_000, 1_000_000, 1.0, 99)
    # with an effectively infinite bankroll, realised RTP ≈ 96.1%
    assert 0.90 < r["rtp_achieved"] < 1.02


def test_stop_loss_and_take_profit(wheel):
    r = engine.run_single("flat", {"target": "1", "units": 1, "stop_loss_pct": 10},
                          100_000, 500, 1.0, 5)
    assert r["stopped_reason"] in ("stop_loss", "take_profit")
    assert r["final_balance"] >= 500 * 0.85  # stopped near the 10% guard


def test_aggregate_runs(wheel):
    runs = [engine.run_single("flat", {"target": "1"}, 3_000, 300, 1.0, 7, run_index=i)
            for i in range(4)]
    agg = aggregate_runs(runs, 300)
    assert agg["runs"] == 4
    assert 0 <= agg["risk_of_ruin"] <= 1
    assert len(agg["balance_bands"]["median"]) > 10
