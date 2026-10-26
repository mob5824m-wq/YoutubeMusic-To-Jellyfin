/* ytm2j web UI */
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

const state = {
  playlists: [],
  selected: new Set(),
  filter: "",
  formats: [],
  browsers: [],
  config: null,
};

const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

async function api(path, opts = {}) {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { raw: text }; }
  if (!res.ok) {
    const msg = (data && (data.detail || data.error)) || res.statusText;
    throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  }
  return data;
}

/* ----------------------------- Config ------------------------------- */
async function loadConfig() {
  const data = await api("/api/config");
  state.config = data.config;
  state.formats = data.audio_formats;
  state.browsers = data.browsers;
  $("#set-output").value = data.config.output_dir || "";
  $("#set-output-hint").textContent = `Resolves to: ${data.platforms.output_dir}`;
  const fmt = $("#set-format");
  fmt.innerHTML = state.formats.map((f) => `<option value="${f}" ${f === data.config.audio_format ? "selected" : ""}>${f}</option>`).join("");
  $("#set-quality").value = data.config.audio_quality || "";
  const browserSel = $("#set-browser");
  browserSel.innerHTML = '<option value="">— none —</option>' +
    state.browsers.map((b) => `<option value="${b.id}" ${b.id === data.config.cookie_browser ? "selected" : ""}>${esc(b.label)}</option>`).join("");
  $("#set-cookiefile").value = data.config.cookie_file || "";
  $("#set-art").checked = !!data.config.embed_thumbnail;
  $("#set-meta").checked = !!data.config.add_metadata;
  $("#set-player").value = data.config.player_client || "";
  $("#set-clientid").value = data.config.google_client_id || "";
  $("#set-clientsecret").value = data.config.google_client_secret || "";
  const hint = $("#set-cookie-hint");
  hint.textContent = data.config.cookie_browser
    ? `Using cookies from ${browserSel.options[browserSel.selectedIndex].text}.`
    : data.config.cookie_file
      ? `Using cookies.txt: ${data.config.cookie_file}`
      : "No cookie source set. Public playlists work without it; your private/liked playlists need cookies.";
}

async function saveConfig() {
  const values = {
    output_dir: $("#set-output").value,
    audio_format: $("#set-format").value,
    audio_quality: $("#set-quality").value || "320",
    cookie_browser: $("#set-browser").value,
    cookie_file: $("#set-cookiefile").value,
    embed_thumbnail: $("#set-art").checked,
    add_metadata: $("#set-meta").checked,
    player_client: $("#set-player").value,
    google_client_id: $("#set-clientid").value,
    google_client_secret: $("#set-clientsecret").value,
  };
  try {
    await api("/api/config", { method: "POST", body: JSON.stringify({ values }) });
    $("#set-error").textContent = "";
    await loadConfig();
    await loadCookies();
    flash("Settings saved");
  } catch (e) {
    $("#set-error").textContent = e.message;
  }
}

/* ----------------------------- Cookies ------------------------------ */
async function loadCookies() {
  const c = await api("/api/cookies");
  const badge = $("#cookie-badge");
  if (c.configured && !c.notes) {
    badge.className = "badge badge-ok";
    badge.textContent = c.method === "browser" ? `✓ cookies: ${c.browser_label}` : "✓ cookies file";
  } else if (c.configured) {
    badge.className = "badge badge-warn";
    badge.textContent = c.method === "browser" ? c.browser_label : "cookies file";
  } else {
    badge.className = "badge badge-err";
    badge.textContent = "⚠ no cookies configured";
  }
}

/* ------------------------------ Auth -------------------------------- */
const authState = { state: "" };

async function loadAuth() {
  try {
    const st = await api("/api/auth/status");
    const badge = $("#auth-badge");
    if (st.connected) {
      badge.className = "badge badge-ok";
      badge.textContent = "Google: connected";
    } else {
      badge.className = "badge badge-muted";
      badge.textContent = st.configured ? "Google: not authorized" : "Google: not configured";
    }
    const s = $("#auth-status-text");
    if (st.connected) {
      s.className = "auth-status ok";
      s.textContent = `Connected - access expires in ${st.expires_in}s.`;
    } else {
      s.className = "auth-status";
      s.textContent = st.reason || "Not connected.";
    }
  } catch (e) { /* offline / not critical */ }
}

async function startAuth() {
  try {
    const data = await api("/api/auth/start", { method: "POST" });
    authState.state = data.state;
    $("#auth-url").value = data.url;
    const win = window.open(data.url, "_blank");
    if (!win) flash("If the popup was blocked, copy the URL and paste the result below");
  } catch (e) {
    alert("Could not start Google authorization:\n\n" + e.message);
  }
}

async function completeAuth() {
  const url = $("#auth-url").value.trim();
  const code = $("#auth-code").value.trim();
  if (!url && !code) return;
  try {
    await api("/api/auth/exchange", { method: "POST", body: JSON.stringify({ state: authState.state, url, code }) });
    $("#auth-code").value = "";
    flash("Google account connected");
    await loadAuth();
    await loadCookies();
  } catch (e) {
    alert("Authorization failed:\n\n" + e.message);
  }
}

async function logoutAuth() {
  await api("/api/auth/logout", { method: "POST" });
  $("#auth-url").value = "";
  flash("Google account disconnected");
  await loadAuth();
}

/* ----------------------------- Playlists ---------------------------- */
async function loadPlaylists() {
  const data = await api("/api/playlists");
  state.playlists = data.playlists || [];
  renderPlaylists();
}

function renderPlaylists() {
  const ul = $("#playlists");
  const empty = $("#playlist-empty");
  const items = state.playlists.filter((p) => {
    const q = state.filter.trim().toLowerCase();
    return !q || (p.title || "").toLowerCase().includes(q) || (p.id || "").toLowerCase().includes(q);
  });
  ul.innerHTML = items.map((p) => {
    const checked = state.selected.has(p.id) ? "checked" : "";
    return `<li class="item" data-id="${esc(p.id)}" data-url="${esc(p.url)}">
      <input type="checkbox" ${checked} data-role="select" />
      <div class="item-main">
        <div class="item-title">${esc(p.title)}</div>
        <div class="item-sub">${p.entry_count ? p.entry_count + " tracks · " : ""}${esc(p.url)}</div>
      </div>
      <span class="kind ${p.kind === "special" ? "special" : ""}">${esc(p.kind)}</span>
      <div class="item-actions">
        <button class="btn btn-sm" data-role="download" title="Download this playlist">↓</button>
        <button class="btn btn-sm" data-role="remove" title="Remove from list">✕</button>
      </div>
    </li>`;
  }).join("");
  empty.style.display = items.length ? "none" : "block";
  syncSelection();
}

function syncSelection() {
  const visible = $$("#playlists .item").map((li) => li.dataset.id);
  // prune selection for hidden/removed items
  for (const id of Array.from(state.selected)) if (!visible.includes(id)) state.selected.delete(id);
  $("#sel-count").textContent = String(state.selected.size);
  $("#btn-download-selected").disabled = state.selected.size === 0;
}

async function discover() {
  const btn = $("#btn-discover");
  btn.disabled = true;
  btn.textContent = "Discovering…";
  try {
    const data = await api("/api/discover", { method: "POST" });
    await loadPlaylists();
    flash(`Found ${data.count} playlist(s)`);
  } catch (e) {
    alert("Discovery failed:\n\n" + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Discover playlists";
  }
}

async function addPlaylist() {
  const input = $("#add-url");
  const url = input.value.trim();
  if (!url) return;
  try {
    await api("/api/playlists/manual", { method: "POST", body: JSON.stringify({ url }) });
    input.value = "";
    await loadPlaylists();
    flash("Playlist added");
  } catch (e) {
    alert("Could not add playlist:\n\n" + e.message);
  }
}

async function downloadSelected() {
  if (!state.selected.size) return;
  await api("/api/download", { method: "POST", body: JSON.stringify({ ids: Array.from(state.selected) }) });
  flash(`Queued ${state.selected.size} playlist(s)`);
  await loadJobs();
}

async function downloadOne(id) {
  await api(`/api/download/${encodeURIComponent(id)}`, { method: "POST" });
  flash("Playlist queued");
  await loadJobs();
}

async function removePlaylist(id) {
  await api(`/api/playlists/${encodeURIComponent(id)}`, { method: "DELETE" });
  state.selected.delete(id);
  await loadPlaylists();
}

/* ------------------------------- Jobs ------------------------------- */
async function loadJobs() {
  const data = await api("/api/jobs");
  renderJobs(data.jobs || []);
}

function renderJobs(jobs) {
  const ul = $("#jobs");
  const empty = $("#jobs-empty");
  ul.innerHTML = jobs.map((j) => {
    const pct = j.total ? Math.round((j.done / j.total) * 100) : (j.status === "done" ? 100 : 0);
    const cls = j.status === "done" ? "job-done" : j.status === "error" ? "job-error" : "";
    const msg = j.error ? `<div class="job-error-msg">${esc(j.error)}</div>` : "";
    const file = j.current_file ? `<div class="job-file">${esc(j.current_file.split("/").pop())}</div>` : "";
    const cancelBtn = ["queued", "running", "paused"].includes(j.status)
      ? `<button class="btn btn-sm" data-role="cancel">Cancel</button>` : "";
    return `<li class="item ${cls}" data-job="${esc(j.id)}">
      <div class="job-top">
        <div class="item-main">
          <div class="item-title">${esc(j.title)}</div>
          <div class="item-sub">${j.done}/${j.total} tracks · ${esc(j.folder)}</div>
          <div class="bar"><span style="width:${pct}%"></span></div>
        </div>
        <div class="item-actions">
          <span class="status">${esc(j.status)}</span>
          ${cancelBtn}
          <button class="btn btn-sm" data-role="remove">✕</button>
        </div>
      </div>
      ${file}
      ${msg}
    </li>`;
  }).join("");
  empty.style.display = jobs.length ? "none" : "block";
}

/* ------------------------------ Helpers ---------------------------- */
let flashTimer = null;
function flash(msg) {
  let el = $("#flash");
  if (!el) {
    el = document.createElement("div");
    el.id = "flash";
    el.style.cssText = "position:fixed;bottom:20px;left:50%;transform:translateX(-50%);background:var(--accent-2);color:#fff;padding:9px 16px;border-radius:999px;z-index:99;font-size:14px;box-shadow:var(--shadow)";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.style.opacity = "1";
  clearTimeout(flashTimer);
  flashTimer = setTimeout(() => { el.style.transition = "opacity .4s"; el.style.opacity = "0"; }, 2200);
}

/* ------------------------------ Events ------------------------------ */
$("#btn-discover").addEventListener("click", discover);
$("#btn-settings").addEventListener("click", () => { $("#settings-modal").hidden = false; loadConfig(); loadAuth(); });
$("#btn-google").addEventListener("click", () => { $("#settings-modal").hidden = false; loadAuth(); startAuth(); });
$("#btn-google-connect").addEventListener("click", startAuth);
$("#btn-google-complete").addEventListener("click", completeAuth);
$("#btn-google-logout").addEventListener("click", logoutAuth);
$("#auth-code").addEventListener("keydown", (e) => { if (e.key === "Enter") completeAuth(); });
$("#btn-settings-close").addEventListener("click", () => { $("#settings-modal").hidden = true; });
$("#btn-settings-cancel").addEventListener("click", () => { $("#settings-modal").hidden = true; });
$("#btn-settings-save").addEventListener("click", saveConfig);
$("#settings-modal").addEventListener("click", (e) => { if (e.target.id === "settings-modal") $("#settings-modal").hidden = true; });
$("#btn-download-selected").addEventListener("click", downloadSelected);
$("#btn-add").addEventListener("click", addPlaylist);
$("#add-url").addEventListener("keydown", (e) => { if (e.key === "Enter") addPlaylist(); });
$("#search").addEventListener("input", (e) => { state.filter = e.target.value; renderPlaylists(); });
$("#btn-clear").addEventListener("click", async () => { await api("/api/jobs/clear", { method: "POST" }); await loadJobs(); });

$("#playlists").addEventListener("change", (e) => {
  if (e.target.dataset.role === "select") {
    const id = e.target.closest(".item").dataset.id;
    if (e.target.checked) state.selected.add(id); else state.selected.delete(id);
    syncSelection();
  }
});
$("#playlists").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-role]");
  if (!btn) return;
  const li = btn.closest(".item");
  const id = li.dataset.id;
  if (btn.dataset.role === "download") await downloadOne(id);
  if (btn.dataset.role === "remove") await removePlaylist(id);
});
$("#jobs").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-role]");
  if (!btn) return;
  const li = btn.closest(".item");
  const id = li.dataset.job;
  if (btn.dataset.role === "cancel") { await api(`/api/jobs/${id}/cancel`, { method: "POST" }); }
  if (btn.dataset.role === "remove") { await api(`/api/jobs/${id}`, { method: "DELETE" }); }
  await loadJobs();
});

/* ------------------------------ Boot ------------------------------- */
(async function boot() {
  try {
    await loadConfig();
    await loadAuth();
    await loadCookies();
    await loadPlaylists();
    await loadJobs();
    setInterval(loadJobs, 1500);
    setInterval(loadCookies, 30000);
    setInterval(loadAuth, 30000);
  } catch (e) {
    console.error(e);
  }
})();
