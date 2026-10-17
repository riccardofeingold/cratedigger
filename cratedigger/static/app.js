// Crate Digger front end. Plain ES module, no build step.

const TOKEN = document.querySelector('meta[name="crate-token"]').content;
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const SOURCE_NAMES = { youtube: "YouTube", jamendo: "Jamendo", archive: "Internet Archive", local: "your files" };
const BROWSER_NAMES = { brave: "Brave", chrome: "Chrome", chromium: "Chromium", edge: "Edge", firefox: "Firefox",
  opera: "Opera", safari: "Safari", vivaldi: "Vivaldi", whale: "Whale" };
const ACTIVE = new Set(["queued", "downloading", "converting", "tagging"]);
const STARTERS = ["deep house", "techno", "lo-fi hip hop", "drum and bass", "disco", "ambient"];
const CAMELOT_HUES = [190, 160, 130, 100, 70, 42, 14, 345, 318, 285, 250, 220];

const ICONS = {
  play: '<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>',
  pause: '<path d="M7 5h3.6v14H7zM13.4 5H17v14h-3.6z" fill="currentColor"/>',
  stop: '<path d="M7 7h10v10H7z" fill="currentColor"/>',
  download: '<path d="M12 4v10.5m0 0-4.2-4.2m4.2 4.2 4.2-4.2M5 19.5h14" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>',
  check: '<path d="m5 12.5 4.5 4.5L19 7.5" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/>',
  external: '<path d="M14 5h5v5M19 5l-8 8M10 6H6a1 1 0 0 0-1 1v11a1 1 0 0 0 1 1h11a1 1 0 0 0 1-1v-4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
  more: '<circle cx="5.5" cy="12" r="1.8" fill="currentColor"/><circle cx="12" cy="12" r="1.8" fill="currentColor"/><circle cx="18.5" cy="12" r="1.8" fill="currentColor"/>',
  close: '<path d="M7 7l10 10M17 7 7 17" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>',
};
const icon = (name) => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${ICONS[name]}</svg>`;

// ------------------------------------------------------------------ helpers

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}
const safeUrl = (url) => (/^https?:\/\//i.test(url || "") ? url : "");
function fmtTime(seconds) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "–";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
const fmtClock = (seconds) => (Number.isFinite(seconds) && seconds > 0 ? fmtTime(seconds) : "0:00");
const fmtBytes = (n) => (n >= 1e9 ? `${(n / 1e9).toFixed(1)} GB` : `${(n / 1e6).toFixed(1)} MB`);
function fmtDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

const store = {
  get(key, fallback) {
    try {
      const raw = localStorage.getItem(`crate:${key}`);
      return raw === null ? fallback : JSON.parse(raw);
    } catch { return fallback; }
  },
  set(key, value) {
    try { localStorage.setItem(`crate:${key}`, JSON.stringify(value)); } catch { /* storage unavailable */ }
  },
};

class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

let expiredShown = false;
async function api(path, { method = "GET", body } = {}) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: { "X-Crate-Token": TOKEN, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError("Crate Digger isn't running. Start it again, then reload this page.", 0);
  }
  let data = null;
  try { data = await res.json(); } catch { /* not JSON */ }
  if (!res.ok) {
    if (res.status === 403 && !expiredShown) {
      expiredShown = true;
      toast("This page belongs to an earlier session.", { kind: "error", timeout: 0, action: { label: "Reload", run: () => location.reload() } });
    }
    throw new ApiError(data?.error || `Something went wrong (${res.status}).`, res.status);
  }
  return data;
}

function toast(message, { kind = "info", timeout = 4200, action } = {}) {
  const el = document.createElement("div");
  el.className = `toast ${kind}`;
  const text = document.createElement("span");
  text.textContent = message;
  el.append(text);
  if (action) {
    const b = document.createElement("button");
    b.type = "button";
    b.className = "link-button";
    b.textContent = action.label;
    b.addEventListener("click", () => { el.remove(); action.run(); });
    el.append(b);
  }
  $("#toasts").append(el);
  const all = $$("#toasts .toast");
  if (all.length > 4) all[0].remove();
  if (timeout) setTimeout(() => el.remove(), timeout);
}

// -------------------------------------------------------------------- state

const state = {
  status: null,
  source: store.get("source", "all"),
  ccOnly: store.get("ccOnly", false),
  query: "",
  sections: {}, // source id -> { status: loading|done|error|off, tracks, error }
  skipped: [], // sources left out of an "Everywhere" search
  jobs: [],
  jobsByUid: new Map(),
  deletedUids: new Set(),
  library: [],
  librarySig: "",
  crateUids: new Set(),
  sort: store.get("sort", { key: "added_at", dir: -1 }),
  filter: "",
  anchorId: null,
  now: null, // what's on the deck
  downloadsOpen: false,
};

// -------------------------------------------------------------------- views

function showView(view) {
  for (const tab of $$(".tab")) tab.setAttribute("aria-selected", String(tab.dataset.view === view));
  $("#view-dig").hidden = view !== "dig";
  $("#view-crate").hidden = view !== "crate";
  history.replaceState(null, "", view === "crate" ? "#crate" : location.pathname);
  if (view === "crate") renderCrate();
}

// ------------------------------------------------------------ status/source

async function loadStatus() {
  try {
    state.status = await api("/api/status");
  } catch (e) {
    toast(e.message, { kind: "error" });
    return;
  }
  renderSourcePicker();
}

const sourceInfo = (id) => state.status?.sources.find((s) => s.id === id);

function renderSourcePicker() {
  for (const btn of $$("#source-picker button")) {
    const id = btn.dataset.source;
    const checked = id === state.source;
    btn.setAttribute("aria-checked", String(checked));
    btn.tabIndex = checked ? 0 : -1;
    const info = id === "all" ? null : sourceInfo(id);
    btn.classList.toggle("is-off", !!info && !info.available);
    btn.title = info && !info.available ? info.message : "";
  }
  $("#cc-only").checked = state.ccOnly;
  $("#cc-only").closest("label").hidden = !(state.source === "all" || state.source === "youtube");
}

function selectSource(id) {
  state.source = id;
  store.set("source", id);
  renderSourcePicker();
  if (state.query) runSearch(state.query);
}

// ------------------------------------------------------------------- search

let searchSeq = 0;

async function runSearch(rawQuery) {
  const query = rawQuery.trim();
  if (!query) {
    $("#search-input").focus();
    return;
  }
  state.query = query;
  const seq = ++searchSeq;
  const known = state.status?.sources ?? [{ id: "youtube", available: true }, { id: "jamendo", available: true }, { id: "archive", available: true }];
  let ids;
  state.skipped = [];
  if (state.source === "all") {
    ids = known.filter((s) => s.available).map((s) => s.id);
    state.skipped = known.filter((s) => !s.available);
  } else {
    ids = [state.source];
  }
  state.sections = Object.fromEntries(ids.map((id) => [id, { status: "loading", tracks: [] }]));
  renderResults();

  const cc = state.ccOnly ? "1" : "0";
  await Promise.all(ids.map(async (id) => {
    let section;
    try {
      const data = await api(`/api/search?source=${id}&q=${encodeURIComponent(query)}&cc=${cc}`);
      section = data.error
        ? { status: data.unavailable ? "off" : "error", tracks: [], error: data.error }
        : { status: "done", tracks: data.tracks };
    } catch (e) {
      section = { status: "error", tracks: [], error: e.message };
    }
    if (seq !== searchSeq) return;
    state.sections[id] = section;
    renderSection(id);
  }));
}

function digEmptyHTML() {
  return `<div class="dig-empty">
    <h2>What are you digging for?</h2>
    <p>Search YouTube, Jamendo and the Internet Archive's netlabels at once. Preview anything, then download it into your crate with BPM, key and cover art already tagged.</p>
    <div class="starter">${STARTERS.map((s) => `<button type="button" data-starter="${esc(s)}">${esc(s)}</button>`).join("")}</div>
  </div>`;
}

function renderResults() {
  const root = $("#results");
  if (!state.query) {
    root.innerHTML = digEmptyHTML();
    return;
  }
  const notes = state.skipped.map((s) => `<p class="notice">${esc(s.message)}${s.id === "jamendo"
    ? ' <button class="link-button" type="button" data-action="settings">Open settings</button>' : ""}</p>`).join("");
  root.innerHTML = notes + Object.keys(state.sections)
    .map((id) => `<section class="divider-section" data-source="${id}" aria-label="${esc(SOURCE_NAMES[id])} results"></section>`)
    .join("");
  for (const id of Object.keys(state.sections)) renderSection(id);
}

function skeletonRows(n) {
  return Array.from({ length: n }, () =>
    '<li class="result is-skeleton" aria-hidden="true"><span></span><div class="art"></div><div class="meta"><p></p><p></p></div></li>').join("");
}

function renderSection(id) {
  const el = $(`.divider-section[data-source="${id}"]`);
  if (!el) return;
  const s = state.sections[id];
  const name = SOURCE_NAMES[id] || id;
  let count = "";
  let body;
  if (s.status === "loading") {
    count = '<span class="divider-count is-loading">Searching</span>';
    body = `<ol class="result-list">${skeletonRows(3)}</ol>`;
  } else if (s.status === "error" || s.status === "off") {
    const fix = /settings/i.test(s.error || "") ? ' <button class="button" type="button" data-action="settings">Open settings</button>' : "";
    body = `<p class="section-message${s.status === "error" ? " is-error" : ""}">${esc(s.error)}${fix}</p>`;
  } else if (!s.tracks.length) {
    body = `<p class="section-message">Nothing on ${esc(name)} for “${esc(state.query)}”. Try fewer or different words.</p>`;
  } else {
    count = `<span class="divider-count">${plural(s.tracks.length, "track")}</span>`;
    body = `<ol class="result-list">${s.tracks.map(resultRowHTML).join("")}</ol>`;
  }
  el.innerHTML = `<header class="divider"><h2 class="divider-tab">${esc(name)}</h2>${count}</header>${body}`;
}

function findResult(uid) {
  for (const s of Object.values(state.sections)) {
    const t = s.tracks?.find((x) => x.uid === uid);
    if (t) return t;
  }
  return null;
}

function resultRowHTML(t) {
  const on = state.now?.key === t.uid;
  const art = safeUrl(t.image_url);
  const page = safeUrl(t.page_url);
  const where = SOURCE_NAMES[t.source] || "the web";
  const facts = (t.genres?.length ? `<span class="genre">${esc(t.genres.join(", "))}</span>` : "")
    + (t.lossless_available ? '<span class="badge" title="A lossless FLAC file is available">FLAC</span>' : "");
  return `<li class="result${on ? " is-playing" : ""}" data-uid="${esc(t.uid)}">
    <button class="cue" type="button" data-action="preview" aria-label="Preview ${esc(t.title)}">${icon(on && isPlaying() ? "pause" : "play")}</button>
    <div class="art">${art ? `<img src="${esc(art)}" alt="" loading="lazy" referrerpolicy="no-referrer">` : ""}</div>
    <div class="meta">
      <p class="title" title="${esc(t.title)}">${esc(t.title)}</p>
      <p class="sub"><span>${esc(t.artist)}</span>${t.album && t.album !== t.title ? `<span class="album">${esc(t.album)}</span>` : ""}</p>
    </div>
    <div class="facts">${facts}</div>
    <span class="len num">${fmtTime(t.duration)}</span>
    <span class="license" title="${esc(t.license)}">${esc(t.license)}</span>
    ${page ? `<a class="icon-button small" href="${esc(page)}" target="_blank" rel="noopener noreferrer" title="Open on ${esc(where)}" aria-label="Open ${esc(t.title)} on ${esc(where)}">${icon("external")}</a>` : "<span></span>"}
    ${grabHTML(t)}
  </li>`;
}

// ---------------------------------------------------------------- downloads

function grabInfo(t) {
  const job = state.jobsByUid.get(t.uid);
  if (job && ACTIVE.has(job.state)) {
    const pct = Math.round((job.progress || 0) * 100);
    const label = { queued: "Queued", converting: "Converting", tagging: "Saving" }[job.state] || (job.bytes_total ? `${pct}%` : "Downloading");
    return { state: "busy", label, pct };
  }
  if (state.crateUids.has(t.uid) || (job?.state === "done" && !state.deletedUids.has(t.uid))) return { state: "done", label: "In crate" };
  if (job?.state === "error") return { state: "error", label: "Retry", title: job.message };
  return { state: "idle", label: "Download" };
}

function grabHTML(t) {
  const g = grabInfo(t);
  const attrs = [
    'class="grab"', 'type="button"', 'data-action="grab"', `data-state="${g.state}"`,
    `data-sig="${esc(`${g.state}|${g.label}`)}"`, `style="--p:${g.pct ?? 0}%"`,
    `aria-label="${esc(`${g.label}: ${t.title}`)}"`,
    g.state === "busy" || g.state === "done" ? 'aria-disabled="true"' : "",
    g.title ? `title="${esc(g.title)}"` : "",
  ].filter(Boolean).join(" ");
  return `<button ${attrs}>${grabInner(g)}</button>`;
}

function grabInner(g) {
  const glyph = g.state === "done" ? icon("check") : g.state === "busy" ? "" : icon("download");
  return `${glyph}<span>${esc(g.label)}</span>`;
}

function applyGrab(btn, t) {
  const g = grabInfo(t);
  const signature = `${g.state}|${g.label}`;
  btn.style.setProperty("--p", `${g.pct ?? 0}%`);
  if (btn.dataset.sig === signature) return;
  btn.dataset.sig = signature;
  btn.dataset.state = g.state;
  if (g.state === "busy" || g.state === "done") btn.setAttribute("aria-disabled", "true");
  else btn.removeAttribute("aria-disabled");
  if (g.title) btn.title = g.title; else btn.removeAttribute("title");
  btn.setAttribute("aria-label", `${g.label}: ${t.title}`);
  btn.innerHTML = grabInner(g);
}

function refreshGrabButtons() {
  for (const li of $$(".result[data-uid]")) {
    const t = findResult(li.dataset.uid);
    const btn = li.querySelector(".grab");
    if (t && btn) applyGrab(btn, t);
  }
}

async function grab(t) {
  const g = grabInfo(t);
  if (g.state === "busy" || g.state === "done") return;
  state.deletedUids.delete(t.uid);
  try {
    const job = await api("/api/downloads", { method: "POST", body: { uid: t.uid } });
    state.jobs = [...state.jobs.filter((j) => j.id !== job.id), job];
    state.jobsByUid.set(job.uid, job);
    state.downloadsOpen = true;
    refreshGrabButtons();
    renderDownloads();
    pollJobs();
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}

let jobsTimer = 0;
async function pollJobs() {
  clearTimeout(jobsTimer);
  let data;
  try {
    data = await api("/api/downloads");
  } catch {
    jobsTimer = setTimeout(pollJobs, 3000);
    return;
  }
  applyJobs(data.jobs);
  if (state.jobs.some((j) => ACTIVE.has(j.state))) jobsTimer = setTimeout(pollJobs, 700);
}

function applyJobs(jobs) {
  const before = new Map(state.jobs.map((j) => [j.id, j.state]));
  state.jobs = jobs;
  state.jobsByUid = new Map(jobs.map((j) => [j.uid, j]));
  let landed = 0;
  for (const j of jobs) {
    const was = before.get(j.id);
    if (!was || was === j.state) continue;
    if (j.state === "done") landed += 1;
    if (j.state === "error") toast(`${j.title}: ${j.message}`, { kind: "error", timeout: 8000 });
  }
  if (landed) refreshLibrary();
  refreshGrabButtons();
  renderDownloads();
}

function jobStatus(j) {
  const pct = Math.round((j.progress || 0) * 100);
  switch (j.state) {
    case "queued": return "Waiting for a free slot";
    case "downloading":
      if (j.bytes_total) return `Downloading, ${pct}% of ${fmtBytes(j.bytes_total)}`;
      return j.bytes_done ? `Downloading, ${fmtBytes(j.bytes_done)} so far` : "Starting the download";
    case "converting": return "Converting to MP3";
    case "tagging": return "Tagging and filing";
    case "done": return j.message || "Saved to your crate";
    case "cancelled": return "Cancelled";
    default: return j.message || "The download failed";
  }
}

function renderDownloads() {
  const jobs = [...state.jobs].reverse();
  const active = jobs.filter((j) => ACTIVE.has(j.state)).length;
  const pill = $("#downloads-toggle");
  pill.hidden = jobs.length === 0;
  pill.classList.toggle("is-busy", active > 0);
  $("#downloads-label").textContent = active ? `Downloading ${active}` : "Downloads";
  pill.setAttribute("aria-expanded", String(state.downloadsOpen && jobs.length > 0));
  $("#downloads").hidden = !state.downloadsOpen || jobs.length === 0;

  const list = $("#download-list");
  const existing = new Map($$("li[data-id]", list).map((li) => [li.dataset.id, li]));
  jobs.forEach((j, index) => {
    let li = existing.get(j.id);
    if (!li) {
      li = document.createElement("li");
      li.className = "job";
      li.dataset.id = j.id;
      li.innerHTML = `<div><p class="job-title"></p><p class="job-status"></p></div><span class="job-action"></span><div class="job-bar"><span></span></div>`;
      li.querySelector(".job-title").textContent = j.title;
    }
    existing.delete(j.id);
    li.dataset.state = j.state;
    li.querySelector(".job-status").textContent = jobStatus(j);
    const indeterminate = ["queued", "converting", "tagging"].includes(j.state) || (j.state === "downloading" && !j.bytes_total);
    const bar = li.querySelector(".job-bar");
    bar.classList.toggle("is-indeterminate", indeterminate);
    bar.firstElementChild.style.setProperty("--p", `${Math.round((j.progress || 0) * 100)}%`);
    const action = li.querySelector(".job-action");
    const wantCancel = ACTIVE.has(j.state);
    if (wantCancel !== !!action.firstElementChild) {
      action.innerHTML = wantCancel ? `<button class="icon-button small" type="button" data-cancel="${esc(j.id)}" aria-label="Cancel ${esc(j.title)}">${icon("close")}</button>` : "";
    }
    if (list.children[index] !== li) list.insertBefore(li, list.children[index] || null);
  });
  for (const li of existing.values()) li.remove();
}

// -------------------------------------------------------------------- crate

let libTimer = 0;
async function refreshLibrary() {
  clearTimeout(libTimer);
  let data;
  try {
    data = await api("/api/library");
  } catch {
    libTimer = setTimeout(refreshLibrary, 4000);
    return;
  }
  const sig = JSON.stringify(data.tracks);
  if (sig !== state.librarySig) {
    state.librarySig = sig;
    state.library = data.tracks;
    state.crateUids = new Set(data.tracks.filter((t) => t.source_id).map((t) => `${t.source}:${t.source_id}`));
    renderCrate();
    refreshGrabButtons();
  }
  $("#crate-count").textContent = String(state.library.length);
  if (state.library.some((t) => t.analysis_status === "pending" || t.analysis_status === "running")) {
    libTimer = setTimeout(refreshLibrary, 1500);
  }
}

function camelotNeighbors(code) {
  const m = /^(\d{1,2})([AB])$/.exec(code || "");
  if (!m) return null;
  const n = Number(m[1]);
  const letter = m[2];
  return new Set([`${n}${letter}`, `${(n % 12) + 1}${letter}`, `${((n + 10) % 12) + 1}${letter}`, `${n}${letter === "A" ? "B" : "A"}`]);
}
const bpmGap = (a, b) => Math.min(...[1, 2, 0.5].map((f) => Math.abs(a * f - b) / b));
const bpmCompatible = (a, b) => bpmGap(a, b) <= 0.06; // a ±6% pitch fader covers it

function applyFilter(tracks) {
  const f = state.filter.trim().toLowerCase();
  if (!f) return tracks;
  const cam = /^0?(\d{1,2})\s*([ab])$/.exec(f);
  if (cam) return tracks.filter((t) => (t.camelot || "").toLowerCase() === `${Number(cam[1])}${cam[2]}`);
  const num = /^(\d{2,3}(?:\.\d+)?)$/.exec(f);
  if (num) return tracks.filter((t) => t.bpm && Math.abs(t.bpm - Number(num[1])) <= 2);
  return tracks.filter((t) => [t.title, t.artist, t.album, t.genre, t.musical_key, t.license]
    .some((v) => (v || "").toLowerCase().includes(f)));
}

function sortTracks(tracks) {
  const { key, dir } = state.sort;
  const value = (t) => {
    if (key === "camelot") {
      const m = /^(\d+)([AB])$/.exec(t.camelot || "");
      return m ? Number(m[1]) * 2 + (m[2] === "B" ? 1 : 0) : null;
    }
    const v = t[key];
    return v === "" || v === undefined ? null : v;
  };
  return [...tracks].sort((a, b) => {
    const va = value(a), vb = value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1; // blanks last either way
    if (vb == null) return -1;
    const c = typeof va === "number" && typeof vb === "number"
      ? va - vb
      : String(va).localeCompare(String(vb), undefined, { sensitivity: "base", numeric: true });
    return c * dir;
  });
}

function visibleRows() {
  const anchor = state.anchorId != null ? state.library.find((t) => t.id === state.anchorId) : null;
  if (state.anchorId != null && !anchor) state.anchorId = null;
  if (anchor) {
    const keys = camelotNeighbors(anchor.camelot);
    const matches = applyFilter(state.library.filter((t) => t.id !== anchor.id && t.bpm && keys?.has(t.camelot) && bpmCompatible(anchor.bpm, t.bpm)));
    matches.sort((a, b) => bpmGap(anchor.bpm, a.bpm) - bpmGap(anchor.bpm, b.bpm));
    return { anchor, tracks: matches, keys };
  }
  return { anchor: null, tracks: sortTracks(applyFilter(state.library)) };
}

function crateRowHTML(t, isAnchor) {
  const on = state.now?.key === `lib:${t.id}`;
  const busy = t.analysis_status === "pending" || t.analysis_status === "running";
  const waiting = '<span class="pending" title="Detecting BPM and key">···</span>';
  const missing = t.analysis_status === "error"
    ? `<span class="na" title="${esc(t.analysis_error)}">n/a</span>`
    : '<span class="na">–</span>';
  const bpm = t.bpm ? t.bpm.toFixed(1) : busy ? waiting : missing;
  const key = t.camelot
    ? `<span class="key-dot" data-n="${parseInt(t.camelot, 10)}" data-mode="${esc(t.camelot.slice(-1))}">${esc(t.camelot)}</span><span class="key-name">${esc(t.musical_key)}</span>`
    : busy ? waiting : missing;
  const dash = '<span class="na">–</span>';
  return `<tr data-id="${t.id}" class="${isAnchor ? "is-anchor" : ""}${on ? " is-playing" : ""}">
    <td class="col-play"><button class="cue small" type="button" data-action="play" aria-label="Play ${esc(t.title)}">${icon(on && isPlaying() ? "pause" : "play")}</button></td>
    <td class="col-track">${isAnchor ? '<span class="anchor-note">Mixing from</span>' : ""}<div class="title" title="${esc(t.title)}">${esc(t.title)}</div><div class="artist">${esc(t.artist)}</div></td>
    <td class="col-bpm num">${bpm}</td>
    <td class="col-key">${key}</td>
    <td class="col-time num">${fmtTime(t.duration)}</td>
    <td class="col-genre" title="${esc(t.genre)}">${esc(t.genre) || dash}</td>
    <td class="col-license" title="${esc(t.license)}">${esc(t.license) || dash}</td>
    <td class="col-added">${esc(fmtDate(t.added_at))}</td>
    <td class="col-menu"><button class="icon-button small" type="button" data-action="menu" aria-haspopup="menu" aria-label="More for ${esc(t.title)}">${icon("more")}</button></td>
  </tr>`;
}

function renderCrate() {
  const empty = $("#crate-empty");
  const wrap = $("#crate-wrap");
  const { anchor, tracks, keys } = visibleRows();
  $("#match-chip").hidden = !anchor;
  if (anchor) $("#match-name").textContent = anchor.title;
  $("#crate-filter").closest("label").hidden = !state.library.length;
  $(".toolbar-actions #copy-credits").hidden = !state.library.length;

  if (!state.library.length) {
    wrap.hidden = true;
    empty.hidden = false;
    empty.innerHTML = `<h2>Your crate is empty</h2>
      <p>Dig for tracks and press Download. They land in <code>${esc(state.status?.library_dir || "your library folder")}</code>, tagged with BPM and key, ready for your DJ software.</p>
      <button class="button primary" type="button" data-action="go-dig">Start digging</button>`;
    return;
  }
  wrap.hidden = false;
  empty.hidden = true;

  let rows = tracks.map((t) => crateRowHTML(t, false)).join("");
  if (anchor) {
    rows = crateRowHTML(anchor, true) + rows;
    if (!tracks.length) {
      const lo = Math.round(anchor.bpm * 0.94), hi = Math.round(anchor.bpm * 1.06);
      rows += `<tr class="row-note"><td colspan="9">Nothing in your crate mixes with this yet. Look for ${esc([...keys].join(", "))} at ${lo}–${hi} BPM (or half or double that).</td></tr>`;
    }
  } else if (!tracks.length) {
    rows = `<tr class="row-note"><td colspan="9">No tracks match “${esc(state.filter)}”.</td></tr>`;
  }
  $("#crate-body").innerHTML = rows;
  for (const th of $$("#crate-table th[data-sort]")) {
    if (!anchor && th.dataset.sort === state.sort.key) th.setAttribute("aria-sort", state.sort.dir > 0 ? "ascending" : "descending");
    else th.removeAttribute("aria-sort");
  }
}

let menuEl = null;
function closeMenu() {
  if (!menuEl) return;
  menuEl.remove();
  menuEl = null;
  document.removeEventListener("pointerdown", onPointerOutsideMenu, true);
}
function onPointerOutsideMenu(e) {
  if (menuEl && !menuEl.contains(e.target)) closeMenu();
}

function openMenu(button, t) {
  closeMenu();
  const analysing = t.analysis_status === "pending" || t.analysis_status === "running";
  const items = [
    ["match", "Find tracks that mix with this", !(t.bpm && t.camelot)],
    ["double", "Double the BPM", !t.bpm],
    ["halve", "Halve the BPM", !t.bpm],
    ["analyze", t.analysis_status === "done" ? "Detect BPM and key again" : "Detect BPM and key", analysing],
    "-",
    ["reveal", "Show in folder"],
    ...(safeUrl(t.page_url) ? [["page", `Open on ${SOURCE_NAMES[t.source] || "the web"}`]] : []),
    "-",
    ["delete", "Delete from crate…", false, "danger"],
  ];
  menuEl = document.createElement("div");
  menuEl.className = "menu";
  menuEl.setAttribute("role", "menu");
  menuEl.innerHTML = items.map((it) => (it === "-" ? "<hr>"
    : `<button type="button" role="menuitem" data-item="${it[0]}"${it[2] ? " disabled" : ""} class="${it[3] || ""}">${esc(it[1])}</button>`)).join("");
  document.body.append(menuEl);
  const r = button.getBoundingClientRect();
  let top = r.bottom + 6;
  if (top + menuEl.offsetHeight > window.innerHeight - 110) top = Math.max(8, r.top - menuEl.offsetHeight - 6);
  menuEl.style.top = `${top}px`;
  menuEl.style.left = `${Math.min(window.innerWidth - menuEl.offsetWidth - 8, Math.max(8, r.right - menuEl.offsetWidth))}px`;
  menuEl.querySelector("button:not([disabled])")?.focus();
  menuEl.addEventListener("click", (e) => {
    const b = e.target.closest("button[data-item]");
    if (!b || b.disabled) return;
    closeMenu();
    button.focus();
    runMenuItem(b.dataset.item, t);
  });
  menuEl.addEventListener("keydown", (e) => {
    const options = $$("button:not([disabled])", menuEl);
    const i = options.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      options[(i + (e.key === "ArrowDown" ? 1 : options.length - 1)) % options.length]?.focus();
    } else if (e.key === "Escape" || e.key === "Tab") {
      e.preventDefault();
      closeMenu();
      button.focus();
    }
  });
  setTimeout(() => document.addEventListener("pointerdown", onPointerOutsideMenu, true));
}

async function runMenuItem(item, t) {
  try {
    if (item === "match") {
      state.anchorId = t.id;
      renderCrate();
    } else if (item === "double" || item === "halve") {
      const { bpm } = await api(`/api/library/${t.id}/bpm`, { method: "POST", body: { factor: item === "double" ? 2 : 0.5 } });
      toast(`BPM set to ${bpm.toFixed(1)}`);
      await refreshLibrary();
    } else if (item === "analyze") {
      await api(`/api/library/${t.id}/analyze`, { method: "POST" });
      await refreshLibrary();
    } else if (item === "reveal") {
      await api(`/api/library/${t.id}/reveal`, { method: "POST" });
    } else if (item === "page") {
      window.open(safeUrl(t.page_url), "_blank", "noopener");
    } else if (item === "delete") {
      if (!window.confirm(`Delete “${t.title}” from your crate? The file is removed from your library folder.`)) return;
      if (state.now?.key === `lib:${t.id}`) stopPlayback();
      await api(`/api/library/${t.id}`, { method: "DELETE" });
      if (t.source_id) state.deletedUids.add(`${t.source}:${t.source_id}`);
      if (state.anchorId === t.id) state.anchorId = null;
      toast(`Deleted “${t.title}”`);
      await refreshLibrary();
      refreshGrabButtons();
    }
  } catch (e) {
    toast(e.message, { kind: "error" });
  }
}

// --------------------------------------------------------------------- deck

const audio = $("#audio");
const wave = $("#waveform");
let colors = readColors();
let hoverX = null;
let rafId = 0;

function readColors() {
  const cs = getComputedStyle(document.documentElement);
  return { played: cs.getPropertyValue("--ink").trim(), rest: cs.getPropertyValue("--rule-strong").trim(), head: cs.getPropertyValue("--stamp").trim() };
}

const isPlaying = () => (state.now?.kind === "youtube" ? true : !!state.now && !audio.paused);
function duration() {
  if (!state.now) return 0;
  if (state.now.kind === "audio" && Number.isFinite(audio.duration) && audio.duration > 0) return audio.duration;
  return state.now.duration || 0;
}

function setDeck(now) {
  state.now = now;
  $("#now-title").textContent = now ? now.title : "Nothing cued";
  $("#now-artist").textContent = now ? now.artist : "Press play on any track to preview it";
  const label = $("#disc-label");
  const art = now ? safeUrl(now.art) : "";
  label.style.backgroundImage = art ? `url("${art.replace(/["\\\n]/g, encodeURIComponent)}")` : "";
  label.classList.toggle("has-art", !!art);
  const cam = /^(\d+)([AB])$/.exec(now?.camelot || "");
  label.style.backgroundColor = !art && cam ? `hsl(${CAMELOT_HUES[Number(cam[1]) - 1]} 60% ${cam[2] === "A" ? 62 : 74}%)` : "";
  label.style.color = !art && cam ? "#17171a" : "";
  $("#disc-text").textContent = !art && cam ? now.camelot : "";
  $("#play-toggle").disabled = !now;
  $("#scrub-note").hidden = now?.kind !== "youtube";
  updateTimes();
  updatePlayingUI();
  drawWave();
}

function updatePlayingUI() {
  const playing = isPlaying();
  const yt = state.now?.kind === "youtube";
  const btn = $("#play-toggle");
  btn.innerHTML = icon(yt ? "stop" : playing ? "pause" : "play");
  btn.setAttribute("aria-label", yt ? "Stop the YouTube preview" : playing ? "Pause" : "Play");
  $("#disc").classList.toggle("is-spinning", playing);
  for (const li of $$(".result[data-uid]")) {
    const on = state.now?.key === li.dataset.uid;
    li.classList.toggle("is-playing", on);
    const cue = li.querySelector(".cue");
    if (cue) cue.innerHTML = icon(on && playing ? "pause" : "play");
  }
  for (const tr of $$("#crate-body tr[data-id]")) {
    const on = state.now?.key === `lib:${tr.dataset.id}`;
    tr.classList.toggle("is-playing", on);
    const cue = tr.querySelector(".cue");
    if (cue) cue.innerHTML = icon(on && playing ? "pause" : "play");
  }
}

function updateTimes() {
  const total = duration();
  const current = state.now?.kind === "audio" ? audio.currentTime : 0;
  $("#time-current").textContent = state.now?.kind === "youtube" ? "" : fmtClock(current);
  $("#time-total").textContent = fmtClock(total);
  wave.setAttribute("aria-valuemax", String(Math.round(total)));
  wave.setAttribute("aria-valuenow", String(Math.round(current)));
  wave.setAttribute("aria-valuetext", `${fmtClock(current)} of ${fmtClock(total)}`);
}

function drawWave() {
  const dpr = window.devicePixelRatio || 1;
  const w = wave.clientWidth, h = wave.clientHeight;
  if (!w || !h) return;
  if (wave.width !== Math.round(w * dpr) || wave.height !== Math.round(h * dpr)) {
    wave.width = Math.round(w * dpr);
    wave.height = Math.round(h * dpr);
  }
  const ctx = wave.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, w, h);
  const total = duration();
  const live = state.now?.kind === "audio" && total > 0;
  const pos = live ? Math.min(1, audio.currentTime / total) : 0;
  const peaks = state.now?.kind === "audio" ? state.now.waveform : null;
  if (peaks?.length) {
    const bars = Math.max(1, Math.min(peaks.length, Math.floor(w / 3)));
    const step = w / bars;
    for (let i = 0; i < bars; i++) {
      const from = Math.floor((i * peaks.length) / bars);
      const to = Math.max(from + 1, Math.floor(((i + 1) * peaks.length) / bars));
      const v = Math.max(...peaks.slice(from, to));
      const bh = Math.max(2, v * (h - 6));
      ctx.fillStyle = (i + 0.5) / bars <= pos ? colors.played : colors.rest;
      ctx.fillRect(i * step, (h - bh) / 2, Math.max(1, step - 1), bh);
    }
  } else {
    ctx.fillStyle = colors.rest;
    ctx.fillRect(0, h / 2 - 2, w, 4);
    ctx.fillStyle = colors.played;
    ctx.fillRect(0, h / 2 - 2, w * pos, 4);
  }
  if (live) {
    ctx.fillStyle = colors.head;
    ctx.fillRect(Math.round(w * pos) - 1, 2, 2, h - 4);
    if (hoverX != null) {
      ctx.globalAlpha = 0.4;
      ctx.fillRect(Math.round(hoverX), 0, 1, h);
      ctx.globalAlpha = 1;
    }
  }
}

function startWaveLoop() {
  cancelAnimationFrame(rafId);
  const tick = () => {
    drawWave();
    if (!audio.paused) rafId = requestAnimationFrame(tick);
  };
  rafId = requestAnimationFrame(tick);
}

function playAudio(now) {
  closeYouTube(false);
  setDeck(now);
  audio.src = now.src;
  audio.play().catch((e) => {
    if (e.name !== "AbortError") toast("Couldn't start playback. Press play to try again.", { kind: "error" });
    updatePlayingUI();
  });
}

function previewResult(t) {
  if (state.now?.key === t.uid) {
    togglePlay();
    return;
  }
  if (t.source === "youtube") {
    openYouTube(t);
    return;
  }
  if (!safeUrl(t.preview_url)) {
    toast("There's no preview for this track.");
    return;
  }
  playAudio({ kind: "audio", key: t.uid, title: t.title, artist: t.artist, art: t.image_url, waveform: t.waveform, duration: t.duration, src: t.preview_url });
}

async function playLibrary(t) {
  const key = `lib:${t.id}`;
  if (state.now?.key === key) {
    togglePlay();
    return;
  }
  playAudio({ kind: "audio", key, title: t.title, artist: t.artist, art: t.image_url, camelot: t.camelot, waveform: null,
    duration: t.duration, src: `/api/library/${t.id}/audio?t=${encodeURIComponent(TOKEN)}` });
  try {
    const full = await api(`/api/library/${t.id}`);
    if (state.now?.key === key) {
      state.now.waveform = full.waveform;
      drawWave();
    }
  } catch { /* the waveform is a nice-to-have */ }
}

function togglePlay() {
  if (!state.now) return;
  if (state.now.kind === "youtube") {
    closeYouTube();
    return;
  }
  if (audio.paused) audio.play().catch(() => updatePlayingUI());
  else audio.pause();
}

function stopPlayback() {
  audio.pause();
  audio.removeAttribute("src");
  audio.load();
  closeYouTube(false);
  setDeck(null);
}

function seekBy(seconds) {
  const total = duration();
  if (!total || state.now?.kind !== "audio") return;
  audio.currentTime = Math.max(0, Math.min(total - 0.1, audio.currentTime + seconds));
  updateTimes();
  drawWave();
}

function seekToClientX(x) {
  const total = duration();
  if (!total || state.now?.kind !== "audio") return;
  const r = wave.getBoundingClientRect();
  audio.currentTime = Math.max(0, Math.min(1, (x - r.left) / r.width)) * total;
  updateTimes();
  drawWave();
}

function openYouTube(t) {
  audio.pause();
  const frame = $("#yt-frame");
  frame.innerHTML = `<iframe src="https://www.youtube-nocookie.com/embed/${encodeURIComponent(t.source_id)}?autoplay=1&rel=0&playsinline=1"
    title="YouTube preview of ${esc(t.title)}" allow="autoplay; encrypted-media; picture-in-picture"
    referrerpolicy="strict-origin-when-cross-origin" allowfullscreen></iframe>`;
  $("#yt-panel").hidden = false;
  setDeck({ kind: "youtube", key: t.uid, title: t.title, artist: t.artist, art: t.image_url, duration: t.duration });
}

function closeYouTube(clearDeck = true) {
  if ($("#yt-panel").hidden) return;
  $("#yt-frame").innerHTML = "";
  $("#yt-panel").hidden = true;
  if (clearDeck && state.now?.kind === "youtube") setDeck(null);
}

// ----------------------------------------------------------------- settings

const dialog = $("#settings-dialog");
const form = $("#settings-form");

function renderHelpers() {
  const t = state.status?.tools || {};
  const items = [
    [!!t.yt_dlp, "yt-dlp", t.yt_dlp
      ? `Version ${esc(t.yt_dlp)}. YouTube changes often, so update it now and then: <code>pip install -U "yt-dlp[default]"</code>`
      : 'Not installed, so YouTube is off. Install it with <code>pip install -U "yt-dlp[default]"</code>'],
    [!!t.deno, "Deno", t.deno
      ? "Found. yt-dlp uses it to unlock YouTube audio."
      : 'Not found. YouTube downloads may fail without it. Install it from <a href="https://deno.com" target="_blank" rel="noopener noreferrer">deno.com</a>, then restart Crate Digger.'],
    [!!t.ffmpeg, "ffmpeg", t.ffmpeg
      ? "Found. It converts YouTube audio and reads files for BPM and key."
      : "Not found. Install it with <code>pip install imageio-ffmpeg</code>"],
    [!!(t.numpy && t.ffmpeg), "BPM and key detection", t.numpy ? (t.ffmpeg ? "Ready." : "Waiting for ffmpeg.") : "Needs numpy: <code>pip install numpy</code>"],
    [!!t.mutagen, "Tag writing", t.mutagen ? "Ready. Title, artist, BPM, key and cover art go into each file." : "Needs mutagen: <code>pip install mutagen</code>"],
  ];
  $("#helper-list").innerHTML = items.map(([ok, name, text]) => `<li>
      <span class="state ${ok ? "ok" : "missing"}" role="img" aria-label="${ok ? "Ready" : "Missing"}">${ok ? "✓" : "!"}</span>
      <span><strong>${esc(name)}</strong>. ${text}</span>
    </li>`).join("");
}

async function openSettings() {
  let data;
  try {
    [data] = await Promise.all([api("/api/settings"), loadStatus()]);
  } catch (e) {
    toast(e.message, { kind: "error" });
    return;
  }
  const s = data.settings;
  const f = form.elements;
  f.library_dir.value = s.library_dir;
  f.jamendo_client_id.value = s.jamendo_client_id;
  f.prefer_lossless.checked = s.prefer_lossless;
  f.auto_analyze.checked = s.auto_analyze;
  f.youtube_cookies_browser.innerHTML = data.cookie_browsers.map((b) => `<option value="${esc(b)}">${b
    ? `Use my ${esc(BROWSER_NAMES[b] || b)} sign-in` : "Don't use a browser sign-in"}</option>`).join("");
  f.youtube_cookies_browser.value = s.youtube_cookies_browser;
  $("#settings-error").hidden = true;
  renderHelpers();
  dialog.showModal();
}

form.addEventListener("submit", async (e) => {
  if (e.submitter?.value === "cancel") return; // the dialog closes itself
  e.preventDefault();
  const f = form.elements;
  const body = {
    library_dir: f.library_dir.value,
    jamendo_client_id: f.jamendo_client_id.value,
    prefer_lossless: f.prefer_lossless.checked,
    auto_analyze: f.auto_analyze.checked,
    youtube_cookies_browser: f.youtube_cookies_browser.value,
  };
  try {
    await api("/api/settings", { method: "POST", body });
    dialog.close();
    toast("Settings saved");
    await loadStatus();
  } catch (err) {
    const el = $("#settings-error");
    el.textContent = err.message;
    el.hidden = false;
  }
});

// ------------------------------------------------------------------- events

function bindEvents() {
  $("#brand").addEventListener("click", (e) => {
    e.preventDefault();
    showView("dig");
    $("#search-input").focus();
  });
  for (const tab of $$(".tab")) tab.addEventListener("click", () => showView(tab.dataset.view));
  window.addEventListener("hashchange", () => showView(location.hash === "#crate" ? "crate" : "dig"));
  $("#open-settings").addEventListener("click", openSettings);

  $("#search-form").addEventListener("submit", (e) => {
    e.preventDefault();
    runSearch($("#search-input").value);
  });
  $("#source-picker").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-source]");
    if (b) selectSource(b.dataset.source);
  });
  $("#source-picker").addEventListener("keydown", (e) => {
    if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
    e.preventDefault();
    const buttons = $$("#source-picker button");
    const i = buttons.findIndex((b) => b.dataset.source === state.source);
    const next = buttons[(i + (e.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length];
    selectSource(next.dataset.source);
    next.focus();
  });
  $("#cc-only").addEventListener("change", (e) => {
    state.ccOnly = e.target.checked;
    store.set("ccOnly", state.ccOnly);
    if (state.query) runSearch(state.query);
  });

  $("#results").addEventListener("click", (e) => {
    const starter = e.target.closest("[data-starter]");
    if (starter) {
      $("#search-input").value = starter.dataset.starter;
      runSearch(starter.dataset.starter);
      return;
    }
    const el = e.target.closest("[data-action]");
    if (!el) return;
    if (el.dataset.action === "settings") {
      openSettings();
      return;
    }
    const t = findResult(el.closest(".result")?.dataset.uid);
    if (!t) return;
    if (el.dataset.action === "preview") previewResult(t);
    if (el.dataset.action === "grab") grab(t);
  });

  $("#downloads-toggle").addEventListener("click", () => {
    state.downloadsOpen = !state.downloadsOpen;
    renderDownloads();
  });
  $("#hide-downloads").addEventListener("click", () => {
    state.downloadsOpen = false;
    renderDownloads();
  });
  $("#clear-downloads").addEventListener("click", async () => {
    try {
      await api("/api/downloads/clear", { method: "POST" });
      await pollJobs();
    } catch (e) { toast(e.message, { kind: "error" }); }
  });
  $("#download-list").addEventListener("click", async (e) => {
    const b = e.target.closest("[data-cancel]");
    if (!b) return;
    try {
      await api(`/api/downloads/${encodeURIComponent(b.dataset.cancel)}/cancel`, { method: "POST" });
      await pollJobs();
    } catch (err) { toast(err.message, { kind: "error" }); }
  });

  $("#crate-filter").addEventListener("input", (e) => {
    state.filter = e.target.value;
    renderCrate();
  });
  $("#match-clear").addEventListener("click", () => {
    state.anchorId = null;
    renderCrate();
  });
  $("#crate-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th[data-sort]");
    if (!th) return;
    const key = th.dataset.sort;
    state.sort = state.sort.key === key ? { key, dir: -state.sort.dir } : { key, dir: key === "added_at" ? -1 : 1 };
    store.set("sort", state.sort);
    state.anchorId = null;
    renderCrate();
  });
  $("#crate-body").addEventListener("click", (e) => {
    const el = e.target.closest("[data-action]");
    if (!el) return;
    const t = state.library.find((x) => x.id === Number(el.closest("tr")?.dataset.id));
    if (!t) return;
    if (el.dataset.action === "play") playLibrary(t);
    if (el.dataset.action === "menu") openMenu(el, t);
  });
  $("#crate-body").addEventListener("dblclick", (e) => {
    if (e.target.closest("button")) return;
    const t = state.library.find((x) => x.id === Number(e.target.closest("tr[data-id]")?.dataset.id));
    if (t) playLibrary(t);
  });
  $("#crate-empty").addEventListener("click", (e) => {
    if (!e.target.closest('[data-action="go-dig"]')) return;
    showView("dig");
    $("#search-input").focus();
  });
  $("#copy-credits").addEventListener("click", async () => {
    const { anchor, tracks } = visibleRows();
    const list = anchor ? [anchor, ...tracks] : tracks;
    if (!list.length) {
      toast("There are no tracks to credit.");
      return;
    }
    const lines = list.map((t) => [`${t.artist} – ${t.title}`, t.license ? `(${t.license})` : "", safeUrl(t.page_url)].filter(Boolean).join(" "));
    try {
      await navigator.clipboard.writeText(lines.join("\n"));
      toast(`Copied credits for ${plural(list.length, "track")}`);
    } catch {
      toast("The browser blocked clipboard access.", { kind: "error" });
    }
  });
  $("#rescan").addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    btn.textContent = "Rescanning…";
    try {
      const r = await api("/api/library/rescan", { method: "POST" });
      const parts = [];
      if (r.added) parts.push(`Found ${plural(r.added, "new track")}`);
      if (r.removed) parts.push(`removed ${plural(r.removed, "missing track")}`);
      toast(parts.length ? `${parts.join(", ")}.` : "Your crate already matches the folder.");
      await refreshLibrary();
    } catch (err) {
      toast(err.message, { kind: "error" });
    } finally {
      btn.disabled = false;
      btn.textContent = "Rescan folder";
    }
  });
  $("#open-folder").addEventListener("click", async () => {
    try { await api("/api/library/open", { method: "POST" }); } catch (e) { toast(e.message, { kind: "error" }); }
  });

  // deck
  $("#play-toggle").addEventListener("click", togglePlay);
  $("#yt-close").addEventListener("click", () => closeYouTube());
  const volume = $("#volume");
  audio.volume = Math.min(1, Math.max(0, Number(store.get("volume", 0.8))));
  volume.value = String(audio.volume);
  volume.addEventListener("input", () => {
    audio.volume = Number(volume.value);
    store.set("volume", audio.volume);
  });
  audio.addEventListener("play", () => { updatePlayingUI(); startWaveLoop(); });
  audio.addEventListener("pause", updatePlayingUI);
  audio.addEventListener("ended", () => { updatePlayingUI(); drawWave(); });
  audio.addEventListener("timeupdate", updateTimes);
  audio.addEventListener("loadedmetadata", () => {
    if (state.now) state.now.duration = audio.duration;
    updateTimes();
    drawWave();
  });
  audio.addEventListener("error", () => {
    if (!state.now || !audio.getAttribute("src")) return;
    toast(state.now.key.startsWith("lib:")
      ? "Couldn't play this file. If you moved it, use Rescan folder."
      : "Couldn't play this preview. The source may be busy, so try again in a moment.", { kind: "error" });
    updatePlayingUI();
  });

  let dragging = false;
  wave.addEventListener("pointerdown", (e) => {
    dragging = true;
    wave.setPointerCapture(e.pointerId);
    seekToClientX(e.clientX);
  });
  wave.addEventListener("pointermove", (e) => {
    hoverX = e.clientX - wave.getBoundingClientRect().left;
    if (dragging) seekToClientX(e.clientX);
    else drawWave();
  });
  wave.addEventListener("pointerup", () => { dragging = false; });
  wave.addEventListener("pointerleave", () => { hoverX = null; drawWave(); });
  wave.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft" || e.key === "ArrowRight") {
      e.preventDefault();
      seekBy((e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 30 : 10));
    } else if (e.key === "Home") {
      e.preventDefault();
      seekBy(-Infinity);
    }
  });
  new ResizeObserver(() => drawWave()).observe(wave);
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    colors = readColors();
    drawWave();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && menuEl) {
      closeMenu();
      return;
    }
    const typing = e.target.closest?.("input, textarea, select, [contenteditable]") || dialog.open;
    if (typing || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "/") {
      e.preventDefault();
      showView("dig");
      $("#search-input").focus();
      $("#search-input").select();
    } else if (e.key === " " && !e.target.closest("button, a, [role=slider], [role=radio]")) {
      e.preventDefault();
      togglePlay();
    } else if ((e.key === "ArrowLeft" || e.key === "ArrowRight") && e.target === document.body) {
      e.preventDefault();
      seekBy((e.key === "ArrowRight" ? 1 : -1) * (e.shiftKey ? 30 : 10));
    }
  });
}

async function init() {
  bindEvents();
  renderSourcePicker();
  renderResults();
  showView(location.hash === "#crate" ? "crate" : "dig");
  setDeck(null);
  await Promise.all([loadStatus(), refreshLibrary(), pollJobs()]);
  renderCrate();
  if (location.hash !== "#crate") $("#search-input").focus();
}

init();
