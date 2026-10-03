"""FastAPI surface: REST for commands/state, WebSocket for the live event feed."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from .cameras import CameraRegistry, available_sources
from .config import settings
from .engine import RelayEngine
from .models import CameraStateOut, SelectRequest, TrackOut

logging.basicConfig(
    level=os.environ.get("RELAY_LOG", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s | %(message)s",
)
log = logging.getLogger("relay.main")


class Hub:
    """Fans engine events (emitted from a worker thread) out to WS clients."""

    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()
        self.queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=1000)
        self.loop: asyncio.AbstractEventLoop | None = None

    def emit_threadsafe(self, event: dict) -> None:
        if self.loop is None:
            return
        self.loop.call_soon_threadsafe(self._put, event)

    def _put(self, event: dict) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # drop under backpressure; state is also pollable over REST

    async def pump(self) -> None:
        while True:
            event = await self.queue.get()
            dead = []
            for ws in list(self.clients):
                try:
                    await ws.send_json(event)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                self.clients.discard(ws)


registry = CameraRegistry()
engine = RelayEngine(registry)
hub = Hub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    hub.loop = asyncio.get_running_loop()
    async with httpx.AsyncClient() as client:
        await registry.load(client)
    log.info("loaded %d cameras from %s", len(registry), settings.camera_source)
    engine.start(hub.emit_threadsafe)
    pump_task = asyncio.create_task(hub.pump())
    try:
        yield
    finally:
        pump_task.cancel()
        engine.stop()


app = FastAPI(title="Relay — vehicle handoff tracker", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # localhost dev; tighten for any real deployment
    allow_methods=["*"],
    allow_headers=["*"],
)


# --------------------------------------------------------------------------- #
# REST
# --------------------------------------------------------------------------- #
@app.get("/api/status")
async def status():
    return engine.status


@app.get("/api/cameras")
async def cameras():
    return [
        {
            "id": c.id, "name": c.name, "lat": c.lat, "lon": c.lon,
            "view": c.view, "has_video": bool(c.video_url), "available": c.available,
        }
        for c in registry.all()
    ]


@app.post("/api/view/{camera_id}")
async def set_view(camera_id: str):
    if not engine.set_viewed(camera_id):
        raise HTTPException(404, "unknown camera")
    return {"ok": True, "camera_id": camera_id}


@app.get("/api/camera/{camera_id}/state", response_model=CameraStateOut)
async def camera_state(camera_id: str):
    st = engine.get_state(camera_id)
    if st is None:
        raise HTTPException(404, "no state yet; view the camera first")
    return CameraStateOut(
        camera_id=st.camera_id, frame_w=st.frame_w, frame_h=st.frame_h,
        seq=st.seq, updated_at=st.updated_at, error=st.error,
        tracks=[TrackOut(track_id=t.track_id, label=t.label,
                         score=t.score, bbox=list(t.bbox)) for t in st.tracks],
    )


@app.get("/api/camera/{camera_id}/frame.jpg")
async def camera_frame(camera_id: str):
    jpeg = engine.get_frame(camera_id)
    if jpeg is None:
        raise HTTPException(404, "no frame yet")
    return Response(content=jpeg, media_type="image/jpeg",
                    headers={"Cache-Control": "no-store"})


@app.get("/api/camera/{camera_id}/clip.mp4")
async def camera_clip(camera_id: str):
    """The latest raw source clip, for the looping 'LIVE' video view (TfL only)."""
    clip = engine.get_clip(camera_id)
    if clip is None:
        raise HTTPException(404, "no clip (source has no video)")
    return Response(content=clip, media_type="video/mp4",
                    headers={"Cache-Control": "no-store"})


@app.post("/api/select")
async def select(req: SelectRequest):
    result = engine.select_target(req.camera_id, req.track_id)
    if result is None:
        raise HTTPException(400, "could not select target (no such track in latest state)")
    return result


@app.post("/api/clear")
async def clear():
    engine.clear_target()
    return {"ok": True}


@app.get("/api/path")
async def path():
    return {"path": engine.get_path()}


@app.get("/api/sources")
async def sources():
    return {"active": engine.source_name, "available": available_sources()}


@app.post("/api/source/{name}")
async def set_source(name: str):
    if name not in available_sources():
        raise HTTPException(404, f"unknown source; available: {available_sources()}")
    if name == engine.source_name:
        return {"ok": True, "source": name, "camera_count": len(registry)}
    async with httpx.AsyncClient() as client:
        await registry.load(client, source_name=name)
    engine.reset_for_new_source(name)
    log.info("switched source to %s (%d cameras)", name, len(registry))
    return {"ok": True, "source": name, "camera_count": len(registry)}


# --------------------------------------------------------------------------- #
# WebSocket
# --------------------------------------------------------------------------- #
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    await websocket.accept()
    hub.clients.add(websocket)
    await websocket.send_json({"type": "hello", "status": engine.status})
    try:
        while True:
            # Clients may send {action:"view"|"select"|"clear", ...} over WS too.
            msg = await websocket.receive_json()
            action = msg.get("action")
            if action == "view" and msg.get("camera_id"):
                engine.set_viewed(msg["camera_id"])
            elif action == "select":
                engine.select_target(msg.get("camera_id"), int(msg.get("track_id")))
            elif action == "clear":
                engine.clear_target()
    except WebSocketDisconnect:
        pass
    finally:
        hub.clients.discard(websocket)


# --------------------------------------------------------------------------- #
# Optionally serve a built frontend (frontend/dist) at the root.
# --------------------------------------------------------------------------- #
_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.isdir(_DIST):
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
