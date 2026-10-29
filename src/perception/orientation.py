"""Ước lượng hướng ký hiệu và chiếu cổng (SDD 8.2.3).

- Lớp đối xứng điện: θ = 0 nếu tỉ lệ bbox gần tỉ lệ `size` của lớp, ngược lại 90. Giữ góc/lật của
  detector khi cùng trục (dao cách ly lật trái/phải chỉ ảnh hưởng hình vẽ lại).
- Lớp bất đối xứng: vẽ ký hiệu ở mọi tổ hợp `allowedRotationsDeg` × `allowMirror`, co theo bbox, so NCC
  với vùng ảnh (nới 10%); mẫu có thêm dây từ cổng ra mép vùng cắt; orientation_score = NCC tốt nhất − NCC tốt nhì.
"""
from __future__ import annotations

import math

import cv2
import numpy as np

from src.core.registry import register
from src.core.symbol_lib import SymbolLibrary, default_library
from src.core.types import DetectedSymbol, NormalizedImage
from src.perception.templates import draw_symbol, ink_image, poses


def axis_rotation(lib: SymbolLibrary, cls: str, bbox) -> int:
    """0 hoặc 90 theo tỉ lệ cạnh bbox so với size của lớp."""
    W0, H0 = lib[cls].size
    if 90 not in lib[cls].allowed_rotations_deg or W0 == H0:
        return 0
    r = math.log(max(bbox[3], 1e-6) / max(bbox[2], 1e-6))
    r0 = math.log(H0 / W0)
    return 0 if abs(r - r0) <= abs(r + r0) else 90


@register("orientation_estimator", "template_ncc")
class TemplateNCCOrientation:
    def __init__(self, crop_margin: float = 0.10, ambiguous_margin: float = 0.05, blur_w: float = 0.5,
                 shift_w: float = 1.0, lib: SymbolLibrary | None = None):
        self.lib = lib or default_library()
        self.margin, self.ambiguous = crop_margin, ambiguous_margin
        self.blur_w, self.shift_w = blur_w, shift_w

    def estimate(self, img: NormalizedImage, symbols: list[DetectedSymbol]) -> list[DetectedSymbol]:
        w = img.median_stroke_px
        ink = ink_image(img.pixels, self.blur_w * w)
        for s in symbols:
            sym = self.lib[s.detector_class]
            combos = poses(self.lib, s.detector_class)
            if sym.orientation_sensitive and len(combos) > 1:
                scored = sorted(((self._score(ink, s, r, m, w), r, m) for r, m in combos), reverse=True)
                best, rot, mir = scored[0]
                s.rotation_deg, s.mirror_x = rot, mir
                s.orientation_score = best - (scored[1][0] if len(scored) > 1 else -1.0)
            else:
                rot = axis_rotation(self.lib, s.detector_class, s.bbox)
                if s.rotation_deg is not None and s.rotation_deg % 180 == rot % 180 \
                        and s.rotation_deg in sym.allowed_rotations_deg:
                    rot = s.rotation_deg
                s.rotation_deg = rot
                s.mirror_x = bool(s.mirror_x and sym.allow_mirror)
                s.orientation_score = None
            placement = self.lib.placement_from_bbox(s.detector_class, s.bbox, s.rotation_deg, s.mirror_x)
            s.ports = self.lib.project_ports(s.detector_class, placement)
        return symbols

    def _score(self, ink: np.ndarray, s: DetectedSymbol, rot: int, mir: bool, w: float) -> float:
        x, y, bw, bh = s.bbox
        mx, my = self.margin * bw + w, self.margin * bh + w
        r = max(1, round(self.shift_w * w))
        H, W = ink.shape
        x0, y0 = math.floor(x - mx), math.floor(y - my)
        cw, ch = math.ceil(bw + 2 * mx), math.ceil(bh + 2 * my)
        wx0, wy0, wx1, wy1 = x0 - r, y0 - r, x0 + cw + r, y0 + ch + r
        if wx0 < 0 or wy0 < 0 or wx1 > W or wy1 > H:
            return -1.0
        placement = self.lib.placement_from_bbox(s.detector_class, s.bbox, rot, mir)
        canvas = np.zeros((ch, cw), np.float32)
        draw_symbol(canvas, self.lib, s.detector_class, placement, w, ox=-x0, oy=-y0)
        # dây nối vào cổng chạy ra tới mép vùng cắt: phân biệt đầu có dây với đầu không dây (mũi tên)
        L = 2 * max(cw, ch)
        for p in self.lib.project_ports(s.detector_class, placement):
            (px, py), (dx, dy) = p.xy, p.direction
            cv2.line(canvas, (round((px - x0) * 16), round((py - y0) * 16)),
                     (round((px + dx * L - x0) * 16), round((py + dy * L - y0) * 16)), 1.0, max(1, round(w)),
                     cv2.LINE_AA, 4)
        if self.blur_w > 0:
            canvas = cv2.GaussianBlur(canvas, (0, 0), self.blur_w * w)
        if canvas.std() < 1e-6:
            return -1.0
        resp = cv2.matchTemplate(ink[wy0:wy1, wx0:wx1], canvas, cv2.TM_CCOEFF_NORMED)
        return float(np.nan_to_num(resp, nan=-1.0).max())


@register("orientation_estimator", "given")
class GivenOrientation:
    """Giữ nguyên góc/lật đã có (ký hiệu cho sẵn), chỉ chiếu cổng."""

    def __init__(self, lib: SymbolLibrary | None = None, **_):
        self.lib = lib or default_library()

    def estimate(self, img: NormalizedImage, symbols: list[DetectedSymbol]) -> list[DetectedSymbol]:
        for s in symbols:
            placement = self.lib.placement_from_bbox(s.detector_class, s.bbox, s.rotation_deg or 0, s.mirror_x)
            s.ports = self.lib.project_ports(s.detector_class, placement)
        return symbols
