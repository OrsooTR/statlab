"""Prediction orchestrator.

A LeagueEngine per league:
  1. loads the match store and builds the leakage-free feature dataset;
  2. fits the four statistical models and three ML classifiers on a time-ordered
     training slice, then fits ensemble weights on the held-out validation tail
     by log-loss minimisation;
  3. refits every member on the full history for live prediction;
  4. serves calibrated ensemble predictions with a Dixon-Coles score grid,
     a 10,000-draw Monte Carlo market simulation, reasoning and risk.

Engines are memoised per league and invalidated when the match store changes.
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any, Optional

import numpy as np

from . import database as store
from .features import FeatureBuilder, build_dataset, _to_date
from .models import DixonColesModel, EloModel, MLModels, PoissonModel, SPIModel
from .models.ensemble import ALL_MODELS, blend, fit_weights
from .simulation import simulate_match

_engines: dict[str, "LeagueEngine"] = {}
_engines_lock = threading.Lock()

VALIDATION_FRACTION = 0.15


class LeagueEngine:
    def __init__(self, league: str) -> None:
        self.league = league
        self.matches = store.get_matches(league)
        if len(self.matches) < 100:
            raise ValueError(
                f"not enough match data for {league} — refresh the league data first "
                f"({len(self.matches)} matches in store)")
        self.fingerprint = (len(self.matches), self.matches[-1]["date"])
        self._fit()

    # ------------------------------------------------------------------ fitting
    def _fit(self) -> None:
        X, y, refs, _ = build_dataset(self.matches)
        n_val = max(30, int(len(refs) * VALIDATION_FRACTION))
        split_at = len(refs) - n_val
        train_refs = refs[:split_at]
        cut_date = train_refs[-1]["date"] if train_refs else None
        train_matches = [m for m in self.matches if m["date"] <= cut_date] if cut_date else []

        # 1. members fitted on the training window
        stat_train = {
            "poisson": PoissonModel().fit(train_matches),
            "dixon_coles": DixonColesModel().fit(train_matches),
            "elo": EloModel().fit(train_matches),
            "spi": SPIModel().fit(train_matches),
        }
        ml_train = MLModels().fit(X[:split_at], y[:split_at])

        # 2. validation probabilities → ensemble weights
        stack, names = [], []
        val_refs, val_y = refs[split_at:], y[split_at:]
        for name, model in stat_train.items():
            if not getattr(model, "fitted", False):
                continue
            P = np.array([[_pvec(model.predict(m["home"], m["away"]))] for m in val_refs])
            stack.append(P.reshape(len(val_refs), 3))
            names.append(name)
        if ml_train.fitted:
            batch = ml_train.predict_proba_batch(X[split_at:])
            for key, P in batch.items():
                stack.append(P)
                names.append(key)
        if stack:
            w = fit_weights(np.stack(stack), val_y)
            self.weights = {n: float(wi) for n, wi in zip(names, w)}
        else:
            self.weights = {n: 1.0 / len(ALL_MODELS) for n in ALL_MODELS}

        # 3. refit every member on the full history for live use
        self.models: dict[str, Any] = {
            "poisson": PoissonModel().fit(self.matches),
            "dixon_coles": DixonColesModel().fit(self.matches),
            "elo": EloModel().fit(self.matches),
            "spi": SPIModel().fit(self.matches),
        }
        self.ml = MLModels().fit(X, y)
        self.builder = _rebuild_state(self.matches)
        self.n_training_rows = len(refs)

    # --------------------------------------------------------------- prediction
    def predict(self, home: str, away: str, match_date: Optional[str] = None,
                odds: Optional[dict] = None, n_sims: int = 10_000) -> dict:
        when = _to_date(match_date) if match_date else date.today()
        model_probs: dict[str, np.ndarray] = {}
        model_detail: dict[str, dict] = {}
        for name, model in self.models.items():
            if getattr(model, "fitted", False):
                pred = model.predict(home, away)
                model_probs[name] = _pvec(pred)
                model_detail[name] = {k: round(float(v), 4) for k, v in pred.items()}
        feats = self.builder.features_for(
            home, away, when,
            (odds or {}).get("home"), (odds or {}).get("draw"), (odds or {}).get("away"))
        if feats is not None and self.ml.fitted:
            for key, p in self.ml.predict_proba(feats).items():
                model_probs[key] = p
                model_detail[key] = {"p_home": round(float(p[0]), 4),
                                     "p_draw": round(float(p[1]), 4),
                                     "p_away": round(float(p[2]), 4)}
        if not model_probs:
            raise ValueError("no model could produce a prediction — insufficient data")

        probs = blend(model_probs, self.weights)
        dc: DixonColesModel = self.models["dixon_coles"]
        score_grid = dc.score_matrix(home, away) if dc.fitted \
            else self.models["poisson"].score_matrix(home, away)
        mu_h, mu_a = (dc if dc.fitted else self.models["poisson"]).expected_goals(home, away)

        # blend-consistent grid: rescale grid outcome masses to the ensemble 1X2
        score_grid = _rescale_grid(score_grid, probs)

        sim = simulate_match(score_grid, n_sims=n_sims,
                             corner_rates=self._corner_rates(home, away),
                             card_rates=self._card_rates(home, away))

        disagreement = float(np.std([p[0] for p in model_probs.values()])
                             + np.std([p[2] for p in model_probs.values()]))
        entropy = float(-(probs * np.log(probs)).sum() / np.log(3))
        confidence = float(probs.max())
        risk = _risk_level(entropy, disagreement)
        top = sim["top_scorelines"][0]

        return {
            "league": self.league, "home": home, "away": away,
            "date": when.isoformat(),
            "probabilities": {"home": round(float(probs[0]), 4),
                              "draw": round(float(probs[1]), 4),
                              "away": round(float(probs[2]), 4)},
            "predicted_scoreline": top["score"],
            "scoreline_probability": round(top["probability"], 4),
            "confidence_pct": round(confidence * 100, 1),
            "risk": risk,
            "expected_goals": {"home": round(mu_h, 2), "away": round(mu_a, 2)},
            "alternatives": sim["top_scorelines"][1:5],
            "markets": {k: sim[k] for k in
                        ("btts", "clean_sheet_home", "clean_sheet_away",
                         "over_under", "goal_distribution")},
            "corners": sim.get("corners"),
            "cards": sim.get("cards"),
            "model_breakdown": model_detail,
            "ensemble_weights": {k: round(v, 4) for k, v in self.weights.items()},
            "model_disagreement": round(disagreement, 4),
            "reasoning": self._reasoning(home, away, when, probs, mu_h, mu_a),
            "disclaimer": ("Calibrated probability estimate, not a certainty. "
                           "Football outcomes are highly stochastic."),
        }

    def _corner_rates(self, home: str, away: str) -> Optional[tuple[float, float]]:
        th = self.builder.teams.get(home)
        ta = self.builder.teams.get(away)
        if not th or not ta:
            return None
        ch = [r["corners"] for r in th.recent if r["corners"] is not None]
        ca = [r["corners"] for r in ta.recent if r["corners"] is not None]
        if not ch or not ca:
            return None
        return (sum(ch) / len(ch), sum(ca) / len(ca))

    def _card_rates(self, home: str, away: str) -> Optional[tuple[float, float]]:
        th = self.builder.teams.get(home)
        ta = self.builder.teams.get(away)
        if not th or not ta:
            return None
        kh = [r["cards"] for r in th.recent if r["cards"] is not None]
        ka = [r["cards"] for r in ta.recent if r["cards"] is not None]
        if not kh or not ka:
            return None
        return (sum(kh) / len(kh), sum(ka) / len(ka))

    def _reasoning(self, home: str, away: str, when: date,
                   probs: np.ndarray, mu_h: float, mu_a: float) -> list[str]:
        out = []
        elo: EloModel = self.models["elo"]
        if elo.fitted:
            rh, ra = elo._r(home), elo._r(away)
            lead = home if rh >= ra else away
            out.append(f"Rating edge: {lead} leads by {abs(rh-ra):.0f} Elo points "
                       f"({home} {rh:.0f} vs {away} {ra:.0f}, home advantage ≈ 62).")
        th = self.builder.teams.get(home)
        ta = self.builder.teams.get(away)
        if th and ta and len(th.recent) >= 3 and len(ta.recent) >= 3:
            hp = sum(r["points"] for r in list(th.recent)[-5:]) / min(5, len(th.recent))
            ap = sum(r["points"] for r in list(ta.recent)[-5:]) / min(5, len(ta.recent))
            out.append(f"Form (last 5): {home} {hp:.2f} points per game vs {away} {ap:.2f}.")
            hx = [r["xgf"] for r in list(th.recent)[-5:] if r["xgf"] is not None]
            ax = [r["xgf"] for r in list(ta.recent)[-5:] if r["xgf"] is not None]
            if hx and ax:
                out.append(f"Shot quality (xG proxy, last 5): {home} {sum(hx)/len(hx):.2f} "
                           f"vs {away} {sum(ax)/len(ax):.2f} created per game.")
        key = tuple(sorted((home, away)))
        meetings = list(self.builder.h2h.get(key, []))
        if meetings:
            hw = sum(1 for m in meetings if (m["fthg"] > m["ftag"]) == (m["home"] == home)
                     and m["fthg"] != m["ftag"])
            dr = sum(1 for m in meetings if m["fthg"] == m["ftag"])
            out.append(f"Head-to-head (last {len(meetings)}): {hw} {home} wins, {dr} draws, "
                       f"{len(meetings)-hw-dr} {away} wins.")
        out.append(f"Model expected goals: {mu_h:.2f} – {mu_a:.2f}; "
                   f"ensemble outcome split {probs[0]*100:.0f}% / {probs[1]*100:.0f}% / {probs[2]*100:.0f}%.")
        return out


def _pvec(pred: dict) -> np.ndarray:
    v = np.array([pred["p_home"], pred["p_draw"], pred["p_away"]])
    v = np.clip(v, 1e-9, 1)
    return v / v.sum()


def _rescale_grid(grid: np.ndarray, probs: np.ndarray) -> np.ndarray:
    """Rescale the score grid so its 1X2 masses equal the ensemble blend."""
    size = grid.shape[0]
    home_mask = np.tril(np.ones_like(grid), -1)
    draw_mask = np.eye(size)
    away_mask = np.triu(np.ones_like(grid), 1)
    out = grid.copy()
    for mask, target in ((home_mask, probs[0]), (draw_mask, probs[1]), (away_mask, probs[2])):
        mass = float((grid * mask).sum())
        if mass > 1e-12:
            out += (target / mass - 1.0) * grid * mask
    return out / out.sum()


def _risk_level(entropy: float, disagreement: float) -> str:
    score = entropy + disagreement * 2
    if score < 0.82:
        return "low"
    if score < 1.02:
        return "medium"
    return "high"


def _rebuild_state(matches: list[dict]) -> FeatureBuilder:
    fb = FeatureBuilder()
    for m in matches:
        fb.update(m)
    return fb


def get_engine(league: str) -> LeagueEngine:
    """Memoised engine per league, invalidated when the store changes."""
    latest = store.get_matches(league)
    fp = (len(latest), latest[-1]["date"]) if latest else (0, None)
    with _engines_lock:
        eng = _engines.get(league)
        if eng is not None and eng.fingerprint == fp:
            return eng
    eng = LeagueEngine(league)
    with _engines_lock:
        _engines[league] = eng
    return eng
