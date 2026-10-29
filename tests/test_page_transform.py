import numpy as np
import pytest
from PIL import Image

from scripts.rasterize_pdf import _TRANSPOSE_CW
from src.core.page_transform import PT_PER_MM, PageTransform

# Trang A4 dọc, 8 px/mm → bitmap 1680 × 2376
A4_W_PT, A4_H_PT = 210 * PT_PER_MM, 297 * PT_PER_MM


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_output_size_and_page_size(rotation):
    t = PageTransform(A4_W_PT, A4_H_PT, 8.0, rotation)
    w, h = t.size_px
    if rotation in (90, 270):
        assert (w, h) == (2376, 1680)
        assert t.page_size_mm == pytest.approx((297, 210))
    else:
        assert (w, h) == (1680, 2376)
        assert t.page_size_mm == pytest.approx((210, 297))


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_to_px_matches_pil_transpose(rotation):
    """Một điểm ảnh đánh dấu trên bitmap gốc phải nằm đúng vị trí to_px dự đoán sau khi xoay."""
    render_size = (40, 30)
    t = PageTransform(40 / 2.0, 30 / 2.0, 2.0 * PT_PER_MM, rotation, render_size)  # scale = 2 px/pt
    bitmap = np.full((render_size[1], render_size[0]), 255, np.uint8)
    col, row = 7, 21
    bitmap[row, col] = 0
    img = Image.fromarray(bitmap)
    if rotation:
        img = img.transpose(_TRANSPOSE_CW[rotation])
    assert img.size == t.size_px

    # Tâm điểm ảnh (col + 0.5, row + 0.5) tính theo point
    x_px, y_px = t.to_px((col + 0.5) / t.scale, (row + 0.5) / t.scale)
    ys, xs = np.nonzero(np.asarray(img) == 0)
    assert (xs[0] + 0.5, ys[0] + 0.5) == pytest.approx((x_px, y_px))


def test_render_size_overrides_rounding():
    t = PageTransform(A4_W_PT, A4_H_PT, 12.0, 270, render_size_px=(2521, 3565))
    assert t.size_px == (3565, 2521)
    assert t.to_px(0, 0) == (0, 2521)


def test_invalid_rotation():
    with pytest.raises(ValueError):
        PageTransform(A4_W_PT, A4_H_PT, 8.0, 45)
