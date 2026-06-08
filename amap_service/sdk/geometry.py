"""Pure geometry helpers for GPS track ↔ road-link conversion.

Coordinates are (longitude, latitude) tuples in degrees (Amap convention).
No external dependencies, no DB access — fully unit-testable.
"""
import math

EARTH_RADIUS_M = 6371000.0


def parse_track(track: str) -> list[tuple[float, float]]:
    """Parse "lng,lat;lng,lat" into [(lng, lat), ...]. Accepts full-width ；/，."""
    if not track:
        return []
    normalized = track.replace("；", ";").replace("，", ",")
    points: list[tuple[float, float]] = []
    for chunk in normalized.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        lng_s, lat_s = chunk.split(",")
        points.append((float(lng_s), float(lat_s)))
    return points


def format_track(coords: list[tuple[float, float]]) -> str:
    """Inverse of parse_track using half-width separators."""
    return ";".join(f"{lng},{lat}" for lng, lat in coords)


def haversine(p1: tuple[float, float], p2: tuple[float, float]) -> float:
    """Great-circle distance in meters between two (lng, lat) points."""
    lng1, lat1 = p1
    lng2, lat2 = p2
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _to_local_xy(p: tuple[float, float], lat0: float) -> tuple[float, float]:
    """Equirectangular projection to local meters, centered at latitude lat0."""
    lng, lat = p
    x = math.radians(lng) * math.cos(math.radians(lat0)) * EARTH_RADIUS_M
    y = math.radians(lat) * EARTH_RADIUS_M
    return x, y


def point_to_segment_distance(
    p: tuple[float, float], a: tuple[float, float], b: tuple[float, float]
) -> float:
    """Shortest distance (meters) from point p to segment a-b (planar approx)."""
    lat0 = a[1]
    px, py = _to_local_xy(p, lat0)
    ax, ay = _to_local_xy(a, lat0)
    bx, by = _to_local_xy(b, lat0)
    dx, dy = bx - ax, by - ay
    if dx == 0.0 and dy == 0.0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def bearing(a: tuple[float, float], b: tuple[float, float]) -> float:
    """Initial bearing in degrees [0, 360) from point a to point b."""
    lng1, lat1 = math.radians(a[0]), math.radians(a[1])
    lng2, lat2 = math.radians(b[0]), math.radians(b[1])
    dlon = lng2 - lng1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angle_diff(b1: float, b2: float) -> float:
    """Smallest absolute difference between two bearings, in [0, 180]."""
    d = abs(b1 - b2) % 360.0
    return d if d <= 180.0 else 360.0 - d


def is_reverse(track_bearing: float, link_bearing: float, threshold_deg: float = 90.0) -> bool:
    """True when the track heads opposite the stored link geometry."""
    return angle_diff(track_bearing, link_bearing) > threshold_deg


def polyline_bearing(coords: list[tuple[float, float]]) -> float:
    """Overall bearing from first to last coordinate; 0.0 if fewer than 2 points."""
    if len(coords) < 2:
        return 0.0
    return bearing(coords[0], coords[-1])


def densify(coords: list, step_m: float) -> list:
    """Resample a polyline so consecutive points are at most step_m apart (linear interpolation).

    Used to turn a first-pass matched path (with straight jumps across gaps) into an evenly
    sampled track for a second matching pass — the inserted points fall on the connecting links.
    """
    if len(coords) < 2 or step_m <= 0:
        return list(coords)
    out = [coords[0]]
    for a, b in zip(coords, coords[1:]):
        dist = haversine(a, b)
        if dist > step_m:
            n = int(dist // step_m)
            for k in range(1, n + 1):
                t = k * step_m / dist
                out.append((a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t))
        if haversine(out[-1], b) > 1e-9:
            out.append(b)
    return out


