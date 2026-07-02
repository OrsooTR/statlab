"""Tests for the interactive Crazy Time table and the football in-play model."""
import pytest

from app.crazytime import table
from app.crazytime.wheel import Wheel
from app.football.live import provider
from app.football.live.inplay import momentum_series, predict_inplay


# ------------------------------------------------------------------ live table
def test_wheel_layout_matches_counts():
    w = Wheel()
    assert len(w.layout) == 54
    from collections import Counter
    c = Counter(w.layout)
    assert c["1"] == 21 and c["2"] == 13 and c["5"] == 7 and c["10"] == 4
    assert c["coin_flip"] == 4 and c["cash_hunt"] == 2
    assert c["pachinko"] == 2 and c["crazy_time"] == 1


def test_table_session_flow():
    s = table.create_session(200)
    sid = s["session_id"]
    assert s["balance"] == 200
    r = table.spin(sid, {"1": 5, "10": 5})
    assert r["total_bet"] == 10
    assert r["phase"] in ("settled", "bonus_settled", "await_choice")
    snap = table.snapshot(sid)
    assert snap["spins"] == 1
    assert snap["balance"] <= 200 - 10 + max(0, r.get("winnings", 0)) + 1e-6


def test_table_rejects_over_balance():
    s = table.create_session(10)
    with pytest.raises(ValueError):
        table.spin(s["session_id"], {"1": 50})


def test_table_bonus_choice_resolves():
    # spin until a choice bonus appears, then resolve it
    s = table.create_session(1_000_000)
    sid = s["session_id"]
    for _ in range(600):
        r = table.spin(sid, {"cash_hunt": 1, "crazy_time": 1})
        if r["phase"] == "await_choice":
            choice = 0 if r["game"] == "cash_hunt" else "blue"
            done = table.bonus_choice(sid, choice)
            assert done["phase"] == "bonus_settled"
            assert done["detail"]["won_multiplier"] >= 2
            return
    pytest.fail("no choice bonus in 600 spins (p < 1e-8)")


def test_table_accounting_consistent():
    s = table.create_session(100_000)
    sid = s["session_id"]
    for _ in range(200):
        r = table.spin(sid, {"1": 1})
        # bonus rounds play even without a chip on them (as in the real game)
        if r["phase"] == "await_choice":
            table.bonus_choice(sid, 0 if r["game"] == "cash_hunt" else "blue")
    snap = table.snapshot(sid)
    assert snap["balance"] == pytest.approx(
        100_000 - snap["total_staked"] + snap["total_returned"], abs=0.01)


# --------------------------------------------------------------------- in-play
def _info(minute, sh, sa, status="2H"):
    return {"minute": minute, "score_home": sh, "score_away": sa,
            "home": "H", "away": "A", "status": status, "finished": status == "FT"}


def test_inplay_probs_sum_to_one():
    p = predict_inplay(_info(60, 1, 0), {}, [])
    pr = p["probabilities"]
    assert pr["home"] + pr["draw"] + pr["away"] == pytest.approx(1.0, abs=1e-3)
    assert pr["home"] > 0.5  # leading at 60' with home rates favours home


def test_inplay_late_lead_is_stronger():
    early = predict_inplay(_info(20, 1, 0), {}, [])["probabilities"]["home"]
    late = predict_inplay(_info(85, 1, 0), {}, [])["probabilities"]["home"]
    assert late > early


def test_inplay_red_card_hurts():
    base = predict_inplay(_info(50, 0, 0), {}, [])["probabilities"]["home"]
    red = predict_inplay(_info(50, 0, 0), {}, [
        {"minute": 40, "side": "home", "type": "card", "detail": "Red Card"}
    ])["probabilities"]["home"]
    assert red < base


def test_inplay_finished_match_is_certain():
    p = predict_inplay(_info(90, 2, 1, status="FT"), {}, [])
    assert p["probabilities"]["home"] == pytest.approx(1.0, abs=1e-6)


def test_momentum_series_shapes():
    ev = [{"minute": 10, "side": "home", "type": "goal", "detail": "Normal Goal"},
          {"minute": 12, "side": "away", "type": "shot", "detail": "Shot on target"}]
    m = momentum_series(ev, 30)
    assert len(m["minutes"]) == len(m["home"]) == len(m["away"])
    assert max(m["home"]) > 0


# ------------------------------------------------------------------ demo feed
def test_demo_provider_consistency():
    p = provider.DemoProvider()
    today = p.today_matches()
    assert len(today) == 6
    for m in today:
        assert m["home"] and m["away"] and m["league"].endswith("DEMO")
    live = p.live_matches()
    for m in live:
        d = p.match_detail(m["id"])
        assert d["info"]["id"] == m["id"]
        assert d["lineups"]["home"]["starters"] and len(d["lineups"]["home"]["starters"]) == 11
        # score must equal goal events shown
        goals_h = sum(1 for e in d["events"] if e["type"] == "goal" and e["side"] == "home")
        assert d["info"]["score_home"] == goals_h
