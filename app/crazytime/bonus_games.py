"""Bonus-game engines: Coin Flip, Cash Hunt, Pachinko, Crazy Time.

Every resolver is fully vectorised: given an RNG and a Top Slot factor array of
length n, it returns n independent bonus payout multiples (per unit staked).

Statistical-equivalence notes (documented, deliberate, and exact):

* Cash Hunt — the board holds `board_size` i.i.d. draws from the multiplier
  distribution, is shuffled, and the player's pick is uniform over the board.
  A uniform pick from a shuffled i.i.d. board is distributionally identical to a
  single draw from the same distribution, so the fast path draws once per event.
  `simulate_cash_hunt_detail` reproduces the full board for inspection.

* Pachinko — wall values are i.i.d.; the puck's landing position (uniform entry
  gate + bounded random walk over peg rows) is independent of the values, so the
  landed value is one draw from the distribution, and the chance of landing on a
  DOUBLE equals double_slots / wall_size. Doubling multiplies the whole wall
  (capped) and re-drops. `simulate_pachinko_detail` runs the explicit peg walk.

* Crazy Time — the three flappers land on i.i.d. uniform segments, so a player's
  random flapper choice is a uniform draw over the 64 segments. DOUBLE/TRIPLE
  rescale all values (capped) and respin.
"""
from __future__ import annotations

import numpy as np


def _dist(cfg: dict, key: str) -> tuple[np.ndarray, np.ndarray]:
    block = cfg[key]
    vals = np.asarray(block["multipliers"], dtype=np.float64)
    w = np.asarray(block["weights"], dtype=np.float64)
    return vals, w / w.sum()


# ------------------------------------------------------------------- Coin Flip
def resolve_coin_flip(rng: np.random.Generator, cfg: dict, ts_factor: np.ndarray) -> np.ndarray:
    n = len(ts_factor)
    vals, p = _dist(cfg, "coin_flip")
    cap = float(cfg["coin_flip"]["max_multiplier"])
    red = rng.choice(vals, size=n, p=p)
    blue = rng.choice(vals, size=n, p=p)
    flip = rng.random(n) < 0.5
    won = np.where(flip, red, blue)
    return np.minimum(won * ts_factor, cap)


# ------------------------------------------------------------------- Cash Hunt
def resolve_cash_hunt(rng: np.random.Generator, cfg: dict, ts_factor: np.ndarray) -> np.ndarray:
    n = len(ts_factor)
    vals, p = _dist(cfg, "cash_hunt")
    cap = float(cfg["cash_hunt"]["max_multiplier"])
    picked = rng.choice(vals, size=n, p=p)
    return np.minimum(picked * ts_factor, cap)


def simulate_cash_hunt_detail(rng: np.random.Generator, cfg: dict, ts: float = 1.0) -> dict:
    """One fully-explicit Cash Hunt round (board, shuffle, pick) for the UI."""
    vals, p = _dist(cfg, "cash_hunt")
    size = int(cfg["cash_hunt"]["board_size"])
    cap = float(cfg["cash_hunt"]["max_multiplier"])
    board = rng.choice(vals, size=size, p=p) * ts
    board = np.minimum(board, cap)
    rng.shuffle(board)
    pick = int(rng.integers(0, size))
    return {"board": board.tolist(), "pick_index": pick, "won": float(board[pick])}


# -------------------------------------------------------------------- Pachinko
def resolve_pachinko(rng: np.random.Generator, cfg: dict, ts_factor: np.ndarray) -> np.ndarray:
    n = len(ts_factor)
    block = cfg["pachinko"]
    vals, p = _dist(cfg, "pachinko")
    cap = float(block["max_multiplier"])
    p_double = block["double_slots"] / block["wall_size"]
    max_doubles = int(block["max_doubles"])

    factor = np.ones(n)
    result = np.zeros(n)
    active = np.ones(n, dtype=bool)
    for _ in range(max_doubles + 1):
        idx = np.flatnonzero(active)
        if idx.size == 0:
            break
        hit_double = rng.random(idx.size) < p_double
        capped = factor[idx] * vals.max() >= cap  # wall fully capped: DOUBLE is moot
        dbl = hit_double & ~capped
        # events that landed on a value slot resolve now
        land = idx[~dbl]
        landed_vals = rng.choice(vals, size=land.size, p=p)
        result[land] = np.minimum(landed_vals * factor[land], cap)
        active[land] = False
        # events that hit DOUBLE double the wall and drop again
        factor[idx[dbl]] *= 2.0
    # safety: any event still active after max_doubles resolves at the cap
    result[active] = cap
    return np.minimum(result * ts_factor, cap)


def simulate_pachinko_detail(rng: np.random.Generator, cfg: dict, ts: float = 1.0) -> dict:
    """One explicit Pachinko round with the real peg walk, for the UI."""
    block = cfg["pachinko"]
    vals, p = _dist(cfg, "pachinko")
    wall_size = int(block["wall_size"])
    rows = int(block["peg_rows"])
    cap = float(block["max_multiplier"])
    drops = []
    factor = 1.0
    for _ in range(int(block["max_doubles"]) + 1):
        wall = list(np.minimum(rng.choice(vals, size=wall_size, p=p) * factor, cap))
        double_pos = rng.choice(wall_size, size=int(block["double_slots"]), replace=False)
        for d in double_pos:
            wall[int(d)] = "DOUBLE"
        pos = int(rng.integers(0, wall_size))
        path = [pos]
        for _ in range(rows):
            pos = int(np.clip(pos + rng.choice([-1, 1]), 0, wall_size - 1))
            path.append(pos)
        landed = wall[pos]
        drops.append({"wall": wall, "path": path, "landed": landed})
        if landed == "DOUBLE" and factor * float(np.max(vals)) < cap:
            factor *= 2.0
            continue
        won = cap if landed == "DOUBLE" else float(landed)
        return {"drops": drops, "won": float(min(won * ts, cap))}
    return {"drops": drops, "won": float(cap)}


# ------------------------------------------------------------------ Crazy Time
def resolve_crazy_time(rng: np.random.Generator, cfg: dict, ts_factor: np.ndarray) -> np.ndarray:
    n = len(ts_factor)
    block = cfg["crazy_time_bonus"]
    vals, p = _dist(cfg, "crazy_time_bonus")
    cap = float(block["max_multiplier"])
    segs = int(block["segments"])
    p_double = block["double_segments"] / segs
    p_triple = block["triple_segments"] / segs
    max_rescales = int(block["max_rescales"])

    factor = np.ones(n)
    result = np.zeros(n)
    active = np.ones(n, dtype=bool)
    for _ in range(max_rescales + 1):
        idx = np.flatnonzero(active)
        if idx.size == 0:
            break
        u = rng.random(idx.size)
        capped = factor[idx] * vals.max() >= cap
        dbl = (u < p_double) & ~capped
        tpl = (u >= p_double) & (u < p_double + p_triple) & ~capped
        rescale = dbl | tpl
        land = idx[~rescale]
        landed_vals = rng.choice(vals, size=land.size, p=p)
        result[land] = np.minimum(landed_vals * factor[land], cap)
        active[land] = False
        factor[idx[dbl]] *= 2.0
        factor[idx[tpl]] *= 3.0
    result[active] = cap
    return np.minimum(result * ts_factor, cap)


BONUS_RESOLVERS = {
    "coin_flip": resolve_coin_flip,
    "cash_hunt": resolve_cash_hunt,
    "pachinko": resolve_pachinko,
    "crazy_time": resolve_crazy_time,
}


def expected_bonus_multipliers(cfg: dict, n: int = 200_000, seed: int = 7) -> dict[str, float]:
    """Monte Carlo estimate of each bonus's BASE mean payout multiple (without
    the Top Slot). The wheel model applies its calibrated Top Slot boost on top."""
    rng = np.random.default_rng(seed)
    ts_factor = np.ones(n)
    out = {}
    for key, fn in BONUS_RESOLVERS.items():
        out[key] = float(fn(rng, cfg, ts_factor).mean())
    return out
