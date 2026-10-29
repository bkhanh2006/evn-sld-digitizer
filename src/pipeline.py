"""Pipeline Phân hệ B (SDD 3): ảnh → SubstationTopologyGraph.

Chạy các khâu theo thứ tự phụ thuộc, mỗi khâu chọn cài đặt qua configs/inference_config.yaml.
Lỗi ở một khâu không làm mất kết quả các khâu trước: khâu lỗi trả kết quả rỗng và sinh issue
`stage_failed` (SDD 11).
"""
from __future__ import annotations

import copy
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

# import để các cài đặt tự đăng ký vào registry
import src.perception.junction_classifier  # noqa: F401
import src.perception.label_matcher  # noqa: F401
import src.perception.ocr_engine  # noqa: F401
import src.perception.orientation  # noqa: F401
import src.perception.scale_normalizer  # noqa: F401
import src.perception.symbol_detector  # noqa: F401
import src.perception.wire_classical  # noqa: F401
import src.reconstruction.assembler  # noqa: F401
from src.core.registry import create
from src.core.schema import Issue, SubstationTopologyGraph
from src.core.types import DetectedSymbol, NormalizedImage, TextItem, WireExtraction
from src.perception.busbar_classifier import DrawingArea
from src.perception.symbol_detector import suppress_text_overlaps
from src.reconstruction.assembler import AssemblyContext

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "configs" / "inference_config.yaml"


def load_config(path: Path = CONFIG_PATH, overrides: dict | None = None) -> dict:
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    for k, v in (overrides or {}).items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k] = {**cfg[k], **v}
        else:
            cfg[k] = v
    return cfg


@dataclass
class PipelineResult:
    graph: SubstationTopologyGraph
    image: NormalizedImage
    symbols: list[DetectedSymbol] = field(default_factory=list)
    texts: list[TextItem] = field(default_factory=list)
    extraction: WireExtraction | None = None
    area: DrawingArea | None = None
    timings: dict[str, float] = field(default_factory=dict)
    failed: dict[str, str] = field(default_factory=dict)


class Pipeline:
    def __init__(self, config_path: Path | str = CONFIG_PATH, overrides: dict | None = None):
        self.config_path = str(config_path)
        self.cfg = load_config(Path(config_path), overrides)
        names = self.cfg["pipeline"]
        th = self.cfg

        def params(section: str, *drop: str) -> dict:
            p = copy.deepcopy(th.get(section) or {})
            for k in drop:
                p.pop(k, None)
            return p

        det_params = params("symbol_detector", "text_overlap", "text_overlap_keep_score")
        self.text_overlap = th.get("symbol_detector", {}).get("text_overlap", 0.6)
        self.text_keep_score = th.get("symbol_detector", {}).get("text_overlap_keep_score", 1.01)
        self.normalizer = create("scale_normalizer", names["scale_normalizer"], **params("scale_normalizer"))
        # cài đặt "given" (ký hiệu cho sẵn khi dựng đáp án) nhận tham số riêng ở <khâu>_args
        if names["symbol_detector"] == "given":
            det_params = dict(th.get("symbol_detector_args") or {})
        self.detector = create("symbol_detector", names["symbol_detector"], **det_params)
        self.orientation = create("orientation_estimator", names["orientation_estimator"],
                                  **(params("orientation_estimator") if names["orientation_estimator"] != "given"
                                     else {}))
        reader_params = params("text_reader") if names["text_reader"] != "ground_truth" else {}
        reader_params.update(th.get("text_reader_args") or {})
        self.reader = create("text_reader", names["text_reader"], **reader_params)
        self.matcher = create("label_matcher", names["label_matcher"], **params("label_matcher"))
        bb = th.get("busbar_classifier") or {}
        self.wires = create("wire_extractor", names["wire_extractor"], **params("wire_extractor"),
                            busbar_stroke_ratio=bb.get("busbar_stroke_ratio", 1.8),
                            busbar_min_length_mm=bb.get("min_length_mm", 20.0))
        self.junctions = create("junction_classifier", names["junction_classifier"], **params("junction_classifier"))
        pm = params("port_matcher")
        pm["mask_grow_w"] = th.get("wire_extractor", {}).get("symbol_mask_grow_w", 1.0)
        self.assembler = create("graph_assembler", names["graph_assembler"], port_matcher=pm,
                                **params("assembler"))

    def run(self, image: np.ndarray, page_id: str, source_file: str | None = None,
            page_size_mm: tuple[float, float] | None = None, log=None,
            texts: list[TextItem] | None = None) -> PipelineResult:
        """texts: chữ đã đọc sẵn (bỏ qua TextReader, dùng khi chạy lại nhiều lần trên cùng ảnh)."""
        timings: dict[str, float] = {}
        failed: dict[str, str] = {}

        def stage(name: str, fn, default):
            t0 = time.perf_counter()
            try:
                return fn()
            except Exception as exc:     # khâu lỗi: giữ kết quả các khâu trước (SDD 11)
                failed[name] = f"{type(exc).__name__}: {exc}"
                if log:
                    log(f"[{name}] LỖI {failed[name]}\n{traceback.format_exc()}")
                return default
            finally:
                timings[name] = round(time.perf_counter() - t0, 3)
                if log:
                    log(f"[{name}] {timings[name]:.1f}s")

        img: NormalizedImage = stage("scale_normalizer", lambda: self.normalizer.normalize(image, page_size_mm), None)
        if img is None:
            raise RuntimeError(f"Không chuẩn hóa được ảnh: {failed.get('scale_normalizer')}")
        area = stage("drawing_area", lambda: self.wires.drawing_area(img), None)
        symbols = stage("symbol_detector", lambda: self.detector.detect(img), [])
        if texts is None:
            texts = stage("text_reader", lambda: self.reader.read(img), [])
        if area is not None:
            symbols = [s for s in symbols if area.contains(s.bbox[0] + s.bbox[2] / 2, s.bbox[1] + s.bbox[3] / 2)]
        code_like = [t for t in texts if len(t.normalized) >= 2]
        symbols = suppress_text_overlaps(symbols, code_like, self.text_overlap, self.text_keep_score)
        symbols = stage("orientation_estimator", lambda: self.orientation.estimate(img, symbols), symbols)
        drawing_texts = [t for t in texts if area is None or area.contains(*_center(t.bbox))]
        extraction = stage("wire_extractor", lambda: self.wires.extract(img, symbols, drawing_texts),
                           WireExtraction([], [], None))
        extraction = stage("junction_classifier", lambda: self.junctions.classify(img, extraction), extraction)
        busbars = [s for s in extraction.segments if s.is_busbar]
        labels = stage("label_matcher", lambda: self.matcher.match(drawing_texts, symbols, busbars), [])

        extra: list[Issue] = []
        if hasattr(self.normalizer, "is_low_resolution") and self.normalizer.is_low_resolution(img):
            extra.append(Issue(id="issue-low-resolution", type="low_resolution_input", severity="error",
                               message=f"Ảnh nguồn phải phóng {img.scale_from_original:.2f}× để đạt "
                                       f"{img.px_per_mm:.1f} px/mm (ngưỡng {self.normalizer.low_res_upscale}×); "
                                       "kết quả chỉ để thử, không đánh giá độ chính xác"))
        for name, msg in failed.items():
            extra.append(Issue(id=f"issue-stage-failed-{name}", type="stage_failed", severity="error",
                               message=f"Khâu {name} lỗi: {msg}"))
        self.assembler.context = AssemblyContext(page_id=page_id, source_file=source_file, area=area,
                                                 extra_issues=extra, config_path=_rel(self.config_path))
        t0 = time.perf_counter()
        graph = self.assembler.assemble(img, symbols, texts, labels, extraction)
        timings["graph_assembler"] = round(time.perf_counter() - t0, 3)
        return PipelineResult(graph, img, symbols, texts, extraction, area, timings, failed)


@dataclass
class PipelineInput:
    image: np.ndarray
    page_id: str
    source_file: str
    page_size_mm: tuple[float, float] | None


def load_input(path: Path | str, page_id: str | None = None) -> PipelineInput:
    """Đọc đầu vào: PDF (raster hóa theo configs/raw_pages.yaml), ảnh có page_meta.json cạnh nó
    (data/processed/<id>/page.png), ảnh tổng hợp (khổ trang lấy từ graphs/), hoặc ảnh raster bất kỳ
    (khổ trang chưa biết → ước lượng tỉ lệ từ độ dày nét)."""
    import json

    from PIL import Image

    path = Path(path)
    rel = _rel(str(path))
    if path.suffix.lower() == ".pdf":
        from scripts.rasterize_pdf import page_transform_for, render_page
        from src.core.raw_pages import load_raw_pages
        pages = [p for p in load_raw_pages() if p.file.resolve() == path.resolve()]
        if not pages:
            raise ValueError(f"{path} chưa khai báo trong configs/raw_pages.yaml")
        page = pages[0]
        img, transform = render_page(page, page_transform_for(page))
        return PipelineInput(np.asarray(img), page_id or page.id, rel, tuple(transform.page_size_mm))
    image = np.asarray(Image.open(path).convert("L"))
    meta = path.with_name("page_meta.json")
    if meta.exists():
        m = json.loads(meta.read_text(encoding="utf-8"))
        return PipelineInput(image, page_id or m["id"], m.get("sourceFile", rel), tuple(m["pageSizeMm"]))
    gt = path.parent.parent / "graphs" / f"{path.stem}_graph.json"
    if gt.exists():
        ppm = json.loads(gt.read_text(encoding="utf-8"))["page"]["pxPerMm"]
        h, w = image.shape[:2]
        return PipelineInput(image, page_id or f"{path.parent.parent.name}_{path.stem}", rel, (w / ppm, h / ppm))
    return PipelineInput(image, page_id or path.stem, rel, None)


def export_result(result: PipelineResult, out_dir: Path | str, page_id: str) -> dict[str, Path]:
    """Ghi <id>_graph.json, <id>.svg (lớp ảnh nguồn mờ phía dưới), <id>.dxf, <id>_page.png, <id>_log.json."""
    import json
    from collections import Counter

    from PIL import Image

    from src.exporters.dxf_exporter import AutoCADDXFExporter
    from src.exporters.svg_exporter import SVGInteractiveExporter

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {"graph": out / f"{page_id}_graph.json", "svg": out / f"{page_id}.svg", "dxf": out / f"{page_id}.dxf",
             "page": out / f"{page_id}_page.png", "log": out / f"{page_id}_log.json"}
    g = result.graph
    paths["graph"].write_text(g.to_json(), encoding="utf-8")
    Image.fromarray(result.image.pixels).save(paths["page"], optimize=True)
    SVGInteractiveExporter().export(g, paths["svg"], source_image=paths["page"].name)
    AutoCADDXFExporter().export(g, paths["dxf"])
    log = {
        "pageId": page_id, "timingsSec": result.timings, "failedStages": result.failed,
        "pxPerMm": result.image.px_per_mm, "pxPerMmSource": result.image.px_per_mm_source,
        "scaleFromOriginal": result.image.scale_from_original, "medianStrokePx": result.image.median_stroke_px,
        "counts": {"devices": len(g.devices), "busbars": len(g.busbars), "wires": len(g.wires),
                   "nodes": len(g.nodes), "nets": len(g.nets), "texts": len(g.texts), "bays": len(g.bays)},
        "devicesByClass": dict(Counter(d.detector_class for d in g.devices)),
        "issuesByType": dict(Counter(i.type for i in g.issues)),
    }
    paths["log"].write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    return paths


def _center(b) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2


def _rel(p: str) -> str:
    try:
        return Path(p).resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return p
