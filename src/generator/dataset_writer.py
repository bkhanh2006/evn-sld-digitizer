"""DatasetWriter (SDD 8.1.5): ghi PNG, SVG, DXF, mask, graph và cộng dồn COCO cho mỗi mẫu.

Cấu trúc thư mục một lần sinh (data/synthetic/<version>_n<N>/):
    images/NNNN.png        ảnh cuối (có nhiễu)
    clean/NNNN.png         ảnh xám trước nhiễu (tiêu chí 8 của validate_dataset)
    svg/NNNN.svg           SVG tương tác (SVGInteractiveExporter)
    dxf/NNNN.dxf           AutoCADDXFExporter
    masks/NNNN_wire.png    mask dây
    graphs/NNNN_graph.json đáp án đồ thị
    annotations_coco.json, split.json, manifest.json
Thông tin để kiểm tra lại (topologyId, khung tên, pxPerMm) nằm trong `images[]` của COCO.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from PIL import Image

from src.core.symbol_lib import SymbolLibrary
from src.exporters.dxf_exporter import AutoCADDXFExporter
from src.exporters.svg_exporter import SVGInteractiveExporter
from src.generator.layout_composer import Layout
from src.generator.renderer import RenderResult

SUBDIRS = ("images", "clean", "svg", "dxf", "masks", "graphs")


class DatasetWriter:
    def __init__(self, out_dir: Path, lib: SymbolLibrary):
        self.out = Path(out_dir)
        self.lib = lib
        for d in SUBDIRS:
            (self.out / d).mkdir(parents=True, exist_ok=True)
        self.svg = SVGInteractiveExporter(lib)
        self.dxf = AutoCADDXFExporter(lib)
        self.images: list[dict] = []
        self.annotations: list[dict] = []
        self.records: list[dict] = []

    def write(self, index: int, layout: Layout, render: RenderResult, seed: int) -> dict:
        name = f"{index:04d}"
        g = layout.graph
        g.page.id = f"{self.out.name}_{name}"
        g.page.source_file = f"images/{name}.png"
        g.provenance.seed = seed
        render.image.save(self.out / "images" / f"{name}.png")
        Image.fromarray(render.clean).save(self.out / "clean" / f"{name}.png")
        Image.fromarray(render.mask).save(self.out / "masks" / f"{name}_wire.png")
        (self.out / "graphs" / f"{name}_graph.json").write_text(g.to_json(), encoding="utf-8")
        self.svg.export(g, self.out / "svg" / f"{name}.svg")
        self.dxf.export(g, self.out / "dxf" / f"{name}.dxf")

        image_id = index
        tb = layout.title_block_px
        self.images.append({
            "id": image_id, "file_name": f"images/{name}.png", "width": g.page.width, "height": g.page.height,
            "topologyId": layout.topology.topology_id, "difficulty": layout.topology.difficulty,
            "preset": layout.topology.preset, "page": layout.page_name, "pxPerMm": g.page.px_per_mm,
            "titleBlockBbox": [tb[0], tb[1], tb[2] - tb[0], tb[3] - tb[1]], "seed": seed,
        })
        for d in g.devices:
            x, y, w, h = d.bbox
            self.annotations.append({
                "id": len(self.annotations) + 1, "image_id": image_id,
                "category_id": self.lib[d.detector_class].class_id, "bbox": [x, y, w, h], "area": w * h,
                "iscrowd": 0, "deviceId": d.id,
                "attributes": {"rotationDeg": d.transform.rotation_deg, "mirrorX": d.transform.mirror_x},
            })
        rec = {"index": index, "name": name, "topologyId": layout.topology.topology_id,
               "devices": len(g.devices), "wires": len(g.wires),
               "crossings": sum(n.type == "crossing_no_connect" for n in g.nodes)}
        self.records.append(rec)
        return rec

    def coco(self) -> dict:
        cats = [{"id": s.class_id, "name": c, "supercategory": s.type} for c, s in
                sorted(self.lib.symbols.items(), key=lambda kv: kv[1].class_id)]
        return {"info": {"description": "Sơ đồ nhất thứ tổng hợp (SDD 8.1)"}, "images": self.images,
                "annotations": self.annotations, "categories": cats}

    @staticmethod
    def split(topology_ids: list[str], val_fraction: float = 0.2) -> dict:
        """Chia theo topologyId bằng băm (ổn định, mọi biến thể của một topology cùng một phía)."""
        out = {"train": [], "val": [], "by": "topologyId"}
        for tid in sorted(set(topology_ids)):
            h = int(hashlib.sha1(tid.encode()).hexdigest(), 16) % 1000
            out["val" if h < val_fraction * 1000 else "train"].append(tid)
        return out

    def finalize(self, manifest: dict, val_fraction: float = 0.2) -> None:
        (self.out / "annotations_coco.json").write_text(json.dumps(self.coco(), ensure_ascii=False), encoding="utf-8")
        split = self.split([im["topologyId"] for im in self.images], val_fraction)
        split["images"] = {k: [im["file_name"] for im in self.images if im["topologyId"] in split[k]]
                           for k in ("train", "val")}
        (self.out / "split.json").write_text(json.dumps(split, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest = dict(manifest, samples=self.records)
        (self.out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
