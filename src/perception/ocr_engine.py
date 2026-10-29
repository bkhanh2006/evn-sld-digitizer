"""TextReader (SDD 8.2.4).

- `paddleocr_ppocrv5`: PaddleOCR 3.x, phát hiện chữ theo tile (gộp box trùng ở vùng chồng lấn), nhận dạng
  từng vùng. Vùng có h/w > vertical_ratio là chữ dọc: thử xoay 90° và 270°, giữ kết quả điểm cao hơn.
  Chuỗi đọc được phân tích bằng EVNCodeGrammar.
- `ground_truth`: đọc chữ từ đồ thị đáp án. Chỉ dùng để đo riêng phần lắp ráp đồ thị (SDD 10.7),
  không phải kết quả nhận dạng.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

import cv2
import numpy as np

from src.core.evn_codes import EVNCodeGrammar, default_grammar
from src.core.registry import register
from src.core.types import NormalizedImage, TextItem
from src.perception.symbol_detector import iou_xywh, overlap_ratio


def make_text_item(idx: int, raw: str, bbox, score: float, angle: int, grammar: EVNCodeGrammar,
                   polygon=None) -> TextItem:
    x, y, w, h = bbox
    parsed = grammar.parse(raw)
    normalized = parsed.normalized if parsed is not None else raw.strip()
    poly = polygon or [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]
    return TextItem(id=f"txt-{idx:04d}", polygon=[tuple(map(float, p)) for p in poly],
                    bbox=(float(x), float(y), float(w), float(h)), raw=raw, normalized=normalized,
                    score=float(score), angle_deg=angle, parsed=parsed)


def tiles(H: int, W: int, size: int, overlap: int):
    step = max(1, size - overlap)
    ys = list(range(0, max(1, H - overlap), step)) or [0]
    xs = list(range(0, max(1, W - overlap), step)) or [0]
    for y in ys:
        for x in xs:
            yield x, y, min(W, x + size), min(H, y + size)


@register("text_reader", "paddleocr_ppocrv5")
class PaddleTextReader:
    def __init__(self, det_model: str = "PP-OCRv5_mobile_det", rec_model: str = "latin_PP-OCRv5_mobile_rec",
                 device: str = "cpu", tile_px: int = 1600, overlap_px: int = 200, vertical_ratio: float = 2.5,
                 min_det_score: float = 0.3, min_rec_score: float = 0.5, enable_mkldnn: bool = False,
                 grammar: EVNCodeGrammar | None = None):
        self.det_model, self.rec_model, self.device = det_model, rec_model, device
        self.tile_px, self.overlap_px = tile_px, overlap_px
        self.vertical_ratio = vertical_ratio
        self.min_det, self.min_rec = min_det_score, min_rec_score
        self.enable_mkldnn = enable_mkldnn
        self.grammar = grammar or default_grammar()
        self._det = self._rec = None

    def _models(self):
        if self._det is None:
            # paddlepaddle 3.3 trên Windows lỗi oneDNN (ConvertPirAttribute2RuntimeAttribute) → tắt MKLDNN
            os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
            from paddleocr import TextDetection, TextRecognition
            self._det = TextDetection(model_name=self.det_model, device=self.device, enable_mkldnn=self.enable_mkldnn)
            self._rec = TextRecognition(model_name=self.rec_model, device=self.device,
                                        enable_mkldnn=self.enable_mkldnn)
        return self._det, self._rec

    def detect_boxes(self, rgb: np.ndarray) -> list[tuple[tuple[float, float, float, float], float, list]]:
        det, _ = self._models()
        H, W = rgb.shape[:2]
        found = []
        for x0, y0, x1, y1 in tiles(H, W, self.tile_px, self.overlap_px):
            res = det.predict(np.ascontiguousarray(rgb[y0:y1, x0:x1]), batch_size=1)[0]
            for poly, sc in zip(res["dt_polys"], res["dt_scores"]):
                if sc < self.min_det:
                    continue
                p = np.asarray(poly, np.float32) + (x0, y0)
                bx0, by0 = p.min(axis=0)
                bx1, by1 = p.max(axis=0)
                # box chạm mép tile (không phải mép ảnh) sẽ có bản đầy đủ ở tile kề
                edge = ((bx0 - x0 < 2 and x0 > 0) or (x1 - bx1 < 2 and x1 < W)
                        or (by0 - y0 < 2 and y0 > 0) or (y1 - by1 < 2 and y1 < H))
                found.append(((float(bx0), float(by0), float(bx1 - bx0), float(by1 - by0)), float(sc) - edge,
                              p.tolist()))
        found.sort(key=lambda f: -(f[1] + 1e-4 * f[0][2] * f[0][3]))
        kept: list = []
        for f in found:
            if all(iou_xywh(f[0], k[0]) < 0.4 and overlap_ratio(f[0], k[0]) < 0.8 for k in kept):
                kept.append(f)
        return kept

    def recognize(self, crops: list[np.ndarray]) -> list[tuple[str, float]]:
        _, rec = self._models()
        if not crops:
            return []
        out = rec.predict(crops, batch_size=16)
        return [(r["rec_text"], float(r["rec_score"])) for r in out]

    def read(self, img: NormalizedImage) -> list[TextItem]:
        gray = img.pixels
        rgb = cv2.cvtColor(gray, cv2.COLOR_GRAY2RGB)
        H, W = gray.shape
        boxes = self.detect_boxes(rgb)
        crops, meta = [], []
        pad = max(2, int(round(img.median_stroke_px)))
        for i, (bbox, _, poly) in enumerate(boxes):
            x, y, w, h = bbox
            x0, y0 = max(0, int(x) - pad), max(0, int(y) - pad)
            x1, y1 = min(W, int(math.ceil(x + w)) + pad), min(H, int(math.ceil(y + h)) + pad)
            crop = np.ascontiguousarray(rgb[y0:y1, x0:x1])
            if crop.size == 0:
                continue
            if h > self.vertical_ratio * w:
                crops += [cv2.rotate(crop, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(crop, cv2.ROTATE_90_COUNTERCLOCKWISE)]
                meta.append((i, (270, 90)))
            else:
                crops.append(crop)
                meta.append((i, (0,)))
        results = self.recognize(crops)
        items: list[TextItem] = []
        k = 0
        for i, angles in meta:
            cands = [(results[k + j][1], results[k + j][0], a) for j, a in enumerate(angles)]
            k += len(angles)
            score, text, angle = max(cands)
            text = text.strip()
            if not text or score < self.min_rec:
                continue
            bbox, _, poly = boxes[i]
            items.append(make_text_item(len(items) + 1, text, bbox, score, angle, self.grammar,
                                        [tuple(p) for p in poly]))
        return items


@register("text_reader", "ground_truth")
class GroundTruthTextReader:
    """Chữ lấy từ đáp án (dữ liệu tổng hợp hoặc real_gold): OCR "hoàn hảo" để đo riêng các khâu sau."""

    def __init__(self, graph_path: str | Path, grammar: EVNCodeGrammar | None = None, include_title_block: bool = True):
        self.graph = json.loads(Path(graph_path).read_text(encoding="utf-8"))
        self.grammar = grammar or default_grammar()
        self.include_title_block = include_title_block

    def read(self, img: NormalizedImage) -> list[TextItem]:
        k = img.scale_from_original
        out = []
        for t in self.graph.get("texts", []):
            if not self.include_title_block and t.get("role") == "title_block":
                continue
            x, y, w, h = (v * k for v in t["bbox"])
            out.append(make_text_item(len(out) + 1, t["raw"], (x, y, w, h), t.get("confidence", 1.0),
                                      int(t.get("angleDeg", 0)), self.grammar))
        return out
