"""Interactive Crazy Time table engine.

Drives the "physical" table in the UI: chip bets on the eight spots, single
wheel spins over the real 54-segment layout, the Top Slot, and fully-playable
bonus rounds:

  * Coin Flip   — both multipliers generated, coin animation data returned;
  * Pachinko    — persistent 16-slot wall, explicit peg-walk drops, DOUBLE
                  re-drops with the whole wall doubling (capped);
  * Cash Hunt   — the 108-symbol board is generated and shown, then shuffled
                  server-side; the player picks a position (two-phase);
  * Crazy Time  — the 64-segment bonus wheel with DOUBLE/TRIPLE rescales and a
                  real flapper choice (blue/green/yellow, two-phase); one spin
                  resolves all three flappers at fixed offsets like the real
                  wheel.

Sessions are in-memory (this is a local desktop app); every spin uses the same
calibrated distributions as the mass Monte Carlo engine, so manual play and
million-spin simulations are statistically identical.
"""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Optional

import numpy as np

from .bonus_games import _dist
from .wheel import SPOT_INDEX, SPOT_KEYS, Wheel

SESSION_TTL = 6 * 3600
FLAPPER_OFFSETS = {"blue": 0, "green": 21, "yellow": 43}

_sessions: dict[str, dict] = {}
_lock = threading.RLock()  # re-entrant: create_session → snapshot → get


def _cleanup() -> None:
    now = time.time()
    dead = [k for k, s in _sessions.items() if now - s["touched"] > SESSION_TTL]
    for k in dead:
        _sessions.pop(k, None)


def create_session(bankroll: float) -> dict:
    with _lock:
        _cleanup()
        sid = uuid.uuid4().hex[:12]
        _sessions[sid] = {
            "id": sid, "balance": float(bankroll), "bankroll": float(bankroll),
            "history": [], "pending": None, "touched": time.time(),
            "rng": np.random.default_rng(),
            "spins": 0, "total_staked": 0.0, "total_returned": 0.0,
        }
        return snapshot(sid)


def get(sid: str) -> Optional[dict]:
    with _lock:
        s = _sessions.get(sid)
        if s:
            s["touched"] = time.time()
        return s


def snapshot(sid: str) -> dict:
    s = get(sid)
    if not s:
        raise KeyError("session not found or expired")
    return {"session_id": s["id"], "balance": round(s["balance"], 2),
            "bankroll": s["bankroll"], "spins": s["spins"],
            "total_staked": round(s["total_staked"], 2),
            "total_returned": round(s["total_returned"], 2),
            "history": s["history"][-30:],
            "pending_bonus": s["pending"]["game"] if s["pending"] else None}


# ------------------------------------------------------------------------ spin
def spin(sid: str, bets: dict[str, float]) -> dict:
    s = get(sid)
    if not s:
        raise KeyError("session not found or expired")
    if s["pending"]:
        # an abandoned choice (page reload, disconnect) must never wedge the
        # session: resolve it with a random pick, statistically neutral
        auto = (int(s["rng"].integers(0, len(s["pending"]["board"])))
                if s["pending"]["game"] == "cash_hunt"
                else str(s["rng"].choice(list(FLAPPER_OFFSETS))))
        bonus_choice(sid, auto)
    wheel = Wheel()
    cfg = wheel.config
    rng: np.random.Generator = s["rng"]

    bets = {k: float(v) for k, v in bets.items() if k in SPOT_INDEX and float(v) > 0}
    total = sum(bets.values())
    if total <= 0:
        raise ValueError("place at least one chip")
    if total > s["balance"] + 1e-9:
        raise ValueError("insufficient balance for this bet")
    min_bet = float(cfg["table"]["min_bet"])
    if any(v < min_bet for v in bets.values()):
        raise ValueError(f"minimum chip per spot is {min_bet}")

    s["balance"] -= total
    s["total_staked"] += total
    s["spins"] += 1

    # Top Slot draw, then the wheel
    ts_spot = SPOT_KEYS[int(rng.choice(8, p=wheel.ts_target_probs))]
    ts_mult = float(rng.choice(wheel.ts_mults, p=wheel.ts_weights))
    index = int(rng.integers(0, wheel.total_segments))
    segment = wheel.layout[index]
    ts_matched = ts_spot == segment
    ts_factor = ts_mult if ts_matched else 1.0

    base = {
        "wheel_index": index, "segment": segment,
        "top_slot": {"spot": ts_spot, "multiplier": ts_mult, "matched": ts_matched},
        "bets": bets, "total_bet": round(total, 2),
    }

    if segment in ("1", "2", "5", "10"):
        pay = float(wheel.pays[SPOT_INDEX[segment]]) * ts_factor
        winnings = bets.get(segment, 0.0) * (1.0 + pay)
        return _settle(s, base, phase="settled", winnings=winnings,
                       detail={"pays": pay})

    # ---- bonus games -------------------------------------------------------
    bet_on_bonus = bets.get(segment, 0.0)
    if segment == "coin_flip":
        vals, p = _dist(cfg, "coin_flip")
        cap = float(cfg["coin_flip"]["max_multiplier"])
        red = min(float(rng.choice(vals, p=p)) * ts_factor, cap)
        blue = min(float(rng.choice(vals, p=p)) * ts_factor, cap)
        side = "red" if rng.random() < 0.5 else "blue"
        won = red if side == "red" else blue
        detail = {"red": red, "blue": blue, "result": side, "won_multiplier": won}
        # winning bets always get the stake back on top of multiplier × stake
        return _settle(s, base, phase="bonus_settled",
                       winnings=bet_on_bonus * (1.0 + won) if bet_on_bonus > 0 else 0.0,
                       detail=detail)

    if segment == "pachinko":
        detail = _play_pachinko(rng, cfg, ts_factor)
        won = detail["won_multiplier"]
        return _settle(s, base, phase="bonus_settled",
                       winnings=bet_on_bonus * (1.0 + won) if bet_on_bonus > 0 else 0.0,
                       detail=detail)

    if segment == "cash_hunt":
        vals, p = _dist(cfg, "cash_hunt")
        size = int(cfg["cash_hunt"]["board_size"])
        cap = float(cfg["cash_hunt"]["max_multiplier"])
        board = np.minimum(rng.choice(vals, size=size, p=p) * ts_factor, cap)
        shuffled = board.copy()
        rng.shuffle(shuffled)
        s["pending"] = {"game": "cash_hunt", "board": shuffled.tolist(),
                        "bet": bet_on_bonus, "base": base}
        return {**base, "phase": "await_choice", "game": "cash_hunt",
                "preview_board": board.tolist(), "board_size": size,
                "balance": round(s["balance"], 2)}

    # crazy_time
    block = cfg["crazy_time_bonus"]
    vals, p = _dist(cfg, "crazy_time_bonus")
    segs = int(block["segments"])
    values = rng.choice(vals, size=segs, p=p) * ts_factor
    specials = rng.choice(segs, size=int(block["double_segments"]) + int(block["triple_segments"]),
                          replace=False)
    kinds = ["double"] * int(block["double_segments"]) + ["triple"] * int(block["triple_segments"])
    wheel64: list[Any] = [round(float(v), 2) for v in values]
    for pos, kind in zip(specials, kinds):
        wheel64[int(pos)] = kind.upper()
    s["pending"] = {"game": "crazy_time", "wheel": wheel64,
                    "cap": float(block["max_multiplier"]),
                    "max_rescales": int(block["max_rescales"]),
                    "bet": bet_on_bonus, "base": base}
    return {**base, "phase": "await_choice", "game": "crazy_time",
            "bonus_wheel": wheel64, "flappers": list(FLAPPER_OFFSETS),
            "balance": round(s["balance"], 2)}


def bonus_choice(sid: str, choice: Any) -> dict:
    s = get(sid)
    if not s or not s["pending"]:
        raise ValueError("no pending bonus round")
    pending = s["pending"]
    s["pending"] = None
    rng: np.random.Generator = s["rng"]

    if pending["game"] == "cash_hunt":
        board = pending["board"]
        idx = int(choice)
        if not 0 <= idx < len(board):
            raise ValueError("invalid board position")
        won = float(board[idx])
        detail = {"board": board, "pick_index": idx, "won_multiplier": won}
        bet = pending["bet"]
        return _settle(s, pending["base"], phase="bonus_settled",
                       winnings=bet * (1.0 + won) if bet > 0 else 0.0, detail=detail)

    # crazy_time flapper
    color = str(choice).lower()
    if color not in FLAPPER_OFFSETS:
        raise ValueError("choose blue, green or yellow")
    wheel64 = list(pending["wheel"])
    cap = pending["cap"]
    spins = []
    won = 0.0
    scale = 1.0
    for _ in range(pending["max_rescales"] + 1):
        idx = int(rng.integers(0, len(wheel64)))
        landed = {c: wheel64[(idx + off) % len(wheel64)] for c, off in FLAPPER_OFFSETS.items()}
        mine = landed[color]
        spins.append({"index": idx, "landed": landed, "scale": scale})
        if mine in ("DOUBLE", "TRIPLE"):
            factor = 2.0 if mine == "DOUBLE" else 3.0
            scale *= factor
            wheel64 = [round(min(v * factor, cap), 2) if isinstance(v, (int, float)) else v
                       for v in wheel64]
            spins[-1]["rescaled_wheel"] = wheel64
            continue
        won = float(min(float(mine), cap))
        break
    else:
        won = cap
    detail = {"flapper": color, "spins": spins, "won_multiplier": won}
    bet = pending["bet"]
    return _settle(s, pending["base"], phase="bonus_settled",
                   winnings=bet * (1.0 + won) if bet > 0 else 0.0, detail=detail)


def _play_pachinko(rng: np.random.Generator, cfg: dict, ts_factor: float) -> dict:
    block = cfg["pachinko"]
    vals, p = _dist(cfg, "pachinko")
    wall_size = int(block["wall_size"])
    rows = int(block["peg_rows"])
    cap = float(block["max_multiplier"])
    n_doubles = int(block["double_slots"])
    values = np.minimum(rng.choice(vals, size=wall_size, p=p) * ts_factor, cap)
    double_pos = set(int(x) for x in rng.choice(wall_size, size=n_doubles, replace=False))
    drops = []
    for _ in range(int(block["max_doubles"]) + 1):
        wall = ["DOUBLE" if i in double_pos else round(float(values[i]), 2)
                for i in range(wall_size)]
        pos = int(rng.integers(0, wall_size))
        path = [pos]
        for _ in range(rows):
            pos = int(np.clip(pos + rng.choice([-1, 1]), 0, wall_size - 1))
            path.append(pos)
        landed = wall[pos]
        drops.append({"wall": wall, "path": path, "landed": landed})
        if landed == "DOUBLE":
            if float(values.max()) >= cap:  # fully capped wall: settle at cap
                return {"drops": drops, "won_multiplier": cap}
            values = np.minimum(values * 2, cap)
            continue
        return {"drops": drops, "won_multiplier": float(landed)}
    return {"drops": drops, "won_multiplier": cap}


def _settle(s: dict, base: dict, phase: str, winnings: float, detail: dict) -> dict:
    winnings = float(winnings)
    s["balance"] += winnings
    s["total_returned"] += winnings
    entry = {"segment": base["segment"],
             "top_slot": base["top_slot"],
             "total_bet": base["total_bet"],
             "winnings": round(winnings, 2),
             "net": round(winnings - base["total_bet"], 2),
             "balance": round(s["balance"], 2)}
    s["history"].append(entry)
    if len(s["history"]) > 500:
        s["history"] = s["history"][-300:]
    return {**base, "phase": phase, "detail": detail,
            "winnings": round(winnings, 2),
            "net": round(winnings - base["total_bet"], 2),
            "balance": round(s["balance"], 2)}
