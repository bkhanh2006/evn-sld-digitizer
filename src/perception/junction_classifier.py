"""JunctionClassifier bằng luật hình học (SDD 8.2.7).

- Bậc 3 → junction (`dot` nếu có chấm đặc, ngược lại `t_plain`).
- Bậc 4: có chấm → junction `dot`; không chấm và hai cặp cạnh đối nhau thẳng hàng → crossing
  `plain_cross`, ghép mỗi cặp thành một dây liên tục.
- Cầu vượt `hop_arc`: WireExtractor đã bỏ cung (mở hình thái), nên dây vượt hiện ra thành hai đoạn thẳng
  hàng có khe ≈ 2r, giữa khe có một dây vuông góc đi qua và trên ảnh gốc có mực ở nửa cung. Ghép hai
  đoạn thành một dây và ghi nút crossing.
- Bậc ≥ 5 hoặc bậc 4 không thẳng hàng → junction độ tin cậy thấp (pending).
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from src.core.registry import register
from src.core.types import CandidateNode, NormalizedImage, WireExtraction, WireSegment
from src.perception.busbar_classifier import nearest_on_polyline
from src.perception.scale_normalizer import ink_mask


def end_direction(points, at_start: bool, min_len: float = 0.0) -> tuple[float, float]:
    """Hướng đi ra khỏi đầu mút (từ đầu mút vào trong dây) — dùng để so cặp cạnh tại một nút."""
    pts = points if at_start else points[::-1]
    x0, y0 = pts[0]
    for x, y in pts[1:]:
        dx, dy = x - x0, y - y0
        L = math.hypot(dx, dy)
        if L > min_len or (x, y) == pts[-1]:
            return (dx / L, dy / L) if L > 0 else (0.0, 0.0)
    return 0.0, 0.0


def touches_start(seg: WireSegment, p) -> bool:
    a, b = seg.points[0], seg.points[-1]
    return math.dist(a, p) <= math.dist(b, p)


def join(s1: WireSegment, s2: WireSegment, at: tuple[float, float], new_id: str) -> WireSegment:
    """Nối s1 và s2 tại điểm `at` (đầu mút chung hoặc hai đầu đối diện qua khe) thành một dây."""
    p1 = s1.points if not touches_start(s1, at) else s1.points[::-1]   # p1 kết thúc tại at
    p2 = s2.points if touches_start(s2, at) else s2.points[::-1]       # p2 bắt đầu tại at
    pts = list(p1) + (list(p2[1:]) if math.dist(p1[-1], p2[0]) < 1e-6 else list(p2))
    return WireSegment(new_id, pts, 0.5 * (s1.stroke_px + s2.stroke_px), False, min(s1.confidence, s2.confidence))


@register("junction_classifier", "geometric_rules")
class GeometricJunctionClassifier:
    def __init__(self, dot_radius_w: float = 1.5, dot_fill: float = 0.8, hop_gap_mm=(0.8, 4.5),
                 hop_arc_ink: float = 0.25, collinear_tol_w: float = 1.5, opposite_cos: float = -0.85):
        self.dot_r, self.dot_fill = dot_radius_w, dot_fill
        self.hop_gap_mm = tuple(hop_gap_mm)
        self.hop_arc_ink = hop_arc_ink
        self.col_tol = collinear_tol_w
        self.opposite_cos = opposite_cos

    # ------------------------------------------------------------------ tiện ích

    def _has_dot(self, ink: np.ndarray, p, w: float) -> bool:
        r = max(1.5, self.dot_r * w)
        H, W = ink.shape
        x0, x1 = max(0, int(p[0] - r)), min(W, int(p[0] + r) + 1)
        y0, y1 = max(0, int(p[1] - r)), min(H, int(p[1] + r) + 1)
        yy, xx = np.mgrid[y0:y1, x0:x1]
        disk = (xx - p[0]) ** 2 + (yy - p[1]) ** 2 <= r * r
        return disk.any() and float(ink[y0:y1, x0:x1][disk].mean()) > self.dot_fill

    def _arc_ink(self, ink: np.ndarray, m, axis_dir, r: float) -> float:
        """Tỉ lệ mực trên nửa cung (bên nào nhiều hơn) bán kính r quanh m, trục theo axis_dir."""
        nx, ny = -axis_dir[1], axis_dir[0]
        H, W = ink.shape
        best = 0.0
        for side in (1, -1):
            hits = 0
            n = 0
            for t in np.linspace(0.2, math.pi - 0.2, 15):
                x = m[0] + r * math.cos(t) * axis_dir[0] + side * r * math.sin(t) * nx
                y = m[1] + r * math.cos(t) * axis_dir[1] + side * r * math.sin(t) * ny
                xi, yi = int(round(x)), int(round(y))
                if 0 <= xi < W and 0 <= yi < H:
                    n += 1
                    hits += bool(ink[max(0, yi - 1):yi + 2, max(0, xi - 1):xi + 2].any())
            if n:
                best = max(best, hits / n)
        return best

    # ------------------------------------------------------------------ phân loại

    def classify(self, img: NormalizedImage, extraction: WireExtraction) -> WireExtraction:
        w, ppm = img.median_stroke_px, img.px_per_mm
        ink = ink_mask(img.pixels)
        segs: dict[str, WireSegment] = {s.id: s for s in extraction.segments}
        nodes: list[CandidateNode] = []
        renamed: dict[str, str] = {}

        def resolve(sid: str) -> str:
            while sid in renamed:
                sid = renamed[sid]
            return sid

        for cn in extraction.nodes:
            ids = [resolve(s) for s in cn.incident_segment_ids if resolve(s) in segs]
            p = cn.position
            dot = self._has_dot(ink, p, w)
            node = replace(cn, incident_segment_ids=ids)
            if len(ids) == 3:
                node.node_type, node.style, node.confidence = "junction", "dot" if dot else "t_plain", 0.9
            elif len(ids) == 4 and dot:
                node.node_type, node.style, node.confidence = "junction", "dot", 0.9
            elif len(ids) == 4:
                pairs = self._opposite_pairs(ids, segs, p, w)
                if pairs is None:
                    node.node_type, node.style, node.confidence = "junction", "t_plain", 0.4
                else:
                    merged = []
                    for a, b in pairs:
                        new = join(segs[a], segs[b], p, a)
                        del segs[b]
                        segs[a] = new
                        renamed[b] = a
                        merged.append(a)
                    node.incident_segment_ids = merged
                    node.node_type, node.style, node.confidence = "crossing_no_connect", "plain_cross", 0.8
            elif len(ids) >= 5:
                node.node_type, node.style, node.confidence = "junction", "dot" if dot else "t_plain", 0.4
            else:
                node.node_type, node.confidence = "false_positive", 0.5
            nodes.append(node)

        nodes = [n for n in nodes if n.node_type != "false_positive"]
        for n in nodes:
            n.incident_segment_ids = list(dict.fromkeys(resolve(s) for s in n.incident_segment_ids))
        nodes += self._hops(ink, segs, nodes, w, ppm)
        return WireExtraction(segments=list(segs.values()), nodes=nodes, wire_mask=extraction.wire_mask)

    def _opposite_pairs(self, ids, segs, p, w):
        dirs = {s: end_direction(segs[s].points, touches_start(segs[s], p), 2 * w) for s in ids}
        best = None
        for other in ids[1:]:
            a = (ids[0], other)
            rest = tuple(s for s in ids[1:] if s != other)
            c1 = dirs[a[0]][0] * dirs[a[1]][0] + dirs[a[0]][1] * dirs[a[1]][1]
            c2 = dirs[rest[0]][0] * dirs[rest[1]][0] + dirs[rest[0]][1] * dirs[rest[1]][1]
            if c1 < self.opposite_cos and c2 < self.opposite_cos:
                score = c1 + c2
                if best is None or score < best[0]:
                    best = (score, [a, rest])
        if best is None or any(a == b for a, b in best[1]):
            return None
        return best[1]

    def _hops(self, ink, segs: dict[str, WireSegment], nodes: list[CandidateNode], w: float,
              ppm: float) -> list[CandidateNode]:
        """Tìm cầu vượt: cặp đầu mút tự do thẳng hàng, đối diện, khe trong hop_gap, có dây vuông góc ở giữa."""
        node_pts = [n.position for n in nodes]
        free = []      # (seg_id, điểm, hướng đi ra khỏi dây tại đầu mút)
        for s in segs.values():
            if s.is_busbar:
                continue
            for at_start in (True, False):
                p = s.points[0] if at_start else s.points[-1]
                if any(math.dist(p, q) < 1.0 for q in node_pts):
                    continue
                dx, dy = end_direction(s.points, at_start, 2 * w)
                free.append((s.id, p, (-dx, -dy)))
        gmin, gmax = self.hop_gap_mm[0] * ppm, self.hop_gap_mm[1] * ppm
        used: set[str] = set()
        out: list[CandidateNode] = []
        for i, (s1, p1, d1) in enumerate(free):
            for s2, p2, d2 in free[i + 1:]:
                if s1 == s2 or s1 in used or s2 in used:
                    continue
                gap = math.dist(p1, p2)
                if not gmin <= gap <= gmax:
                    continue
                ux, uy = (p2[0] - p1[0]) / gap, (p2[1] - p1[1]) / gap
                if d1[0] * ux + d1[1] * uy < 0.9 or -(d2[0] * ux + d2[1] * uy) < 0.9:
                    continue
                if abs(ux) > 0.1 and abs(uy) > 0.1:      # chỉ xét dây ngang/dọc
                    continue
                m = ((p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2)
                cross = None
                for s in segs.values():
                    if s.is_busbar or s.id in (s1, s2):
                        continue
                    d, _ = nearest_on_polyline(m, s.points)
                    if d <= self.col_tol * w + 1:
                        cross = s.id
                        break
                if cross is None:
                    continue
                arc = self._arc_ink(ink, m, (ux, uy), gap / 2)
                style, conf = ("hop_arc", 0.85) if arc >= self.hop_arc_ink else ("plain_cross", 0.5)
                new = join(segs[s1], segs[s2], p1, s1)
                del segs[s2]
                segs[s1] = new
                for n in nodes:
                    n.incident_segment_ids = [s1 if x == s2 else x for x in n.incident_segment_ids]
                used.update((s1, s2))
                out.append(CandidateNode(f"cn-hop-{len(out) + 1:03d}", m, [s1, cross], "crossing_no_connect",
                                         style, conf))
        return out
