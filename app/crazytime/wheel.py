"""Crazy Time wheel model.

Loads the config-driven wheel definition and exposes:
- segment layout, probabilities and paytable
- the Top Slot distribution, with the target-spot probabilities SOLVED from the
  published per-spot RTP targets in the config (Evolution's reel weighting is
  proprietary; this calibration reproduces the published RTP of every bet spot)
- analytic + Monte Carlo RTP per bet spot

The eight bet spots are indexed in a fixed order used across the whole module:
  0:"1"  1:"2"  2:"5"  3:"10"  4:coin_flip  5:cash_hunt  6:pachinko  7:crazy_time
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np

CONFIG_PATH = Path(__file__).with_name("wheel_config.json")

SPOT_KEYS = ["1", "2", "5", "10", "coin_flip", "cash_hunt", "pachinko", "crazy_time"]
SPOT_INDEX = {k: i for i, k in enumerate(SPOT_KEYS)}
NUMBER_SPOTS = [0, 1, 2, 3]
BONUS_SPOTS = [4, 5, 6, 7]


@lru_cache(maxsize=1)
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


class Wheel:
    """Immutable wheel definition derived from the config file."""

    def __init__(self, config: dict | None = None) -> None:
        cfg = config or load_config()
        self.config = cfg
        segs = cfg["wheel"]["segments"]
        counts = {s["key"]: int(s["count"]) for s in segs}
        if set(counts) != set(SPOT_KEYS):
            raise ValueError("wheel_config segments must define exactly the 8 bet spots")
        self.counts = np.array([counts[k] for k in SPOT_KEYS], dtype=np.int64)
        self.total_segments = int(self.counts.sum())
        self.probs = self.counts / self.total_segments
        pays = {s["key"]: float(s.get("pays", 0.0)) for s in segs}
        # pays[i] is the win multiple for number spots (bonus spots resolve dynamically)
        self.pays = np.array([pays[k] for k in SPOT_KEYS], dtype=np.float64)

        layout = cfg["wheel"].get("layout")
        if layout:
            if len(layout) != self.total_segments:
                raise ValueError("wheel layout length must equal total segments")
            from collections import Counter
            lc = Counter(layout)
            for i, key in enumerate(SPOT_KEYS):
                if lc.get(key, 0) != int(self.counts[i]):
                    raise ValueError(f"wheel layout count mismatch for '{key}'")
            self.layout = list(layout)
        else:
            self.layout = [k for i, k in enumerate(SPOT_KEYS) for _ in range(int(self.counts[i]))]

        ts = cfg["top_slot"]
        self.ts_mults = np.array(ts["multipliers"], dtype=np.float64)
        w = np.array(ts["weights"], dtype=np.float64)
        self.ts_weights = w / w.sum()
        self.ts_mean = float((self.ts_mults * self.ts_weights).sum())
        self.ts_target_probs = self._solve_target_probs(cfg)

    def _solve_target_probs(self, cfg: dict) -> np.ndarray:
        """Solve the Top Slot target-spot distribution from RTP targets.

        For a number spot i with segment probability p_i and base payout b_i,
        a Top Slot hit multiplies the payout, so:
            RTP_i = p_i * (1 + b_i * ((1 - q_i) + q_i * m))
        where q_i is the probability the Top Slot targets spot i and m is the
        mean Top Slot multiplier. Solving for q_i:
            q_i = (F_i - 1) / (m - 1),  F_i = (RTP_i / p_i - 1) / b_i
        The remaining probability mass is split across the bonus spots in
        proportion to their segment counts.
        """
        targets = cfg["rtp_targets"]
        m = self.ts_mean
        q = np.zeros(8)
        for i in NUMBER_SPOTS:
            key = SPOT_KEYS[i]
            f = (targets[key] / self.probs[i] - 1.0) / self.pays[i]
            q[i] = (f - 1.0) / (m - 1.0)
            if q[i] <= 0:
                raise ValueError(f"RTP target for '{key}' unreachable with this Top Slot table")
        remainder = 1.0 - q[NUMBER_SPOTS].sum()
        if remainder <= 0:
            raise ValueError("Top Slot number targets exceed probability 1; raise the mean multiplier")
        bonus_counts = self.counts[BONUS_SPOTS].astype(np.float64)
        q[BONUS_SPOTS] = remainder * bonus_counts / bonus_counts.sum()
        return q

    def ts_boost(self, spot: int) -> float:
        """Expected multiplicative payout boost from the Top Slot for a spot."""
        q = float(self.ts_target_probs[spot])
        return (1.0 - q) + q * self.ts_mean

    # -- descriptive ----------------------------------------------------------
    def describe(self) -> dict:
        """Full statistical description consumed by the UI and reports.

        Bonus expectations are Monte Carlo estimates (200k rounds each),
        cached on the instance — strategies query this during setup.
        """
        cached = getattr(self, "_describe_cache", None)
        if cached is not None:
            return cached
        from .bonus_games import expected_bonus_multipliers  # local import: avoid cycle

        bonus_ev = expected_bonus_multipliers(self.config, n=200_000, seed=7)
        targets = self.config.get("rtp_targets", {})
        spots = []
        for i, key in enumerate(SPOT_KEYS):
            p = float(self.probs[i])
            boost = self.ts_boost(i)
            if i in NUMBER_SPOTS:
                mean_pay = float(self.pays[i] * boost)
            else:
                mean_pay = float(bonus_ev[key] * boost)
            rtp = p * (1 + mean_pay)
            spots.append({
                "key": key,
                "segments": int(self.counts[i]),
                "probability": p,
                "base_pays": float(self.pays[i]) if i in NUMBER_SPOTS else None,
                "mean_payout_multiple": mean_pay,
                "rtp": rtp,
                "rtp_target": targets.get(key),
                "expected_loss_per_unit": 1 - rtp,
                "top_slot_target_prob": float(self.ts_target_probs[i]),
            })
        result = {
            "total_segments": self.total_segments,
            "spots": spots,
            "top_slot": {
                "multipliers": self.ts_mults.tolist(),
                "probabilities": self.ts_weights.tolist(),
                "mean_multiplier": self.ts_mean,
                "target_spot_probabilities": self.ts_target_probs.tolist(),
            },
            "table": self.config["table"],
        }
        self._describe_cache = result
        return result
