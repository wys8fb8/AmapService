"""API 响应 pydantic 模型(用于 OpenAPI 文档)。link_id 为字符串(防 JS 损精度)。"""
from typing import Optional

from pydantic import BaseModel


class SegmentItem(BaseModel):
    seq: int
    link_id: str
    reverse: int
    line_track: Optional[str] = None
    state: Optional[int] = None
    speed: Optional[int] = None
    travel_time: Optional[int] = None


class SegmentDirection(BaseModel):
    direction: int
    segments: list[SegmentItem]


class SegmentView(BaseModel):
    line_name: str
    directions: list[SegmentDirection]


class TrafficView(BaseModel):
    line_name: str
    traffic_time: Optional[str] = None
    directions: list[SegmentDirection]


class SectionLink(BaseModel):
    link_id: str
    state: int
    pct: int
    line_track: Optional[str] = None


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
