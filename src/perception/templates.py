"""Vẽ mẫu ký hiệu thành ảnh để so khớp (dùng chung cho detector NCC và OrientationEstimator).

Hình lấy từ assets/symbols_svg (cùng nguồn với bộ sinh và bộ xuất), đặt theo SDD 2.2 bằng
geometry.local_to_page. Mẫu là ảnh "mực" float32 trong [0, 1] (1 = nét).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import cv2
import numpy as np

from src.core.geometry import Placement, local_to_page, rotated_bbox_local
from src.core.symbol_lib import SymbolLibrary
from src.core.symbol_svg import parse_symbol_svg

_SHIFT = 4                      # vẽ dưới điểm ảnh của OpenCV: tọa độ × 16
_ONE = 1 << _SHIFT


@lru_cache(maxsize=64)
def _primitives(svg_path: str):
    from pathlib import Path
    return tuple(parse_symbol_svg(Path(svg_path)))


@dataclass(frozen=True)
class Template:
    detector_class: str
    rotation_deg: int
    mirror_x: bool
    scale: float                            # px / lu
    image: np.ndarray                       # float32 [0, 1]
    bbox_in_template: tuple[float, float, float, float]   # x, y, w, h của hình ký hiệu trong mẫu
    axis: np.ndarray | None = None          # bool: dải quanh trục dây qua các cổng (để kiểm phần dư)


def _pts(points, placement: Placement, ox: float, oy: float) -> np.ndarray:
    out = []
    for u, v in points:
        x, y = local_to_page(u, v, placement)
        out.append((round((x + ox) * _ONE), round((y + oy) * _ONE)))
    return np.array(out, dtype=np.int32)


def draw_symbol(canvas: np.ndarray, lib: SymbolLibrary, cls: str, placement: Placement, stroke_px: float,
                ox: float = 0.0, oy: float = 0.0, value: float = 1.0) -> None:
    """Vẽ ký hiệu lên canvas float32 (giá trị mực = value). stroke_px ứng với stroke-width 2 lu."""
    sym = lib[cls]
    for prim in _primitives(str(sym.svg_file)):
        t = max(1, round(stroke_px * prim.stroke_width / 2.0))
        if prim.kind == "circle":
            (cx, cy), = prim.points
            x, y = local_to_page(cx, cy, placement)
            r = prim.radius * placement.scale
            center = (round((x + ox) * _ONE), round((y + oy) * _ONE))
            if prim.filled:
                cv2.circle(canvas, center, round(r * _ONE), value, -1, cv2.LINE_AA, _SHIFT)
            cv2.circle(canvas, center, round(r * _ONE), value, t, cv2.LINE_AA, _SHIFT)
            continue
        pts = _pts(prim.points, placement, ox, oy)
        if prim.filled:
            cv2.fillPoly(canvas, [pts], value, cv2.LINE_AA, _SHIFT)
        cv2.polylines(canvas, [pts], prim.closed, value, t, cv2.LINE_AA, _SHIFT)


def render_template(lib: SymbolLibrary, cls: str, rotation_deg: int, mirror_x: bool, scale: float,
                    stroke_px: float, pad_px: float, stubs: bool = True, blur_sigma: float = 0.0) -> Template:
    """Mẫu của một tư thế: hình ký hiệu + lề pad_px; stubs=True vẽ thêm đoạn dây ngắn từ cổng ra mép mẫu
    (dây luôn nối vào cổng trên bản vẽ thật, nên mẫu có dây khớp tốt hơn)."""
    x0, y0, x1, y1 = rotated_bbox_local(lib[cls].bbox_local, rotation_deg, mirror_x)
    half = stroke_px / 2
    w_px = (x1 - x0) * scale + 2 * (pad_px + half)
    h_px = (y1 - y0) * scale + 2 * (pad_px + half)
    W, H = max(3, math.ceil(w_px)), max(3, math.ceil(h_px))
    # gốc cục bộ đặt sao cho hộp ký hiệu nằm giữa mẫu
    cx = (W - (x1 - x0) * scale) / 2 - x0 * scale
    cy = (H - (y1 - y0) * scale) / 2 - y0 * scale
    placement = Placement(cx, cy, rotation_deg, mirror_x, scale)
    canvas = np.zeros((H, W), np.float32)
    draw_symbol(canvas, lib, cls, placement, stroke_px)
    if stubs:
        t = max(1, round(stroke_px))
        for port in lib.project_ports(cls, placement):
            (px, py), (dx, dy) = port.xy, port.direction
            ex, ey = px + dx * (pad_px + half + 1), py + dy * (pad_px + half + 1)
            cv2.line(canvas, (round(px * _ONE), round(py * _ONE)), (round(ex * _ONE), round(ey * _ONE)), 1.0, t,
                     cv2.LINE_AA, _SHIFT)
    if blur_sigma > 0:
        canvas = cv2.GaussianBlur(canvas, (0, 0), blur_sigma)
    np.clip(canvas, 0, 1, out=canvas)
    # trục dây: từ mỗi cổng đi vào trong thân ký hiệu hết bề dài hộp theo hướng đó, dày ≈ 2 nét + mờ
    axis = np.zeros((H, W), np.uint8)
    t_axis = max(1, round(2 * stroke_px + 2 * blur_sigma))
    span = max((x1 - x0), (y1 - y0)) * scale
    for port in lib.project_ports(cls, placement):
        (px, py), (dx, dy) = port.xy, port.direction
        a = (round((px + dx * (pad_px + half + 1)) * _ONE), round((py + dy * (pad_px + half + 1)) * _ONE))
        b = (round((px - dx * span) * _ONE), round((py - dy * span) * _ONE))
        cv2.line(axis, a, b, 1, t_axis, cv2.LINE_8, _SHIFT)
    bx, by = cx + x0 * scale, cy + y0 * scale
    return Template(cls, rotation_deg, mirror_x, scale, canvas, (bx, by, (x1 - x0) * scale, (y1 - y0) * scale),
                    axis > 0)


def poses(lib: SymbolLibrary, cls: str) -> list[tuple[int, bool]]:
    """Các tổ hợp (góc, lật) hợp lệ của lớp, bỏ tổ hợp cho cùng hình (lớp đối xứng không lật)."""
    sym = lib[cls]
    mirrors = (False, True) if sym.allow_mirror else (False,)
    return [(r, m) for r in sym.allowed_rotations_deg for m in mirrors]


def ink_image(gray: np.ndarray, blur_sigma: float = 0.0) -> np.ndarray:
    """Ảnh xám uint8 → ảnh mực float32 [0, 1] (1 = đen)."""
    ink = (255.0 - gray.astype(np.float32)) / 255.0
    if blur_sigma > 0:
        ink = cv2.GaussianBlur(ink, (0, 0), blur_sigma)
    return ink
