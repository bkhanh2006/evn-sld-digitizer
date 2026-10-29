"""WireExtractor cổ điển (SDD 8.2.6): che ký hiệu và chữ → morphology → skeleton → truy vết polyline.

1. Che vùng không phải dây: ngoài vùng vẽ (khung, khung tên), bbox ký hiệu nới 1 w, chữ (trừ nét
   thẳng dài đi qua vùng chữ).
2. Tách thanh cái (busbar_classifier) rồi xóa khỏi ảnh dây.
3. Mở hình thái với kernel ngang + dọc (giữ nét thẳng, bỏ vụn chữ, cung cầu vượt); HoughLinesP lấy
   lại đoạn xiên dài.
4. Skeleton → đồ thị điểm ảnh → truy vết cạnh giữa các nút bậc ≠ 2 → tỉa gai, gộp nút gần nhau
   (chữ thập thành một nút bậc 4), nối cạnh qua nút bậc 2 → Douglas–Peucker.
Nút bậc ≥ 3 thành CandidateNode cho JunctionClassifier; đầu mút bậc 1 để PortMatcher ghép.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from skimage.morphology import skeletonize

from src.core.registry import register
from src.core.types import CandidateNode, DetectedSymbol, NormalizedImage, TextItem, WireExtraction, WireSegment
from src.perception.busbar_classifier import DrawingArea, extract_busbars, find_drawing_area
from src.perception.scale_normalizer import ink_mask

_NB = [(-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)]


# ---------------------------------------------------------------------------- đồ thị skeleton

@dataclass
class _Edge:
    a: int
    b: int
    pts: list[tuple[float, float]]          # (x, y) từ nút a tới nút b

    def length(self) -> float:
        p = np.asarray(self.pts)
        return float(np.hypot(*np.diff(p, axis=0).T).sum()) if len(p) > 1 else 0.0


@dataclass
class _Graph:
    pos: dict[int, tuple[float, float]] = field(default_factory=dict)
    edges: dict[int, _Edge] = field(default_factory=dict)
    _next_edge: int = 0

    def add_edge(self, e: _Edge) -> int:
        self._next_edge += 1
        self.edges[self._next_edge] = e
        return self._next_edge

    def incident(self) -> dict[int, list[int]]:
        inc: dict[int, list[int]] = {n: [] for n in self.pos}
        for k, e in self.edges.items():
            inc[e.a].append(k)
            if e.b != e.a:
                inc[e.b].append(k)
        return inc

    def degree(self) -> dict[int, int]:
        deg = {n: 0 for n in self.pos}
        for e in self.edges.values():
            deg[e.a] += 1
            deg[e.b] += 1
        return deg


def skeleton_graph(mask: np.ndarray) -> _Graph:
    """Skeleton nhị phân → đồ thị: nút = cụm điểm bậc ≥ 3 hoặc điểm đầu mút, cạnh = đường điểm bậc 2."""
    sk = skeletonize(mask > 0)
    H, W = sk.shape
    nb = cv2.filter2D(sk.astype(np.uint8), -1, np.array([[1, 1, 1], [1, 0, 1], [1, 1, 1]], np.uint8),
                      borderType=cv2.BORDER_CONSTANT)
    nb = np.where(sk, nb, 0)
    node_px = sk & (nb != 2) & (nb > 0)
    n_lab, lab = cv2.connectedComponents(node_px.astype(np.uint8), connectivity=8)
    g = _Graph()
    ys, xs = np.nonzero(node_px)
    sums: dict[int, list[float]] = {}
    for y, x in zip(ys, xs):
        s = sums.setdefault(int(lab[y, x]), [0.0, 0.0, 0])
        s[0] += x
        s[1] += y
        s[2] += 1
    for k, (sx, sy, c) in sums.items():
        g.pos[k] = (sx / c, sy / c)

    visited = np.zeros_like(sk)
    seen_pairs: set[tuple] = set()

    def nbrs(y, x):
        for dy, dx in _NB:
            yy, xx = y + dy, x + dx
            if 0 <= yy < H and 0 <= xx < W and sk[yy, xx]:
                yield yy, xx

    for y, x in zip(ys, xs):
        c0 = int(lab[y, x])
        for py, px in nbrs(y, x):
            if node_px[py, px]:
                c1 = int(lab[py, px])
                key = (min(c0, c1), max(c0, c1), min((y, x), (py, px)), max((y, x), (py, px)))
                if c1 != c0 and key not in seen_pairs:
                    seen_pairs.add(key)
                    g.add_edge(_Edge(c0, c1, [g.pos[c0], g.pos[c1]]))
                continue
            if visited[py, px]:
                continue
            path = [(x, y), (px, py)]
            visited[py, px] = True
            prev, cur = (y, x), (py, px)
            end = None
            while end is None:
                cands = [q for q in nbrs(*cur) if q != prev]
                nodes = [q for q in cands if node_px[q] and (int(lab[q]) != c0 or len(path) > 3)]
                if nodes:
                    q = min(nodes, key=lambda q: abs(q[0] - cur[0]) + abs(q[1] - cur[1]))
                    end = int(lab[q])
                    path.append((q[1], q[0]))
                    break
                free = [q for q in cands if not visited[q] and not node_px[q]]
                if not free:
                    break
                q = min(free, key=lambda q: abs(q[0] - cur[0]) + abs(q[1] - cur[1]))
                visited[q] = True
                path.append((q[1], q[0]))
                prev, cur = cur, q
            if end is None:
                continue
            pts = [g.pos[c0]] + [(float(a), float(b)) for a, b in path[1:-1]] + [g.pos[end]]
            g.add_edge(_Edge(c0, end, pts))
    return g


def _merge_through(g: _Graph, n: int, inc: dict[int, list[int]]) -> None:
    """Nút bậc 2: nối hai cạnh thành một (đỉnh rẽ thành đỉnh polyline)."""
    k1, k2 = inc[n]
    e1, e2 = g.edges[k1], g.edges[k2]
    p1 = e1.pts if e1.b == n else e1.pts[::-1]
    p2 = e2.pts if e2.a == n else e2.pts[::-1]
    a = e1.a if e1.b == n else e1.b
    b = e2.b if e2.a == n else e2.a
    del g.edges[k1], g.edges[k2]
    del g.pos[n]
    g.add_edge(_Edge(a, b, p1 + p2[1:]))


def simplify_graph(g: _Graph, spur_px: float, merge_px: float, min_isolated_px: float) -> None:
    for _ in range(3):
        changed = False
        deg = g.degree()
        inc = g.incident()
        # tỉa gai: cạnh ngắn từ đầu mút vào nút bậc ≥ 3
        for k, e in list(g.edges.items()):
            if k not in g.edges:
                continue
            da, db = deg.get(e.a, 0), deg.get(e.b, 0)
            if e.length() < spur_px and ((da == 1 and db >= 3) or (db == 1 and da >= 3)):
                tip = e.a if da == 1 else e.b
                del g.edges[k]
                g.pos.pop(tip, None)
                changed = True
        # gộp hai nút bậc ≥ 3 nối bằng cạnh rất ngắn (chữ thập skeleton tách thành 2 chữ T)
        deg = g.degree()
        for k, e in list(g.edges.items()):
            if k not in g.edges or e.a == e.b:
                continue
            if deg.get(e.a, 0) >= 3 and deg.get(e.b, 0) >= 3 and e.length() < merge_px:
                keep, drop = e.a, e.b
                ka, kb = deg[keep], deg[drop]
                wa, wb = ka / (ka + kb), kb / (ka + kb)
                (xa, ya), (xb, yb) = g.pos[keep], g.pos[drop]
                g.pos[keep] = (wa * xa + wb * xb, wa * ya + wb * yb)
                del g.edges[k]
                for e2 in g.edges.values():
                    if e2.a == drop:
                        e2.a = keep
                    if e2.b == drop:
                        e2.b = keep
                del g.pos[drop]
                deg = g.degree()
                changed = True
        # nút bậc 2: nối qua
        inc = g.incident()
        for n in list(g.pos):
            if n in inc and len(inc[n]) == 2 and n in g.pos:
                k1, k2 = inc[n]
                if k1 in g.edges and k2 in g.edges and g.edges[k1] is not g.edges[k2] \
                        and g.edges[k1].a != g.edges[k1].b and g.edges[k2].a != g.edges[k2].b:   # bỏ qua vòng tự thân
                    _merge_through(g, n, inc)
                    inc = g.incident()
                    changed = True
        # bỏ nút cô lập và mảnh vụn hai đầu tự do quá ngắn
        deg = g.degree()
        for k, e in list(g.edges.items()):
            if deg.get(e.a) == 1 and deg.get(e.b) == 1 and e.length() < min_isolated_px:
                del g.edges[k]
                changed = True
        deg = g.degree()
        for n in [n for n, d in deg.items() if d == 0]:
            del g.pos[n]
        if not changed:
            break
    # đặt đầu cạnh đúng vị trí nút (sau khi gộp nút đã dời)
    for e in g.edges.values():
        e.pts[0], e.pts[-1] = g.pos[e.a], g.pos[e.b]


def dp_simplify(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    if len(pts) <= 2:
        return list(pts)
    arr = np.asarray(pts, np.float32).reshape(-1, 1, 2)
    out = cv2.approxPolyDP(arr, eps, False).reshape(-1, 2)
    res = [tuple(map(float, p)) for p in out]
    res[0], res[-1] = pts[0], pts[-1]
    return res


def orthogonalize(pts: list[tuple[float, float]], tol_deg: float = 4.0) -> list[tuple[float, float]]:
    """Đoạn gần ngang/dọc thì nắn thẳng (giữ nguyên hai đầu mút của polyline)."""
    p = [list(q) for q in pts]
    t = math.tan(math.radians(tol_deg))
    for i in range(len(p) - 1):
        dx, dy = p[i + 1][0] - p[i][0], p[i + 1][1] - p[i][1]
        if abs(dy) <= t * abs(dx):
            if i + 1 == len(p) - 1:
                p[i][1] = p[i + 1][1] if i > 0 else p[i][1]
            else:
                p[i + 1][1] = p[i][1]
        elif abs(dx) <= t * abs(dy):
            if i + 1 == len(p) - 1:
                p[i][0] = p[i + 1][0] if i > 0 else p[i][0]
            else:
                p[i + 1][0] = p[i][0]
    out = [tuple(q) for q in p]
    # bỏ đỉnh trùng hoặc thẳng hàng
    clean = [out[0]]
    for q in out[1:]:
        if abs(q[0] - clean[-1][0]) > 1e-6 or abs(q[1] - clean[-1][1]) > 1e-6:
            clean.append(q)
    return clean if len(clean) >= 2 else [out[0], out[-1]]


# ---------------------------------------------------------------------------- che vùng

def mask_symbols(ink: np.ndarray, symbols: list[DetectedSymbol], grow: float) -> None:
    H, W = ink.shape
    for s in symbols:
        x, y, w, h = s.bbox
        x0, y0 = max(0, int(math.floor(x - grow))), max(0, int(math.floor(y - grow)))
        x1, y1 = min(W, int(math.ceil(x + w + grow)) + 1), min(H, int(math.ceil(y + h + grow)) + 1)
        ink[y0:y1, x0:x1] = False


def mask_texts(ink: np.ndarray, texts: list[TextItem], keep_len: int) -> None:
    if not texts:
        return
    m = ink.astype(np.uint8)
    keep = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (keep_len, 1)))
    keep |= cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, keep_len)))
    H, W = ink.shape
    for t in texts:
        x, y, w, h = t.bbox
        x0, y0 = max(1, int(x) - 1), max(1, int(y) - 1)
        x1, y1 = min(W - 1, int(math.ceil(x + w)) + 2), min(H - 1, int(math.ceil(y + h)) + 2)
        if x1 <= x0 or y1 <= y0:
            continue
        region = ink[y0:y1, x0:x1]
        entering = _entering_strokes(region, ink[y0 - 1, x0:x1], ink[y1, x0:x1], ink[y0:y1, x0 - 1], ink[y0:y1, x1])
        ink[y0:y1, x0:x1] = region & ((keep[y0:y1, x0:x1] > 0) | entering)


def _entering_strokes(region: np.ndarray, above, below, left, right) -> np.ndarray:
    """Nét đi vào hộp chữ từ ngoài (dây chạy qua sát nhãn): đoạn mực liền từ mép hộp, ở cột/hàng mà ngay
    ngoài mép có mực. Hộp OCR có khi ôm cả ký hiệu hoặc đoạn dây ngắn cạnh chữ."""
    keep = np.zeros_like(region)
    h, w = region.shape

    def run(arr: np.ndarray) -> int:
        """Độ dài đoạn True liền từ đầu mảng."""
        stop = np.flatnonzero(~arr)
        return int(stop[0]) if stop.size else arr.size

    for c in np.flatnonzero(above):
        keep[:run(region[:, c]), c] = True
    for c in np.flatnonzero(below):
        n = run(region[::-1, c])
        keep[h - n:, c] = True
    for r in np.flatnonzero(left):
        keep[r, :run(region[r, :])] = True
    for r in np.flatnonzero(right):
        n = run(region[r, ::-1])
        keep[r, w - n:] = True
    return keep


def median_text_height(texts: list[TextItem]) -> float | None:
    hs = [min(t.bbox[2], t.bbox[3]) for t in texts if t.bbox[2] > 0 and t.bbox[3] > 0]
    return float(np.median(hs)) if hs else None


# ---------------------------------------------------------------------------- cài đặt

@register("wire_extractor", "classical")
class ClassicalWireExtractor:
    def __init__(self, symbol_mask_grow_w: float = 1.0, text_keep_line_heights: float = 3.0,
                 line_kernel_mm: float = 0.8, min_segment_mm: float = 1.0, simplify_w: float = 1.0,
                 node_merge_w: float = 3.0, frame_line_ratio: float = 0.6, title_block_zone: float = 0.3,
                 busbar_stroke_ratio: float = 1.8, busbar_min_length_mm: float = 20.0,
                 hough_min_mm: float = 4.0):
        self.grow_w = symbol_mask_grow_w
        self.text_keep = text_keep_line_heights
        self.line_kernel_mm = line_kernel_mm
        self.min_segment_mm = min_segment_mm
        self.simplify_w = simplify_w
        self.node_merge_w = node_merge_w
        self.frame_ratio, self.title_zone = frame_line_ratio, title_block_zone
        self.busbar_ratio, self.busbar_min_mm = busbar_stroke_ratio, busbar_min_length_mm
        self.hough_min_mm = hough_min_mm
        self.area: DrawingArea | None = None       # vùng vẽ của lần chạy gần nhất (pipeline đọc lại)

    def drawing_area(self, img: NormalizedImage) -> DrawingArea:
        return find_drawing_area(ink_mask(img.pixels), self.frame_ratio, self.title_zone,
                                 margin_px=3 * img.median_stroke_px)

    def extract(self, img: NormalizedImage, symbols: list[DetectedSymbol], texts: list[TextItem]) -> WireExtraction:
        gray, w, ppm = img.pixels, img.median_stroke_px, img.px_per_mm
        ink = ink_mask(gray)
        area = self.area = self.drawing_area(img)
        x0, y0, x1, y1 = (int(round(v)) for v in area.content)
        roi = np.zeros_like(ink)
        roi[max(0, y0):y1, max(0, x0):x1] = True
        ink &= roi

        mask_symbols(ink, symbols, self.grow_w * w)
        th = median_text_height(texts) or 2.0 * ppm
        mask_texts(ink, texts, max(3, int(self.text_keep * th)))

        busbars, bmask = extract_busbars(gray, ink, w, self.busbar_ratio, self.busbar_min_mm * ppm, area)
        if busbars:
            ink &= cv2.dilate(bmask, np.ones((3, 3), np.uint8), iterations=max(1, round(w))) == 0

        # giữ nét thẳng ngang/dọc + đoạn xiên dài (Hough)
        m = ink.astype(np.uint8)
        k = max(3, int(round(self.line_kernel_mm * ppm)))
        lines = cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1)))
        lines |= cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k)))
        rest = m & (lines == 0)
        hough = cv2.HoughLinesP(rest * 255, 1, np.pi / 180, threshold=int(self.hough_min_mm * ppm),
                                minLineLength=self.hough_min_mm * ppm, maxLineGap=w)
        if hough is not None:
            diag = np.zeros_like(m)
            for xa, ya, xb, yb in hough[:, 0]:
                ang = abs(math.degrees(math.atan2(yb - ya, xb - xa))) % 90
                if 10 < ang < 80:
                    cv2.line(diag, (int(xa), int(ya)), (int(xb), int(yb)), 1, max(1, round(w)))
            lines |= diag & m
        wire_mask = lines

        g = skeleton_graph(wire_mask)
        simplify_graph(g, spur_px=max(2.0 * w, 0.6 * ppm), merge_px=self.node_merge_w * w,
                       min_isolated_px=self.min_segment_mm * ppm)

        segments: list[WireSegment] = []
        edge_seg: dict[int, str] = {}
        dt = cv2.distanceTransform(wire_mask, cv2.DIST_L2, 3)
        for i, (k_e, e) in enumerate(sorted(g.edges.items(), key=lambda kv: kv[1].pts[0]), start=1):
            pts = orthogonalize(dp_simplify(e.pts, self.simplify_w * w))
            pts[0], pts[-1] = g.pos[e.a], g.pos[e.b]
            if len(pts) < 2 or (len(pts) == 2 and pts[0] == pts[1]):
                continue
            sid = f"seg-{i:04d}"
            samples = [dt[min(dt.shape[0] - 1, int(round(y))), min(dt.shape[1] - 1, int(round(x)))]
                       for x, y in e.pts[1:-1]] or [w / 2]
            stroke = float(max(1.0, 2 * np.median(samples) - 1)) if samples else w
            segments.append(WireSegment(sid, pts, stroke, False, 1.0))
            edge_seg[k_e] = sid

        inc = g.incident()
        nodes: list[CandidateNode] = []
        for n, ks in sorted(inc.items(), key=lambda kv: g.pos[kv[0]]):
            if len(ks) >= 3:
                nodes.append(CandidateNode(f"cn-{len(nodes) + 1:04d}", g.pos[n],
                                           [edge_seg[k] for k in ks if k in edge_seg]))
        full_mask = (wire_mask > 0) | (bmask > 0)
        return WireExtraction(segments=busbars + segments, nodes=nodes, wire_mask=full_mask.astype(np.uint8) * 255)
