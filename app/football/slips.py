"""Accumulator (bet-slip) builder.

Selections carry a model probability and odds (imported from the data source
when available, else supplied manually). Slips of size 2–10 are enumerated
from the strongest candidates and ranked by expected value with a probability
tie-break. Combined probability assumes independence across matches, which is
sound for selections drawn from different fixtures.
"""
from __future__ import annotations

import itertools
from typing import Optional

MAX_CANDIDATES = 14      # enumeration pool cap: C(14,5)=2002 combos, instant
MAX_SLIPS_RETURNED = 12


def selection_metrics(prob: float, odds: Optional[float]) -> dict:
    implied = 1.0 / odds if odds and odds > 1 else None
    ev = prob * odds - 1.0 if odds and odds > 1 else None
    return {
        "estimated_probability": round(prob, 4),
        "odds": odds,
        "implied_probability": round(implied, 4) if implied is not None else None,
        "expected_value": round(ev, 4) if ev is not None else None,
        "value_diff": round(prob - implied, 4) if implied is not None else None,
    }


def build_slips(candidates: list[dict], size: int) -> list[dict]:
    """candidates: [{match, market, selection, probability, odds}, ...]"""
    if not 2 <= size <= 10:
        raise ValueError("slip size must be between 2 and 10")
    usable = [c for c in candidates
              if c.get("probability") and c.get("odds") and c["odds"] > 1]
    if len(usable) < size:
        raise ValueError(
            f"need at least {size} candidates with both probability and odds "
            f"(got {len(usable)})")
    # one selection per match — never combine correlated legs
    seen: dict[str, dict] = {}
    for c in usable:
        key = c.get("match", f"{c.get('home')}-{c.get('away')}")
        ev = c["probability"] * c["odds"] - 1
        if key not in seen or ev > seen[key]["probability"] * seen[key]["odds"] - 1:
            seen[key] = c
    pool = sorted(seen.values(), key=lambda c: c["probability"] * c["odds"], reverse=True)
    pool = pool[:MAX_CANDIDATES]
    if len(pool) < size:
        raise ValueError(f"only {len(pool)} independent matches available for a {size}-leg slip")

    slips = []
    for combo in itertools.combinations(pool, size):
        prob = 1.0
        odds = 1.0
        for c in combo:
            prob *= c["probability"]
            odds *= c["odds"]
        ev = prob * odds - 1.0
        slips.append({
            "selections": [
                {**{k: c.get(k) for k in ("match", "league", "date", "home", "away",
                                          "market", "selection")},
                 **selection_metrics(c["probability"], c["odds"])}
                for c in combo
            ],
            "size": size,
            "combined_odds": round(odds, 2),
            "estimated_probability": round(prob, 5),
            "implied_probability": round(1.0 / odds, 5),
            "expected_value": round(ev, 4),
            "value_diff": round(prob - 1.0 / odds, 5),
        })
    slips.sort(key=lambda s: (s["expected_value"], s["estimated_probability"]), reverse=True)
    for rank, s in enumerate(slips[:MAX_SLIPS_RETURNED], start=1):
        s["rank"] = rank
    return slips[:MAX_SLIPS_RETURNED]
