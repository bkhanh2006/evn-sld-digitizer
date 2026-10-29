"""Lát cắt dọc (SDD 1.1-5, 10.4): sơ đồ rút gọn dựng tay (fixture slice_graph) → graph → SVG/DXF/PNG
→ đọc lại và kiểm tra."""
import io
import json
import math
import re
import xml.etree.ElementTree as ET

import ezdxf
import numpy as np
import pytest
import resvg_py
from PIL import Image

from src.core.net_builder import check_nets_consistent
from src.core.schema import SubstationTopologyGraph, check_invariants
from src.core.symbol_lib import default_library
from src.core.symbol_svg import SVG_NS
from src.core.text_metrics import find_font
from src.exporters.dxf_exporter import AutoCADDXFExporter, read_back
from src.exporters.svg_exporter import SVGInteractiveExporter, extract_embedded, split_hops

PORT_TOL_MM = 0.01


def test_slice_graph_is_valid(slice_graph, builder):
    assert check_invariants(slice_graph) == []
    assert check_nets_consistent(slice_graph, builder.lib)


def test_svg_has_one_use_per_device_and_embeds_graph(slice_graph):
    svg = SVGInteractiveExporter().build(slice_graph)
    root = ET.fromstring(svg)
    layer = next(g for g in root.iter(f"{SVG_NS}g") if g.get("id") == "layer-devices")
    uses = list(layer.iter(f"{SVG_NS}use"))
    assert len(uses) == len(slice_graph.devices)
    assert {u.get("data-id") for u in uses} == {d.id for d in slice_graph.devices}
    embedded = extract_embedded(svg)
    assert SubstationTopologyGraph.from_json(embedded["graph-data"]) == slice_graph
    meta = json.loads(embedded["sld-meta"])
    assert meta["symbols"]["circuit_breaker"]["conduction"] == "by_state"


def test_svg_use_transform_matches_port_projection(slice_graph):
    """Cổng chiếu qua transform của <use> (theo đúng chuỗi phép SVG) trùng tọa độ cổng trong đồ thị."""
    lib = default_library()
    svg = SVGInteractiveExporter().build(slice_graph, interactive=False)
    root = ET.fromstring(svg)
    by_pos = {}
    for u in root.iter(f"{SVG_NS}use"):
        nums = [float(v) for v in re.findall(r"-?\d+(?:\.\d+)?", u.get("transform"))]
        tx, ty, rot, sx, sy = nums
        by_pos[(round(tx, 3), round(ty, 3))] = (u.get("href").removeprefix("#sym-"), rot, sx, sy)
    for d in slice_graph.devices:
        cls, rot, sx, sy = by_pos[(round(d.transform.x, 3), round(d.transform.y, 3))]
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        for pdef in lib[cls].ports:
            u, v = pdef.local_x * sx, pdef.local_y * sy          # scale trước, rồi rotate, rồi translate
            x, y = d.transform.x + c * u - s * v, d.transform.y + s * u + c * v
            gp = d.port(pdef.id)
            assert (x, y) == pytest.approx((gp.x, gp.y), abs=1e-6)


def test_dxf_round_trip(slice_graph, tmp_path):
    path = tmp_path / "slice.dxf"
    AutoCADDXFExporter().export(slice_graph, path)
    doc = ezdxf.readfile(path)
    auditor = doc.audit()
    assert not auditor.has_errors
    back = read_back(doc, slice_graph)
    assert back["inserts"] == len(slice_graph.devices)
    ppm = slice_graph.page.px_per_mm
    for d in slice_graph.devices:
        for p in d.ports:
            x, y = back["ports"][d.id][p.id]
            assert abs(x - p.x) / ppm < PORT_TOL_MM and abs(y - p.y) / ppm < PORT_TOL_MM
    # mã thiết bị nằm trong ATTRIB của INSERT
    codes = {a.dxf.text for e in doc.modelspace().query("INSERT") for a in e.attribs}
    assert {"171", "131-1", "TI131"} <= codes
    assert {"HV_110KV", "BUSBAR_110KV", "TEXT"} <= {layer.dxf.name for layer in doc.layers}


def test_png_render_has_ink_on_wires_and_ports(slice_graph):
    svg = SVGInteractiveExporter().build(slice_graph, interactive=False)
    png = resvg_py.svg_to_bytes(svg_string=svg, skip_system_fonts=True,
                                font_files=[str(find_font("arial.ttf"))])
    img = np.asarray(Image.open(io.BytesIO(png)).convert("L"))
    assert img.shape == (slice_graph.page.height, slice_graph.page.width)
    for w in slice_graph.wires:
        (x0, y0), (x1, y1) = w.points[0], w.points[-1]
        mx, my = round((x0 + x1) / 2), round((y0 + y1) / 2)
        assert img[my, mx] < 128, w.id
    for t in slice_graph.texts:
        x, y, bw, bh = (round(v) for v in t.bbox)
        assert (img[y:y + bh + 1, x:x + bw + 1] < 128).any(), t.id


def test_hop_split_bulges_over_crossing():
    steps = split_hops([(0, 0), (100, 0)], [(50, 0)], 5)
    assert steps == [("L", (45, 0)), ("A", (55, 0), 1), ("L", (100, 0))]
    steps = split_hops([(0, 100), (0, 0)], [(0, 50)], 5)       # đi lên: cung sang trái, sweep 1
    assert steps[1] == ("A", (0, 45), 1)
