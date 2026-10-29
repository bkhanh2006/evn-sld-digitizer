"""Đọc file SVG ký hiệu trong assets/symbols_svg.

Chỉ hỗ trợ tập con mà thư viện ký hiệu dùng: line, polyline, polygon, rect, circle (tọa độ số,
không có transform). Nhờ vậy cùng một hình được chuyển chính xác sang block DXF.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

SVG_NS = "{http://www.w3.org/2000/svg}"
DEFAULT_STROKE = 2.0


@dataclass(frozen=True)
class Primitive:
    kind: str                                  # "polyline" | "circle"
    points: tuple[tuple[float, float], ...]    # polyline: các đỉnh; circle: (tâm,)
    radius: float = 0.0
    closed: bool = False
    filled: bool = False
    stroke_width: float = DEFAULT_STROKE

    def bounds(self) -> tuple[float, float, float, float]:
        if self.kind == "circle":
            (cx, cy), r = self.points[0], self.radius
            return cx - r, cy - r, cx + r, cy + r
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)


def _num(el: ET.Element, name: str) -> float:
    value = el.get(name)
    if value is None or value == "":
        raise ValueError(f"<{el.tag}> thiếu thuộc tính {name}")
    return float(value)


def _points(text: str) -> tuple[tuple[float, float], ...]:
    pts = []
    for pair in text.split():
        x, y = pair.split(",")
        pts.append((float(x), float(y)))
    return tuple(pts)


def parse_symbol_svg(path: Path) -> list[Primitive]:
    root = ET.parse(path).getroot()
    prims: list[Primitive] = []

    def walk(el: ET.Element, stroke: float, fill: str) -> None:
        if "transform" in el.attrib:
            raise ValueError(f"{path.name}: không hỗ trợ thuộc tính transform")
        stroke = float(el.get("stroke-width", stroke))
        fill = el.get("fill", fill)
        filled = fill not in ("none", "")
        tag = el.tag.removeprefix(SVG_NS)
        if tag == "line":
            prims.append(Primitive("polyline", ((_num(el, "x1"), _num(el, "y1")), (_num(el, "x2"), _num(el, "y2"))),
                                   stroke_width=stroke))
        elif tag in ("polyline", "polygon"):
            prims.append(Primitive("polyline", _points(el.get("points", "")), closed=tag == "polygon",
                                   filled=filled and tag == "polygon", stroke_width=stroke))
        elif tag == "rect":
            x, y, w, h = _num(el, "x"), _num(el, "y"), _num(el, "width"), _num(el, "height")
            prims.append(Primitive("polyline", ((x, y), (x + w, y), (x + w, y + h), (x, y + h)), closed=True,
                                   filled=filled, stroke_width=stroke))
        elif tag == "circle":
            prims.append(Primitive("circle", ((_num(el, "cx"), _num(el, "cy")),), radius=_num(el, "r"),
                                   filled=filled, stroke_width=stroke))
        elif tag not in ("svg", "g"):
            raise ValueError(f"{path.name}: thẻ <{tag}> chưa được hỗ trợ")
        for child in el:
            walk(child, stroke, fill)

    walk(root, DEFAULT_STROKE, "none")
    return prims


def primitives_bounds(prims: list[Primitive]) -> tuple[float, float, float, float]:
    """Hộp bao hình học (đường tâm nét, không tính bề dày nét)."""
    b = [p.bounds() for p in prims]
    return min(q[0] for q in b), min(q[1] for q in b), max(q[2] for q in b), max(q[3] for q in b)


def viewbox(path: Path) -> tuple[float, float, float, float]:
    x, y, w, h = (float(v) for v in ET.parse(path).getroot().get("viewBox").split())
    return x, y, x + w, y + h
