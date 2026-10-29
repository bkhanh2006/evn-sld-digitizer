"""Detector ký hiệu.

Giai đoạn 3 chỉ có cài đặt tạm thời không học máy `template_ncc`: so khớp mẫu đa góc bằng tương quan
chéo chuẩn hóa (NCC) với mẫu vẽ từ chính 19 SVG ký hiệu. Giai đoạn 4 thay bằng Faster R-CNN / YOLO
cùng interface `SymbolDetector` (SDD 7.2).

Hai lượt: so khớp thô trên ảnh thu nhỏ (nét còn ≈ 1,6 px) rồi tinh chỉnh từng đỉnh ở độ phân giải gốc.
"""
from __future__ import annotations

import cv2
import numpy as np

from src.core.registry import register
from src.core.symbol_lib import SymbolLibrary, default_library
from src.core.types import DetectedSymbol, NormalizedImage, TextItem
from src.perception.templates import _primitives, draw_symbol, ink_image, poses, render_template


def iou_xywh(a, b) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def overlap_ratio(a, b) -> float:
    """Diện tích giao / diện tích box nhỏ hơn."""
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    small = min(a[2] * a[3], b[2] * b[3])
    return ix * iy / small if small > 0 else 0.0


def _ncc(image: np.ndarray, tpl: np.ndarray, min_ink_ratio: float = 0.5) -> np.ndarray:
    """NCC (TM_CCOEFF_NORMED); cửa sổ có lượng mực < min_ink_ratio × mực của mẫu nhận 0.

    Nền trắng tuyệt đối (ảnh raster từ PDF) có phương sai 0, OpenCV trả giá trị vô nghĩa (tới 1,0).
    """
    resp = np.nan_to_num(cv2.matchTemplate(image, tpl, cv2.TM_CCOEFF_NORMED), nan=0.0, posinf=0.0, neginf=0.0)
    th, tw = tpl.shape
    ink = cv2.boxFilter(image, -1, (tw, th), anchor=(0, 0), normalize=False, borderType=cv2.BORDER_CONSTANT)
    ink = ink[:resp.shape[0], :resp.shape[1]]
    resp[ink < min_ink_ratio * float(tpl.sum())] = 0.0
    return np.clip(resp, -1.0, 1.0)


def residual_ncc(window: np.ndarray, tpl) -> float:
    """NCC chỉ trên điểm ngoài dải trục dây của mẫu: dây trơn (hay chữ T) đi qua không đủ để khớp."""
    if tpl.axis is None:
        return 1.0
    out = ~tpl.axis
    a = window[out].astype(np.float64)
    b = tpl.image[out].astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    den = np.sqrt((a * a).sum() * (b * b).sum())
    return float((a * b).sum() / den) if den > 1e-9 else 0.0


def _peaks(resp: np.ndarray, thr: float, win: tuple[int, int], limit: int = 4000) -> list[tuple[int, int, float]]:
    """Cực đại địa phương ≥ thr trong cửa sổ win = (h, w)."""
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (max(3, win[1]) | 1, max(3, win[0]) | 1))
    dil = cv2.dilate(resp, k)
    ys, xs = np.nonzero((resp >= dil) & (resp >= thr))
    if ys.size > limit:
        order = np.argsort(-resp[ys, xs])[:limit]
        ys, xs = ys[order], xs[order]
    return [(int(x), int(y), float(resp[y, x])) for y, x in zip(ys, xs)]


def nms(cands: list[DetectedSymbol], iou_thr: float, containment: float, judge=None) -> list[DetectedSymbol]:
    """NMS giữa mọi lớp: hai box trùng (IoU) hoặc box này nằm gần trọn trong box kia là xung đột.

    Không có `judge`: giữ box điểm cao hơn. Có `judge(c, k)`: ứng viên c thay được các box đã giữ k xung
    đột với nó nếu judge thắng tất cả (so trên cùng một cửa sổ, xem TemplateNCCDetector._union_better).
    """
    if not cands:
        return []
    order = sorted(range(len(cands)), key=lambda i: -cands[i].score)
    kept: list[int] = []
    kb = np.zeros((0, 4))
    for i in order:
        c = cands[i]
        x, y, w, h = c.bbox
        if kb.shape[0]:
            ix = np.clip(np.minimum(x + w, kb[:, 0] + kb[:, 2]) - np.maximum(x, kb[:, 0]), 0, None)
            iy = np.clip(np.minimum(y + h, kb[:, 1] + kb[:, 3]) - np.maximum(y, kb[:, 1]), 0, None)
            inter = ix * iy
            area_k = kb[:, 2] * kb[:, 3]
            iou = inter / np.maximum(w * h + area_k - inter, 1e-9)
            cont = inter / np.maximum(np.minimum(w * h, area_k), 1e-9)
            conflict = (iou > iou_thr) | (cont > containment)
            if conflict.any():
                if judge is None or not all(judge(c, cands[kept[j]]) for j in np.flatnonzero(conflict)):
                    continue
                keep_mask = ~conflict
                kept = [k for k, m in zip(kept, keep_mask) if m]
                kb = kb[keep_mask]
        kept.append(i)
        kb = np.vstack([kb, [c.bbox]])
    return [cands[i] for i in kept]


def suppress_text_overlaps(symbols: list[DetectedSymbol], texts: list[TextItem], ratio: float,
                           keep_score: float = 1.01) -> list[DetectedSymbol]:
    """Bỏ box nằm phần lớn trong vùng chữ (chữ số "1", "7", "-" dễ trùng mẫu dao cách ly), trừ box có
    NCC ≥ keep_score (hộp chữ của OCR có thể phủ cả ký hiệu sát bên)."""
    out = []
    for s in symbols:
        if s.score >= keep_score:
            out.append(s)
            continue
        area = s.bbox[2] * s.bbox[3]
        inside = 0.0
        for t in texts:
            tb = t.bbox
            ix = max(0.0, min(s.bbox[0] + s.bbox[2], tb[0] + tb[2]) - max(s.bbox[0], tb[0]))
            iy = max(0.0, min(s.bbox[1] + s.bbox[3], tb[1] + tb[3]) - max(s.bbox[1], tb[1]))
            inside += ix * iy
        if area <= 0 or inside / area < ratio:
            out.append(s)
    return out


@register("symbol_detector", "template_ncc")
class TemplateNCCDetector:
    def __init__(self, score_threshold: float = 0.55, class_thresholds: dict | None = None,
                 scales: tuple[float, ...] = (1.0,), pad_w: float = 2.5, blur_w: float = 0.5, nms_iou: float = 0.3,
                 containment: float = 0.75, text_overlap: float = 0.6, classes: list[str] | None = None,
                 coarse_stroke_px: float = 0.9, residual_threshold: float = 0.5,
                 residual_class_thresholds: dict | None = None, solid_w: float = 1.5,
                 lib: SymbolLibrary | None = None):
        self.lib = lib or default_library()
        self.thr = score_threshold
        self.class_thr = dict(class_thresholds or {})
        self.scales = tuple(scales)
        self.pad_w, self.blur_w = pad_w, blur_w
        self.nms_iou, self.containment = nms_iou, containment
        self.text_overlap = text_overlap
        self.classes = list(classes) if classes else self.lib.classes
        self.coarse_stroke_px = coarse_stroke_px
        self.residual_thr = residual_threshold
        self.residual_class_thr = dict(residual_class_thresholds or {})
        self.solid_w = solid_w
        # luật chấm đặc chỉ áp cho ký hiệu nối tiếp trên dây (≥ 2 cổng, không có hình tô đặc): đó là các lớp
        # hay bị nhận nhầm ở chấm nối chữ T. Thiết bị một cổng có vạch đất sát nhau, nhị phân hóa thành khối đặc.
        self.solid_check = {c for c in self.lib.classes if len(self.lib[c].ports) >= 2
                            and not any(p.filled for p in _primitives(str(self.lib[c].svg_file)))}

    def threshold(self, cls: str) -> float:
        return self.class_thr.get(cls, self.thr)

    def detect(self, img: NormalizedImage) -> list[DetectedSymbol]:
        w = img.median_stroke_px
        s_exp = img.px_per_mm * self.lib.library_unit_mm
        f = max(1, int(w // self.coarse_stroke_px))
        full = ink_image(img.pixels, self.blur_w * w)
        H, W = full.shape
        # độ dày cục bộ của mực: chấm nối (đĩa đặc) dày hơn hẳn nét ký hiệu
        solid = (cv2.distanceTransform((img.pixels < 128).astype(np.uint8), cv2.DIST_L2, 3) >= self.solid_w * w).astype(np.uint8)
        coarse = cv2.resize(full, (W // f, H // f), interpolation=cv2.INTER_AREA) if f > 1 else full

        cands: list[DetectedSymbol] = []
        for cls in self.classes:
            thr = self.threshold(cls)
            for rot, mir in poses(self.lib, cls):
                for k in self.scales:
                    tpl_f = render_template(self.lib, cls, rot, mir, s_exp * k, w, self.pad_w * w,
                                            blur_sigma=self.blur_w * w)
                    th, tw = tpl_f.image.shape
                    if th >= H or tw >= W:
                        continue
                    if f > 1:
                        tpl_c = cv2.resize(tpl_f.image, (max(3, tw // f), max(3, th // f)),
                                           interpolation=cv2.INTER_AREA)
                    else:
                        tpl_c = tpl_f.image
                    resp = _ncc(coarse, tpl_c)
                    coarse_thr = thr - 0.12 if f > 1 else thr
                    for x, y, sc in _peaks(resp, coarse_thr, (tpl_c.shape[0] // 2, tpl_c.shape[1] // 2)):
                        best = self._refine(full, tpl_f.image, x * f, y * f, f + 2) if f > 1 else (x, y, sc)
                        if best is None or best[2] < thr:
                            continue
                        win = full[best[1]:best[1] + th, best[0]:best[0] + tw]
                        if win.shape != tpl_f.image.shape or                                 residual_ncc(win, tpl_f) < self.residual_class_thr.get(cls, self.residual_thr):
                            continue
                        bx, by, bw, bh = tpl_f.bbox_in_template
                        box = (best[0] + bx, best[1] + by, bw, bh)
                        if cls in self.solid_check and self._has_solid(solid, cls, box, rot, mir, w):
                            continue
                        cands.append(DetectedSymbol(
                            id="", detector_class=cls, bbox=box, score=best[2],
                            rotation_deg=rot, mirror_x=mir))
        judge = lambda c, k: self._union_better(full, c, k, w)   # noqa: E731
        kept = nms(cands, self.nms_iou, self.containment, judge)
        kept.sort(key=lambda d: (d.bbox[1], d.bbox[0]))
        for i, d in enumerate(kept, start=1):
            d.id = f"det-{i:04d}"
        return kept

    def _has_solid(self, solid: np.ndarray, cls: str, box, rot: int, mir: bool, w: float) -> bool:
        """Trong box có mực đặc (chấm nối) ngoài các vùng tròn bán kính 3 w quanh cổng không."""
        x0, y0 = max(0, int(box[0])), max(0, int(box[1]))
        x1, y1 = int(box[0] + box[2]) + 1, int(box[1] + box[3]) + 1
        region = solid[y0:y1, x0:x1]
        if not region.any():
            return False
        region = region.copy()
        placement = self.lib.placement_from_bbox(cls, box, rot, mir)
        for p in self.lib.project_ports(cls, placement):
            cv2.circle(region, (round(p.xy[0] - x0), round(p.xy[1] - y0)), max(2, round(3 * w)), 0, -1)
        return bool(region.any())

    def _render_at(self, d: DetectedSymbol, rect: tuple[int, int, int, int], w: float) -> np.ndarray:
        """Vẽ ký hiệu d (và dây từ cổng ra tới mép) trong khung rect = (x0, y0, x1, y1), đã làm mờ."""
        x0, y0, x1, y1 = rect
        canvas = np.zeros((y1 - y0, x1 - x0), np.float32)
        placement = self.lib.placement_from_bbox(d.detector_class, d.bbox, d.rotation_deg or 0, d.mirror_x)
        draw_symbol(canvas, self.lib, d.detector_class, placement, w, ox=-x0, oy=-y0)
        L = 2 * max(x1 - x0, y1 - y0)
        for p in self.lib.project_ports(d.detector_class, placement):
            (px, py), (dx, dy) = p.xy, p.direction
            cv2.line(canvas, (round((px - x0) * 16), round((py - y0) * 16)),
                     (round((px + dx * L - x0) * 16), round((py + dy * L - y0) * 16)), 1.0, max(1, round(w)),
                     cv2.LINE_AA, 4)
        if self.blur_w > 0:
            canvas = cv2.GaussianBlur(canvas, (0, 0), self.blur_w * w)
        return canvas

    def _union_better(self, full: np.ndarray, c: DetectedSymbol, k: DetectedSymbol, w: float,
                      tie: float = 0.02) -> bool:
        """c giải thích mực tốt hơn k trên vùng chung: so NCC của hai mẫu trong cửa sổ của box nhỏ hơn.

        Box lớn chứa box nhỏ: hai mẫu chỉ được so trên phần ảnh cả hai cùng phủ (ký hiệu lớn có thể
        "nuốt" hai đầu cáp ▽…△ kề nhau nếu so trên cửa sổ hợp). Hòa trong `tie` thì box lớn thắng
        (dao tiếp địa thắng ký hiệu đất ở đuôi nó) — chỉ áp khi box lớn gấp ≥ 1,5 lần box nhỏ.
        """
        small = c if c.bbox[2] * c.bbox[3] <= k.bbox[2] * k.bbox[3] else k
        pad = self.pad_w * w
        H, W = full.shape
        x0, y0 = max(0, int(small.bbox[0] - pad)), max(0, int(small.bbox[1] - pad))
        x1 = min(W, int(small.bbox[0] + small.bbox[2] + pad) + 1)
        y1 = min(H, int(small.bbox[1] + small.bbox[3] + pad) + 1)
        win = full[y0:y1, x0:x1]

        def score(d):
            t = self._render_at(d, (x0, y0, x1, y1), w)
            a, b = win - win.mean(), t - t.mean()
            den = float(np.sqrt((a * a).sum() * (b * b).sum()))
            return float((a * b).sum()) / den if den > 1e-9 else 0.0

        sc, sk = score(c), score(k)
        ratio = max(c.bbox[2] * c.bbox[3], k.bbox[2] * k.bbox[3]) / max(1e-9, small.bbox[2] * small.bbox[3])
        if ratio < 1.5:          # hai box cỡ gần nhau: so thẳng
            return sc > sk
        return sc > sk + tie if small is c else sc >= sk - tie

    @staticmethod
    def _refine(full: np.ndarray, tpl: np.ndarray, x: int, y: int, r: int) -> tuple[int, int, float] | None:
        th, tw = tpl.shape
        H, W = full.shape
        x0, y0 = max(0, x - r), max(0, y - r)
        x1, y1 = min(W, x + tw + r), min(H, y + th + r)
        if x1 - x0 < tw or y1 - y0 < th:
            return None
        resp = _ncc(full[y0:y1, x0:x1], tpl)
        _, mx, _, loc = cv2.minMaxLoc(resp)
        return x0 + loc[0], y0 + loc[1], float(mx)


@register("symbol_detector", "given")
class GivenSymbolDetector:
    """Ký hiệu cho sẵn (danh sách đã duyệt khi dựng đáp án thật): {class, bbox, rotation, mirror, id?}."""

    def __init__(self, symbols: list[dict]):
        self.items = symbols

    def detect(self, img: NormalizedImage) -> list[DetectedSymbol]:
        k = img.scale_from_original
        return [DetectedSymbol(id=s.get("id") or f"det-{i:04d}", detector_class=s["class"],
                               bbox=tuple(v * k for v in s["bbox"]), score=float(s.get("score", 1.0)),
                               rotation_deg=int(s.get("rotation", 0)), mirror_x=bool(s.get("mirror", False)))
                for i, s in enumerate(self.items, start=1)]
