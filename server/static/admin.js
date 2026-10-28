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
    toastTimer = setTimeout(() => el.classList.remove("show"), 3200);
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
    $("#logout-btn").classList.add("hidden");
  }
  function showPanel() {
    $("#login").classList.add("hidden");
    $("#panel").classList.remove("hidden");
    $("#logout-btn").classList.remove("hidden");
  }

  $("#login-btn").onclick = async () => {
    try {
      await api("/api/admin/login", {
        method: "POST",
        body: JSON.stringify({ pin: $("#pin").value }),
      });
      $("#pin").value = "";
      showPanel();
      refresh();
    } catch (e) { toast(e.message); }
  };
  $("#pin").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#login-btn").click();
  });
  $("#logout-btn").onclick = async () => {
    try { await api("/api/admin/logout", { method: "POST" }); } catch {}
    showLogin();
  };

  let status = null;

  function badge(el, ok, okText, badText) {
    el.textContent = ok ? okText : badText;
    el.className = ok ? "ok" : "bad";
  }

  function row(label, sub, buttons) {
    const li = document.createElement("li");
    const meta = document.createElement("div");
    meta.className = "t-meta";
    const name = document.createElement("div");
    name.className = "t-name";
    name.textContent = label;
    meta.append(name);
    if (sub) {
      const s = document.createElement("div");
      s.className = "t-artist";
      s.textContent = sub;
      meta.append(s);
    }
    li.append(meta, ...buttons);
    return li;
  }

  function button(text, cls, onclick) {
    const b = document.createElement("button");
    b.textContent = text;
    if (cls) b.className = cls;
    b.onclick = onclick;
    return b;
  }

  async function refresh() {
    try {
      status = await api("/api/admin/status");
    } catch { return; }

    // warnings
    const warns = status.config_warnings || [];
    $("#warnings").classList.toggle("hidden", warns.length === 0);
    const wl = $("#warn-list");
    wl.replaceChildren();
    for (const w of warns) {
      const li = document.createElement("li");
      li.textContent = w;
      wl.append(li);
    }

    badge($("#st-spotify"), status.spotify_connected, "connected", "not connected");
    badge(
      $("#st-player"),
      status.librespot_visible,
      "online",
      status.player_linked ? "linked, not visible yet (give it ~20 s)" : "not linked"
    );
    const cast = status.cast || {};
    badge(
      $("#st-cast"),
      cast.connected && cast.casting_ours,
      `casting to ${cast.name}`,
      cast.connected ? `${cast.name} (idle)` : "no target selected"
    );
    badge(
      $("#st-encoder"),
      status.encoder_running,
      `running (${status.stream_listeners} listener${status.stream_listeners === 1 ? "" : "s"})`,
      "idle (starts when the speakers connect)"
    );
    $("#st-error-row").classList.toggle("hidden", !status.last_error);
    $("#st-error").textContent = status.last_error || "";

    // Spotify sections
    $("#spotify-connect").classList.toggle("hidden", status.spotify_connected);
    $("#player-link").classList.toggle(
      "hidden", !(status.spotify_connected && status.uses_dev_app && !status.player_linked)
    );
    $("#spotify-manage").classList.toggle("hidden", !status.spotify_connected);

    const jt = $("#jam-toggle");
    jt.textContent = status.jam_active ? "⏹ Stop the jam" : "▶ Start the jam";
    jt.classList.toggle("primary", !status.jam_active);
    jt.classList.toggle("danger", status.jam_active);

    // cast devices
    const sel = $("#cast-select");
    sel.replaceChildren(new Option("— choose speakers —", ""));
    for (const d of status.cast_devices) {
      const label = `${d.is_group ? "🔊 group: " : "🔈 "}${d.name} (${d.model})`;
      sel.append(new Option(label, d.uuid, false, d.selected));
    }
    if (typeof cast.volume === "number") {
      $("#volume").value = Math.round(cast.volume * 100);
      $("#volume-val").textContent = Math.round(cast.volume * 100) + "%";
    }

    // network: setup mode + allowlist
    const net = status.network || {};
    const remaining = net.setup_mode_remaining || 0;
    $("#setup-mode-on").classList.toggle("hidden", remaining > 0);
    $("#setup-mode-off").classList.toggle("hidden", remaining === 0);
    $("#setup-mode-status").textContent = !net.helper_available
      ? "(firewall helper not running on this system)"
      : remaining > 0
        ? `ON — ${Math.ceil(remaining / 60)} min left: the jam Wi-Fi has internet right now`
        : "off (walled garden active)";
    const al = $("#allow-list");
    al.replaceChildren();
    const allowed = net.allowed || {};
    for (const mac of Object.keys(allowed).sort()) {
      const info = allowed[mac] || {};
      al.append(row(info.name || "device", mac, [
        button("Remove", "danger", async () => {
          try { await api(`/api/admin/network/allow/${mac}`, { method: "DELETE" }); } catch (e) { toast(e.message); }
          setTimeout(refresh, 800);
        }),
      ]));
    }
    if (!Object.keys(allowed).length) {
      al.append(row("No devices allowlisted yet", "speakers appear here automatically once they join the jam Wi-Fi", []));
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
      ul.append(row(
        item.track.name,
        `${item.track.artists} · ${item.votes}▲ · by ${item.added_by}`,
        [button("Remove", "danger", async () => {
          await api(`/api/admin/queue/${item.id}`, { method: "DELETE" });
          renderModeration();
        })]
      ));
    }
  }

  function renderDevices() {
    const dev = $("#adm-devices");
    dev.replaceChildren();
    for (const d of status.devices) {
      if (status.banned.includes(d.id)) continue;
      dev.append(row(d.name, d.ip || "", [
        button("Block", "danger", async () => {
          try {
            const r = await api("/api/admin/ban", {
              method: "POST",
              body: JSON.stringify({ device_id: d.id }),
            });
            toast(r.mac ? "Blocked (device + hardware address)" : "Blocked");
          } catch (e) { toast(e.message); }
          refresh();
        }),
      ]));
    }
    const banned = $("#adm-banned");
    banned.replaceChildren();
    for (const id of status.banned) {
      banned.append(row(id.slice(0, 12) + "…", "", [
        button("Unblock", "", async () => {
          await api(`/api/admin/ban/${id}`, { method: "DELETE" });
          refresh();
        }),
      ]));
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
      toast("Spotify connected 🎉 — the player is logging in");
      $("#spotify-paste").value = "";
      refresh();
    } catch (e) { toast(e.message); }
  };
  $("#player-auth").onclick = async () => {
    try {
      const { url } = await api("/api/admin/spotify/player/auth-url");
      window.open(url, "_blank");
    } catch (e) { toast(e.message); }
  };
  $("#player-finish").onclick = async () => {
    try {
      await api("/api/admin/spotify/player/callback", {
        method: "POST",
        body: JSON.stringify({ url: $("#player-paste").value }),
      });
      toast("Player linked 🎉");
      $("#player-paste").value = "";
      refresh();
    } catch (e) { toast(e.message); }
  };
  $("#player-restart").onclick = async () => {
    try { await api("/api/admin/player/restart", { method: "POST" }); toast("Restarting the player…"); }
    catch (e) { toast(e.message); }
  };
  $("#spotify-disconnect").onclick = async () => {
    if (!confirm("Disconnect the Spotify account? You'll need to link it again.")) return;
    try { await api("/api/admin/spotify/disconnect", { method: "POST" }); refresh(); }
    catch (e) { toast(e.message); }
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

  $("#setup-mode-on").onclick = async () => {
    try {
      await api("/api/admin/network/setup-mode", { method: "POST", body: JSON.stringify({ minutes: 30 }) });
      toast("Setup mode on for 30 minutes");
      setTimeout(refresh, 1200);
    } catch (e) { toast(e.message); }
  };
  $("#setup-mode-off").onclick = async () => {
    try {
      await api("/api/admin/network/setup-mode", { method: "POST", body: JSON.stringify({ minutes: 0 }) });
      toast("Setup mode off");
      setTimeout(refresh, 1200);
    } catch (e) { toast(e.message); }
  };
  $("#allow-add").onclick = async () => {
    try {
      await api("/api/admin/network/allow", {
        method: "POST",
        body: JSON.stringify({ mac: $("#allow-mac").value.trim(), name: $("#allow-name").value.trim() }),
      });
      $("#allow-mac").value = ""; $("#allow-name").value = "";
      toast("Allowed");
      setTimeout(refresh, 1200);
    } catch (e) { toast(e.message); }
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
    if (!$("#panel").classList.contains("hidden") && document.visibilityState === "visible") refresh();
  }, 8000);
})();
