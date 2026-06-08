from amap_service.config.schema import DatabaseConfig, SqliteConfig
from amap_service.db.engine import make_engine
from amap_service.db.migrate import init_db
from amap_service.db.repositories import upsert_road_links
from amap_service.sdk.connector import GraphConnector


def _seed(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0), (0.0, 0.0009)]},
        {"link_id": 2, "road_name": "B", "length": 1, "formway": 15, "roadclass": 9,   # the bridge
         "line_track": "", "coords": [(0.0, 0.0009), (0.0, 0.0018)]},
        {"link_id": 3, "road_name": "C", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0018), (0.0, 0.0027)]},
        {"link_id": 12, "road_name": "Brev", "length": 1, "formway": 15, "roadclass": 9,  # bridge, stored reversed
         "line_track": "", "coords": [(0.0, 0.0018), (0.0, 0.0009)]},
    ])
    return e


_SEG1 = {"link_id": 1, "reverse_coords": False, "line_track": "0.0,0.0;0.0,0.0009"}
_SEG3 = {"link_id": 3, "reverse_coords": False, "line_track": "0.0,0.0018;0.0,0.0027"}


def test_fill_splices_missing_connecting_link(tmp_path):
    gc = GraphConnector(_seed(tmp_path))
    out = gc.fill([_SEG1, _SEG3])   # link 2 is missing between them
    assert [s["link_id"] for s in out] == [1, 2, 3]
    assert out[1]["reverse_coords"] is False
    assert out[1]["line_track"] == "0.0,0.0009;0.0,0.0018"   # oriented in travel direction


def test_fill_noop_when_already_connected(tmp_path):
    gc = GraphConnector(_seed(tmp_path))
    seg2 = {"link_id": 2, "reverse_coords": False, "line_track": "0.0,0.0009;0.0,0.0018"}
    assert [s["link_id"] for s in gc.fill([_SEG1, seg2])] == [1, 2]


def test_fill_leaves_gap_when_no_bridge_exists(tmp_path):
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0), (0.0, 0.0009)]},
        {"link_id": 3, "road_name": "C", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0018), (0.0, 0.0027)]},
    ])
    gc = GraphConnector(e)
    assert [s["link_id"] for s in gc.fill([_SEG1, _SEG3])] == [1, 3]   # nothing to splice → gap left


def test_fill_orients_reversed_stored_bridge(tmp_path):
    # router must traverse the bridge against its stored coord order → reverse_coords True
    r = GraphConnector(_seed(tmp_path))._route((0.0, 0.0009), (0.0, 0.0018), exclude={1, 2, 3})
    assert [(s["link_id"], s["reverse_coords"]) for s in r] == [(12, True)]
    assert r[0]["line_track"] == "0.0,0.0009;0.0,0.0018"   # still oriented in travel direction


def test_fill_rejects_overlong_detour(tmp_path):
    # the only available bridge is an L-shaped link far longer than the straight gap; a tight
    # detour budget makes the connector decline to fill (better a gap than a bogus loop).
    e = make_engine(DatabaseConfig(type="sqlite", sqlite=SqliteConfig(path=str(tmp_path / "t.db"))))
    init_db(e)
    upsert_road_links(e, [
        {"link_id": 1, "road_name": "A", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0), (0.0, 0.0009)]},
        {"link_id": 2, "road_name": "L", "length": 1, "formway": 15, "roadclass": 9,   # detours far east
         "line_track": "", "coords": [(0.0, 0.0009), (0.003, 0.00135), (0.0, 0.0018)]},
        {"link_id": 3, "road_name": "C", "length": 1, "formway": 15, "roadclass": 9,
         "line_track": "", "coords": [(0.0, 0.0018), (0.0, 0.0027)]},
    ])
    gc = GraphConnector(e, detour_factor=1.5, detour_slack_m=20.0)
    assert gc.fill([_SEG1, _SEG3]) == [_SEG1, _SEG3]   # over-budget → no splice, gap preserved


def test_fill_passthrough_single_segment(tmp_path):
    gc = GraphConnector(_seed(tmp_path))
    assert gc.fill([_SEG1]) == [_SEG1]
    assert gc.fill([]) == []
