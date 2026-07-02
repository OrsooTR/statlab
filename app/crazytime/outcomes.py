"""Vectorised pre-generation of complete spin outcomes.

For n spins this produces, entirely in NumPy:
  result   — winning bet spot index (0..7)
  pay      — payout multiple (winnings per unit staked on the winning spot;
             the stake itself is returned on top, i.e. total return = stake*(1+pay))
  ts_spot  — Top Slot target spot for the spin
  ts_mult  — Top Slot multiplier for the spin

Every game mechanic is resolved here: wheel distribution, Top Slot matching,
and all four bonus games with their own randomness. The strategy engine then
only has to walk the bankroll through these arrays.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .bonus_games import BONUS_RESOLVERS
from .wheel import NUMBER_SPOTS, SPOT_INDEX, SPOT_KEYS, Wheel


@dataclass
class SpinOutcomes:
    result: np.ndarray   # int8   (n,)
    pay: np.ndarray      # float64(n,)
    ts_spot: np.ndarray  # int8   (n,)
    ts_mult: np.ndarray  # float64(n,)

    def __len__(self) -> int:
        return len(self.result)


def generate(wheel: Wheel, n: int, rng: np.random.Generator) -> SpinOutcomes:
    cfg = wheel.config

    # 1. Wheel spins — independent draws over the 54-segment distribution.
    result = rng.choice(8, size=n, p=wheel.probs).astype(np.int8)

    # 2. Top Slot — target spot drawn from the RTP-calibrated distribution,
    #    multiplier from the weighted table.
    ts_spot = rng.choice(8, size=n, p=wheel.ts_target_probs).astype(np.int8)
    ts_mult = rng.choice(wheel.ts_mults, size=n, p=wheel.ts_weights)
    ts_matched = ts_spot == result
    ts_factor = np.where(ts_matched, ts_mult, 1.0)

    # 3. Payouts.
    pay = np.zeros(n, dtype=np.float64)
    for i in NUMBER_SPOTS:
        mask = result == i
        pay[mask] = wheel.pays[i] * ts_factor[mask]
    for key, fn in BONUS_RESOLVERS.items():
        i = SPOT_INDEX[key]
        mask = result == i
        cnt = int(mask.sum())
        if cnt:
            pay[mask] = fn(rng, cfg, ts_factor[mask])

    return SpinOutcomes(result=result, pay=pay, ts_spot=ts_spot, ts_mult=ts_mult)


def spot_name(i: int) -> str:
    return SPOT_KEYS[i]
