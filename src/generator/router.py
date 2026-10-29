"""Đi dây trực giao bằng A* trên lưới thô (SDD 8.1.3 bước 4).

- Chỉ đi ngang/dọc. Chi phí = độ dài + λ_bend × số lần rẽ + λ_cross × số lần cắt dây khác.
- Không đi xuyên vật cản (bbox ký hiệu, nhãn, thanh cái đã nới rộng).
- Chỉ cắt dây khác vuông góc: quanh mỗi đoạn dây có "hành lang" rộng ±clearance; trong hành lang
  của đoạn dọc chỉ được đi ngang thẳng (không rẽ), và ngược lại. Quanh đầu mút/góc gấp của dây
  không được đi qua, để điểm cắt luôn nằm giữa đoạn.
- Lưới gồm các đường cách đều `cell` cộng tọa độ của điểm dẫn ra từ cổng đầu và cuối, nên đường đi
  khớp đúng cổng mà không cần nấc lệch nhỏ.
Đơn vị là đơn vị của tọa độ truyền vào (bộ sinh dùng mm).
"""
from __future__ import annotations

import bisect
import heapq
from dataclasses import dataclass

Vec = tuple[float, float]
DIRS = ((1, 0), (-1, 0), (0, 1), (0, -1))
_EPS = 1e-9


@dataclass(frozen=True)
class Crossing:
    position: Vec
    wire_id: str              # dây đã có bị dây mới cắt qua


def simplify(points: list[Vec]) -> list[Vec]:
    """Bỏ điểm trùng và điểm thẳng hàng ở giữa."""
    out: list[Vec] = []
    for p in points:
        if out and abs(out[-1][0] - p[0]) < _EPS and abs(out[-1][1] - p[1]) < _EPS:
            continue
        if len(out) >= 2:
            (ax, ay), (bx, by) = out[-2], out[-1]
            if (abs(ax - bx) < _EPS and abs(bx - p[0]) < _EPS) or (abs(ay - by) < _EPS and abs(by - p[1]) < _EPS):
                out[-1] = p
                continue
        out.append(p)
    return out


def segments(points: list[Vec]) -> list[tuple[Vec, Vec]]:
    return [(a, b) for a, b in zip(points, points[1:]) if a != b]


def perpendicular_crossing(s1: tuple[Vec, Vec], s2: tuple[Vec, Vec], margin: float = 1e-6) -> Vec | None:
    """Giao điểm nằm hẳn bên trong hai đoạn trực giao vuông góc nhau; None nếu không có."""
    (a, b), (c, d) = s1, s2
    h1, h2 = abs(a[1] - b[1]) < _EPS, abs(c[1] - d[1]) < _EPS
    if h1 == h2:
        return None
    (h, v) = (s1, s2) if h1 else (s2, s1)
    y, x = h[0][1], v[0][0]
    if min(h[0][0], h[1][0]) + margin < x < max(h[0][0], h[1][0]) - margin and \
            min(v[0][1], v[1][1]) + margin < y < max(v[0][1], v[1][1]) - margin:
        return (x, y)
    return None


class OrthogonalRouter:
    def __init__(self, bounds: tuple[float, float, float, float], cell: float, clearance: float,
                 bend_cost: float, cross_cost: float):
        self.bounds, self.cell, self.clear = bounds, cell, clearance
        self.bend_cost, self.cross_cost = bend_cost, cross_cost
        self.obstacles: list[tuple[float, float, float, float]] = []
        self.wires: list[tuple[str, tuple[Vec, Vec]]] = []

    def add_obstacle(self, rect: tuple[float, float, float, float], pad: float | None = None) -> None:
        """rect = (x0, y0, x1, y1); nới rộng thêm pad (mặc định = clearance)."""
        p = self.clear if pad is None else pad
        x0, y0, x1, y1 = rect
        self.obstacles.append((min(x0, x1) - p, min(y0, y1) - p, max(x0, x1) + p, max(y0, y1) + p))

    def add_wire(self, wire_id: str, points: list[Vec]) -> None:
        for s in segments(points):
            self.wires.append((wire_id, s))

    # ------------------------------------------------------------------ lưới

    def _axis(self, lo: float, hi: float, extra: list[float]) -> list[float]:
        n = int((hi - lo) / self.cell)
        vals = {round(lo + i * self.cell, 6) for i in range(n + 1)} | {round(v, 6) for v in extra if lo <= v <= hi}
        return sorted(vals)

    def _grid(self, extra_x: list[float], extra_y: list[float]):
        bx0, by0, bx1, by1 = self.bounds
        xs, ys = self._axis(bx0, bx1, extra_x), self._axis(by0, by1, extra_y)
        nx, ny = len(xs), len(ys)
        blocked = [False] * (nx * ny)
        flag_v = [False] * (nx * ny)      # hành lang của đoạn dây dọc: chỉ đi ngang qua
        flag_h = [False] * (nx * ny)

        def mark(arr, x0, y0, x1, y1):
            i0, i1 = bisect.bisect_left(xs, x0 - _EPS), bisect.bisect_right(xs, x1 + _EPS)
            j0, j1 = bisect.bisect_left(ys, y0 - _EPS), bisect.bisect_right(ys, y1 + _EPS)
            for j in range(j0, j1):
                row = j * nx
                for i in range(i0, i1):
                    arr[row + i] = True

        for r in self.obstacles:
            mark(blocked, *r)
        c = self.clear
        for _, ((ax, ay), (bx, by)) in self.wires:
            if abs(ax - bx) < _EPS:
                mark(flag_v, ax - c, min(ay, by), ax + c, max(ay, by))
            else:
                mark(flag_h, min(ax, bx), ay - c, max(ax, bx), ay + c)
            for px, py in ((ax, ay), (bx, by)):
                mark(blocked, px - c, py - c, px + c, py + c)
        for k in range(nx * ny):
            if flag_v[k] and flag_h[k]:
                blocked[k] = True
        return xs, ys, blocked, flag_v, flag_h

    # ------------------------------------------------------------------ A*

    def route(self, start: Vec, start_dir: Vec, end: Vec, end_dir: Vec, lead: float | None = None) -> list[Vec] | None:
        """Đường từ cổng start (hướng ra start_dir) tới cổng end (hướng ra end_dir); None nếu không có."""
        lead = lead if lead is not None else self.clear + self.cell
        ls = (start[0] + start_dir[0] * lead, start[1] + start_dir[1] * lead)
        le = (end[0] + end_dir[0] * lead, end[1] + end_dir[1] * lead)
        xs, ys, blocked, flag_v, flag_h = self._grid([ls[0], le[0]], [ls[1], le[1]])
        nx = len(xs)

        def idx(p: Vec) -> tuple[int, int] | None:
            i, j = bisect.bisect_left(xs, p[0] - 1e-6), bisect.bisect_left(ys, p[1] - 1e-6)
            if i < nx and j < len(ys) and abs(xs[i] - p[0]) < 1e-6 and abs(ys[j] - p[1]) < 1e-6:
                return i, j
            return None

        si, ei = idx(ls), idx(le)
        if si is None or ei is None or blocked[si[1] * nx + si[0]] or blocked[ei[1] * nx + ei[0]]:
            return None
        arrive_dir = DIRS.index((-end_dir[0], -end_dir[1]))
        d0 = DIRS.index((round(start_dir[0]), round(start_dir[1])))
        ex, ey = xs[ei[0]], ys[ei[1]]

        def h(i: int, j: int) -> float:
            return abs(xs[i] - ex) + abs(ys[j] - ey)

        start_state = (si[0], si[1], d0)
        goal = ("goal",)
        g = {start_state: 0.0}
        prev: dict[tuple, tuple] = {}
        tie = 0                                   # phá hòa trong heap, tránh so sánh state khác kiểu
        heap = [(h(*si), 0.0, tie, start_state)]
        while heap:
            f, cost, _, state = heapq.heappop(heap)
            if state == goal:
                return self._finish(prev[goal], prev, xs, ys, start, end)
            if cost > g.get(state, float("inf")) + _EPS:
                continue
            i, j, d = state
            if (i, j) == ei:
                total = cost + (self.bend_cost if d != arrive_dir else 0.0)
                if total < g.get(goal, float("inf")):
                    g[goal], prev[goal] = total, state
                    tie += 1
                    heapq.heappush(heap, (total, total, tie, goal))
                continue
            k = j * nx + i
            for nd, (dx, dy) in enumerate(DIRS):
                if (dx, dy) == (-DIRS[d][0], -DIRS[d][1]):
                    continue
                if flag_v[k] and (dy != 0 or nd != d):
                    continue
                if flag_h[k] and (dx != 0 or nd != d):
                    continue
                ni, nj = i + dx, j + dy
                if not (0 <= ni < nx and 0 <= nj < len(ys)):
                    continue
                nk = nj * nx + ni
                if blocked[nk] or (flag_v[nk] and dy != 0) or (flag_h[nk] and dx != 0):
                    continue
                step = abs(xs[ni] - xs[i]) + abs(ys[nj] - ys[j])
                c = cost + step + (self.bend_cost if nd != d else 0.0)
                if (flag_v[nk] and not flag_v[k]) or (flag_h[nk] and not flag_h[k]):
                    c += self.cross_cost
                ns = (ni, nj, nd)
                if c < g.get(ns, float("inf")) - _EPS:
                    g[ns] = c
                    prev[ns] = state
                    tie += 1
                    heapq.heappush(heap, (c + h(ni, nj), c, tie, ns))
        return None

    @staticmethod
    def _finish(state, prev, xs, ys, start: Vec, end: Vec) -> list[Vec]:
        pts = []
        s = state
        while s is not None:
            pts.append((xs[s[0]], ys[s[1]]))
            s = prev.get(s)
        pts.reverse()
        return simplify([start, *pts, end])

    def crossings(self, points: list[Vec]) -> list[Crossing]:
        """Các điểm dây mới cắt vuông góc qua dây đã có."""
        out = []
        for s in segments(points):
            for wid, t in self.wires:
                p = perpendicular_crossing(s, t)
                if p is not None:
                    out.append(Crossing(p, wid))
        return out
