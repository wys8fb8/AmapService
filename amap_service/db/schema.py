from sqlalchemy import (
    BigInteger, Column, Float, Index, Integer, MetaData, Table, Text,
    TIMESTAMP, UniqueConstraint, func,
)

metadata = MetaData()

road_link = Table(
    "road_link", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", BigInteger, nullable=False, unique=True),
    Column("road_name", Text),
    Column("length", Integer),
    Column("formway", Integer),
    Column("roadclass", Integer),
    Column("line_track", Text),
    Column("created_at", TIMESTAMP, server_default=func.current_timestamp()),
    Index("idx_road_link_road_name", "road_name"),
    Index("idx_road_link_formway", "formway"),
    Index("idx_road_link_roadclass", "roadclass"),
)

road_link_coord = Table(
    "road_link_coord", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", BigInteger, nullable=False),
    Column("seq", Integer, nullable=False),
    Column("longitude", Float, nullable=False),
    Column("latitude", Float, nullable=False),
    UniqueConstraint("link_id", "seq", name="idx_road_link_coord_uniq"),
    Index("idx_road_link_coord_lid", "link_id"),
    # spatial bbox lookups: SDK candidate-link matching + connectivity-repair gap routing
    Index("idx_road_link_coord_lnglat", "longitude", "latitude"),
)

traffic_status = Table(
    "traffic_status", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("link_id", BigInteger, nullable=False, unique=True),
    Column("speed", Integer),
    Column("state", Integer),
    Column("travel_time", Integer),
    Column("traffic_time", Text),   # 路况时间：响应顶层 utcSeconds(Unix秒) 转 "yyyy-MM-dd HH:mm:ss"(东八区)
    Column("updated_at", TIMESTAMP, server_default=func.current_timestamp()),
    Index("idx_traffic_status_state", "state"),
    Index("idx_traffic_status_updated", "updated_at"),
)

transit_line_raw = Table(
    "transit_line_raw", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("line_name", Text),
    Column("raw_response", Text),
    Column("fetched_at", TIMESTAMP, server_default=func.current_timestamp()),
)

# 需求2 阶段二：每条公交线路每个方向的有序路段（由需求3 SDK 从 LineLonLat 轨迹转换得到）。
transit_segment = Table(
    "transit_segment", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("line_name", Text, nullable=False),     # 线路号，如 "47"、"192"
    Column("nor_code", Text),                       # 线路规范码 NorCode，如 "004700"
    Column("direction", Integer, nullable=False),   # UpDown: 0=上行/单环, 1=下行
    Column("seq", Integer, nullable=False),         # 方向内有序序号（从0开始）
    Column("link_id", BigInteger, nullable=False),  # 匹配到的路段ID（64位）
    Column("reverse_coords", Integer, nullable=False, server_default="0"),  # 是否逆行(0/1)
    Column("line_track", Text),                     # 该路段途经段轨迹"经度,纬度;..."（裁剪到公交实际经过部分，已按行进方向；reverse_coords=1 表示与路段存储方向相反）
    Column("created_at", TIMESTAMP, server_default=func.current_timestamp()),
    UniqueConstraint("line_name", "direction", "seq", name="idx_transit_segment_uniq"),
    Index("idx_transit_segment_line", "line_name"),
    Index("idx_transit_segment_link", "link_id"),
)

# v2 站间路况分段：站级静态数据（由 transit-build 从线路实体落地）。
transit_station = Table(
    "transit_station", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("line_name", Text, nullable=False),
    Column("nor_code", Text),
    Column("direction", Integer, nullable=False),     # 0=上行/单环, 1=下行
    Column("level_id", Integer, nullable=False),       # 站级号
    Column("level_name", Text),                        # 站名
    Column("longitude", Float, nullable=False),        # Lon02
    Column("latitude", Float, nullable=False),         # Lat02
    Column("created_at", TIMESTAMP, server_default=func.current_timestamp()),
    UniqueConstraint("line_name", "direction", "level_id", name="idx_transit_station_uniq"),
    Index("idx_transit_station_line", "line_name", "direction"),
)

# v2 站间路况分段：相邻两站之间各路段的长度占比（静态，由 section-build 计算；不含路况）。
transit_section_link = Table(
    "transit_section_link", metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("line_name", Text, nullable=False),
    Column("nor_code", Text),
    Column("direction", Integer, nullable=False),
    Column("from_level_id", Integer, nullable=False),  # 区间起点站级
    Column("to_level_id", Integer, nullable=False),    # 区间终点站级（方法一入参）
    Column("seq", Integer, nullable=False),            # link 在区间内的顺序（0 起）
    Column("link_id", BigInteger, nullable=False),     # 路段 ID（64 位）
    Column("length_m", Float, nullable=False),         # 区间内该 link 的长度（米，事实源）
    Column("pct", Integer, nullable=False),            # 预计算占比（区间内和=100）
    Column("built_at", TIMESTAMP, server_default=func.current_timestamp()),
    UniqueConstraint("line_name", "direction", "to_level_id", "seq", name="idx_transit_section_link_uniq"),
    Index("idx_transit_section_link_line", "line_name", "direction"),
)
