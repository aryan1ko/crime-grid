import L from 'leaflet';
import 'leaflet/dist/leaflet.css';

// Camera markers as lightweight DivIcons so we can restyle them by state
// (idle / viewing / source / armed / hit) without swapping image assets.
export class CameraMap {
  constructor(elId, { onPick }) {
    this.onPick = onPick;
    this.markers = new Map(); // id -> L.marker
    this.state = new Map();   // id -> state class
    this.lines = [];          // handoff corridor lines

    this.map = L.map(elId, { zoomControl: true, preferCanvas: true })
      .setView([51.509, -0.118], 12);

    this.pathLayer = L.layerGroup().addTo(this.map);  // confirmed journey

    // Esri Dark Gray Canvas — a dark basemap that needs no API key. (Swap the
    // path to `World_Imagery/MapServer` for keyless satellite imagery.)
    L.tileLayer(
      'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}',
      {
        attribution: 'Tiles &copy; Esri · public traffic cameras',
        maxZoom: 16,
      }
    ).addTo(this.map);
  }

  addCameras(cams) {
    const pts = [];
    for (const c of cams) {
      if (c.lat == null || c.lon == null) continue;
      const m = L.marker([c.lat, c.lon], { icon: this._icon('idle') })
        .addTo(this.map);
      m.bindPopup(`<b>${escapeHtml(c.name)}</b><br>${c.id}`);
      m.on('click', () => this.onPick(c));
      this.markers.set(c.id, m);
      pts.push([c.lat, c.lon]);
    }
    // Fit the view to wherever the cameras actually are (London, Austin, …).
    if (pts.length) this.map.fitBounds(pts, { padding: [30, 30] });
  }

  _icon(cls, size = 12) {
    return L.divIcon({
      className: '',
      html: `<div class="cam-dot ${cls}" style="width:${size}px;height:${size}px"></div>`,
      iconSize: [size, size],
      iconAnchor: [size / 2, size / 2],
    });
  }

  setState(id, cls, size) {
    const m = this.markers.get(id);
    if (!m) return;
    this.state.set(id, cls);
    m.setIcon(this._icon(cls, size || (cls === 'idle' ? 12 : 16)));
    if (cls !== 'idle') m.setZIndexOffset(cls === 'hit' ? 1000 : 500);
  }

  // Reset every non-idle marker back to idle.
  resetStates(except = new Set()) {
    for (const [id, cls] of this.state) {
      if (cls !== 'idle' && !except.has(id)) this.setState(id, 'idle');
    }
  }

  focus(lat, lon, zoom = 14) {
    this.map.flyTo([lat, lon], zoom, { duration: 0.6 });
  }

  // Draw corridor lines from the source camera to each armed candidate.
  drawCorridors(source, candidates) {
    this.clearCorridors();
    for (const c of candidates) {
      if (c.lat == null || c.lon == null) continue;
      const line = L.polyline(
        [[source.lat, source.lon], [c.lat, c.lon]],
        { color: '#3aa0ff', weight: 1.5, opacity: 0.5, dashArray: '4 6' }
      ).addTo(this.map);
      this.lines.push(line);
    }
  }

  clearCorridors() {
    for (const l of this.lines) l.remove();
    this.lines = [];
  }

  // Draw the car's confirmed path: source → each re-identified sighting, in order.
  drawPath(points) {
    this.pathLayer.clearLayers();
    const pts = points.filter((p) => p.lat != null && p.lon != null);
    if (pts.length === 0) return;
    const latlngs = pts.map((p) => [p.lat, p.lon]);
    if (latlngs.length > 1) {
      // glow underlay + bright path line
      L.polyline(latlngs, { color: '#ffb020', weight: 8, opacity: 0.18 })
        .addTo(this.pathLayer);
      L.polyline(latlngs, { color: '#ffb020', weight: 3, opacity: 0.95 })
        .addTo(this.pathLayer);
    }
    pts.forEach((p, i) => {
      const isSource = i === 0;
      L.marker([p.lat, p.lon], {
        icon: L.divIcon({
          className: '',
          html: `<div class="wp ${isSource ? 'wp-src' : ''}">${i}</div>`,
          iconSize: [20, 20], iconAnchor: [10, 10],
        }),
        zIndexOffset: 1200,
      }).addTo(this.pathLayer)
        .bindPopup(`<b>#${i} ${escapeHtml(p.name || p.camera_id)}</b><br>` +
          (isSource ? 'selected here' :
            `+${Math.round(p.elapsed_s)}s · sim ${p.similarity ?? '—'}`));
    });
  }

  clearPath() {
    this.pathLayer.clearLayers();
  }

  // Remove every camera marker (used when switching source/city).
  clearCameras() {
    for (const m of this.markers.values()) m.remove();
    this.markers.clear();
    this.state.clear();
    this.clearCorridors();
    this.clearPath();
  }
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"]/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}
