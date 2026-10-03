import './style.css';
import { CameraMap } from './map.js';
import * as api from './api.js';

const el = (id) => document.getElementById(id);

const app = {
  cams: new Map(),        // id -> camera
  cam: null,              // currently viewed camera
  tracks: [],             // latest tracks for the viewed camera
  target: null,           // { source_camera_id, track_id, label }
  candidates: [],         // armed handoff candidates
  triggered: new Set(),   // camera ids that fired
  path: [],               // ordered confirmed sightings (the car's journey)
  following: false,       // auto-jump the feed to each new sighting
  hit: null,              // { camera_id, track_id } of the latest match to highlight
  viewToken: 0,           // increments per view to cancel stale async renders
  source: null,
  map: null,
};

async function init() {
  app.map = new CameraMap('map', { onPick: pickCamera });

  const [status, cams, srcs] = await Promise.all([
    api.getStatus(), api.getCameras(), api.getSources(),
  ]);
  app.cams = new Map(cams.map((c) => [c.id, c]));
  app.map.addCameras(cams);
  app.source = srcs.active;
  app.status = status;
  renderSourceToggle(srcs);
  renderStatus(status, false);

  api.connectWS(onEvent,
    () => renderStatus(app.status, true),
    () => renderStatus(app.status, false));

  // Deep link: /#cam=<id> opens that camera straight away (shareable links).
  openCameraFromHash();
  window.addEventListener('hashchange', openCameraFromHash);

  // Tick the "updated Xs ago" indicator so the slow source cadence is visible.
  setInterval(updateFreshness, 1000);
}

function updateFreshness() {
  if (!app.cam || !app.lastChangeAt) return;
  const age = Math.round((Date.now() - app.lastChangeAt) / 1000);
  const label = age < 3 ? 'updated just now'
    : age < 90 ? `updated ${age}s ago`
    : `updated ${Math.round(age / 60)}m ago`;
  const stale = age > 20 ? ' · source refreshes slowly' : '';
  el('feed-meta').textContent = `${app.cam.id} · ${label}${stale}`;
}

function openCameraFromHash() {
  const m = location.hash.match(/cam=([^&]+)/);
  if (!m) return;
  const cam = app.cams.get(decodeURIComponent(m[1]));
  if (cam) pickCamera(cam);
}

const SOURCE_LABELS = { tfl: 'London', austin: 'Austin' };

function renderSourceToggle(srcs) {
  const box = el('source-toggle');
  box.innerHTML = srcs.available.map((name) =>
    `<button class="src-btn ${name === srcs.active ? 'active' : ''}" data-src="${name}">` +
    `${SOURCE_LABELS[name] || name}</button>`).join('');
  box.querySelectorAll('.src-btn').forEach((b) => {
    b.onclick = () => switchSource(b.dataset.src);
  });
}

async function switchSource(name) {
  if (name === app.source) return;
  el('source-toggle').querySelectorAll('.src-btn').forEach((b) => {
    b.classList.toggle('active', b.dataset.src === name);
    b.disabled = true;
  });
  // Reset UI state for the new city.
  onCleared();
  app.cam = null;
  el('feed-title').textContent = 'NO FEED';
  el('feed-meta').textContent = '';
  el('feed-img').style.display = 'none';
  el('boxes').innerHTML = '';
  el('feed-empty').style.display = '';
  el('feed-empty').textContent = `switching to ${SOURCE_LABELS[name] || name}…`;
  el('events').innerHTML = '';

  await api.setSource(name);
  await reloadSource(name);
  el('source-toggle').querySelectorAll('.src-btn').forEach((b) => (b.disabled = false));
}

// Reload cameras + status for the active source (also used when switched elsewhere).
async function reloadSource(name) {
  app.source = name;
  const [cams, status] = await Promise.all([api.getCameras(), api.getStatus()]);
  app.cams = new Map(cams.map((c) => [c.id, c]));
  app.map.clearCameras();
  app.map.addCameras(cams);          // re-fits the map to the new city
  app.status = status;
  renderStatus(status, true);
  el('source-toggle').querySelectorAll('.src-btn').forEach(
    (b) => b.classList.toggle('active', b.dataset.src === name));
  el('feed-empty').textContent = 'Select a camera on the map.';
  logEvent('select', `source → <b>${SOURCE_LABELS[name] || name}</b> (${cams.length} cameras)`);
}

function renderStatus(s, live) {
  el('status').innerHTML =
    `<span>${live ? '<b>● LIVE</b>' : '○ offline'}</span>` +
    `<span>detector <b>${s.detector_available ? 'on' : 'off'}</b></span>` +
    `<span>re-id <b>${s.reid_backend}</b></span>` +
    `<span>cams <b>${s.camera_count}</b></span>`;
}

// --- Opening / viewing a camera ------------------------------------------ //
// Manual pick from the UI: the user takes control, so stop auto-following and
// drop any match highlight from a previous hop.
function pickCamera(cam) {
  return openCamera(cam, { manual: true });
}

async function openCamera(cam, { manual = true, matchLabel = null } = {}) {
  const token = ++app.viewToken;         // guards against racing async renders
  app.cam = cam;
  app.tracks = [];
  app.lastState = null;
  app.lastChangeAt = null;
  if (manual) { app.following = false; app.hit = null; }
  // Auto-follow freezes on the match frame; manual uses the source's default.
  app.feedMode = (!manual || matchLabel) ? 'inspect' : (cam.has_video ? 'live' : 'inspect');
  el('feed-title').textContent = cam.name.toUpperCase() + (matchLabel ? '  ◂ MATCH' : '');
  el('feed-meta').textContent = cam.id;
  el('feed-empty').textContent = 'acquiring feed…';
  el('feed-empty').style.display = '';
  el('feed-img').style.display = 'none';
  el('feed-video').style.display = 'none';
  el('boxes').innerHTML = '';
  el('mode-btn').classList.add('hidden');
  el('track-hint').textContent = cam.has_video
    ? 'Looping the live clip. Click the feed to inspect & pick a vehicle.'
    : 'Click a vehicle box to start tracking it.';
  if (manual && !app.target) app.map.resetStates();
  if (!app.triggered.has(cam.id)) {
    app.map.setState(cam.id,
      app.target && app.target.source_camera_id === cam.id ? 'source' : 'viewing');
  }
  app.map.focus(cam.lat, cam.lon, Math.max(app.map.map.getZoom(), 14));
  await api.viewCamera(cam.id);

  // WebSocket only pushes a frame when it *changes*; a camera that's already
  // been processed and is static would never arrive. Pull the current frame
  // now so the feed populates immediately, then let WS handle updates.
  for (let i = 0; i < 12 && app.viewToken === token; i++) {
    const st = await api.getState(cam.id);
    if (st && st.seq > 0 && app.viewToken === token) { renderFeed(st); break; }
    await new Promise((r) => setTimeout(r, 1500));
  }
}

// --- Feed rendering ------------------------------------------------------ //
function renderFeed(state) {
  const stage = el('feed-stage');
  if (state.frame_w && state.frame_h) {
    stage.style.aspectRatio = `${state.frame_w} / ${state.frame_h}`;
  }
  app.lastState = state;
  app.lastChangeAt = Date.now();   // this frame is a real change (seq bumped)
  app.tracks = state.tracks || [];
  el('feed-empty').style.display = 'none';

  const cam = app.cams.get(state.camera_id);
  const hasVideo = cam && cam.has_video;
  // Point both media elements at the latest content; applyFeedMode shows one.
  el('feed-img').src = api.frameUrl(state.camera_id, state.seq);
  if (hasVideo) {
    const v = el('feed-video');
    if (!v.src.includes(`clip.mp4?seq=${state.seq}`)) {
      v.src = api.clipUrl(state.camera_id, state.seq);
      v.load();
    }
  }
  applyFeedMode();

  if (!app.target) {
    const n = app.tracks.length;
    const inspect = app.feedMode === 'inspect' || !hasVideo;
    el('track-hint').textContent = n
      ? (inspect ? `${n} vehicle${n > 1 ? 's' : ''} detected — click one to track it.`
                 : `${n} vehicle${n > 1 ? 's' : ''} detected — click the feed to inspect & pick one.`)
      : 'No vehicles in this frame; waiting for the next pass…';
  }
}

function applyFeedMode() {
  const cam = app.cam;
  const hasVideo = cam && cam.has_video && el('feed-video').src;
  const live = app.feedMode === 'live' && hasVideo;
  const vid = el('feed-video'), img = el('feed-img'), btn = el('mode-btn');

  if (live) {
    vid.style.display = 'block';
    img.style.display = 'none';
    el('boxes').style.display = 'none';
    vid.play().catch(() => {});
  } else {
    vid.pause();
    vid.style.display = 'none';
    img.style.display = 'block';
    el('boxes').style.display = 'block';
    if (app.lastState) drawBoxes(app.lastState);
  }
  // The toggle only makes sense when there's a clip to loop.
  btn.classList.toggle('hidden', !hasVideo);
  btn.textContent = live ? '⏸ INSPECT' : '▶ LIVE';
}

function setFeedMode(mode) {
  app.feedMode = mode;
  applyFeedMode();
}

function drawBoxes(state) {
  const box = el('boxes');
  box.innerHTML = '';
  const fw = state.frame_w || 352, fh = state.frame_h || 288;
  for (const t of app.tracks) {
    const [x, y, w, h] = t.bbox;
    const d = document.createElement('div');
    d.className = 'vbox';
    // Highlight the matched vehicle only on the camera where it was matched.
    if (app.hit && app.hit.camera_id === state.camera_id &&
        app.hit.track_id === t.track_id) d.classList.add('hit');
    else if (app.target && app.target.source_camera_id === state.camera_id &&
        app.target.track_id === t.track_id) d.classList.add('target');
    d.style.left = `${(x / fw) * 100}%`;
    d.style.top = `${(y / fh) * 100}%`;
    d.style.width = `${(w / fw) * 100}%`;
    d.style.height = `${(h / fh) * 100}%`;
    d.innerHTML = `<span class="tag">${t.label} ${(t.score * 100) | 0}%</span>`;
    d.onclick = () => onSelectBox(state.camera_id, t);
    box.appendChild(d);
  }
}

// --- Selecting a target vehicle ------------------------------------------ //
async function onSelectBox(cameraId, track) {
  try {
    const res = await api.selectTarget(cameraId, track.track_id);
    applyArmed(res);
    logEvent('select', `tracking <b>${track.label}</b> from ${shortName(cameraId)}`);
  } catch (e) {
    logEvent('select', `select failed: ${e.message}`);
  }
}

function applyArmed(res) {
  app.target = res.target;
  app.candidates = res.candidates || [];
  app.triggered.clear();
  if (!res.relay) {
    // A fresh manual selection: (re)start following, clear any old match.
    app.following = true;
    app.hit = null;
  }

  app.map.resetStates();
  app.map.setState(app.target.source_camera_id, 'source');
  const src = app.cams.get(app.target.source_camera_id);
  for (const c of app.candidates) app.map.setState(c.camera_id, 'armed');
  if (src) app.map.drawCorridors(src, app.candidates);

  renderTarget();
  if (app.lastState) drawBoxes(app.lastState);  // refresh target highlight
}

function renderTarget() {
  const panel = el('target-panel');
  panel.classList.remove('hidden');
  const t = app.target;
  const cands = app.candidates.map((c) => {
    const nm = shortName(c.camera_id);
    const hit = app.triggered.has(c.camera_id);
    const al = c.aligned ? '' : ' <span class="d">(off-axis)</span>';
    return `<div class="cand ${hit ? 'hit' : ''}" data-id="${c.camera_id}">
      <span class="pip"></span>
      <span class="nm">${escapeHtml(nm)}${al}</span>
      <span class="d">${Math.round(c.distance_m)}m · ${fmtWin(c)}</span>
    </div>`;
  }).join('');

  el('target-body').innerHTML = `
    <div class="tgt-row"><span class="k">vehicle</span><span>${t.label}${t.has_embedding ? '' : ' (colour only)'}</span></div>
    <div class="tgt-row"><span class="k">source</span><span>${escapeHtml(shortName(t.source_camera_id))}</span></div>
    <div class="tgt-row"><span class="k">armed cams</span><span>${app.candidates.length}</span></div>
    <div class="cand-list">${cands || '<span class="d">no downstream cameras in range</span>'}</div>`;

  panel.querySelectorAll('.cand').forEach((n) => {
    n.onclick = () => {
      const c = app.cams.get(n.dataset.id);
      if (c) pickCamera(c);
    };
  });
}

// --- WebSocket events ---------------------------------------------------- //
function onEvent(msg) {
  switch (msg.type) {
    case 'hello':
      renderStatus(msg.status, true);
      break;
    case 'state':
      if (app.cam && msg.camera_id === app.cam.id) renderFeed(msg);
      break;
    case 'armed':
      applyArmed(msg);
      break;
    case 'trigger':
      onTrigger(msg);
      break;
    case 'path':
      onPath(msg);
      break;
    case 'source':
      // Source was switched (possibly from elsewhere); resync if needed.
      if (msg.source && msg.source !== app.source) {
        onCleared();
        app.cam = null;
        reloadSource(msg.source);
      }
      break;
    case 'cleared':
      onCleared();
      break;
  }
}

function onPath(msg) {
  app.path = msg.path || [];
  app.map.drawPath(app.path);
  renderPath();
}

function renderPath() {
  const panel = el('path-panel');
  if (!app.path.length) { panel.classList.add('hidden'); return; }
  panel.classList.remove('hidden');
  const hops = app.path.length - 1;
  el('path-meta').textContent = hops
    ? `${hops} hop${hops > 1 ? 's' : ''} · ${totalPathMeters()}`
    : 'armed — awaiting first sighting';
  el('path-body').innerHTML = app.path.map((p, i) => {
    const sub = i === 0
      ? 'selected here'
      : `+${Math.round(p.elapsed_s)}s · ${hopMeters(i)} · sim ${p.similarity ?? '—'}`;
    return `<div class="hop ${i === 0 ? 'src' : ''}" data-id="${p.camera_id}">
        <span class="n">${i}</span>
        <span class="info"><span class="nm">${escapeHtml(p.name || p.camera_id)}</span>
          <span class="sub">${sub}</span></span>
      </div>`;
  }).join('');
  el('path-body').querySelectorAll('.hop').forEach((n) => {
    n.onclick = () => { const c = app.cams.get(n.dataset.id); if (c) pickCamera(c); };
  });
}

function haversine(a, b) {
  const R = 6371000, toR = Math.PI / 180;
  const dLat = (b.lat - a.lat) * toR, dLon = (b.lon - a.lon) * toR;
  const s = Math.sin(dLat / 2) ** 2 +
    Math.cos(a.lat * toR) * Math.cos(b.lat * toR) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(s));
}
function hopMeters(i) {
  if (i < 1) return '';
  return `${Math.round(haversine(app.path[i - 1], app.path[i]))}m`;
}
function totalPathMeters() {
  let m = 0;
  for (let i = 1; i < app.path.length; i++) m += haversine(app.path[i - 1], app.path[i]);
  return m >= 1000 ? `${(m / 1000).toFixed(1)}km total` : `${Math.round(m)}m total`;
}

function onTrigger(ev) {
  app.triggered.add(ev.camera_id);
  app.hit = { camera_id: ev.camera_id, track_id: ev.track_id };
  app.map.setState(ev.camera_id, 'hit');
  logEvent('trigger',
    `<b>MATCH</b> ${ev.label} at ${escapeHtml(shortName(ev.camera_id))} ` +
    `· sim ${ev.similarity} · +${ev.elapsed_s}s · ${Math.round(ev.distance_m)}m`);
  renderTarget();

  // Auto-follow the car to the camera that caught it — but only while we're
  // still following. Once the user has manually picked a camera, we just flag
  // the sighting on the map/log/path and leave their view alone.
  const cam = app.cams.get(ev.camera_id);
  if (cam && app.following) {
    openCamera(cam, { manual: false, matchLabel: ev.label });
  }
}

function onCleared() {
  app.target = null;
  app.candidates = [];
  app.triggered.clear();
  app.path = [];
  app.following = false;
  app.hit = null;
  app.map.resetStates(new Set(app.cam ? [app.cam.id] : []));
  app.map.clearCorridors();
  app.map.clearPath();
  if (app.cam) app.map.setState(app.cam.id, 'viewing');
  el('target-panel').classList.add('hidden');
  el('path-panel').classList.add('hidden');
  if (app.lastState) drawBoxes(app.lastState);   // drop target/hit highlights
}

el('clear-btn').onclick = () => api.clearTarget();
el('mode-btn').onclick = () =>
  setFeedMode(app.feedMode === 'live' ? 'inspect' : 'live');
// Clicking the looping video drops into inspect mode so boxes become selectable.
el('feed-video').onclick = () => setFeedMode('inspect');

// --- helpers ------------------------------------------------------------- //
function shortName(id) {
  const c = app.cams.get(id);
  return c ? c.name : id;
}
function fmtWin(c) {
  const a = Math.round(c.eta_min_s), b = Math.round(c.eta_max_s);
  if (b >= 60) return `ETA ${Math.round(a / 60)}–${Math.round(b / 60)}min`;
  return `ETA ${a}–${b}s`;
}
function logEvent(kind, html) {
  const d = document.createElement('div');
  d.className = `ev ${kind}`;
  const now = new Date();
  const t = now.toTimeString().slice(0, 8);
  d.innerHTML = `<span class="t">${t}</span>${html}`;
  const box = el('events');
  box.prepend(d);
  while (box.children.length > 40) box.lastChild.remove();
}
function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

init();
