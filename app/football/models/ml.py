"""Machine-learning classifiers over the engineered feature set.

Three members: gradient boosting, random forest and an MLP neural network,
each producing calibrated (H, D, A) probabilities. Calibration uses sigmoid
(Platt) scaling with time-ordered folds when enough data is available.
"""
from __future__ import annotations

import warnings
from typing import Optional

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import HistGradientBoostingClassifier, RandomForestClassifier
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

MIN_TRAIN = 300
MIN_CALIBRATE = 800


def _base_models() -> dict[str, object]:
    return {
        "gbm": HistGradientBoostingClassifier(
            max_iter=250, learning_rate=0.06, max_depth=4,
            l2_regularization=1.0, min_samples_leaf=40, random_state=11),
        "rf": RandomForestClassifier(
            n_estimators=300, max_depth=9, min_samples_leaf=25,
            max_features="sqrt", n_jobs=-1, random_state=11),
        "mlp": Pipeline([
            ("scale", StandardScaler()),
            ("net", MLPClassifier(hidden_layer_sizes=(64, 32), activation="relu",
                                  alpha=1e-3, learning_rate_init=1e-3, max_iter=400,
                                  early_stopping=True, n_iter_no_change=15,
                                  random_state=11)),
        ]),
    }


class MLModels:
    """Wrapper that fits/predicts all three classifiers as one unit."""

    names = ["ml_gbm", "ml_rf", "ml_mlp"]

    def __init__(self) -> None:
        self.models: dict[str, object] = {}
        self.fitted = False

    def fit(self, X: np.ndarray, y: np.ndarray) -> "MLModels":
        if len(X) < MIN_TRAIN or len(np.unique(y)) < 3:
            self.fitted = False
            return self
        self.models = {}
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            for key, model in _base_models().items():
                if len(X) >= MIN_CALIBRATE:
                    clf = CalibratedClassifierCV(model, method="sigmoid", cv=3)
                else:
                    clf = model
                clf.fit(X, y)
                self.models[key] = clf
        self.fitted = True
        return self

    def predict_proba(self, x: np.ndarray) -> Optional[dict[str, np.ndarray]]:
        """x: single feature vector → {'ml_gbm': [pH,pD,pA], ...}"""
        if not self.fitted:
            return None
        X = x.reshape(1, -1)
        out = {}
        for key, clf in self.models.items():
            p = clf.predict_proba(X)[0]
            # classes_ are [0,1,2] == [H,D,A] by construction of the labels
            out[f"ml_{key}"] = np.clip(p, 1e-6, 1.0) / np.clip(p, 1e-6, 1.0).sum()
        return out

    def predict_proba_batch(self, X: np.ndarray) -> Optional[dict[str, np.ndarray]]:
        if not self.fitted or len(X) == 0:
            return None
        out = {}
        for key, clf in self.models.items():
            P = clf.predict_proba(X)
            P = np.clip(P, 1e-6, 1.0)
            out[f"ml_{key}"] = P / P.sum(axis=1, keepdims=True)
        return out
