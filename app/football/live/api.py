"""FastAPI router for the Live Center."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from . import inplay, provider

router = APIRouter(prefix="/api/live", tags=["live"])


class SettingsRequest(BaseModel):
    provider: str = Field(pattern="^(demo|api_football)$")
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
