"""Resolve national-team names from live feeds to the results dataset."""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from . import data as intl_data

# feed spelling -> dataset spelling for the frequent mismatches
NATION_ALIASES = {
    "usa": "United States", "united states of america": "United States",
    "south korea": "South Korea", "korea republic": "South Korea",
    "north korea": "North Korea", "korea dpr": "North Korea",
    "ir iran": "Iran", "iran": "Iran",
    "china pr": "China", "china": "China",
    "czechia": "Czech Republic", "czech republic": "Czech Republic",
    "turkiye": "Turkey", "türkiye": "Turkey",
    "cote d ivoire": "Ivory Coast", "cote divoire": "Ivory Coast",
    "ivory coast": "Ivory Coast",
    "cape verde": "Cape Verde", "cabo verde": "Cape Verde",
    "bosnia and herzegovina": "Bosnia and Herzegovina", "bosnia": "Bosnia and Herzegovina",
    "dr congo": "DR Congo", "congo dr": "DR Congo",
    "republic of ireland": "Republic of Ireland", "ireland": "Republic of Ireland",
    "north macedonia": "North Macedonia", "macedonia": "North Macedonia",
    "uae": "United Arab Emirates",
}


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s.lower()).split())


def resolve_nation(name: str) -> Optional[str]:
    f = _fold(name)
    if f in NATION_ALIASES:
        return NATION_ALIASES[f]
    nations = intl_data.all_nations()
    by_fold = {_fold(n): n for n in nations}
    if f in by_fold:
        return by_fold[f]
    # token containment (e.g. "Rep. of Korea")
    want = set(f.split())
    best, best_score = None, 0.0
    for fold, orig in by_fold.items():
        tt = set(fold.split())
        if not tt:
            continue
        score = len(want & tt) / min(len(want), len(tt))
        if score > best_score:
            best, best_score = orig, score
    return best if best_score >= 0.6 else None
