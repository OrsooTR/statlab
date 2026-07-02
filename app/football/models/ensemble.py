"""Log-loss-optimal ensemble blending.

Every member model contributes an (H, D, A) probability vector; the blend
weights live on the simplex (softmax parametrisation) and are fitted on a
held-out, time-ordered validation window by minimising multinomial log loss
with L-BFGS. Falls back to uniform weights when validation data is thin.
"""
from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

STAT_MODELS = ["poisson", "dixon_coles", "elo", "spi"]
ML_MODELS = ["ml_gbm", "ml_rf", "ml_mlp"]
ALL_MODELS = STAT_MODELS + ML_MODELS


def _softmax(z: np.ndarray) -> np.ndarray:
    e = np.exp(z - z.max())
    return e / e.sum()


def fit_weights(prob_stack: np.ndarray, y: np.ndarray) -> np.ndarray:
    """prob_stack: (n_models, n_samples, 3); y: (n_samples,) in {0,1,2}."""
    k, n, _ = prob_stack.shape
    if n < 30:
        return np.ones(k) / k
    idx = np.arange(n)

    def neg_ll(z: np.ndarray) -> float:
        w = _softmax(z)
        blend = np.tensordot(w, prob_stack, axes=1)          # (n, 3)
        p = np.clip(blend[idx, y], 1e-12, 1.0)
        return -np.log(p).mean()

    best = None
    for start in (np.zeros(k), np.log(np.full(k, 1.0 / k)) + np.random.default_rng(3).normal(0, 0.3, k)):
        res = minimize(neg_ll, start, method="L-BFGS-B")
        if best is None or res.fun < best.fun:
            best = res
    return _softmax(best.x)


def blend(model_probs: dict[str, np.ndarray], weights: dict[str, float]) -> np.ndarray:
    """Weighted blend of whichever member models produced a probability vector."""
    total = np.zeros(3)
    wsum = 0.0
    for name, p in model_probs.items():
        w = weights.get(name, 0.0)
        if w > 0:
            total += w * np.asarray(p)
            wsum += w
    if wsum <= 0:  # no weighted member available: plain average
        ps = list(model_probs.values())
        total = np.mean(ps, axis=0)
        wsum = 1.0
    out = total / wsum
    out = np.clip(out, 1e-9, 1.0)
    return out / out.sum()
