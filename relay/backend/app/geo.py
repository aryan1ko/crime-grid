"""Small geodesy helpers — great-circle distance and bearing."""

from __future__ import annotations

import math
import re

EARTH_R = 6_371_000.0  # metres

# Compass tokens → degrees clockwise from north.
COMPASS = {
    "N": 0, "NE": 45, "E": 90, "SE": 135,
    "S": 180, "SW": 225, "W": 270, "NW": 315,
}


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * EARTH_R * math.asin(min(1.0, math.sqrt(a)))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from point 1 to point 2, degrees clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


def angular_diff(a: float, b: float) -> float:
    """Smallest absolute difference between two bearings, in [0, 180]."""
    d = abs((a - b) % 360.0)
    return min(d, 360.0 - d)


def view_to_bearing(view: str | None) -> float | None:
    """Extract a compass bearing from TfL's freeform `view` text.

    Examples it must handle: "West", "NW Zoom - Ealing Road",
    "NORTH\\/West - Stroud Green Road", "SOUTH-Vaughan Way", "WEST-Romford Rd".
    The direction sits at the start; the rest is a human description. We only
    accept a leading compass token that is a whole word, so a street name like
    "Ealing..." is not misread as East.
    """
    if not view:
        return None
    s = view.upper().replace("\\", "").replace("/", "").strip()
    s = (s.replace("NORTH", "N").replace("SOUTH", "S")
          .replace("EAST", "E").replace("WEST", "W"))
    m = re.match(r"([NSEW]{1,2})(?![A-Z])", s)
    if not m:
        return None
    tok = m.group(1)
    if tok in COMPASS:
        return COMPASS[tok]
    # Fall back to just the primary direction if a 2-letter combo is invalid.
    return COMPASS.get(tok[0])
