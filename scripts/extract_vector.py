"""Trích nét vector của trang PDF sang tọa độ ảnh raster (px), làm nguồn nhãn nháp.

Tọa độ dùng cùng PageTransform với rasterize_pdf.py, nên vector.json khớp page.png từng pixel.
Pipeline nhận dạng không đọc vector; file này chỉ dùng để tạo nhãn nháp cho dây, thanh cái
và hình học ký hiệu.

    python scripts/extract_vector.py --id E18.9 --overlay
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

import numpy as np
import pymupdf
from PIL import Image, ImageDraw, ImageFilter

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.rasterize_pdf import PROCESSED_DIR, page_transform_for  # noqa: E402
from src.core.page_transform import PageTransform  # noqa: E402
from src.core.raw_pages import ROOT, RawPage, load_raw_pages  # noqa: E402

CURVE_SAMPLES = 8          # số đoạn khi xấp xỉ một đường Bézier bậc 3
WHITE_LEVEL = 0.9          # màu có mọi kênh > ngưỡng này coi là trắng (nét che, không phải mực)
DARK_THRESHOLD = 200       # điểm ảnh xám < ngưỡng này coi là có mực


def _bezier(p0, p1, p2, p3, n: int = CURVE_SAMPLES) -> list[tuple[float, float]]:
    t = np.linspace(0.0, 1.0, n + 1)[:, None]
    pts = (1 - t) ** 3 * p0 + 3 * (1 - t) ** 2 * t * p1 + 3 * (1 - t) * t ** 2 * p2 + t ** 3 * p3
    return [tuple(p) for p in pts]


def _is_white(color) -> bool:
    return color is not None and all(c > WHITE_LEVEL for c in color)


def _round_pts(pts) -> list[list[float]]:
    return [[round(x, 2), round(y, 2)] for x, y in pts]


def convert_path(path: dict, transform: PageTransform) -> dict:
    """Một path của PyMuPDF → các item có tọa độ ảnh. Đường cong giữ 4 điểm điều khiển."""
    items = []
    for item in path["items"]:
        op = item[0]
        if op == "l":
            kind, pts = "line", [item[1], item[2]]
        elif op == "c":
            kind, pts = "curve", list(item[1:5])
        elif op == "re":
            r = item[1]
            kind, pts = "rect", [r.tl, r.tr, r.br, r.bl]
        elif op == "qu":
            q = item[1]
            kind, pts = "quad", [q.ul, q.ur, q.lr, q.ll]
        else:
            raise ValueError(f"Loại item PDF chưa hỗ trợ: {op}")
        items.append({"kind": kind, "points": _round_pts(transform.to_px(p.x, p.y) for p in pts)})

    xs = [x for it in items for x, _ in it["points"]]
    ys = [y for it in items for _, y in it["points"]]
    stroke, fill = path.get("color"), path.get("fill")
    return {
        "type": path["type"],                                  # "s" nét | "f" tô | "fs" cả hai
        "strokePx": round((path.get("width") or 0) * transform.scale, 3),
        "stroke": list(stroke) if stroke is not None else None,
        "fill": list(fill) if fill is not None else None,
        "isWhite": _is_white(fill if path["type"] == "f" else stroke),
        "closePath": bool(path.get("closePath")),
        "bboxPx": [round(min(xs), 2), round(min(ys), 2), round(max(xs) - min(xs), 2), round(max(ys) - min(ys), 2)],
        "items": items,
    }


def extract(page: RawPage, transform: PageTransform) -> dict:
    with pymupdf.open(page.file) as doc:
        pdf_page = doc[page.page - 1]
        if pdf_page.rotation:
            raise NotImplementedError(f"{page.id}: trang PDF có /Rotate={pdf_page.rotation}, chưa hỗ trợ")
        drawings = pdf_page.get_drawings()
        image_boxes = [pdf_page.get_image_bbox(img) for img in pdf_page.get_images(full=True)]

    paths = []
    for i, d in enumerate(drawings):
        p = convert_path(d, transform)
        p["id"] = f"vp-{i:05d}"
        paths.append(p)

    images = []
    for r in image_boxes:
        if r.is_infinite or r.is_empty:
            continue
        corners = [transform.to_px(pt.x, pt.y) for pt in (r.tl, r.br)]
        x0, x1 = sorted(c[0] for c in corners)
        y0, y1 = sorted(c[1] for c in corners)
        images.append([round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)])

    w, h = transform.size_px
    return {
        "id": page.id,
        "width": w,
        "height": h,
        "pxPerMm": transform.px_per_mm,
        "rotationDeg": transform.rotation_deg,
        "embeddedImagesBboxPx": images,   # dấu, chữ ký: không có nét vector tương ứng
        "paths": [{"id": p.pop("id"), **p} for p in paths],
    }


def _item_polyline(item: dict) -> list[tuple[float, float]]:
    pts = [tuple(p) for p in item["points"]]
    if item["kind"] == "curve":
        return _bezier(*(np.array(p) for p in pts))
    if item["kind"] in ("rect", "quad"):
        return pts + [pts[0]]
    return pts


def render_mask(vector: dict, include_fills: bool) -> Image.Image:
    """Vẽ các nét (và tùy chọn phần tô) không trắng thành mask 0/255."""
    mask = Image.new("L", (vector["width"], vector["height"]), 0)
    draw = ImageDraw.Draw(mask)
    for p in vector["paths"]:
        if p["isWhite"]:
            continue
        if include_fills and "f" in p["type"]:
            poly = [pt for it in p["items"] for pt in _item_polyline(it)]
            if len(poly) >= 3:
                draw.polygon(poly, fill=255)
        if "s" in p["type"]:
            width = max(1, round(p["strokePx"]))
            for it in p["items"]:
                draw.line(_item_polyline(it), fill=255, width=width)
    return mask


def alignment_scores(raster: Image.Image, vector: dict, tolerance_px: int = 1) -> dict:
    """Đo độ khớp giữa vector và ảnh raster.

    precision: tỉ lệ điểm của nét vector rơi vào mực trên ảnh (cho phép lệch tolerance_px).
    recall: tỉ lệ điểm mực trên ảnh được vector (nét + phần tô) phủ, bỏ vùng ảnh nhúng.
    """
    size = 2 * tolerance_px + 1
    ink = np.asarray(raster.filter(ImageFilter.MinFilter(size))) < DARK_THRESHOLD
    strokes = np.asarray(render_mask(vector, include_fills=False)) > 0
    precision = float(ink[strokes].mean()) if strokes.any() else 0.0

    covered = np.asarray(render_mask(vector, include_fills=True).filter(ImageFilter.MaxFilter(size))) > 0
    raw_ink = np.asarray(raster) < DARK_THRESHOLD
    for x, y, w, h in vector["embeddedImagesBboxPx"]:
        raw_ink[int(y):int(np.ceil(y + h)), int(x):int(np.ceil(x + w))] = False
    recall = float(covered[raw_ink].mean()) if raw_ink.any() else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4), "tolerancePx": tolerance_px}


def save_overlay(raster: Image.Image, vector: dict, out_path: Path) -> None:
    """Ảnh gốc nhạt làm nền, phần tô màu xanh, nét màu đỏ."""
    base = Image.eval(raster, lambda v: 155 + v * 100 // 255).convert("RGB")
    fills = render_mask(vector, include_fills=True)
    strokes = render_mask(vector, include_fills=False)
    base.paste((70, 110, 230), mask=fills)
    base.paste((220, 30, 30), mask=strokes)
    base.save(out_path, optimize=True)


def summarize(vector: dict) -> dict:
    paths = vector["paths"]
    return {
        "paths": len(paths),
        "items": dict(Counter(it["kind"] for p in paths for it in p["items"])),
        "types": dict(Counter(p["type"] for p in paths)),
        "strokePx": dict(Counter(p["strokePx"] for p in paths if "s" in p["type"]).most_common(6)),
        "whitePaths": sum(p["isWhite"] for p in paths),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", action="append", help="Chỉ xử lý trang có id này (lặp lại được)")
    parser.add_argument("--overlay", action="store_true", help="Xuất overlay.png và in độ khớp với page.png")
    parser.add_argument("--out", type=Path, default=PROCESSED_DIR)
    args = parser.parse_args()

    pages = load_raw_pages()
    if args.id:
        pages = [p for p in pages if p.id in args.id]

    for page in pages:
        page_dir = args.out / page.id
        meta_path = page_dir / "page_meta.json"
        if not meta_path.exists():
            sys.exit(f"{page.id}: chưa có {meta_path.relative_to(ROOT)}, hãy chạy rasterize_pdf.py trước")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        transform = page_transform_for(page, meta["pxPerMm"], tuple(meta["renderSizePx"]))

        vector = extract(page, transform)
        (page_dir / "vector.json").write_text(json.dumps(vector, ensure_ascii=False), encoding="utf-8")
        print(f"{page.id}: {json.dumps(summarize(vector), ensure_ascii=False)}")

        if args.overlay:
            raster = Image.open(page_dir / "page.png").convert("L")
            if raster.size != (vector["width"], vector["height"]):
                sys.exit(f"{page.id}: page.png {raster.size} khác kích thước vector {(vector['width'], vector['height'])}")
            save_overlay(raster, vector, page_dir / "overlay.png")
            print(f"  khớp raster: {alignment_scores(raster, vector)}")


if __name__ == "__main__":
    main()
