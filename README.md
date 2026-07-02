# StatLab — Statistical Simulation & Football Analytics Desktop App

A production desktop application with two fully independent modules behind one
premium dark-glassmorphism interface:

1. **Crazy Time Statistical Simulator** — a Monte Carlo laboratory that reproduces the
   Evolution Crazy Time wheel (54 segments in the physical layout, Top Slot, all four
   bonus games, calibrated to the published per-spot RTPs). Play it hands-on at the
   **Live Table** — chips, a running balance, single spins on the physical wheel and
   fully playable bonus rounds — or compare 23 betting strategies across millions of
   independent random spins, including your exact chip layout. It does **not** predict
   outcomes — it quantifies the risk profile of staking plans under a fixed house edge.
2. **Football AI Prediction Engine** — calibrated match probabilities from a seven-model
   ensemble (Poisson, Dixon-Coles, Elo, SPI-like ratings, gradient boosting, random
   forest, MLP neural network), with 10,000-run match simulation, walk-forward
   backtesting (accuracy, log loss, Brier, calibration, ROI, CLV), daily fixture
   predictions with imported bookmaker odds, an EV-ranked accumulator builder, and a
   **Live Center**: every match of the day auto-listed, live scores, statistics,
   lineups, substitutions, event timelines, momentum charts and in-play Poisson
   probability updates.

## Download (Windows)

From the [Releases page](../../releases):

- **StatLab-Setup.exe** — recommended. A proper Windows installer (Start-menu &
  desktop shortcuts, uninstaller). Once installed, the app updates **itself** from
  inside — open *Updates* and click *Download & install*; no manual re-download.
- **StatLab-windows.zip** — portable build; extract anywhere and run `StatLab.exe`.

No Python required. Your data (match database, reports, live settings) is stored in a
`data/` folder next to the app.

### Staying up to date
StatLab checks GitHub for new releases on startup and flags the **Updates** page.
On the installed build, one click downloads the new installer and applies it in place.

## Run from source

```powershell
pip install -r requirements.txt

# native desktop window (Edge WebView2)
python run_desktop.py

# — or — serve to your default browser
python run_server.py
```

## Live football data — no API key needed

The Live Center's default **auto** provider cross-references multiple open sources
with no registration: **ESPN public JSON feeds** (22 competitions: live scores,
statistics, lineups, events) and **OpenLigaDB** (German leagues), merging duplicate
fixtures by team-name matching and labelling every match with its sources. The full
source registry lives in `app/football/live/sources.json` — add an adapter and an
entry to plug in more.

Matches are grouped by **category** (international national-team games, continental
club cups, domestic leagues) then by country and competition, with a category filter —
so a World Cup night no longer mixes with club fixtures. Any match in a supported
league shows a **🎯 Predict** button that runs the full ensemble prediction on the fly
(feed team names are matched to the historical database and the league is downloaded on
demand).

### National teams & World Cup, with player markets
National-team matches (World Cup, Euro, any nation vs nation) get their own model built
from the open [international results dataset](https://github.com/martj42/international_results)
(~49k matches since 1872): a neutral-venue-aware, tournament-weighted Poisson + Elo +
Dixon-Coles engine. When the starting XIs are published, the **🎯 Predict + scorers**
button also produces **player markets** — anytime / first / 2+ goalscorer and penalty —
computed from real international goal data (each player's share of his team's goals scales
the model-expected goals into a personal Poisson rate), plus per-player analysis. Card
markets are shown as an honest positional estimate (no per-player international card log is
available without a paid feed). Club player markets require the optional API-Football key.

Honesty note: diretta.it / Flashscore / SofaScore / FotMob are deliberately **not**
scraped — their terms prohibit automated access and they run anti-bot protection;
they are listed as disabled in the registry with the reason documented.

Optionally, a free [api-football.com](https://www.api-football.com) key (100
requests/day) enriches the auto feed further; the app tracks the daily quota and
caches responses so the free tier is never burned by auto-refresh.

The app runs entirely on your machine at `http://127.0.0.1:8765`
(interactive API docs at `/docs`). First use of the football module:
open **Data Manager → Refresh all leagues** to download the historical match
database (football-data.co.uk, ~45k matches across 12 leagues, cached on disk).

## Tests

```powershell
python -m pytest tests -q
```

20 tests cover wheel calibration against published RTPs, bonus-game mechanics,
every betting strategy, Monte Carlo aggregation, all statistical models on a
synthetic league with known strengths, feature leakage, ensemble weighting,
market simulation and the slip builder.

## Layout

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full design: module
boundaries, data flows, database schema, API surface, caching, concurrency and
extension points (new strategies, new competitions, wheel updates — all
config-driven, no code changes).

```
app/core       shared config, SQLite, background jobs, PDF/Excel/CSV/JSON exports
app/crazytime  wheel model, bonus engines, Monte Carlo engine, 22 strategies, metrics
app/football   data pipeline, features, models/, ensemble, simulation, backtest, slips
app/static     SPA frontend (vanilla JS + vendored Chart.js, offline-capable)
data/          runtime database, download cache, generated reports
tests/         engine test suites
```

## Responsible use

- The Crazy Time module is a *simulator*. Every bet spot has negative expected value
  (95.9–96.1% RTP numbers, 94.3–95.8% bonuses); no staking plan changes that. The UI
  displays expected loss alongside every result.
- The football module outputs calibrated probabilities with risk indicators and
  model-disagreement diagnostics — estimates, never certainties, never financial advice.
