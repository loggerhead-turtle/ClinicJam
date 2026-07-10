/* ClinicJam kiosk — touch-first UI with its own on-screen keyboard,
   meant to run fullscreen in Chromium on the Pi's touchscreen. */
(() => {
  "use strict";

  const $ = (s) => document.querySelector(s);

  // The kiosk is a shared device: one stable identity for everyone at the
  // screen. Votes from the kiosk count once, same as any single phone.
  const deviceId =
    localStorage.getItem("cj_kiosk_device") ||
    (() => {
      const id = "kiosk-" + Array.from(crypto.getRandomValues(new Uint8Array(8)))
        .map((b) => b.toString(16).padStart(2, "0")).join("");
      localStorage.setItem("cj_kiosk_device", id);
      return id;
    })();

  const headers = () => ({
    "Content-Type": "application/json",
    "X-Device-Id": deviceId,
    "X-Device-Name": "the kiosk",
  });

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

  // ---------------- rendering ----------------
  let state = null;
  let progressBase = 0, progressAt = 0;

  function fmtDur(ms) {
    const s = Math.round(ms / 1000);
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
  }

  function trackRow(track, right, byline) {
    const li = document.createElement("li");
    const img = document.createElement("img");
    img.src = track.art_url || "";
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
    $("#k-listeners").textContent = state.listeners ? `${state.listeners} listening` : "";
    $("#k-idle").classList.toggle("hidden", state.active);
    $("#k-mode").textContent =
      state.queue_mode === "vote" ? "top-voted plays next" : "first come, first played";

    const now = state.now_playing;
    $("#k-now").classList.toggle("hidden", !now);
    $("#k-skipvote").classList.toggle("hidden", !now);
    if (now) {
      $("#k-art").src = now.track.art_url || "";
      $("#k-title").textContent = now.track.name;
      $("#k-artist").textContent = now.track.artists;
      $("#k-by").textContent = `added by ${now.added_by}`;
      progressBase = state.progress_ms;
      progressAt = Date.now();
      $("#k-skipvote").classList.toggle("voted", state.you_voted_skip);
      $("#k-skipcount").textContent =
        state.skip_votes ? `(${state.skip_votes}/${state.skip_threshold})` : "";
    }

    const queueEl = $("#k-queue");
    queueEl.replaceChildren();
    $("#k-empty").classList.toggle("hidden", state.queue.length > 0);
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

  setInterval(() => {
    const now = state && state.now_playing;
    if (!now) return;
    let ms = state.paused ? progressBase : progressBase + (Date.now() - progressAt);
    $("#k-progress").style.width =
      Math.min(100, (ms / now.track.duration_ms) * 100) + "%";
  }, 1000);

  // ---------------- search + on-screen keyboard ----------------
  const searchInput = $("#k-search");
  let query = "";
  let searchTimer;

  const KEY_ROWS = [
    ["1","2","3","4","5","6","7","8","9","0"],
    ["q","w","e","r","t","y","u","i","o","p"],
    ["a","s","d","f","g","h","j","k","l","'"],
    ["z","x","c","v","b","n","m","-","&"],
    ["space","⌫"],
  ];

  function buildKeyboard() {
    const kb = $("#k-keyboard");
    kb.replaceChildren();
    for (const row of KEY_ROWS) {
      const rowEl = document.createElement("div");
      rowEl.className = "k-krow";
      for (const key of row) {
        const b = document.createElement("button");
        b.className = "k-key";
        if (key === "space") { b.classList.add("space"); b.textContent = "space"; }
        else if (key === "⌫") { b.classList.add("wide"); b.textContent = "⌫"; }
        else b.textContent = key;
        b.onclick = () => {
          if (key === "⌫") query = query.slice(0, -1);
          else if (key === "space") query += " ";
          else query += key;
          onQueryChanged();
        };
        rowEl.append(b);
      }
      kb.append(rowEl);
    }
  }

  function setKeyboard(open) {
    $("#k-keyboard").classList.toggle("hidden", !open);
    searchInput.classList.toggle("active", open);
    if (!open && !query) showResults(false);
  }

  function showResults(show) {
    $("#k-results").classList.toggle("hidden", !show);
    $("#k-queue-panel").classList.toggle("hidden", show);
    $("#k-clear").classList.toggle("hidden", !show);
  }

  function onQueryChanged() {
    searchInput.value = query;
    clearTimeout(searchTimer);
    if (query.trim().length < 2) {
      $("#k-results").replaceChildren();
      showResults(false);
      return;
    }
    searchTimer = setTimeout(doSearch, 350);
  }

  async function doSearch() {
    try {
      const { tracks } = await api(`/api/search?q=${encodeURIComponent(query.trim())}`);
      const el = $("#k-results");
      el.replaceChildren();
      showResults(true);
      for (const t of tracks) {
        const add = document.createElement("button");
        add.className = "add-btn";
        add.textContent = "+";
        add.onclick = async () => {
          try {
            await api("/api/queue", { method: "POST", body: JSON.stringify({ uri: t.uri }) });
            toast(`Added “${t.name}” 🎉`);
            clearSearch();
          } catch (err) { toast(err.message); }
        };
        el.append(trackRow(t, add));
      }
    } catch (err) { toast(err.message); }
  }

  function clearSearch() {
    query = "";
    searchInput.value = "";
    $("#k-results").replaceChildren();
    showResults(false);
    setKeyboard(false);
  }

  searchInput.addEventListener("click", () => setKeyboard(true));
  $("#k-clear").addEventListener("click", clearSearch);
  buildKeyboard();

  // ---------------- vote to skip ----------------
  $("#k-skipvote").addEventListener("click", async () => {
    try {
      state = await api("/api/skip-vote", { method: "POST" });
      render();
      toast("Skip vote counted");
    } catch (e) { toast(e.message); }
  });

  // ---------------- host controls (PIN unlock) ----------------
  let hostUnlocked = false;
  let pin = "";

  function buildPinPad() {
    const pad = $("#k-pin-pad");
    pad.replaceChildren();
    for (const key of ["1","2","3","4","5","6","7","8","9","⌫","0","OK"]) {
      const b = document.createElement("button");
      b.textContent = key;
      b.onclick = async () => {
        if (key === "⌫") pin = pin.slice(0, -1);
        else if (key === "OK") { await tryUnlock(); return; }
        else if (pin.length < 8) pin += key;
        $("#k-pin-display").textContent = "•".repeat(pin.length);
      };
      pad.append(b);
    }
  }

  async function tryUnlock() {
    try {
      const resp = await fetch("/api/admin/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      if (!resp.ok) throw new Error("Wrong PIN");
      hostUnlocked = true;
      $("#k-host-controls").classList.remove("hidden");
      $("#k-pin").close();
      toast("Host controls unlocked");
    } catch (e) {
      toast(e.message);
    } finally {
      pin = "";
      $("#k-pin-display").textContent = "";
    }
  }

  $("#k-lock").onclick = () => {
    if (hostUnlocked) {
      // toggle visibility so the strip can be re-hidden on a shared screen
      $("#k-host-controls").classList.toggle("hidden");
      return;
    }
    pin = "";
    $("#k-pin-display").textContent = "";
    $("#k-pin").showModal();
  };
  $("#k-pin-cancel").onclick = () => $("#k-pin").close();
  buildPinPad();

  const hostApi = (path) => () =>
    fetch(path, { method: "POST" }).then((r) => {
      if (r.status === 401) { hostUnlocked = false; $("#k-host-controls").classList.add("hidden"); toast("Session expired — unlock again"); }
      else if (!r.ok) toast("Action failed");
    });
  $("#k-pause").onclick = hostApi("/api/admin/playback/pause");
  $("#k-resume").onclick = hostApi("/api/admin/playback/resume");
  $("#k-skip").onclick = hostApi("/api/admin/playback/skip");
  $("#k-stop").onclick = () => {
    if (confirm("Stop the jam for everyone?")) hostApi("/api/admin/jam/stop")();
  };

  // ---------------- live updates ----------------
  function connectWS() {
    const ws = new WebSocket(`ws://${location.host}/ws?device=${deviceId}`);
    const ping = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) ws.send("ping");
    }, 30000);
    ws.onmessage = (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") { state = msg.state; render(); }
    };
    ws.onclose = () => { clearInterval(ping); setTimeout(connectWS, 2500); };
  }

  async function boot() {
    try { state = await api("/api/state"); render(); } catch {}
    connectWS();
  }
  boot();
})();
