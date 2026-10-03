// Thin client over the Relay backend. Paths are same-origin (Vite proxies
// /api and /ws to :8000 in dev; in production the backend serves this app).

export async function getStatus() {
  return (await fetch('/api/status')).json();
}

export async function getCameras() {
  return (await fetch('/api/cameras')).json();
}

export async function viewCamera(id) {
  return (await fetch(`/api/view/${encodeURIComponent(id)}`, { method: 'POST' })).json();
}

export async function getState(id) {
  const r = await fetch(`/api/camera/${encodeURIComponent(id)}/state`);
  if (!r.ok) return null;
  return r.json();
}

export function frameUrl(id, seq) {
  return `/api/camera/${encodeURIComponent(id)}/frame.jpg?seq=${seq}`;
}

export function clipUrl(id, seq) {
  return `/api/camera/${encodeURIComponent(id)}/clip.mp4?seq=${seq}`;
}

export async function selectTarget(cameraId, trackId) {
  const r = await fetch('/api/select', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ camera_id: cameraId, track_id: trackId }),
  });
  if (!r.ok) throw new Error((await r.json()).detail || 'select failed');
  return r.json();
}

export async function clearTarget() {
  return (await fetch('/api/clear', { method: 'POST' })).json();
}

export async function getSources() {
  return (await fetch('/api/sources')).json();
}

export async function setSource(name) {
  const r = await fetch(`/api/source/${encodeURIComponent(name)}`, { method: 'POST' });
  if (!r.ok) throw new Error('source switch failed');
  return r.json();
}

// Auto-reconnecting WebSocket. `onEvent(msg)` receives each decoded event.
export function connectWS(onEvent, onOpen, onClose) {
  let ws, closed = false;
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const url = `${proto}://${location.host}/ws`;

  function open() {
    ws = new WebSocket(url);
    ws.onopen = () => onOpen && onOpen();
    ws.onmessage = (e) => {
      try { onEvent(JSON.parse(e.data)); } catch (_) {}
    };
    ws.onclose = () => {
      onClose && onClose();
      if (!closed) setTimeout(open, 1500);
    };
    ws.onerror = () => ws.close();
  }
  open();

  return {
    send: (obj) => ws && ws.readyState === 1 && ws.send(JSON.stringify(obj)),
    close: () => { closed = true; ws && ws.close(); },
  };
}
