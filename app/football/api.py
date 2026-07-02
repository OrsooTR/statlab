"""FastAPI router for the Football AI Prediction Engine."""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core import jobs
from ..core.database import dumps, read_conn, rows_to_dicts, utcnow, write_conn
from . import backtest as bt
from . import data_sources as ds
from . import database as store
from . import reports, slips
from .predict import get_engine

router = APIRouter(prefix="/api/football", tags=["football"])


class PredictRequest(BaseModel):
    league: str
    home: str
    away: str
    date: Optional[str] = None
    odds: Optional[dict[str, float]] = None
    n_sims: int = Field(default=10_000, ge=1_000, le=200_000)


class PredictDayRequest(BaseModel):
    date: Optional[str] = None
    league: Optional[str] = None


class BacktestRequest(BaseModel):
    league: str
    seasons: list[str] = Field(min_length=1, max_length=6)
    value_threshold: float = Field(default=1.06, ge=1.0, le=1.5)


class SlipCandidate(BaseModel):
    match: Optional[str] = None
    league: Optional[str] = None
    date: Optional[str] = None
    home: Optional[str] = None
    away: Optional[str] = None
    market: str = "1X2"
    selection: str
    probability: float = Field(gt=0, lt=1)
    odds: float = Field(gt=1)


class SlipRequest(BaseModel):
    candidates: list[SlipCandidate] = Field(min_length=2, max_length=60)
    size: int = Field(ge=2, le=10)


class ExportRequest(BaseModel):
    kind: str = Field(pattern="^(prediction|backtest)$")
    id: int
    format: str = Field(pattern="^(pdf|xlsx|csv|json)$")


# ------------------------------------------------------------------- data layer
@router.get("/competitions")
def competitions() -> dict:
    return {"competitions": store.data_summary(),
            "current_season": ds.current_season_code()}


@router.post("/refresh")
def refresh(league: Optional[str] = None) -> dict:
    if league:
        if ds.competition(league) is None:
            raise HTTPException(404, detail=f"unknown competition {league}")
        job = jobs.manager.submit_thread("fb-refresh", store.refresh_league, league)
    else:
        job = jobs.manager.submit_thread("fb-refresh", store.refresh_all)
    return {"job_id": job.id}


@router.get("/teams")
def teams(league: str) -> list[str]:
    return store.get_teams(league)


@router.get("/seasons")
def seasons(league: str) -> list[str]:
    return store.get_seasons(league)


@router.get("/matches")
def matches(league: str, season: Optional[str] = None, limit: int = 200) -> list[dict]:
    ms = store.get_matches(league, season=season)
    return ms[-limit:][::-1]


@router.get("/fixtures")
def fixtures(league: Optional[str] = None, day: Optional[str] = None) -> list[dict]:
    fx = ds.fetch_fixtures()
    if league:
        fx = [f for f in fx if f["league"] == league]
    if day:
        fx = [f for f in fx if f["date"] == day]
    return fx


# ------------------------------------------------------------------ predictions
def _predict_and_store(progress, req: PredictRequest) -> dict:
    progress(0.1, "fitting league models")
    engine = get_engine(req.league)
    progress(0.7, "simulating match")
    pred = engine.predict(req.home, req.away, req.date, req.odds, req.n_sims)
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fb_predictions (created_at, league, home, away, match_date, "
            "prediction_json) VALUES (?,?,?,?,?,?)",
            (utcnow(), req.league, req.home, req.away, pred["date"], dumps(pred)))
        pred["id"] = int(cur.lastrowid)
    return pred


@router.post("/predict")
def predict(req: PredictRequest) -> dict:
    job = jobs.manager.submit_thread("fb-predict", _predict_and_store, req)
    return {"job_id": job.id}


MIN_MATCHES_FOR_MODEL = 200


class PredictLiveRequest(BaseModel):
    fd_code: str                       # football-data league code (from the feed)
    home: str                          # raw feed team names
    away: str
    date: Optional[str] = None
    odds: Optional[dict[str, float]] = None


def _ensure_league_data(progress, code: str) -> None:
    existing = store.get_matches(code)
    if len(existing) < MIN_MATCHES_FOR_MODEL:
        progress(0.1, f"downloading {code} history…")
        store.refresh_league(lambda f, m="": progress(0.1 + 0.4 * f, m), code)


def _predict_live(progress, req: PredictLiveRequest) -> dict:
    from . import resolve
    comp = ds.competition(req.fd_code)
    if comp is None:
        raise ValueError(f"no historical model for this competition "
                         f"(feed code {req.fd_code}). Predictions cover supported "
                         f"domestic leagues; national-team and cup ties are not modelled.")
    _ensure_league_data(progress, req.fd_code)
    home = resolve.resolve_team(req.home, req.fd_code)
    away = resolve.resolve_team(req.away, req.fd_code)
    if not home or not away:
        missing = req.home if not home else req.away
        raise ValueError(f"could not match '{missing}' to a team in {comp['name']} "
                         f"history — the model needs a team with past results in this league.")
    progress(0.6, "fitting models & simulating")
    engine = get_engine(req.fd_code)
    pred = engine.predict(home, away, req.date, req.odds, n_sims=10_000)
    pred["resolved"] = {"home": home, "away": away,
                        "feed_home": req.home, "feed_away": req.away}
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fb_predictions (created_at, league, home, away, match_date, "
            "prediction_json) VALUES (?,?,?,?,?,?)",
            (utcnow(), req.fd_code, home, away, pred["date"], dumps(pred)))
        pred["id"] = int(cur.lastrowid)
    return pred


@router.post("/predict-live")
def predict_live(req: PredictLiveRequest) -> dict:
    job = jobs.manager.submit_thread("fb-predict-live", _predict_live, req)
    return {"job_id": job.id}


def _predict_day(progress, req: PredictDayRequest) -> dict:
    day = req.date or date.today().isoformat()
    fx = ds.fetch_fixtures()
    fx = [f for f in fx if f["date"] == day and (not req.league or f["league"] == req.league)]
    if not fx:
        return {"date": day, "predictions": [], "message": "no fixtures found for this date"}
    out = []
    for i, f in enumerate(fx):
        progress(i / len(fx), f"{f['home']} vs {f['away']}")
        try:
            engine = get_engine(f["league"])
            odds = {"home": f["b365h"], "draw": f["b365d"], "away": f["b365a"]} \
                if f.get("b365h") else None
            pred = engine.predict(f["home"], f["away"], f["date"], odds, n_sims=5_000)
            pred["odds"] = odds
            out.append(pred)
        except ValueError as exc:
            out.append({"league": f["league"], "home": f["home"], "away": f["away"],
                        "date": f["date"], "error": str(exc)})
    ok = [p for p in out if "error" not in p]
    ok.sort(key=lambda p: p["confidence_pct"], reverse=True)
    return {"date": day, "predictions": ok,
            "skipped": [p for p in out if "error" in p]}


@router.post("/predict-day")
def predict_day(req: PredictDayRequest) -> dict:
    job = jobs.manager.submit_thread("fb-predict-day", _predict_day, req)
    return {"job_id": job.id}


@router.get("/predictions")
def predictions(limit: int = 50) -> list[dict]:
    return reports.recent_predictions(limit)


# -------------------------------------------------------------------- backtests
def _run_backtest(progress, req: BacktestRequest) -> dict:
    metrics = bt.run_backtest(progress, req.league, req.seasons, req.value_threshold)
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO fb_backtests (created_at, league, seasons, metrics_json) "
            "VALUES (?,?,?,?)",
            (utcnow(), req.league, ",".join(req.seasons), dumps(metrics)))
        metrics["id"] = int(cur.lastrowid)
    metrics["league"] = req.league
    metrics["seasons"] = req.seasons
    return metrics


@router.post("/backtest")
def backtest(req: BacktestRequest) -> dict:
    job = jobs.manager.submit_thread("fb-backtest", _run_backtest, req)
    return {"job_id": job.id}


@router.get("/backtests")
def backtests() -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, league, seasons, metrics_json FROM fb_backtests "
            "ORDER BY id DESC LIMIT 50").fetchall()
    out = []
    for r in rows_to_dicts(rows):
        m = json.loads(r.pop("metrics_json"))
        r["summary"] = {
            "accuracy": m["accuracy"], "log_loss": m["log_loss"],
            "brier_score": m["brier_score"], "roi": m["betting"]["roi"],
            "yield_pct": m["betting"]["yield_pct"],
            "clv": m["betting"]["closing_line_value"],
            "matches": m["matches_evaluated"],
        }
        out.append(r)
    return out


@router.get("/backtests/{bt_id}")
def backtest_detail(bt_id: int) -> dict:
    with read_conn() as conn:
        row = conn.execute("SELECT * FROM fb_backtests WHERE id=?", (bt_id,)).fetchone()
    if not row:
        raise HTTPException(404, detail="backtest not found")
    d = dict(row)
    d["metrics"] = json.loads(d.pop("metrics_json"))
    return d


# ------------------------------------------------------------------------ slips
@router.post("/slip/build")
def build_slip(req: SlipRequest) -> dict:
    try:
        built = slips.build_slips([c.model_dump() for c in req.candidates], req.size)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    with write_conn() as conn:
        conn.execute("INSERT INTO fb_slips (created_at, slip_json) VALUES (?,?)",
                     (utcnow(), dumps({"size": req.size, "slips": built[:3]})))
    return {"slips": built}


# -------------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard() -> dict:
    today = date.today().isoformat()
    fx = [f for f in ds.fetch_fixtures() if f["date"] >= today][:40]
    preds = reports.recent_predictions(200)
    _settle_predictions(preds)
    settled = [p for p in preds if p.get("actual_result")]
    correct = sum(1 for p in settled
                  if _predicted_outcome(p) == p["actual_result"])
    with read_conn() as conn:
        row = conn.execute(
            "SELECT metrics_json FROM fb_backtests ORDER BY id DESC LIMIT 1").fetchone()
    last_bt = json.loads(row["metrics_json"]) if row else None
    return {
        "today": today,
        "upcoming_fixtures": fx,
        "recent_predictions": preds[:20],
        "settled_count": len(settled),
        "settled_accuracy": round(correct / len(settled), 4) if settled else None,
        "latest_backtest": {
            "accuracy": last_bt["accuracy"], "log_loss": last_bt["log_loss"],
            "brier": last_bt["brier_score"], "roi": last_bt["betting"]["roi"],
            "calibration": last_bt["calibration"],
        } if last_bt else None,
    }


def _predicted_outcome(p: dict) -> str:
    pr = p.get("probabilities", {})
    if not pr:
        return ""
    best = max(("home", "draw", "away"), key=lambda k: pr.get(k, 0))
    return {"home": "H", "draw": "D", "away": "A"}[best]


def _settle_predictions(preds: list[dict]) -> None:
    """Fill actual results for stored predictions once matches are played."""
    updates = []
    with read_conn() as conn:
        for p in preds:
            if p.get("actual_result") or "id" not in p:
                continue
            row = conn.execute(
                "SELECT ftr FROM fb_matches WHERE league=? AND home=? AND away=? "
                "AND date=? AND ftr IS NOT NULL",
                (p["league"], p["home"], p["away"], p["date"])).fetchone()
            if row:
                p["actual_result"] = row["ftr"]
                updates.append((row["ftr"], p["id"]))
    if updates:
        with write_conn() as conn:
            conn.executemany(
                "UPDATE fb_predictions SET actual_result=? WHERE id=?", updates)


# ---------------------------------------------------------------------- exports
@router.post("/export")
def export(req: ExportRequest) -> dict:
    try:
        if req.kind == "prediction":
            path = reports.export_prediction(req.id, req.format)
        else:
            path = reports.export_backtest(req.id, req.format)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"path": path}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(404, detail="job not found")
    return job.snapshot()
