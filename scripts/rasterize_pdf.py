"""Raster hóa các trang bản vẽ khai báo trong configs/raw_pages.yaml.

Mỗi trang cho ra data/processed/<id>/page.png (ảnh xám) và page_meta.json.
pxPerMm lấy theo kích thước trang PDF thật (SDD mục 2.1, 8.2.1), không giả định khổ A3.

    python scripts/rasterize_pdf.py                 # mọi trang
    python scripts/rasterize_pdf.py --id E18.9      # một trang
    python scripts/rasterize_pdf.py --id E18.9 --px-per-mm 8
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.page_transform import PageTransform  # noqa: E402
from src.core.raw_pages import ROOT, RawPage, load_raw_pages  # noqa: E402

PROCESSED_DIR = ROOT / "data" / "processed"

# Xoay theo chiều kim đồng hồ → hằng số transpose của PIL (ROTATE_* của PIL quay ngược chiều kim đồng hồ)
_TRANSPOSE_CW = {
    90: Image.Transpose.ROTATE_270,
    180: Image.Transpose.ROTATE_180,
    270: Image.Transpose.ROTATE_90,
}


def page_transform_for(page: RawPage, px_per_mm: float | None = None,
                       render_size_px: tuple[int, int] | None = None) -> PageTransform:
    pdf = pdfium.PdfDocument(page.file)
    try:
        width_pt, height_pt = pdf[page.page - 1].get_size()
    finally:
        pdf.close()
    return PageTransform(width_pt, height_pt, px_per_mm or page.px_per_mm, page.rotation_deg, render_size_px)


def render_page(page: RawPage, transform: PageTransform) -> tuple[Image.Image, PageTransform]:
    """Trả về ảnh đã xoay và PageTransform gắn kích thước bitmap thực tế."""
    pdf = pdfium.PdfDocument(page.file)
    try:
        bitmap = pdf[page.page - 1].render(scale=transform.scale, grayscale=True)
        img = bitmap.to_pil().convert("L")
    finally:
        pdf.close()
    expected = transform.unrotated_size_px
    if abs(img.width - expected[0]) > 1 or abs(img.height - expected[1]) > 1:
        raise RuntimeError(f"{page.id}: kích thước render {img.size} lệch so với dự kiến {expected}")
    transform = PageTransform(transform.width_pt, transform.height_pt, transform.px_per_mm,
                              transform.rotation_deg, img.size)
    if transform.rotation_deg:
        img = img.transpose(_TRANSPOSE_CW[transform.rotation_deg])
    return img, transform


def rasterize(page: RawPage, px_per_mm: float | None = None, out_dir: Path = PROCESSED_DIR) -> Path:
    img, transform = render_page(page, page_transform_for(page, px_per_mm))

    page_dir = out_dir / page.id
    page_dir.mkdir(parents=True, exist_ok=True)
    img.save(page_dir / "page.png", optimize=True)

    w_mm, h_mm = transform.page_size_mm
    meta = {
        "id": page.id,
        "sourceFile": page.file.relative_to(ROOT).as_posix(),
        "sourceSha256": hashlib.sha256(page.file.read_bytes()).hexdigest(),
        "pageIndex": page.page,
        "drawingType": page.drawing_type,
        "width": img.width,
        "height": img.height,
        "unit": "px",
        "origin": "top-left",
        "pxPerMm": transform.px_per_mm,
        "pxPerMmSource": "pdf_page_size",
        "pageSizeMm": [round(w_mm, 2), round(h_mm, 2)],
        "pdfPageSizePt": [transform.width_pt, transform.height_pt],
        "renderSizePx": list(transform.unrotated_size_px),
        "rotationDeg": transform.rotation_deg,
    }
    (page_dir / "page_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return page_dir


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--id", action="append", help="Chỉ xử lý trang có id này (lặp lại được)")
    parser.add_argument("--px-per-mm", type=float, help="Ghi đè pxPerMm trong cấu hình")
    parser.add_argument("--out", type=Path, default=PROCESSED_DIR)
    args = parser.parse_args()

    pages = load_raw_pages()
    if args.id:
        unknown = set(args.id) - {p.id for p in pages}
        if unknown:
            parser.error(f"Không có id: {', '.join(sorted(unknown))}")
        pages = [p for p in pages if p.id in args.id]

    for page in pages:
        page_dir = rasterize(page, args.px_per_mm, args.out)
        meta = json.loads((page_dir / "page_meta.json").read_text(encoding="utf-8"))
        print(f"{page.id}: {meta['width']}x{meta['height']} px, {meta['pxPerMm']} px/mm -> {page_dir.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
