"""Statistical metrics for simulation runs.

All heavy statistics are accumulated online inside the engine loop (RunAccumulator)
so a 10-million-spin run never materialises more than one chunk of per-spin data.
"""
from __future__ import annotations

import math

import numpy as np

from ..core.config import CURVE_POINTS


class RunAccumulator:
    """Online statistics for a single simulation run."""

    def __init__(self, bankroll: float, n_spins: int) -> None:
        self.bankroll = bankroll
        self.n_spins = n_spins
        self.stride = max(1, math.ceil(n_spins / CURVE_POINTS))
        # balance path
        self.curve: list[float] = [bankroll]
        self.peak_curve: list[float] = [bankroll]
        self.max_balance = bankroll
        self.min_balance = bankroll
        self.peak = bankroll
        self.max_drawdown = 0.0
        self.dd_sum = 0.0
        # betting flow
        self.total_staked = 0.0
        self.total_returned = 0.0
        self.spins_played = 0
        self.spins_bet = 0
        # streaks
        self.cur_win_streak = 0
        self.cur_lose_streak = 0
        self.longest_win_streak = 0
        self.longest_lose_streak = 0
        # bonuses
        self.bonus_hits = 0
        self.bonus_wins_amount = 0.0
        self.total_win_amount = 0.0
        # per-spin net returns (in currency) histogram, accumulated chunk-wise
        self.net_samples: list[np.ndarray] = []
        self.net_sum = 0.0
        self.net_sq_sum = 0.0
        self.ruined_at: int | None = None
        self.stopped_reason = "completed"

    # called once per spin from the tight loop -------------------------------
    def record(self, spin_idx: int, balance: float, staked: float, returned: float,
               was_bonus_result: bool, bet_placed: bool) -> None:
        net = returned - staked
        self.total_staked += staked
        self.total_returned += returned
        self.net_sum += net
        self.net_sq_sum += net * net
        self.spins_played += 1
        if bet_placed:
            self.spins_bet += 1
            if net > 0:
                self.cur_win_streak += 1
                self.cur_lose_streak = 0
                if self.cur_win_streak > self.longest_win_streak:
                    self.longest_win_streak = self.cur_win_streak
                self.total_win_amount += returned
                if was_bonus_result:
                    self.bonus_hits += 1
                    self.bonus_wins_amount += returned
            elif net < 0:
                self.cur_lose_streak += 1
                self.cur_win_streak = 0
                if self.cur_lose_streak > self.longest_lose_streak:
                    self.longest_lose_streak = self.cur_lose_streak
        if balance > self.max_balance:
            self.max_balance = balance
        if balance < self.min_balance:
            self.min_balance = balance
        if balance > self.peak:
            self.peak = balance
        dd = 0.0 if self.peak <= 0 else (self.peak - balance) / self.peak
        if dd > self.max_drawdown:
            self.max_drawdown = dd
        self.dd_sum += dd
        if spin_idx % self.stride == 0:
            self.curve.append(balance)
            self.peak_curve.append(self.peak)

    def add_net_chunk(self, nets: np.ndarray) -> None:
        # keep a bounded reservoir for the distribution histogram
        if sum(len(c) for c in self.net_samples) < 400_000:
            self.net_samples.append(nets)

    # -------------------------------------------------------------------------
    def finalize(self, final_balance: float) -> dict:
        n = max(1, self.spins_played)
        profit = final_balance - self.bankroll
        mean_net = self.net_sum / n
        var = max(0.0, self.net_sq_sum / n - mean_net * mean_net)
        vol = math.sqrt(var)
        rtp = self.total_returned / self.total_staked if self.total_staked > 0 else 0.0
        nets = np.concatenate(self.net_samples) if self.net_samples else np.zeros(1)
        hist_counts, hist_edges = np.histogram(nets, bins=40)
        dd_curve = [0.0 if p <= 0 else (p - b) / p for b, p in zip(self.curve, self.peak_curve)]
        return {
            "final_balance": final_balance,
            "max_balance": self.max_balance,
            "min_balance": self.min_balance,
            "profit": max(0.0, profit),
            "loss": max(0.0, -profit),
            "net_profit": profit,
            "roi": profit / self.bankroll,
            "max_drawdown": self.max_drawdown,
            "avg_drawdown": self.dd_sum / n,
            "expected_value_per_spin": mean_net,
            "volatility_per_spin": vol,
            "sharpe_like": mean_net / vol if vol > 0 else 0.0,
            "longest_winning_streak": self.longest_win_streak,
            "longest_losing_streak": self.longest_lose_streak,
            "bonus_hits": self.bonus_hits,
            "bonus_frequency": self.bonus_hits / max(1, self.spins_bet),
            "bonus_contribution": (self.bonus_wins_amount / self.total_win_amount
                                   if self.total_win_amount > 0 else 0.0),
            "avg_spin_return": (self.total_returned / self.total_staked - 1.0
                                if self.total_staked > 0 else 0.0),
            "total_staked": self.total_staked,
            "total_returned": self.total_returned,
            "rtp_achieved": rtp,
            "spins_played": self.spins_played,
            "spins_bet": self.spins_bet,
            "ruined": self.ruined_at is not None,
            "ruined_at_spin": self.ruined_at,
            "stopped_reason": self.stopped_reason,
            "balance_curve": [round(v, 4) for v in self.curve],
            "drawdown_curve": [round(v, 6) for v in dd_curve],
            "return_histogram": {
                "counts": hist_counts.tolist(),
                "edges": [round(float(e), 4) for e in hist_edges],
            },
        }


def aggregate_runs(runs: list[dict], bankroll: float) -> dict:
    """Combine per-run metric blocks into the multi-run result the UI shows."""
    finals = np.array([r["final_balance"] for r in runs], dtype=np.float64)
    profits = finals - bankroll
    ruined = sum(1 for r in runs if r["ruined"])
    # percentile bands over balance curves (equal grids by construction)
    min_len = min(len(r["balance_curve"]) for r in runs)
    curves = np.array([r["balance_curve"][:min_len] for r in runs])
    band = {
        "median": np.percentile(curves, 50, axis=0).round(4).tolist(),
        "p10": np.percentile(curves, 10, axis=0).round(4).tolist(),
        "p90": np.percentile(curves, 90, axis=0).round(4).tolist(),
    }
    hist_counts, hist_edges = np.histogram(finals, bins=min(30, max(5, len(runs) // 2)))

    def mean(key: str) -> float:
        return float(np.mean([r[key] for r in runs]))

    return {
        "runs": len(runs),
        "risk_of_ruin": ruined / len(runs),
        "mean_spins_survived": float(np.mean([r["spins_played"] for r in runs])),
        "mean_final_balance": float(finals.mean()),
        "median_final_balance": float(np.median(finals)),
        "std_final_balance": float(finals.std()),
        "best_final_balance": float(finals.max()),
        "worst_final_balance": float(finals.min()),
        "prob_profit": float((profits > 0).mean()),
        "mean_roi": float(profits.mean() / bankroll),
        "mean_max_drawdown": mean("max_drawdown"),
        "mean_avg_drawdown": mean("avg_drawdown"),
        "mean_rtp_achieved": mean("rtp_achieved"),
        "mean_bonus_frequency": mean("bonus_frequency"),
        "mean_bonus_contribution": mean("bonus_contribution"),
        "mean_volatility": mean("volatility_per_spin"),
        "mean_ev_per_spin": mean("expected_value_per_spin"),
        "longest_winning_streak": max(r["longest_winning_streak"] for r in runs),
        "longest_losing_streak": max(r["longest_losing_streak"] for r in runs),
        "final_balance_histogram": {
            "counts": hist_counts.tolist(),
            "edges": [round(float(e), 4) for e in hist_edges],
        },
        "balance_bands": band,
        "representative_run": runs[0],
    }
