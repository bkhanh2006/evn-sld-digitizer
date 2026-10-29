"""Đọc danh mục trang bản vẽ gốc (configs/raw_pages.yaml)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "configs" / "raw_pages.yaml"


@dataclass(frozen=True)
class RawPage:
    id: str
    file: Path
    page: int              # đánh số từ 1 như trên trình xem PDF
    drawing_type: str      # "A" | "B"
    rotation_deg: int
    px_per_mm: float


def load_raw_pages(config_path: Path = DEFAULT_CONFIG) -> list[RawPage]:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    defaults = cfg.get("defaults", {})
    pages = []
    for entry in cfg["pages"]:
        merged = {**defaults, **entry}
        pages.append(RawPage(
            id=merged["id"],
            file=ROOT / merged["file"],
            page=int(merged["page"]),
            drawing_type=merged["drawingType"],
            rotation_deg=int(merged["rotationDeg"]),
            px_per_mm=float(merged["pxPerMm"]),
        ))
    return pages


def find_raw_page(page_id: str, config_path: Path = DEFAULT_CONFIG) -> RawPage:
    for p in load_raw_pages(config_path):
        if p.id == page_id:
            return p
    raise KeyError(f"Không có trang '{page_id}' trong {config_path}")
