"""Shared dataclasses / pydantic models for the API surface."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from pydantic import BaseModel


# --------------------------------------------------------------------------- #
# Internal engine types (not serialized directly)
# --------------------------------------------------------------------------- #

@dataclass
class Camera:
    """A geolocated traffic camera from the source adapter."""
    id: str
    name: str
    lat: float
    lon: float
    image_url: str
    video_url: Optional[str] = None
    view: Optional[str] = None       # compass direction the camera faces, e.g. "West"
    available: bool = True


@dataclass
class Track:
    """One tracked vehicle within a single camera's most recent frame."""
    track_id: int
    cls: int                          # COCO class id
    label: str                        # "car", "truck", ...
    score: float
    # Bounding box in processed-frame pixel coordinates: x, y, w, h.
    bbox: tuple[float, float, float, float]
    embedding: Optional[list[float]] = None   # L2-normalized appearance vector
    color_hist: Optional[list[float]] = None  # normalized HSV histogram


@dataclass
class CameraState:
    """Latest processed result for one camera."""
    camera_id: str
    frame_w: int = 0
    frame_h: int = 0
    seq: int = 0                      # increments each time the frame is refreshed
    updated_at: float = 0.0
    tracks: list[Track] = field(default_factory=list)
    jpeg: Optional[bytes] = None      # latest annotated-free frame bytes
    error: Optional[str] = None


@dataclass
class Target:
    """The vehicle the user is following."""
    source_camera_id: str
    track_id: int
    cls: int
    label: str
    embedding: Optional[list[float]]
    color_hist: Optional[list[float]]
    selected_at: float


@dataclass
class HandoffCandidate:
    camera_id: str
    distance_m: float
    bearing_deg: float
    aligned: bool                     # roughly in the camera's view direction
    eta_min_s: float                  # earliest plausible arrival (from selection)
    eta_max_s: float                  # latest plausible arrival


# --------------------------------------------------------------------------- #
# API response / request models
# --------------------------------------------------------------------------- #

class SelectRequest(BaseModel):
    camera_id: str
    track_id: int


class TrackOut(BaseModel):
    track_id: int
    label: str
    score: float
    bbox: list[float]


class CameraStateOut(BaseModel):
    camera_id: str
    frame_w: int
    frame_h: int
    seq: int
    updated_at: float
    tracks: list[TrackOut]
    error: Optional[str] = None
