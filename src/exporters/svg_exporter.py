"""SVGInteractiveExporter (SDD 8.4.1) và các hàm vẽ SVG dùng chung với renderer của bộ sinh.

Ký hiệu nạp từ assets/symbols_svg thành `<g id="sym-<lớp>">` trong `<defs>`, đặt bằng `<use>` với
transform = translate(x y) rotate(θ) scale(m·s s), s = px/lu (SDD 2.2). Dùng `<g>` thay `<symbol>`
để không phát sinh viewport riêng (symbol không có viewBox bị cắt ở gốc tọa độ).

Nút crossing kiểu `hop_arc`: dây ở `connections[0]` vẽ cầu vượt nửa vòng tròn tại điểm giao.
"""
from __future__ import annotations

import json
import math
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from functools import lru_cache
from html import escape
from pathlib import Path

import yaml

from src.core.net_builder import NON_CONDUCTING_PORT_ROLES, SOURCE_DEVICE_TYPE, Energizer, EnergizationResult
from src.core.schema import NodeRef, SubstationTopologyGraph, Wire, WireRef
from src.core.symbol_lib import SymbolLibrary, default_library
from src.core.symbol_svg import SVG_NS
from src.core.text_metrics import find_font, fit_text, nfc

ROOT = Path(__file__).resolve().parents[2]
CAD_LAYERS_PATH = ROOT / "configs" / "cad_layers.yaml"
JS_PATH = Path(__file__).with_name("svg_interactive.js")
LAYERS = ("busbars", "wires", "nodes", "devices", "texts", "issues")
LAYER_NAMES_VI = {"source": "Ảnh gốc", "busbars": "Thanh cái", "wires": "Dây", "nodes": "Nút",
                  "devices": "Thiết bị", "texts": "Chữ", "issues": "Cảnh báo"}


@lru_cache(maxsize=4)
def load_layer_config(path: Path = CAD_LAYERS_PATH) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _fmt(v: float) -> str:
    """Số gọn cho SVG: tối đa 3 chữ số thập phân."""
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s == "-0" else s


# ---------------------------------------------------------------------------- ký hiệu

def _el_to_svg(el: ET.Element, stroke_scale: float) -> str:
    tag = el.tag.removeprefix(SVG_NS)
    attrs = []
    for k, v in el.attrib.items():
        if k == "stroke-width":
            v = _fmt(float(v) * stroke_scale)
        if k.startswith("data-"):
            continue
        attrs.append(f'{k}="{escape(v)}"')
    children = "".join(_el_to_svg(c, stroke_scale) for c in el)
    head = f"<{tag} {' '.join(attrs)}" if attrs else f"<{tag}"
    return f"{head}>{children}</{tag}>" if children else f"{head}/>"


def symbol_def(lib: SymbolLibrary, cls: str, stroke_scale: float = 1.0) -> str:
    """`<g id="sym-<lớp>">` chứa hình ký hiệu ở đơn vị lu; nét dùng currentColor."""
    root = ET.parse(lib[cls].svg_file).getroot()
    body = "".join(_el_to_svg(c, stroke_scale) for c in root)
    return f'<g id="sym-{cls}">{body}</g>'


def use_transform(x: float, y: float, rotation_deg: int, mirror_x: bool, scale: float) -> str:
    m = -1.0 if mirror_x else 1.0
    return f"translate({_fmt(x)} {_fmt(y)}) rotate({rotation_deg}) scale({_fmt(m * scale)} {_fmt(scale)})"


# ---------------------------------------------------------------------------- dây và cầu vượt

def hop_points(graph: SubstationTopologyGraph) -> dict[str, list[tuple[float, float]]]:
    """wireId → các điểm dây đó phải vẽ cầu vượt (nút hop_arc, dây đứng đầu connections)."""
    out: dict[str, list[tuple[float, float]]] = {}
    for n in graph.nodes:
        if n.type == "crossing_no_connect" and n.style == "hop_arc" and n.connections:
            c = n.connections[0]
            if isinstance(c, WireRef):
                out.setdefault(c.id, []).append(tuple(n.position))
    return out


def split_hops(points: list[tuple[float, float]], hops: list[tuple[float, float]], r: float):
    """Chia polyline thành các bước ("L", điểm) hoặc ("A", điểm, sweep) với cầu vượt bán kính r.

    Cầu vượt trên đoạn ngang cong lên trên, trên đoạn dọc cong sang trái (trên màn hình).
    sweep = 1 là cung theo chiều kim đồng hồ trên màn hình.
    """
    steps: list[tuple] = []
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        length = math.hypot(x1 - x0, y1 - y0)
        if length == 0:
            continue
        dx, dy = (x1 - x0) / length, (y1 - y0) / length
        on_seg = []
        for hx, hy in hops:
            t = (hx - x0) * dx + (hy - y0) * dy
            off = abs((hx - x0) * dy - (hy - y0) * dx)
            if off < 1e-6 and r < t < length - r:
                on_seg.append(t)
        for t in sorted(on_seg):
            steps.append(("L", (x0 + dx * (t - r), y0 + dy * (t - r))))
            sweep = 1 if (round(dx), round(dy)) in ((1, 0), (0, -1)) else 0
            steps.append(("A", (x0 + dx * (t + r), y0 + dy * (t + r)), sweep))
        steps.append(("L", (x1, y1)))
    return steps


def wire_path_d(points: list[tuple[float, float]], hops: list[tuple[float, float]], r: float) -> str:
    d = [f"M{_fmt(points[0][0])} {_fmt(points[0][1])}"]
    for step in split_hops(points, hops, r):
        (x, y) = step[1]
        if step[0] == "L":
            d.append(f"L{_fmt(x)} {_fmt(y)}")
        else:
            d.append(f"A{_fmt(r)} {_fmt(r)} 0 0 {step[2]} {_fmt(x)} {_fmt(y)}")
    return " ".join(d)


# ---------------------------------------------------------------------------- chữ

@dataclass(frozen=True)
class FontSpec:
    family: str
    file: str                  # tên file hoặc đường dẫn, tra bằng text_metrics.find_font
    weight: str = "normal"


def text_element(text: str, bbox, angle_deg: int, font: FontSpec, color: str, attrs: str = "") -> str:
    """`<text>` có cỡ chữ và điểm neo suy từ bbox, để chữ vẽ ra nằm đúng bbox."""
    size, (ax, ay) = fit_text(text, str(find_font(font.file)), tuple(bbox), angle_deg)
    rot = f' transform="rotate({angle_deg} {_fmt(ax)} {_fmt(ay)})"' if angle_deg else ""
    weight = f' font-weight="{font.weight}"' if font.weight != "normal" else ""
    return (f'<text x="{_fmt(ax)}" y="{_fmt(ay)}" font-family="{font.family}" font-size="{_fmt(size)}"'
            f'{weight} fill="{color}"{rot}{attrs}>{escape(nfc(text))}</text>')


# ---------------------------------------------------------------------------- dựng SVG

@dataclass
class DrawStyle:
    """Tham số vẽ. Độ dày dây/thanh cái lấy từ đồ thị (strokeWidthPx)."""
    symbol_stroke_scale: float = 1.0
    junction_radius_px: float | None = None    # None → junctionDotRadiusMm × pxPerMm
    hop_radius_px: float | None = None         # None → hopRadiusMm × pxPerMm
    color: str = "#000000"
    font: FontSpec | None = None               # None → font trong cad_layers.yaml
    text_fonts: dict[str, FontSpec] = field(default_factory=dict)   # textId → font riêng
    background: str | None = "#ffffff"


def _wire_net_colors(graph: SubstationTopologyGraph, energization: EnergizationResult, cfg: dict) -> dict[str, dict]:
    """netId → {color, dash} theo kết quả Energizer (SDD 8.4.1)."""
    svg = cfg["svg"]
    out = {}
    for net in graph.nets:
        st = energization.states.get(net.id)
        dash = None
        if st is None or st.energized is False:
            color = svg["deenergized"]
        elif st.energized is None:
            color, dash = svg["unknown"], svg["unknownDash"]
        else:
            layer = cfg["voltage_layers"].get(net.voltage_kv, cfg["unknown_voltage"])
            color = layer["svgColor"]
        if st is not None and st.earthed:
            color = svg["earthed"]
        out[net.id] = {"color": color, "dash": dash}
    return out


class SVGInteractiveExporter:
    def __init__(self, symbol_lib: SymbolLibrary | None = None, layer_config: dict | None = None):
        self.lib = symbol_lib or default_library()
        self.cfg = layer_config or load_layer_config()

    def default_font(self) -> FontSpec:
        return FontSpec(self.cfg["svg"]["fontFamily"], self.cfg["svg"]["fontFile"])

    # ------------------------------------------------------------------ các lớp

    def _defs(self, graph: SubstationTopologyGraph, style: DrawStyle, interactive: bool) -> str:
        classes = set(d.detector_class for d in graph.devices)
        if interactive:
            # biến thể đóng/mở cần có sẵn để JS đổi href khi click
            types = {self.lib[c].type for c in classes}
            classes |= {c for c in self.lib.classes if self.lib[c].type in types}
        body = "".join(symbol_def(self.lib, c, style.symbol_stroke_scale) for c in sorted(classes))
        return f"<defs>{body}</defs>"

    def layer_busbars(self, graph, colors, style, data: bool) -> str:
        index = graph.net_index()
        parts = []
        for b in graph.busbars:
            net = index.get(("busbar", b.id))
            c = colors.get(net, {}).get("color", style.color) if colors else style.color
            pts = " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in b.polyline)
            extra = f' data-id="{b.id}" data-net="{net}" data-code="{escape(b.code or "")}"' if data else ""
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{c}" '
                         f'stroke-width="{_fmt(b.stroke_width_px)}" stroke-linecap="butt"{extra}/>')
        return "".join(parts)

    def layer_wires(self, graph, colors, style, data: bool, hop_r: float) -> str:
        index = graph.net_index()
        hops = hop_points(graph)
        parts = []
        for w in graph.wires:
            net = index.get(("wire", w.id))
            col = colors.get(net, {}) if colors else {}
            c = col.get("color", style.color)
            dash = f' stroke-dasharray="{col["dash"]}"' if col.get("dash") else ""
            extra = f' data-id="{w.id}" data-net="{net}"' if data else ""
            parts.append(f'<path d="{wire_path_d(list(w.points), hops.get(w.id, []), hop_r)}" fill="none" '
                         f'stroke="{c}" stroke-width="{_fmt(w.stroke_width_px)}" stroke-linecap="round" '
                         f'stroke-linejoin="round"{dash}{extra}/>')
        return "".join(parts)

    def layer_nodes(self, graph, colors, style, data: bool, dot_r: float) -> str:
        index = graph.net_index()
        parts = []
        for n in graph.nodes:
            x, y = n.position
            if n.type == "junction" and n.style == "dot":
                first = n.connections[0] if n.connections else None
                net = index.get(first.key()) if first is not None else None
                c = colors.get(net, {}).get("color", style.color) if colors else style.color
                extra = f' data-id="{n.id}" data-net="{net}"' if data else ""
                parts.append(f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(dot_r)}" fill="{c}"{extra}/>')
            elif data:
                parts.append(f'<circle cx="{_fmt(x)}" cy="{_fmt(y)}" r="{_fmt(dot_r)}" fill="none" stroke="none" '
                             f'data-id="{n.id}" data-type="{n.type}" data-style="{n.style}"/>')
        return "".join(parts)

    def device_color(self, graph, dev, colors, style) -> str:
        if not colors:
            return style.color
        index = graph.net_index()
        nets = [index.get(("port", dev.id, p.id)) for p in dev.ports if p.role not in NON_CONDUCTING_PORT_ROLES]
        cols = [colors[n]["color"] for n in nets if n in colors]
        deen = self.cfg["svg"]["deenergized"]
        live = [c for c in cols if c != deen]
        return live[0] if live else (cols[0] if cols else style.color)

    def layer_devices(self, graph, colors, style, data: bool) -> str:
        parts = []
        for d in graph.devices:
            t = d.transform
            href = f"#sym-{d.detector_class}"
            c = self.device_color(graph, d, colors, style)
            extra = ""
            if data:
                code = d.code.normalized if d.code else ""
                state = d.operational_state or d.drawing_state or ""
                extra = (f' data-id="{d.id}" data-class="{d.detector_class}" data-type="{d.type}" '
                         f'data-code="{escape(code)}" data-state="{state}" style="cursor:pointer"')
            parts.append(f'<use href="{href}" transform="{use_transform(t.x, t.y, t.rotation_deg, t.mirror_x, t.scale)}" '
                         f'color="{c}"{extra}/>')
        return "".join(parts)

    def layer_texts(self, graph, style, data: bool) -> str:
        default = style.font or self.default_font()
        parts = []
        for t in graph.texts:
            if not t.raw.strip():
                continue
            font = style.text_fonts.get(t.id, default)
            extra = f' data-id="{t.id}" data-role="{t.role}"' if data else ""
            parts.append(text_element(t.raw, t.bbox, t.angle_deg, font, style.color, extra))
        return "".join(parts)

    def layer_issues(self, graph) -> str:
        cols = self.cfg["svg"]["issueColors"]
        half = 0.02 * max(graph.page.width, graph.page.height)
        parts = []
        for i in graph.issues:
            if i.position is None:
                continue
            x, y = i.position
            parts.append(f'<rect x="{_fmt(x - half)}" y="{_fmt(y - half)}" width="{_fmt(2 * half)}" '
                         f'height="{_fmt(2 * half)}" fill="none" stroke="{cols[i.severity]}" stroke-width="3" '
                         f'stroke-dasharray="8 4" data-issue="{i.id}" class="issue-{i.severity}">'
                         f'<title>{escape(i.type)}: {escape(i.message)}</title></rect>')
        return "".join(parts)

    # ------------------------------------------------------------------ xuất

    def radii(self, graph: SubstationTopologyGraph, style: DrawStyle) -> tuple[float, float]:
        ppm = graph.page.px_per_mm
        dot_r = style.junction_radius_px or self.cfg["junctionDotRadiusMm"] * ppm
        hop_r = style.hop_radius_px or self.cfg["hopRadiusMm"] * ppm
        return dot_r, hop_r

    def build(self, graph: SubstationTopologyGraph, style: DrawStyle | None = None, interactive: bool = True,
              source_image: str | None = None, sources: list[str] | None = None,
              underlay: str = "", overlay: str = "") -> str:
        """Dựng chuỗi SVG.

        interactive=False cho ảnh tĩnh của bộ sinh: nét đen, không script, không data-*.
        underlay/overlay: SVG thô chèn dưới/trên các lớp đồ thị (khung tên, vật gây nhiễu).
        """
        style = style or DrawStyle()
        w, h = graph.page.width, graph.page.height
        dot_r, hop_r = self.radii(graph, style)
        colors = {}
        if interactive:
            colors = _wire_net_colors(graph, Energizer(self.lib).energize(graph, sources), self.cfg)

        out = [f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               f'viewBox="0 0 {w} {h}" width="{w}" height="{h}">']
        out.append(self._defs(graph, style, interactive))
        if style.background:
            out.append(f'<rect id="background" x="0" y="0" width="{w}" height="{h}" fill="{style.background}"/>')
        if source_image:
            out.append(f'<g id="layer-source" opacity="0.35"><image href="{escape(source_image)}" x="0" y="0" '
                       f'width="{w}" height="{h}"/></g>')
        if underlay:
            out.append(f'<g id="layer-underlay">{underlay}</g>')
        out.append(f'<g id="layer-busbars">{self.layer_busbars(graph, colors, style, interactive)}</g>')
        out.append(f'<g id="layer-wires">{self.layer_wires(graph, colors, style, interactive, hop_r)}</g>')
        out.append(f'<g id="layer-nodes">{self.layer_nodes(graph, colors, style, interactive, dot_r)}</g>')
        out.append(f'<g id="layer-devices">{self.layer_devices(graph, colors, style, interactive)}</g>')
        out.append(f'<g id="layer-texts">{self.layer_texts(graph, style, interactive)}</g>')
        if interactive:
            out.append(f'<g id="layer-issues">{self.layer_issues(graph)}</g>')
        if overlay:
            out.append(f'<g id="layer-overlay">{overlay}</g>')
        if interactive:
            out.append(self._controls(graph, source_image is not None))
            out.append(self._scripts(graph, sources))
        out.append("</svg>")
        return "".join(out)

    def export(self, graph: SubstationTopologyGraph, path: Path | str, **kwargs) -> str:
        svg = self.build(graph, **kwargs)
        Path(path).write_text(svg, encoding="utf-8")
        return svg

    # ------------------------------------------------------------------ tương tác

    def _controls(self, graph: SubstationTopologyGraph, has_source: bool) -> str:
        size = max(graph.page.width, graph.page.height) / 120
        layers = (("source",) if has_source else ()) + LAYERS
        items = []
        for i, name in enumerate(layers):
            items.append(f'<text x="{_fmt(size)}" y="{_fmt(size * (1.6 + 1.4 * i))}" font-size="{_fmt(size)}" '
                         f'font-family="Arial" data-layer="{name}" style="cursor:pointer">■ {LAYER_NAMES_VI[name]}</text>')
        return f'<g id="ui-controls" fill="#333">{"".join(items)}</g>'

    def sld_meta(self) -> dict:
        """Thông tin JS cần, lấy từ nguồn chuẩn (metadata ký hiệu, NetBuilder, cad_layers)."""
        symbols = {c: {"type": s.type, "netBehavior": s.net_behavior, "conduction": s.conduction,
                       "drawingState": s.drawing_state} for c, s in self.lib.symbols.items()}
        svg = self.cfg["svg"]
        return {
            "symbols": symbols,
            "nonConductingPortRoles": sorted(NON_CONDUCTING_PORT_ROLES),
            "sourceDeviceType": SOURCE_DEVICE_TYPE,
            "colors": {"voltage": {str(k): v["svgColor"] for k, v in self.cfg["voltage_layers"].items()},
                       "unknownVoltage": self.cfg["unknown_voltage"]["svgColor"],
                       "deenergized": svg["deenergized"], "earthed": svg["earthed"], "unknown": svg["unknown"],
                       "unknownDash": svg["unknownDash"]},
        }

    def _scripts(self, graph: SubstationTopologyGraph, sources: list[str] | None) -> str:
        def cdata(s: str) -> str:
            return "<![CDATA[" + s.replace("]]>", "]]]]><![CDATA[>") + "]]>"

        meta = self.sld_meta()
        if sources is not None:
            meta["sources"] = sources
        js = JS_PATH.read_text(encoding="utf-8")
        return (f'<script type="application/json" id="graph-data">{cdata(graph.to_json(indent=None))}</script>'
                f'<script type="application/json" id="sld-meta">{cdata(json.dumps(meta, ensure_ascii=False))}</script>'
                f'<script type="text/javascript">{cdata(js)}</script>')


def extract_embedded(svg_text: str) -> dict[str, str]:
    """Đọc lại nội dung các thẻ <script> có id (graph-data, sld-meta) từ SVG đã xuất."""
    root = ET.fromstring(svg_text)
    out = {}
    for el in root.iter(f"{SVG_NS}script"):
        out[el.get("id") or el.get("type") or "script"] = el.text or ""
    return out
