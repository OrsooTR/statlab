/* Football Live Center: real-time matches, stats, lineups, events, in-play predictions. */
"use strict";

(() => {
  const { register, get, post, toast, chart, baseOpts, esc, fmt, pct, probBar } = App;

  let refreshTimer = null;
  let detailTimer = null;
  let currentMatch = null;

  function clearTimers() {
    if (refreshTimer) clearInterval(refreshTimer);
    if (detailTimer) clearInterval(detailTimer);
    refreshTimer = detailTimer = null;
  }

  register("fb/live", async (view) => {
    clearTimers();
    currentMatch = null;
    const settings = await get("/api/live/settings");
    view.innerHTML = `
      <div class="page-title">🔴 Live Center</div>
      <div class="page-sub">Every match of the day, automatically — live scores, statistics,
        lineups, substitutions, event timeline and in-play probability updates.</div>
      <div class="card mb">
        <div style="display:flex;gap:14px;flex-wrap:wrap;align-items:end">
          <label class="field" style="margin:0;min-width:210px"><span>Data provider</span>
            <select id="lv-provider">
              <option value="demo" ${settings.provider === "demo" ? "selected" : ""}>Demo simulation (built-in)</option>
              <option value="api_football" ${settings.provider === "api_football" ? "selected" : ""}>API-Football (real live data)</option>
            </select></label>
          <label class="field" style="margin:0;flex:1;min-width:220px"><span>API-Football key
            (free at api-football.com${settings.has_key ? " — key saved ✓" : ""})</span>
            <input id="lv-key" type="password" placeholder="${settings.has_key ? "•••••••• (saved)" : "paste your API key"}"></label>
          <button class="btn small ghost" id="lv-save">Save</button>
          <label class="field" style="margin:0"><span>Auto-refresh</span>
            <select id="lv-auto">
              <option value="0">off (manual)</option>
              <option value="60" ${settings.provider === "demo" ? "selected" : ""}>every 60s</option>
              <option value="120">every 2 min</option>
            </select></label>
          <button class="btn small" id="lv-refresh">⟳ Refresh now</button>
        </div>
        <div class="faint mt" id="lv-status">${providerLabel(settings.status)}</div>
      </div>
      <div id="lv-lists"></div>
      <div id="lv-detail"></div>`;

    document.getElementById("lv-save").addEventListener("click", async () => {
      try {
        const s = await post("/api/live/settings", {
          provider: document.getElementById("lv-provider").value,
          api_key: document.getElementById("lv-key").value.trim(),
        });
        document.getElementById("lv-status").textContent = providerLabel(s.status);
        toast("Live settings saved", "ok");
        await loadLists();
      } catch (err) { toast(err.message, "err", 7000); }
    });
    document.getElementById("lv-refresh").addEventListener("click", loadLists);
    document.getElementById("lv-auto").addEventListener("change", setupAuto);

    await loadLists();
    setupAuto();

    function setupAuto() {
      if (refreshTimer) clearInterval(refreshTimer);
      const s = parseInt(document.getElementById("lv-auto").value, 10);
      if (s > 0) refreshTimer = setInterval(() => {
        loadLists();
        if (currentMatch) loadDetail(currentMatch, true);
      }, s * 1000);
    }
  });

  function providerLabel(st) {
    if (!st) return "";
    if (st.demo) return "DEMO mode — simulated matches so every feature is testable. " + (st.note || "");
    return `API-Football connected — ${st.requests_today}/${st.daily_budget} requests used today (free-tier quota is protected).`;
  }

  async function loadLists() {
    const box = document.getElementById("lv-lists");
    if (!box) return;
    try {
      const d = await get("/api/live/matches");
      const card = (m, live) => `
        <div class="card" data-match="${esc(m.id)}" style="cursor:pointer;padding:14px">
          <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
            <div>
              <b>${esc(m.home)}</b> <span class="value" style="font-size:18px">
                ${m.score_home !== null ? m.score_home + " – " + m.score_away : "vs"}</span> <b>${esc(m.away)}</b>
              <div class="faint">${esc(m.league)}</div>
            </div>
            <div style="text-align:right">
              ${live ? `<span class="pill pill-bad" style="animation:pulse 1.5s infinite">● ${m.status === "HT" ? "HT" : (m.minute ?? "") + "′"}</span>`
                     : m.finished ? `<span class="pill pill-muted">FT</span>`
                     : `<span class="pill pill-accent">${esc((m.kickoff || "").slice(11, 16) || "today")}</span>`}
            </div>
          </div>
        </div>`;
      box.innerHTML = `
        <style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}</style>
        ${d.live.length ? `<div class="section-title">🔴 Live now (${d.live.length})</div>
          <div class="grid grid-2 mb">${d.live.map((m) => card(m, true)).join("")}</div>` : ""}
        ${d.upcoming.length ? `<div class="section-title">🕒 Upcoming today (${d.upcoming.length})</div>
          <div class="grid grid-2 mb">${d.upcoming.map((m) => card(m, false)).join("")}</div>` : ""}
        ${d.finished.length ? `<div class="section-title">✅ Finished today (${d.finished.length})</div>
          <div class="grid grid-2 mb">${d.finished.map((m) => card(m, false)).join("")}</div>` : ""}
        ${!d.live.length && !d.upcoming.length && !d.finished.length
          ? `<div class="card empty">No matches found today from the provider.</div>` : ""}`;
      box.querySelectorAll("[data-match]").forEach((c) =>
        c.addEventListener("click", () => loadDetail(c.dataset.match, false)));
    } catch (err) {
      box.innerHTML = `<div class="card"><div class="section-title">Provider error</div>
        <div class="muted">${esc(err.message)}</div></div>`;
    }
  }

  async function loadDetail(id, silent) {
    currentMatch = id;
    const box = document.getElementById("lv-detail");
    if (!box) return;
    if (!silent) box.innerHTML = `<div class="card empty"><span class="spinner"></span> loading match…</div>`;
    let d;
    try {
      d = await get(`/api/live/match/${encodeURIComponent(id)}`);
    } catch (err) {
      if (!silent) box.innerHTML = `<div class="card"><div class="muted">${esc(err.message)}</div></div>`;
      return;
    }
    renderDetail(box, d);
    box.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function renderDetail(box, d) {
    const i = d.info;
    const p = d.prediction;
    const ev = d.events || [];
    const icon = { goal: "⚽", card: "🟨", subst: "🔁", shot: "🎯", var: "📺" };
    const evRow = (e) => `
      <div style="display:flex;gap:10px;align-items:baseline;padding:5px 0;
        border-bottom:1px solid rgba(255,255,255,.04);
        ${e.side === "home" ? "" : "flex-direction:row-reverse;text-align:right"}">
        <span class="pill pill-muted" style="min-width:44px;text-align:center">${e.minute}′</span>
        <span>${e.detail && e.detail.includes("Red") ? "🟥" : icon[e.type] || "•"}</span>
        <span class="muted">${esc(e.player || e.detail)}${e.type === "goal" && e.assist
          ? ` <span class="faint">(assist ${esc(e.assist)})</span>` : ""}
          ${e.type === "subst" ? ` <span class="faint">${esc(e.detail)}</span>` : ""}</span>
      </div>`;

    const statRow = (label, keyList) => {
      const gv = (side) => {
        for (const k of keyList) {
          const v = d.stats?.[side]?.[k];
          if (v !== undefined && v !== null) return v;
        }
        return "–";
      };
      return `<tr><td style="text-align:right">${gv("home")}</td>
        <th style="text-align:center">${label}</th><td>${gv("away")}</td></tr>`;
    };

    box.innerHTML = `
      <div class="card mb mt">
        <div style="text-align:center">
          <div class="faint">${esc(i.league)} ${i.live ? `· <span class="pill pill-bad">● LIVE ${i.status === "HT" ? "HT" : (i.minute ?? "") + "′"}</span>` : i.finished ? "· FT" : ""}</div>
          <div class="page-title" style="margin:8px 0">
            ${esc(i.home)} <span style="color:var(--accent2)">${i.score_home ?? ""} – ${i.score_away ?? ""}</span> ${esc(i.away)}
          </div>
        </div>
        ${p ? `
        <div class="mt">${probBar(p.probabilities.home, p.probabilities.draw, p.probabilities.away)}</div>
        <div class="faint" style="display:flex;justify-content:space-between;margin-top:4px">
          <span>${esc(i.home)} ${pct(p.probabilities.home)}</span>
          <span>draw ${pct(p.probabilities.draw)}</span>
          <span>${esc(i.away)} ${pct(p.probabilities.away)}</span></div>
        <div class="mt" style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center">
          <span class="pill pill-accent">expected final ${p.expected_final.home} – ${p.expected_final.away}</span>
          <span class="pill pill-muted">next goal: ${esc(i.home)} ${pct(p.next_goal.home)} · none ${pct(p.next_goal.none)} · ${esc(i.away)} ${pct(p.next_goal.away)}</span>
          <span class="pill pill-muted">over 2.5: ${pct(p.over_probabilities["2.5"])}</span>
          ${p.model_inputs.red_cards[0] + p.model_inputs.red_cards[1] > 0
            ? `<span class="pill pill-bad">🟥 ${p.model_inputs.red_cards[0]} – ${p.model_inputs.red_cards[1]}</span>` : ""}
        </div>
        <div class="mt" style="text-align:center">
          ${p.top_scorelines.map((s) => `<span class="pill pill-muted" style="margin:2px">${s.score} ${pct(s.probability)}</span>`).join("")}
        </div>
        <div class="faint mt" style="text-align:center">${esc(p.disclaimer)}
          — live μ ${p.model_inputs.live_adjusted_mu[0]} / ${p.model_inputs.live_adjusted_mu[1]}
          (pre-match ${p.model_inputs.prematch_mu[0]} / ${p.model_inputs.prematch_mu[1]})</div>` : ""}
      </div>
      ${d.momentum ? `
      <div class="card mb"><div class="section-title">📈 Momentum (attacking pressure)</div>
        <div class="chart-box short"><canvas id="lv-momentum"></canvas></div></div>` : ""}
      <div class="grid grid-2 mb">
        <div class="card"><div class="section-title">📊 Match statistics</div>
          <div class="table-wrap"><table><tbody>
            ${statRow("Possession", ["ball_possession"])}
            ${statRow("Shots", ["total_shots"])}
            ${statRow("On target", ["shots_on_goal"])}
            ${statRow("Corners", ["corner_kicks"])}
            ${statRow("Yellow cards", ["yellow_cards"])}
            ${statRow("Red cards", ["red_cards"])}
            ${statRow("Fouls", ["fouls"])}
          </tbody></table></div></div>
        <div class="card"><div class="section-title">⏱️ Events timeline</div>
          <div style="max-height:330px;overflow-y:auto">
            ${ev.length ? ev.slice().reverse().map(evRow).join("") : `<div class="muted">No events yet.</div>`}
          </div></div>
      </div>
      ${d.lineups ? `
      <div class="grid grid-2 mb">
        ${["home", "away"].map((side) => {
          const l = d.lineups[side];
          return `<div class="card"><div class="section-title">
              ${esc(l.team)} <span class="pill pill-accent">${esc(l.formation)}</span>
              <span class="faint">coach ${esc(l.coach)}</span></div>
            <div class="table-wrap"><table><tbody>
              ${l.starters.map((pl) => `<tr><td style="width:36px">${pl.number ?? ""}</td>
                <td>${esc(pl.name)}</td><td class="faint">${esc(pl.pos || "")}</td></tr>`).join("")}
            </tbody></table></div>
            <div class="faint mt">Bench: ${l.substitutes.map((pl) => esc(pl.name)).join(", ")}</div>
          </div>`;
        }).join("")}
      </div>` : ""}`;

    if (d.momentum) {
      chart("lv-momentum", {
        type: "line",
        data: { labels: d.momentum.minutes.map((m) => m + "′"),
          datasets: [
            { label: i.home, data: d.momentum.home, borderColor: "#6d5df6",
              backgroundColor: "rgba(109,93,246,.18)", fill: true, pointRadius: 0, borderWidth: 1.6, tension: .35 },
            { label: i.away, data: d.momentum.away.map((v) => -v), borderColor: "#22d3ee",
              backgroundColor: "rgba(34,211,238,.15)", fill: true, pointRadius: 0, borderWidth: 1.6, tension: .35 },
          ]},
        options: baseOpts({ y: { ticks: { callback: (v) => Math.abs(v) } } }),
      });
    }
  }
})();
