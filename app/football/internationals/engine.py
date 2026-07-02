"""National-team prediction engine.

A time-decayed Poisson attack/defence model with explicit neutral-venue handling
(home advantage applies only to non-neutral matches), a goal-margin Elo with the
same neutrality logic, a Dixon-Coles low-score correction, and a Monte Carlo
market simulation. Tournament matches are up-weighted over friendlies.

Everything is fitted from the open international results dataset — real data,
computed live. Engines are memoised per (data fingerprint).
"""
from __future__ import annotations

import math
import threading
from datetime import date, datetime
from typing import Optional

import numpy as np

from ..models.dixon_coles import tau_matrix
from ..models.poisson import independent_score_matrix, outcome_probs
from ..simulation import simulate_match
from . import data as intl_data

DECAY_XI = 0.75          # per-year time decay (nations play sparsely)
FRIENDLY_WEIGHT = 0.6    # friendlies count less than competitive matches
SHRINK = 0.62            # shrink attack/defence toward 1 (ridge on sparse data)
MAX_GOALS = 10
ELO_K = 40.0
ELO_INIT = 1500.0


def _to_date(s) -> date:
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _is_friendly(t: str) -> bool:
    return "friendly" in (t or "").lower()


class NationalEngine:
    def __init__(self, matches: list[dict]) -> None:
        self.matches = matches
        self.fingerprint = (len(matches), matches[-1]["date"] if matches else "")
        self._fit()

    # ------------------------------------------------------------------ fitting
    def _fit(self) -> None:
        played = [m for m in self.matches if m.get("fthg") is not None]
        ref = max(_to_date(m["date"]) for m in played)
        teams = sorted({m["home"] for m in played} | {m["away"] for m in played})
        idx = {t: i for i, t in enumerate(teams)}
        n = len(teams)
        h = np.array([idx[m["home"]] for m in played])
        a = np.array([idx[m["away"]] for m in played])
        gh = np.array([m["fthg"] for m in played], dtype=np.float64)
        ga = np.array([m["ftag"] for m in played], dtype=np.float64)
        neutral = np.array([m["neutral"] for m in played], dtype=bool)
        w = np.array([
            math.exp(-DECAY_XI * (ref - _to_date(m["date"])).days / 365.25)
            * (FRIENDLY_WEIGHT if _is_friendly(m["tournament"]) else 1.0)
            for m in played])

        atk = np.ones(n)
        dfn = np.ones(n)
        home_adv = 1.25
        mean = float((w * (gh + ga)).sum() / (2 * w.sum()))
        for _ in range(30):
            hf = np.where(neutral, 1.0, home_adv)
            mu_h = mean * atk[h] * dfn[a] * hf
            mu_a = mean * atk[a] * dfn[h]
            num = np.bincount(h, weights=w * gh, minlength=n) + np.bincount(a, weights=w * ga, minlength=n)
            den = np.bincount(h, weights=w * mu_h, minlength=n) + np.bincount(a, weights=w * mu_a, minlength=n)
            atk *= np.where(den > 0, num / np.maximum(den, 1e-9), 1.0)
            atk /= atk.mean()
            hf = np.where(neutral, 1.0, home_adv)
            mu_h = mean * atk[h] * dfn[a] * hf
            mu_a = mean * atk[a] * dfn[h]
            num = np.bincount(a, weights=w * gh, minlength=n) + np.bincount(h, weights=w * ga, minlength=n)
            den = np.bincount(a, weights=w * mu_h, minlength=n) + np.bincount(h, weights=w * mu_a, minlength=n)
            dfn *= np.where(den > 0, num / np.maximum(den, 1e-9), 1.0)
            dfn /= dfn.mean()
            # home advantage from non-neutral matches only
            nn = ~neutral
            if nn.any():
                mu_h = mean * atk[h] * dfn[a] * home_adv
                exp_home_nn = (w[nn] * mu_h[nn]).sum()
                act_home_nn = (w[nn] * gh[nn]).sum()
                home_adv *= float(act_home_nn / max(exp_home_nn, 1e-9))
                home_adv = min(max(home_adv, 1.0), 1.42)

        # shrink toward 1 on the log scale to tame extremes from sparse minnow
        # data, then renormalise to unit mean
        atk = atk ** SHRINK
        atk /= atk.mean()
        dfn = dfn ** SHRINK
        dfn /= dfn.mean()
        self.attack = {t: float(atk[i]) for t, i in idx.items()}
        self.defence = {t: float(dfn[i]) for t, i in idx.items()}
        self.home_adv = float(home_adv)
        self.mean = mean
        self.rho = self._fit_rho(played, ref)
        self.elo = self._fit_elo(played)

    def _fit_rho(self, played: list[dict], ref: date) -> float:
        rows = []
        for m in played:
            gh, ga = m["fthg"], m["ftag"]
            if gh > 1 or ga > 1:
                continue
            mu_h, mu_a = self.expected_goals(m["home"], m["away"], m["neutral"])
            w = math.exp(-DECAY_XI * (ref - _to_date(m["date"])).days / 365.25)
            rows.append((gh, ga, mu_h, mu_a, w))
        best_rho, best_ll = 0.0, -np.inf
        for rho in np.linspace(-0.2, 0.1, 61):
            ll, ok = 0.0, True
            for gh, ga, mu_h, mu_a, wt in rows:
                if gh == 0 and ga == 0:
                    t = 1 - mu_h * mu_a * rho
                elif gh == 0 and ga == 1:
                    t = 1 + mu_h * rho
                elif gh == 1 and ga == 0:
                    t = 1 + mu_a * rho
                else:
                    t = 1 - rho
                if t <= 0:
                    ok = False
                    break
                ll += wt * math.log(t)
            if ok and ll > best_ll:
                best_ll, best_rho = ll, float(rho)
        return best_rho

    def _fit_elo(self, played: list[dict]) -> dict[str, float]:
        r: dict[str, float] = {}
        for m in played:
            home, away = m["home"], m["away"]
            rh, ra = r.get(home, ELO_INIT), r.get(away, ELO_INIT)
            adv = 0.0 if m["neutral"] else 65.0
            exp = 1.0 / (1.0 + 10 ** (-(rh + adv - ra) / 400.0))
            gh, ga = m["fthg"], m["ftag"]
            score = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
            mult = math.log(abs(gh - ga) + 1.0)
            k = ELO_K * (1.5 if not _is_friendly(m["tournament"]) else 0.8)
            delta = k * mult * (score - exp)
            r[home] = rh + delta
            r[away] = ra - delta
        return r

    # ---------------------------------------------------------------- predicting
    def expected_goals(self, home: str, away: str, neutral: bool) -> tuple[float, float]:
        ah, dh = self.attack.get(home, 1.0), self.defence.get(home, 1.0)
        aa, da = self.attack.get(away, 1.0), self.defence.get(away, 1.0)
        hadv = 1.0 if neutral else self.home_adv
        mu_h = self.mean * ah * da * hadv
        mu_a = self.mean * aa * dh
        return max(0.15, min(mu_h, 6.0)), max(0.15, min(mu_a, 6.0))

    def known(self, team: str) -> bool:
        return team in self.attack

    def elo_of(self, team: str) -> float:
        return self.elo.get(team, ELO_INIT)

    def predict(self, home: str, away: str, neutral: bool = True,
                n_sims: int = 10_000) -> dict:
        mu_h, mu_a = self.expected_goals(home, away, neutral)
        grid = independent_score_matrix(mu_h, mu_a, MAX_GOALS)
        grid = grid * tau_matrix(mu_h, mu_a, self.rho, MAX_GOALS)
        grid /= grid.sum()

        # Elo cross-check blended into the 1X2 for robustness
        adv = 0.0 if neutral else 65.0
        elo_ph = 1.0 / (1.0 + 10 ** (-(self.elo_of(home) + adv - self.elo_of(away)) / 400.0))
        pph, ppd, ppa = outcome_probs(grid)
        # convert elo expected score into a soft 1X2 with a draw band
        draw = ppd
        elo_home = elo_ph * (1 - draw)
        elo_away = (1 - elo_ph) * (1 - draw)
        w_elo = 0.35
        ph = (1 - w_elo) * pph + w_elo * elo_home
        pd_ = (1 - w_elo) * ppd + w_elo * draw
        pa = (1 - w_elo) * ppa + w_elo * elo_away
        s = ph + pd_ + pa
        probs = np.array([ph / s, pd_ / s, pa / s])
        grid = _rescale_grid(grid, probs)

        sim = simulate_match(grid, n_sims=n_sims, seed=None)
        top = sim["top_scorelines"][0]
        entropy = float(-(probs * np.log(probs)).sum() / np.log(3))
        return {
            "home": home, "away": away, "neutral": neutral,
            "probabilities": {"home": round(float(probs[0]), 4),
                              "draw": round(float(probs[1]), 4),
                              "away": round(float(probs[2]), 4)},
            "predicted_scoreline": top["score"],
            "scoreline_probability": round(top["probability"], 4),
            "confidence_pct": round(float(probs.max()) * 100, 1),
            "risk": "low" if entropy < 0.8 else ("medium" if entropy < 1.0 else "high"),
            "expected_goals": {"home": round(mu_h, 2), "away": round(mu_a, 2)},
            "alternatives": sim["top_scorelines"][1:5],
            "markets": {k: sim[k] for k in
                        ("btts", "clean_sheet_home", "clean_sheet_away",
                         "over_under", "goal_distribution")},
            "ratings": {"home_elo": round(self.elo_of(home)),
                        "away_elo": round(self.elo_of(away))},
            "score_grid": grid.tolist(),
        }


def _rescale_grid(grid: np.ndarray, probs: np.ndarray) -> np.ndarray:
    size = grid.shape[0]
    masks = (np.tril(np.ones_like(grid), -1), np.eye(size), np.triu(np.ones_like(grid), 1))
    out = grid.copy()
    for mask, target in zip(masks, probs):
        mass = float((grid * mask).sum())
        if mass > 1e-12:
            out += (target / mass - 1.0) * grid * mask
    return out / out.sum()


# --------------------------------------------------------------- engine cache
_engine: Optional[NationalEngine] = None
_engine_lock = threading.Lock()


def get_engine() -> NationalEngine:
    matches = intl_data.load_results()
    if len(matches) < 500:
        raise ValueError("international results dataset unavailable")
    fp = (len(matches), matches[-1]["date"])
    with _engine_lock:
        global _engine
        if _engine is not None and _engine.fingerprint == fp:
            return _engine
    eng = NationalEngine(matches)
    with _engine_lock:
        _engine = eng
    return eng
