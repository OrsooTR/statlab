/* Crazy Time simulator views. */
"use strict";

(() => {
  const { register, get, post, del, pollJob, toast, chart, baseOpts, PALETTE,
          esc, fmt, pct, money, statCard } = App;

  let CONFIG = null;     // /api/crazytime/config
  let STRATEGIES = null; // /api/crazytime/strategies

  async function loadMeta() {
    if (!CONFIG) CONFIG = await get("/api/crazytime/config");
    if (!STRATEGIES) STRATEGIES = await get("/api/crazytime/strategies");
  }

  const SPOT_LABELS = { "1": "1", "2": "2", "5": "5", "10": "10",
    coin_flip: "Coin Flip", cash_hunt: "Cash Hunt", pachinko: "Pachinko",
    crazy_time: "Crazy Time" };

  // ================================================================ form utils
  function paramField(sch, prefix) {
    const id = `${prefix}-${sch.key}`;
    if (sch.type === "select") {
      const opts = sch.options.map((o) =>
        `<option value="${esc(o)}" ${o === sch.default ? "selected" : ""}>${esc(SPOT_LABELS[o] || o)}</option>`).join("");
      return `<label class="field"><span>${esc(sch.label)}</span>
        <select id="${id}" data-key="${esc(sch.key)}">${opts}</select></label>`;
    }
    return `<label class="field"><span>${esc(sch.label)}</span>
      <input id="${id}" data-key="${esc(sch.key)}" type="number" value="${sch.default}"
        min="${sch.min}" max="${sch.max}" step="${sch.step || 1}"></label>`;
  }

  function strategyParamsHtml(name, prefix) {
    const s = STRATEGIES.find((x) => x.name === name);
    const own = s.params.map((p) => paramField(p, prefix)).join("");
    const common = s.common_params.map((p) => paramField(p, prefix)).join("");
    return `<div class="muted mb">${esc(s.description)}</div>
      <div class="grid grid-2">${own}</div>
      <details class="mt"><summary class="muted" style="cursor:pointer">Session guards (stop loss / take profit / max bet)</summary>
      <div class="grid grid-2 mt">${common}</div></details>`;
  }

  function collectParams(container) {
    const params = {};
    container.querySelectorAll("[data-key]").forEach((el) => {
      params[el.dataset.key] = el.type === "number" || el.tagName === "INPUT"
        ? parseFloat(el.value) : el.value;
    });
    return params;
  }

  function chipRow(values, defaultVal, id, fmtFn = (v) => fmt(v, 0)) {
    return `<div class="chip-row" id="${id}">` + values.map((v) =>
      `<div class="chip ${v === defaultVal ? "on" : ""}" data-v="${v}">${fmtFn(v)}</div>`).join("") + `</div>`;
  }

  function bindChips(id, input) {
    const row = document.getElementById(id);
    row.addEventListener("click", (e) => {
      const c = e.target.closest(".chip");
      if (!c) return;
      row.querySelectorAll(".chip").forEach((x) => x.classList.remove("on"));
      c.classList.add("on");
      document.getElementById(input).value = c.dataset.v;
    });
  }

  // ================================================================= simulator
  register("ct/simulator", async (view) => {
    await loadMeta();
    const stratOpts = STRATEGIES.map((s) =>
      `<option value="${s.name}">${esc(s.label)} — ${esc(s.category)}</option>`).join("");
    view.innerHTML = `
      <div class="page-title">Crazy Time — Monte Carlo Simulator</div>
      <div class="page-sub">Every spin is independent and random. Strategies change your
        risk profile, never the house edge. Wheel calibrated to published Evolution RTPs.</div>
      <div class="grid grid-side">
        <div class="card">
          <div class="section-title">🎛️ Simulation setup</div>
          <label class="field"><span>Strategy</span>
            <select id="sim-strategy">${stratOpts}</select></label>
          <div id="sim-params"></div>
          <label class="field mt"><span>Spins per run</span>
            <input id="sim-spins" type="number" value="100000" min="100" max="10000000"></label>
          ${chipRow(CONFIG.spin_presets, 100000, "spin-chips", (v) => v >= 1e6 ? (v / 1e6) + "M" : (v / 1e3) + "k")}
          <label class="field mt"><span>Independent runs (parallel Monte Carlo paths)</span>
            <input id="sim-runs" type="number" value="20" min="1" max="2000"></label>
          <label class="field"><span>Starting bankroll</span>
            <input id="sim-bankroll" type="number" value="500" min="1"></label>
          ${chipRow(CONFIG.bankroll_presets, 500, "bank-chips")}
          <label class="field mt"><span>Bet unit</span>
            <input id="sim-unit" type="number" value="1" min="0.1" step="0.1"></label>
          <label class="field"><span>Random seed (blank = random)</span>
            <input id="sim-seed" type="number" placeholder="random"></label>
          <button class="btn mt" id="sim-run" style="width:100%">▶ Run simulation</button>
          <div class="progress-track" id="sim-progress-track" style="display:none">
            <div class="progress-bar" id="sim-progress"></div></div>
          <div class="faint" id="sim-progress-msg"></div>
        </div>
        <div id="sim-results"><div class="card empty">Configure a simulation and press Run.
          A 100k-spin × 20-run batch completes in seconds.</div></div>
      </div>`;

    const paramsBox = document.getElementById("sim-params");
    const renderParams = () =>
      paramsBox.innerHTML = strategyParamsHtml(document.getElementById("sim-strategy").value, "sp");
    renderParams();
    document.getElementById("sim-strategy").addEventListener("change", renderParams);
    bindChips("spin-chips", "sim-spins");
    bindChips("bank-chips", "sim-bankroll");

    document.getElementById("sim-run").addEventListener("click", async () => {
      const btn = document.getElementById("sim-run");
      const body = {
        strategy: document.getElementById("sim-strategy").value,
        params: collectParams(paramsBox),
        spins: parseInt(document.getElementById("sim-spins").value, 10),
        runs: parseInt(document.getElementById("sim-runs").value, 10),
        bankroll: parseFloat(document.getElementById("sim-bankroll").value),
        bet_unit: parseFloat(document.getElementById("sim-unit").value),
      };
      const seed = document.getElementById("sim-seed").value;
      if (seed) body.seed = parseInt(seed, 10);
      btn.disabled = true;
      document.getElementById("sim-progress-track").style.display = "";
      try {
        const { job_id } = await post("/api/crazytime/simulate", body);
        const result = await pollJob("/api/crazytime", job_id, (j) => {
          document.getElementById("sim-progress").style.width = (j.progress * 100) + "%";
          document.getElementById("sim-progress-msg").textContent = j.message || "";
        });
        renderResults(document.getElementById("sim-results"), result, body);
        toast("Simulation complete", "ok");
      } catch (err) {
        toast(err.message, "err", 7000);
      } finally {
        btn.disabled = false;
        document.getElementById("sim-progress-track").style.display = "none";
        document.getElementById("sim-progress-msg").textContent = "";
      }
    });
  });

  function renderResults(box, r, body) {
    const rep = r.representative_run;
    const roiCls = r.mean_roi > 0 ? "good" : "bad";
    box.innerHTML = `
      <div class="grid grid-4 mb">
        ${statCard("Mean final balance", money(r.mean_final_balance), roiCls, `median ${money(r.median_final_balance)}`)}
        ${statCard("Mean ROI", pct(r.mean_roi), roiCls, `P(profit) ${pct(r.prob_profit)}`)}
        ${statCard("Risk of ruin", pct(r.risk_of_ruin), r.risk_of_ruin > 0.3 ? "bad" : r.risk_of_ruin > 0.05 ? "warn" : "good", `${r.runs} runs`)}
        ${statCard("RTP achieved", pct(r.mean_rtp_achieved), "", "vs ~95.5% theoretical")}
      </div>
      <div class="grid grid-4 mb">
        ${statCard("Max drawdown (mean)", pct(r.mean_max_drawdown), "warn")}
        ${statCard("Volatility / spin", fmt(r.mean_volatility, 3))}
        ${statCard("Bonus frequency", pct(r.mean_bonus_frequency), "", `contribution ${pct(r.mean_bonus_contribution)}`)}
        ${statCard("Streaks (max)", `${r.longest_winning_streak}W / ${r.longest_losing_streak}L`)}
      </div>
      <div class="card mb">
        <div class="section-title">📈 Balance curves (median with 10–90% band, representative run)</div>
        <div class="chart-box tall"><canvas id="ct-balance"></canvas></div>
      </div>
      <div class="grid grid-2 mb">
        <div class="card"><div class="section-title">📉 Drawdown (representative run)</div>
          <div class="chart-box short"><canvas id="ct-dd"></canvas></div></div>
        <div class="card"><div class="section-title">📊 Final balance distribution (${r.runs} runs)</div>
          <div class="chart-box short"><canvas id="ct-final-hist"></canvas></div></div>
      </div>
      <div class="grid grid-2 mb">
        <div class="card"><div class="section-title">🎲 Per-spin net return distribution</div>
          <div class="chart-box short"><canvas id="ct-ret-hist"></canvas></div></div>
        <div class="card"><div class="section-title">Summary</div>
          <div class="table-wrap"><table><tbody>
            <tr><td>Best / worst final</td><td>${money(r.best_final_balance)} / ${money(r.worst_final_balance)}</td></tr>
            <tr><td>EV per spin</td><td class="${r.mean_ev_per_spin >= 0 ? "good" : "bad"}">${fmt(r.mean_ev_per_spin, 4)}</td></tr>
            <tr><td>Avg drawdown</td><td>${pct(r.mean_avg_drawdown)}</td></tr>
            <tr><td>Total staked (rep. run)</td><td>${money(rep.total_staked)}</td></tr>
            <tr><td>Spins played (rep. run)</td><td>${fmt(rep.spins_played, 0)} (${esc(rep.stopped_reason)})</td></tr>
            <tr><td>Bonus hits (rep. run)</td><td>${fmt(rep.bonus_hits, 0)}</td></tr>
          </tbody></table></div>
          <div class="mt">
            <button class="btn small ghost" data-exp="pdf">Export PDF</button>
            <button class="btn small ghost" data-exp="xlsx">Excel</button>
            <button class="btn small ghost" data-exp="csv">CSV</button>
            <button class="btn small ghost" data-exp="json">JSON</button>
          </div>
        </div>
      </div>`;

    box.querySelectorAll("[data-exp]").forEach((b) => b.addEventListener("click", async () => {
      try {
        const { path } = await post("/api/crazytime/export",
          { simulation_ids: [r.simulation_id], format: b.dataset.exp });
        toast(`Exported → ${path}`, "ok", 8000);
      } catch (err) { toast(err.message, "err"); }
    }));

    const bands = r.balance_bands;
    const labels = bands.median.map((_, i) => i);
    chart("ct-balance", {
      type: "line",
      data: { labels, datasets: [
        { label: "p90", data: bands.p90, borderColor: "rgba(34,211,238,.0)",
          backgroundColor: "rgba(109,93,246,.16)", fill: "+1", pointRadius: 0, borderWidth: 0 },
        { label: "p10", data: bands.p10, borderColor: "rgba(34,211,238,0)", pointRadius: 0, borderWidth: 0 },
        { label: "median", data: bands.median, borderColor: "#6d5df6", pointRadius: 0, borderWidth: 2 },
        { label: "representative run", data: rep.balance_curve.slice(0, labels.length),
          borderColor: "#22d3ee", pointRadius: 0, borderWidth: 1.2 },
      ]},
      options: baseOpts(),
    });
    chart("ct-dd", {
      type: "line",
      data: { labels: rep.drawdown_curve.map((_, i) => i), datasets: [
        { label: "drawdown", data: rep.drawdown_curve.map((v) => -v * 100),
          borderColor: "#f87171", backgroundColor: "rgba(248,113,113,.15)",
          fill: true, pointRadius: 0, borderWidth: 1.5 }]},
      options: baseOpts({ y: { ticks: { callback: (v) => v + "%" } } }),
    });
    const fh = r.final_balance_histogram;
    chart("ct-final-hist", {
      type: "bar",
      data: { labels: fh.edges.slice(0, -1).map((e, i) => `${fmt(e, 0)}`),
        datasets: [{ label: "runs", data: fh.counts, backgroundColor: "#6d5df6" }] },
      options: baseOpts(),
    });
    const rh = rep.return_histogram;
    chart("ct-ret-hist", {
      type: "bar",
      data: { labels: rh.edges.slice(0, -1).map((e) => fmt(e, 1)),
        datasets: [{ label: "spins", data: rh.counts, backgroundColor: "#22d3ee" }] },
      options: baseOpts({ y: { type: "logarithmic" } }),
    });
  }

  // ================================================================ wheel page
  register("ct/wheel", async (view) => {
    CONFIG = await get("/api/crazytime/config");
    const w = CONFIG.wheel;
    const rows = w.spots.map((s, i) => `
      <tr><td><span class="dot" style="display:inline-block;background:${PALETTE[i]}"></span>
        ${esc(SPOT_LABELS[s.key])}</td>
        <td>${s.segments}</td><td>${pct(s.probability, 2)}</td>
        <td>${s.base_pays !== null ? s.base_pays + ":1" : "bonus"}</td>
        <td>${fmt(s.mean_payout_multiple, 2)}x</td>
        <td class="${s.rtp > 0.955 ? "good" : ""}">${pct(s.rtp, 2)}</td>
        <td>${s.rtp_target ? pct(s.rtp_target, 2) : "–"}</td>
        <td>${pct(s.top_slot_target_prob, 1)}</td></tr>`).join("");
    view.innerHTML = `
      <div class="page-title">Wheel Model &amp; RTP</div>
      <div class="page-sub">54 segments, Top Slot and all four bonus games — statistically
        calibrated to Evolution's published return-to-player figures. Everything below is
        computed from <code>wheel_config.json</code>.</div>
      <div class="grid grid-2">
        <div class="card">
          <div class="section-title">🎡 Segment distribution</div>
          <div class="chart-box"><canvas id="wheel-pie"></canvas></div>
        </div>
        <div class="card">
          <div class="section-title">💰 RTP by bet spot (simulated vs published)</div>
          <div class="chart-box"><canvas id="rtp-bars"></canvas></div>
        </div>
      </div>
      <div class="card mt">
        <div class="section-title">📋 Full paytable &amp; probabilities</div>
        <div class="table-wrap"><table>
          <thead><tr><th>Bet spot</th><th>Segments</th><th>P(hit)</th><th>Base pays</th>
            <th>Mean payout (incl. Top Slot &amp; bonuses)</th><th>RTP</th><th>Published RTP</th>
            <th>Top Slot target%</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <div class="faint mt">Top Slot multiplier table: ${w.top_slot.multipliers.map((m, i) =>
          `${m}x (${pct(w.top_slot.probabilities[i], 1)})`).join(" · ")} — mean ${fmt(w.top_slot.mean_multiplier, 2)}x</div>
      </div>
      <div class="card mt">
        <div class="section-title">🎁 Bonus game inspector — run a live bonus round</div>
        <div class="chip-row">
          <div class="chip" data-bonus="coin_flip">Coin Flip</div>
          <div class="chip" data-bonus="cash_hunt">Cash Hunt</div>
          <div class="chip" data-bonus="pachinko">Pachinko</div>
          <div class="chip" data-bonus="crazy_time">Crazy Time</div>
        </div>
        <div id="bonus-out" class="mt muted">Pick a bonus to simulate one explicit round
          (Cash Hunt shows the full 108-symbol board; Pachinko shows the real peg walk).</div>
      </div>`;

    chart("wheel-pie", {
      type: "doughnut",
      data: { labels: w.spots.map((s) => SPOT_LABELS[s.key]),
        datasets: [{ data: w.spots.map((s) => s.segments), backgroundColor: PALETTE,
                     borderColor: "rgba(0,0,0,.3)", borderWidth: 2 }] },
      options: baseOpts({ noScales: true }),
    });
    chart("rtp-bars", {
      type: "bar",
      data: { labels: w.spots.map((s) => SPOT_LABELS[s.key]),
        datasets: [
          { label: "model RTP", data: w.spots.map((s) => s.rtp * 100), backgroundColor: "#6d5df6" },
          { label: "published RTP", data: w.spots.map((s) => (s.rtp_target || 0) * 100), backgroundColor: "#22d3ee" },
        ]},
      options: baseOpts({ y: { min: 90, max: 100, ticks: { callback: (v) => v + "%" } } }),
    });

    view.querySelectorAll("[data-bonus]").forEach((c) => c.addEventListener("click", async () => {
      const g = c.dataset.bonus;
      const out = document.getElementById("bonus-out");
      out.innerHTML = `<span class="spinner"></span>`;
      const r = await get(`/api/crazytime/bonus-demo/${g}`);
      if (g === "cash_hunt") {
        const cells = r.board.map((v, i) =>
          `<span style="display:inline-block;width:52px;padding:3px 0;margin:2px;text-align:center;
            border-radius:6px;font-size:11px;background:${i === r.pick_index ? "rgba(52,211,153,.35)" : "var(--glass-strong)"}">${v}x</span>`).join("");
        out.innerHTML = `<div class="mb">Board of 108 symbols — your random pick is highlighted:
          <b class="good">won ${r.won}x</b></div><div>${cells}</div>`;
      } else if (g === "pachinko") {
        const drops = r.drops.map((d, i) => {
          const wall = d.wall.map((v, j) =>
            `<span style="display:inline-block;width:52px;padding:3px 0;margin:2px;text-align:center;border-radius:6px;font-size:11px;
              background:${j === d.path[d.path.length - 1] ? "rgba(52,211,153,.35)" : v === "DOUBLE" ? "rgba(251,191,36,.3)" : "var(--glass-strong)"}">${v === "DOUBLE" ? "2×ALL" : v + "x"}</span>`).join("");
          return `<div class="mb"><div class="faint">drop ${i + 1} — puck path: ${d.path.join(" → ")}</div>${wall}</div>`;
        }).join("");
        out.innerHTML = `${drops}<div><b class="good">won ${r.won}x</b></div>`;
      } else {
        out.innerHTML = `<div>Bonus resolved: <b class="good">won ${r.won}x</b> (multiplier per unit staked)</div>`;
      }
    }));
  });

  // ================================================================== compare
  register("ct/compare", async (view) => {
    await loadMeta();
    const entries = [];
    view.innerHTML = `
      <div class="page-title">Strategy Comparison</div>
      <div class="page-sub">Every strategy sees the <b>same random outcomes</b> (paired
        Monte Carlo), so differences are pure strategy effects — then ranked by a composite
        of ROI, P(profit), ruin risk and drawdown.</div>
      <div class="grid grid-side">
        <div class="card">
          <div class="section-title">➕ Add strategies</div>
          <label class="field"><span>Strategy</span>
            <select id="cmp-strategy">${STRATEGIES.map((s) =>
              `<option value="${s.name}">${esc(s.label)}</option>`).join("")}</select></label>
          <div id="cmp-params"></div>
          <button class="btn ghost mt" id="cmp-add" style="width:100%">Add to comparison</button>
          <hr style="border-color:var(--border);margin:16px 0">
          <label class="field"><span>Spins per run</span>
            <input id="cmp-spins" type="number" value="50000" min="100" max="10000000"></label>
          <label class="field"><span>Runs per strategy</span>
            <input id="cmp-runs" type="number" value="15" min="1" max="500"></label>
          <label class="field"><span>Bankroll</span>
            <input id="cmp-bankroll" type="number" value="500"></label>
          <label class="field"><span>Bet unit</span>
            <input id="cmp-unit" type="number" value="1" step="0.1"></label>
          <button class="btn mt" id="cmp-run" style="width:100%">▶ Run comparison</button>
          <div class="progress-track" id="cmp-track" style="display:none">
            <div class="progress-bar" id="cmp-progress"></div></div>
        </div>
        <div>
          <div class="card mb"><div class="section-title">🧺 Entries</div>
            <div id="cmp-list" class="muted">No strategies added yet — add at least two.</div></div>
          <div id="cmp-results"></div>
        </div>
      </div>`;

    const paramsBox = document.getElementById("cmp-params");
    const renderP = () => paramsBox.innerHTML =
      strategyParamsHtml(document.getElementById("cmp-strategy").value, "cp");
    renderP();
    document.getElementById("cmp-strategy").addEventListener("change", renderP);

    const renderList = () => {
      const box = document.getElementById("cmp-list");
      if (!entries.length) { box.innerHTML = "No strategies added yet — add at least two."; return; }
      box.innerHTML = entries.map((e, i) => `<span class="pill pill-accent" style="margin:3px">
        ${esc(e.name)} <a href="#" data-del="${i}" style="color:#f87171;text-decoration:none">✕</a></span>`).join("");
      box.querySelectorAll("[data-del]").forEach((a) => a.addEventListener("click", (ev) => {
        ev.preventDefault();
        entries.splice(parseInt(a.dataset.del, 10), 1);
        renderList();
      }));
    };

    document.getElementById("cmp-add").addEventListener("click", () => {
      const name = document.getElementById("cmp-strategy").value;
      const s = STRATEGIES.find((x) => x.name === name);
      entries.push({ strategy: name, params: collectParams(paramsBox),
                     name: `${s.label} #${entries.filter((e) => e.strategy === name).length + 1}` });
      renderList();
    });

    document.getElementById("cmp-run").addEventListener("click", async () => {
      if (entries.length < 2) { toast("Add at least two strategies", "err"); return; }
      const btn = document.getElementById("cmp-run");
      btn.disabled = true;
      document.getElementById("cmp-track").style.display = "";
      try {
        const { job_id } = await post("/api/crazytime/compare", {
          entries,
          spins: parseInt(document.getElementById("cmp-spins").value, 10),
          runs: parseInt(document.getElementById("cmp-runs").value, 10),
          bankroll: parseFloat(document.getElementById("cmp-bankroll").value),
          bet_unit: parseFloat(document.getElementById("cmp-unit").value),
        });
        const result = await pollJob("/api/crazytime", job_id, (j) => {
          document.getElementById("cmp-progress").style.width = (j.progress * 100) + "%";
        });
        renderComparison(document.getElementById("cmp-results"), result.comparison);
      } catch (err) { toast(err.message, "err", 7000); }
      finally {
        btn.disabled = false;
        document.getElementById("cmp-track").style.display = "none";
      }
    });
  });

  function renderComparison(box, comp) {
    const rows = comp.map((c) => {
      const r = c.results;
      return `<tr><td>#${c.rank}</td><td>${esc(c.name)}</td>
        <td class="${r.mean_roi > 0 ? "good" : "bad"}">${pct(r.mean_roi)}</td>
        <td>${pct(r.prob_profit)}</td><td>${pct(r.risk_of_ruin)}</td>
        <td>${fmt(r.mean_spins_survived, 0)}</td>
        <td>${pct(r.mean_max_drawdown)}</td><td>${pct(r.mean_rtp_achieved)}</td>
        <td>${money(r.mean_final_balance)}</td><td>${fmt(c.score, 3)}</td></tr>`;
    }).join("");
    box.innerHTML = `
      <div class="card mb"><div class="section-title">🏆 Ranking</div>
        <div class="table-wrap"><table>
          <thead><tr><th>Rank</th><th>Strategy</th><th>Mean ROI</th><th>P(profit)</th>
            <th>Ruin</th><th>Spins survived</th><th>Max DD</th><th>RTP</th><th>Mean final</th><th>Score</th></tr></thead>
          <tbody>${rows}</tbody></table></div>
        <div class="mt"><button class="btn small ghost" id="cmp-exp-pdf">Export PDF</button>
          <button class="btn small ghost" id="cmp-exp-xlsx">Excel</button></div>
      </div>
      <div class="card mb"><div class="section-title">📈 Median balance curves</div>
        <div class="chart-box tall"><canvas id="cmp-curves"></canvas></div></div>
      <div class="grid grid-2">
        <div class="card"><div class="section-title">ROI vs risk of ruin</div>
          <div class="chart-box short"><canvas id="cmp-scatter"></canvas></div></div>
        <div class="card"><div class="section-title">Max drawdown</div>
          <div class="chart-box short"><canvas id="cmp-dd"></canvas></div></div>
      </div>`;

    const ids = comp.map((c) => c.simulation_id);
    document.getElementById("cmp-exp-pdf").addEventListener("click", async () => {
      const { path } = await post("/api/crazytime/export", { simulation_ids: ids, format: "pdf" });
      toast(`Exported → ${path}`, "ok", 8000);
    });
    document.getElementById("cmp-exp-xlsx").addEventListener("click", async () => {
      const { path } = await post("/api/crazytime/export", { simulation_ids: ids, format: "xlsx" });
      toast(`Exported → ${path}`, "ok", 8000);
    });

    const maxLen = Math.max(...comp.map((c) => c.results.balance_bands.median.length));
    chart("cmp-curves", {
      type: "line",
      data: { labels: Array.from({ length: maxLen }, (_, i) => i),
        datasets: comp.map((c, i) => ({ label: c.name, data: c.results.balance_bands.median,
          borderColor: PALETTE[i % PALETTE.length], pointRadius: 0, borderWidth: 1.8 })) },
      options: baseOpts(),
    });
    chart("cmp-scatter", {
      type: "scatter",
      data: { datasets: comp.map((c, i) => ({ label: c.name,
        data: [{ x: c.results.risk_of_ruin * 100, y: c.results.mean_roi * 100 }],
        backgroundColor: PALETTE[i % PALETTE.length], pointRadius: 7 })) },
      options: baseOpts({ x: { title: { display: true, text: "risk of ruin %", color: "#6b7194" } },
                          y: { title: { display: true, text: "mean ROI %", color: "#6b7194" } } }),
    });
    chart("cmp-dd", {
      type: "bar",
      data: { labels: comp.map((c) => c.name),
        datasets: [{ label: "mean max drawdown %", data: comp.map((c) => c.results.mean_max_drawdown * 100),
                     backgroundColor: comp.map((_, i) => PALETTE[i % PALETTE.length]) }] },
      options: baseOpts({ y: { ticks: { callback: (v) => v + "%" } } }),
    });
  }

  // ================================================================== history
  register("ct/history", async (view) => {
    const sims = await get("/api/crazytime/simulations");
    if (!sims.length) {
      view.innerHTML = `<div class="page-title">Results History</div>
        <div class="card empty">No stored simulations yet.</div>`;
      return;
    }
    view.innerHTML = `
      <div class="page-title">Results History</div>
      <div class="page-sub">Every simulation is persisted and exportable. Select rows to
        build a combined report.</div>
      <div class="card"><div class="table-wrap"><table>
        <thead><tr><th></th><th>ID</th><th>When</th><th>Name</th><th>Strategy</th>
          <th>Spins</th><th>Runs</th><th>Bankroll</th><th></th></tr></thead>
        <tbody>${sims.map((s) => `<tr>
          <td><input type="checkbox" data-id="${s.id}" style="width:auto"></td>
          <td>${s.id}</td><td>${esc(s.created_at.slice(0, 16).replace("T", " "))}</td>
          <td>${esc(s.name)}</td><td>${esc(s.strategy)}</td>
          <td>${fmt(s.spins, 0)}</td><td>${s.runs}</td><td>${money(s.bankroll)}</td>
          <td><button class="btn small danger" data-del="${s.id}">delete</button></td></tr>`).join("")}
        </tbody></table></div>
        <div class="mt">
          <button class="btn small" data-fmt="pdf">Export selected — PDF</button>
          <button class="btn small ghost" data-fmt="xlsx">Excel</button>
          <button class="btn small ghost" data-fmt="csv">CSV</button>
          <button class="btn small ghost" data-fmt="json">JSON</button>
        </div></div>`;

    view.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      await del(`/api/crazytime/simulations/${b.dataset.del}`);
      toast("Deleted", "ok");
      location.reload();
    }));
    view.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", async () => {
      const ids = [...view.querySelectorAll("input[data-id]:checked")].map((c) => parseInt(c.dataset.id, 10));
      if (!ids.length) { toast("Select at least one simulation", "err"); return; }
      try {
        const { path } = await post("/api/crazytime/export", { simulation_ids: ids, format: b.dataset.fmt });
        toast(`Exported → ${path}`, "ok", 8000);
      } catch (err) { toast(err.message, "err"); }
    }));
  });
})();
