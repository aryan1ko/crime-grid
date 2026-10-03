"""Camera source adapters.

A source adapter knows how to (a) list geolocated cameras and (b) fetch the
latest feed bytes for one camera. Swapping in a different public-camera network
(a state DOT, a city 511, your own RTSP gateway) means implementing this one
interface.
"""

from __future__ import annotations

import logging
from typing import Protocol

import httpx

from .config import settings
from .models import Camera

log = logging.getLogger("relay.cameras")


class CameraSource(Protocol):
    name: str

    async def list_cameras(self, client: httpx.AsyncClient) -> list[Camera]: ...


class TflJamCamSource:
    """Transport for London JamCams.

    ~890 cameras, each with a still JPEG and a ~10s MP4 clip, plus a compass
    `view` direction. The MP4 clips make real within-camera tracking possible
    (a still refreshing every few seconds cannot give velocity).
    """

    name = "tfl"
    ENDPOINT = "https://api.tfl.gov.uk/Place/Type/JamCam"

    async def list_cameras(self, client: httpx.AsyncClient) -> list[Camera]:
        params = {}
        if settings.tfl_app_key:
            params["app_key"] = settings.tfl_app_key
        resp = await client.get(self.ENDPOINT, params=params, timeout=30.0)
        resp.raise_for_status()
        raw = resp.json()
        cams: list[Camera] = []
        for item in raw:
            props = {p["key"]: p["value"] for p in item.get("additionalProperties", [])}
            lat, lon = item.get("lat"), item.get("lon")
            image_url = props.get("imageUrl")
            if lat is None or lon is None or not image_url:
                continue
            cams.append(
                Camera(
                    id=item["id"],
                    name=item.get("commonName", item["id"]),
                    lat=float(lat),
                    lon=float(lon),
                    image_url=image_url,
                    video_url=props.get("videoUrl"),
                    view=props.get("view"),
                    available=str(props.get("available", "true")).lower() == "true",
                )
            )
        log.info("TfL: loaded %d cameras", len(cams))
        return cams


class AustinSource:
    """City of Austin traffic cameras (Austin Transportation / Mobility).

    Published on the Austin open-data portal (Socrata). ~820 live cameras, each
    a 1920x1080 still refreshed periodically (no video clips), with GPS. No key
    required. The feed has no camera-facing direction, so the topology layer
    falls back to nearest-neighbour handoff candidates.
    """

    name = "austin"
    ENDPOINT = "https://data.austintexas.gov/resource/b4k4-adkb.json"

    async def list_cameras(self, client: httpx.AsyncClient) -> list[Camera]:
        params = {
            "camera_status": "TURNED_ON",
            "$limit": "5000",
            "$where": "location IS NOT NULL",
        }
        resp = await client.get(self.ENDPOINT, params=params, timeout=30.0)
        resp.raise_for_status()
        raw = resp.json()
        cams: list[Camera] = []
        for item in raw:
            loc = item.get("location") or {}
            coords = loc.get("coordinates") or []
            img = item.get("screenshot_address")
            if len(coords) != 2 or not img:
                continue
            lon, lat = float(coords[0]), float(coords[1])
            cams.append(
                Camera(
                    id=str(item["camera_id"]),
                    name=(item.get("location_name") or item["camera_id"]).strip(),
                    lat=lat,
                    lon=lon,
                    image_url=img,
                    video_url=None,          # Austin is still-image only
                    view=None,               # no facing direction published
                    available=True,
                )
            )
        log.info("Austin: loaded %d live cameras", len(cams))
        return cams


_SOURCES: dict[str, CameraSource] = {
    TflJamCamSource.name: TflJamCamSource(),
    AustinSource.name: AustinSource(),
}


def available_sources() -> list[str]:
    return list(_SOURCES)


def get_source(name: str | None = None) -> CameraSource:
    name = name or settings.camera_source
    src = _SOURCES.get(name)
    if src is None:
        raise ValueError(
            f"Unknown camera source {name!r}; available: {list(_SOURCES)}"
        )
    return src


class CameraRegistry:
    """In-memory registry of all cameras, loaded once at startup."""

    def __init__(self) -> None:
        self._by_id: dict[str, Camera] = {}

    async def load(self, client: httpx.AsyncClient, source_name: str | None = None) -> None:
        cams = await get_source(source_name).list_cameras(client)
        self._by_id = {c.id: c for c in cams}

    def all(self) -> list[Camera]:
        return list(self._by_id.values())

    def get(self, cam_id: str) -> Camera | None:
        return self._by_id.get(cam_id)

    def __len__(self) -> int:
        return len(self._by_id)
