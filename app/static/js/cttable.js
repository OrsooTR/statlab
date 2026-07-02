/* Crazy Time — interactive live table: physical wheel, chips, playable bonuses. */
"use strict";

(() => {
  const { register, get, post, pollJob, toast, esc, fmt, pct } = App;

  const SPOTS = ["1", "2", "5", "10", "coin_flip", "cash_hunt", "pachinko", "crazy_time"];
  const LABELS = { "1": "1", "2": "2", "5": "5", "10": "10", coin_flip: "COIN FLIP",
    cash_hunt: "CASH HUNT", pachinko: "PACHINKO", crazy_time: "CRAZY TIME" };
  const PAYS = { "1": "1:1", "2": "2:1", "5": "5:1", "10": "10:1",
    coin_flip: "bonus", cash_hunt: "bonus", pachinko: "bonus", crazy_time: "bonus" };
  const COLORS = { "1": "#4a7dd9", "2": "#e8c547", "5": "#d4589a", "10": "#7b4fd8",
    coin_flip: "#ef8354", cash_hunt: "#3fb68b", pachinko: "#e070c0", crazy_time: "#e0334b" };
  const CHIPS = [0.1, 0.5, 1, 5, 10, 25, 100];
  const CHIP_COLORS = { 0.1: "#8d99ae", 0.5: "#48bfe3", 1: "#4a7dd9", 5: "#e0334b",
    10: "#3fb68b", 25: "#e8c547", 100: "#111" };

  let session = null;
  let layout = null;
  let bets = {};
  let lastBets = {};
  let selectedChip = 1;
  let wheelAngle = 0;
  let spinning = false;

  register("ct/table", async (view) => {
    layout = await get("/api/crazytime/table/layout");
    if (!session) session = await post("/api/crazytime/table/session", { bankroll: 500 });
    else session = await get(`/api/crazytime/table/session/${session.session_id}`).catch(async () =>
      await post("/api/crazytime/table/session", { bankroll: 500 }));

    view.innerHTML = `
      <div class="page-title">Crazy Time — Live Table</div>
      <div class="page-sub">The physical wheel with chips and a running balance. Every spin
        uses the same calibrated engine as the mass simulator — RTP ≈ 94.4–96.1% by spot,
        the house edge never changes.</div>
      <div class="grid" style="grid-template-columns: minmax(420px,1fr) 380px; gap:18px">
        <div>
          <div class="card" style="text-align:center; position:relative">
            <div id="topslot" class="mb" style="display:flex;justify-content:center;gap:10px">
              <div class="pill pill-muted" id="ts-spot">TOP SLOT</div>
              <div class="pill pill-muted" id="ts-mult">— x</div>
            </div>
            <div style="position:relative; display:inline-block">
              <div style="position:absolute;left:50%;top:-6px;transform:translateX(-50%);z-index:3;
                width:0;height:0;border-left:13px solid transparent;border-right:13px solid transparent;
                border-top:22px solid #fff;filter:drop-shadow(0 2px 6px rgba(0,0,0,.6))"></div>
              <canvas id="wheel" width="460" height="460" style="max-width:100%"></canvas>
              <div id="wheel-center" style="position:absolute;inset:0;display:grid;place-items:center;
                pointer-events:none;font-weight:800;font-size:22px" ></div>
            </div>
            <div class="mt">
              <button class="btn" id="spin-btn" style="min-width:180px;font-size:16px">🎡 SPIN</button>
            </div>
            <div class="wheel-legend" style="justify-content:center">
              ${SPOTS.map((s) => `<span class="item"><span class="dot" style="background:${COLORS[s]}"></span>${LABELS[s]}</span>`).join("")}
            </div>
          </div>
          <div class="card mt">
            <div class="section-title">🕘 Results history</div>
            <div id="tbl-history" class="chip-row"></div>
          </div>
        </div>
        <div>
          <div class="card">
            <div style="display:flex;justify-content:space-between;align-items:baseline">
              <div class="stat" style="padding:0"><div class="label">Balance</div>
                <div class="value" id="tbl-balance">–</div></div>
              <div style="text-align:right">
                <div class="faint">total bet</div>
                <div style="font-weight:700" id="tbl-totalbet">0</div>
              </div>
            </div>
            <div class="faint" id="tbl-lastwin"></div>
            <hr style="border-color:var(--border);margin:12px 0">
            <div class="faint mb">Pick a chip, then click the spots. Right-click a spot to clear it.</div>
            <div class="chip-row mb" id="chip-picker">
              ${CHIPS.map((c) => `<div class="chip ${c === 1 ? "on" : ""}" data-chip="${c}"
                style="border-color:${CHIP_COLORS[c]};font-weight:700">${c}</div>`).join("")}
            </div>
            <div class="grid grid-2" id="bet-board" style="gap:8px">
              ${SPOTS.map((s) => `<div class="card" data-spot="${s}" style="padding:10px;cursor:pointer;
                text-align:center;border-color:${COLORS[s]}55;animation:none">
                <div style="font-weight:800;color:${COLORS[s]}">${LABELS[s]}</div>
                <div class="faint">${PAYS[s]}</div>
                <div style="font-weight:700;margin-top:4px" data-amt="${s}">–</div></div>`).join("")}
            </div>
            <div class="mt" style="display:flex;gap:8px;flex-wrap:wrap">
              <button class="btn small ghost" id="clear-bets">Clear</button>
              <button class="btn small ghost" id="rebet">Rebet</button>
              <button class="btn small ghost" id="new-session">New session…</button>
            </div>
          </div>
          <div class="card mt">
            <div class="section-title">⚡ Mass-simulate this exact layout</div>
            <div class="faint mb">Runs your current chip layout through the Monte Carlo engine.</div>
            <div class="grid grid-2">
              <label class="field"><span>Spins</span>
                <select id="auto-spins">${[1000, 10000, 100000, 500000, 1000000, 5000000, 10000000]
                  .map((n) => `<option value="${n}" ${n === 100000 ? "selected" : ""}>${n >= 1e6 ? n / 1e6 + "M" : n / 1e3 + "k"}</option>`).join("")}</select></label>
              <label class="field"><span>Runs</span>
                <input id="auto-runs" type="number" value="10" min="1" max="500"></label>
            </div>
            <button class="btn ghost" id="auto-run" style="width:100%">Simulate layout</button>
            <div class="progress-track" id="auto-track" style="display:none">
              <div class="progress-bar" id="auto-progress"></div></div>
            <div id="auto-out" class="mt"></div>
          </div>
        </div>
      </div>
      <div id="bonus-overlay" style="display:none;position:fixed;inset:0;z-index:50;
        background:rgba(5,6,15,.82);backdrop-filter:blur(8px);overflow-y:auto">
        <div style="max-width:900px;margin:40px auto;padding:0 20px" id="bonus-content"></div>
      </div>`;

    drawWheel(wheelAngle);
    refreshPanel();
    bindTable(view);
  });

  // ------------------------------------------------------------------- wheel
  function drawWheel(angle, highlight = -1) {
    const cv = document.getElementById("wheel");
    if (!cv) return;
    const ctx = cv.getContext("2d");
    const cx = cv.width / 2, cy = cv.height / 2;
    const R = cv.width / 2 - 8, r = 60;
    const n = layout.layout.length;
    const seg = (2 * Math.PI) / n;
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    for (let i = 0; i < n; i++) {
      const key = layout.layout[i];
      const a0 = -Math.PI / 2 + i * seg, a1 = a0 + seg;
      ctx.beginPath();
      ctx.moveTo(0, 0);
      ctx.arc(0, 0, R, a0, a1);
      ctx.closePath();
      ctx.fillStyle = COLORS[key];
      if (highlight === i) ctx.fillStyle = "#ffffff";
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.55)";
      ctx.lineWidth = 1.5;
      ctx.stroke();
      // label
      ctx.save();
      ctx.rotate(a0 + seg / 2 + Math.PI / 2);
      ctx.translate(0, -R + 26);
      ctx.rotate(Math.PI);
      ctx.fillStyle = highlight === i ? "#111" : "rgba(255,255,255,.95)";
      ctx.font = "bold 13px Segoe UI";
      ctx.textAlign = "center";
      const short = { coin_flip: "CF", cash_hunt: "CH", pachinko: "PA", crazy_time: "CT" };
      ctx.fillText(short[key] || key, 0, 0);
      ctx.restore();
    }
    // hub
    ctx.beginPath();
    ctx.arc(0, 0, r, 0, 2 * Math.PI);
    ctx.fillStyle = "#14162c";
    ctx.fill();
    ctx.strokeStyle = "rgba(255,255,255,.25)";
    ctx.lineWidth = 3;
    ctx.stroke();
    ctx.restore();
  }

  function spinTo(index) {
    return new Promise((resolve) => {
      const n = layout.layout.length;
      const seg = (2 * Math.PI) / n;
      const target = -(index + 0.5) * seg;          // segment centre under pointer
      const current = wheelAngle % (2 * Math.PI);
      const turns = 5 * 2 * Math.PI;
      const delta = turns + ((target - current) % (2 * Math.PI) + 2 * Math.PI) % (2 * Math.PI);
      const start = performance.now(), dur = 4200, a0 = wheelAngle;
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      (function frame(now) {
        const t = Math.min(1, (now - start) / dur);
        wheelAngle = a0 + delta * ease(t);
        drawWheel(wheelAngle, t === 1 ? index : -1);
        if (t < 1) requestAnimationFrame(frame);
        else resolve();
      })(performance.now());
    });
  }

  // ------------------------------------------------------------------- panel
  function refreshPanel() {
    document.getElementById("tbl-balance").textContent = fmt(session.balance, 2);
    const total = Object.values(bets).reduce((a, b) => a + b, 0);
    document.getElementById("tbl-totalbet").textContent = fmt(total, 2);
    SPOTS.forEach((s) => {
      const el = document.querySelector(`[data-amt="${s}"]`);
      if (el) el.textContent = bets[s] ? fmt(bets[s], 2) : "–";
    });
    const hist = document.getElementById("tbl-history");
    if (hist) hist.innerHTML = (session.history || []).slice().reverse().map((h) =>
      `<span class="chip" style="border-color:${COLORS[h.segment]};color:${COLORS[h.segment]};cursor:default"
        title="bet ${h.total_bet} → won ${h.winnings}${h.top_slot.matched ? " · TOP SLOT " + h.top_slot.multiplier + "x" : ""}">
        ${LABELS[h.segment]}${h.top_slot.matched ? " ⚡" : ""} ${h.net >= 0 ? "+" + fmt(h.net, 1) : fmt(h.net, 1)}</span>`).join("");
  }

  function bindTable(view) {
    document.getElementById("chip-picker").addEventListener("click", (e) => {
      const c = e.target.closest("[data-chip]");
      if (!c) return;
      selectedChip = parseFloat(c.dataset.chip);
      document.querySelectorAll("#chip-picker .chip").forEach((x) => x.classList.remove("on"));
      c.classList.add("on");
    });
    const board = document.getElementById("bet-board");
    board.addEventListener("click", (e) => {
      const s = e.target.closest("[data-spot]");
      if (!s || spinning) return;
      bets[s.dataset.spot] = (bets[s.dataset.spot] || 0) + selectedChip;
      refreshPanel();
    });
    board.addEventListener("contextmenu", (e) => {
      const s = e.target.closest("[data-spot]");
      if (!s) return;
      e.preventDefault();
      delete bets[s.dataset.spot];
      refreshPanel();
    });
    document.getElementById("clear-bets").addEventListener("click", () => { bets = {}; refreshPanel(); });
    document.getElementById("rebet").addEventListener("click", () => { bets = { ...lastBets }; refreshPanel(); });
    document.getElementById("new-session").addEventListener("click", async () => {
      const v = prompt("New session bankroll:", "500");
      if (!v) return;
      session = await post("/api/crazytime/table/session", { bankroll: parseFloat(v) });
      bets = {};
      refreshPanel();
      toast("New session started", "ok");
    });
    document.getElementById("spin-btn").addEventListener("click", doSpin);
    document.getElementById("auto-run").addEventListener("click", autoplay);
  }

  async function doSpin() {
    if (spinning) return;
    const total = Object.values(bets).reduce((a, b) => a + b, 0);
    if (total <= 0) { toast("Place at least one chip", "err"); return; }
    spinning = true;
    document.getElementById("spin-btn").disabled = true;
    try {
      const r = await post("/api/crazytime/table/spin",
        { session_id: session.session_id, bets });
      lastBets = { ...bets };
      // top slot reveal
      document.getElementById("ts-spot").textContent = LABELS[r.top_slot.spot];
      document.getElementById("ts-mult").textContent = r.top_slot.multiplier + "x";
      document.getElementById("ts-spot").className = "pill " + (r.top_slot.matched ? "pill-good" : "pill-muted");
      document.getElementById("ts-mult").className = "pill " + (r.top_slot.matched ? "pill-good" : "pill-muted");
      await spinTo(r.wheel_index);
      document.getElementById("wheel-center").textContent = LABELS[r.segment];
      setTimeout(() => { const el = document.getElementById("wheel-center"); if (el) el.textContent = ""; }, 2500);
      if (r.phase === "settled") {
        settleUI(r);
      } else if (r.phase === "bonus_settled") {
        await showBonus(r.segment, r, null);
        settleUI(r);
      } else if (r.phase === "await_choice") {
        await showBonus(r.game, r, async (choice) => {
          const done = await post("/api/crazytime/table/bonus-choice",
            { session_id: session.session_id, choice });
          settleUI(done);
          return done;
        });
      }
    } catch (err) {
      toast(err.message, "err", 6000);
    } finally {
      spinning = false;
      document.getElementById("spin-btn").disabled = false;
      session = await get(`/api/crazytime/table/session/${session.session_id}`);
      refreshPanel();
    }
  }

  function settleUI(r) {
    const stakeBack = r.winnings > 0 ? (r.bets[r.segment] || 0) : 0;
    const pureWin = r.winnings - stakeBack;
    const msg = r.winnings > 0
      ? `payout ${fmt(r.winnings, 2)} = ${fmt(pureWin, 2)} win + ${fmt(stakeBack, 2)} stake back (net ${r.net >= 0 ? "+" : ""}${fmt(r.net, 2)})`
      : `no win (net ${fmt(r.net, 2)})`;
    document.getElementById("tbl-lastwin").textContent =
      `Last spin: ${LABELS[r.segment]}${r.top_slot.matched ? ` with TOP SLOT ${r.top_slot.multiplier}x` : ""} — ${msg}`;
    toast(`${LABELS[r.segment]} — ${msg}`, r.net >= 0 ? "ok" : "err", 4500);
  }

  // ----------------------------------------------------------- bonus overlays
  function overlay(html) {
    const ov = document.getElementById("bonus-overlay");
    document.getElementById("bonus-content").innerHTML = html;
    ov.style.display = "";
    return ov;
  }
  function closeOverlay() {
    document.getElementById("bonus-overlay").style.display = "none";
  }

  async function showBonus(game, r, choose) {
    if (game === "coin_flip") {
      const d = r.detail;
      overlay(`<div class="card" style="text-align:center">
        <div class="page-title">🪙 COIN FLIP</div>
        <div style="display:flex;justify-content:center;gap:30px;margin:20px 0">
          <div class="card" style="border-color:#e0334b;min-width:130px">
            <div class="faint">RED</div><div class="value" style="font-size:30px">${d.red}x</div></div>
          <div class="card" style="border-color:#4a7dd9;min-width:130px">
            <div class="faint">BLUE</div><div class="value" style="font-size:30px">${d.blue}x</div></div>
        </div>
        <div id="coin" style="width:90px;height:90px;border-radius:50%;margin:0 auto 16px;
          display:grid;place-items:center;font-weight:800;font-size:20px;color:#fff;
          background:linear-gradient(135deg,#666,#999);transition:background .3s">?</div>
        <div id="coin-msg" class="muted">flipping…</div>
        <button class="btn mt" id="bonus-close" style="display:none">Collect</button></div>`);
      await sleep(500);
      const coin = document.getElementById("coin");
      for (let i = 0; i < 10; i++) {
        coin.style.background = i % 2 ? "linear-gradient(135deg,#e0334b,#a02) " : "linear-gradient(135deg,#4a7dd9,#249)";
        coin.textContent = i % 2 ? "R" : "B";
        await sleep(120 + i * 30);
      }
      coin.style.background = d.result === "red" ? "linear-gradient(135deg,#e0334b,#a02)" : "linear-gradient(135deg,#4a7dd9,#249)";
      coin.textContent = d.result.toUpperCase();
      document.getElementById("coin-msg").innerHTML =
        `<b class="good">${d.result.toUpperCase()} wins — ${d.won_multiplier}x</b>` +
        (r.bets.coin_flip ? ` · you win ${fmt(r.winnings, 2)}` : " · you had no chips on Coin Flip");
      const btn = document.getElementById("bonus-close");
      btn.style.display = "";
      await waitClick(btn);
      closeOverlay();
      return;
    }

    if (game === "pachinko") {
      const d = r.detail;
      overlay(`<div class="card" style="text-align:center">
        <div class="page-title">🎯 PACHINKO</div><div id="pk-area"></div>
        <button class="btn mt" id="bonus-close" style="display:none">Collect</button></div>`);
      const area = document.getElementById("pk-area");
      for (const drop of d.drops) {
        area.innerHTML = `<div class="chip-row" style="justify-content:center;margin:14px 0" id="pk-wall">
          ${drop.wall.map((v, i) => `<span class="chip" data-i="${i}" style="cursor:default;min-width:54px;
            ${v === "DOUBLE" ? "border-color:#e8c547;color:#e8c547" : ""}">${v === "DOUBLE" ? "2×ALL" : v + "x"}</span>`).join("")}
        </div><div class="muted" id="pk-msg">puck dropping…</div>`;
        const cells = [...area.querySelectorAll("[data-i]")];
        for (const pos of drop.path) {
          cells.forEach((c) => c.classList.remove("on"));
          cells[pos].classList.add("on");
          await sleep(140);
        }
        document.getElementById("pk-msg").innerHTML = drop.landed === "DOUBLE"
          ? `<b style="color:#e8c547">DOUBLE — all values ×2, dropping again…</b>`
          : `<b class="good">landed on ${drop.landed}x</b>`;
        await sleep(1100);
      }
      area.insertAdjacentHTML("beforeend", `<div class="mt"><b class="good">Pachinko pays ${d.won_multiplier}x</b>
        ${r.bets.pachinko ? ` · you win ${fmt(r.winnings, 2)}` : " · you had no chips on Pachinko"}</div>`);
      const btn = document.getElementById("bonus-close");
      btn.style.display = "";
      await waitClick(btn);
      closeOverlay();
      return;
    }

    if (game === "cash_hunt") {
      overlay(`<div class="card" style="text-align:center">
        <div class="page-title">🔫 CASH HUNT</div>
        <div class="muted mb">108 multipliers revealed… memorise, they shuffle, then pick a target!</div>
        <div id="chgrid" style="display:grid;grid-template-columns:repeat(12,1fr);gap:4px"></div>
        <div class="mt muted" id="ch-msg"></div></div>`);
      const grid = document.getElementById("chgrid");
      grid.innerHTML = r.preview_board.map((v) =>
        `<div style="padding:6px 2px;border-radius:6px;background:var(--glass-strong);font-size:11px">${v}x</div>`).join("");
      await sleep(2600);
      grid.innerHTML = Array.from({ length: r.board_size }, (_, i) =>
        `<div data-pick="${i}" style="padding:6px 2px;border-radius:6px;background:rgba(109,93,246,.3);
          cursor:pointer;font-size:11px">🎯</div>`).join("");
      document.getElementById("ch-msg").textContent = "shuffled — click a square";
      const pick = await new Promise((resolve) => grid.addEventListener("click", (e) => {
        const c = e.target.closest("[data-pick]");
        if (c) resolve(parseInt(c.dataset.pick, 10));
      }, { once: true }));
      const done = await choose(pick);
      const d = done.detail;
      grid.innerHTML = d.board.map((v, i) =>
        `<div style="padding:6px 2px;border-radius:6px;font-size:11px;
          background:${i === d.pick_index ? "rgba(52,211,153,.5)" : "var(--glass-strong)"}">${v}x</div>`).join("");
      document.getElementById("ch-msg").innerHTML =
        `<b class="good">you hit ${d.won_multiplier}x</b>` +
        (done.bets.cash_hunt ? ` · you win ${fmt(done.winnings, 2)}` : " · you had no chips on Cash Hunt");
      document.getElementById("bonus-content").firstElementChild.insertAdjacentHTML("beforeend",
        `<button class="btn mt" id="bonus-close">Collect</button>`);
      await waitClick(document.getElementById("bonus-close"));
      closeOverlay();
      return;
    }

    if (game === "crazy_time") {
      overlay(`<div class="card" style="text-align:center">
        <div class="page-title" style="color:#e0334b">🎪 CRAZY TIME</div>
        <div class="muted mb">The giant 64-segment wheel behind the red door. Pick your flapper!</div>
        <canvas id="ctw" width="380" height="380" style="max-width:100%"></canvas>
        <div class="mt" id="ct-flappers">
          <button class="btn" style="background:#4a7dd9" data-fl="blue">BLUE</button>
          <button class="btn" style="background:#3fb68b" data-fl="green">GREEN</button>
          <button class="btn" style="background:#e8c547;color:#222" data-fl="yellow">YELLOW</button>
        </div>
        <div class="mt muted" id="ct-msg"></div></div>`);
      let wheelVals = r.bonus_wheel;
      drawBonusWheel(wheelVals, 0);
      const color = await new Promise((resolve) =>
        document.getElementById("ct-flappers").addEventListener("click", (e) => {
          const b = e.target.closest("[data-fl]");
          if (b) resolve(b.dataset.fl);
        }, { once: true }));
      document.getElementById("ct-flappers").style.display = "none";
      document.getElementById("ct-msg").textContent = `spinning with the ${color.toUpperCase()} flapper…`;
      const done = await choose(color);
      const d = done.detail;
      for (const s of d.spins) {
        await animateBonusWheel(wheelVals, s.index);
        const mine = s.landed[color];
        if (mine === "DOUBLE" || mine === "TRIPLE") {
          document.getElementById("ct-msg").innerHTML =
            `<b style="color:#e8c547">${mine}! all values ${mine === "DOUBLE" ? "×2" : "×3"} — respin…</b>`;
          wheelVals = s.rescaled_wheel || wheelVals;
          drawBonusWheel(wheelVals, 0);
          await sleep(1200);
        } else {
          document.getElementById("ct-msg").innerHTML =
            `<b class="good">${color.toUpperCase()} flapper lands ${mine}x</b>
             <span class="faint">(blue ${s.landed.blue} · green ${s.landed.green} · yellow ${s.landed.yellow})</span>` +
            (done.bets.crazy_time ? `<br>you win ${fmt(done.winnings, 2)}` : "<br>you had no chips on Crazy Time");
        }
      }
      document.getElementById("bonus-content").firstElementChild.insertAdjacentHTML("beforeend",
        `<button class="btn mt" id="bonus-close">Collect</button>`);
      await waitClick(document.getElementById("bonus-close"));
      closeOverlay();
    }
  }

  function drawBonusWheel(vals, angle, highlight = -1) {
    const cv = document.getElementById("ctw");
    if (!cv) return;
    const ctx = cv.getContext("2d");
    const cx = cv.width / 2, cy = cv.height / 2, R = cv.width / 2 - 6;
    const n = vals.length, seg = (2 * Math.PI) / n;
    const palette = ["#4a7dd9", "#3fb68b", "#e8c547", "#d4589a", "#7b4fd8", "#ef8354"];
    ctx.clearRect(0, 0, cv.width, cv.height);
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(angle);
    for (let i = 0; i < n; i++) {
      const a0 = -Math.PI / 2 + i * seg;
      ctx.beginPath(); ctx.moveTo(0, 0); ctx.arc(0, 0, R, a0, a0 + seg); ctx.closePath();
      const v = vals[i];
      ctx.fillStyle = v === "DOUBLE" ? "#fff" : v === "TRIPLE" ? "#000" : palette[i % palette.length];
      if (highlight === i) ctx.fillStyle = "#22d3ee";
      ctx.fill();
      ctx.strokeStyle = "rgba(0,0,0,.5)"; ctx.lineWidth = 1; ctx.stroke();
      ctx.save();
      ctx.rotate(a0 + seg / 2 + Math.PI / 2);
      ctx.translate(0, -R + 16);
      ctx.rotate(Math.PI);
      ctx.fillStyle = v === "DOUBLE" ? "#222" : "#fff";
      ctx.font = "bold 8px Segoe UI"; ctx.textAlign = "center";
      ctx.fillText(v === "DOUBLE" ? "x2" : v === "TRIPLE" ? "x3" : String(v), 0, 0);
      ctx.restore();
    }
    ctx.beginPath(); ctx.arc(0, 0, 30, 0, 2 * Math.PI);
    ctx.fillStyle = "#14162c"; ctx.fill();
    ctx.restore();
    // pointer
    ctx.beginPath();
    ctx.moveTo(cx - 9, 2); ctx.lineTo(cx + 9, 2); ctx.lineTo(cx, 20); ctx.closePath();
    ctx.fillStyle = "#fff"; ctx.fill();
  }

  function animateBonusWheel(vals, index) {
    return new Promise((resolve) => {
      const n = vals.length, seg = (2 * Math.PI) / n;
      const target = -(index + 0.5) * seg + 4 * 2 * Math.PI;
      const start = performance.now(), dur = 2800;
      const ease = (t) => 1 - Math.pow(1 - t, 3);
      (function frame(now) {
        const t = Math.min(1, (now - start) / dur);
        drawBonusWheel(vals, target * ease(t), t === 1 ? index : -1);
        if (t < 1) requestAnimationFrame(frame);
        else setTimeout(resolve, 400);
      })(performance.now());
    });
  }

  // -------------------------------------------------------------- autoplay
  async function autoplay() {
    const total = Object.values(bets).reduce((a, b) => a + b, 0);
    if (total <= 0) { toast("Place chips first — the layout is what gets simulated", "err"); return; }
    const params = {};
    SPOTS.forEach((s) => { params[`u_${s}`] = bets[s] || 0; });
    const btn = document.getElementById("auto-run");
    btn.disabled = true;
    document.getElementById("auto-track").style.display = "";
    try {
      const { job_id } = await post("/api/crazytime/simulate", {
        strategy: "custom_layout", params,
        spins: parseInt(document.getElementById("auto-spins").value, 10),
        runs: parseInt(document.getElementById("auto-runs").value, 10),
        bankroll: session.balance > 0 ? session.balance : 500,
        bet_unit: 1,
        name: "Live table layout",
      });
      const r = await pollJob("/api/crazytime", job_id, (j) => {
        document.getElementById("auto-progress").style.width = (j.progress * 100) + "%";
      });
      document.getElementById("auto-out").innerHTML = `
        <div class="table-wrap"><table><tbody>
          <tr><td>Mean final balance</td><td>${fmt(r.mean_final_balance, 2)}</td></tr>
          <tr><td>Mean ROI</td><td class="${r.mean_roi > 0 ? "good" : "bad"}">${pct(r.mean_roi)}</td></tr>
          <tr><td>Risk of ruin</td><td>${pct(r.risk_of_ruin)}</td></tr>
          <tr><td>Mean spins survived</td><td>${fmt(r.mean_spins_survived, 0)}</td></tr>
          <tr><td>RTP achieved</td><td>${pct(r.mean_rtp_achieved)}</td></tr>
          <tr><td>Max drawdown (mean)</td><td>${pct(r.mean_max_drawdown)}</td></tr>
        </tbody></table></div>
        <div class="faint mt">Full charts in Results History (saved as “Live table layout”).</div>`;
    } catch (err) { toast(err.message, "err", 7000); }
    finally {
      btn.disabled = false;
      document.getElementById("auto-track").style.display = "none";
    }
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const waitClick = (el) => new Promise((r) => el.addEventListener("click", r, { once: true }));
})();
