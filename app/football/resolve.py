"""Resolve external (live-feed) team names to the historical database.

Live feeds (ESPN, OpenLigaDB) and football-data.co.uk spell teams differently
("Bayern Munich" vs "Bayern Munich", "Man City" vs "Man City", "Internazionale"
vs "Inter"). This module maps a feed name to the closest team present in the
historical store for a given league, using accent-folded token overlap with a
curated alias table for the frequent mismatches.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

from . import database as store

# curated aliases: normalised feed token-set → football-data canonical name.
ALIASES = {
    "manchester city": "Man City", "man city": "Man City",
    "manchester united": "Man United", "man united": "Man Utd", "man utd": "Man United",
    "internazionale": "Inter", "inter milan": "Inter",
    "ac milan": "Milan",
    "tottenham hotspur": "Tottenham", "spurs": "Tottenham",
    "wolverhampton wanderers": "Wolves", "wolverhampton": "Wolves",
    "newcastle united": "Newcastle", "west ham united": "West Ham",
    "brighton hove albion": "Brighton", "brighton and hove albion": "Brighton",
    "nottingham forest": "Nott'm Forest", "nottingham": "Nott'm Forest",
    "leicester city": "Leicester", "norwich city": "Norwich", "hull city": "Hull",
    "paris saint germain": "Paris SG", "psg": "Paris SG",
    "bayern munich": "Bayern Munich", "bayern munchen": "Bayern Munich",
    "borussia dortmund": "Dortmund", "borussia monchengladbach": "M'gladbach",
    "bayer leverkusen": "Leverkusen", "eintracht frankfurt": "Ein Frankfurt",
    "atletico madrid": "Ath Madrid", "athletic club": "Ath Bilbao",
    "athletic bilbao": "Ath Bilbao", "real betis": "Betis",
    "real sociedad": "Sociedad", "celta vigo": "Celta", "deportivo alaves": "Alaves",
    "real valladolid": "Valladolid", "rayo vallecano": "Vallecano",
    "sporting cp": "Sp Lisbon", "sporting lisbon": "Sp Lisbon",
    "fc porto": "Porto", "sl benfica": "Benfica",
    "olympique marseille": "Marseille", "olympique lyonnais": "Lyon",
}

STOP = {"fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "cd", "rc", "sv",
        "vfb", "vfl", "bsc", "tsg", "rb", "cp", "sl", "club", "de", "1899", "the"}


def _fold(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9 ]", " ", s.lower()).strip()


def _tokens(name: str) -> set[str]:
    toks = set(_fold(name).split())
    core = toks - STOP
    return core or toks


def resolve_team(name: str, league: str) -> Optional[str]:
    """Best-matching historical team name for a feed team name, or None."""
    folded = _fold(name)
    if folded in ALIASES:
        alias = ALIASES[folded]
    else:
        alias = None
    teams = store.get_teams(league, last_n_seasons=3)
    if not teams:
        return None
    if alias and alias in teams:
        return alias
    # exact fold match
    by_fold = {_fold(t): t for t in teams}
    if folded in by_fold:
        return by_fold[folded]
    if alias and _fold(alias) in by_fold:
        return by_fold[_fold(alias)]
    # token-overlap best match
    want = _tokens(alias or name)
    best, best_score = None, 0.0
    for t in teams:
        tt = _tokens(t)
        if not tt or not want:
            continue
        score = len(want & tt) / min(len(want), len(tt))
        # small boost for a shared longest token (e.g. "bayern")
        if want & tt and max((len(x) for x in want & tt), default=0) >= 5:
            score += 0.15
        if score > best_score:
            best, best_score = t, score
    return best if best_score >= 0.5 else None
