/* StatLab SPA shell: hash router, API client, job polling, chart helpers. */
"use strict";

const App = (() => {
  const routes = {};
  let charts = [];

  // ------------------------------------------------------------ API client
  async function api(path, opts = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) { /* noop */ }
      throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
    }
    return res.json();
  }

  const get = (p) => api(p);
  const post = (p, body) => api(p, { method: "POST", body });
  const del = (p) => api(p, { method: "DELETE" });

  // ------------------------------------------------------- job poll helper
  async function pollJob(base, jobId, onProgress) {
    for (;;) {
      const j = await get(`${base}/jobs/${jobId}`);
      if (onProgress) onProgress(j);
      if (j.status === "done") return j.result;
      if (j.status === "error") throw new Error(j.error || "job failed");
      await new Promise((r) => setTimeout(r, 450));
    }
  }

  // ---------------------------------------------------------------- toasts
  function toast(msg, kind = "ok", ms = 4200) {
    const root = document.getElementById("toast-root");
    const el = document.createElement("div");
    el.className = `toast ${kind}`;
    el.textContent = msg;
    root.appendChild(el);
    setTimeout(() => el.remove(), ms);
  }

  // ---------------------------------------------------------------- router
  function register(route, renderFn) { routes[route] = renderFn; }

  function destroyCharts() {
    charts.forEach((c) => { try { c.destroy(); } catch (e) { /* noop */ } });
    charts = [];
  }

  async function navigate() {
    const hash = location.hash.replace(/^#\//, "") || "ct/simulator";
    const route = routes[hash] ? hash : "ct/simulator";
    document.querySelectorAll("nav a").forEach((a) =>
      a.classList.toggle("active", a.dataset.route === route));
    destroyCharts();
    const view = document.getElementById("view");
    view.innerHTML = `<div class="empty"><span class="spinner"></span>&nbsp; loading…</div>`;
    try {
      await routes[route](view);
    } catch (err) {
      view.innerHTML = `<div class="card"><div class="section-title">Something went wrong</div>
        <div class="muted">${esc(err.message)}</div></div>`;
    }
  }

  // ----------------------------------------------------------- chart utils
  const PALETTE = ["#6d5df6", "#22d3ee", "#34d399", "#fbbf24", "#f87171",
                   "#e879f9", "#60a5fa", "#fb923c", "#a3e635", "#94a3b8"];

  function baseOpts(extra = {}) {
    return {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { labels: { color: "#9aa0bd", font: { size: 11 }, boxWidth: 12 } },
        tooltip: { backgroundColor: "rgba(15,17,35,.95)", borderColor: "rgba(255,255,255,.1)", borderWidth: 1 },
        ...extra.plugins,
      },
      scales: extra.noScales ? undefined : {
        x: { ticks: { color: "#6b7194", font: { size: 10 }, maxTicksLimit: 12 },
             grid: { color: "rgba(255,255,255,.04)" }, ...(extra.x || {}) },
        y: { ticks: { color: "#6b7194", font: { size: 10 } },
             grid: { color: "rgba(255,255,255,.05)" }, ...(extra.y || {}) },
      },
      animation: { duration: 500 },
      ...extra.root,
    };
  }

  function chart(canvasId, config) {
    const el = document.getElementById(canvasId);
    if (!el) return null;
    const c = new Chart(el.getContext("2d"), config);
    charts.push(c);
    return c;
  }

  // --------------------------------------------------------------- helpers
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (v, d = 2) => (v === null || v === undefined || Number.isNaN(v))
    ? "–" : Number(v).toLocaleString("en-US", { maximumFractionDigits: d, minimumFractionDigits: 0 });
  const pct = (v, d = 1) => (v === null || v === undefined) ? "–" : (v * 100).toFixed(d) + "%";
  const money = (v) => (v === null || v === undefined) ? "–"
    : Number(v).toLocaleString("en-US", { maximumFractionDigits: 2 });

  function statCard(label, value, cls = "", sub = "") {
    return `<div class="card stat"><div class="label">${esc(label)}</div>
      <div class="value ${cls}">${value}</div>
      ${sub ? `<div class="sub">${esc(sub)}</div>` : ""}</div>`;
  }

  function probBar(ph, pd, pa) {
    const w = (x) => Math.max(3, x * 100);
    return `<div class="prob-bar">
      <div class="prob-h" style="flex:${w(ph)}">${(ph * 100).toFixed(0)}%</div>
      <div class="prob-d" style="flex:${w(pd)}">${(pd * 100).toFixed(0)}%</div>
      <div class="prob-a" style="flex:${w(pa)}">${(pa * 100).toFixed(0)}%</div>
    </div>`;
  }

  // ----------------------------------------------------------------- start
  async function start() {
    window.addEventListener("hashchange", navigate);
    navigate();
    try {
      await get("/api/health");
      const pill = document.getElementById("health-pill");
      pill.textContent = "● engine online";
      pill.className = "pill pill-good";
      if (App.checkUpdatesOnStart) App.checkUpdatesOnStart();
    } catch (e) {
      const pill = document.getElementById("health-pill");
      pill.textContent = "● backend offline";
      pill.className = "pill pill-bad";
    }
  }

  return { register, start, get, post, del, pollJob, toast, chart, baseOpts,
           PALETTE, esc, fmt, pct, money, statCard, probBar };
})();
