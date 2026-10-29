"""Kiểm tra tiêu chí xong của Giai đoạn 0 trên bản vẽ thật.

Bản vẽ gốc không nằm trong git, nên test tự bỏ qua nếu máy không có file.
"""
import json

import pytest
from PIL import Image

from scripts.extract_vector import alignment_scores, extract
from scripts.rasterize_pdf import page_transform_for, rasterize
from src.core.raw_pages import find_raw_page

PAGE = find_raw_page("E18.9")
pytestmark = pytest.mark.skipif(not PAGE.file.exists(), reason=f"Thiếu bản vẽ gốc {PAGE.file}")


@pytest.fixture(scope="module")
def rendered(tmp_path_factory):
    out = tmp_path_factory.mktemp("processed")
    page_dir = rasterize(PAGE, px_per_mm=8.0, out_dir=out)
    meta = json.loads((page_dir / "page_meta.json").read_text(encoding="utf-8"))
    raster = Image.open(page_dir / "page.png").convert("L")
    transform = page_transform_for(PAGE, meta["pxPerMm"], tuple(meta["renderSizePx"]))
    return meta, raster, extract(PAGE, transform)


def test_meta_landscape_a4(rendered):
    meta, raster, _ = rendered
    assert meta["pxPerMm"] == 8.0
    assert meta["pxPerMmSource"] == "pdf_page_size"
    assert meta["pageSizeMm"] == pytest.approx([297, 210], abs=0.5)
    assert raster.width > raster.height                     # bản vẽ đã xoay về khổ ngang
    assert (meta["width"], meta["height"]) == raster.size


def test_vector_matches_raster(rendered):
    _, raster, vector = rendered
    assert (vector["width"], vector["height"]) == raster.size
    scores = alignment_scores(raster, vector)
    assert scores["precision"] >= 0.98
    assert scores["recall"] >= 0.97
