"""AutoCADDXFExporter (SDD 8.4.2): đồ thị → bản vẽ .dxf bằng ezdxf.

Block ký hiệu lấy từ assets/symbols.dxf: file này được mở làm tài liệu mẫu (đã có đủ 19 block,
layer SYM_PORTS và APPID), nên không cần chép block sang tài liệu mới. Mỗi INSERT mang xdata
(APPID, id thiết bị) để đọc lại khi kiểm tra round-trip.

Cầu vượt (nút hop_arc) được vẽ thành đoạn cung có bulge ngay trong LWPOLYLINE của dây.
"""
from __future__ import annotations

import math
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

from src.core.geometry import Placement, cad_insert_params, to_cad_point
from src.core.schema import Device, SubstationTopologyGraph
from src.core.symbol_lib import ASSETS_DIR, SymbolLibrary, default_library
from src.core.text_metrics import find_font, fit_text
from src.exporters.svg_exporter import hop_points, load_layer_config, split_hops

SYMBOLS_DXF = ASSETS_DIR / "symbols.dxf"
XDATA_APPID = "SLD_DIGITIZER"          # trùng scripts/build_symbols_dxf.py
CAP_HEIGHT_RATIO = 0.716               # chiều cao chữ hoa / cỡ chữ của Arial; DXF đo chữ theo chữ hoa


class DXFExportError(RuntimeError):
    pass


class AutoCADDXFExporter:
    def __init__(self, symbol_lib: SymbolLibrary | None = None, layer_config: dict | None = None,
                 template: Path = SYMBOLS_DXF):
        self.lib = symbol_lib or default_library()
        self.cfg = layer_config or load_layer_config()
        self.template = template

    # ------------------------------------------------------------------ tiện ích

    def _layer(self, doc: Drawing, name: str, color: int, lineweight: int | None = None) -> str:
        if name not in doc.layers:
            layer = doc.layers.add(name, color=color)
            if lineweight is not None:
                layer.dxf.lineweight = lineweight
        return name

    def _voltage_layer(self, doc: Drawing, kv: int | None, busbar: bool = False) -> str:
        spec = self.cfg["voltage_layers"].get(kv, self.cfg["unknown_voltage"])
        if busbar:
            return self._layer(doc, spec["busbarLayer"], spec["color"], self.cfg["lineweight"]["busbar"])
        return self._layer(doc, spec["layer"], spec["color"], self.cfg["lineweight"]["wire"])

    def _net_layer(self, doc: Drawing, graph: SubstationTopologyGraph, net_id: str | None) -> str:
        net = next((n for n in graph.nets if n.id == net_id), None)
        if net is not None and net.kind == "ground":
            g = self.cfg["ground"]
            return self._layer(doc, g["layer"], g["color"], self.cfg["lineweight"]["wire"])
        return self._voltage_layer(doc, net.voltage_kv if net else None)

    @staticmethod
    def placement(dev: Device) -> Placement:
        t = dev.transform
        return Placement(t.x, t.y, t.rotation_deg, t.mirror_x, t.scale)

    # ------------------------------------------------------------------ xuất

    def export(self, graph: SubstationTopologyGraph, path: Path | str | None = None) -> Drawing:
        doc = ezdxf.readfile(self.template)
        doc.units = ezdxf.units.MM
        msp = doc.modelspace()
        for e in list(msp):
            msp.delete_entity(e)

        page = graph.page
        ppm, H = page.px_per_mm, page.height

        def cad(x: float, y: float) -> tuple[float, float]:
            return to_cad_point(x, y, H, ppm)

        text_cfg = self.cfg["text"]
        if text_cfg["style"] not in doc.styles:
            doc.styles.add(text_cfg["style"], font=text_cfg["font"])
        text_layer = self._layer(doc, text_cfg["layer"], text_cfg["color"])
        index = graph.net_index()

        # Thanh cái
        for b in graph.busbars:
            kv = b.voltage_kv
            if kv is None:
                net = next((n for n in graph.nets if n.id == index.get(("busbar", b.id))), None)
                kv = net.voltage_kv if net else None
            width = self.cfg["busbarWidthMm"] or b.stroke_width_px / ppm
            msp.add_lwpolyline([cad(x, y) for x, y in b.polyline],
                               dxfattribs={"layer": self._voltage_layer(doc, kv, busbar=True), "const_width": width})

        # Dây (cầu vượt = đoạn bulge nửa vòng tròn)
        hops = hop_points(graph)
        hop_r = self.cfg["hopRadiusMm"] * ppm
        for w in graph.wires:
            pts = [(*cad(*w.points[0]), 0.0)]
            for step in split_hops(list(w.points), hops.get(w.id, []), hop_r):
                if step[0] == "A":
                    # sweep=1 là cung theo chiều kim đồng hồ khi nhìn → bulge âm trong CAD (Y lên)
                    x0, y0, _ = pts[-1]
                    pts[-1] = (x0, y0, -1.0 if step[2] == 1 else 1.0)
                pts.append((*cad(*step[1]), 0.0))
            msp.add_lwpolyline(pts, format="xyb",
                               dxfattribs={"layer": self._net_layer(doc, graph, index.get(("wire", w.id)))})

        # Nút: chấm nối
        dot_r = self.cfg["junctionDotRadiusMm"]
        for n in graph.nodes:
            if n.type == "junction" and n.style == "dot":
                first = n.connections[0].key() if n.connections else None
                layer = self._net_layer(doc, graph, index.get(first))
                hatch = msp.add_hatch(dxfattribs={"layer": layer})
                hatch.paths.add_edge_path().add_arc(cad(*n.position), dot_r, 0, 360)

        # Thiết bị
        texts = {t.id: t for t in graph.texts}
        font_path = str(find_font(text_cfg["font"]))
        for d in graph.devices:
            sym = self.lib[d.detector_class]
            p = self.placement(d)
            port_net = index.get(("port", d.id, d.ports[0].id)) if d.ports else None
            layer = self._net_layer(doc, graph, port_net)
            if d.code is not None and d.code.voltage_kv is not None:
                layer = self._voltage_layer(doc, d.code.voltage_kv)
            ref = msp.add_blockref(sym.dxf_block_name, cad(p.x, p.y),
                                   dxfattribs={"layer": layer, **cad_insert_params(p, ppm, self.lib.library_unit_mm)})
            ref.set_xdata(XDATA_APPID, [(1000, d.id)])
            values = {}
            if d.code is not None and "DEVICE_CODE" in sym.cad_attributes:
                values["DEVICE_CODE"] = d.code.normalized
            if values:
                ref.add_auto_attribs(values)
            label = texts.get(d.label_text_ids[0]) if d.label_text_ids else None
            if label is not None and label.raw.strip():
                for attrib in ref.attribs:
                    if attrib.dxf.tag == "DEVICE_CODE":
                        self._place_text(attrib, label, font_path, cad, ppm)
                        attrib.dxf.layer = text_layer

        # Chữ không thuộc thiết bị
        assigned = {tid for d in graph.devices for tid in d.label_text_ids}
        for t in graph.texts:
            if t.id in assigned or not t.raw.strip():
                continue
            size, (ax, ay) = fit_text(t.raw, font_path, tuple(t.bbox), t.angle_deg)
            mtext = msp.add_mtext(t.raw, dxfattribs={"layer": text_layer, "style": text_cfg["style"],
                                                      "char_height": size * CAP_HEIGHT_RATIO / ppm,
                                                      "rotation": (-t.angle_deg) % 360,
                                                      "attachment_point": 7})
            mtext.dxf.insert = cad(ax, ay)

        self._review_clouds(doc, graph, cad, ppm)

        auditor = doc.audit()
        if auditor.has_errors:
            raise DXFExportError("; ".join(str(e.message) for e in auditor.errors))
        if path is not None:
            doc.saveas(path)
        return doc

    def _place_text(self, attrib, text, font_path: str, cad, ppm: float) -> None:
        """Dời ATTRIB tới vị trí nhãn trên bản gốc (SDD 8.4.2 bước 4)."""
        size, (ax, ay) = fit_text(text.raw, font_path, tuple(text.bbox), text.angle_deg)
        attrib.dxf.insert = cad(ax, ay)
        attrib.dxf.height = size * CAP_HEIGHT_RATIO / ppm
        attrib.dxf.rotation = (-text.angle_deg) % 360
        attrib.dxf.style = self.cfg["text"]["style"]
        attrib.dxf.halign = 0
        attrib.dxf.valign = 0
        attrib.is_invisible = False

    # ------------------------------------------------------------------ AI_REVIEW

    def _review_clouds(self, doc: Drawing, graph: SubstationTopologyGraph, cad, ppm: float) -> None:
        """Đám mây quanh issue và đối tượng còn pending (SDD 8.4.2 bước 5)."""
        rv = self.cfg["review"]
        targets: list[tuple[tuple[float, float, float, float], str]] = []
        for i in graph.issues:
            if i.position is not None:
                x, y = i.position
                targets.append(((x, y, 0.0, 0.0), f"{i.type} {i.id}"))
        for d in graph.devices:
            if d.review_status == "pending":
                targets.append((tuple(d.bbox), f"pending {d.id}"))
        for n in graph.nodes:
            if n.review_status == "pending":
                targets.append(((*n.position, 0.0, 0.0), f"pending {n.id}"))
        if not targets:
            return
        layer = self._layer(doc, rv["layer"], rv["color"])
        msp = doc.modelspace()
        margin = rv["cloudMarginMm"] * ppm
        for (x, y, w, h), label in targets:
            x0, y0 = cad(x - margin, y + h + margin)
            x1, y1 = cad(x + w + margin, y - margin)
            msp.add_lwpolyline(_cloud(x0, y0, x1, y1, rv["cloudMarginMm"]), format="xyb", close=True,
                               dxfattribs={"layer": layer})
            m = msp.add_mtext(label, dxfattribs={"layer": layer, "char_height": rv["textHeightMm"],
                                                 "attachment_point": 7})
            m.dxf.insert = (x0, y1 + 0.5)


def _cloud(x0: float, y0: float, x1: float, y1: float, arc_mm: float) -> list[tuple[float, float, float]]:
    """Hình chữ nhật viền đám mây: các cung nhỏ lồi ra ngoài (bulge âm khi đi ngược chiều kim đồng hồ)."""
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    pts = []
    for (ax, ay), (bx, by) in zip(corners, corners[1:] + corners[:1]):
        n = max(1, math.ceil(math.hypot(bx - ax, by - ay) / arc_mm))
        for k in range(n):
            pts.append((ax + (bx - ax) * k / n, ay + (by - ay) * k / n, -0.5))
    return pts


def read_back(path_or_doc, graph: SubstationTopologyGraph, symbol_lib: SymbolLibrary | None = None) -> dict:
    """Đọc lại DXF: số INSERT và tọa độ cổng (px) của từng thiết bị, suy từ POINT cổng trong block."""
    from src.core.geometry import from_cad_point

    lib = symbol_lib or default_library()
    doc = path_or_doc if isinstance(path_or_doc, Drawing) else ezdxf.readfile(path_or_doc)
    port_layer = "SYM_PORTS"
    inserts = [e for e in doc.modelspace() if e.dxftype() == "INSERT"]
    ports: dict[str, dict[str, tuple[float, float]]] = {}
    for ins in inserts:
        dev_id = ins.get_xdata(XDATA_APPID)[0][1]
        m = ins.matrix44()
        out = {}
        for e in doc.blocks.get(ins.dxf.name):
            if e.dxftype() == "POINT" and e.dxf.layer == port_layer:
                c = m.transform(e.dxf.location)
                out[e.get_xdata(XDATA_APPID)[0][1]] = from_cad_point(c.x, c.y, graph.page.height,
                                                                     graph.page.px_per_mm)
        ports[dev_id] = out
    return {"inserts": len(inserts), "ports": ports, "doc": doc, "lib": lib}
