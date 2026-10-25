(() => {
  const $ = (sel) => document.querySelector(sel);
  const STORE_KEY = "leadRadar.v1";
  const POLL_MS = 60000;
  const TABS = [
    ["inbox", "Inbox"],
    ["saved", "Saved"],
    ["contacted", "Contacted"],
    ["ignored", "Ignored"],
  ];

  // ---- per-browser state (statuses, last-seen marker, sound) ----
  const state = { status: {}, lastSeen: 0, sound: true };
  try { Object.assign(state, JSON.parse(localStorage.getItem(STORE_KEY) || "{}")); } catch (e) { /* private mode */ }
  const save = () => { try { localStorage.setItem(STORE_KEY, JSON.stringify(state)); } catch (e) { /* ignore */ } };

  let leads = [];
  let status = null;
  let known = null; // ids loaded so far; null until the first load
  let tab = "inbox";
  const filters = { q: "", source: "", min: 0, sort: "found" };
  const baseTitle = document.title;

  // ---- helpers ----
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }
  const ts = (iso) => Date.parse(iso) || 0;
  function ago(iso) {
    const s = Math.max(0, (Date.now() - ts(iso)) / 1000);
    if (s < 90) return "just now";
    if (s < 3600) return Math.round(s / 60) + "m ago";
    if (s < 86400) return Math.round(s / 3600) + "h ago";
    return Math.round(s / 86400) + "d ago";
  }
  const safeUrl = (u) => (/^https?:\/\//i.test(u) ? u : "#");
  const statusOf = (l) => state.status[l.id] || "inbox";
  const isUnseen = (l) => statusOf(l) === "inbox" && ts(l.found) > state.lastSeen;
  const scoreClass = (n) => (n >= 75 ? "high" : n >= 55 ? "mid" : "low");

  // ---- loading ----
  async function fetchJson(name) {
    const res = await fetch(name + "?t=" + Date.now(), { cache: "no-store" });
    if (!res.ok) throw new Error(name + " " + res.status);
    return res.json();
  }

  async function load() {
    try {
      const [data, st] = await Promise.all([fetchJson("leads.json"), fetchJson("status.json").catch(() => null)]);
      const fresh = known ? data.leads.filter((l) => !known.has(l.id)) : [];
      leads = data.leads;
      status = st;
      known = new Set(leads.map((l) => l.id));
      if (fresh.length) announce(fresh);
      render();
    } catch (err) {
      $("#meta").textContent = "Could not load data (" + err.message + ")";
      $("#pulse").className = "pulse bad";
    }
  }

  // ---- notifications ----
  function beep() {
    if (!state.sound) return;
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain); gain.connect(ctx.destination);
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.15, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.4);
      osc.start(); osc.stop(ctx.currentTime + 0.4);
    } catch (e) { /* autoplay blocked until the user interacts */ }
  }

  function toast(title, sub) {
    const t = el("div", "toast", title);
    if (sub) t.appendChild(el("small", "", sub));
    t.addEventListener("click", () => { t.remove(); openPanel(true); });
    $("#toasts").appendChild(t);
    setTimeout(() => t.remove(), 8000);
  }

  function announce(fresh) {
    const best = fresh.slice().sort((a, b) => b.score - a.score)[0];
    const head = fresh.length === 1 ? "1 new lead" : fresh.length + " new leads";
    toast(head, best.title);
    beep();
    if ("Notification" in window && Notification.permission === "granted") {
      const n = new Notification("Lead Radar: " + head, { body: best.title });
      n.onclick = () => { window.focus(); n.close(); };
    }
  }

  function openPanel(open) {
    const panel = $("#panel");
    panel.hidden = !open;
    $("#bell").setAttribute("aria-expanded", String(open));
    if (open) renderPanel();
  }

  function renderPanel() {
    const list = $("#notifList");
    list.replaceChildren();
    const unseen = leads.filter(isUnseen).sort((a, b) => ts(b.found) - ts(a.found));
    if (!unseen.length) {
      list.appendChild(el("li", "notif-empty", "You're all caught up."));
    }
    unseen.slice(0, 12).forEach((l) => {
      const li = el("li");
      li.appendChild(el("span", "n-title", l.title));
      li.appendChild(el("span", "n-sub", `Score ${l.score} · ${l.source} · ${ago(l.found)}`));
      li.addEventListener("click", () => {
        openPanel(false);
        const card = document.getElementById("lead-" + l.id);
        if (card) card.scrollIntoView({ behavior: "smooth", block: "center" });
      });
      list.appendChild(li);
    });
    $("#soundOpt").checked = state.sound;
    $("#desktopOpt").hidden = !("Notification" in window) || Notification.permission === "granted";

    const health = $("#health");
    health.replaceChildren();
    const sources = (status && status.sources) || {};
    Object.keys(sources).forEach((name) => {
      const s = sources[name];
      const li = el("li", s.ok ? "" : "bad", s.ok ? `${name}: OK (${s.count} items)` : `${name}: ${s.error || "failed"}`);
      health.appendChild(li);
    });
    if (status && status.ai === false) {
      health.appendChild(el("li", "", "AI scoring off (no GEMINI_API_KEY): using keyword scoring only"));
    }
  }

  // ---- rendering ----
  function visible() {
    const q = filters.q.trim().toLowerCase();
    const out = leads.filter((l) => {
      if (statusOf(l) !== tab) return false;
      if (filters.source && l.source !== filters.source) return false;
      if (l.score < filters.min) return false;
      if (q && !`${l.title} ${l.snippet} ${l.author}`.toLowerCase().includes(q)) return false;
      return true;
    });
    out.sort(filters.sort === "score" ? (a, b) => b.score - a.score : (a, b) => ts(b.found) - ts(a.found));
    return out;
  }

  function setStatus(id, value) {
    if (value === "inbox") delete state.status[id]; else state.status[id] = value;
    save();
    render();
  }

  function card(l) {
    const c = el("article", "card" + (isUnseen(l) ? " is-new" : ""));
    c.id = "lead-" + l.id;
    c.appendChild(el("div", "score " + scoreClass(l.score), String(l.score)));
    const body = el("div");
    body.appendChild(el("h3", "", l.title));

    const row = el("div", "row");
    if (isUnseen(l)) row.appendChild(el("span", "chip new", "NEW"));
    row.appendChild(el("span", "chip", l.source));
    if (l.author) row.appendChild(el("span", "", l.author));
    row.appendChild(el("span", "", "posted " + ago(l.posted)));
    (l.keywords || []).slice(0, 4).forEach((k) => row.appendChild(el("span", "chip", k)));
    body.appendChild(row);

    if (l.snippet) body.appendChild(el("p", "snippet", l.snippet));
    if (l.ai_reason) body.appendChild(el("p", "reason", "AI: " + l.ai_reason));

    const actions = el("div", "actions");
    const open = el("a", "btn primary", "Open post");
    open.href = safeUrl(l.url); open.target = "_blank"; open.rel = "noopener noreferrer";
    open.addEventListener("click", () => { if (statusOf(l) === "inbox") { /* viewing counts as seen */ markOneSeen(l); } });
    actions.appendChild(open);
    const add = (label, value) => {
      const b = el("button", "btn", label);
      b.addEventListener("click", () => setStatus(l.id, value));
      actions.appendChild(b);
    };
    if (tab === "inbox") { add("Save", "saved"); add("Contacted", "contacted"); add("Ignore", "ignored"); }
    else { add("Back to inbox", "inbox"); if (tab !== "contacted") add("Contacted", "contacted"); }
    body.appendChild(actions);
    c.appendChild(body);
    return c;
  }

  function markOneSeen(l) {
    state.lastSeen = Math.max(state.lastSeen, ts(l.found));
    save();
    setTimeout(render, 0);
  }

  function renderTabs() {
    const nav = $("#tabs");
    nav.replaceChildren();
    TABS.forEach(([key, label]) => {
      const b = el("button", "tab", label);
      b.setAttribute("role", "tab");
      b.setAttribute("aria-selected", String(key === tab));
      const n = leads.filter((l) => statusOf(l) === key).length;
      b.appendChild(el("span", "count", String(n)));
      b.addEventListener("click", () => { tab = key; render(); });
      nav.appendChild(b);
    });
  }

  function renderSources() {
    const select = $("#source");
    const names = [...new Set(leads.map((l) => l.source))].sort();
    if (select.options.length - 1 === names.length) return;
    const current = select.value;
    select.replaceChildren(new Option("All sources", ""));
    names.forEach((n) => select.appendChild(new Option(n, n)));
    select.value = names.includes(current) ? current : "";
  }

  function renderMeta() {
    const meta = $("#meta");
    const pulse = $("#pulse");
    if (!status) { meta.textContent = leads.length + " leads"; pulse.className = "pulse"; return; }
    const entries = Object.values(status.sources || {});
    const okCount = entries.filter((s) => s.ok).length;
    const stale = Date.now() - ts(status.updated) > 60 * 60 * 1000;
    meta.textContent = `Last scan ${ago(status.updated)} · ${okCount}/${entries.length} sources OK`;
    pulse.className = "pulse " + (stale || okCount === 0 ? "bad" : "ok");
  }

  function render() {
    renderMeta();
    renderSources();
    renderTabs();
    const unseen = leads.filter(isUnseen).length;
    const badge = $("#badge");
    badge.hidden = unseen === 0;
    badge.textContent = unseen > 99 ? "99+" : String(unseen);
    document.title = unseen ? `(${unseen}) ${baseTitle}` : baseTitle;
    if (!$("#panel").hidden) renderPanel();

    const list = $("#list");
    const items = visible();
    list.replaceChildren(...items.map(card));
    const empty = $("#empty");
    empty.hidden = items.length > 0;
    empty.textContent = leads.length
      ? "Nothing here with these filters."
      : "No leads yet. The first scan will fill this in.";
  }

  // ---- events ----
  $("#bell").addEventListener("click", () => openPanel($("#panel").hidden));
  $("#markSeen").addEventListener("click", () => {
    state.lastSeen = Math.max(state.lastSeen, ...leads.map((l) => ts(l.found)));
    save(); render();
  });
  $("#soundOpt").addEventListener("change", (e) => { state.sound = e.target.checked; save(); if (state.sound) beep(); });
  $("#desktopOpt").addEventListener("click", async () => {
    if ("Notification" in window) { await Notification.requestPermission(); renderPanel(); }
  });
  $("#q").addEventListener("input", (e) => { filters.q = e.target.value; render(); });
  $("#source").addEventListener("change", (e) => { filters.source = e.target.value; render(); });
  $("#sort").addEventListener("change", (e) => { filters.sort = e.target.value; render(); });
  $("#min").addEventListener("input", (e) => {
    filters.min = Number(e.target.value);
    $("#minOut").textContent = e.target.value;
    render();
  });
  document.addEventListener("click", (e) => {
    if (!$("#panel").hidden && !e.target.closest("#panel, #bell, .toast")) openPanel(false);
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); });

  load();
  setInterval(load, POLL_MS);
})();
