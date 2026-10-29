"""Chuẩn hóa tỉ lệ ảnh đầu vào (SDD 8.2.1).

- Biết khổ trang (PDF, dữ liệu sinh): pxPerMm = cạnh ảnh / cạnh trang.
- Ảnh raster không rõ khổ: ước lượng từ độ dày nét của các đường thẳng dài (distance transform),
  chặn trên bởi giả định bản vẽ không nhỏ hơn A4.
- pxPerMm ngoài [min, max] thì resample về target. Phóng > low_res_upscale lần là ảnh nguồn kém:
  vẫn chạy, nhưng pipeline sinh issue `low_resolution_input`.
"""
from __future__ import annotations

import cv2
import numpy as np

from src.core.registry import register
from src.core.types import NormalizedImage


def to_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim == 3:
        if image.shape[2] == 4:
            image = image[:, :, :3]
        image = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY)
    if image.dtype != np.uint8:
        image = np.clip(image, 0, 255).astype(np.uint8)
    return image


def ink_mask(gray: np.ndarray) -> np.ndarray:
    """Điểm mực (True) theo ngưỡng Otsu, chặn trong [100, 200] để ảnh gần như trắng không bị đảo."""
    thr, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    thr = float(np.clip(thr, 100, 200))
    return gray < thr


def line_stroke_width(gray: np.ndarray, ink: np.ndarray | None = None, kernel_px: int | None = None,
                      quantile: float = 0.3) -> float:
    """Độ dày nét dây (px, lẻ được) của các đường thẳng ngang/dọc dài, không lẫn nét chữ.

    Trên mỗi lát cắt vuông góc qua nét, độ dày = tổng độ đậm (255 − xám)/255 của lát cắt, nên không
    phụ thuộc ngưỡng nhị phân và khử răng cưa. Mỗi đoạn thẳng (thành phần liên thông) góp một giá trị,
    trọng số là độ dài chặn ở 10% cạnh ảnh (khung bản vẽ rất dài không lấn át). Lấy phân vị `quantile`
    theo trọng số: dây là nét mảnh nhất trong các nét thẳng dài, nét ký hiệu và thanh cái dày hơn.
    """
    if ink is None:
        ink = gray < 160
    h, w = ink.shape
    k = kernel_px or max(9, int(0.012 * max(h, w)))
    m = ink.astype(np.uint8)
    dark = (255.0 - gray.astype(np.float32)) / 255.0
    bg = float(np.median(dark[~ink])) if (~ink).any() else 0.0
    dark = np.clip(dark - bg, 0, None)
    per_line, weights = [], []
    cap = 0.1 * max(h, w)
    for lines, d in ((cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (k, 1))), dark),
                     (cv2.morphologyEx(m, cv2.MORPH_OPEN, cv2.getStructuringElement(cv2.MORPH_RECT, (1, k))).T,
                      dark.T)):
        widths, pos = _cross_widths(lines, d)
        if widths.size == 0:
            continue
        _, labels = cv2.connectedComponents(np.ascontiguousarray(lines))
        lab = labels[pos[:, 0], pos[:, 1]]
        order = np.argsort(lab, kind="stable")
        lab, widths = lab[order], widths[order]
        cuts = np.flatnonzero(np.diff(lab)) + 1
        for g in np.split(widths, cuts):
            per_line.append(float(np.median(g)))
            weights.append(min(float(g.size), cap))
    if not per_line:
        return 1.0
    order = np.argsort(per_line)
    cw = np.cumsum(np.asarray(weights)[order])
    return float(np.asarray(per_line)[order][np.searchsorted(cw, quantile * cw[-1])])


def _cross_widths(lines: np.ndarray, dark: np.ndarray, max_run: int = 40) -> tuple[np.ndarray, np.ndarray]:
    """Độ dày theo trục 0 tại từng cột cắt qua nét: tổng độ đậm trên đoạn mực (nới 1 px mỗi bên).
    Trả về (độ dày, vị trí (hàng, cột) điểm đầu của lát cắt)."""
    if not lines.any():
        return np.zeros(0, np.float32), np.zeros((0, 2), int)
    a = np.pad(lines.astype(np.int8), ((1, 1), (0, 0)))
    d = np.diff(a, axis=0)
    starts = np.argwhere(d == 1)
    ends = np.argwhere(d == -1)
    # mỗi cột có số đầu = số cuối và xen kẽ nhau: sắp theo (cột, hàng) là ghép được
    s = starts[np.lexsort((starts[:, 0], starts[:, 1]))]
    e = ends[np.lexsort((ends[:, 0], ends[:, 1]))]
    run = e[:, 0] - s[:, 0]
    ok = (run > 0) & (run <= max_run)
    s, e = s[ok], e[ok]
    cs = np.vstack([np.zeros((1, dark.shape[1]), np.float32), np.cumsum(dark, axis=0)])
    r0 = np.clip(s[:, 0] - 1, 0, dark.shape[0])
    r1 = np.clip(e[:, 0] + 1, 0, dark.shape[0])
    pos = np.stack([np.clip(s[:, 0], 0, dark.shape[0] - 1), s[:, 1]], axis=1)
    return cs[r1, s[:, 1]] - cs[r0, s[:, 1]], pos


@register("scale_normalizer", "stroke_based")
class StrokeBasedNormalizer:
    def __init__(self, nominal_stroke_mm: float = 0.25, min_px_per_mm: float = 8.0, max_px_per_mm: float = 16.0,
                 target_px_per_mm: float = 10.0, low_res_upscale: float = 1.5,
                 min_page_long_side_mm: float = 297.0):
        self.nominal_stroke_mm = nominal_stroke_mm
        self.min_ppm, self.max_ppm, self.target_ppm = min_px_per_mm, max_px_per_mm, target_px_per_mm
        self.low_res_upscale = low_res_upscale
        self.min_long_mm = min_page_long_side_mm

    def estimate_px_per_mm(self, gray: np.ndarray) -> tuple[float, float]:
        """(pxPerMm ước lượng, độ dày nét đo được px)."""
        w = line_stroke_width(gray)
        by_stroke = w / self.nominal_stroke_mm
        upper = max(gray.shape) / self.min_long_mm
        return min(by_stroke, upper), w

    def normalize(self, image: np.ndarray, page_size_mm: tuple[float, float] | None) -> NormalizedImage:
        gray = to_gray(image)
        h, w = gray.shape
        if page_size_mm:
            long_px, short_px = max(w, h), min(w, h)
            long_mm, short_mm = max(page_size_mm), min(page_size_mm)
            ppm = 0.5 * (long_px / long_mm + short_px / short_mm)
            source = "pdf_page_size"
        else:
            ppm, _ = self.estimate_px_per_mm(gray)
            source = "estimated"

        factor = 1.0
        if ppm < self.min_ppm or ppm > self.max_ppm:
            factor = self.target_ppm / ppm
            interp = cv2.INTER_CUBIC if factor > 1 else cv2.INTER_AREA
            gray = cv2.resize(gray, (round(w * factor), round(h * factor)), interpolation=interp)
            ppm *= factor
        stroke = line_stroke_width(gray)
        return NormalizedImage(pixels=gray, px_per_mm=float(ppm), px_per_mm_source=source,
                               scale_from_original=float(factor), median_stroke_px=float(max(1.0, stroke)))

    def is_low_resolution(self, img: NormalizedImage) -> bool:
        return img.scale_from_original > self.low_res_upscale
