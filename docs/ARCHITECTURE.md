# StatLab Desktop — Architecture

StatLab is a production desktop application with two fully independent analytical modules
sharing one application shell:

1. **Crazy Time Statistical Simulator** — a Monte Carlo laboratory for comparing betting
   strategies against a faithful reproduction of the Evolution Crazy Time wheel.
   It does **not** predict outcomes; every spin is independent and random.
2. **Football AI Prediction Engine** — a calibrated-probability analytics platform built on
   an ensemble of statistical and machine-learning models, with match simulation,
   backtesting, and accumulator (bet-slip) construction.

---

## 1. Technology stack

| Layer            | Technology                                   | Why |
|------------------|----------------------------------------------|-----|
| Runtime          | Python 3.10                                  | Scientific stack, multiprocessing, single deployable runtime |
| API server       | FastAPI + Uvicorn (localhost only)           | Async, typed, OpenAPI-documented internal API |
| Desktop shell    | pywebview (Edge WebView2 on Windows)         | Native window hosting the UI; falls back to browser |
| Numerics         | NumPy / pandas                               | Vectorised Monte Carlo, data frames |
| ML               | scikit-learn                                 | Gradient boosting, random forest, MLP neural network, calibration |
| Persistence      | SQLite (stdlib `sqlite3`, WAL mode)          | Zero-install embedded DB |
| Reports          | reportlab (PDF), openpyxl (Excel), stdlib CSV/JSON | Export pipeline |
| Frontend         | Vanilla ES2020 SPA + Chart.js (vendored)     | No build step, fast startup, fully offline-capable UI |
| Concurrency      | `asyncio` (API), `ProcessPoolExecutor` (simulation), thread pool (downloads) | CPU-bound work off the event loop |

## 2. Folder hierarchy

```
Test TOP SICRET/
├── run_desktop.py            # Launch native desktop window (pywebview)
├── run_server.py             # Launch API+UI in the default browser
├── requirements.txt
├── README.md
├── docs/
│   └── ARCHITECTURE.md       # This document
├── data/                     # Runtime data (created on first run)
│   ├── statlab.db            # SQLite database
│   ├── cache/                # Downloaded football CSVs (etag-cached)
│   └── exports/              # Generated PDF/XLSX/CSV/JSON reports
├── app/
│   ├── __init__.py
│   ├── main.py               # FastAPI app factory, router mounting, static files
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py         # Paths, constants, settings loader
│   │   ├── database.py       # SQLite connection manager, schema migration
│   │   ├── jobs.py           # Async background-job manager with progress reporting
│   │   └── exports.py        # Shared PDF/Excel/CSV/JSON export toolkit
│   ├── crazytime/
│   │   ├── __init__.py
│   │   ├── wheel_config.json # Config-driven wheel layout, paytable, bonus params
│   │   ├── wheel.py          # Wheel model: 54 segments, Top Slot, probabilities
│   │   ├── bonus_games.py    # Cash Hunt, Coin Flip, Pachinko, Crazy Time engines
│   │   ├── outcomes.py       # Vectorised spin-outcome pre-generation
│   │   ├── strategies.py     # Strategy library (20 strategies, editable params)
│   │   ├── engine.py         # Monte Carlo runner, multiprocessing, chunking
│   │   ├── metrics.py        # Drawdown, risk of ruin, EV, volatility, streaks…
│   │   ├── reports.py        # Crazy Time PDF/Excel report builders
│   │   └── api.py            # /api/crazytime/* endpoints
│   └── football/
│       ├── __init__.py
│       ├── competitions.json # Config-driven league registry (adapts w/o code change)
│       ├── data_sources.py   # football-data.co.uk downloader, cache, CSV parser
│       ├── database.py       # Match store, feature store (SQLite)
│       ├── features.py       # Feature engineering (form, xG proxies, rest, H2H…)
│       ├── models/
│       │   ├── __init__.py
│       │   ├── poisson.py    # Maximum-likelihood attack/defence Poisson
│       │   ├── dixon_coles.py# Bivariate low-score correction + time decay
│       │   ├── elo.py        # Goal-margin Elo with home advantage
│       │   ├── spi.py        # SPI-like offence/defence composite rating
│       │   ├── ml.py         # GradientBoosting, RandomForest, MLP + calibration
│       │   └── ensemble.py   # Log-loss-weighted blend of all model outputs
│       ├── simulation.py     # Monte Carlo match simulator (scorelines, markets)
│       ├── predict.py        # Prediction orchestrator (fit → blend → simulate)
│       ├── backtest.py       # Walk-forward backtesting engine + metrics
│       ├── slips.py          # Accumulator builder, EV ranking, odds handling
│       ├── reports.py        # Football PDF/Excel report builders
│       └── api.py            # /api/football/* endpoints
├── app/static/               # SPA frontend
│   ├── index.html
│   ├── css/app.css           # Dark glassmorphism theme
│   ├── js/app.js             # Router, state, API client
│   ├── js/crazytime.js       # Module-1 views
│   ├── js/football.js        # Module-2 views
│   └── vendor/chart.umd.js   # Chart.js (vendored, offline)
└── tests/
    ├── test_crazytime.py
    └── test_football.py
```

## 3. Module boundaries & dependency rules

- `app.core` depends on nothing else inside `app`.
- `app.crazytime` and `app.football` depend only on `app.core`. **They never import
  each other** — either module can be removed or replaced without touching the other.
- `app.main` composes the two routers and the static frontend.
- The frontend mirrors this: `crazytime.js` and `football.js` are independent view
  bundles registered against the shared router in `app.js`.

## 4. Data flow

### 4.1 Crazy Time simulation

```
UI (strategy config, spins, bankroll)
  → POST /api/crazytime/simulate            (job created)
  → jobs.JobManager schedules run in ProcessPoolExecutor
      outcomes.generate(n)   — vectorised NumPy pre-generation of every spin:
                                wheel segment, Top Slot target+multiplier,
                                fully-resolved bonus multipliers per bet spot
      engine.run(strategy)   — sequential bankroll walk over the outcome arrays
                                (strategy state is path-dependent by definition)
      metrics.compute(...)   — full statistics block
  → UI polls GET /api/crazytime/jobs/{id}   (progress %, then results)
  → results persisted to SQLite for comparison dashboards & exports
```

Parallelism: a request for *R runs × N spins* fans out one process per run
(up to CPU count). Within a run, outcome pre-generation is vectorised; only the
bankroll walk is a tight Python loop over preallocated arrays.

### 4.2 Football prediction

```
competitions.json → data_sources.refresh()  — download CSVs (cached by season)
                  → database.upsert_matches()
UI: predict fixture (league, home, away, date)
  → predict.PredictionOrchestrator
      features.build(match)          — 40+ engineered features
      models: poisson, dixon_coles, elo, spi, ml.{gbm,rf,mlp}
      ensemble.blend()               — weights fitted on held-out log loss
      simulation.simulate(mu_h, mu_a, rho, n=10000)
  → response: 1X2 probabilities, scoreline grid, markets, reasoning,
    confidence, risk indicator, alternatives
Backtest: walk-forward over seasons — train on t<k, predict season k, score.
```

## 5. Database schema (SQLite)

```sql
-- shared
CREATE TABLE ct_simulations (
  id INTEGER PRIMARY KEY, created_at TEXT, name TEXT, strategy TEXT,
  params_json TEXT, spins INTEGER, runs INTEGER, bankroll REAL,
  results_json TEXT            -- full metrics block + downsampled curves
);
CREATE TABLE fb_matches (
  id INTEGER PRIMARY KEY, league TEXT, season TEXT, date TEXT,
  home TEXT, away TEXT, fthg INTEGER, ftag INTEGER, ftr TEXT,
  hs INTEGER, as_ INTEGER, hst INTEGER, ast INTEGER,
  hc INTEGER, ac INTEGER, hy INTEGER, ay INTEGER, hr INTEGER, ar INTEGER,
  b365h REAL, b365d REAL, b365a REAL, extra_json TEXT,
  UNIQUE(league, date, home, away)
);
CREATE TABLE fb_predictions (
  id INTEGER PRIMARY KEY, created_at TEXT, league TEXT, home TEXT, away TEXT,
  match_date TEXT, prediction_json TEXT, actual_result TEXT
);
CREATE TABLE fb_backtests (
  id INTEGER PRIMARY KEY, created_at TEXT, league TEXT, seasons TEXT,
  metrics_json TEXT
);
CREATE TABLE fb_slips (
  id INTEGER PRIMARY KEY, created_at TEXT, slip_json TEXT
);
```

WAL mode; one writer via a lock; readers unrestricted. JSON columns hold
nested analytical payloads that never need relational queries.

## 6. Internal API surface

All endpoints are `application/json`, served on `127.0.0.1:8765`, documented at `/docs`.

```
GET  /api/health
-- Crazy Time
GET  /api/crazytime/config           wheel layout, paytable, RTP table
GET  /api/crazytime/strategies       strategy registry + parameter schemas
POST /api/crazytime/simulate         {strategy, params, spins, runs, bankroll, bet_unit, seed?}
POST /api/crazytime/compare          {entries:[...], spins, runs, bankroll}
GET  /api/crazytime/jobs/{id}        job progress / result
GET  /api/crazytime/simulations      stored results
POST /api/crazytime/export           {simulation_ids, format: pdf|xlsx|csv|json}
-- Football
GET  /api/football/competitions
POST /api/football/refresh           download/update league data (job)
GET  /api/football/teams?league=
GET  /api/football/matches?league=&season=
POST /api/football/predict           {league, home, away, date?}
POST /api/football/predict-day       {league, date} → all fixtures
POST /api/football/backtest          {league, test_seasons, stake…} (job)
GET  /api/football/backtests
POST /api/football/slip/build        {candidates|auto, size, odds{manual|imported}}
GET  /api/football/dashboard         daily view, accuracy, calibration, ROI
POST /api/football/export            {kind, id, format}
GET  /api/football/jobs/{id}
```

## 7. Caching

- **Football CSVs** — cached on disk per (league, season); re-downloaded only when the
  season is current and the cache is older than 12 h.
- **Fitted models** — memoised per (league, training-cutoff) in-process (LRU) so
  predicting ten fixtures in one league fits the models once.
- **Wheel outcome tables** — Crazy Time segment/paytable arrays are built once per
  process from `wheel_config.json`.
- **Frontend** — static assets are immutable per app version; API responses are not
  cached client-side except the config/strategy registries.

## 8. Concurrency model

- Uvicorn event loop serves API + static UI.
- `core.jobs.JobManager` runs long tasks (simulations, downloads, backtests) on a
  `ProcessPoolExecutor` (CPU-bound) or a thread pool (I/O-bound) and exposes
  progress via polling — the UI shows live progress bars.
- Crazy Time multi-run simulations parallelise across runs (one independent RNG
  substream per run via `numpy.random.SeedSequence.spawn`).
- SQLite writes are serialised through a module-level lock.

## 9. Extensibility

- **Wheel changes** → edit `wheel_config.json` (segments, paytable, bonus parameters,
  Top Slot distribution). No code change.
- **New betting strategy** → subclass `Strategy`, declare `PARAMS`, register with
  `@register_strategy`. It appears in the UI automatically (parameter forms are
  generated from the schema).
- **New competition** → add an entry to `competitions.json` (code, name, source URL
  pattern, seasons). The downloader, models and UI adapt automatically.
- **New model** → implement `fit(matches)` / `predict(home, away)` returning
  (p_home, p_draw, p_away, mu_home, mu_away); register in `ensemble.MODEL_REGISTRY`.

## 10. Responsible-use posture

- Module 1 is explicitly a *simulator*: the UI states that no strategy changes the
  house edge and displays expected loss alongside every result.
- Module 2 outputs **calibrated probabilities with uncertainty**, never certainties;
  every prediction carries a risk indicator and calibration diagnostics.
