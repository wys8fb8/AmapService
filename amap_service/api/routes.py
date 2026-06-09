"""需求3/4/5 路由。需求4/5 响应体与对应 MQTT 主题 payload 同构(同一视图层产出)。"""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from amap_service.api import schemas
from amap_service.api.deps import require_api_key
from amap_service.views.line_views import (
    build_segment_view, build_traffic_view, build_section_view, build_station_section_view,
)

router = APIRouter(prefix="/api/v1")

health_router = APIRouter(prefix="/api/v1")


@health_router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/lines", response_model=list[schemas.LineSummary], dependencies=[Depends(require_api_key)])
def list_lines(request: Request):
    return request.app.state.static_cache.lines()


@router.get("/lines/{line_name}/segments", response_model=schemas.SegmentView,
            dependencies=[Depends(require_api_key)])
def line_segments(request: Request, line_name: str,
                  direction: Optional[int] = Query(default=None, ge=0, le=1)):
    view = build_segment_view(request.app.state.static_cache, line_name, direction)
    if view is None:
        raise HTTPException(status_code=404, detail=f"line not found: {line_name}")
    return view


@router.get("/lines/{line_name}/traffic", response_model=schemas.TrafficView,
            dependencies=[Depends(require_api_key)])
def line_traffic(request: Request, line_name: str,
                 direction: Optional[int] = Query(default=None, ge=0, le=1),
                 geometry: bool = Query(default=False)):
    view = build_traffic_view(request.app.state.static_cache,
                              request.app.state.traffic_reader, line_name, direction, geometry)
    if view is None:
        raise HTTPException(status_code=404, detail=f"line not found: {line_name}")
    return view


@router.get("/lines/{line_name}/sections", response_model=schemas.SectionView,
            dependencies=[Depends(require_api_key)])
def line_sections(request: Request, line_name: str,
                  direction: Optional[int] = Query(default=None, ge=0, le=1),
                  geometry: bool = Query(default=False)):
    view = build_section_view(request.app.state.static_cache,
                              request.app.state.traffic_reader, line_name, direction, geometry)
    if view is None:
        raise HTTPException(status_code=404, detail=f"line not found: {line_name}")
    return view


@router.get("/lines/{line_name}/sections/{to_level_id}", response_model=schemas.StationSectionView,
            dependencies=[Depends(require_api_key)])
def line_single_section(request: Request, line_name: str, to_level_id: int,
                        direction: int = Query(..., ge=0, le=1),
                        geometry: bool = Query(default=False)):
    view = build_station_section_view(request.app.state.static_cache,
                                      request.app.state.traffic_reader,
                                      line_name, direction, to_level_id, geometry)
    if view is None:
        raise HTTPException(status_code=404,
                            detail=f"section not found: {line_name}/{direction}/{to_level_id}")
    return view
