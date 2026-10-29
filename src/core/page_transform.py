"""Phép đổi tọa độ từ trang PDF (point, gốc trên-trái) sang ảnh raster đã xoay (px).

Dùng chung cho rasterize_pdf.py và extract_vector.py để ảnh và nhãn vector khớp nhau
từng pixel. Quy ước hệ ảnh theo SDD mục 2.1: gốc trên-trái, Y hướng xuống.
"""
from __future__ import annotations

from dataclasses import dataclass

PT_PER_MM = 72.0 / 25.4


@dataclass(frozen=True)
class PageTransform:
    width_pt: float        # kích thước trang PDF sau /Rotate của chính PDF, chưa xoay thêm
    height_pt: float
    px_per_mm: float
    rotation_deg: int = 0  # xoay thêm theo chiều kim đồng hồ để bản vẽ đứng đúng chiều
    # Kích thước bitmap thực tế trình render trả về (trước khi xoay). pdfium có thể làm tròn
    # lên 1 px so với width_pt × scale; phép xoay phải dùng đúng kích thước này.
    render_size_px: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.rotation_deg not in (0, 90, 180, 270):
            raise ValueError(f"rotation_deg phải thuộc {{0, 90, 180, 270}}, nhận {self.rotation_deg}")

    @property
    def scale(self) -> float:
        """Số px trên 1 point."""
        return self.px_per_mm / PT_PER_MM

    @property
    def unrotated_size_px(self) -> tuple[int, int]:
        if self.render_size_px is not None:
            return self.render_size_px
        return round(self.width_pt * self.scale), round(self.height_pt * self.scale)

    @property
    def size_px(self) -> tuple[int, int]:
        w, h = self.unrotated_size_px
        return (h, w) if self.rotation_deg in (90, 270) else (w, h)

    @property
    def page_size_mm(self) -> tuple[float, float]:
        w, h = self.width_pt / PT_PER_MM, self.height_pt / PT_PER_MM
        return (h, w) if self.rotation_deg in (90, 270) else (w, h)

    def to_px(self, x_pt: float, y_pt: float) -> tuple[float, float]:
        """Điểm trên trang PDF (point) → điểm trên ảnh đã xoay (px)."""
        x, y = x_pt * self.scale, y_pt * self.scale
        w, h = self.unrotated_size_px
        if self.rotation_deg == 90:
            return h - y, x
        if self.rotation_deg == 180:
            return w - x, h - y
        if self.rotation_deg == 270:
            return y, w - x
        return x, y
