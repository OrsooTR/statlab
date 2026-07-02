"""Walk-forward backtesting engine.

For every test season the models are fitted strictly on prior data, then walk
the season chronologically, refitting on an expanding window every REFIT_DAYS.
Ensemble weights come from the pre-season validation tail (no look-ahead).

Outputs: accuracy, log loss, Brier score, calibration curve, exact-score hit
rate, and a value-betting simulation against Bet365 opening odds with ROI,
yield, drawdown and Closing Line Value where closing odds exist.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Optional

import numpy as np

from . import database as store
from .features import FeatureBuilder, build_dataset, _to_date
from .models import DixonColesModel, EloModel, MLModels, PoissonModel, SPIModel
from .models.ensemble import blend, fit_weights

REFIT_DAYS = 30
VALUE_THRESHOLD = 1.06   # bet when model_p × odds exceeds this
STAKE = 1.0

OUTCOMES = ["H", "D", "A"]


def run_backtest(progress, league: str, test_seasons: list[str],
                 value_threshold: float = VALUE_THRESHOLD) -> dict:
    all_matches = store.get_matches(league)
    if len(all_matches) < 300:
        raise ValueError(f"not enough data for a backtest on {league}")
    seasons = store.get_seasons(league)
    test_seasons = [s for s in test_seasons if s in seasons]
    if not test_seasons:
        raise ValueError("none of the requested seasons exist in the store")

    records: list[dict] = []
    for si, season in enumerate(sorted(test_seasons)):
        season_matches = [m for m in all_matches if m["season"] == season]
        history = [m for m in all_matches if m["date"] < season_matches[0]["date"]]
        if len(history) < 200:
            continue
        records.extend(_walk_season(
            lambda f, msg, si=si: progress((si + f) / len(test_seasons), msg),
            history, season_matches, season))
    if not records:
        raise ValueError("backtest produced no evaluable matches")
    return _score(records, value_threshold)


def _walk_season(progress, history: list[dict], season_matches: list[dict],
                 season: str) -> list[dict]:
    # ensemble weights from the last 15% of history (time-ordered validation)
    X_h, y_h, refs_h, _ = build_dataset(history)
    n_val = max(30, int(len(refs_h) * 0.15))
    split = len(refs_h) - n_val
    stat_val = _fit_stats([m for m in history if m["date"] <= refs_h[split - 1]["date"]])
    ml_val = MLModels().fit(X_h[:split], y_h[:split])
    stack, names = [], []
    for name, model in stat_val.items():
        if getattr(model, "fitted", False):
            P = np.array([_pvec(model.predict(m["home"], m["away"])) for m in refs_h[split:]])
            stack.append(P)
            names.append(name)
    if ml_val.fitted:
        for key, P in ml_val.predict_proba_batch(X_h[split:]).items():
            stack.append(P)
            names.append(key)
    weights = ({n: float(w) for n, w in zip(names, fit_weights(np.stack(stack), y_h[split:]))}
               if stack else {})

    # expanding-window walk with periodic refits
    window = list(history)
    models = _fit_stats(window)
    X_all, y_all, _, _ = build_dataset(window)
    ml = MLModels().fit(X_all, y_all)
    builder = _state(window)
    last_refit = _to_date(window[-1]["date"])

    out = []
    for i, m in enumerate(season_matches):
        if m.get("fthg") is None or m.get("ftr") not in OUTCOMES:
            continue
        d = _to_date(m["date"])
        if (d - last_refit).days >= REFIT_DAYS:
            models = _fit_stats(window)
            X_all, y_all, _, _ = build_dataset(window)
            ml = MLModels().fit(X_all, y_all)
            builder = _state(window)
            last_refit = d
            progress(i / len(season_matches), f"{season}: refit at {d.isoformat()}")

        model_probs = {}
        for name, model in models.items():
            if getattr(model, "fitted", False):
                model_probs[name] = _pvec(model.predict(m["home"], m["away"]))
        feats = builder.features_for(m["home"], m["away"], d,
                                     m.get("b365h"), m.get("b365d"), m.get("b365a"))
        if feats is not None and ml.fitted:
            model_probs.update(ml.predict_proba(feats))
        if not model_probs:
            window.append(m)
            builder.update(m)
            continue
        probs = blend(model_probs, weights)
        dc = models.get("dixon_coles")
        pred_score = None
        if dc is not None and dc.fitted:
            grid = dc.score_matrix(m["home"], m["away"])
            k = int(np.argmax(grid))
            pred_score = f"{k // grid.shape[0]}-{k % grid.shape[0]}"
        out.append({
            "season": season, "date": m["date"], "home": m["home"], "away": m["away"],
            "probs": probs.tolist(), "result": m["ftr"],
            "actual_score": f"{m['fthg']}-{m['ftag']}",
            "pred_score": pred_score,
            "odds": [m.get("b365h"), m.get("b365d"), m.get("b365a")],
            "closing": [m.get("b365ch"), m.get("b365cd"), m.get("b365ca")],
        })
        window.append(m)
        builder.update(m)
    return out


def _fit_stats(matches: list[dict]) -> dict:
    return {"poisson": PoissonModel().fit(matches),
            "dixon_coles": DixonColesModel().fit(matches),
            "elo": EloModel().fit(matches),
            "spi": SPIModel().fit(matches)}


def _state(matches: list[dict]) -> FeatureBuilder:
    fb = FeatureBuilder()
    for m in matches:
        fb.update(m)
    return fb


def _pvec(pred: dict) -> np.ndarray:
    v = np.array([pred["p_home"], pred["p_draw"], pred["p_away"]])
    v = np.clip(v, 1e-9, 1)
    return v / v.sum()


# ------------------------------------------------------------------- scoring
def _score(records: list[dict], value_threshold: float) -> dict:
    y = np.array([OUTCOMES.index(r["result"]) for r in records])
    P = np.array([r["probs"] for r in records])
    n = len(records)

    pred = P.argmax(axis=1)
    accuracy = float((pred == y).mean())
    p_true = np.clip(P[np.arange(n), y], 1e-12, 1)
    log_loss = float(-np.log(p_true).mean())
    onehot = np.zeros_like(P)
    onehot[np.arange(n), y] = 1.0
    brier = float(((P - onehot) ** 2).sum(axis=1).mean())
    score_hits = sum(1 for r in records if r["pred_score"] and r["pred_score"] == r["actual_score"])

    # calibration over all outcome-probability pairs
    flat_p = P.ravel()
    flat_o = onehot.ravel()
    bins = np.linspace(0, 1, 11)
    calibration = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        mask = (flat_p >= lo) & (flat_p < hi)
        if mask.sum() >= 10:
            calibration.append({"bin_mid": round(float((lo + hi) / 2), 3),
                                "predicted": round(float(flat_p[mask].mean()), 4),
                                "observed": round(float(flat_o[mask].mean()), 4),
                                "count": int(mask.sum())})

    # value-betting simulation vs opening odds
    bankroll, peak, max_dd = 0.0, 0.0, 0.0
    staked, pnl, bets, wins = 0.0, 0.0, 0, 0
    clv_sum, clv_n = 0.0, 0
    curve = []
    for r, yi in zip(records, y):
        odds = r["odds"]
        best_ev, pick = 0.0, None
        for k in range(3):
            if odds[k] and odds[k] > 1:
                ev = r["probs"][k] * odds[k]
                if ev > best_ev:
                    best_ev, pick = ev, k
        if pick is not None and best_ev >= value_threshold:
            bets += 1
            staked += STAKE
            won = STAKE * (odds[pick] - 1) if pick == yi else -STAKE
            wins += 1 if pick == yi else 0
            pnl += won
            bankroll += won
            closing = r["closing"][pick]
            if closing and closing > 1:
                clv_sum += odds[pick] / closing - 1
                clv_n += 1
        peak = max(peak, bankroll)
        max_dd = max(max_dd, peak - bankroll)
        curve.append(round(bankroll, 3))

    return {
        "matches_evaluated": n,
        "accuracy": round(accuracy, 4),
        "log_loss": round(log_loss, 4),
        "brier_score": round(brier, 4),
        "exact_score_hit_rate": round(score_hits / n, 4),
        "calibration": calibration,
        "betting": {
            "value_threshold": value_threshold,
            "bets_placed": bets,
            "hit_rate": round(wins / bets, 4) if bets else None,
            "total_staked": round(staked, 2),
            "profit": round(pnl, 3),
            "roi": round(pnl / staked, 4) if staked else None,
            "yield_pct": round(pnl / staked * 100, 2) if staked else None,
            "max_drawdown": round(max_dd, 3),
            "closing_line_value": round(clv_sum / clv_n, 4) if clv_n else None,
            "clv_sample": clv_n,
            "bankroll_curve": curve[:: max(1, len(curve) // 500)],
        },
        "sample": [
            {k: r[k] for k in ("date", "home", "away", "result", "pred_score",
                               "actual_score", "probs")}
            for r in records[-20:]
        ],
    }
