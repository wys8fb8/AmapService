from amap_service.sdk.section_compute import (
    build_chain, section_links, largest_remainder, sample_chain, align_stations,
    compute_section_rows,
)


def _seg(link_id, track):
    return {"link_id": link_id, "line_track": track}


def test_build_chain_and_section_links():
    chain = build_chain([_seg(1, "0.0,0.0;0.001,0.0"), _seg(2, "0.001,0.0;0.002,0.0")])
    assert [c.link_id for c in chain] == [1, 2]
    L = chain[0].arc_end
    pairs = section_links(chain, L * 0.5, L * 1.5)
    assert [lid for lid, _ in pairs] == [1, 2]


def test_largest_remainder_sums_to_100():
    assert sum(largest_remainder([1.0, 1.0, 1.0])) == 100
    assert largest_remainder([1.0, 1.0, 1.0]) == [33, 33, 34]


def test_align_stations_out_and_back_disambiguates():
    out = "0.0,0.0;0.0025,0.0;0.005,0.0;0.0075,0.0;0.01,0.0"
    back = "0.01,0.0;0.0075,0.0;0.005,0.0;0.0025,0.0;0.0,0.0"
    chain = build_chain([_seg(1, out), _seg(1, back)])
    total = chain[-1].arc_end
    arcs = align_stations(sample_chain(chain, 20.0),
                          [(0.002, 0.0), (0.0095, 0.0), (0.005, 0.0), (0.001, 0.0)])
    assert arcs == sorted(arcs)
    assert arcs[0] < total / 2 and arcs[2] > total / 2 and arcs[3] > total / 2


def test_compute_section_rows_basic():
    chain = build_chain([_seg(1, "0.0,0.0;0.001,0.0"), _seg(2, "0.001,0.0;0.002,0.0")])
    stations = [(1, 0.0, 0.0), (2, 0.001, 0.0), (3, 0.002, 0.0)]  # (level_id, lng, lat)
    rows = compute_section_rows(chain, stations, sample_step_m=4.0)
    spans = {(r["from_level_id"], r["to_level_id"]) for r in rows}
    assert spans == {(1, 2), (2, 3)}
    for span in [(1, 2), (2, 3)]:
        seg = [r for r in rows if (r["from_level_id"], r["to_level_id"]) == span]
        assert sum(r["pct"] for r in seg) == 100
        assert [r["seq"] for r in seg] == list(range(len(seg)))
    s12 = [r for r in rows if (r["from_level_id"], r["to_level_id"]) == (1, 2)]
    assert s12 == [{"from_level_id": 1, "to_level_id": 2, "seq": 0, "link_id": 1,
                    "length_m": s12[0]["length_m"], "pct": 100}]
    assert s12[0]["length_m"] > 0


def test_compute_section_rows_too_few_stations():
    chain = build_chain([_seg(1, "0.0,0.0;0.001,0.0")])
    assert compute_section_rows(chain, [(1, 0.0, 0.0)], 4.0) == []
    assert compute_section_rows([], [(1, 0.0, 0.0), (2, 0.001, 0.0)], 4.0) == []
