"""PortMatcher (SDD 8.3.1): ghép đầu mút dây tự do với cổng thiết bị bằng gán toàn cục.

C(e, p) = α · dist / d_norm(p) + β · (1 + v_e · n_p) / 2

- dist: khoảng cách từ đầu mút tới đoạn [cổng, cổng + n_p · g]. WireExtractor che bbox ký hiệu nới
  `grow` nên đầu dây dừng cách cổng khoảng g = grow + w/2 dọc theo hướng cổng (tinh chỉnh cổng,
  SDD 8.2.3): đo tới cả đoạn này thay vì riêng điểm cổng.
- v_e: hướng đi của dây khi tiến về đầu mút; n_p: hướng ra của cổng.
- Hungarian, mỗi đầu mút có một cột giả chi phí τ_candidate.

Sau gán: đầu mút sát thân thanh cái → `t_on_busbar`; cổng còn trống sát thanh cái, sát cổng đối diện
hoặc sát thân một dây → nối bằng dây ngắn (khe giữa hai thiết bị kề nhau bị mở hình thái xóa mất).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from src.core.evn_codes import EVNCodeGrammar
from src.core.types import CandidateNode, DetectedSymbol, NormalizedImage, WireExtraction, WireSegment
from src.perception.busbar_classifier import nearest_on_polyline, point_segment_distance
from src.perception.junction_classifier import end_direction

BIG = 1e6


@dataclass
class EndLink:
    kind: str                                   # port | busbar | dangling
    status: str                                 # confirmed | candidate | dangling
    point: tuple[float, float]                  # tọa độ đầu dây sau khi nắn
    device_id: str | None = None
    port_id: str | None = None
    busbar_id: str | None = None
    cost: float | None = None
    resolved_by: str | None = None


@dataclass
class PortMatchResult:
    links: dict[tuple[str, int], EndLink] = field(default_factory=dict)     # (seg_id, 0|−1) → liên kết
    unconnected_ports: list[tuple[str, str]] = field(default_factory=list)  # (symbol_id, port_id)


@dataclass(frozen=True)
class _End:
    seg_id: str
    idx: int                                    # 0 = đầu, −1 = cuối
    xy: tuple[float, float]
    v: tuple[float, float]                      # hướng đi khi tiến về đầu mút


class PortMatcher:
    def __init__(self, alpha: float = 1.0, beta: float = 0.5, port_accept: float = 0.35,
                 port_candidate: float = 0.80, busbar_snap_w: float = 2.0, port_gap_mm: float = 3.0,
                 mask_grow_w: float = 1.0, grammar: EVNCodeGrammar | None = None):
        self.alpha, self.beta = alpha, beta
        self.tau_accept, self.tau_cand = port_accept, port_candidate
        self.snap_w, self.gap_mm, self.grow_w = busbar_snap_w, port_gap_mm, mask_grow_w
        self.grammar = grammar

    def cost(self, e: _End, s: DetectedSymbol, port, w: float) -> float:
        g = (self.grow_w + 0.5) * w
        px, py = port.xy
        nx, ny = port.direction
        dist, _ = point_segment_distance(e.xy, (px, py), (px + nx * g, py + ny * g))
        d_norm = max(2 * w, 0.25 * min(s.bbox[2], s.bbox[3]))
        return self.alpha * dist / d_norm + self.beta * (1 + e.v[0] * nx + e.v[1] * ny) / 2

    def match(self, img: NormalizedImage, symbols: list[DetectedSymbol], extraction: WireExtraction,
              codes: dict[str, str] | None = None) -> PortMatchResult:
        """codes: symbol_id → mã đã ghép (để phân xử bằng luật hậu tố khi có nhiều ứng viên)."""
        w, ppm = img.median_stroke_px, img.px_per_mm
        segs = {s.id: s for s in extraction.segments}
        busbars = [s for s in extraction.segments if s.is_busbar]
        node_pts = [n.position for n in extraction.nodes]
        ends: list[_End] = []
        for s in extraction.segments:
            if s.is_busbar:
                continue
            for idx in (0, -1):
                p = s.points[idx]
                if any(math.dist(p, q) < 1.0 for q in node_pts):
                    continue
                dx, dy = end_direction(s.points, idx == 0, 2 * w)
                ends.append(_End(s.id, idx, p, (-dx, -dy)))
        ports = [(s, p) for s in symbols for p in s.ports]
        res = PortMatchResult()

        E, P = len(ends), len(ports)
        assigned_port: set[int] = set()
        if E and P:
            M = np.full((E, P + E), BIG)
            reach = 4 * max(s.bbox[2] + s.bbox[3] for s in symbols) + 10 * w
            for i, e in enumerate(ends):
                for j, (s, p) in enumerate(ports):
                    if abs(e.xy[0] - p.xy[0]) > reach or abs(e.xy[1] - p.xy[1]) > reach:
                        continue
                    c = self.cost(e, s, p, w)
                    if c <= self.tau_cand:
                        M[i, j] = c
                M[i, P + i] = self.tau_cand
            rows, cols = linear_sum_assignment(M)
            for i, j in zip(rows, cols):
                if j >= P or M[i, j] >= BIG:
                    continue
                s, p = ports[j]
                c = float(M[i, j])
                status = "confirmed" if c <= self.tau_accept else "candidate"
                resolved = None
                if status == "candidate" and codes:
                    resolved = self._evn_rule(i, M, ports, codes)
                res.links[(ends[i].seg_id, ends[i].idx)] = EndLink("port", status, p.xy, s.id, p.port_id,
                                                                   cost=c, resolved_by=resolved)
                assigned_port.add(j)

        # đầu mút chưa ghép: sát thanh cái → t_on_busbar, ngược lại treo
        for e in ends:
            key = (e.seg_id, e.idx)
            if key in res.links:
                continue
            hit = self._near_busbar(e.xy, busbars, w, self.snap_w * w + 2 * w)
            if hit is not None:
                res.links[key] = EndLink("busbar", "confirmed", hit[1], busbar_id=hit[0])
            else:
                res.links[key] = EndLink("dangling", "dangling", e.xy)

        # cổng còn trống
        free = [j for j in range(P) if j not in assigned_port]
        gap = self.gap_mm * ppm
        done: set[int] = set()
        new_id = 0
        for j in free:
            s, p = ports[j]
            hit = self._near_busbar(p.xy, busbars, w, gap)
            if hit is not None:
                new_id += 1
                sid = f"seg-port-{new_id:03d}"
                seg = WireSegment(sid, [p.xy, hit[1]], w, False, 0.6)
                extraction.segments.append(seg)
                res.links[(sid, 0)] = EndLink("port", "candidate", p.xy, s.id, p.port_id, cost=None)
                res.links[(sid, -1)] = EndLink("busbar", "confirmed", hit[1], busbar_id=hit[0])
                done.add(j)
        for j in free:
            if j in done:
                continue
            s, p = ports[j]
            best = None
            for k in free:
                if k == j or k in done or ports[k][0].id == s.id:
                    continue
                s2, p2 = ports[k]
                d = math.dist(p.xy, p2.xy)
                if d > gap or d < 1e-6:
                    continue
                ux, uy = (p2.xy[0] - p.xy[0]) / d, (p2.xy[1] - p.xy[1]) / d
                if p.direction[0] * ux + p.direction[1] * uy > 0.7 and \
                        -(p2.direction[0] * ux + p2.direction[1] * uy) > 0.7:
                    if best is None or d < best[0]:
                        best = (d, k)
            if best is not None:
                k = best[1]
                s2, p2 = ports[k]
                new_id += 1
                sid = f"seg-port-{new_id:03d}"
                extraction.segments.append(WireSegment(sid, [p.xy, p2.xy], w, False, 0.6))
                res.links[(sid, 0)] = EndLink("port", "candidate", p.xy, s.id, p.port_id)
                res.links[(sid, -1)] = EndLink("port", "candidate", p2.xy, s2.id, p2.port_id)
                done.update((j, k))
        for j in free:
            if j in done:
                continue
            s, p = ports[j]
            tap = self._tap_wire(p, extraction, gap, w)
            if tap is not None:
                seg_id, foot = tap
                node = self._split(extraction, segs, seg_id, foot)
                for idx, half in ((0, f"{seg_id}a"), (-1, f"{seg_id}b")):
                    if (seg_id, idx) in res.links:
                        res.links[(half, idx)] = res.links.pop((seg_id, idx))
                new_id += 1
                sid = f"seg-port-{new_id:03d}"
                extraction.segments.append(WireSegment(sid, [p.xy, foot], w, False, 0.6))
                node.incident_segment_ids.append(sid)
                res.links[(sid, 0)] = EndLink("port", "candidate", p.xy, s.id, p.port_id)
                done.add(j)
                continue
            res.unconnected_ports.append((s.id, p.port_id))
        return res

    def _evn_rule(self, i: int, M: np.ndarray, ports, codes: dict[str, str]) -> str | None:
        """Đầu mút có ≥ 2 ứng viên candidate: ghi nhận khi đúng một ứng viên khớp luật hậu tố DCL."""
        cands = [j for j in range(len(ports)) if self.tau_accept < M[i, j] <= self.tau_cand]
        if len(cands) < 2 or self.grammar is None:
            return None
        ok = 0
        for j in cands:
            code = codes.get(ports[j][0].id)
            parsed = self.grammar.parse(code) if code else None
            if parsed is not None and parsed.details.get("side", "").startswith("busbar"):
                ok += 1
        return "evn_rule" if ok == 1 else None

    @staticmethod
    def _near_busbar(p, busbars: list[WireSegment], w: float, extra: float):
        best = None
        for b in busbars:
            d, foot = nearest_on_polyline(p, b.points)
            lim = b.stroke_px / 2 + extra
            if d <= lim and (best is None or d < best[0]):
                best = (d, b.id, foot)
        return None if best is None else (best[1], best[2])

    @staticmethod
    def _tap_wire(port, extraction: WireExtraction, gap: float, w: float):
        """Cổng trống nằm sát thân một dây (không phải gần đầu mút) → điểm rẽ trên dây đó."""
        best = None
        for s in extraction.segments:
            if s.is_busbar:
                continue
            d, foot = nearest_on_polyline(port.xy, s.points)
            if d > gap or min(math.dist(foot, s.points[0]), math.dist(foot, s.points[-1])) < 2 * w:
                continue
            ux, uy = foot[0] - port.xy[0], foot[1] - port.xy[1]
            L = math.hypot(ux, uy)
            if L > 0 and (port.direction[0] * ux + port.direction[1] * uy) / L < 0.7:
                continue
            if best is None or d < best[0]:
                best = (d, s.id, foot)
        return None if best is None else (best[1], best[2])

    @staticmethod
    def _split(extraction: WireExtraction, segs: dict[str, WireSegment], seg_id: str, foot) -> CandidateNode:
        """Cắt dây tại foot thành hai đoạn và thêm nút junction t_plain ở đó."""
        s = next(x for x in extraction.segments if x.id == seg_id)
        pts = s.points
        best_i, best_d = 0, math.inf
        for i, (a, b) in enumerate(zip(pts, pts[1:])):
            d, _ = point_segment_distance(foot, a, b)
            if d < best_d:
                best_i, best_d = i, d
        first = pts[:best_i + 1] + [foot]
        second = [foot] + pts[best_i + 1:]
        extraction.segments.remove(s)
        a = WireSegment(f"{seg_id}a", first, s.stroke_px, False, s.confidence)
        b = WireSegment(f"{seg_id}b", second, s.stroke_px, False, s.confidence)
        extraction.segments += [a, b]
        segs[a.id], segs[b.id] = a, b
        for n in extraction.nodes:
            if seg_id in n.incident_segment_ids:
                # nút ở đầu mút hoặc giữa dây (crossing): chọn nửa chứa nút
                repl = a.id if nearest_on_polyline(n.position, first)[0] <= nearest_on_polyline(n.position, second)[0] \
                    else b.id
                n.incident_segment_ids = [repl if x == seg_id else x for x in n.incident_segment_ids]
        node = CandidateNode(f"cn-tap-{seg_id}", foot, [a.id, b.id], "junction", "t_plain", 0.6)
        extraction.nodes.append(node)
        return node
