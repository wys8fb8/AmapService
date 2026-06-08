import math
from amap_service.sdk import geometry as g


def test_parse_track_basic_and_fullwidth():
    assert g.parse_track("120.0,31.0;120.001,31.0") == [(120.0, 31.0), (120.001, 31.0)]
    assert g.parse_track("120.0,31.0；120.001,31.0") == [(120.0, 31.0), (120.001, 31.0)]
    assert g.parse_track("") == []
    assert g.parse_track("  120.0,31.0 ;  ") == [(120.0, 31.0)]


def test_format_track_roundtrip():
    assert g.format_track([(120.0, 31.0), (120.001, 31.0)]) == "120.0,31.0;120.001,31.0"
    assert g.format_track([]) == ""


def test_haversine_known_distances():
    assert g.haversine((0.0, 0.0), (0.0, 0.0)) == 0.0
    assert abs(g.haversine((0.0, 0.0), (0.0, 1.0)) - 111194.9) < 1.0


def test_bearing_cardinals():
    assert abs(g.bearing((0.0, 0.0), (0.0, 1.0)) - 0.0) < 1e-6
    assert abs(g.bearing((0.0, 0.0), (1.0, 0.0)) - 90.0) < 1e-6


def test_angle_diff():
    assert g.angle_diff(350.0, 10.0) == 20.0
    assert g.angle_diff(90.0, 270.0) == 180.0
    assert g.angle_diff(10.0, 10.0) == 0.0


def test_is_reverse():
    assert g.is_reverse(270.0, 90.0) is True
    assert g.is_reverse(90.0, 90.0) is False
    assert g.is_reverse(120.0, 90.0, threshold_deg=45.0) is False


def test_point_to_segment_distance():
    d = g.point_to_segment_distance((0.001, 0.001), (0.0, 0.0), (0.002, 0.0))
    assert abs(d - 111.19) < 2.0
    d2 = g.point_to_segment_distance((0.0, 0.001), (0.0, 0.0), (0.0, 0.0))
    assert abs(d2 - 111.19) < 2.0


def test_polyline_bearing():
    assert abs(g.polyline_bearing([(0.0, 0.0), (0.001, 0.0), (0.002, 0.0)]) - 90.0) < 1e-3
    assert g.polyline_bearing([(0.0, 0.0)]) == 0.0


def test_densify_inserts_points_on_long_segments():
    # ~222m east-west segment at lat 31; densify at 50m -> intermediate points, endpoints kept
    poly = [(120.0, 31.0), (120.002, 31.0)]
    out = g.densify(poly, 50.0)
    assert out[0] == (120.0, 31.0) and out[-1] == (120.002, 31.0)
    assert len(out) >= 4
    # consecutive spacing <= ~50m
    assert max(g.haversine(out[i], out[i + 1]) for i in range(len(out) - 1)) <= 55.0


def test_densify_short_segment_unchanged():
    poly = [(120.0, 31.0), (120.00001, 31.0)]
    assert g.densify(poly, 50.0) == poly
