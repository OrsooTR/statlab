/* System / Updates view: version info and in-app auto-update. */
"use strict";

(() => {
  const { register, get, post, pollJob, toast, esc } = App;

  // startup badge: flag the sidebar link if an update is available
  App.checkUpdatesOnStart = async () => {
    try {
      const u = await get("/api/system/check-update");
      if (u.available) {
        const link = document.querySelector('nav a[data-route="sys/updates"]');
        if (link && !link.querySelector(".upd-badge")) {
          const b = document.createElement("span");
          b.className = "upd-badge pill pill-good";
          b.style.cssText = "margin-left:auto;font-size:9px;padding:2px 7px";
          b.textContent = u.latest;
          link.appendChild(b);
        }
        toast(`Update available: ${u.latest} (you have ${u.current}). Open Updates to install.`, "ok", 8000);
      }
    } catch (e) { /* offline: ignore */ }
  };

  register("sys/updates", async (view) => {
    const info = await get("/api/system/info");
    view.innerHTML = `
      <div class="page-title">Updates</div>
      <div class="page-sub">In-app updates — no manual re-download. StatLab checks the public
        GitHub releases and, on the packaged build, downloads and runs the installer for you.</div>
      <div class="grid grid-2">
        <div class="card">
          <div class="section-title">📦 This installation</div>
          <div class="table-wrap"><table><tbody>
            <tr><td>Version</td><td><b>${esc(info.version)}</b></td></tr>
            <tr><td>Mode</td><td>${info.frozen ? "packaged executable" : "running from source"}</td></tr>
            <tr><td>Repository</td><td>${esc(info.repo)}</td></tr>
          </tbody></table></div>
          <button class="btn mt" id="upd-check" style="width:100%">🔍 Check for updates</button>
        </div>
        <div class="card" id="upd-panel">
          <div class="section-title">⬆️ Latest release</div>
          <div class="muted">Press “Check for updates”.</div>
        </div>
      </div>`;

    document.getElementById("upd-check").addEventListener("click", checkNow);
    checkNow();

    async function checkNow() {
      const panel = document.getElementById("upd-panel");
      panel.innerHTML = `<div class="section-title">⬆️ Latest release</div>
        <div class="muted"><span class="spinner"></span> checking GitHub…</div>`;
      let u;
      try { u = await get("/api/system/check-update"); }
      catch (err) { panel.innerHTML = `<div class="muted">${esc(err.message)}</div>`; return; }
      if (u.error) {
        panel.innerHTML = `<div class="section-title">⬆️ Latest release</div>
          <div class="muted">${esc(u.error)}</div>`;
        return;
      }
      if (!u.available) {
        panel.innerHTML = `<div class="section-title">✅ Up to date</div>
          <div class="muted">You are running the latest version (${esc(u.current)}).</div>`;
        return;
      }
      panel.innerHTML = `
        <div class="section-title">🆕 ${esc(u.latest)} available
          <span class="pill pill-good" style="margin-left:8px">new</span></div>
        <div class="faint mb">You have ${esc(u.current)}${u.asset_size
          ? ` · download ${Math.round(u.asset_size / 1048576)} MB` : ""}</div>
        <div class="card" style="max-height:220px;overflow-y:auto;background:rgba(0,0,0,.2)">
          <pre style="white-space:pre-wrap;font-size:11.5px;margin:0;color:var(--text-dim)">${esc(u.notes || "")}</pre>
        </div>
        <div class="mt">
          ${u.can_auto_install
            ? `<button class="btn" id="upd-apply" style="width:100%">⬇️ Download &amp; install now</button>`
            : `<a class="btn" href="${esc(u.release_page)}" target="_blank" style="width:100%;text-decoration:none">Open release page ↗</a>
               <div class="faint mt">Auto-install runs on the packaged app; from source, download manually.</div>`}
        </div>
        <div class="progress-track" id="upd-track" style="display:none"><div class="progress-bar" id="upd-progress"></div></div>
        <div class="faint" id="upd-msg"></div>`;

      const applyBtn = document.getElementById("upd-apply");
      if (applyBtn) applyBtn.addEventListener("click", async () => {
        applyBtn.disabled = true;
        document.getElementById("upd-track").style.display = "";
        try {
          const { job_id } = await post("/api/system/apply-update", null);
          const res = await pollJob("/api/system", job_id, (j) => {
            document.getElementById("upd-progress").style.width = (j.progress * 100) + "%";
            document.getElementById("upd-msg").textContent = j.message || "";
          });
          if (res.status === "installing") {
            document.getElementById("upd-msg").innerHTML =
              `<b class="good">Installer launched — StatLab will close and reopen updated.</b>`;
            toast("Updating… the app will restart.", "ok", 9000);
          } else {
            document.getElementById("upd-msg").textContent = res.message || res.status;
          }
        } catch (err) {
          toast(err.message, "err", 8000);
          applyBtn.disabled = false;
        }
      });
    }
  });
})();
