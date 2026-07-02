/* Football AI Prediction Engine views. */
"use strict";

(() => {
  const { register, get, post, pollJob, toast, chart, baseOpts, PALETTE,
          esc, fmt, pct, statCard, probBar } = App;

  let COMPS = null;

  async function comps() {
    if (!COMPS) COMPS = await get("/api/football/competitions");
    return COMPS;
  }

  const leagueOpts = (list, withData = false) => list
    .filter((c) => !withData || c.matches > 0)
    .map((c) => `<option value="${c.code}">${esc(c.name)} (${esc(c.country)})</option>`).join("");

  // ================================================================ dashboard
  register("fb/dashboard", async (view) => {
    const d = await get("/api/football/dashboard");
    const bt = d.latest_backtest;
    const fixtures = d.upcoming_fixtures.slice(0, 14).map((f) => `
      <tr><td>${esc(f.date)} ${esc(f.time || "")}</td><td>${esc(f.league)}</td>
        <td>${esc(f.home)} – ${esc(f.away)}</td>
        <td>${f.b365h ? `${f.b365h} / ${f.b365d} / ${f.b365a}` : "–"}</td></tr>`).join("");
    const preds = d.recent_predictions.map((p) => {
      const pr = p.probabilities || {};
      const outcome = p.actual_result
        ? (bestOutcome(pr) === p.actual_result
            ? `<span class="pill pill-good">✓ ${p.actual_result}</span>`
            : `<span class="pill pill-bad">✗ ${p.actual_result}</span>`)
        : `<span class="pill pill-muted">pending</span>`;
      return `<tr><td>${esc(p.date)}</td><td>${esc(p.home)} – ${esc(p.away)}</td>
        <td>${esc(p.predicted_scoreline || "")}</td>
        <td>${p.confidence_pct}%</td><td>${outcome}</td></tr>`;
    }).join("");
    view.innerHTML = `
      <div class="page-title">Football Dashboard</div>
      <div class="page-sub">Calibrated probabilities from a seven-model ensemble.
        Estimates, never certainties.</div>
      <div class="grid grid-4 mb">
        ${statCard("Settled predictions", d.settled_count, "", "with known results")}
        ${statCard("Live accuracy", d.settled_accuracy !== null ? pct(d.settled_accuracy) : "–",
                   d.settled_accuracy > 0.5 ? "good" : "", "argmax vs result")}
        ${statCard("Backtest accuracy", bt ? pct(bt.accuracy) : "–", "", bt ? `log loss ${bt.log_loss}` : "run a backtest")}
        ${statCard("Backtest ROI", bt && bt.roi !== null ? pct(bt.roi) : "–",
                   bt && bt.roi > 0 ? "good" : "bad", "value betting vs B365")}
      </div>
      <div class="grid grid-2">
        <div class="card"><div class="section-title">📅 Upcoming fixtures</div>
          ${fixtures ? `<div class="table-wrap"><table>
            <thead><tr><th>Date</th><th>League</th><th>Match</th><th>1X2 odds</th></tr></thead>
            <tbody>${fixtures}</tbody></table></div>`
            : `<div class="empty">No fixtures feed available (season break or offline).</div>`}
        </div>
        <div class="card"><div class="section-title">🎯 Recent predictions</div>
          ${preds ? `<div class="table-wrap"><table>
            <thead><tr><th>Date</th><th>Match</th><th>Score</th><th>Conf.</th><th>Result</th></tr></thead>
            <tbody>${preds}</tbody></table></div>`
            : `<div class="empty">No predictions yet — try the Match Predictor.</div>`}
        </div>
      </div>
      ${bt && bt.calibration && bt.calibration.length ? `
      <div class="card mt"><div class="section-title">🎚️ Model calibration (latest backtest)</div>
        <div class="chart-box"><canvas id="fb-calib"></canvas></div></div>` : ""}`;

    if (bt && bt.calibration && bt.calibration.length) {
      chart("fb-calib", {
        type: "line",
        data: { labels: bt.calibration.map((c) => c.bin_mid),
          datasets: [
            { label: "observed frequency", data: bt.calibration.map((c) => c.observed),
              borderColor: "#22d3ee", pointRadius: 4, borderWidth: 2 },
            { label: "perfect calibration", data: bt.calibration.map((c) => c.bin_mid),
              borderColor: "rgba(255,255,255,.25)", borderDash: [6, 4], pointRadius: 0 },
          ]},
        options: baseOpts(),
      });
    }
  });

  const bestOutcome = (pr) =>
    ({ home: "H", draw: "D", away: "A" })[["home", "draw", "away"]
      .reduce((a, b) => (pr[a] || 0) >= (pr[b] || 0) ? a : b)];

  // ================================================================== predict
  register("fb/predict", async (view) => {
    const c = await comps();
    view.innerHTML = `
      <div class="page-title">Match Predictor</div>
      <div class="page-sub">Poisson · Dixon-Coles · Elo · SPI · Gradient Boosting ·
        Random Forest · Neural Network → log-loss-weighted ensemble → 10,000-run
        Monte Carlo match simulation.</div>
      <div class="grid grid-side">
        <div class="card">
          <div class="section-title">⚙️ Fixture</div>
          <label class="field"><span>League</span>
            <select id="pr-league">${leagueOpts(c.competitions, true)}</select></label>
          <label class="field"><span>Home team</span><select id="pr-home"></select></label>
          <label class="field"><span>Away team</span><select id="pr-away"></select></label>
          <label class="field"><span>Match date (optional)</span>
            <input id="pr-date" type="date"></label>
          <details><summary class="muted" style="cursor:pointer">Bookmaker odds (optional, improves ML features)</summary>
            <div class="grid grid-3 mt">
              <label class="field"><span>Home</span><input id="pr-oh" type="number" step="0.01" min="1.01"></label>
              <label class="field"><span>Draw</span><input id="pr-od" type="number" step="0.01" min="1.01"></label>
              <label class="field"><span>Away</span><input id="pr-oa" type="number" step="0.01" min="1.01"></label>
            </div></details>
          <button class="btn mt" id="pr-run" style="width:100%">🎯 Predict match</button>
          <div class="progress-track" id="pr-track" style="display:none">
            <div class="progress-bar" id="pr-progress"></div></div>
          <div class="faint mt">First prediction per league fits all models (~10-30s);
            subsequent ones are instant.</div>
        </div>
        <div id="pr-out"><div class="card empty">Choose a fixture. If the league has no
          data yet, load it in the Data Manager first.</div></div>
      </div>`;

    const loadTeams = async () => {
      const lg = document.getElementById("pr-league").value;
      const teams = await get(`/api/football/teams?league=${lg}`);
      const opts = teams.map((t) => `<option>${esc(t)}</option>`).join("");
      document.getElementById("pr-home").innerHTML = opts;
      document.getElementById("pr-away").innerHTML = opts;
      if (teams.length > 1) document.getElementById("pr-away").selectedIndex = 1;
    };
    if (c.competitions.some((x) => x.matches > 0)) await loadTeams();
    document.getElementById("pr-league").addEventListener("change", loadTeams);

    document.getElementById("pr-run").addEventListener("click", async () => {
      const btn = document.getElementById("pr-run");
      const body = {
        league: document.getElementById("pr-league").value,
        home: document.getElementById("pr-home").value,
        away: document.getElementById("pr-away").value,
      };
      if (body.home === body.away) { toast("Pick two different teams", "err"); return; }
      const dt = document.getElementById("pr-date").value;
      if (dt) body.date = dt;
      const oh = parseFloat(document.getElementById("pr-oh").value);
      const od = parseFloat(document.getElementById("pr-od").value);
      const oa = parseFloat(document.getElementById("pr-oa").value);
      if (oh && od && oa) body.odds = { home: oh, draw: od, away: oa };
      btn.disabled = true;
      document.getElementById("pr-track").style.display = "";
      try {
        const { job_id } = await post("/api/football/predict", body);
        const p = await pollJob("/api/football", job_id, (j) => {
          document.getElementById("pr-progress").style.width = (j.progress * 100) + "%";
        });
        renderPrediction(document.getElementById("pr-out"), p);
      } catch (err) { toast(err.message, "err", 9000); }
      finally {
        btn.disabled = false;
        document.getElementById("pr-track").style.display = "none";
      }
    });
  });

  function renderPrediction(box, p) {
    const pr = p.probabilities;
    const riskCls = { low: "pill-good", medium: "pill-warn", high: "pill-bad" }[p.risk];
    const alt = p.alternatives.map((a) =>
      `<span class="pill pill-muted" style="margin:3px">${a.score} · ${pct(a.probability)}</span>`).join("");
    const ou = p.markets.over_under;
    box.innerHTML = `
      <div class="card mb">
        <div style="display:flex;justify-content:space-between;align-items:start;flex-wrap:wrap;gap:10px">
          <div>
            <div class="page-title" style="font-size:20px">${esc(p.home)} vs ${esc(p.away)}</div>
            <div class="faint">${esc(p.league)} · ${esc(p.date)}</div>
          </div>
          <div style="text-align:right">
            <div class="value" style="font-size:30px;font-weight:800">${esc(p.predicted_scoreline)}</div>
            <div class="faint">most likely scoreline (${pct(p.scoreline_probability)})</div>
          </div>
        </div>
        <div class="mt">${probBar(pr.home, pr.draw, pr.away)}</div>
        <div class="faint" style="display:flex;justify-content:space-between;margin-top:4px">
          <span>${esc(p.home)}</span><span>draw</span><span>${esc(p.away)}</span></div>
        <div class="mt">
          <span class="pill pill-accent">confidence ${p.confidence_pct}%</span>
          <span class="pill ${riskCls}">risk: ${p.risk}</span>
          <span class="pill pill-muted">xG ${p.expected_goals.home} – ${p.expected_goals.away}</span>
          <span class="pill pill-muted">model disagreement ${fmt(p.model_disagreement, 3)}</span>
        </div>
        <div class="mt"><b class="muted">Alternative outcomes:</b><br>${alt}</div>
        <div class="mt">
          <button class="btn small ghost" data-fmt="pdf">PDF</button>
          <button class="btn small ghost" data-fmt="xlsx">Excel</button>
          <button class="btn small ghost" data-fmt="csv">CSV</button>
          <button class="btn small ghost" data-fmt="json">JSON</button>
        </div>
      </div>
      <div class="grid grid-2 mb">
        <div class="card"><div class="section-title">🧠 Reasoning</div>
          ${p.reasoning.map((r) => `<div class="muted mb" style="line-height:1.55">• ${esc(r)}</div>`).join("")}
          <div class="faint mt">${esc(p.disclaimer)}</div></div>
        <div class="card"><div class="section-title">📦 Markets</div>
          <div class="table-wrap"><table><tbody>
            <tr><td>Both teams to score</td><td>${pct(p.markets.btts)}</td></tr>
            <tr><td>Clean sheet ${esc(p.home)}</td><td>${pct(p.markets.clean_sheet_home)}</td></tr>
            <tr><td>Clean sheet ${esc(p.away)}</td><td>${pct(p.markets.clean_sheet_away)}</td></tr>
            <tr><td>Over 1.5 goals</td><td>${pct(ou["1.5"].over)}</td></tr>
            <tr><td>Over 2.5 goals</td><td>${pct(ou["2.5"].over)}</td></tr>
            <tr><td>Over 3.5 goals</td><td>${pct(ou["3.5"].over)}</td></tr>
            ${p.corners ? `<tr><td>Corners (expected / over 9.5)</td>
              <td>${fmt(p.corners.expected_total, 1)} / ${pct(p.corners.over_9_5)}</td></tr>` : ""}
            ${p.cards ? `<tr><td>Cards (expected / over 3.5)</td>
              <td>${fmt(p.cards.expected_total, 1)} / ${pct(p.cards.over_3_5)}</td></tr>` : ""}
          </tbody></table></div></div>
      </div>
      <div class="grid grid-2">
        <div class="card"><div class="section-title">⚽ Goal distribution</div>
          <div class="chart-box short"><canvas id="pr-goals"></canvas></div></div>
        <div class="card"><div class="section-title">🤖 Model breakdown (P home / draw / away)</div>
          <div class="chart-box short"><canvas id="pr-models"></canvas></div></div>
      </div>`;

    box.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", async () => {
      try {
        const { path } = await post("/api/football/export",
          { kind: "prediction", id: p.id, format: b.dataset.fmt });
        toast(`Exported → ${path}`, "ok", 8000);
      } catch (err) { toast(err.message, "err"); }
    }));

    const gd = p.markets.goal_distribution;
    chart("pr-goals", {
      type: "bar",
      data: { labels: gd.home.map((_, i) => i),
        datasets: [
          { label: p.home, data: gd.home.map((v) => v * 100), backgroundColor: "#6d5df6" },
          { label: p.away, data: gd.away.map((v) => v * 100), backgroundColor: "#22d3ee" },
        ]},
      options: baseOpts({ y: { ticks: { callback: (v) => v + "%" } } }),
    });
    const models = Object.keys(p.model_breakdown);
    chart("pr-models", {
      type: "bar",
      data: { labels: models,
        datasets: [
          { label: "home", data: models.map((m) => (p.model_breakdown[m].p_home || 0) * 100), backgroundColor: "#6d5df6" },
          { label: "draw", data: models.map((m) => (p.model_breakdown[m].p_draw || 0) * 100), backgroundColor: "rgba(255,255,255,.25)" },
          { label: "away", data: models.map((m) => (p.model_breakdown[m].p_away || 0) * 100), backgroundColor: "#22d3ee" },
        ]},
      options: baseOpts({ x: { stacked: true }, y: { stacked: true, max: 100,
        ticks: { callback: (v) => v + "%" } } }),
    });
  }

  // ===================================================================== day
  register("fb/day", async (view) => {
    const c = await comps();
    const today = new Date().toISOString().slice(0, 10);
    view.innerHTML = `
      <div class="page-title">Daily Fixtures — batch predictions</div>
      <div class="page-sub">Predicts every fixture of the day (bookmaker odds imported
        automatically when the feed provides them) and ranks by confidence.</div>
      <div class="card mb">
        <div class="grid grid-3">
          <label class="field"><span>Date</span><input id="day-date" type="date" value="${today}"></label>
          <label class="field"><span>League (optional)</span>
            <select id="day-league"><option value="">All leagues with data</option>
              ${leagueOpts(c.competitions, true)}</select></label>
          <label class="field"><span>&nbsp;</span>
            <button class="btn" id="day-run" style="width:100%">Predict the day</button></label>
        </div>
        <div class="progress-track" id="day-track" style="display:none">
          <div class="progress-bar" id="day-progress"></div></div>
        <div class="faint" id="day-msg"></div>
      </div>
      <div id="day-out"></div>`;

    document.getElementById("day-run").addEventListener("click", async () => {
      const btn = document.getElementById("day-run");
      btn.disabled = true;
      document.getElementById("day-track").style.display = "";
      try {
        const { job_id } = await post("/api/football/predict-day", {
          date: document.getElementById("day-date").value,
          league: document.getElementById("day-league").value || null,
        });
        const r = await pollJob("/api/football", job_id, (j) => {
          document.getElementById("day-progress").style.width = (j.progress * 100) + "%";
          document.getElementById("day-msg").textContent = j.message || "";
        });
        window.__dayPredictions = r.predictions; // feeds the slip builder
        renderDay(document.getElementById("day-out"), r);
      } catch (err) { toast(err.message, "err", 8000); }
      finally {
        btn.disabled = false;
        document.getElementById("day-track").style.display = "none";
        document.getElementById("day-msg").textContent = "";
      }
    });
  });

  function renderDay(box, r) {
    if (!r.predictions.length) {
      box.innerHTML = `<div class="card empty">${esc(r.message || "No predictable fixtures on this date.")}
        ${r.skipped && r.skipped.length ? `<div class="faint mt">${r.skipped.length} fixtures skipped
        (league data not loaded).</div>` : ""}</div>`;
      return;
    }
    box.innerHTML = r.predictions.map((p) => {
      const pr = p.probabilities;
      const riskCls = { low: "pill-good", medium: "pill-warn", high: "pill-bad" }[p.risk];
      const oddsTxt = p.odds ? `odds ${p.odds.home} / ${p.odds.draw} / ${p.odds.away}` : "no odds feed";
      return `<div class="card mb">
        <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px;align-items:center">
          <div><b>${esc(p.home)} vs ${esc(p.away)}</b>
            <span class="faint">· ${esc(p.league)} · ${esc(p.date)} · ${oddsTxt}</span></div>
          <div><span class="pill pill-accent">${esc(p.predicted_scoreline)}</span>
            <span class="pill pill-muted">conf ${p.confidence_pct}%</span>
            <span class="pill ${riskCls}">${p.risk}</span></div>
        </div>
        <div class="mt">${probBar(pr.home, pr.draw, pr.away)}</div>
      </div>`;
    }).join("") + `<div class="faint mb">These predictions are now available as candidates
      in the Slip Builder.</div>`;
  }

  // ==================================================================== slips
  register("fb/slips", async (view) => {
    const candidates = [];
    const fromDay = window.__dayPredictions || [];
    fromDay.forEach((p) => {
      const pr = p.probabilities;
      const picks = [["home", pr.home, p.odds && p.odds.home],
                     ["draw", pr.draw, p.odds && p.odds.draw],
                     ["away", pr.away, p.odds && p.odds.away]];
      const best = picks.reduce((a, b) => (a[1] >= b[1] ? a : b));
      candidates.push({
        match: `${p.home} vs ${p.away}`, league: p.league, date: p.date,
        home: p.home, away: p.away, market: "1X2",
        selection: best[0], probability: best[1], odds: best[2] || null,
      });
    });

    view.innerHTML = `
      <div class="page-title">Accumulator Slip Builder</div>
      <div class="page-sub">Builds 2–10 leg slips from your candidates, one selection per
        match, ranked by expected value. Odds import automatically from the fixtures feed
        (via Daily Fixtures) or can be entered manually.</div>
      <div class="grid grid-side">
        <div class="card">
          <div class="section-title">➕ Add candidate manually</div>
          <label class="field"><span>Match label</span>
            <input id="sl-match" placeholder="Arsenal vs Chelsea"></label>
          <label class="field"><span>Selection</span>
            <select id="sl-sel"><option value="home">Home win</option>
              <option value="draw">Draw</option><option value="away">Away win</option></select></label>
          <label class="field"><span>Estimated probability (0–1) — from a prediction</span>
            <input id="sl-prob" type="number" min="0.01" max="0.99" step="0.01" value="0.55"></label>
          <label class="field"><span>Bookmaker odds (decimal)</span>
            <input id="sl-odds" type="number" min="1.01" step="0.01" value="1.9"></label>
          <button class="btn ghost" id="sl-add" style="width:100%">Add candidate</button>
          <hr style="border-color:var(--border);margin:16px 0">
          <label class="field"><span>Selections per slip</span>
            <select id="sl-size">${[2,3,4,5,6,7,8,9,10].map((n) =>
              `<option ${n === 3 ? "selected" : ""}>${n}</option>`).join("")}</select></label>
          <button class="btn" id="sl-build" style="width:100%">🧾 Build ranked slips</button>
        </div>
        <div>
          <div class="card mb"><div class="section-title">🧺 Candidates (<span id="sl-count">${candidates.length}</span>)</div>
            <div id="sl-list"></div></div>
          <div id="sl-out"></div>
        </div>
      </div>`;

    const renderList = () => {
      document.getElementById("sl-count").textContent = candidates.length;
      const box = document.getElementById("sl-list");
      if (!candidates.length) {
        box.innerHTML = `<div class="muted">No candidates. Run Daily Fixtures to auto-import
          model picks, or add manually.</div>`;
        return;
      }
      box.innerHTML = `<div class="table-wrap"><table>
        <thead><tr><th>Match</th><th>Pick</th><th>Model P</th><th>Odds</th><th>EV</th><th></th></tr></thead>
        <tbody>${candidates.map((c, i) => {
          const ev = c.odds ? (c.probability * c.odds - 1) : null;
          return `<tr><td>${esc(c.match)}</td><td>${esc(c.selection)}</td>
            <td>${pct(c.probability)}</td>
            <td><input type="number" step="0.01" min="1.01" value="${c.odds || ""}"
                 data-odds="${i}" style="width:80px;padding:4px 8px"></td>
            <td class="${ev > 0 ? "good" : ev !== null ? "bad" : ""}">${ev !== null ? pct(ev) : "set odds"}</td>
            <td><a href="#" data-rm="${i}" style="color:#f87171;text-decoration:none">✕</a></td></tr>`;
        }).join("")}</tbody></table></div>`;
      box.querySelectorAll("[data-odds]").forEach((inp) => inp.addEventListener("change", () => {
        candidates[parseInt(inp.dataset.odds, 10)].odds = parseFloat(inp.value) || null;
        renderList();
      }));
      box.querySelectorAll("[data-rm]").forEach((a) => a.addEventListener("click", (e) => {
        e.preventDefault();
        candidates.splice(parseInt(a.dataset.rm, 10), 1);
        renderList();
      }));
    };
    renderList();

    document.getElementById("sl-add").addEventListener("click", () => {
      const match = document.getElementById("sl-match").value.trim();
      if (!match) { toast("Enter a match label", "err"); return; }
      candidates.push({
        match, market: "1X2",
        selection: document.getElementById("sl-sel").value,
        probability: parseFloat(document.getElementById("sl-prob").value),
        odds: parseFloat(document.getElementById("sl-odds").value),
      });
      renderList();
    });

    document.getElementById("sl-build").addEventListener("click", async () => {
      const size = parseInt(document.getElementById("sl-size").value, 10);
      const usable = candidates.filter((c) => c.odds && c.probability);
      try {
        const { slips } = await post("/api/football/slip/build",
          { candidates: usable, size });
        renderSlips(document.getElementById("sl-out"), slips);
      } catch (err) { toast(err.message, "err", 8000); }
    });
  });

  function renderSlips(box, slips) {
    box.innerHTML = slips.map((s) => `
      <div class="card mb">
        <div style="display:flex;justify-content:space-between;flex-wrap:wrap;gap:8px">
          <b>#${s.rank} — ${s.size}-leg accumulator</b>
          <div>
            <span class="pill pill-accent">odds ${s.combined_odds}</span>
            <span class="pill pill-muted">est. P ${pct(s.estimated_probability)}</span>
            <span class="pill ${s.expected_value > 0 ? "pill-good" : "pill-bad"}">EV ${pct(s.expected_value)}</span>
          </div>
        </div>
        <div class="table-wrap mt"><table>
          <thead><tr><th>Match</th><th>Pick</th><th>Model P</th><th>Odds</th><th>Implied P</th><th>Edge</th></tr></thead>
          <tbody>${s.selections.map((c) => `<tr>
            <td>${esc(c.match)}</td><td>${esc(c.selection)}</td>
            <td>${pct(c.estimated_probability)}</td><td>${c.odds}</td>
            <td>${pct(c.implied_probability)}</td>
            <td class="${c.value_diff > 0 ? "good" : "bad"}">${pct(c.value_diff)}</td></tr>`).join("")}
          </tbody></table></div>
      </div>`).join("") || `<div class="card empty">No slips could be built.</div>`;
  }

  // ================================================================= backtest
  register("fb/backtest", async (view) => {
    const c = await comps();
    view.innerHTML = `
      <div class="page-title">Backtesting Engine</div>
      <div class="page-sub">Walk-forward replay: models are fitted only on data strictly
        before each match, refit every 30 days. Includes a value-betting simulation
        against Bet365 opening odds with Closing Line Value.</div>
      <div class="grid grid-side">
        <div class="card">
          <div class="section-title">⚙️ Setup</div>
          <label class="field"><span>League</span>
            <select id="bt-league">${leagueOpts(c.competitions, true)}</select></label>
          <label class="field"><span>Test seasons</span>
            <select id="bt-seasons" multiple size="6"></select></label>
          <label class="field"><span>Value threshold (P × odds)</span>
            <input id="bt-thresh" type="number" value="1.06" min="1" max="1.5" step="0.01"></label>
          <button class="btn" id="bt-run" style="width:100%">⏮️ Run backtest</button>
          <div class="progress-track" id="bt-track" style="display:none">
            <div class="progress-bar" id="bt-progress"></div></div>
          <div class="faint" id="bt-msg"></div>
          <div class="faint mt">A full season backtest refits ~10 times and takes a few
            minutes per season.</div>
        </div>
        <div id="bt-out">
          <div class="card mb"><div class="section-title">📜 Previous backtests</div>
            <div id="bt-history"></div></div>
        </div>
      </div>`;

    const loadSeasons = async () => {
      const lg = document.getElementById("bt-league").value;
      const seasons = await get(`/api/football/seasons?league=${lg}`);
      document.getElementById("bt-seasons").innerHTML = seasons.slice().reverse().map((s, i) =>
        `<option value="${s}" ${i === 0 ? "selected" : ""}>20${s.slice(0, 2)}/${s.slice(2)}</option>`).join("");
    };
    if (c.competitions.some((x) => x.matches > 0)) await loadSeasons();
    document.getElementById("bt-league").addEventListener("change", loadSeasons);

    const loadHistory = async () => {
      const hist = await get("/api/football/backtests");
      document.getElementById("bt-history").innerHTML = hist.length
        ? `<div class="table-wrap"><table>
            <thead><tr><th>ID</th><th>League</th><th>Seasons</th><th>Acc.</th><th>Log loss</th>
              <th>ROI</th><th>CLV</th><th></th></tr></thead>
            <tbody>${hist.map((h) => `<tr><td>${h.id}</td><td>${esc(h.league)}</td>
              <td>${esc(h.seasons)}</td><td>${pct(h.summary.accuracy)}</td>
              <td>${h.summary.log_loss}</td>
              <td class="${h.summary.roi > 0 ? "good" : "bad"}">${h.summary.roi !== null ? pct(h.summary.roi) : "–"}</td>
              <td>${h.summary.clv !== null ? pct(h.summary.clv) : "–"}</td>
              <td><a href="#" data-view="${h.id}" style="color:var(--accent2)">view</a></td></tr>`).join("")}
            </tbody></table></div>`
        : `<div class="muted">No backtests yet.</div>`;
      document.getElementById("bt-history").querySelectorAll("[data-view]").forEach((a) =>
        a.addEventListener("click", async (e) => {
          e.preventDefault();
          const d = await get(`/api/football/backtests/${a.dataset.view}`);
          renderBacktest(document.getElementById("bt-out"), { ...d.metrics, id: d.id },
                         d.league, d.seasons);
        }));
    };
    await loadHistory();

    document.getElementById("bt-run").addEventListener("click", async () => {
      const seasons = [...document.getElementById("bt-seasons").selectedOptions].map((o) => o.value);
      if (!seasons.length) { toast("Select at least one season", "err"); return; }
      const btn = document.getElementById("bt-run");
      btn.disabled = true;
      document.getElementById("bt-track").style.display = "";
      try {
        const { job_id } = await post("/api/football/backtest", {
          league: document.getElementById("bt-league").value,
          seasons,
          value_threshold: parseFloat(document.getElementById("bt-thresh").value),
        });
        const m = await pollJob("/api/football", job_id, (j) => {
          document.getElementById("bt-progress").style.width = (j.progress * 100) + "%";
          document.getElementById("bt-msg").textContent = j.message || "";
        });
        renderBacktest(document.getElementById("bt-out"), m,
                       document.getElementById("bt-league").value, seasons.join(","));
        toast("Backtest complete", "ok");
      } catch (err) { toast(err.message, "err", 9000); }
      finally {
        btn.disabled = false;
        document.getElementById("bt-track").style.display = "none";
        document.getElementById("bt-msg").textContent = "";
      }
    });
  });

  function renderBacktest(box, m, league, seasons) {
    const bet = m.betting;
    box.innerHTML = `
      <div class="grid grid-4 mb">
        ${statCard("Accuracy (1X2)", pct(m.accuracy), m.accuracy > 0.5 ? "good" : "", `${m.matches_evaluated} matches`)}
        ${statCard("Log loss", m.log_loss, m.log_loss < 1.0 ? "good" : "warn", "lower is better (~0.98 market)")}
        ${statCard("Brier score", m.brier_score, "", "lower is better")}
        ${statCard("Exact score hits", pct(m.exact_score_hit_rate), "", "predicted scoreline")}
      </div>
      <div class="grid grid-4 mb">
        ${statCard("Value bets", bet.bets_placed, "", `threshold ${bet.value_threshold}`)}
        ${statCard("Betting ROI", bet.roi !== null ? pct(bet.roi) : "–", bet.roi > 0 ? "good" : "bad",
                   bet.yield_pct !== null ? `yield ${bet.yield_pct}%` : "")}
        ${statCard("Max drawdown", fmt(bet.max_drawdown, 1) + "u", "warn", "flat 1-unit stakes")}
        ${statCard("Closing Line Value", bet.closing_line_value !== null ? pct(bet.closing_line_value) : "–",
                   bet.closing_line_value > 0 ? "good" : "", `${bet.clv_sample} bets with closing odds`)}
      </div>
      <div class="grid grid-2 mb">
        <div class="card"><div class="section-title">🎚️ Calibration curve</div>
          <div class="chart-box short"><canvas id="bt-calib"></canvas></div></div>
        <div class="card"><div class="section-title">💹 Value-betting bankroll (units)</div>
          <div class="chart-box short"><canvas id="bt-curve"></canvas></div></div>
      </div>
      <div class="card">
        <div class="section-title">🔎 Recent evaluated matches
          ${m.id ? `<span style="margin-left:auto">
            <button class="btn small ghost" data-fmt="pdf">PDF</button>
            <button class="btn small ghost" data-fmt="xlsx">Excel</button>
            <button class="btn small ghost" data-fmt="json">JSON</button></span>` : ""}</div>
        <div class="table-wrap"><table>
          <thead><tr><th>Date</th><th>Match</th><th>P(H/D/A)</th><th>Predicted</th><th>Actual</th><th>Result</th></tr></thead>
          <tbody>${m.sample.map((s) => `<tr><td>${esc(s.date)}</td>
            <td>${esc(s.home)} – ${esc(s.away)}</td>
            <td>${s.probs.map((p) => (p * 100).toFixed(0)).join(" / ")}</td>
            <td>${esc(s.pred_score || "–")}</td><td>${esc(s.actual_score)}</td>
            <td>${esc(s.result)}</td></tr>`).join("")}</tbody></table></div>
      </div>`;

    if (m.id) box.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", async () => {
      try {
        const { path } = await post("/api/football/export",
          { kind: "backtest", id: m.id, format: b.dataset.fmt });
        toast(`Exported → ${path}`, "ok", 8000);
      } catch (err) { toast(err.message, "err"); }
    }));

    chart("bt-calib", {
      type: "line",
      data: { labels: m.calibration.map((c) => c.bin_mid),
        datasets: [
          { label: "observed", data: m.calibration.map((c) => c.observed),
            borderColor: "#22d3ee", pointRadius: 4, borderWidth: 2 },
          { label: "perfect", data: m.calibration.map((c) => c.bin_mid),
            borderColor: "rgba(255,255,255,.25)", borderDash: [6, 4], pointRadius: 0 },
        ]},
      options: baseOpts(),
    });
    chart("bt-curve", {
      type: "line",
      data: { labels: bet.bankroll_curve.map((_, i) => i),
        datasets: [{ label: "P&L (units)", data: bet.bankroll_curve,
          borderColor: bet.profit >= 0 ? "#34d399" : "#f87171",
          backgroundColor: "rgba(52,211,153,.1)", fill: true, pointRadius: 0, borderWidth: 1.6 }]},
      options: baseOpts(),
    });
  }

  // ===================================================================== data
  register("fb/data", async (view) => {
    const render = async () => {
      COMPS = null;
      const c = await comps();
      view.innerHTML = `
        <div class="page-title">Data Manager</div>
        <div class="page-sub">Historical results, statistics and bookmaker odds from
          football-data.co.uk. Finished seasons are cached forever; the current season
          refreshes every 12 hours. Add competitions in <code>competitions.json</code>.</div>
        <div class="card">
          <div style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px">
            <div class="section-title" style="margin:0">💾 Competitions (season ${c.current_season})</div>
            <button class="btn" id="dm-refresh-all">⟳ Refresh all leagues</button>
          </div>
          <div class="progress-track" id="dm-track" style="display:none">
            <div class="progress-bar" id="dm-progress"></div></div>
          <div class="faint" id="dm-msg"></div>
          <div class="table-wrap mt"><table>
            <thead><tr><th>League</th><th>Country</th><th>Matches</th><th>Seasons</th>
              <th>Coverage</th><th></th></tr></thead>
            <tbody>${c.competitions.map((x) => `<tr>
              <td><b>${esc(x.name)}</b> <span class="faint">${x.code}</span></td>
              <td>${esc(x.country)}</td>
              <td>${x.matches ? fmt(x.matches, 0) : `<span class="pill pill-warn">no data</span>`}</td>
              <td>${x.seasons || "–"}</td>
              <td class="faint">${x.first_date ? `${x.first_date} → ${x.last_date}` : "–"}</td>
              <td><button class="btn small ghost" data-lg="${x.code}">refresh</button></td></tr>`).join("")}
            </tbody></table></div>
        </div>`;

      const runRefresh = async (league) => {
        document.getElementById("dm-track").style.display = "";
        try {
          const url = league ? `/api/football/refresh?league=${league}` : "/api/football/refresh";
          const { job_id } = await post(url, null);
          await pollJob("/api/football", job_id, (j) => {
            document.getElementById("dm-progress").style.width = (j.progress * 100) + "%";
            document.getElementById("dm-msg").textContent = j.message || "";
          });
          toast("Data refresh complete", "ok");
          await render();
        } catch (err) { toast(err.message, "err", 8000); }
      };
      document.getElementById("dm-refresh-all").addEventListener("click", () => runRefresh(null));
      view.querySelectorAll("[data-lg]").forEach((b) =>
        b.addEventListener("click", () => runRefresh(b.dataset.lg)));
    };
    await render();
  });
})();
