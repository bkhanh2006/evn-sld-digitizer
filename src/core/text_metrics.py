"""Đo chữ bằng font thật để bbox của nhãn khớp với chữ được vẽ ra.

Bộ sinh dùng `text_bbox` để tính bbox đáp án; bộ xuất SVG dùng `fit_text` để từ bbox suy
ngược cỡ chữ và điểm neo (trái, đường chân chữ). Hai hàm là nghịch đảo của nhau với cùng font.
Góc chữ theo quy ước ảnh (SDD 2.2): dương là quay theo chiều kim đồng hồ, 270 = đọc từ dưới lên.
"""
from __future__ import annotations

import os
import unicodedata
from functools import lru_cache
from pathlib import Path

from PIL import ImageFont

from src.core.geometry import rotated_bbox_local

REF_SIZE = 1000          # đo ở cỡ lớn rồi chia, tránh sai số làm tròn pixel của PIL
FONT_DIRS = [Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts", Path("/usr/share/fonts"),
             Path("/usr/local/share/fonts"), Path.home() / ".fonts"]


@lru_cache(maxsize=None)
def find_font(name: str) -> Path:
    """Tên file font (arial.ttf) hoặc đường dẫn → đường dẫn tuyệt đối."""
    p = Path(name)
    if p.is_file():
        return p.resolve()
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        direct = d / name
        if direct.is_file():
            return direct
        for hit in d.rglob("*"):
            if hit.name.lower() == name.lower():
                return hit
    raise FileNotFoundError(f"Không tìm thấy font {name} trong {[str(d) for d in FONT_DIRS]}")


@lru_cache(maxsize=None)
def _font(path: str) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, REF_SIZE, layout_engine=ImageFont.Layout.BASIC)


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text)


@lru_cache(maxsize=4096)
def ink_box(text: str, font_path: str) -> tuple[float, float, float, float]:
    """Hộp mực (l, t, r, b) ở cỡ chữ 1, gốc tại đầu đường chân chữ, Y xuống."""
    if not text.strip():
        raise ValueError("Chuỗi rỗng không có hộp mực")
    l, t, r, b = _font(font_path).getbbox(nfc(text), anchor="ls")
    return l / REF_SIZE, t / REF_SIZE, r / REF_SIZE, b / REF_SIZE


def text_bbox(text: str, font_path: str, size: float, anchor: tuple[float, float],
              angle_deg: int = 0) -> tuple[float, float, float, float]:
    """bbox [x, y, w, h] của chữ cỡ `size` (px) neo tại `anchor`, xoay `angle_deg`."""
    l, t, r, b = ink_box(text, font_path)
    x0, y0, x1, y1 = rotated_bbox_local((l * size, t * size, r * size, b * size), angle_deg, False)
    return anchor[0] + x0, anchor[1] + y0, x1 - x0, y1 - y0


def fit_text(text: str, font_path: str, bbox: tuple[float, float, float, float],
             angle_deg: int = 0) -> tuple[float, tuple[float, float]]:
    """Nghịch đảo của text_bbox: bbox → (cỡ chữ, điểm neo)."""
    l, t, r, b = ink_box(text, font_path)
    x, y, w, h = bbox
    along, across = (w, h) if angle_deg in (0, 180) else (h, w)
    size = 0.5 * (along / (r - l) + across / (b - t))
    x0, y0, _, _ = rotated_bbox_local((l * size, t * size, r * size, b * size), angle_deg, False)
    return size, (x - x0, y - y0)
