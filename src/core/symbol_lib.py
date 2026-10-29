"""Thư viện ký hiệu: đọc assets/symbols_metadata.json (nguồn chuẩn duy nhất, SDD mục 5)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.core.geometry import (Placement, direction_to_page, local_to_page, placement_from_bbox,
                               rotated_bbox_local)
from src.core.types import ProjectedPort

ROOT = Path(__file__).resolve().parents[2]
ASSETS_DIR = ROOT / "assets"
METADATA_PATH = ASSETS_DIR / "symbols_metadata.json"

NET_BEHAVIORS = ("split", "passthrough", "terminal")
CONDUCTIONS = ("by_state", "always", "earthing_by_state", None)


@dataclass(frozen=True)
class PortDef:
    id: str
    role: str
    local_x: float
    local_y: float
    direction: tuple[float, float]


@dataclass(frozen=True)
class SymbolDef:
    detector_class: str
    class_id: int
    type: str
    drawing_state: str | None
    svg_file: Path
    dxf_block_name: str
    size: tuple[float, float]
    bbox_local: tuple[float, float, float, float]     # x0, y0, x1, y1 (lu, quanh gốc cục bộ)
    net_behavior: str
    conduction: str | None
    orientation_sensitive: bool
    allowed_rotations_deg: tuple[int, ...]
    allow_mirror: bool
    ports: tuple[PortDef, ...]
    cad_attributes: tuple[str, ...]

    def port(self, port_id: str) -> PortDef:
        for p in self.ports:
            if p.id == port_id:
                return p
        raise KeyError(f"{self.detector_class} không có cổng {port_id}")


class SymbolLibrary:
    def __init__(self, metadata: dict, assets_dir: Path = ASSETS_DIR):
        self.schema_version: str = metadata["schemaVersion"]
        self.library_unit_mm: float = float(metadata["libraryUnitMm"])
        self.symbols: dict[str, SymbolDef] = {}
        for cls, m in metadata["symbols"].items():
            if m["netBehavior"] not in NET_BEHAVIORS:
                raise ValueError(f"{cls}: netBehavior không hợp lệ {m['netBehavior']}")
            if m["conduction"] not in CONDUCTIONS:
                raise ValueError(f"{cls}: conduction không hợp lệ {m['conduction']}")
            if m.get("bboxLocal") is None:
                raise ValueError(f"{cls}: chưa có bboxLocal, chạy scripts/build_symbols_dxf.py --update-metadata")
            self.symbols[cls] = SymbolDef(
                detector_class=cls,
                class_id=int(m["classId"]),
                type=m["type"],
                drawing_state=m.get("drawingState"),
                svg_file=assets_dir / m["svgFile"],
                dxf_block_name=m["dxfBlockName"],
                size=(float(m["size"]["width"]), float(m["size"]["height"])),
                bbox_local=tuple(float(v) for v in m["bboxLocal"]),
                net_behavior=m["netBehavior"],
                conduction=m["conduction"],
                orientation_sensitive=bool(m["orientationSensitive"]),
                allowed_rotations_deg=tuple(m["allowedRotationsDeg"]),
                allow_mirror=bool(m["allowMirror"]),
                ports=tuple(PortDef(p["id"], p["role"], float(p["localX"]), float(p["localY"]),
                                    (float(p["direction"][0]), float(p["direction"][1]))) for p in m["ports"]),
                cad_attributes=tuple(m["cadAttributes"]),
            )

    @classmethod
    def load(cls, path: Path = METADATA_PATH) -> "SymbolLibrary":
        return cls(json.loads(path.read_text(encoding="utf-8")), path.parent)

    def __getitem__(self, detector_class: str) -> SymbolDef:
        return self.symbols[detector_class]

    def __contains__(self, detector_class: str) -> bool:
        return detector_class in self.symbols

    @property
    def classes(self) -> list[str]:
        return sorted(self.symbols, key=lambda c: self.symbols[c].class_id)

    def classes_of_type(self, device_type: str) -> list[str]:
        return [c for c in self.classes if self.symbols[c].type == device_type]

    def project_ports(self, detector_class: str, placement: Placement) -> list[ProjectedPort]:
        """Chiếu mọi cổng của ký hiệu ra tọa độ trang (SDD 2.2)."""
        sym = self.symbols[detector_class]
        return [
            ProjectedPort(p.id, p.role, local_to_page(p.local_x, p.local_y, placement),
                          direction_to_page(*p.direction, placement))
            for p in sym.ports
        ]

    def page_bbox(self, detector_class: str, placement: Placement) -> tuple[float, float, float, float]:
        """bbox [x, y, w, h] trên trang của hình ký hiệu (đường tâm nét) khi đặt theo placement."""
        x0, y0, x1, y1 = rotated_bbox_local(self.symbols[detector_class].bbox_local, placement.rotation_deg,
                                            placement.mirror_x)
        s = placement.scale
        return placement.x + s * x0, placement.y + s * y0, s * (x1 - x0), s * (y1 - y0)

    def placement_from_bbox(self, detector_class: str, bbox: tuple[float, float, float, float],
                            rotation_deg: int, mirror_x: bool) -> Placement:
        return placement_from_bbox(bbox, self.symbols[detector_class].bbox_local, rotation_deg, mirror_x)


@lru_cache(maxsize=1)
def default_library() -> SymbolLibrary:
    return SymbolLibrary.load()
