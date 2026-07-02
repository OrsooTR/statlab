"""Football match store on top of the shared SQLite database."""
from __future__ import annotations

from typing import Optional

import httpx

from ..core.database import read_conn, rows_to_dicts, write_conn
from . import data_sources as ds

MATCH_COLS = ["league", "season", "date", "home", "away", "fthg", "ftag", "ftr",
              "hthg", "htag", "hs", "as_", "hst", "ast", "hc", "ac", "hy", "ay",
              "hr", "ar", "hf", "af", "b365h", "b365d", "b365a",
              "b365ch", "b365cd", "b365ca", "avg_over25", "avg_under25", "extra_json"]


def upsert_matches(records: list[dict]) -> int:
    if not records:
        return 0
    cols = ",".join(MATCH_COLS)
    marks = ",".join("?" * len(MATCH_COLS))
    updates = ",".join(f"{c}=excluded.{c}" for c in MATCH_COLS
                       if c not in ("league", "date", "home", "away"))
    sql = (f"INSERT INTO fb_matches ({cols}) VALUES ({marks}) "
           f"ON CONFLICT(league, date, home, away) DO UPDATE SET {updates}")
    with write_conn() as conn:
        conn.executemany(sql, [tuple(r.get(c) for c in MATCH_COLS) for r in records])
    return len(records)


def refresh_league(progress, code: str) -> dict:
    """Download every season for a league and upsert into the store (job entry)."""
    comp = ds.competition(code)
    if comp is None:
        raise ValueError(f"unknown competition {code}")
    seasons = ds.season_codes(comp["first_season"])
    total = 0
    fetched = []
    with httpx.Client(timeout=ds.TIMEOUT, headers=ds.HEADERS, follow_redirects=True) as client:
        for i, season in enumerate(seasons):
            progress(i / len(seasons), f"{comp['name']} {ds.season_label(season)}")
            path = ds.fetch_season_csv(code, season, client)
            if path is None:
                continue
            records = ds.parse_season_csv(path, code, season)
            total += upsert_matches(records)
            if records:
                fetched.append(season)
    return {"league": code, "seasons_loaded": fetched, "matches_upserted": total}


def refresh_all(progress) -> dict:
    comps = ds.load_competitions()["competitions"]
    results = []
    for i, comp in enumerate(comps):
        def sub_progress(frac: float, msg: str) -> None:
            progress((i + frac) / len(comps), msg)
        results.append(refresh_league(sub_progress, comp["code"]))
    return {"leagues": results,
            "total_matches": sum(r["matches_upserted"] for r in results)}


# --------------------------------------------------------------------- queries
def get_matches(league: str, season: Optional[str] = None,
                before: Optional[str] = None, played_only: bool = True) -> list[dict]:
    sql = "SELECT * FROM fb_matches WHERE league=?"
    args: list = [league]
    if season:
        sql += " AND season=?"
        args.append(season)
    if before:
        sql += " AND date<?"
        args.append(before)
    if played_only:
        sql += " AND fthg IS NOT NULL AND ftag IS NOT NULL"
    sql += " ORDER BY date, id"
    with read_conn() as conn:
        return rows_to_dicts(conn.execute(sql, args).fetchall())


def get_teams(league: str, last_n_seasons: int = 2) -> list[str]:
    with read_conn() as conn:
        seasons = [r["season"] for r in conn.execute(
            "SELECT DISTINCT season FROM fb_matches WHERE league=? ORDER BY season DESC LIMIT ?",
            (league, last_n_seasons)).fetchall()]
        if not seasons:
            return []
        marks = ",".join("?" * len(seasons))
        rows = conn.execute(
            f"SELECT DISTINCT home AS t FROM fb_matches WHERE league=? AND season IN ({marks}) "
            f"UNION SELECT DISTINCT away FROM fb_matches WHERE league=? AND season IN ({marks}) "
            "ORDER BY t",
            [league, *seasons, league, *seasons]).fetchall()
    return [r["t"] for r in rows]


def get_seasons(league: str) -> list[str]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT season FROM fb_matches WHERE league=? ORDER BY season",
            (league,)).fetchall()
    return [r["season"] for r in rows]


def data_summary() -> list[dict]:
    with read_conn() as conn:
        rows = conn.execute(
            "SELECT league, COUNT(*) AS matches, MIN(date) AS first_date, "
            "MAX(date) AS last_date, COUNT(DISTINCT season) AS seasons "
            "FROM fb_matches GROUP BY league ORDER BY league").fetchall()
    summary = rows_to_dicts(rows)
    by_code = {s["league"]: s for s in summary}
    out = []
    for comp in ds.load_competitions()["competitions"]:
        entry = {"code": comp["code"], "name": comp["name"], "country": comp["country"],
                 "matches": 0, "seasons": 0, "first_date": None, "last_date": None}
        if comp["code"] in by_code:
            s = by_code[comp["code"]]
            entry.update(matches=s["matches"], seasons=s["seasons"],
                         first_date=s["first_date"], last_date=s["last_date"])
        out.append(entry)
    return out
