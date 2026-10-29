"""LabelMatcher (SDD 8.2.5): ghép chữ với thiết bị, thanh cái và đầu đường dây.

Bước 1 ghép 1–1 mã thiết bị bằng Hungarian có hàng/cột "không ghép": ma trận (m+n) × (n+m), mỗi phía
giả có chi phí c_null/2 nên một cặp thật chỉ được chọn khi chi phí < c_null. Ràng buộc cứng: lớp
detector phải thuộc `expected_types` của mã.
"""
from __future__ import annotations

import math

import numpy as np
from scipy.optimize import linear_sum_assignment

from src.core.registry import register
from src.core.symbol_lib import SymbolLibrary, default_library
from src.core.types import DetectedSymbol, LabelAssignment, TextItem, WireSegment
from src.perception.busbar_classifier import nearest_on_polyline

BIG = 1e6
# loại thiết bị có dòng thông số (63/63/63MVA, 4MVAr, 100kVA…)
PARAMETER_TYPES = frozenset({"transformer", "shunt_capacitor", "reactor", "current_transformer"})
LINE_TERMINAL_TYPE = "line_terminal"


def box_gap(a, b) -> float:
    dx = max(0.0, max(a[0] - (b[0] + b[2]), b[0] - (a[0] + a[2])))
    dy = max(0.0, max(a[1] - (b[1] + b[3]), b[1] - (a[1] + a[3])))
    return math.hypot(dx, dy)


def center(b) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def text_height(texts: list[TextItem]) -> float:
    hs = [min(t.bbox[2], t.bbox[3]) for t in texts if min(t.bbox[2], t.bbox[3]) > 0]
    return float(np.median(hs)) if hs else 20.0


@register("label_matcher", "hungarian_with_null")
class HungarianLabelMatcher:
    def __init__(self, search_text_heights: float = 8.0, w1: float = 1.0, w2: float = 0.5, null_cost: float = 0.6,
                 pending_ratio: float = 0.7, lib: SymbolLibrary | None = None):
        self.k = search_text_heights
        self.w1, self.w2 = w1, w2
        self.c_null, self.pending_ratio = null_cost, pending_ratio
        self.lib = lib or default_library()

    def cost(self, t: TextItem, d: DetectedSymbol, r_max: float) -> float:
        if t.parsed is None or d.detector_class not in t.parsed.expected_types:
            return math.inf
        gap = box_gap(t.bbox, d.bbox)
        if gap > r_max:
            return math.inf
        (tx, ty), (dx, dy) = center(t.bbox), center(d.bbox)
        return self.w1 * gap / r_max + self.w2 * min(abs(tx - dx), abs(ty - dy)) / r_max

    def match(self, texts: list[TextItem], symbols: list[DetectedSymbol],
              busbars: list[WireSegment]) -> list[LabelAssignment]:
        r_max = self.k * text_height(texts)
        out: list[LabelAssignment] = []

        # Bước 1: mã thiết bị ↔ thiết bị
        codes = [t for t in texts if t.parsed is not None and t.parsed.role == "device_code"]
        m, n = len(codes), len(symbols)
        if m and n:
            M = np.full((m + n, n + m), BIG)
            for i, t in enumerate(codes):
                for j, d in enumerate(symbols):
                    c = self.cost(t, d, r_max)
                    if math.isfinite(c):
                        M[i, j] = c
                M[i, n + i] = self.c_null / 2
            for j in range(n):
                M[m + j, j] = self.c_null / 2
            M[m:, n:] = 0.0
            rows, cols = linear_sum_assignment(M)
            for i, j in zip(rows, cols):
                if i < m and j < n and M[i, j] < self.c_null:
                    out.append(LabelAssignment(codes[i].id, "device", symbols[j].id, float(M[i, j]),
                                               pending=M[i, j] > self.pending_ratio * self.c_null))

        # Bước 2: dòng thông số → thiết bị có thông số gần nhất (không độc quyền)
        param_devs = [d for d in symbols if self.lib[d.detector_class].type in PARAMETER_TYPES]
        for t in texts:
            if t.parsed is None or t.parsed.role != "parameter" or not param_devs:
                continue
            d = min(param_devs, key=lambda d: box_gap(t.bbox, d.bbox))
            gap = box_gap(t.bbox, d.bbox)
            if gap <= 3 * r_max:
                out.append(LabelAssignment(t.id, "device", d.id, gap / r_max, pending=gap > r_max))

        # Bước 3: mã thanh cái → thanh cái gần nhất; tên lộ → mũi tên đầu đường dây gần nhất
        for t in texts:
            if t.parsed is None or t.parsed.role != "busbar_code" or not busbars:
                continue
            c = center(t.bbox)
            dist, b = min(((nearest_on_polyline(c, b.points)[0], b) for b in busbars), key=lambda x: x[0])
            if dist <= 2 * r_max:
                out.append(LabelAssignment(t.id, "busbar", b.id, dist / r_max, pending=dist > r_max))
        arrows = [d for d in symbols if self.lib[d.detector_class].type == LINE_TERMINAL_TYPE]
        for t in texts:
            if t.parsed is not None or not arrows:
                continue
            d = min(arrows, key=lambda d: box_gap(t.bbox, d.bbox))
            gap = box_gap(t.bbox, d.bbox)
            if gap <= 2 * r_max:
                out.append(LabelAssignment(t.id, "line_terminal", d.id, gap / r_max, pending=gap > r_max))
        return out
