"""Tách thanh cái khỏi dây và tìm vùng vẽ (khung bản vẽ, khung tên) — SDD 8.2.6.

Thanh cái là đoạn thẳng ngang/dọc dài có độ dày ≥ busbar_stroke_ratio × w. Độ dày đo theo từng lát
cắt vuông góc bằng tổng độ đậm (như scale_normalizer.line_stroke_width), nên không phụ thuộc ngưỡng
nhị phân. Thanh cái vẽ bằng hình chữ nhật tô đặc (C12 trên E18.9) cũng được đo đúng.
"""
from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from src.core.types import WireSegment


@dataclass(frozen=True)
class DrawingArea:
    """Vùng vẽ sơ đồ (x0, y0, x1, y1) px: bên trong khung, phía trên khung tên."""
    content: tuple[float, float, float, float]
    title_block: tuple[float, float, float, float] | None

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.content
        return x0 <= x <= x1 and y0 <= y <= y1


def _long_lines(ink: np.ndarray, length: int, horizontal: bool) -> np.ndarray:
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (length, 1) if horizontal else (1, length))
    return cv2.morphologyEx(ink.astype(np.uint8), cv2.MORPH_OPEN, k)


def find_drawing_area(ink: np.ndarray, frame_ratio: float = 0.6, title_zone: float = 0.3,
                      margin_px: float = 6.0, border_zone: float = 0.12) -> DrawingArea:
    """Khung bản vẽ: các đường ngang/dọc dài ≥ frame_ratio cạnh trang nằm sát biên (trong border_zone).
    Khung tên: đường ngang dài cao nhất trong title_zone dưới trang, khác đường khung dưới."""
    H, W = ink.shape
    rows = np.flatnonzero(_long_lines(ink, int(frame_ratio * W), True).any(axis=1))
    cols = np.flatnonzero(_long_lines(ink, int(frame_ratio * H), False).any(axis=0))
    top = max((r for r in rows if r < border_zone * H), default=0)
    bottom = min((r for r in rows if r > (1 - border_zone) * H), default=H - 1)
    left = max((c for c in cols if c < border_zone * W), default=0)
    right = min((c for c in cols if c > (1 - border_zone) * W), default=W - 1)
    title = None
    tb_rows = [r for r in rows if r > (1 - title_zone) * H and r < bottom - 0.02 * H]
    if tb_rows:
        ty = min(tb_rows)
        title = (float(left), float(ty), float(right), float(bottom))
        bottom = ty
    m = margin_px
    return DrawingArea((left + m, top + m, right - m, bottom - m), title)


def _profile_runs(mask: np.ndarray, min_len: int, max_gap: int) -> list[tuple[int, int]]:
    """Các đoạn True liên tiếp (cho phép khe ≤ max_gap) dài ≥ min_len trên mảng 1 chiều."""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    runs, start, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > max_gap + 1:
            runs.append((start, prev))
            start = i
        prev = i
    runs.append((start, prev))
    return [(a, b) for a, b in runs if b - a + 1 >= min_len]


def extract_busbars(gray: np.ndarray, ink: np.ndarray, w: float, ratio: float, min_len_px: float,
                    area: DrawingArea | None = None) -> tuple[list[WireSegment], np.ndarray]:
    """Trả về (thanh cái dạng WireSegment is_busbar=True, mask điểm ảnh thuộc thanh cái)."""
    H, W = gray.shape
    dark = (255.0 - gray.astype(np.float32)) / 255.0
    busbars: list[WireSegment] = []
    bmask = np.zeros((H, W), np.uint8)
    L = max(5, int(min_len_px))
    for horizontal in (True, False):
        lines = _long_lines(ink, L, horizontal)
        if area is not None:
            x0, y0, x1, y1 = (int(round(v)) for v in area.content)
            keep = np.zeros_like(lines)
            keep[max(0, y0):y1, max(0, x0):x1] = 1
            lines &= keep
        n, labels, stats, _ = cv2.connectedComponentsWithStats(lines, connectivity=8)
        for i in range(1, n):
            x, y, bw, bh, _ = stats[i]
            # nới 1 px để tính cả viền khử răng cưa
            x, y = max(0, x - 1), max(0, y - 1)
            bw, bh = min(W - x, bw + 2), min(H - y, bh + 2)
            comp = cv2.dilate((labels[y:y + bh, x:x + bw] == i).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
            d = dark[y:y + bh, x:x + bw] * comp
            if not horizontal:
                comp, d = comp.T, d.T
            # độ dày tại từng vị trí dọc theo đoạn = tổng độ đậm của lát cắt vuông góc
            thick = d.sum(axis=0)
            runs = _profile_runs(thick >= ratio * w, L, max_gap=int(3 * w))
            for a, b in runs:
                t = float(np.median(thick[a:b + 1]))
                rows = np.arange(comp.shape[0], dtype=np.float32)
                weights = d[:, a:b + 1].sum(axis=1)
                c = float((rows * weights).sum() / max(weights.sum(), 1e-6))
                if horizontal:
                    yc = y + c
                    pts = [(float(x + a), yc), (float(x + b + 1), yc)]
                    y0b, y1b = int(np.floor(yc - t / 2 - w)), int(np.ceil(yc + t / 2 + w))
                    bmask[max(0, y0b):y1b + 1, x + a:x + b + 1] = 1
                else:
                    xc = x + c
                    pts = [(xc, float(y + a)), (xc, float(y + b + 1))]
                    x0b, x1b = int(np.floor(xc - t / 2 - w)), int(np.ceil(xc + t / 2 + w))
                    bmask[y + a:y + b + 1, max(0, x0b):x1b + 1] = 1
                busbars.append(WireSegment(id="", points=pts, stroke_px=t, is_busbar=True, confidence=1.0))
    busbars.sort(key=lambda s: (s.points[0][1], s.points[0][0]))
    for i, b in enumerate(busbars, start=1):
        b.id = f"bus-{i:02d}"
    return busbars, bmask


def point_segment_distance(p, a, b) -> tuple[float, tuple[float, float]]:
    """Khoảng cách điểm–đoạn và chân đường vuông góc (kẹp trong đoạn)."""
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx + (p[1] - ay) * dy) / L2))
    fx, fy = ax + t * dx, ay + t * dy
    return float(np.hypot(p[0] - fx, p[1] - fy)), (fx, fy)


def nearest_on_polyline(p, points) -> tuple[float, tuple[float, float]]:
    best = (float("inf"), (0.0, 0.0))
    for a, b in zip(points, points[1:]):
        d = point_segment_distance(p, a, b)
        if d[0] < best[0]:
            best = d
    return best
