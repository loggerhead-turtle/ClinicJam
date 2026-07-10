/* ClinicJam host panel. */
(() => {
  "use strict";
  const $ = (s) => document.querySelector(s);

  let toastTimer;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  async function api(path, opts = {}) {
    const resp = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
    if (resp.status === 401) { showLogin(); throw new Error("Login required"); }
    if (!resp.ok) {
      let msg = "Request failed";
      try { msg = (await resp.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    return resp.json();
  }

  function showLogin() {
    $("#login").classList.remove("hidden");
    $("#panel").classList.add("hidden");
  }
  function showPanel() {
    $("#login").classList.add("hidden");
    $("#panel").classList.remove("hidden");
  }

  $("#login-btn").onclick = async () => {
    try {
      await api("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ pin: $("#pin").value }),
      });
      showPanel();
      refresh();
    } catch (e) { toast(e.message); }
  };
  $("#pin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#login-btn").click();
  });

  let status = null;

  function badge(el, ok, okText, badText) {
    el.textContent = ok ? okText : badText;
    el.className = ok ? "ok" : "bad";
  }

  async function refresh() {
    try {
      status = await api("/api/admin/status");
    } catch { return; }
    badge($("#st-spotify"), status.spotify_connected, "connected", "not connected");
    badge($("#st-librespot"), status.librespot_visible, "online", "not visible");
    const cast = status.cast || {};
    badge(
      $("#st-cast"),
      cast.connected && cast.casting_ours,
      `casting to ${cast.name}`,
      cast.connected ? `${cast.name} (idle)` : "no target selected"
    );
    $("#st-error-row").classList.toggle("hidden", !status.last_error);
    $("#st-error").textContent = status.last_error || "";
    $("#spotify-connect").classList.toggle("hidden", status.spotify_connected);

    const jt = $("#jam-toggle");
    jt.textContent = status.jam_active ? "⏹ Stop the jam" : "▶ Start the jam";
    jt.classList.toggle("primary", !status.jam_active);
    jt.classList.toggle("danger", status.jam_active);

    // cast devices
    const sel = $("#cast-select");
    sel.replaceChildren(new Option("— choose speakers —", ""));
    for (const d of status.cast_devices) {
      const label = `${d.is_group ? "🔊 group: " : "🔈 "}${d.name} (${d.model})`;
      const opt = new Option(label, d.uuid, false, d.selected);
      sel.append(opt);
    }
    if (typeof cast.volume === "number") {
      $("#volume").value = Math.round(cast.volume * 100);
      $("#volume-val").textContent = Math.round(cast.volume * 100) + "%";
    }

    // settings
    const s = status.settings;
    $("#set-mode").value = s.queue_mode;
    $("#set-explicit").value = String(s.explicit_filter);
    $("#set-skip").value = String(s.skip_ratio);
    $("#set-limit").value = s.per_user_limit;
    $("#set-fallback").value = s.fallback_playlist || "";

    renderModeration();
    renderDevices();
  }

  async function renderModeration() {
    let state;
    try { state = await api("/api/state"); } catch { return; }
    const ul = $("#adm-queue");
    ul.replaceChildren();
    $("#adm-queue-empty").classList.toggle("hidden", state.queue.length > 0);
    for (const item of state.queue) {
      const li = document.createElement("li");
      const meta = document.createElement("div");
      meta.className = "t-meta";
      meta.innerHTML = `<div class="t-name"></div><div class="t-artist"></div>`;
      meta.querySelector(".t-name").textContent = item.track.name;
      meta.querySelector(".t-artist").textContent =
        `${item.track.artists} · ${item.votes}▲ · by ${item.added_by}`;
      const del = document.createElement("button");
      del.className = "danger";
      del.textContent = "Remove";
      del.onclick = async () => {
        await api(`/api/admin/queue/${item.id}`, { method: "DELETE" });
        renderModeration();
      };
      li.append(meta, del);
      ul.append(li);
    }
  }

  function renderDevices() {
    const dev = $("#adm-devices");
    dev.replaceChildren();
    for (const d of status.devices) {
      if (status.banned.includes(d.id)) continue;
      const li = document.createElement("li");
      const meta = document.createElement("div");
      meta.className = "t-meta";
      meta.innerHTML = `<div class="t-name"></div>`;
      meta.querySelector(".t-name").textContent = d.name;
      const ban = document.createElement("button");
      ban.className = "danger";
      ban.textContent = "Block";
      ban.onclick = async () => {
        await api("/api/admin/ban", {
          method: "POST",
          body: JSON.stringify({ device_id: d.id }),
        });
        refresh();
      };
      li.append(meta, ban);
      dev.append(li);
    }
    const banned = $("#adm-banned");
    banned.replaceChildren();
    for (const id of status.banned) {
      const li = document.createElement("li");
      const meta = document.createElement("div");
      meta.className = "t-meta";
      meta.innerHTML = `<div class="t-name" style="color:var(--muted)"></div>`;
      meta.querySelector(".t-name").textContent = id.slice(0, 12) + "…";
      const unban = document.createElement("button");
      unban.textContent = "Unblock";
      unban.onclick = async () => {
        await api(`/api/admin/ban/${id}`, { method: "DELETE" });
        refresh();
      };
      li.append(meta, unban);
      banned.append(li);
    }
  }

  // ---------- controls ----------
  $("#jam-toggle").onclick = async () => {
    const path = status && status.jam_active ? "/api/admin/jam/stop" : "/api/admin/jam/start";
    try { await api(path, { method: "POST" }); refresh(); } catch (e) { toast(e.message); }
  };
  $("#pb-skip").onclick = () => api("/api/admin/playback/skip", { method: "POST" }).catch((e) => toast(e.message));
  $("#pb-pause").onclick = () => api("/api/admin/playback/pause", { method: "POST" }).catch((e) => toast(e.message));
  $("#pb-resume").onclick = () => api("/api/admin/playback/resume", { method: "POST" }).catch((e) => toast(e.message));

  $("#spotify-auth").onclick = async () => {
    try {
      const { url } = await api("/api/admin/spotify/auth-url");
      window.open(url, "_blank");
    } catch (e) { toast(e.message); }
  };
  $("#spotify-finish").onclick = async () => {
    try {
      await api("/api/admin/spotify/callback", {
        method: "POST",
        body: JSON.stringify({ url: $("#spotify-paste").value }),
      });
      toast("Spotify connected 🎉");
      $("#spotify-paste").value = "";
      refresh();
    } catch (e) { toast(e.message); }
  };

  $("#cast-select").onchange = async (e) => {
    if (!e.target.value) return;
    try {
      await api("/api/admin/cast/target", {
        method: "POST",
        body: JSON.stringify({ uuid: e.target.value }),
      });
      toast("Speakers selected");
      refresh();
    } catch (err) { toast(err.message); }
  };
  $("#cast-refresh").onclick = refresh;

  let volTimer;
  $("#volume").oninput = (e) => {
    $("#volume-val").textContent = e.target.value + "%";
    clearTimeout(volTimer);
    volTimer = setTimeout(() => {
      api("/api/admin/cast/volume", {
        method: "POST",
        body: JSON.stringify({ level: e.target.value / 100 }),
      }).catch((err) => toast(err.message));
    }, 300);
  };

  $("#set-save").onclick = async () => {
    try {
      await api("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify({
          queue_mode: $("#set-mode").value,
          explicit_filter: $("#set-explicit").value === "true",
          skip_ratio: parseFloat($("#set-skip").value),
          per_user_limit: parseInt($("#set-limit").value || "0", 10),
          fallback_playlist: $("#set-fallback").value.trim(),
        }),
      });
      toast("Rules saved");
    } catch (e) { toast(e.message); }
  };

  // boot: try an authed call; if the cookie is still valid we skip login
  api("/api/admin/status")
    .then(() => { showPanel(); refresh(); })
    .catch(() => showLogin());

  setInterval(() => {
    if (!$("#panel").classList.contains("hidden")) refresh();
  }, 8000);
})();
