"""Betting-strategy library for the Crazy Time simulator.

Every strategy:
  * declares PARAMS — a schema the UI renders into an editable form;
  * implements next_bets() -> list[(spot_index, amount)] and observe(...);
  * is registered via @register_strategy and appears everywhere automatically.

The simulator is honest: no strategy changes the house edge. The library exists
to compare *risk profiles* (variance, drawdown, ruin probability, streaks) of
different staking plans under identical random outcomes.

Spot indices: 0:"1" 1:"2" 2:"5" 3:"10" 4:coin_flip 5:cash_hunt 6:pachinko 7:crazy_time
"""
from __future__ import annotations

import math
import random
from typing import Any

from .wheel import BONUS_SPOTS, NUMBER_SPOTS, SPOT_KEYS, Wheel

REGISTRY: dict[str, type["Strategy"]] = {}


def register_strategy(cls: type["Strategy"]) -> type["Strategy"]:
    REGISTRY[cls.NAME] = cls
    return cls


def num(key: str, label: str, default: float, mn: float, mx: float, step: float = 1) -> dict:
    return {"key": key, "label": label, "type": "number",
            "default": default, "min": mn, "max": mx, "step": step}


def sel(key: str, label: str, default: str, options: list[str]) -> dict:
    return {"key": key, "label": label, "type": "select", "default": default, "options": options}


SPOT_OPTIONS = list(SPOT_KEYS)

TARGET_PRESETS: dict[str, list[int]] = {
    "1": [0], "2": [1], "5": [2], "10": [3],
    "coin_flip": [4], "cash_hunt": [5], "pachinko": [6], "crazy_time": [7],
    "all_numbers": NUMBER_SPOTS,
    "all_bonuses": BONUS_SPOTS,
    "everything": list(range(8)),
}
TARGET_OPTIONS = list(TARGET_PRESETS.keys())


class Strategy:
    NAME = "base"
    LABEL = "Base"
    CATEGORY = "core"
    DESCRIPTION = ""
    PARAMS: list[dict] = []

    def __init__(self, params: dict[str, Any], bet_unit: float, bankroll: float,
                 wheel: Wheel, seed: int = 0) -> None:
        self.p = {sch["key"]: params.get(sch["key"], sch["default"]) for sch in self.PARAMS}
        self.unit = float(bet_unit)
        self.bankroll = float(bankroll)
        self.balance = float(bankroll)
        self.wheel = wheel
        self.rand = random.Random(seed)
        self.reset()

    # -- overridable ----------------------------------------------------------
    def reset(self) -> None:  # initialise progression state
        pass

    def next_bets(self) -> list[tuple[int, float]]:  # (spot, amount)
        raise NotImplementedError

    def observe(self, won: bool, net: float, result: int, pay: float) -> None:
        pass

    # -- helpers --------------------------------------------------------------
    def targets(self, key: str = "target") -> list[int]:
        return TARGET_PRESETS[self.p.get(key, "1")]

    def split(self, total: float, spots: list[int]) -> list[tuple[int, float]]:
        amt = total / len(spots)
        return [(s, amt) for s in spots]

    @classmethod
    def schema(cls) -> dict:
        return {"name": cls.NAME, "label": cls.LABEL, "category": cls.CATEGORY,
                "description": cls.DESCRIPTION, "params": cls.PARAMS}


# ============================================================== core staking plans
@register_strategy
class FlatBetting(Strategy):
    NAME = "flat"
    LABEL = "Flat Betting"
    CATEGORY = "classic"
    DESCRIPTION = "The same stake on the same spots every spin. The volatility baseline."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("units", "Units per spin", 1, 0.1, 100, 0.1)]

    def next_bets(self):
        return self.split(self.unit * self.p["units"], self.targets())


@register_strategy
class Martingale(Strategy):
    NAME = "martingale"
    LABEL = "Martingale"
    CATEGORY = "classic"
    DESCRIPTION = "Double the stake after every loss; reset after a win. Small frequent wins, catastrophic tail risk."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("factor", "Loss multiplier", 2, 1.1, 5, 0.1),
              num("max_steps", "Max progression steps", 10, 1, 25)]

    def reset(self):
        self.step = 0

    def next_bets(self):
        stake = self.unit * (self.p["factor"] ** self.step)
        return self.split(stake, self.targets())

    def observe(self, won, net, result, pay):
        self.step = 0 if won else min(self.step + 1, int(self.p["max_steps"]))


@register_strategy
class ReverseMartingale(Strategy):
    NAME = "reverse_martingale"
    LABEL = "Reverse Martingale"
    CATEGORY = "classic"
    DESCRIPTION = "Double after each win, bank the run after N straight wins; reset on any loss."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("factor", "Win multiplier", 2, 1.1, 5, 0.1),
              num("run_length", "Bank after N wins", 3, 1, 10)]

    def reset(self):
        self.step = 0

    def next_bets(self):
        return self.split(self.unit * (self.p["factor"] ** self.step), self.targets())

    def observe(self, won, net, result, pay):
        if won:
            self.step += 1
            if self.step >= int(self.p["run_length"]):
                self.step = 0
        else:
            self.step = 0


@register_strategy
class Paroli(Strategy):
    NAME = "paroli"
    LABEL = "Paroli (1-2-4)"
    CATEGORY = "classic"
    DESCRIPTION = "Three-step positive progression 1-2-4; reset after completing the cycle or losing."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS)]
    LADDER = [1, 2, 4]

    def reset(self):
        self.step = 0

    def next_bets(self):
        return self.split(self.unit * self.LADDER[self.step], self.targets())

    def observe(self, won, net, result, pay):
        self.step = (self.step + 1) % len(self.LADDER) if won else 0


@register_strategy
class Fibonacci(Strategy):
    NAME = "fibonacci"
    LABEL = "Fibonacci"
    CATEGORY = "classic"
    DESCRIPTION = "Advance one Fibonacci step after a loss, retreat two after a win."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("max_steps", "Max progression steps", 15, 3, 25)]
    FIB = [1, 1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233, 377, 610, 987, 1597,
           2584, 4181, 6765, 10946, 17711, 28657, 46368, 75025]

    def reset(self):
        self.step = 0

    def next_bets(self):
        return self.split(self.unit * self.FIB[self.step], self.targets())

    def observe(self, won, net, result, pay):
        if won:
            self.step = max(0, self.step - 2)
        else:
            self.step = min(self.step + 1, int(self.p["max_steps"]))


@register_strategy
class DAlembert(Strategy):
    NAME = "dalembert"
    LABEL = "D'Alembert"
    CATEGORY = "classic"
    DESCRIPTION = "Add one unit after a loss, remove one after a win. A gentle arithmetic progression."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("max_units", "Max units", 20, 2, 100)]

    def reset(self):
        self.units = 1

    def next_bets(self):
        return self.split(self.unit * self.units, self.targets())

    def observe(self, won, net, result, pay):
        self.units = max(1, self.units - 1) if won else min(int(self.p["max_units"]), self.units + 1)


@register_strategy
class OscarsGrind(Strategy):
    NAME = "oscars_grind"
    LABEL = "Oscar's Grind"
    CATEGORY = "classic"
    DESCRIPTION = "Targets +1 unit per cycle: raise the stake one unit after wins, never after losses, and never overshoot the cycle target."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("cycle_profit", "Cycle profit target (units)", 1, 1, 10)]

    def reset(self):
        self.units = 1
        self.cycle_pnl = 0.0

    def next_bets(self):
        # never bet more than needed to close the cycle at +target units
        need = self.p["cycle_profit"] * self.unit - self.cycle_pnl
        stake = min(self.units * self.unit, max(self.unit, need))
        return self.split(stake, self.targets())

    def observe(self, won, net, result, pay):
        self.cycle_pnl += net
        if self.cycle_pnl >= self.p["cycle_profit"] * self.unit:
            self.reset()
        elif won:
            self.units += 1


@register_strategy
class Labouchere(Strategy):
    NAME = "labouchere"
    LABEL = "Labouchère"
    CATEGORY = "classic"
    DESCRIPTION = "Cancellation system: stake = first + last of the line; cross them off on a win, append the lost stake on a loss."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("line_length", "Starting line length", 6, 2, 12),
              num("max_line", "Max line length", 30, 5, 60)]

    def reset(self):
        self.line = list(range(1, int(self.p["line_length"]) + 1))

    def next_bets(self):
        if not self.line:
            self.reset()
        stake = (self.line[0] + (self.line[-1] if len(self.line) > 1 else 0)) * self.unit
        return self.split(stake, self.targets())

    def observe(self, won, net, result, pay):
        if won:
            self.line = self.line[1:-1] if len(self.line) > 1 else []
        else:
            if len(self.line) < int(self.p["max_line"]):
                lost_units = self.line[0] + (self.line[-1] if len(self.line) > 1 else 0)
                self.line.append(lost_units)


@register_strategy
class OneThreeTwoSix(Strategy):
    NAME = "one_three_two_six"
    LABEL = "1-3-2-6"
    CATEGORY = "classic"
    DESCRIPTION = "Positive progression 1-3-2-6 across four straight wins; any loss restarts the sequence."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS)]
    LADDER = [1, 3, 2, 6]

    def reset(self):
        self.step = 0

    def next_bets(self):
        return self.split(self.unit * self.LADDER[self.step], self.targets())

    def observe(self, won, net, result, pay):
        self.step = (self.step + 1) % len(self.LADDER) if won else 0


# ============================================================ proportional staking
@register_strategy
class PercentageBetting(Strategy):
    NAME = "percentage"
    LABEL = "Percentage Betting"
    CATEGORY = "proportional"
    DESCRIPTION = "Stake a fixed percentage of the current balance every spin."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("percent", "Percent of balance", 2, 0.1, 25, 0.1)]

    def next_bets(self):
        return self.split(self.balance * self.p["percent"] / 100.0, self.targets())


@register_strategy
class KellyFraction(Strategy):
    NAME = "kelly"
    LABEL = "Kelly Fraction"
    CATEGORY = "proportional"
    DESCRIPTION = ("True Kelly staking on one spot. Because every Crazy Time spot has negative "
                   "expectation, pure Kelly stakes zero — this implementation shows that honestly, "
                   "betting the table minimum unless you assume a hypothetical edge.")
    PARAMS = [sel("target", "Bet target", "1", ["1", "2", "5", "10", "coin_flip", "cash_hunt", "pachinko", "crazy_time"]),
              num("fraction", "Fraction of full Kelly", 0.5, 0.05, 1, 0.05),
              num("assumed_edge", "Assumed extra edge %", 0, 0, 20, 0.5)]

    def reset(self):
        spot = TARGET_PRESETS[self.p["target"]][0]
        desc = self.wheel.describe()["spots"][spot]
        p_win = desc["probability"]
        b = desc["mean_payout_multiple"]  # net odds received on a win
        edge_boost = self.p["assumed_edge"] / 100.0
        p_adj = min(0.999, p_win * (1 + edge_boost))
        kelly = (p_adj * (b + 1) - 1) / b if b > 0 else 0.0
        self.kelly_frac = max(0.0, kelly) * self.p["fraction"]
        self.spot = spot

    def next_bets(self):
        stake = self.balance * self.kelly_frac
        if stake <= 0:
            stake = self.unit * 0.1  # table-minimum probe bet: Kelly says don't play
        return [(self.spot, stake)]


@register_strategy
class AntiMartingale(Strategy):
    NAME = "anti_martingale"
    LABEL = "Anti-Martingale"
    CATEGORY = "proportional"
    DESCRIPTION = "Continuous version: multiply the stake after wins, cut it after losses, bounded between 1 and max units."
    PARAMS = [sel("target", "Bet target", "1", TARGET_OPTIONS),
              num("win_factor", "Multiply on win", 1.5, 1.1, 3, 0.1),
              num("loss_factor", "Multiply on loss", 0.5, 0.2, 0.9, 0.05),
              num("max_units", "Max units", 32, 2, 200)]

    def reset(self):
        self.units = 1.0

    def next_bets(self):
        return self.split(self.unit * self.units, self.targets())

    def observe(self, won, net, result, pay):
        self.units *= self.p["win_factor"] if won else self.p["loss_factor"]
        self.units = min(max(self.units, 1.0), float(self.p["max_units"]))


# ================================================================ coverage styles
@register_strategy
class BonusHunting(Strategy):
    NAME = "bonus_hunting"
    LABEL = "Bonus Hunting"
    CATEGORY = "coverage"
    DESCRIPTION = "Equal stakes on all four bonus games every spin — pure high-variance jackpot chasing (9/54 hit rate)."
    PARAMS = [num("units", "Units per bonus", 1, 0.1, 50, 0.1)]

    def next_bets(self):
        amt = self.unit * self.p["units"]
        return [(s, amt) for s in BONUS_SPOTS]


@register_strategy
class NumbersOnly(Strategy):
    NAME = "numbers_only"
    LABEL = "Numbers Only"
    CATEGORY = "coverage"
    DESCRIPTION = "Cover 1, 2, 5 and 10 with stakes proportional to their segment counts (45/54 hit rate, low variance)."
    PARAMS = [num("units", "Total units per spin", 4, 0.4, 100, 0.1),
              sel("weighting", "Stake weighting", "segments", ["segments", "equal"])]

    def next_bets(self):
        total = self.unit * self.p["units"]
        if self.p["weighting"] == "equal":
            return self.split(total, NUMBER_SPOTS)
        counts = [self.wheel.counts[i] for i in NUMBER_SPOTS]
        s = sum(counts)
        return [(i, total * c / s) for i, c in zip(NUMBER_SPOTS, counts)]


@register_strategy
class BonusOnlySingle(Strategy):
    NAME = "bonus_only_single"
    LABEL = "Single Bonus Sniper"
    CATEGORY = "coverage"
    DESCRIPTION = "Stake on exactly one chosen bonus game every spin and ride its full volatility."
    PARAMS = [sel("bonus", "Bonus", "crazy_time", ["coin_flip", "cash_hunt", "pachinko", "crazy_time"]),
              num("units", "Units per spin", 1, 0.1, 50, 0.1)]

    def next_bets(self):
        return [(TARGET_PRESETS[self.p["bonus"]][0], self.unit * self.p["units"])]


@register_strategy
class MixedCoverage(Strategy):
    NAME = "mixed_coverage"
    LABEL = "Mixed Coverage"
    CATEGORY = "coverage"
    DESCRIPTION = "Cover every spot with stakes proportional to segment probability — the closest thing to betting on the wheel itself."
    PARAMS = [num("units", "Total units per spin", 8, 0.8, 200, 0.1)]

    def next_bets(self):
        total = self.unit * self.p["units"]
        return [(i, total * float(self.wheel.probs[i])) for i in range(8)]


# ================================================================ adaptive / other
@register_strategy
class AdaptiveStreak(Strategy):
    NAME = "adaptive_streak"
    LABEL = "Adaptive Streak"
    CATEGORY = "adaptive"
    DESCRIPTION = "Scales the stake up after a losing streak and back down after wins, with configurable sensitivity."
    PARAMS = [sel("target", "Bet target", "all_numbers", TARGET_OPTIONS),
              num("trigger", "Streak length trigger", 3, 1, 10),
              num("scale", "Scale factor per trigger", 1.5, 1.1, 3, 0.1),
              num("max_units", "Max units", 20, 2, 100)]

    def reset(self):
        self.streak = 0
        self.units = 1.0

    def next_bets(self):
        return self.split(self.unit * self.units, self.targets())

    def observe(self, won, net, result, pay):
        if won:
            self.streak = 0
            self.units = max(1.0, self.units / self.p["scale"])
        else:
            self.streak += 1
            if self.streak % int(self.p["trigger"]) == 0:
                self.units = min(float(self.p["max_units"]), self.units * self.p["scale"])


@register_strategy
class Randomized(Strategy):
    NAME = "randomized"
    LABEL = "Randomized"
    CATEGORY = "adaptive"
    DESCRIPTION = "A control strategy: a random spot and a random stake every spin. Useful as a Monte Carlo baseline."
    PARAMS = [num("min_units", "Min units", 1, 0.1, 50, 0.1),
              num("max_units", "Max units", 3, 0.1, 100, 0.1)]

    def next_bets(self):
        spot = self.rand.randrange(8)
        lo, hi = self.p["min_units"], max(self.p["min_units"], self.p["max_units"])
        return [(spot, self.unit * self.rand.uniform(lo, hi))]


@register_strategy
class DrawdownDefender(Strategy):
    NAME = "drawdown_defender"
    LABEL = "Drawdown Defender"
    CATEGORY = "original"
    DESCRIPTION = ("Original: percentage staking whose percent glides toward zero as current "
                   "drawdown deepens — an automatic de-risking curve.")
    PARAMS = [sel("target", "Bet target", "all_numbers", TARGET_OPTIONS),
              num("base_percent", "Base percent of balance", 3, 0.2, 15, 0.1),
              num("defense", "Defense strength", 2, 0.5, 6, 0.1)]

    def reset(self):
        self.peak = self.balance

    def next_bets(self):
        self.peak = max(self.peak, self.balance)
        dd = 0.0 if self.peak <= 0 else 1 - self.balance / self.peak
        pct = self.p["base_percent"] * math.exp(-self.p["defense"] * dd * 4)
        return self.split(self.balance * pct / 100.0, self.targets())


@register_strategy
class VolatilityLadder(Strategy):
    NAME = "volatility_ladder"
    LABEL = "Volatility Ladder"
    CATEGORY = "original"
    DESCRIPTION = ("Original: monitors the variance of recent spin returns and rotates between "
                   "low-volatility number coverage and high-volatility bonus coverage.")
    PARAMS = [num("window", "Lookback window (spins)", 50, 10, 500),
              num("units", "Total units per spin", 4, 0.4, 100, 0.1),
              num("threshold", "Variance threshold", 4, 0.5, 50, 0.5)]

    def reset(self):
        self.returns: list[float] = []
        self.mode_numbers = True

    def next_bets(self):
        total = self.unit * self.p["units"]
        spots = NUMBER_SPOTS if self.mode_numbers else BONUS_SPOTS
        return self.split(total, spots)

    def observe(self, won, net, result, pay):
        u = self.unit * self.p["units"]
        self.returns.append(net / u if u > 0 else 0.0)
        w = int(self.p["window"])
        if len(self.returns) > w:
            self.returns.pop(0)
        if len(self.returns) == w:
            mean = sum(self.returns) / w
            var = sum((r - mean) ** 2 for r in self.returns) / w
            self.mode_numbers = var > self.p["threshold"]


@register_strategy
class EVGuardian(Strategy):
    NAME = "ev_guardian"
    LABEL = "EV Guardian"
    CATEGORY = "original"
    DESCRIPTION = ("Original: flat-bets the spot with the best RTP and sits out for a cooldown "
                   "after the session loss breaches a guard threshold — a disciplined loss-limiter.")
    PARAMS = [num("units", "Units per spin", 1, 0.1, 50, 0.1),
              num("guard_loss_pct", "Guard: session loss %", 20, 5, 80),
              num("cooldown", "Cooldown spins", 25, 5, 500)]

    def reset(self):
        desc = self.wheel.describe()["spots"]
        self.spot = max(range(8), key=lambda i: desc[i]["rtp"])
        self.cooldown_left = 0

    def next_bets(self):
        loss_frac = 1 - self.balance / self.bankroll
        if self.cooldown_left > 0:
            self.cooldown_left -= 1
            return []
        if loss_frac * 100 >= self.p["guard_loss_pct"]:
            self.cooldown_left = int(self.p["cooldown"])
            return []
        return [(self.spot, self.unit * self.p["units"])]


@register_strategy
class HotWheelChaser(Strategy):
    NAME = "hot_wheel_chaser"
    LABEL = "Hot Wheel Chaser"
    CATEGORY = "original"
    DESCRIPTION = ("Original, deliberately fallacious: raises bonus stakes the longer no bonus has hit. "
                   "Spins are independent, so this cannot work — it exists to demonstrate the "
                   "gambler's fallacy quantitatively.")
    PARAMS = [num("units", "Base units", 1, 0.1, 50, 0.1),
              num("drought_trigger", "Drought trigger (spins)", 8, 2, 50),
              num("escalation", "Escalation per trigger", 1.5, 1.1, 3, 0.1),
              num("max_units", "Max units", 16, 2, 100)]

    def reset(self):
        self.drought = 0
        self.units = 1.0

    def next_bets(self):
        amt = self.unit * self.p["units"] * self.units / 4
        return [(s, amt) for s in BONUS_SPOTS]

    def observe(self, won, net, result, pay):
        if result in BONUS_SPOTS:
            self.drought = 0
            self.units = 1.0
        else:
            self.drought += 1
            if self.drought % int(self.p["drought_trigger"]) == 0:
                self.units = min(float(self.p["max_units"]), self.units * self.p["escalation"])


@register_strategy
class CustomLayout(Strategy):
    NAME = "custom_layout"
    LABEL = "Custom Chip Layout"
    CATEGORY = "coverage"
    DESCRIPTION = ("Repeats your exact chip layout from the live table every spin — "
                   "the bridge between manual play and mass simulation. Units are "
                   "multiplied by the bet unit.")
    PARAMS = [num("u_1", "Units on 1", 0, 0, 1000, 0.1),
              num("u_2", "Units on 2", 0, 0, 1000, 0.1),
              num("u_5", "Units on 5", 0, 0, 1000, 0.1),
              num("u_10", "Units on 10", 0, 0, 1000, 0.1),
              num("u_coin_flip", "Units on Coin Flip", 0, 0, 1000, 0.1),
              num("u_cash_hunt", "Units on Cash Hunt", 0, 0, 1000, 0.1),
              num("u_pachinko", "Units on Pachinko", 0, 0, 1000, 0.1),
              num("u_crazy_time", "Units on Crazy Time", 0, 0, 1000, 0.1)]

    def reset(self):
        self.layout = [(i, float(self.p[f"u_{k}"]))
                       for i, k in enumerate(SPOT_KEYS) if float(self.p[f"u_{k}"]) > 0]

    def next_bets(self):
        return [(s, self.unit * u) for s, u in self.layout]


COMMON_PARAMS = [
    num("stop_loss_pct", "Stop loss (% of bankroll, 0=off)", 0, 0, 100),
    num("take_profit_pct", "Take profit (% of bankroll, 0=off)", 0, 0, 1000),
    num("max_bet", "Max total bet per spin (0=table max)", 0, 0, 100000),
]


def registry_schemas() -> list[dict]:
    out = [cls.schema() for cls in REGISTRY.values()]
    for s in out:
        s["common_params"] = COMMON_PARAMS
    return out


def create(name: str, params: dict, bet_unit: float, bankroll: float,
           wheel: Wheel, seed: int = 0) -> Strategy:
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy: {name}")
    return REGISTRY[name](params, bet_unit, bankroll, wheel, seed)
