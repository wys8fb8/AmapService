"""API 响应 pydantic 模型(用于 OpenAPI 文档)。link_id 为字符串(防 JS 损精度)。"""
from typing import Optional

from pydantic import BaseModel


# 需求3 静态路段:不含实时路况字段(state/speed/travel_time)
class SegmentItem(BaseModel):
    seq: int
    link_id: str
    reverse: int
    line_track: Optional[str] = None


class SegmentDirection(BaseModel):
    direction: int
    segments: list[SegmentItem]


class SegmentView(BaseModel):
    line_name: str
    directions: list[SegmentDirection]


# 需求4 逐路段实时路况:在静态字段基础上带 state/speed/travel_time
class TrafficSegmentItem(BaseModel):
    seq: int
    link_id: str
    reverse: int
    line_track: Optional[str] = None
    state: Optional[int] = None
    speed: Optional[int] = None
    travel_time: Optional[int] = None


class TrafficSegmentDirection(BaseModel):
    direction: int
    segments: list[TrafficSegmentItem]


class TrafficView(BaseModel):
    line_name: str
    traffic_time: Optional[str] = None
    directions: list[TrafficSegmentDirection]


class SectionLink(BaseModel):
    link_id: str
    state: int
    pct: int


class SectionItem(BaseModel):
    from_level_id: int
    to_level_id: int
    links: list[SectionLink]


class SectionDirection(BaseModel):
    direction: int
    sections: list[SectionItem]


class SectionView(BaseModel):
    line_name: str
    traffic_time: Optional[str] = None
    directions: list[SectionDirection]


class StationSectionView(BaseModel):
    line_name: str
    direction: int
    to_level_id: int
    traffic_time: Optional[str] = None
    links: list[SectionLink]


class LineSummary(BaseModel):
    line_name: str
    directions: list[int]
    has_segments: bool
    has_sections: bool
    station_count: int


class HealthStatus(BaseModel):
    status: str
