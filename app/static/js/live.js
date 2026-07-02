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
          <label class="field" style="margin:0;min-width:250px"><span>Data provider</span>
            <select id="lv-provider">
              <option value="auto" ${settings.provider === "auto" ? "selected" : ""}>Multi-source auto (no key: ESPN + OpenLigaDB)</option>
              <option value="demo" ${settings.provider === "demo" ? "selected" : ""}>Demo simulation (built-in)</option>
              <option value="api_football" ${settings.provider === "api_football" ? "selected" : ""}>API-Football only (key required)</option>
            </select></label>
          <label class="field" style="margin:0;flex:1;min-width:220px"><span>API-Football key — optional, enriches auto mode
            ${settings.has_key ? "(key saved ✓)" : ""}</span>
            <input id="lv-key" type="password" placeholder="${settings.has_key ? "•••••••• (saved)" : "optional API key"}"></label>
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
      <div id="lv-detail"></div>
      <div id="lv-predict-overlay" style="display:none;position:fixed;inset:0;z-index:60;
        background:rgba(5,6,15,.86);backdrop-filter:blur(8px);overflow-y:auto">
        <div style="max-width:1000px;margin:30px auto;padding:0 20px" id="lv-predict-content"></div>
      </div>`;

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
    if (st.provider === "auto") {
      const names = (st.sources || []).map((s) =>
        s.provider === "espn" ? `ESPN (${s.leagues} competitions)` :
        s.provider === "openligadb" ? `OpenLigaDB (${s.leagues})` :
        s.provider === "api_football" ? `API-Football (${s.requests_today}/${s.daily_budget} today)` : s.provider);
      return `Cross-referenced sources: ${names.join(" + ")}. Duplicate fixtures are merged by team-name matching; each match shows its sources. Full registry in app/football/live/sources.json.`;
    }
    return `API-Football connected — ${st.requests_today}/${st.daily_budget} requests used today (free-tier quota is protected).`;
  }

  const CATEGORY_META = {
    international: { label: "International — national teams", icon: "🌍", order: 0 },
    continental:  { label: "Continental club cups",         icon: "🏆", order: 1 },
    domestic:     { label: "Domestic leagues",              icon: "🏟️", order: 2 },
  };
  let lastData = null;
  let catFilter = "all";

  function card(m) {
    const live = m.live, fin = m.finished;
    const isIntl = m.category === "international";
    const predictable = !!m.fd_code || isIntl;
    const predictLabel = isIntl ? "🎯 Predict + scorers" : "🎯 Predict";
    return `
      <div class="card lv-card" data-match="${esc(m.id)}" style="cursor:pointer;padding:13px">
        <div style="display:flex;justify-content:space-between;align-items:center;gap:8px;flex-wrap:wrap">
          <div style="min-width:0">
            <b>${esc(m.home)}</b> <span class="value" style="font-size:17px">
              ${m.score_home !== null && m.score_home !== undefined ? m.score_home + " – " + m.score_away : "vs"}</span> <b>${esc(m.away)}</b>
            <div class="faint">${(m.sources || []).map(esc).join(" ✚ ")}</div>
          </div>
          <div style="text-align:right;display:flex;flex-direction:column;gap:5px;align-items:flex-end">
            ${live ? `<span class="pill pill-bad" style="animation:pulse 1.5s infinite">● ${m.status === "HT" ? "HT" : (m.minute ?? "") + "′"}</span>`
                   : fin ? `<span class="pill pill-muted">FT</span>`
                   : `<span class="pill pill-accent">${esc((m.kickoff || "").slice(11, 16) || "today")}</span>`}
            ${predictable && (!fin || isIntl)
              ? `<button class="btn small" data-predict="${esc(m.id)}" style="padding:4px 10px;font-size:11px">${predictLabel}</button>`
              : ""}
          </div>
        </div>
      </div>`;
  }

  function groupByCompetition(matches) {
    // returns [{category, country, flag, league, matches:[]}] ordered sensibly
    const groups = {};
    for (const m of matches) {
      const key = `${m.category}|${m.country}|${m.league}`;
      (groups[key] = groups[key] || { category: m.category || "domestic",
        country: m.country || "", flag: m.flag || "", league: m.league, matches: [] }).matches.push(m);
    }
    return Object.values(groups).sort((a, b) => {
      const ca = (CATEGORY_META[a.category] || { order: 9 }).order;
      const cb = (CATEGORY_META[b.category] || { order: 9 }).order;
      if (ca !== cb) return ca - cb;
      if (a.country !== b.country) return a.country.localeCompare(b.country);
      return a.league.localeCompare(b.league);
    });
  }

  function renderStatusSection(title, icon, matches) {
    if (!matches.length) return "";
    const filtered = catFilter === "all" ? matches : matches.filter((m) => m.category === catFilter);
    if (!filtered.length) return "";
    const groups = groupByCompetition(filtered);
    let html = `<div class="section-title" style="margin-top:18px">${icon} ${title} (${filtered.length})</div>`;
    let lastCat = null;
    for (const g of groups) {
      if (g.category !== lastCat) {
        const cm = CATEGORY_META[g.category] || { label: g.category, icon: "•" };
        html += `<div class="faint" style="margin:14px 0 6px;text-transform:uppercase;letter-spacing:1.5px;font-size:10.5px">${cm.icon} ${esc(cm.label)}</div>`;
        lastCat = g.category;
      }
      html += `<div style="display:flex;align-items:center;gap:8px;margin:10px 0 6px">
          <span style="font-size:15px">${g.flag}</span>
          <b style="font-size:13px">${esc(g.country)}${g.country ? " · " : ""}${esc(g.league)}</b>
          <span class="faint">${g.matches.length}</span></div>
        <div class="grid grid-2">${g.matches.map(card).join("")}</div>`;
    }
    return html;
  }

  function renderLists() {
    const box = document.getElementById("lv-lists");
    if (!box || !lastData) return;
    const d = lastData;
    const cats = [...new Set([...d.live, ...d.upcoming, ...d.finished].map((m) => m.category))]
      .filter(Boolean);
    const filterBar = `
      <div class="chip-row mb" style="margin-top:6px">
        <div class="chip ${catFilter === "all" ? "on" : ""}" data-cat="all">All</div>
        ${["international", "continental", "domestic"].filter((c) => cats.includes(c)).map((c) =>
          `<div class="chip ${catFilter === c ? "on" : ""}" data-cat="${c}">${CATEGORY_META[c].icon} ${CATEGORY_META[c].label}</div>`).join("")}
      </div>`;
    const body =
      renderStatusSection("Live now", "🔴", d.live) +
      renderStatusSection("Upcoming", "🕒", d.upcoming) +
      renderStatusSection("Finished today", "✅", d.finished);
    box.innerHTML = `<style>@keyframes pulse{0%,100%{opacity:1}50%{opacity:.45}}</style>` + filterBar +
      (body || `<div class="card empty">No matches in this category right now.</div>`);
    box.querySelectorAll("[data-cat]").forEach((c) => c.addEventListener("click", () => {
      catFilter = c.dataset.cat; renderLists();
    }));
    box.querySelectorAll("[data-predict]").forEach((b) => b.addEventListener("click", (e) => {
      e.stopPropagation();
      predictFromLive(b.dataset.predict);
    }));
    box.querySelectorAll("[data-match]").forEach((c) =>
      c.addEventListener("click", () => loadDetail(c.dataset.match, false)));
  }

  async function loadLists() {
    const box = document.getElementById("lv-lists");
    if (!box) return;
    try {
      lastData = await get("/api/live/matches");
      renderLists();
    } catch (err) {
      box.innerHTML = `<div class="card"><div class="section-title">Provider error</div>
        <div class="muted">${esc(err.message)}</div></div>`;
    }
  }

  function findMatch(id) {
    return [...(lastData.live || []), ...(lastData.upcoming || []), ...(lastData.finished || [])]
      .find((x) => x.id === id);
  }

  function openPredictOverlay(titleHtml) {
    const ov = document.getElementById("lv-predict-overlay");
    const content = document.getElementById("lv-predict-content");
    ov.style.display = "";
    content.innerHTML = `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div class="section-title" style="margin:0">${titleHtml}</div>
        <button class="btn small ghost" id="lv-predict-close">✕ Close</button></div>
      <div class="muted mt"><span class="spinner"></span> working…</div>
      <div class="progress-track mt"><div class="progress-bar" id="lv-predict-progress"></div></div></div>`;
    document.getElementById("lv-predict-close").addEventListener("click", () => { ov.style.display = "none"; });
    return { ov, content };
  }

  async function predictFromLive(id) {
    const m = findMatch(id);
    if (!m) return;
    if (m.category === "international") return predictInternational(m);
    if (!m.fd_code) {
      toast("No historical model for this competition.", "err", 6000);
      return;
    }
    const ov = document.getElementById("lv-predict-overlay");
    const content = document.getElementById("lv-predict-content");
    ov.style.display = "";
    content.innerHTML = `<div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center">
        <div class="section-title" style="margin:0">🎯 Predicting ${esc(m.home)} vs ${esc(m.away)}</div>
        <button class="btn small ghost" id="lv-predict-close">✕ Close</button></div>
      <div class="muted mt"><span class="spinner"></span> fitting models on ${esc(m.country)} ${esc(m.league)} history…
        <span class="faint">(first time downloads the league; ~10-30s)</span></div>
      <div class="progress-track mt"><div class="progress-bar" id="lv-predict-progress"></div></div></div>`;
    document.getElementById("lv-predict-close").addEventListener("click", () => { ov.style.display = "none"; });
    try {
      const { job_id } = await post("/api/football/predict-live",
        { fd_code: m.fd_code, home: m.home, away: m.away, date: (m.kickoff || "").slice(0, 10) || null });
      const p = await App.pollJob("/api/football", job_id, (j) => {
        const bar = document.getElementById("lv-predict-progress");
        if (bar) bar.style.width = (j.progress * 100) + "%";
      });
      content.innerHTML = `<div style="display:flex;justify-content:flex-end;margin-bottom:8px">
          <button class="btn small ghost" id="lv-predict-close2">✕ Close</button></div>
        ${p.resolved && (p.resolved.home !== p.resolved.feed_home || p.resolved.away !== p.resolved.feed_away)
          ? `<div class="faint mb">Matched to historical teams: ${esc(p.resolved.feed_home)} → <b>${esc(p.resolved.home)}</b>,
             ${esc(p.resolved.feed_away)} → <b>${esc(p.resolved.away)}</b></div>` : ""}
        <div id="lv-predict-box"></div>`;
      document.getElementById("lv-predict-close2").addEventListener("click", () => { ov.style.display = "none"; });
      App.renderPrediction(document.getElementById("lv-predict-box"), p);
    } catch (err) {
      content.innerHTML = `<div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div class="section-title" style="margin:0">Prediction unavailable</div>
          <button class="btn small ghost" id="lv-predict-close3">✕ Close</button></div>
        <div class="muted mt">${esc(err.message)}</div></div>`;
      document.getElementById("lv-predict-close3").addEventListener("click", () => { ov.style.display = "none"; });
    }
  }

  async function predictInternational(m) {
    const { ov, content } = openPredictOverlay(`🌍 ${esc(m.home)} vs ${esc(m.away)} — full analysis`);
    try {
      const { job_id } = await post("/api/live/international-prediction",
        { fixture_id: m.id, neutral: true });
      const r = await App.pollJob("/api/live", job_id, (j) => {
        const bar = document.getElementById("lv-predict-progress");
        if (bar) bar.style.width = (j.progress * 100) + "%";
      });
      content.innerHTML = `<div style="display:flex;justify-content:flex-end;margin-bottom:8px">
          <button class="btn small ghost" id="lv-intl-close">✕ Close</button></div>
        <div id="lv-intl-box"></div>`;
      document.getElementById("lv-intl-close").addEventListener("click", () => { ov.style.display = "none"; });
      renderInternational(document.getElementById("lv-intl-box"), r);
    } catch (err) {
      content.innerHTML = `<div class="card">
        <div style="display:flex;justify-content:space-between;align-items:center">
          <div class="section-title" style="margin:0">Prediction unavailable</div>
          <button class="btn small ghost" id="lv-intl-close2">✕ Close</button></div>
        <div class="muted mt">${esc(err.message)}</div></div>`;
      document.getElementById("lv-intl-close2").addEventListener("click", () => { ov.style.display = "none"; });
    }
  }

  function renderInternational(box, r) {
    const p = r.prediction, pr = p.probabilities;
    const ou = p.markets.over_under;
    const riskCls = { low: "pill-good", medium: "pill-warn", high: "pill-bad" }[p.risk];
    const scorerTable = (sideKey) => {
      const s = r.player_markets ? r.player_markets[sideKey] : null;
      if (!s) return "";
      const rows = s.players.filter((pl) => pl.markets.anytime_scorer > 0.01).slice(0, 8).map((pl) => `
        <tr><td>${esc(pl.name)} <span class="faint">${esc(pl.position)}</span></td>
          <td>${(pl.markets.anytime_scorer * 100).toFixed(0)}%</td>
          <td>${(pl.markets.first_scorer * 100).toFixed(0)}%</td>
          <td>${(pl.markets.two_plus_scorer * 100).toFixed(0)}%</td>
          <td>${pl.markets.penalty_goal > 0 ? (pl.markets.penalty_goal * 100).toFixed(0) + "%" : "–"}</td>
          <td class="faint">${pl.analysis.goals}g</td></tr>`).join("");
      return `<div class="card"><div class="section-title">⚽ ${esc(s.team)} — scorer markets</div>
        ${rows ? `<div class="table-wrap"><table>
          <thead><tr><th>Player</th><th>Anytime</th><th>First</th><th>2+</th><th>Pen</th><th>Intl</th></tr></thead>
          <tbody>${rows}</tbody></table></div>`
          : `<div class="muted">No scorer data matched for this lineup.</div>`}</div>`;
    };
    const bookingTable = (sideKey) => {
      const s = r.player_markets ? r.player_markets[sideKey] : null;
      if (!s) return "";
      const rows = s.players.slice().sort((a, b) => b.markets.to_be_booked_est - a.markets.to_be_booked_est)
        .slice(0, 5).map((pl) => `<tr><td>${esc(pl.name)} <span class="faint">${esc(pl.position)}</span></td>
          <td>${(pl.markets.to_be_booked_est * 100).toFixed(0)}%</td></tr>`).join("");
      return `<div class="card"><div class="section-title">🟨 ${esc(s.team)} — to be booked <span class="faint">(estimate)</span></div>
        <div class="table-wrap"><table><thead><tr><th>Player</th><th>Est.</th></tr></thead>
        <tbody>${rows}</tbody></table></div></div>`;
    };

    box.innerHTML = `
      <div class="card mb">
        <div style="text-align:center">
          <div class="faint">${esc(p.resolved.feed_home)} vs ${esc(p.resolved.feed_away)}
            ${p.neutral ? "· neutral venue" : ""} · national teams model</div>
          <div class="page-title" style="margin:6px 0">${esc(p.home)}
            <span style="color:var(--accent2)">${esc(p.predicted_scoreline)}</span> ${esc(p.away)}</div>
        </div>
        <div class="mt">${probBar(pr.home, pr.draw, pr.away)}</div>
        <div class="faint" style="display:flex;justify-content:space-between;margin-top:4px">
          <span>${esc(p.home)} ${pct(pr.home)}</span><span>draw ${pct(pr.draw)}</span>
          <span>${esc(p.away)} ${pct(pr.away)}</span></div>
        <div class="mt" style="display:flex;gap:8px;flex-wrap:wrap;justify-content:center">
          <span class="pill pill-accent">confidence ${p.confidence_pct}%</span>
          <span class="pill ${riskCls}">risk ${p.risk}</span>
          <span class="pill pill-muted">xG ${p.expected_goals.home} – ${p.expected_goals.away}</span>
          <span class="pill pill-muted">Elo ${p.ratings.home_elo} – ${p.ratings.away_elo}</span>
          <span class="pill pill-muted">BTTS ${pct(p.markets.btts)}</span>
          <span class="pill pill-muted">Over 2.5 ${pct(ou["2.5"].over)}</span>
        </div>
        <div class="mt" style="text-align:center">
          ${p.alternatives.map((a) => `<span class="pill pill-muted" style="margin:2px">${a.score} ${pct(a.probability)}</span>`).join("")}
        </div>
      </div>
      ${r.has_lineups ? `
        <div class="grid grid-2 mb">${scorerTable("home")}${scorerTable("away")}</div>
        <div class="card mb"><div class="section-title">🎫 Suggested scorer picks (ranked by probability)</div>
          <div id="lv-intl-picks"></div></div>
        <div class="grid grid-2 mb">${bookingTable("home")}${bookingTable("away")}</div>
        <div class="faint">${esc(r.player_markets.notes.scoring_markets)}<br>${esc(r.player_markets.notes.card_markets)}</div>
      ` : `<div class="card"><div class="muted">Lineups not published yet — scorer markets appear once the
        starting XIs are announced (usually ~1 hour before kickoff). The match prediction above is available now.</div></div>`}`;

    if (r.has_lineups) {
      const all = [...r.player_markets.home.players.map((p) => ({ ...p, team: r.player_markets.home.team })),
        ...r.player_markets.away.players.map((p) => ({ ...p, team: r.player_markets.away.team }))]
        .filter((p) => p.markets.anytime_scorer > 0.03)
        .sort((a, b) => b.markets.anytime_scorer - a.markets.anytime_scorer).slice(0, 6);
      document.getElementById("lv-intl-picks").innerHTML = all.map((p) => `
        <div style="display:flex;justify-content:space-between;padding:6px 0;border-bottom:1px solid rgba(255,255,255,.05)">
          <span><b>${esc(p.name)}</b> <span class="faint">${esc(p.team)} · ${p.analysis.goals} intl goals</span></span>
          <span><span class="pill pill-good">anytime ${(p.markets.anytime_scorer * 100).toFixed(0)}%</span>
            <span class="pill pill-muted">first ${(p.markets.first_scorer * 100).toFixed(0)}%</span></span>
        </div>`).join("");
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
