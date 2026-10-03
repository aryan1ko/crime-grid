"""Deciding which camera is "next down the road".

We don't have lane-level road topology for arbitrary public cameras, and we
can't recover a vehicle's true world heading from an uncalibrated street
camera. So we use the robust field approach: arm a small set of plausible
downstream cameras and let whichever one actually sees the car fire the
trigger. "Plausible" = near enough, and roughly in the direction the source
camera faces (which, for traffic cams, aligns with the road).

Each candidate also gets an arrival-time window derived from distance and an
assumed speed band, so a match far outside the window can be rejected as a
different vehicle that merely looks similar.
"""

from __future__ import annotations

from .config import settings
from .geo import angular_diff, bearing_deg, haversine_m, view_to_bearing
from .models import Camera, HandoffCandidate


def downstream_candidates(source: Camera, cameras: list[Camera]) -> list[HandoffCandidate]:
    """Rank handoff candidates for a vehicle leaving `source`."""
    source_view = view_to_bearing(source.view)
    scored: list[HandoffCandidate] = []

    for cam in cameras:
        if cam.id == source.id or not cam.available:
            continue
        dist = haversine_m(source.lat, source.lon, cam.lat, cam.lon)
        if dist < 1.0 or dist > settings.handoff_radius_m:
            continue

        brg = bearing_deg(source.lat, source.lon, cam.lat, cam.lon)
        # If we know which way the source camera faces, prefer cameras that lie
        # ahead along that line of sight (both the oncoming and the receding
        # directions are kept — the car could go either way on the road).
        aligned = True
        if source_view is not None:
            fwd = angular_diff(brg, source_view)
            back = angular_diff(brg, (source_view + 180.0) % 360.0)
            aligned = min(fwd, back) <= settings.bearing_tolerance_deg

        eta_min = max(0.0, dist / settings.speed_max_ms - settings.window_early_tol_s)
        eta_max = dist / settings.speed_min_ms + settings.window_pad_s

        scored.append(
            HandoffCandidate(
                camera_id=cam.id,
                distance_m=dist,
                bearing_deg=brg,
                aligned=aligned,
                eta_min_s=eta_min,
                eta_max_s=eta_max,
            )
        )

    # Aligned candidates first, then nearest. If nothing is aligned (e.g. the
    # source has no view direction), fall back to pure nearest-neighbour.
    scored.sort(key=lambda c: (not c.aligned, c.distance_m))
    aligned = [c for c in scored if c.aligned]
    ranked = aligned if aligned else scored
    return ranked[: settings.max_handoff_cams]
