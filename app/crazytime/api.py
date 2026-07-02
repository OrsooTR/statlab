"""FastAPI router for the Crazy Time simulator."""
from __future__ import annotations

import json
from typing import Any, Optional

import numpy as np
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..core import jobs
from ..core.database import dumps, read_conn, rows_to_dicts, utcnow, write_conn
from . import engine, reports, table
from .bonus_games import simulate_cash_hunt_detail, simulate_pachinko_detail
from .metrics import aggregate_runs
from .strategies import REGISTRY, registry_schemas
from .wheel import Wheel, load_config

router = APIRouter(prefix="/api/crazytime", tags=["crazytime"])

SPIN_PRESETS = [1_000, 10_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000]
BANKROLL_PRESETS = [100, 200, 300, 400, 500, 600, 700, 800, 900, 1000]
MAX_TOTAL_SPINS = 50_000_000  # spins × runs guardrail (memory / wall-clock)


class SimulateRequest(BaseModel):
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    spins: int = Field(ge=100, le=10_000_000)
    runs: int = Field(default=1, ge=1, le=2_000)
    bankroll: float = Field(gt=0, le=10_000_000)
    bet_unit: float = Field(default=1.0, gt=0)
    seed: Optional[int] = None
    name: Optional[str] = None


class CompareEntry(BaseModel):
    strategy: str
    params: dict[str, Any] = Field(default_factory=dict)
    name: Optional[str] = None


class CompareRequest(BaseModel):
    entries: list[CompareEntry] = Field(min_length=1, max_length=50)
    spins: int = Field(ge=100, le=10_000_000)
    runs: int = Field(default=1, ge=1, le=2_000)
    bankroll: float = Field(gt=0, le=10_000_000)
    bet_unit: float = Field(default=1.0, gt=0)
    seed: Optional[int] = None


class ExportRequest(BaseModel):
    simulation_ids: list[int] = Field(min_length=1)
    format: str = Field(pattern="^(pdf|xlsx|csv|json)$")


def _persist(name: str, strategy: str, params: dict, spins: int, runs: int,
             bankroll: float, results: dict) -> int:
    with write_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ct_simulations (created_at, name, strategy, params_json, "
            "spins, runs, bankroll, results_json) VALUES (?,?,?,?,?,?,?,?)",
            (utcnow(), name, strategy, dumps(params), spins, runs, bankroll, dumps(results)),
        )
        return int(cur.lastrowid)


def _validate(req: SimulateRequest | CompareRequest, n_entries: int = 1) -> None:
    total = req.spins * req.runs * n_entries
    if total > MAX_TOTAL_SPINS:
        raise HTTPException(422, detail=(
            f"spins × runs × entries = {total:,} exceeds the {MAX_TOTAL_SPINS:,} guardrail; "
            "reduce runs or spins"))


@router.get("/config")
def get_config() -> dict:
    wheel = Wheel()
    return {
        "wheel": wheel.describe(),
        "raw_config": load_config(),
        "spin_presets": SPIN_PRESETS,
        "bankroll_presets": BANKROLL_PRESETS,
        "max_total_spins": MAX_TOTAL_SPINS,
    }


@router.get("/strategies")
def get_strategies() -> list[dict]:
    return registry_schemas()


@router.post("/simulate")
def simulate(req: SimulateRequest) -> dict:
    if req.strategy not in REGISTRY:
        raise HTTPException(422, detail=f"unknown strategy {req.strategy}")
    _validate(req)
    seed = req.seed if req.seed is not None else int(np.random.SeedSequence().entropy % (2**63))
    name = req.name or REGISTRY[req.strategy].LABEL

    def on_done(agg: dict) -> dict:
        sim_id = _persist(name, req.strategy, req.params, req.spins, req.runs,
                          req.bankroll, agg)
        agg["simulation_id"] = sim_id
        return agg

    if req.runs == 1:
        payload = {"strategy": req.strategy, "params": req.params, "spins": req.spins,
                   "bankroll": req.bankroll, "bet_unit": req.bet_unit, "seed": seed}
        job = jobs.manager.submit_process(
            "ct-simulate", engine.run_single_job, payload,
            on_done=lambda r: on_done(aggregate_runs([r], req.bankroll)))
    else:
        args_list = [(req.strategy, req.params, req.spins, req.bankroll,
                      req.bet_unit, seed, i) for i in range(req.runs)]
        job = jobs.manager.submit_process_fanout(
            "ct-simulate", engine.run_one_of_many, args_list,
            aggregate=lambda rs: aggregate_runs(rs, req.bankroll), on_done=on_done)
    return {"job_id": job.id}


@router.post("/compare")
def compare(req: CompareRequest) -> dict:
    for e in req.entries:
        if e.strategy not in REGISTRY:
            raise HTTPException(422, detail=f"unknown strategy {e.strategy}")
    _validate(req, len(req.entries))
    seed = req.seed if req.seed is not None else int(np.random.SeedSequence().entropy % (2**63))

    args_list: list[tuple] = []
    for k, e in enumerate(req.entries):
        for i in range(req.runs):
            # every entry sees identical RNG substreams → paired comparison
            args_list.append((e.strategy, e.params, req.spins, req.bankroll,
                              req.bet_unit, seed, i))

    def aggregate(results: list[dict]) -> dict:
        out = []
        for k, e in enumerate(req.entries):
            chunk = results[k * req.runs:(k + 1) * req.runs]
            agg = aggregate_runs(chunk, req.bankroll)
            name = e.name or REGISTRY[e.strategy].LABEL
            sim_id = _persist(name, e.strategy, e.params, req.spins, req.runs,
                              req.bankroll, agg)
            out.append({"name": name, "strategy": e.strategy, "simulation_id": sim_id,
                        "results": agg})
        # rank: blend of mean ROI, P(profit), inverse ruin, inverse drawdown,
        # and survival time (discriminates strategies when all paths ruin)
        for entry in out:
            r = entry["results"]
            entry["score"] = (r["mean_roi"] + r["prob_profit"]
                              - r["risk_of_ruin"] - r["mean_max_drawdown"] * 0.5
                              + 0.5 * r["mean_spins_survived"] / req.spins)
        out.sort(key=lambda x: x["score"], reverse=True)
        for rank, entry in enumerate(out, start=1):
            entry["rank"] = rank
        return {"comparison": out}

    job = jobs.manager.submit_process_fanout("ct-compare", engine.run_one_of_many,
                                             args_list, aggregate=aggregate)
    return {"job_id": job.id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.manager.get(job_id)
    if job is None:
        raise HTTPException(404, detail="job not found")
    return job.snapshot()


@router.get("/simulations")
def list_simulations() -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT id, created_at, name, strategy, spins, runs, bankroll "
            "FROM ct_simulations ORDER BY id DESC LIMIT 200").fetchall()
    return rows_to_dicts(rows)


@router.get("/simulations/{sim_id}")
def get_simulation(sim_id: int) -> dict:
    sims = reports.load_simulations([sim_id])
    if not sims:
        raise HTTPException(404, detail="simulation not found")
    return sims[0]


@router.delete("/simulations/{sim_id}")
def delete_simulation(sim_id: int) -> dict:
    with write_conn() as conn:
        conn.execute("DELETE FROM ct_simulations WHERE id=?", (sim_id,))
    return {"deleted": sim_id}


@router.post("/export")
def export(req: ExportRequest) -> dict:
    try:
        path = reports.export_simulations(req.simulation_ids, req.format)
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc
    return {"path": path}


# ------------------------------------------------------------ interactive table
class TableSessionRequest(BaseModel):
    bankroll: float = Field(default=500, gt=0, le=10_000_000)


class TableSpinRequest(BaseModel):
    session_id: str
    bets: dict[str, float]


class TableChoiceRequest(BaseModel):
    session_id: str
    choice: Any


@router.post("/table/session")
def table_session(req: TableSessionRequest) -> dict:
    return table.create_session(req.bankroll)


@router.get("/table/session/{sid}")
def table_session_get(sid: str) -> dict:
    try:
        return table.snapshot(sid)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc


@router.get("/table/layout")
def table_layout() -> dict:
    wheel = Wheel()
    return {"layout": wheel.layout,
            "top_slot_multipliers": wheel.ts_mults.tolist(),
            "table": wheel.config["table"]}


@router.post("/table/spin")
def table_spin(req: TableSpinRequest) -> dict:
    try:
        return table.spin(req.session_id, req.bets)
    except KeyError as exc:
        raise HTTPException(404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.post("/table/bonus-choice")
def table_bonus_choice(req: TableChoiceRequest) -> dict:
    try:
        return table.bonus_choice(req.session_id, req.choice)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, detail=str(exc)) from exc


@router.get("/bonus-demo/{game}")
def bonus_demo(game: str) -> dict:
    """One fully-explicit bonus round for the UI's bonus inspector."""
    rng = np.random.default_rng()
    cfg = load_config()
    if game == "cash_hunt":
        return simulate_cash_hunt_detail(rng, cfg)
    if game == "pachinko":
        return simulate_pachinko_detail(rng, cfg)
    if game in ("coin_flip", "crazy_time"):
        from .bonus_games import BONUS_RESOLVERS
        ts = np.ones(1)
        won = float(BONUS_RESOLVERS[game](rng, cfg, ts)[0])
        return {"won": won}
    raise HTTPException(404, detail="unknown bonus game")
