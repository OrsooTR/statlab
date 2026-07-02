"""Report builders for football predictions and backtests."""
from __future__ import annotations

import json
from typing import Any

from ..core import exports
from ..core.database import read_conn, rows_to_dicts


def _load(table: str, row_id: int) -> dict | None:
    with read_conn() as conn:
        row = conn.execute(f"SELECT * FROM {table} WHERE id=?", (row_id,)).fetchone()
    return dict(row) if row else None


def prediction_blocks(p: dict[str, Any]) -> list[dict]:
    pr = p["probabilities"]
    blocks = [
        {"type": "title", "text": f"Match Prediction — {p['home']} vs {p['away']}"},
        {"type": "paragraph", "text": p.get("disclaimer", "")},
        {"type": "kv", "items": [
            ["League", p["league"]], ["Date", p["date"]],
            ["Predicted scoreline", p["predicted_scoreline"]],
            ["Confidence", f"{p['confidence_pct']}%"],
            ["Risk indicator", p["risk"]],
            ["Home win", f"{pr['home']*100:.1f}%"],
            ["Draw", f"{pr['draw']*100:.1f}%"],
            ["Away win", f"{pr['away']*100:.1f}%"],
            ["Expected goals", f"{p['expected_goals']['home']} – {p['expected_goals']['away']}"],
            ["BTTS", f"{p['markets']['btts']*100:.1f}%"],
            ["Over 2.5", f"{p['markets']['over_under']['2.5']['over']*100:.1f}%"],
        ]},
        {"type": "heading", "text": "Reasoning"},
    ]
    for r in p.get("reasoning", []):
        blocks.append({"type": "paragraph", "text": r})
    blocks.append({"type": "heading", "text": "Alternative scorelines"})
    blocks.append({"type": "table", "columns": ["Score", "Probability"],
                   "rows": [[a["score"], f"{a['probability']*100:.2f}%"]
                            for a in p.get("alternatives", [])]})
    blocks.append({"type": "heading", "text": "Model breakdown"})
    rows = []
    for name, d in p.get("model_breakdown", {}).items():
        rows.append([name, f"{d.get('p_home', 0)*100:.1f}%", f"{d.get('p_draw', 0)*100:.1f}%",
                     f"{d.get('p_away', 0)*100:.1f}%",
                     f"{p.get('ensemble_weights', {}).get(name, 0)*100:.1f}%"])
    blocks.append({"type": "table",
                   "columns": ["Model", "Home", "Draw", "Away", "Ensemble weight"],
                   "rows": rows})
    return blocks


def backtest_blocks(b: dict[str, Any], league: str, seasons: str) -> list[dict]:
    bet = b["betting"]
    blocks = [
        {"type": "title", "text": f"Backtest Report — {league}"},
        {"type": "kv", "items": [
            ["Seasons", seasons],
            ["Matches evaluated", b["matches_evaluated"]],
            ["Accuracy (1X2)", f"{b['accuracy']*100:.2f}%"],
            ["Log loss", b["log_loss"]],
            ["Brier score", b["brier_score"]],
            ["Exact-score hit rate", f"{b['exact_score_hit_rate']*100:.2f}%"],
            ["Value bets placed", bet["bets_placed"]],
            ["Betting ROI", f"{bet['roi']*100:.2f}%" if bet["roi"] is not None else "n/a"],
            ["Yield", f"{bet['yield_pct']}%" if bet["yield_pct"] is not None else "n/a"],
            ["Max drawdown (units)", bet["max_drawdown"]],
            ["Closing Line Value", f"{bet['closing_line_value']*100:.2f}%"
             if bet["closing_line_value"] is not None else "n/a"],
        ]},
        {"type": "heading", "text": "Calibration"},
        {"type": "table", "columns": ["Bin", "Predicted", "Observed", "Count"],
         "rows": [[c["bin_mid"], c["predicted"], c["observed"], c["count"]]
                  for c in b["calibration"]]},
        {"type": "heading", "text": "Recent evaluated matches"},
        {"type": "table",
         "columns": ["Date", "Home", "Away", "Result", "Predicted score", "Actual"],
         "rows": [[s["date"], s["home"], s["away"], s["result"],
                   s["pred_score"] or "-", s["actual_score"]] for s in b["sample"]]},
    ]
    return blocks


def export_prediction(pred_id: int, fmt: str) -> str:
    row = _load("fb_predictions", pred_id)
    if not row:
        raise ValueError("prediction not found")
    p = json.loads(row["prediction_json"])
    if fmt == "pdf":
        return str(exports.export_pdf("football-prediction", prediction_blocks(p)))
    if fmt == "xlsx":
        return str(exports.export_xlsx("football-prediction", {"Prediction": prediction_blocks(p)}))
    if fmt == "csv":
        pr = p["probabilities"]
        return str(exports.export_csv(
            "football-prediction",
            ["league", "date", "home", "away", "p_home", "p_draw", "p_away",
             "scoreline", "confidence_pct", "risk"],
            [[p["league"], p["date"], p["home"], p["away"], pr["home"], pr["draw"],
              pr["away"], p["predicted_scoreline"], p["confidence_pct"], p["risk"]]]))
    if fmt == "json":
        return str(exports.export_json("football-prediction", p))
    raise ValueError(f"unsupported format {fmt}")


def export_backtest(bt_id: int, fmt: str) -> str:
    row = _load("fb_backtests", bt_id)
    if not row:
        raise ValueError("backtest not found")
    b = json.loads(row["metrics_json"])
    if fmt == "pdf":
        return str(exports.export_pdf("football-backtest",
                                      backtest_blocks(b, row["league"], row["seasons"])))
    if fmt == "xlsx":
        return str(exports.export_xlsx(
            "football-backtest", {"Backtest": backtest_blocks(b, row["league"], row["seasons"])}))
    if fmt == "csv":
        return str(exports.export_csv(
            "football-backtest",
            ["metric", "value"],
            [["accuracy", b["accuracy"]], ["log_loss", b["log_loss"]],
             ["brier", b["brier_score"]], ["roi", b["betting"]["roi"]],
             ["yield_pct", b["betting"]["yield_pct"]],
             ["clv", b["betting"]["closing_line_value"]]]))
    if fmt == "json":
        return str(exports.export_json("football-backtest", b))
    raise ValueError(f"unsupported format {fmt}")


def recent_predictions(limit: int = 100) -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM fb_predictions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    out = []
    for r in rows_to_dicts(rows):
        p = json.loads(r["prediction_json"])
        p["id"] = r["id"]
        p["created_at"] = r["created_at"]
        p["actual_result"] = r["actual_result"]
        out.append(p)
    return out
