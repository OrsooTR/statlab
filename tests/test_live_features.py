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


def test_bonus_pays_stake_back():
    """Winning a bonus must return multiplier × stake PLUS the stake itself:
    5 on a bonus that pays 2x → payout 15 (10 win + 5 stake back)."""
    s = table.create_session(1_000_000)
    sid = s["session_id"]
    for _ in range(800):
        r = table.spin(sid, {"coin_flip": 5})
        if r["segment"] == "coin_flip":
            won = r["detail"]["won_multiplier"]
            assert r["winnings"] == pytest.approx(5 * (1 + won), abs=0.01)
            return
    pytest.fail("coin_flip never hit in 800 spins (p < 1e-25)")


def test_number_pays_stake_back():
    s = table.create_session(1_000_000)
    sid = s["session_id"]
    for _ in range(400):
        r = table.spin(sid, {"10": 5})
        if r["phase"] == "await_choice":
            table.bonus_choice(sid, 0 if r["game"] == "cash_hunt" else "blue")
            continue
        if r["segment"] == "10":
            pays = r["detail"]["pays"]
            assert r["winnings"] == pytest.approx(5 * (1 + pays), abs=0.01)
            return
    pytest.fail("'10' never hit in 400 spins (p < 1e-13)")


# -------------------------------------------------------------- multi-source
def test_aggregator_merges_same_fixture():
    from app.football.live.aggregator import merge_matches, same_fixture
    espn = {"id": "espn:ger.1:1", "kickoff": "2026-07-02T18:30", "home": "Bayern Munich",
            "away": "Borussia Dortmund", "score_home": 1, "score_away": 0,
            "minute": 55, "live": True, "finished": False, "sources": ["espn"]}
    oldb = {"id": "oldb:bl1:9", "kickoff": "2026-07-02T18:30", "home": "FC Bayern München",
            "away": "Borussia Dortmund", "score_home": 1, "score_away": 0,
            "minute": None, "live": True, "finished": False, "sources": ["openligadb"]}
    other = {"id": "espn:eng.1:2", "kickoff": "2026-07-02T20:00", "home": "Arsenal",
             "away": "Chelsea", "score_home": None, "score_away": None,
             "minute": None, "live": False, "finished": False, "sources": ["espn"]}
    assert same_fixture(espn, oldb)
    assert not same_fixture(espn, other)
    merged = merge_matches([[espn, other], [oldb]])
    assert len(merged) == 2
    bayern = next(m for m in merged if "Bayern" in m["home"])
    assert set(bayern["sources"]) == {"espn", "openligadb"}
    assert bayern["minute"] == 55  # richer ESPN record won and kept its minute


def test_sources_registry_is_honest():
    import json
    from pathlib import Path
    cfg = json.loads((Path("app/football/live/sources.json")).read_text(encoding="utf-8"))
    ids = {s["id"]: s for s in cfg["sources"]}
    assert ids["espn"]["enabled"] is True and ids["espn"]["auth"] == "none"
    assert ids["openligadb"]["enabled"] is True
    # scraping-hostile sites must stay disabled with a documented reason
    for blocked in ("diretta_flashscore", "sofascore", "fotmob"):
        assert ids[blocked]["enabled"] is False
        assert ids[blocked]["_why_disabled"]


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
