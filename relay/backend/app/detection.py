"""Vehicle detection.

Public traffic feeds are low-resolution (352x288) and update slowly, so a single
still frame frequently catches an empty instant even on a busy road. We instead
sample several frames from the camera's short MP4 clip, run YOLO on each, and:

  * expose the *richest* frame (most vehicles) as the display frame, so the UI
    always shows something to click and the boxes align with the frame shown;
  * keep every sampled frame's detections (with appearance embeddings) so the
    re-ID matcher can catch the target in whichever frame it appears.

A still-image source degrades to a single sampled frame. ByteTrack-style
persistent IDs across the clip are a documented extension (ultralytics is
already installed); they are not required here because cross-camera matching is
driven by re-ID embeddings, not by within-camera track continuity.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

from .config import settings
from .models import Track
from .reid import ReID

log = logging.getLogger("relay.detection")

_COCO = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}
# Cap embeddings per frame to the largest vehicles — keeps cost bounded on busy
# frames and small/distant boxes make poor re-ID crops anyway.
_MAX_EMBED_PER_FRAME = 12


@dataclass
class FrameResult:
    frame_w: int
    frame_h: int
    jpeg: bytes | None
    tracks: list[Track]


@dataclass
class ClipResult:
    display: FrameResult                       # richest frame, for the UI
    frames: list[FrameResult] = field(default_factory=list)  # all, for matching


class Detector:
    def __init__(self, reid: ReID) -> None:
        self.reid = reid
        self._model = None
        self.available = False
        self._load()

    def _load(self) -> None:
        try:
            from ultralytics import YOLO
        except Exception as exc:
            log.warning("detection disabled (ultralytics unavailable): %s", exc)
            return
        try:
            self._model = YOLO(settings.yolo_weights)
            self.available = True
            log.info("detector loaded: %s", settings.yolo_weights)
        except Exception as exc:
            log.error("failed to load YOLO weights %s: %s", settings.yolo_weights, exc)

    # ------------------------------------------------------------------ #
    def analyze_clip(self, path: str, k: int | None = None) -> ClipResult | None:
        """Sample up to k frames from a clip and detect vehicles in each."""
        if not self.available:
            return None
        import cv2

        k = k or settings.frames_per_clip
        cap = cv2.VideoCapture(path)
        n = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        if n <= 0:
            # Some containers don't report a count; read sequentially instead.
            frames = self._read_all(cap)
        else:
            idxs = self._sample_indices(n, k)
            frames = []
            for i in idxs:
                cap.set(cv2.CAP_PROP_POS_FRAMES, i)
                ok, f = cap.read()
                if ok:
                    frames.append(f)
        cap.release()
        if not frames:
            return None
        return self._analyze_frames(frames)

    def analyze_image(self, jpeg_bytes: bytes) -> ClipResult | None:
        if not self.available:
            return None
        import cv2
        arr = np.frombuffer(jpeg_bytes, np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return None
        return self._analyze_frames([frame])

    # ------------------------------------------------------------------ #
    def _analyze_frames(self, frames: list[np.ndarray]) -> ClipResult:
        import cv2
        results: list[FrameResult] = []
        for frame in frames:
            fh, fw = frame.shape[:2]
            res = self._model.predict(
                source=frame, classes=list(settings.vehicle_classes),
                conf=settings.det_conf, verbose=False,
            )[0]
            tracks = self._tracks_from_result(res, frame)
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
            results.append(FrameResult(
                frame_w=fw, frame_h=fh,
                jpeg=buf.tobytes() if ok else None, tracks=tracks))
        display = max(results, key=lambda r: len(r.tracks))
        return ClipResult(display=display, frames=results)

    def _tracks_from_result(self, res, frame: np.ndarray) -> list[Track]:
        boxes = res.boxes
        if boxes is None or len(boxes) == 0:
            return []
        xyxy = boxes.xyxy.cpu().numpy()
        clss = boxes.cls.cpu().numpy().astype(int)
        confs = boxes.conf.cpu().numpy()
        order = np.argsort(-((xyxy[:, 2] - xyxy[:, 0]) * (xyxy[:, 3] - xyxy[:, 1])))
        tracks: list[Track] = []
        for rank, i in enumerate(order):
            x1, y1, x2, y2 = xyxy[i]
            cls = int(clss[i])
            crop = self._crop(frame, x1, y1, x2, y2)
            # Only the largest few get the (costlier) deep embedding.
            embed = (self.reid.embed(crop)
                     if self.reid.available and rank < _MAX_EMBED_PER_FRAME else None)
            tracks.append(Track(
                track_id=rank,  # per-frame index; stable within one frame
                cls=cls,
                label=_COCO.get(cls, str(cls)),
                score=float(confs[i]),
                bbox=(float(x1), float(y1), float(x2 - x1), float(y2 - y1)),
                embedding=embed,
                color_hist=self.reid.color_hist(crop),
            ))
        return tracks

    # ------------------------------------------------------------------ #
    @staticmethod
    def _sample_indices(n: int, k: int) -> list[int]:
        if k >= n:
            return list(range(n))
        step = n / k
        return [min(n - 1, int(i * step)) for i in range(k)]

    @staticmethod
    def _read_all(cap) -> list[np.ndarray]:
        frames, ok = [], True
        while ok:
            ok, f = cap.read()
            if ok:
                frames.append(f)
        # Thin to a manageable count.
        if len(frames) > settings.frames_per_clip:
            idxs = Detector._sample_indices(len(frames), settings.frames_per_clip)
            frames = [frames[i] for i in idxs]
        return frames

    @staticmethod
    def _crop(frame: np.ndarray, x1, y1, x2, y2) -> np.ndarray:
        h, w = frame.shape[:2]
        xa, ya = max(0, int(x1)), max(0, int(y1))
        xb, yb = min(w, int(x2)), min(h, int(y2))
        if xb <= xa or yb <= ya:
            return np.empty((0, 0, 3), np.uint8)
        return frame[ya:yb, xa:xb]
