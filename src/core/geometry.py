"""Phép chiếu cổng ký hiệu ra trang và quy đổi sang AutoCAD (SDD mục 2.2–2.3).

Hệ ảnh: gốc trên-trái, Y hướng xuống, góc θ dương quay theo chiều kim đồng hồ trên màn hình.
Hệ CAD: gốc dưới-trái, Y hướng lên, đơn vị mm.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

ROTATIONS = (0, 90, 180, 270)


@dataclass(frozen=True)
class Placement:
    """Đặt một ký hiệu lên trang: tâm (gốc cục bộ) tại (x, y) px, xoay θ, lật X cục bộ trước khi xoay."""
    x: float
    y: float
    rotation_deg: int = 0
    mirror_x: bool = False
    scale: float = 1.0      # px / lu

    def __post_init__(self) -> None:
        if self.rotation_deg not in ROTATIONS:
            raise ValueError(f"rotation_deg phải thuộc {ROTATIONS}, nhận {self.rotation_deg}")


def _rot(theta_deg: float) -> tuple[float, float, float, float]:
    t = math.radians(theta_deg)
    c, s = math.cos(t), math.sin(t)
    # Góc bội của 90° cho giá trị đúng tuyệt đối, tránh sai số 1e-17
    if theta_deg % 90 == 0:
        c, s = round(c), round(s)
    return c, -s, s, c


def rotate_mirror(u: float, v: float, rotation_deg: int, mirror_x: bool) -> tuple[float, float]:
    """R(θ) · M · (u, v)."""
    if mirror_x:
        u = -u
    a, b, c, d = _rot(rotation_deg)
    return a * u + b * v, c * u + d * v


def local_to_page(u: float, v: float, p: Placement) -> tuple[float, float]:
    """X = c + s · R(θ) · M · u"""
    du, dv = rotate_mirror(u, v, p.rotation_deg, p.mirror_x)
    return p.x + p.scale * du, p.y + p.scale * dv


def direction_to_page(dx: float, dy: float, p: Placement) -> tuple[float, float]:
    """Hướng ra của cổng: biến đổi như điểm nhưng không nhân scale, không cộng tâm."""
    return rotate_mirror(dx, dy, p.rotation_deg, p.mirror_x)


def rotated_bbox_local(bbox_local: tuple[float, float, float, float], rotation_deg: int,
                       mirror_x: bool) -> tuple[float, float, float, float]:
    """Hộp (x0, y0, x1, y1) cục bộ sau R·M, vẫn ở đơn vị lu và quanh gốc cục bộ."""
    x0, y0, x1, y1 = bbox_local
    pts = [rotate_mirror(u, v, rotation_deg, mirror_x) for u, v in ((x0, y0), (x1, y0), (x1, y1), (x0, y1))]
    xs, ys = [q[0] for q in pts], [q[1] for q in pts]
    return min(xs), min(ys), max(xs), max(ys)


def placement_from_bbox(bbox: tuple[float, float, float, float], bbox_local: tuple[float, float, float, float],
                        rotation_deg: int, mirror_x: bool) -> Placement:
    """Từ bbox trang [x, y, w, h] và hướng đã biết, suy ra tâm và scale (SDD 8.2.3).

    scale = trung bình tỉ lệ theo hai chiều của hộp đã xoay; tâm = tâm bbox trừ độ lệch của
    tâm hộp cục bộ so với gốc (ký hiệu lệch tâm như MBA, TU).
    """
    x, y, w, h = bbox
    rx0, ry0, rx1, ry1 = rotated_bbox_local(bbox_local, rotation_deg, mirror_x)
    s = 0.5 * (w / (rx1 - rx0) + h / (ry1 - ry0))
    cx_off, cy_off = 0.5 * (rx0 + rx1), 0.5 * (ry0 + ry1)
    return Placement(x + w / 2 - s * cx_off, y + h / 2 - s * cy_off, rotation_deg, mirror_x, s)


# ---------------------------------------------------------------- AutoCAD (SDD 2.3)

def to_cad_point(x: float, y: float, page_height_px: float, px_per_mm: float) -> tuple[float, float]:
    """Ảnh (px, Y xuống, gốc trên-trái) → CAD (mm, Y lên, gốc dưới-trái)."""
    return x / px_per_mm, (page_height_px - y) / px_per_mm


def from_cad_point(x_mm: float, y_mm: float, page_height_px: float, px_per_mm: float) -> tuple[float, float]:
    return x_mm * px_per_mm, page_height_px - y_mm * px_per_mm


def cad_insert_params(p: Placement, px_per_mm: float, library_unit_mm: float) -> dict:
    """Thuộc tính INSERT: rotation = −θ, xscale = m·k, yscale = k, với k = s / s_exp.

    Block DXF phải được vẽ ở hệ Y hướng lên: cổng cục bộ (u, v) nằm tại (u, −v) × libraryUnitMm.
    """
    k = p.scale / (px_per_mm * library_unit_mm)
    m = -1.0 if p.mirror_x else 1.0
    return {"rotation": (-p.rotation_deg) % 360.0, "xscale": m * k, "yscale": k}


def local_to_block_mm(u: float, v: float, library_unit_mm: float) -> tuple[float, float]:
    """Tọa độ cục bộ ký hiệu (lu, Y xuống) → tọa độ trong block DXF (mm, Y lên)."""
    return u * library_unit_mm, -v * library_unit_mm
