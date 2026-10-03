"""Runtime configuration. Everything is overridable via environment variables
so the app has sane defaults but stays deployable."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _f(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _i(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


@dataclass(frozen=True)
class Settings:
    # --- Camera source ---
    #   "tfl"    : ~890 London JamCams (small stills + short MP4 clips). Clips
    #              capture real motion, so the live handoff can actually fire.
    #   "austin" : ~820 City of Austin traffic cameras (1920x1080 stills, GPS) —
    #              HD but slow-refreshing, so handoffs rarely trigger live.
    # Both are keyless. Override with RELAY_SOURCE=tfl / austin, or switch at
    # runtime from the UI.
    camera_source: str = os.environ.get("RELAY_SOURCE", "tfl")
    tfl_app_key: str = os.environ.get("TFL_APP_KEY", "")  # optional, raises rate limits

    # --- Ingest / processing ---
    # Seconds between re-fetching an active camera's feed.
    poll_interval_s: float = _f("RELAY_POLL_INTERVAL", 4.0)
    # Use the MP4 clip (sample several frames) rather than a single still.
    # Default on: a single low-res still often catches an empty instant, while
    # the ~10s clip reliably contains the vehicles on the road.
    use_video: bool = os.environ.get("RELAY_USE_VIDEO", "1") != "0"
    # How many frames to sample across each clip for detection.
    frames_per_clip: int = _i("RELAY_FRAMES_PER_CLIP", 6)

    # --- Detection ---
    # YOLO weights (auto-downloaded by ultralytics on first use).
    yolo_weights: str = os.environ.get("RELAY_YOLO_WEIGHTS", "yolov8n.pt")
    det_conf: float = _f("RELAY_DET_CONF", 0.35)
    # COCO vehicle classes: car, motorcycle, bus, truck.
    vehicle_classes: tuple[int, ...] = (2, 3, 5, 7)

    # --- Re-identification ---
    # Cosine-similarity threshold above which a candidate counts as the target.
    # Lowered from 0.72 to favour tracking *continuity* (recall) over precision:
    # on slow/sparse feeds, a stricter threshold loses the car at the next hop.
    reid_threshold: float = _f("RELAY_REID_THRESHOLD", 0.60)
    # Weight of the deep embedding vs. the HSV colour histogram when both exist.
    reid_embed_weight: float = _f("RELAY_REID_EMBED_WEIGHT", 0.7)

    # --- Topology ("next camera down the road") ---
    # Consider cameras within this radius as handoff candidates.
    handoff_radius_m: float = _f("RELAY_HANDOFF_RADIUS_M", 1800.0)
    # Keep at most this many downstream candidates armed at once.
    max_handoff_cams: int = _i("RELAY_MAX_HANDOFF_CAMS", 5)
    # Assumed vehicle speed band (m/s) used to derive the arrival time window.
    speed_min_ms: float = _f("RELAY_SPEED_MIN", 4.0)    # ~14 km/h (congested)
    speed_max_ms: float = _f("RELAY_SPEED_MAX", 25.0)   # ~90 km/h (free flow)
    # Grace padding added to the LATE end of the arrival window (seconds),
    # for stops, congestion and slow turns.
    window_pad_s: float = _f("RELAY_WINDOW_PAD", 45.0)
    # Small tolerance on the EARLY end — a vehicle can't plausibly arrive at a
    # downstream camera before (distance / max_speed) minus this. Keeping this
    # small is what rejects a look-alike that was already parked at the far
    # camera when the target was selected.
    window_early_tol_s: float = _f("RELAY_WINDOW_EARLY_TOL", 8.0)
    # A camera is "downstream" if the bearing to it is within this many degrees
    # of the source camera's reported view direction.
    bearing_tolerance_deg: float = _f("RELAY_BEARING_TOL", 75.0)

    # --- Relay (multi-hop following) ---
    # On a confirmed re-identification, re-anchor to the matched vehicle and arm
    # the NEW camera's downstream set, so the car is followed hop by hop.
    relay_auto: bool = os.environ.get("RELAY_AUTO", "1") != "0"
    # Stop after this many hops to bound compounding re-ID drift / runaway.
    relay_max_hops: int = _i("RELAY_MAX_HOPS", 12)
    # Keep a camera armed this long past its latest arrival time, so slow feeds
    # get several refresh cycles to catch the car before we give up on it.
    arm_linger_s: float = _f("RELAY_ARM_LINGER", 180.0)

    # --- Server ---
    host: str = os.environ.get("RELAY_HOST", "127.0.0.1")
    port: int = _i("RELAY_PORT", 8000)


settings = Settings()
