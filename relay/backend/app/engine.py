"""The relay engine — orchestration.

A single background worker thread polls the set of *active* cameras (the one the
user is viewing, plus any armed downstream candidates), runs detection on each,
and keeps the latest state. When the user selects a target vehicle, the engine
computes downstream handoff cameras, arms them, and watches their detections for
a re-ID match within the expected arrival window. A match emits a `trigger`
event to the WebSocket layer.

Torch inference is CPU-bound and not asyncio-friendly, so all CV work happens on
this worker thread; the asyncio/FastAPI side only reads shared state (under a
lock) and relays events.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import time
from typing import Callable, Optional

import httpx

from .cameras import CameraRegistry
from .config import settings
from .detection import Detector
from .models import Camera, CameraState, HandoffCandidate, Target, Track
from .reid import ReID, similarity
from .topology import downstream_candidates

log = logging.getLogger("relay.engine")

EmitFn = Callable[[dict], None]


class _Armed:
    def __init__(self, cand: HandoffCandidate, armed_at: float) -> None:
        self.cand = cand
        self.armed_at = armed_at
        self.best_sim = 0.0
        self.triggered = False


class RelayEngine:
    def __init__(self, registry: CameraRegistry) -> None:
        self.registry = registry
        self.reid = ReID()
        self.detector = Detector(self.reid)

        self._lock = threading.RLock()
        self._states: dict[str, CameraState] = {}
        self._src_hash: dict[str, str] = {}      # cam_id -> last source-frame hash
        self._clips: dict[str, bytes] = {}       # cam_id -> last raw MP4 clip bytes
        self._active: dict[str, float] = {}      # cam_id -> last processed (0 = due now)
        self._armed: dict[str, _Armed] = {}
        self._target: Optional[Target] = None
        self._viewed: Optional[str] = None
        self._path: list[dict] = []              # ordered confirmed sightings
        self.source_name = settings.camera_source

        self._emit: Optional[EmitFn] = None
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="relay-worker", daemon=True)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def start(self, emit: EmitFn) -> None:
        self._emit = emit
        self._thread.start()
        log.info("engine started (detector=%s, reid=%s)",
                 self.detector.available, self.reid.kind)

    def stop(self) -> None:
        self._stop.set()
        try:
            self._client.close()
        except Exception:
            pass

    @property
    def status(self) -> dict:
        return {
            "detector_available": self.detector.available,
            "reid_backend": self.reid.kind,
            "yolo_weights": settings.yolo_weights,
            "camera_count": len(self.registry),
            "source": self.source_name,
            "reid_threshold": settings.reid_threshold,
        }

    def reset_for_new_source(self, source_name: str) -> None:
        """Clear all tracking state after the camera registry is reloaded."""
        with self._lock:
            self.source_name = source_name
            self._states.clear()
            self._src_hash.clear()
            self._active.clear()
            self._armed = {}
            self._target = None
            self._viewed = None
            self._path = []
        self._broadcast({"type": "source", "source": source_name,
                         "camera_count": len(self.registry)})

    # ------------------------------------------------------------------ #
    # Public commands (called from the asyncio side)
    # ------------------------------------------------------------------ #
    def set_viewed(self, cam_id: str) -> bool:
        if self.registry.get(cam_id) is None:
            return False
        with self._lock:
            self._viewed = cam_id
            self._active.setdefault(cam_id, 0.0)  # due immediately
        return True

    def get_state(self, cam_id: str) -> Optional[CameraState]:
        with self._lock:
            st = self._states.get(cam_id)
        return st

    def get_frame(self, cam_id: str) -> Optional[bytes]:
        with self._lock:
            st = self._states.get(cam_id)
            return st.jpeg if st else None

    def get_clip(self, cam_id: str) -> Optional[bytes]:
        with self._lock:
            return self._clips.get(cam_id)

    def select_target(self, cam_id: str, track_id: int) -> Optional[dict]:
        cam = self.registry.get(cam_id)
        if cam is None:
            return None
        with self._lock:
            st = self._states.get(cam_id)
            if st is None:
                return None
            track = next((t for t in st.tracks if t.track_id == track_id), None)
            if track is None:
                return None
            self._target = Target(
                source_camera_id=cam_id,
                track_id=track_id,
                cls=track.cls,
                label=track.label,
                embedding=track.embedding,
                color_hist=track.color_hist,
                selected_at=time.time(),
            )
            cands = downstream_candidates(cam, self.registry.all())
            self._armed = {c.camera_id: _Armed(c, time.time()) for c in cands}
            for cid in self._armed:
                self._active.setdefault(cid, 0.0)  # arm => poll immediately
            # The path starts at the camera where the vehicle was selected.
            self._path = [{
                "camera_id": cam_id, "name": cam.name, "lat": cam.lat, "lon": cam.lon,
                "t": time.time(), "elapsed_s": 0.0, "kind": "source",
                "label": track.label,
            }]
            payload = {
                "type": "armed",
                "target": {
                    "source_camera_id": cam_id,
                    "track_id": track_id,
                    "label": track.label,
                    "has_embedding": track.embedding is not None,
                },
                "candidates": [self._cand_dict(c) for c in cands],
            }
            path_event = {"type": "path", "path": list(self._path)}
        self._broadcast(payload)
        self._broadcast(path_event)
        return payload

    def clear_target(self) -> None:
        with self._lock:
            self._target = None
            self._armed = {}
            self._path = []
        self._broadcast({"type": "cleared"})

    def get_path(self) -> list[dict]:
        with self._lock:
            return list(self._path)

    def _cand_dict(self, c: HandoffCandidate) -> dict:
        cam = self.registry.get(c.camera_id)
        return {
            "camera_id": c.camera_id,
            "name": cam.name if cam else c.camera_id,
            "lat": cam.lat if cam else None,
            "lon": cam.lon if cam else None,
            "distance_m": round(c.distance_m, 1),
            "bearing_deg": round(c.bearing_deg, 1),
            "aligned": c.aligned,
            "eta_min_s": round(c.eta_min_s, 1),
            "eta_max_s": round(c.eta_max_s, 1),
        }

    # ------------------------------------------------------------------ #
    # Worker loop
    # ------------------------------------------------------------------ #
    def _run(self) -> None:
        while not self._stop.is_set():
            cam_id = self._next_due()
            if cam_id is None:
                self._stop.wait(0.25)
                continue
            try:
                self._process_camera(cam_id)
            except Exception as exc:
                log.exception("processing %s failed: %s", cam_id, exc)
            self._expire_armed()

    def _next_due(self) -> Optional[str]:
        now = time.time()
        with self._lock:
            due = [
                (last, cid) for cid, last in self._active.items()
                if now - last >= settings.poll_interval_s
            ]
        if not due:
            return None
        due.sort()  # oldest first
        return due[0][1]

    def _process_camera(self, cam_id: str) -> None:
        cam = self.registry.get(cam_id)
        if cam is None:
            with self._lock:
                self._active.pop(cam_id, None)
            return

        with self._lock:
            self._active[cam_id] = time.time()  # mark processed up front

        fetched = self._fetch_source(cam)
        if fetched is None:
            self._set_state(CameraState(camera_id=cam_id, error="feed unavailable",
                                        updated_at=time.time()))
            return
        kind, raw, src_hash = fetched
        if kind == "video":
            with self._lock:
                self._clips[cam_id] = raw   # cache for the looping-video endpoint

        # Public feeds refresh on their own (slow) schedule — Austin stills can
        # be byte-identical for a minute. Skip re-detecting an unchanged frame:
        # it wastes CPU and would fire no new information.
        with self._lock:
            prev = self._states.get(cam_id)
            unchanged = (prev is not None and prev.error is None
                         and self._src_hash.get(cam_id) == src_hash)
        if unchanged:
            return

        clip = self._detect_source(kind, raw)
        if clip is None:
            self._set_state(CameraState(camera_id=cam_id, error="detect failed",
                                        updated_at=time.time()))
            return

        disp = clip.display
        with self._lock:
            prev = self._states.get(cam_id)
            prev_seq = prev.seq if prev else 0
            self._src_hash[cam_id] = src_hash
        state = CameraState(
            camera_id=cam_id,
            frame_w=disp.frame_w,
            frame_h=disp.frame_h,
            seq=prev_seq + 1,
            updated_at=time.time(),   # time the frame actually CHANGED
            tracks=disp.tracks,
            jpeg=disp.jpeg,
        )
        self._set_state(state)
        self._broadcast(self._state_event(state))

        # Matching happens against armed cameras only, over every sampled frame.
        with self._lock:
            is_armed = cam_id in self._armed
        if is_armed:
            self._match(cam_id, clip)

    def _fetch_source(self, cam: Camera):
        """Fetch raw feed bytes and a content hash, without detecting yet.

        Returns (kind, raw_bytes, hash) where kind is "video" or "image".
        """
        if settings.use_video and cam.video_url and self.detector.available:
            try:
                r = self._client.get(cam.video_url)
                r.raise_for_status()
                return "video", r.content, hashlib.md5(r.content).hexdigest()
            except Exception as exc:
                log.debug("video fetch failed for %s (%s); trying still", cam.id, exc)
        try:
            r = self._client.get(cam.image_url)
            r.raise_for_status()
            return "image", r.content, hashlib.md5(r.content).hexdigest()
        except Exception as exc:
            log.debug("image fetch failed for %s: %s", cam.id, exc)
            return None

    def _detect_source(self, kind: str, raw: bytes):
        if kind == "video":
            with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tf:
                tf.write(raw)
                path = tf.name
            try:
                return self.detector.analyze_clip(path)
            finally:
                try:
                    os.unlink(path)
                except OSError:
                    pass
        if self.detector.available:
            return self.detector.analyze_image(raw)
        # No detector: still expose the raw frame so the UI shows something.
        from .detection import ClipResult, FrameResult
        fr = FrameResult(frame_w=0, frame_h=0, jpeg=raw, tracks=[])
        return ClipResult(display=fr, frames=[fr])

    def _match(self, cam_id: str, clip) -> None:
        with self._lock:
            target = self._target
            armed = self._armed.get(cam_id)
        if target is None or armed is None or armed.triggered:
            return

        elapsed = time.time() - target.selected_at
        best_track: Optional[Track] = None
        best_frame = None
        best_sim = 0.0
        for fr in clip.frames:
            for t in fr.tracks:
                if t.cls != target.cls and not _compatible(t.cls, target.cls):
                    continue
                sim = similarity(target.embedding, target.color_hist,
                                 t.embedding, t.color_hist, settings.reid_embed_weight)
                if sim > best_sim:
                    best_sim, best_track, best_frame = sim, t, fr

        with self._lock:
            armed.best_sim = max(armed.best_sim, best_sim)

        if best_track is None:
            return

        # Trigger only within the plausible arrival window. The earliest bound
        # rejects a look-alike that was already sitting at a far camera when the
        # target was selected (the +2s "match" at a camera 1 km away).
        in_window = armed.cand.eta_min_s <= elapsed <= armed.cand.eta_max_s
        if best_sim >= settings.reid_threshold and in_window:
            with self._lock:
                armed.triggered = True
            # Surface the exact frame the match occurred in so the UI can show
            # the re-identified vehicle (not just any frame of the clip).
            seq = self._promote_match_frame(cam_id, best_frame, best_track)
            cam = self.registry.get(cam_id)
            event = {
                "type": "trigger",
                "camera_id": cam_id,
                "camera_name": cam.name if cam else cam_id,
                "lat": cam.lat if cam else None,
                "lon": cam.lon if cam else None,
                "track_id": best_track.track_id,
                "label": best_track.label,
                "similarity": round(best_sim, 3),
                "bbox": list(best_track.bbox),
                "frame_w": best_frame.frame_w,
                "frame_h": best_frame.frame_h,
                "seq": seq,
                "elapsed_s": round(elapsed, 1),
                "window": [round(armed.cand.eta_min_s, 1), round(armed.cand.eta_max_s, 1)],
                "distance_m": round(armed.cand.distance_m, 1),
            }
            log.info("TRIGGER: %s re-identified at %s (sim=%.3f, +%.0fs)",
                     target.label, cam_id, best_sim, elapsed)
            self._broadcast(event)
            self._record_and_relay(cam, best_track, best_sim, elapsed)

    def _record_and_relay(self, cam: Camera, track: Track, sim: float, elapsed: float) -> None:
        """Append the confirmed sighting to the path and, if auto-relay is on,
        re-anchor the target to it and arm the NEW camera's downstream set."""
        relay_payload = None
        path_event = None
        with self._lock:
            if self._target is None:
                return
            self._path.append({
                "camera_id": cam.id, "name": cam.name, "lat": cam.lat, "lon": cam.lon,
                "t": time.time(), "elapsed_s": round(elapsed, 1),
                "similarity": round(sim, 3), "kind": "sighting", "label": track.label,
            })
            path_event = {"type": "path", "path": list(self._path)}

            hops = len(self._path) - 1  # sightings since the source
            if settings.relay_auto and hops < settings.relay_max_hops:
                # Re-anchor appearance to the freshest confirmed crop and hunt
                # forward from this camera.
                self._target = Target(
                    source_camera_id=cam.id,
                    track_id=track.track_id,
                    cls=track.cls,
                    label=track.label,
                    embedding=track.embedding or self._target.embedding,
                    color_hist=track.color_hist or self._target.color_hist,
                    selected_at=time.time(),
                )
                cands = downstream_candidates(cam, self.registry.all())
                self._armed = {c.camera_id: _Armed(c, time.time()) for c in cands}
                for cid in self._armed:
                    self._active.setdefault(cid, 0.0)
                relay_payload = {
                    "type": "armed",
                    "relay": True,
                    "target": {
                        "source_camera_id": cam.id,
                        "track_id": track.track_id,
                        "label": track.label,
                        "has_embedding": self._target.embedding is not None,
                    },
                    "candidates": [self._cand_dict(c) for c in cands],
                }

        self._broadcast(path_event)
        if relay_payload:
            self._broadcast(relay_payload)

    def _promote_match_frame(self, cam_id: str, frame, track) -> int:
        """Make the matched frame the camera's current displayed state."""
        with self._lock:
            prev = self._states.get(cam_id)
            seq = (prev.seq + 1) if prev else 1
            self._states[cam_id] = CameraState(
                camera_id=cam_id, frame_w=frame.frame_w, frame_h=frame.frame_h,
                seq=seq, updated_at=time.time(), tracks=frame.tracks, jpeg=frame.jpeg,
            )
        return seq

    def _expire_armed(self) -> None:
        now = time.time()
        with self._lock:
            target = self._target
            if target is None:
                return
            expired = []
            for cid, a in self._armed.items():
                # Keep an armed camera well past its arrival window so slow feeds
                # get several refresh cycles to catch the car.
                if now - target.selected_at > a.cand.eta_max_s + settings.arm_linger_s:
                    expired.append(cid)
            for cid in expired:
                self._armed.pop(cid, None)
                if cid != self._viewed:
                    self._active.pop(cid, None)

    # ------------------------------------------------------------------ #
    def _set_state(self, state: CameraState) -> None:
        with self._lock:
            self._states[state.camera_id] = state

    def _state_event(self, state: CameraState) -> dict:
        return {
            "type": "state",
            "camera_id": state.camera_id,
            "frame_w": state.frame_w,
            "frame_h": state.frame_h,
            "seq": state.seq,
            "updated_at": state.updated_at,
            "tracks": [
                {"track_id": t.track_id, "label": t.label,
                 "score": round(t.score, 3), "bbox": list(t.bbox)}
                for t in state.tracks
            ],
        }

    def _broadcast(self, event: dict) -> None:
        if self._emit:
            try:
                self._emit(event)
            except Exception as exc:
                log.debug("emit failed: %s", exc)


def _compatible(a: int, b: int) -> bool:
    """Treat truck/bus and car/motorcycle as weakly interchangeable, since YOLO
    flips between them across angles for the same vehicle."""
    groups = [{2, 3}, {5, 7}]
    return any(a in g and b in g for g in groups)
