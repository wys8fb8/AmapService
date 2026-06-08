from amap_service.sdk.station_traffic import build_chain, section_links


def _seg(link_id, track):
    return {"link_id": link_id, "line_track": track}


def test_build_chain_arc_intervals_contiguous():
    # two links end-to-end along the equator; ~111.32 m per 0.001° lng
    chain = build_chain([
        _seg(1, "0.0,0.0;0.001,0.0"),
        _seg(2, "0.001,0.0;0.002,0.0"),
    ])
    assert [c.link_id for c in chain] == [1, 2]
    assert chain[0].arc_start == 0.0
    # link2 starts exactly where link1 ends (shared arc scale)
    assert abs(chain[0].arc_end - chain[1].arc_start) < 1e-9
    assert chain[1].arc_end > chain[1].arc_start > 0.0


def test_build_chain_skips_degenerate_segments():
    chain = build_chain([_seg(1, ""), _seg(2, "5,5"), _seg(3, "0.0,0.0;0.001,0.0")])
    assert [c.link_id for c in chain] == [3]  # empty + single-point dropped


def test_section_links_overlap_lengths():
    chain = build_chain([
        _seg(1, "0.0,0.0;0.001,0.0"),   # arc ~ [0, L]
        _seg(2, "0.001,0.0;0.002,0.0"),  # arc ~ [L, 2L]
    ])
    L = chain[0].arc_end
    # span from mid-link1 to mid-link2 -> half of link1 + half of link2
    pairs = section_links(chain, L * 0.5, L * 1.5)
    assert [lid for lid, _ in pairs] == [1, 2]
    assert abs(pairs[0][1] - L * 0.5) < 1e-6
    assert abs(pairs[1][1] - L * 0.5) < 1e-6


def test_section_links_zero_length_span_returns_covering_link_full():
    chain = build_chain([_seg(7, "0.0,0.0;0.001,0.0")])
    mid = chain[0].arc_end * 0.5
    pairs = section_links(chain, mid, mid)  # zero-length
    assert len(pairs) == 1 and pairs[0][0] == 7 and pairs[0][1] == 1.0
