"""FastAPI router for the Live Center."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ...core import jobs
from . import inplay, provider

router = APIRouter(prefix="/api/live", tags=["live"])


class SettingsRequest(BaseModel):
    provider: str = Field(pattern="^(auto|demo|api_football)$")
    api_key: str = ""


@router.get("/settings")
def get_settings() -> dict:
    s = provider.load_settings()
    p = provider.get_provider()
    return {"provider": s.get("provider", "demo"),
            "has_key": bool(s.get("api_key")),
            "status": p.status()}


@router.post("/settings")
def set_settings(req: SettingsRequest) -> dict:
    s = provider.load_settings()
    s["provider"] = req.provider
    if req.api_key:
        s["api_key"] = req.api_key.strip()
    if req.provider == "api_football" and not s.get("api_key"):
        raise HTTPException(422, detail="API-Football requires an API key "
                                        "(free at api-football.com)")
    provider.save_settings(s)
    return get_settings()


@router.get("/matches")
def matches() -> dict:
    p = provider.get_provider()
    try:
        live = p.live_matches()
        today = p.today_matches()
    except RuntimeError as exc:
        raise HTTPException(429, detail=str(exc)) from exc
    except httpx_errors() as exc:  # network/auth problems surface cleanly
        raise HTTPException(502, detail=f"live data provider error: {exc}") from exc
    live_ids = {m["id"] for m in live}
    upcoming = [m for m in today if m["id"] not in live_ids and not m["finished"]]
    finished = [m for m in today if m["id"] not in live_ids and m["finished"]]
    return {"provider": p.status(), "live": live,
            "upcoming": upcoming, "finished": finished}


@router.get("/match/{fixture_id}")
def match_detail(fixture_id: str) -> dict:
    p = provider.get_provider()
    try:
        d = p.match_detail(fixture_id)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(429, detail=str(exc)) from exc
    except httpx_errors() as exc:
        raise HTTPException(502, detail=f"live data provider error: {exc}") from exc
    info = d["info"]
    prediction = None
    momentum = None
    if info.get("minute") is not None:
        prediction = inplay.predict_inplay(info, d["stats"], d["events"])
        momentum = inplay.momentum_series(d["events"], info["minute"] or 0)
    return {**d, "prediction": prediction, "momentum": momentum}


def httpx_errors():
    import httpx
    return (httpx.HTTPError, httpx.HTTPStatusError)


# ------------------------------------------------ national-team full prediction
class IntlPredictRequest(BaseModel):
    fixture_id: str
    neutral: bool = True


def _lineup_players(lineups: dict, side: str) -> list[dict]:
    if not lineups or side not in lineups:
        return []
    starters = lineups[side].get("starters", [])
    return [{"name": p.get("name", ""), "pos": p.get("pos", "")} for p in starters]


def _international_prediction(progress, fixture_id: str, neutral: bool) -> dict:
    from ..internationals import engine as nat_engine
    from ..internationals import players as nat_players
    from ..internationals import resolve as nat_resolve

    progress(0.15, "loading national-team model")
    p = provider.get_provider()
    detail = p.match_detail(fixture_id)
    info = detail["info"]
    home_raw, away_raw = info["home"], info["away"]
    home = nat_resolve.resolve_nation(home_raw)
    away = nat_resolve.resolve_nation(away_raw)
    if not home or not away:
        missing = home_raw if not home else away_raw
        raise ValueError(f"could not match national team '{missing}' to the results dataset")
    eng = nat_engine.get_engine()
    if not eng.known(home) or not eng.known(away):
        raise ValueError("insufficient international history for one of these teams")
    progress(0.55, "simulating match & scorers")
    pred = eng.predict(home, away, neutral=neutral, n_sims=10_000)
    pred["resolved"] = {"home": home, "away": away,
                        "feed_home": home_raw, "feed_away": away_raw}
    mu_h = pred["expected_goals"]["home"]
    mu_a = pred["expected_goals"]["away"]
    lineups = detail.get("lineups")
    lh = _lineup_players(lineups, "home")
    la = _lineup_players(lineups, "away")
    markets = None
    if lh and la:
        progress(0.8, "computing player markets")
        markets = nat_players.player_markets(home, away, mu_h, mu_a, lh, la)
    return {"info": info, "prediction": pred, "player_markets": markets,
            "lineups": lineups, "has_lineups": bool(lh and la)}


@router.post("/international-prediction")
def international_prediction(req: IntlPredictRequest) -> dict:
    job = jobs.manager.submit_thread(
        "intl-predict", _international_prediction, req.fixture_id, req.neutral)
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(404, detail="job not found")
    return job.snapshot()
