"""Kiểu dữ liệu trung gian giữa các khâu của pipeline (SDD mục 7.1)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

import numpy as np


@dataclass(frozen=True)
class NormalizedImage:
    pixels: np.ndarray          # ảnh xám uint8 đã đưa về tỉ lệ chuẩn
    px_per_mm: float
    px_per_mm_source: str       # "pdf_page_size" | "estimated"
    scale_from_original: float  # hệ số đã resample so với ảnh gốc
    median_stroke_px: float


@dataclass(frozen=True)
class ProjectedPort:
    port_id: str
    role: str
    xy: tuple[float, float]
    direction: tuple[float, float]            # hướng ra, hệ trang


@dataclass
class DetectedSymbol:
    id: str
    detector_class: str
    bbox: tuple[float, float, float, float]   # x, y, w, h
    score: float
    rotation_deg: int | None = None           # điền ở OrientationEstimator
    mirror_x: bool = False
    orientation_score: float | None = None
    ports: list[ProjectedPort] = field(default_factory=list)


@dataclass(frozen=True)
class ParsedCode:
    pattern_id: str                           # khóa trong evn_codes.yaml
    role: str                                 # device_code | busbar_code | parameter | bay_label | pole_number
    expected_types: frozenset[str]            # lớp detector được phép ghép
    voltage_kv: int | None
    bay_code: str | None
    suffix: str | None
    normalized: str = ""
    details: Mapping[str, str] = field(default_factory=dict)   # ý nghĩa đã giải: vị trí máy cắt, phía dao…
    unknown_suffix: bool = False


@dataclass
class TextItem:
    id: str
    polygon: list[tuple[float, float]]
    bbox: tuple[float, float, float, float]
    raw: str
    normalized: str
    score: float
    angle_deg: int                            # 0 | 90 | 270
    parsed: ParsedCode | None


@dataclass
class WireSegment:
    id: str
    points: list[tuple[float, float]]
    stroke_px: float
    is_busbar: bool
    confidence: float


@dataclass
class CandidateNode:
    id: str
    position: tuple[float, float]
    incident_segment_ids: list[str]
    node_type: str | None = None              # junction | crossing_no_connect | bend | false_positive
    style: str | None = None
    confidence: float | None = None


@dataclass
class WireExtraction:
    segments: list[WireSegment]
    nodes: list[CandidateNode]
    wire_mask: np.ndarray | None              # để tính Wire IoU


@dataclass(frozen=True)
class LabelAssignment:
    text_id: str
    target_kind: str                          # "device" | "busbar" | "line_terminal"
    target_id: str
    cost: float
    pending: bool                             # ghép được nhưng chi phí gần ngưỡng → chờ duyệt
