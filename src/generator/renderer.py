"""SyntheticRenderer (SDD 8.1.4): đồ thị → SVG tĩnh → PNG bằng resvg, thêm vật gây nhiễu và nhiễu ảnh.

- Ảnh sạch (`clean`): ảnh xám ngay sau khi vẽ, đã có khung, khung tên, dấu, chữ ký; dùng cho
  tiêu chí 8 của validate_dataset.
- Ảnh cuối (`image`): ảnh màu sau xám hóa không đều, làm mờ, nén JPEG.
- Mask dây: chỉ thanh cái + dây + chấm nối, cùng độ dày nét, cùng bộ vẽ (khớp từng pixel với ảnh).
Nghiêng ảnh chưa bật (skew_max_deg = 0): nghiêng làm lệch đáp án, phải biến đổi đồ thị theo.
"""
from __future__ import annotations

import io
import math
import random
from dataclasses import dataclass

import numpy as np
import resvg_py
from PIL import Image, ImageFilter

from src.core.schema import SubstationTopologyGraph
from src.core.symbol_lib import SymbolLibrary
from src.core.text_metrics import find_font
from src.exporters.svg_exporter import DrawStyle, SVGInteractiveExporter, _fmt
from src.generator.layout_composer import Layout

DARK = 160                      # ngưỡng "điểm tối" trên ảnh xám
MASK_ON = 128


@dataclass
class RenderResult:
    svg: str                    # SVG tĩnh đã dùng để vẽ ảnh
    clean: np.ndarray           # uint8 xám, trước nhiễu
    image: Image.Image          # RGB, sau nhiễu
    mask: np.ndarray            # uint8 {0, 255}


def font_files(gen_cfg: dict) -> list[str]:
    files = []
    for f in gen_cfg["style"]["fonts"]:
        files += [str(find_font(f["file"])), str(find_font(f["bold"]))]
    return files


def svg_to_array(svg: str, fonts: list[str]) -> np.ndarray:
    png = resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True, font_files=fonts)
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGB"))


class SyntheticRenderer:
    def __init__(self, lib: SymbolLibrary, gen_cfg: dict):
        self.lib, self.cfg = lib, gen_cfg
        self.exporter = SVGInteractiveExporter(lib)
        self.fonts = font_files(gen_cfg)

    def draw_style(self, layout: Layout) -> DrawStyle:
        st = layout.style
        return DrawStyle(symbol_stroke_scale=st.symbol_stroke_scale, font=st.font, text_fonts=layout.text_fonts)

    # ------------------------------------------------------------------ vật gây nhiễu (không có nhãn)

    def _frame(self, layout: Layout) -> str:
        ppm = layout.graph.page.px_per_mm
        w = 0.35 * ppm
        x0, y0, x1, y1 = layout.frame_px
        parts = [f'<rect x="{_fmt(x0)}" y="{_fmt(y0)}" width="{_fmt(x1 - x0)}" height="{_fmt(y1 - y0)}" '
                 f'fill="none" stroke="#000" stroke-width="{_fmt(w)}"/>']
        tx0, ty0, tx1, ty1 = layout.title_block_px
        parts.append(f'<line x1="{_fmt(tx0)}" y1="{_fmt(ty0)}" x2="{_fmt(tx1)}" y2="{_fmt(ty0)}" stroke="#000" '
                     f'stroke-width="{_fmt(w)}"/>')
        for c in layout.title_cells_px[1:]:
            parts.append(f'<line x1="{_fmt(c[0])}" y1="{_fmt(ty0)}" x2="{_fmt(c[0])}" y2="{_fmt(ty1)}" '
                         f'stroke="#000" stroke-width="{_fmt(w * 0.7)}"/>')
        return "".join(parts)

    def _signature(self, cell, rng: random.Random, ppm: float) -> str:
        x0, y0, x1, y1 = cell
        cx, cy = (x0 + x1) / 2 + rng.uniform(-0.15, 0.15) * (x1 - x0), (y0 + y1) / 2
        w, h = (x1 - x0) * rng.uniform(0.3, 0.5), (y1 - y0) * rng.uniform(0.5, 0.9)
        pts = [(cx - w / 2, cy + rng.uniform(-h, h) / 2)]
        d = [f"M{_fmt(pts[0][0])} {_fmt(pts[0][1])}"]
        n = rng.randint(4, 8)
        for i in range(1, n + 1):
            x = cx - w / 2 + w * i / n
            y = cy + rng.uniform(-h, h) / 2
            c1 = (x - w / n * rng.uniform(0.2, 1.2), cy + rng.uniform(-h, h) / 2)
            d.append(f"Q{_fmt(c1[0])} {_fmt(c1[1])} {_fmt(x)} {_fmt(y)}")
        color = rng.choice(("#1a237e", "#0d47a1", "#111111"))
        return (f'<path d="{" ".join(d)}" fill="none" stroke="{color}" stroke-width="{_fmt(0.3 * ppm)}" '
                f'stroke-linecap="round" opacity="0.85"/>')

    def _stamp(self, layout: Layout, rng: random.Random) -> str:
        ppm = layout.graph.page.px_per_mm
        tx0, ty0, tx1, ty1 = layout.title_block_px
        r = rng.uniform(11, 15) * ppm
        cell = layout.title_cells_px[1]
        cx = cell[0] + rng.uniform(-0.3, 0.3) * (cell[2] - cell[0])
        cy = (ty0 + ty1) / 2 + rng.uniform(-0.2, 0.2) * (ty1 - ty0)
        color = self.cfg["render"]["red_stamp_color"]
        letters = "".join(rng.choice("ABCDEGHIKLMNOPQRSTUVXY ") for _ in range(26))
        sw = 0.35 * ppm
        fs = 0.17 * r
        star = []
        for k in range(10):
            rr = 0.22 * r if k % 2 == 0 else 0.09 * r
            a = -math.pi / 2 + k * math.pi / 5
            star.append(f"{_fmt(cx + rr * math.cos(a))},{_fmt(cy + rr * math.sin(a))}")
        pid = f"stamp-ring-{rng.randint(0, 10**6)}"
        ring = 0.78 * r
        return (f'<g opacity="{_fmt(rng.uniform(0.65, 0.9))}" fill="none" stroke="{color}" stroke-width="{_fmt(sw)}">'
                f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(r)}"/>'
                f'<circle cx="{_fmt(cx)}" cy="{_fmt(cy)}" r="{_fmt(0.6 * r)}"/>'
                f'<path id="{pid}" d="M{_fmt(cx - ring)} {_fmt(cy)} a{_fmt(ring)} {_fmt(ring)} 0 1 1 {_fmt(2 * ring)} 0 '
                f'a{_fmt(ring)} {_fmt(ring)} 0 1 1 {_fmt(-2 * ring)} 0" stroke="none"/>'
                f'<text font-family="Arial" font-size="{_fmt(fs)}" fill="{color}" stroke="none">'
                f'<textPath href="#{pid}">{letters}</textPath></text>'
                f'<polygon points="{" ".join(star)}" fill="{color}" stroke="none"/></g>')

    # ------------------------------------------------------------------ vẽ

    def static_svg(self, layout: Layout, rng: random.Random) -> str:
        rc = self.cfg["render"]
        ppm = layout.graph.page.px_per_mm
        overlay = []
        for cell in layout.signature_cells_px:
            if rng.random() < rc["signature_p"]:
                overlay.append(self._signature(cell, rng, ppm))
        if rng.random() < rc["stamp_p"]:
            overlay.append(self._stamp(layout, rng))
        return self.exporter.build(layout.graph, self.draw_style(layout), interactive=False,
                                   underlay=self._frame(layout), overlay="".join(overlay))

    def mask(self, graph: SubstationTopologyGraph, style: DrawStyle) -> np.ndarray:
        """Mask dây: thanh cái + dây + chấm nối, nét trắng trên nền đen, cùng độ dày nét với ảnh."""
        w, h = graph.page.width, graph.page.height
        white = DrawStyle(symbol_stroke_scale=style.symbol_stroke_scale, color="#ffffff", background=None)
        dot_r, hop_r = self.exporter.radii(graph, white)
        body = (self.exporter.layer_busbars(graph, {}, white, False)
                + self.exporter.layer_wires(graph, {}, white, False, hop_r)
                + self.exporter.layer_nodes(graph, {}, white, False, dot_r))
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">'
               f'<rect width="{w}" height="{h}" fill="#000"/>{body}</svg>')
        arr = svg_to_array(svg, self.fonts)[:, :, 0]
        return np.where(arr >= MASK_ON, 255, 0).astype(np.uint8)

    def noisy(self, rgb: np.ndarray, rng: random.Random) -> Image.Image:
        rc = self.cfg["render"]
        img = rgb.astype(np.float32)
        h, w = img.shape[:2]
        amp = rng.uniform(*rc["gray_unevenness"])
        if amp > 0:
            nrng = np.random.default_rng(rng.randint(0, 2**31))
            low = nrng.random((6, 8)).astype(np.float32)
            field = np.asarray(Image.fromarray((low * 255).astype(np.uint8)).resize((w, h), Image.BICUBIC),
                               dtype=np.float32) / 255.0
            img = img * (1.0 - amp * field[:, :, None])
        out = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
        sigma = rng.uniform(*rc["blur_sigma_px"])
        if sigma > 0.05:
            out = out.filter(ImageFilter.GaussianBlur(sigma))
        buf = io.BytesIO()
        out.save(buf, format="JPEG", quality=rng.randint(*rc["jpeg_quality"]))
        return Image.open(io.BytesIO(buf.getvalue())).convert("RGB")

    def render(self, layout: Layout, rng: random.Random) -> RenderResult:
        if self.cfg["render"].get("skew_max_deg", 0):
            raise NotImplementedError("Nghiêng ảnh cần biến đổi cả đồ thị đáp án; giữ skew_max_deg = 0")
        svg = self.static_svg(layout, rng)
        rgb = svg_to_array(svg, self.fonts)
        clean = np.asarray(Image.fromarray(rgb).convert("L"))
        return RenderResult(svg=svg, clean=clean, image=self.noisy(rgb, rng),
                            mask=self.mask(layout.graph, self.draw_style(layout)))
