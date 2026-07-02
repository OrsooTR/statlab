"""Cross-source aggregation ("auto" provider).

Queries every enabled source concurrently, merges fixtures that refer to the
same real-world match (same day + fuzzy team-name similarity across sources,
e.g. "Bayern München" vs "Bayern Munich"), keeps the richest source as the
primary record, fills missing scores from the others, and annotates each match
with every source that reported it. Detail requests are routed back to the
owning source via the id namespace (espn:…, oldb:…, numeric → API-Football).
"""
from __future__ import annotations

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

from . import provider as base
from .espn_source import ESPNSource
from .openligadb_source import OpenLigaDBSource

# richer sources win the primary-record election
SOURCE_RANK = {"espn": 3, "api_football": 2, "openligadb": 1}

STOPWORDS = {"fc", "afc", "cf", "sc", "ac", "as", "ss", "ssc", "us", "cd", "rc",
             "sv", "vfb", "vfl", "bsc", "tsg", "rb", "1", "04", "05", "09", "1899",
             "borussia", "real", "club", "de", "the", "team", "calcio", "united"}


def _norm_tokens(name: str) -> set[str]:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    tokens = set(re.findall(r"[a-z]+", s.lower()))
    core = tokens - STOPWORDS
    return core or tokens


def _team_sim(a: str, b: str) -> float:
    ta, tb = _norm_tokens(a), _norm_tokens(b)
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    return inter / min(len(ta), len(tb))


def same_fixture(m1: dict, m2: dict) -> bool:
    if (m1.get("kickoff") or "")[:10] != (m2.get("kickoff") or "")[:10]:
        return False
    return (_team_sim(m1["home"], m2["home"]) >= 0.5
            and _team_sim(m1["away"], m2["away"]) >= 0.5)


def merge_matches(lists: list[list[dict]]) -> list[dict]:
    merged: list[dict] = []
    for lst in lists:
        for m in lst:
            hit = next((x for x in merged if same_fixture(x, m)), None)
            if hit is None:
                merged.append(dict(m))
                continue
            # keep the richer record as primary
            cur_rank = SOURCE_RANK.get((hit.get("sources") or ["?"])[0], 0)
            new_rank = SOURCE_RANK.get((m.get("sources") or ["?"])[0], 0)
            primary, secondary = (m, hit) if new_rank > cur_rank else (hit, m)
            out = dict(primary)
            out["sources"] = sorted(set((hit.get("sources") or []) + (m.get("sources") or [])),
                                    key=lambda s: -SOURCE_RANK.get(s, 0))
            # fill gaps from the secondary record
            for key in ("score_home", "score_away", "minute", "kickoff"):
                if out.get(key) is None and secondary.get(key) is not None:
                    out[key] = secondary[key]
            out["live"] = bool(primary.get("live") or secondary.get("live"))
            out["finished"] = bool(primary.get("finished") and
                                   (secondary.get("finished") or len(out["sources"]) == 1)) \
                or bool(primary.get("finished") or secondary.get("finished"))
            merged[merged.index(hit)] = out
    return merged


class AutoAggregator:
    name = "auto"
    is_demo = False

    def __init__(self) -> None:
        self.sources: list = [ESPNSource(), OpenLigaDBSource()]
        settings = base.load_settings()
        if settings.get("api_key"):
            self.sources.append(base.ApiFootballProvider(settings["api_key"]))

    def status(self) -> dict:
        return {"provider": "auto", "demo": False,
                "sources": [s.status() for s in self.sources],
                "note": "Cross-referenced multi-source feed (no key required); "
                        "adding an API-Football key enriches it further."}

    def _gather(self, method: str) -> list[dict]:
        lists: list[list[dict]] = []
        with ThreadPoolExecutor(max_workers=len(self.sources)) as pool:
            futures = {pool.submit(getattr(s, method)): s for s in self.sources}
            for fut in as_completed(futures):
                try:
                    result = fut.result()
                    src = futures[fut]
                    for m in result:
                        m.setdefault("sources", [getattr(src, "name", "?")])
                    lists.append(result)
                except Exception:
                    continue  # one broken source never breaks the feed
        return merge_matches(lists)

    def live_matches(self) -> list[dict]:
        return [m for m in self._gather("live_matches") if m.get("live")]

    def today_matches(self) -> list[dict]:
        return self._gather("today_matches")

    def match_detail(self, fixture_id: str) -> dict:
        owner: Optional[object] = None
        if fixture_id.startswith("espn:"):
            owner = next((s for s in self.sources if s.name == "espn"), None)
        elif fixture_id.startswith("oldb:"):
            owner = next((s for s in self.sources if s.name == "openligadb"), None)
        elif fixture_id.isdigit():
            owner = next((s for s in self.sources if s.name == "api_football"), None)
        if owner is None:
            raise KeyError(f"no source owns fixture id '{fixture_id}'")
        return owner.match_detail(fixture_id)
