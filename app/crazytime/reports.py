"""Report builders for Crazy Time simulations (PDF / Excel / CSV / JSON)."""
from __future__ import annotations

import json
from typing import Any

from ..core import exports
from ..core.database import read_conn, rows_to_dicts

SUMMARY_FIELDS = [
    ("mean_final_balance", "Mean final balance"),
    ("median_final_balance", "Median final balance"),
    ("best_final_balance", "Best final balance"),
    ("worst_final_balance", "Worst final balance"),
    ("mean_roi", "Mean ROI"),
    ("prob_profit", "P(profit)"),
    ("risk_of_ruin", "Risk of ruin"),
    ("mean_max_drawdown", "Mean max drawdown"),
    ("mean_avg_drawdown", "Mean avg drawdown"),
    ("mean_rtp_achieved", "Mean RTP achieved"),
    ("mean_bonus_frequency", "Bonus frequency"),
    ("mean_bonus_contribution", "Bonus contribution"),
    ("mean_volatility", "Volatility / spin"),
    ("mean_ev_per_spin", "EV / spin"),
    ("longest_winning_streak", "Longest win streak"),
    ("longest_losing_streak", "Longest lose streak"),
]


def load_simulations(ids: list[int]) -> list[dict[str, Any]]:
    with read_conn() as conn:
        marks = ",".join("?" * len(ids))
        rows = conn.execute(
            f"SELECT * FROM ct_simulations WHERE id IN ({marks}) ORDER BY id", ids
        ).fetchall()
    sims = rows_to_dicts(rows)
    for s in sims:
        s["results"] = json.loads(s.pop("results_json"))
        s["params"] = json.loads(s.pop("params_json"))
    return sims


def _blocks(sims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [
        {"type": "title", "text": "Crazy Time Strategy Simulation Report"},
        {"type": "paragraph",
         "text": ("Statistical simulator output. Every spin is independent and random; "
                  "no betting strategy changes the house edge. Metrics below describe the "
                  "risk profile of each staking plan under identical wheel behaviour.")},
    ]
    if len(sims) > 1:
        cols = ["Strategy", "Spins", "Runs", "Bankroll", "Mean final", "Mean ROI",
                "P(profit)", "Risk of ruin", "Max DD", "RTP"]
        rows = []
        for s in sims:
            r = s["results"]
            rows.append([s["name"], s["spins"], s["runs"], s["bankroll"],
                         round(r["mean_final_balance"], 2), f"{r['mean_roi']*100:.2f}%",
                         f"{r['prob_profit']*100:.1f}%", f"{r['risk_of_ruin']*100:.1f}%",
                         f"{r['mean_max_drawdown']*100:.1f}%",
                         f"{r['mean_rtp_achieved']*100:.2f}%"])
        blocks.append({"type": "heading", "text": "Strategy comparison"})
        blocks.append({"type": "table", "columns": cols, "rows": rows})
    for s in sims:
        r = s["results"]
        blocks.append({"type": "heading",
                       "text": f"{s['name']} — {s['strategy']} ({s['spins']:,} spins × {s['runs']} runs)"})
        items = [["Bankroll", s["bankroll"]], ["Parameters", json.dumps(s["params"])]]
        for key, label in SUMMARY_FIELDS:
            if key in r:
                v = r[key]
                if isinstance(v, float) and ("roi" in key or "drawdown" in key or "prob" in key
                                             or "risk" in key or "rtp" in key or "frequency" in key
                                             or "contribution" in key):
                    v = f"{v*100:.2f}%"
                items.append([label, v])
        blocks.append({"type": "kv", "items": items})
    return blocks


def export_simulations(ids: list[int], fmt: str) -> str:
    sims = load_simulations(ids)
    if not sims:
        raise ValueError("no simulations found for the given ids")
    if fmt == "pdf":
        return str(exports.export_pdf("crazytime-report", _blocks(sims)))
    if fmt == "xlsx":
        sheets = {"Summary": _blocks(sims)}
        for s in sims:
            r = s["results"]
            curve = r["representative_run"]["balance_curve"]
            sheets[f"Curve {s['id']}"[:31]] = [
                {"type": "heading", "text": f"{s['name']} representative balance curve"},
                {"type": "table", "columns": ["point", "balance"],
                 "rows": [[i, v] for i, v in enumerate(curve)]},
            ]
        return str(exports.export_xlsx("crazytime-report", sheets))
    if fmt == "csv":
        cols = ["id", "name", "strategy", "spins", "runs", "bankroll"] + [k for k, _ in SUMMARY_FIELDS]
        rows = [[s["id"], s["name"], s["strategy"], s["spins"], s["runs"], s["bankroll"]]
                + [s["results"].get(k) for k, _ in SUMMARY_FIELDS] for s in sims]
        return str(exports.export_csv("crazytime-report", cols, rows))
    if fmt == "json":
        return str(exports.export_json("crazytime-report", sims))
    raise ValueError(f"unsupported format: {fmt}")
