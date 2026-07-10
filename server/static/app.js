/* ClinicJam guest app — no build step, runs fine in captive-portal browsers. */
(() => {
  "use strict";

  // ---------- identity ----------
  const deviceId =
    localStorage.getItem("cj_device") ||
    (() => {
      const id = Array.from(crypto.getRandomValues(new Uint8Array(12)))
        .map((b) => b.toString(16).padStart(2, "0"))
        .join("");
      localStorage.setItem("cj_device", id);
      return id;
    })();
  let deviceName = localStorage.getItem("cj_name") || "";

  const $ = (sel) => document.querySelector(sel);
  const headers = () => ({
    "Content-Type": "application/json",
    "X-Device-Id": deviceId,
    "X-Device-Name": deviceName,
  });

  // ---------- toast ----------
  let toastTimer;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove("show"), 2600);
  }

  async function api(path, opts = {}) {
    const resp = await fetch(path, { ...opts, headers: headers() });
    if (!resp.ok) {
      let msg = "Something went wrong";
      try { msg = (await resp.json()).detail || msg; } catch {}
      throw new Error(msg);
    }
    return resp.json();
  }

  // ---------- name prompt ----------
  if (!deviceName) {
    const dlg = $("#name-dialog");
    dlg.showModal();
    dlg.addEventListener("close", () => {
      deviceName = ($("#name-input").value || "").trim().slice(0, 24) || "guest";
      localStorage.setItem("cj_name", deviceName);
    });
  }

  // ---------- rendering ----------
  let state = null;
  let progressBase = 0;   // ms at last state message
  let progressAt = 0;     // Date.now() at last state message

  function fmtDur(ms) {
    const s = Math.round(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function trackRow(track, right, byline) {
    const li = document.createElement("li");
    const img = document.createElement("img");
    img.src = track.art_url || "";
    img.alt = "";
    const meta = document.createElement("div");
    meta.className = "t-meta";
    const name = document.createElement("div");
    name.className = "t-name";
    name.textContent = track.name;
    const artist = document.createElement("div");
    artist.className = "t-artist";
    artist.textContent = `${track.artists} · ${fmtDur(track.duration_ms)}`;
    meta.append(name, artist);
    if (byline) {
      const by = document.createElement("div");
      by.className = "t-by";
      by.textContent = byline;
      meta.append(by);
    }
    li.append(img, meta, right);
    return li;
  }

  function render() {
    if (!state) return;
    $("#listeners").textContent = state.listeners
      ? `${state.listeners} listening` : "";
    $("#offline").classList.toggle("hidden", state.active);
    $("#mode-pill").textContent =
      state.queue_mode === "vote" ? "top-voted plays next" : "first come, first played";

    // now playing
    const now = state.now_playing;
    $("#now").classList.toggle("hidden", !now);
    if (now) {
      $("#now-art").src = now.track.art_url || "";
      $("#now-title").textContent = now.track.name;
      $("#now-artist").textContent = now.track.artists;
      $("#now-by").textContent = `added by ${now.added_by}`;
      progressBase = state.progress_ms;
      progressAt = Date.now();
      const skipBtn = $("#skipvote");
      skipBtn.classList.toggle("voted", state.you_voted_skip);
      $("#skip-count").textContent =
        state.skip_votes ? `(${state.skip_votes}/${state.skip_threshold})` : "";
    }

    // queue
    const queueEl = $("#queue");
    queueEl.replaceChildren();
    $("#queue-empty").classList.toggle("hidden", state.queue.length > 0);
    for (const item of state.queue) {
      const vote = document.createElement("button");
      vote.className = "vote-btn" + (item.voted ? " voted" : "");
      vote.innerHTML = `<span class="n">${item.votes}</span>▲`;
      vote.onclick = async () => {
        try {
          state = await api(`/api/queue/${item.id}/vote`, {
            method: "POST",
            body: JSON.stringify({ up: !item.voted }),
          });
          render();
        } catch (e) { toast(e.message); }
      };
      queueEl.append(trackRow(item.track, vote, `added by ${item.added_by}`));
    }
  }

  // smooth progress bar between updates
  setInterval(() => {
    const now = state && state.now_playing;
    if (!now) return;
    let ms = progressBase + (Date.now() - progressAt);
    if (state.paused) ms = progressBase;
    const pct = Math.min(100, (ms / now.track.duration_ms) * 100);
    $("#progress-bar").style.width = pct + "%";
  }, 1000);

  // ---------- search ----------
  let searchTimer;
  $("#search").addEventListener("input", (e) => {
    clearTimeout(searchTimer);
    const q = e.target.value.trim();
    if (q.length < 2) { $("#results").replaceChildren(); return; }
    searchTimer = setTimeout(async () => {
      try {
        const { tracks } = await api(`/api/search?q=${encodeURIComponent(q)}`);
        const el = $("#results");
        el.replaceChildren();
        for (const t of tracks) {
          const add = document.createElement("button");
          add.className = "add-btn";
          add.textContent = "+";
          add.onclick = async () => {
            try {
              await api("/api/queue", { method: "POST", body: JSON.stringify({ uri: t.uri }) });
              toast(`Added “${t.name}” to the jam 🎉`);
              $("#search").value = "";
              el.replaceChildren();
            } catch (err) { toast(err.message); }
          };
          el.append(trackRow(t, add));
        }
      } catch (err) { toast(err.message); }
    }, 350);
  });

  // ---------- vote to skip ----------
  $("#skipvote").addEventListener("click", async () => {
    try {
      state = await api("/api/skip-vote", { method: "POST" });
      render();
      toast("Skip vote counted");
    } catch (e) { toast(e.message); }
  });

  // ---------- live updates ----------
  function connectWS() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?device=${deviceId}`);
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 30000);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") { state = msg.state; render(); }
    };
    ws.onclose = () => {
      clearInterval(ping);
      setTimeout(connectWS, 2500);
    };
  }

  async function boot() {
    try { state = await api("/api/state"); render(); } catch {}
    connectWS();
  }
  boot();
})();
