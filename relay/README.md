# RELAY — camera-to-camera vehicle handoff tracker

Open a live traffic camera, **click a vehicle**, and RELAY arms the cameras
**down the road** and alerts you when that vehicle is re-identified passing the
next one.

It runs on real, public, geolocated cameras. Two sources are built in, both
keyless — pick one with `RELAY_SOURCE`:

- **`austin`** (default): ~820 City of Austin traffic cameras, 1920×1080 stills
  with GPS. High resolution → the best detection and re-ID quality.
- **`tfl`**: ~890 [Transport for London JamCams](https://api.tfl.gov.uk/),
  small (352×288) stills plus short MP4 clips across London.

A Python computer-vision backend does the detection and re-identification; a
browser front-end shows the map, the feeds, and the handoff. The map
auto-centers on whichever city's cameras are loaded.

> Adapted from the camera/geo ideas in **God's Eye View**, rebuilt around one
> focused capability: following a single vehicle across a camera network.

---

## What it does

1. **Map of live cameras.** Click any marker to open that camera's feed. The
   backend fetches the feed, runs **YOLO** vehicle detection, and overlays
   clickable boxes on the frame.
2. **Pick a target.** Click a vehicle's box. The backend captures that
   vehicle's **appearance embedding** (a re-ID feature vector) and colour
   signature.
3. **Arm the road ahead.** It computes the plausible **next cameras down the
   road** — nearest cameras within range, preferring those aligned with the
   source camera's facing direction — and starts scanning each one. Dashed
   corridors on the map show the armed links; each armed camera gets an
   **arrival-time window** from its distance.
4. **Trigger on re-identification.** When a vehicle at an armed camera matches
   the target (cosine similarity over the threshold) **within its arrival
   window**, RELAY fires a `TRIGGER`: the map marker flashes red, the feed jumps
   to that camera with the matched vehicle boxed, and the event is logged.
5. **Plot the path + keep following (auto-relay).** Each confirmed sighting is
   added to the car's **path**, drawn on the map as numbered waypoints joined by
   a line, with a journey timeline (per-hop time, distance, similarity) in the
   side panel. RELAY then **re-anchors to the matched vehicle and arms the new
   camera's downstream set**, following the car hop by hop across the network
   (capped by `RELAY_MAX_HOPS`).

Switch cities any time with the **London / Austin** toggle in the top bar.

## Architecture

```
  TfL JamCams ──► Backend (FastAPI, worker thread)                 Frontend (Vite + Leaflet)
  (video+GPS)      │  cameras.py   source adapter / registry        │  map.js    camera markers, states, corridors
                   │  detection.py YOLO vehicle detection           │  feed      clickable detection boxes
                   │  reid.py      appearance embedding + colour     │  main.js   target card, event log
                   │  topology.py  "next camera down the road"       │  api.js    REST + WebSocket
                   │  engine.py    poll → detect → match → trigger   │
                   └─ main.py      REST + WebSocket  ───────────────►┘  (ws: state / armed / trigger)
```

- **One worker thread** does all CV (torch is CPU-bound and not asyncio
  friendly); FastAPI only reads shared state and relays events over WebSocket.
- **Only active cameras are processed** — the one you're viewing plus the armed
  handoff candidates — so the load scales with what you're doing, not with 890
  cameras.

## Quick start

Requirements: **Python 3.10+** and **Node 18+**. First run downloads the YOLO
weights (~6 MB) and a ResNet-50 (~100 MB).

```bash
cd relay-tracker
scripts/setup.sh      # venv + backend deps (incl. torch) + frontend deps
scripts/dev.sh        # backend :8000 + frontend :5173
```

Open **http://localhost:5173**. Click a camera (busy A-roads like the **A406**,
**A40**, **Hanger Lane** are good bets), then click a vehicle to start tracking.

> **Tip for a reliable demo:** pick a source camera with several vehicles and an
> armed camera that's close (an adjacent junction), so the same vehicle actually
> reaches it quickly and inside the arrival window.

To run without the dev proxy, build the frontend and let the backend serve it:

```bash
cd frontend && npm run build          # emits frontend/dist
cd ../backend && ./run.sh             # serves API + the built app at :8000
```

## How the hard parts are handled (and their limits)

**This is genuinely hard, and the results are not perfect — by design of the
problem, not a bug.** Being honest about where it's strong and weak:

- **Detection** is solid: YOLO on even a 352×288 clip reliably finds vehicles.
  A single still often catches an empty instant, so RELAY samples several frames
  from each camera's short MP4 clip and uses the richest one.
- **Cross-camera re-ID is the weak link.** The default embedding is a
  general-purpose ImageNet ResNet-50, which is only moderately discriminative on
  tiny, low-resolution car crops — two dark hatchbacks can score similarly. Two
  defenses keep false positives down:
  - a **colour-histogram** signal blended with the embedding, and
  - the **arrival-time window**: a match at a camera 1 km away two seconds after
    you selected the target is physically impossible, so it's rejected as a
    look-alike that was already parked there.
  - For a real upgrade, install a purpose-built re-ID model: `pip install
    torchreid` and set `RELAY_REID_BACKEND=torchreid` (OSNet). The hook is
    already in `reid.py`.
- **"Next camera down the road"** is an approximation. We don't have lane-level
  road topology for arbitrary public cameras, and an uncalibrated street camera
  can't give a vehicle's true world heading. So RELAY arms a *small set* of
  plausible downstream cameras (near + roughly in the source camera's facing
  direction) and lets whichever one actually sees the car fire — which is how
  you'd do it in practice. Snapping cameras to OSM ways for true topological
  "downstream" is the natural next step (see below).
- **Feed cadence — matters a lot for live triggers.** Public feeds refresh on
  their own schedule, and this differs sharply by source:
  - **Austin** stills are HD but refresh *slowly* — often only every 30–120s,
    and variably per camera (some sat unchanged for 2+ minutes in testing). Great
    for detection/selection quality; a specific car usually passes *between*
    refreshes, so live cross-camera triggers are unlikely. The feed will look
    near-static — that's the source, not the app. RELAY hashes each frame and
    only re-processes on real change, and the feed panel shows "updated Xs ago".
  - **TfL** serves 10-second MP4 clips (continuous motion at 25fps). Lower
    resolution, but far more likely to actually capture a moving vehicle — so
    **`RELAY_SOURCE=tfl` is the better choice for demonstrating the live
    handoff/trigger.**
  Either way the detection → select → arm → re-ID pipeline is identical; only the
  odds of catching a given car at the next camera differ.

## Configuration

All via environment variables (see `backend/app/config.py`):

| Variable | Default | Meaning |
|---|---|---|
| `RELAY_SOURCE` | `austin` | camera network: `austin` or `tfl` |
| `RELAY_REID_THRESHOLD` | `0.72` | similarity needed to call it a match |
| `RELAY_REID_BACKEND` | `auto` | `auto` / `torchreid` (OSNet) |
| `RELAY_HANDOFF_RADIUS_M` | `1800` | how far downstream to consider cameras |
| `RELAY_MAX_HANDOFF_CAMS` | `5` | max cameras armed at once |
| `RELAY_SPEED_MIN` / `RELAY_SPEED_MAX` | `4` / `25` m/s | arrival-window speed band |
| `RELAY_BEARING_TOL` | `75` | ° off the camera's view direction still "downstream" |
| `RELAY_POLL_INTERVAL` | `4.0` | seconds between re-fetching an active camera |
| `RELAY_USE_VIDEO` | `1` | sample clip frames (`0` = single still) |
| `TFL_APP_KEY` | – | optional TfL key to raise rate limits |

## Swapping the camera source

Public cameras vary by region. To use a different network (a state DOT, a
city 511 feed, your own RTSP gateway), implement the small `CameraSource`
protocol in `backend/app/cameras.py` — `list_cameras()` returning `Camera`
records with `lat`/`lon`/`image_url`/`video_url` — and register it. Everything
downstream (detection, re-ID, topology, triggers) is source-agnostic.

## Ideas to extend

- **True road topology:** snap cameras to OpenStreetMap ways (Overpass), group
  by road, and define "downstream" as the next camera along the way in the
  vehicle's direction, replacing the bearing heuristic.
- **torchreid / VeRi-trained weights** for much stronger vehicle re-ID.
- **Persistent within-camera tracking** (ultralytics ByteTrack) for stable IDs
  and velocity while you watch a single camera.
- **More source cities:** add another `CameraSource` adapter and it joins the
  in-app source toggle automatically.

## Legal / ethical note

Uses only public, officially-published camera feeds under their terms. Traffic
cameras are low-resolution and not designed to identify individuals; this is a
demonstration of multi-camera vehicle **re-identification** as a computer-vision
problem. Don't use it to surveil or track people.
