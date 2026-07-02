"""Monte Carlo simulation engine.

Design for speed:
  * every spin outcome (wheel, Top Slot, fully-resolved bonuses) is pre-generated
    in vectorised NumPy chunks (outcomes.generate);
  * the only sequential part — the bankroll walk, which is path-dependent by
    definition for progressive strategies — runs over plain Python floats
    (faster than NumPy scalars in a tight loop);
  * multi-run simulations fan out one process per run with independent RNG
    substreams (SeedSequence.spawn), scaling across all CPU cores.

Throughput on a modern core is roughly 1.5–3 million strategy-spins per second
for simple strategies, so a 10M-spin run completes in a handful of seconds and
multi-run batches scale with core count.
"""
from __future__ import annotations

from typing import Any, Optional

import numpy as np

from .metrics import RunAccumulator
from .outcomes import generate
from .strategies import create
from .wheel import BONUS_SPOTS, Wheel

CHUNK = 1_000_000


def run_single(strategy_name: str, params: dict, spins: int, bankroll: float,
               bet_unit: float, seed_entropy: int, run_index: int = 0,
               progress=None) -> dict:
    """Execute one full simulation run and return its metrics block."""
    ss = np.random.SeedSequence(entropy=seed_entropy, spawn_key=(run_index,))
    rng = np.random.default_rng(ss)
    wheel = Wheel()
    strat = create(strategy_name, params, bet_unit, bankroll, wheel,
                   seed=int(ss.generate_state(1)[0]))

    table_min = float(wheel.config["table"]["min_bet"])
    table_max = float(wheel.config["table"]["max_bet"])
    max_bet = float(params.get("max_bet", 0)) or table_max
    stop_loss = float(params.get("stop_loss_pct", 0)) / 100.0
    take_profit = float(params.get("take_profit_pct", 0)) / 100.0
    stop_loss_level = bankroll * (1 - stop_loss) if stop_loss > 0 else -1.0
    take_profit_level = bankroll * (1 + take_profit) if take_profit > 0 else float("inf")

    acc = RunAccumulator(bankroll, spins)
    balance = bankroll
    bonus_set = set(BONUS_SPOTS)
    spin_idx = 0
    done = False

    while spin_idx < spins and not done:
        n = min(CHUNK, spins - spin_idx)
        out = generate(wheel, n, rng)
        results = out.result.tolist()
        pays = out.pay.tolist()
        nets = np.empty(n, dtype=np.float64)

        for i in range(n):
            strat.balance = balance
            bets = strat.next_bets()
            # clamp stakes to table limits and available balance
            staked = 0.0
            for j, (spot, amt) in enumerate(bets):
                if amt < table_min:
                    amt = table_min if amt > 0 else 0.0
                if amt > table_max:
                    amt = table_max
                bets[j] = (spot, amt)
                staked += amt
            if staked > max_bet and staked > 0:
                scale = max_bet / staked
                bets = [(s, a * scale) for s, a in bets]
                staked = max_bet
            if staked > balance:
                if balance <= table_min:
                    acc.ruined_at = spin_idx + i
                    acc.stopped_reason = "ruin"
                    nets = nets[:i]
                    done = True
                    break
                scale = balance / staked
                bets = [(s, a * scale) for s, a in bets]
                staked = balance

            result = results[i]
            pay = pays[i]
            returned = 0.0
            if staked > 0:
                balance -= staked
                for spot, amt in bets:
                    if spot == result:
                        returned += amt * (1.0 + pay)
                balance += returned

            net = returned - staked
            nets[i] = net
            strat.balance = balance
            strat.observe(returned > staked, net, result, pay)
            acc.record(spin_idx + i, balance, staked, returned,
                       result in bonus_set, staked > 0)

            if balance <= stop_loss_level:
                acc.stopped_reason = "stop_loss"
                nets = nets[:i + 1]
                done = True
                break
            if balance >= take_profit_level:
                acc.stopped_reason = "take_profit"
                nets = nets[:i + 1]
                done = True
                break

        acc.add_net_chunk(np.asarray(nets))
        spin_idx += n
        if progress is not None:
            progress(min(spin_idx / spins, 1.0), f"{spin_idx:,}/{spins:,} spins")

    return acc.finalize(balance)


# ---------------------------------------------------------------- process entrypoints
def run_single_job(queue, payload: dict) -> dict:
    """Single-run entrypoint for JobManager.submit_process (per-spin progress)."""

    def progress(frac: float, message: str) -> None:
        try:
            queue.put((frac, message))
        except Exception:
            pass

    return run_single(payload["strategy"], payload["params"], payload["spins"],
                      payload["bankroll"], payload["bet_unit"], payload["seed"],
                      run_index=0, progress=progress)


def run_one_of_many(strategy: str, params: dict, spins: int, bankroll: float,
                    bet_unit: float, seed: int, run_index: int) -> dict:
    """Fan-out entrypoint: one independent run of a multi-run batch."""
    return run_single(strategy, params, spins, bankroll, bet_unit, seed,
                      run_index=run_index)
