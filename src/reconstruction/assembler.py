"""GraphAssembler (SDD 8.3.2): gom kết quả các khâu thành SubstationTopologyGraph.

PortMatcher → dựng devices / busbars / wires / nodes / texts / bays → NetBuilder → EVNRuleValidator.
"""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from src.core.evn_codes import EVNCodeGrammar, default_grammar
from src.core.geometry import Placement
from src.core.net_builder import NetBuilder
from src.core.registry import register
from src.core.schema import (Bay, Busbar, BusbarRef, DanglingRef, Device, DeviceCode, DevicePort, DeviceRef, Issue,
                             Node, NodeRef, Page, PortRef, Provenance, SubstationTopologyGraph, Text, Transform, Wire,
                             WireRef)
from src.core.symbol_lib import SymbolLibrary, default_library
from src.core.types import DetectedSymbol, LabelAssignment, NormalizedImage, TextItem, WireExtraction
from src.perception.busbar_classifier import DrawingArea
from src.perception.label_matcher import LINE_TERMINAL_TYPE, box_gap, text_height
from src.reconstruction.graph_validator import EVNRuleValidator
from src.reconstruction.port_matcher import PortMatcher

PIPELINE_VERSION = "0.3.0"

# chi tiết vị trí máy cắt (evn_codes) → loại ngăn
BAY_KIND_BY_POSITION = {"line": "line", "transformer": "transformer", "aux_transformer": "aux",
                        "shunt_or_generator": "capacitor", "bus_coupler": "bus_coupler"}
BAY_KIND_BY_PATTERN = {"bus_vt": "bus_vt", "capacitor_bay_breaker": "capacitor", "reactor_bay_breaker": "reactor",
                       "host_switch": "unknown", "main_transformer": "transformer"}


@dataclass
class AssemblyContext:
    page_id: str
    source_file: str | None = None
    area: DrawingArea | None = None
    extra_issues: list[Issue] = field(default_factory=list)
    config_path: str | None = None


def _round_pt(p) -> tuple[float, float]:
    return round(float(p[0]), 2), round(float(p[1]), 2)


@register("graph_assembler", "default")
class DefaultGraphAssembler:
    def __init__(self, port_matcher: dict | None = None, confirm_score: float = 0.7, unexpected_scale: float = 0.3,
                 lib: SymbolLibrary | None = None, grammar: EVNCodeGrammar | None = None):
        self.lib = lib or default_library()
        self.grammar = grammar or default_grammar()
        self.port_matcher = PortMatcher(grammar=self.grammar, **(port_matcher or {}))
        self.confirm_score = confirm_score
        self.unexpected_scale = unexpected_scale
        self.context = AssemblyContext(page_id="page")

    def assemble(self, img: NormalizedImage, symbols: list[DetectedSymbol], texts: list[TextItem],
                 labels: list[LabelAssignment], extraction: WireExtraction) -> SubstationTopologyGraph:
        ctx = self.context
        w = img.median_stroke_px
        issues: list[Issue] = list(ctx.extra_issues)
        sym_by_id = {s.id: s for s in symbols}
        text_by_id = {t.id: t for t in texts}

        # ---------------- mã thiết bị
        dev_label: dict[str, LabelAssignment] = {}
        for la in labels:
            if la.target_kind == "device" and text_by_id[la.text_id].parsed is not None \
                    and text_by_id[la.text_id].parsed.role == "device_code":
                dev_label[la.target_id] = la
        codes = {sid: text_by_id[la.text_id].normalized for sid, la in dev_label.items()}

        # ---------------- ghép cổng
        pm = self.port_matcher.match(img, symbols, extraction, codes)

        # ---------------- thiết bị
        dev_id = {}
        used_ids: Counter = Counter()
        for s in symbols:
            if s.id.startswith("dev-"):         # ký hiệu cho sẵn (dựng đáp án) giữ id của nó
                dev_id[s.id] = s.id
                continue
            code = codes.get(s.id)
            base = f"dev-{code}" if code else f"dev-{s.detector_class}"
            used_ids[base] += 1
            dev_id[s.id] = base if used_ids[base] == 1 and code else f"{base}-{used_ids[base]}"
        devices: list[Device] = []
        s_exp = img.px_per_mm * self.lib.library_unit_mm
        for s in symbols:
            sym = self.lib[s.detector_class]
            placement: Placement = self.lib.placement_from_bbox(s.detector_class, s.bbox, s.rotation_deg or 0,
                                                                s.mirror_x)
            la = dev_label.get(s.id)
            code = None
            if la is not None:
                p = text_by_id[la.text_id].parsed
                code = DeviceCode(normalized=p.normalized, voltage_kv=p.voltage_kv, bay_code=p.bay_code,
                                  suffix=p.suffix)
            pending = s.score < self.confirm_score or (la is not None and la.pending)
            devices.append(Device(
                id=dev_id[s.id], detector_class=s.detector_class, type=sym.type,
                code=code, label_text_ids=[la.text_id] if la else [],
                bbox=tuple(round(v, 2) for v in s.bbox),
                transform=Transform(x=round(placement.x, 2), y=round(placement.y, 2), rotation_deg=s.rotation_deg or 0,
                                    mirror_x=s.mirror_x, scale=round(placement.scale, 4)),
                ports=[DevicePort(id=p.port_id, role=p.role, x=round(p.xy[0], 2), y=round(p.xy[1], 2))
                       for p in s.ports],
                drawing_state=sym.drawing_state, operational_state=sym.drawing_state,
                confidence=round(s.score, 3), review_status="pending" if pending else "confirmed"))
            if s.orientation_score is not None and s.orientation_score < 0.05:
                issues.append(Issue(id=f"issue-orient-{dev_id[s.id]}", type="ambiguous_orientation", severity="warning",
                                    position=_round_pt(_center(s.bbox)), object_refs=[DeviceRef(id=dev_id[s.id])],
                                    message=f"Hướng của {dev_id[s.id]} chưa chắc chắn (chênh NCC {s.orientation_score:.3f})"))
            if abs(placement.scale / s_exp - 1) > self.unexpected_scale:
                issues.append(Issue(id=f"issue-scale-{dev_id[s.id]}", type="unexpected_symbol_scale", severity="info",
                                    position=_round_pt(_center(s.bbox)), object_refs=[DeviceRef(id=dev_id[s.id])],
                                    message=f"Tỉ lệ ký hiệu {placement.scale / s_exp:.2f} × tỉ lệ dự kiến"))

        # ---------------- thanh cái
        bus_label: dict[str, list[str]] = {}
        for la in labels:
            if la.target_kind == "busbar":
                bus_label.setdefault(la.target_id, []).append(la.text_id)
        busbars: list[Busbar] = []
        bus_id: dict[str, str] = {}
        for seg in (s for s in extraction.segments if s.is_busbar):
            tids = sorted(bus_label.get(seg.id, []), key=lambda t: -text_by_id[t].score)
            parsed = text_by_id[tids[0]].parsed if tids else None
            code = parsed.normalized if parsed else None
            bid = f"bus-{code}" if code and f"bus-{code}" not in bus_id.values() else f"bus-{len(busbars) + 1:02d}"
            bus_id[seg.id] = bid
            busbars.append(Busbar(id=bid, code=code, voltage_kv=parsed.voltage_kv if parsed else None,
                                  label_text_ids=tids[:1], polyline=[_round_pt(p) for p in seg.points],
                                  stroke_width_px=round(seg.stroke_px, 2), confidence=1.0 if code else 0.7,
                                  review_status="confirmed" if code else "pending"))

        # ---------------- nút và dây
        nodes: list[Node] = []
        node_id: dict[str, str] = {}
        for cn in extraction.nodes:
            if cn.node_type not in ("junction", "crossing_no_connect"):
                continue
            nid = f"node-{len(nodes) + 1:04d}"
            node_id[cn.id] = nid
            style = cn.style or ("t_plain" if cn.node_type == "junction" else "plain_cross")
            nodes.append(Node(id=nid, type=cn.node_type, style=style, position=_round_pt(cn.position),
                              connections=[], confidence=round(cn.confidence or 0.5, 3),
                              review_status="confirmed" if (cn.confidence or 0) >= 0.6 else "pending"))
        node_by_id = {n.id: n for n in nodes}
        wire_id: dict[str, str] = {}
        wires: list[Wire] = []
        seg_nodes: dict[str, list[tuple[str, tuple[float, float]]]] = {}
        for cn in extraction.nodes:
            if cn.id not in node_id:
                continue
            for sid in cn.incident_segment_ids:
                seg_nodes.setdefault(sid, []).append((node_id[cn.id], cn.position))
        for seg in (s for s in extraction.segments if not s.is_busbar):
            wid = f"wire-{len(wires) + 1:04d}"
            wire_id[seg.id] = wid
            pts = [list(p) for p in seg.points]
            ends = []
            statuses = []
            for idx in (0, -1):
                link = pm.links.get((seg.id, idx))
                p = tuple(pts[idx])
                if link is None:
                    # đầu mút tại nút (junction) của WireExtractor
                    near = [(math.dist(p, pos), nid) for nid, pos in seg_nodes.get(seg.id, [])]
                    near = [x for x in near if x[0] < 1.0]
                    if near and node_by_id[min(near)[1]].type == "junction":
                        ends.append(NodeRef(id=min(near)[1]))
                    else:
                        ends.append(DanglingRef(position=_round_pt(p)))
                        statuses.append("dangling")
                    continue
                if link.kind == "port":
                    pts[idx] = list(link.point)
                    ends.append(PortRef(device_id=dev_id[link.device_id], port_id=link.port_id))
                    statuses.append(link.status)
                    if link.status == "candidate":
                        issues.append(Issue(
                            id=f"issue-port-{wid}-{0 if idx == 0 else 1}", type="ambiguous_port_connection",
                            severity="warning", position=_round_pt(link.point),
                            object_refs=[PortRef(device_id=dev_id[link.device_id], port_id=link.port_id),
                                         WireRef(id=wid)],
                            message=f"Đầu dây {wid} ghép cổng {link.port_id} của {dev_id[link.device_id]} "
                                    f"với chi phí {link.cost if link.cost is not None else float('nan'):.2f}; cần xác nhận",
                            resolved_by=link.resolved_by))
                elif link.kind == "busbar":
                    pts[idx] = list(link.point)
                    nid = f"node-{len(nodes) + 1:04d}"
                    node = Node(id=nid, type="junction", style="t_on_busbar", position=_round_pt(link.point),
                                connections=[BusbarRef(id=bus_id[link.busbar_id])], confidence=0.9,
                                review_status="confirmed")
                    nodes.append(node)
                    node_by_id[nid] = node
                    ends.append(NodeRef(id=nid))
                else:
                    ends.append(DanglingRef(position=_round_pt(p)))
                    statuses.append("dangling")
                    issues.append(Issue(id=f"issue-dangling-{wid}-{0 if idx == 0 else 1}", type="dangling_wire_end",
                                        severity="warning", position=_round_pt(p), object_refs=[WireRef(id=wid)],
                                        message=f"Đầu dây {wid} không nối vào cổng, nút hay thanh cái nào"))
            real = [s for s in statuses if s != "dangling"]
            status = "candidate" if "candidate" in real else ("dangling" if statuses and not real
                                                              and all(isinstance(e, DanglingRef) for e in ends)
                                                              else "confirmed")
            wires.append(Wire(id=wid, points=[_round_pt(p) for p in pts], stroke_width_px=round(seg.stroke_px, 2),
                              from_=ends[0], to=ends[1], connection_status=status,
                              confidence=round(seg.confidence, 3)))
            for ref in ends:
                if isinstance(ref, NodeRef):
                    node_by_id[ref.id].connections.append(WireRef(id=wid))
        for n in nodes:
            # crossing: dây đi xuyên qua nút (không kết thúc tại nút)
            if n.type == "crossing_no_connect":
                cn = next(c for c in extraction.nodes if node_id.get(c.id) == n.id)
                n.connections = [WireRef(id=wire_id[s]) for s in dict.fromkeys(cn.incident_segment_ids)
                                 if s in wire_id]
            uniq = {c.key(): c for c in n.connections}
            n.connections = list(uniq.values())
        nodes = [n for n in nodes if n.connections]
        valid_nodes = {n.id for n in nodes}
        for wobj in wires:
            for attr in ("from_", "to"):
                ref = getattr(wobj, attr)
                if isinstance(ref, NodeRef) and ref.id not in valid_nodes:
                    setattr(wobj, attr, DanglingRef(position=wobj.points[0 if attr == "from_" else -1]))

        for sid, pid in pm.unconnected_ports:
            issues.append(Issue(id=f"issue-unconnected-{dev_id[sid]}-{pid}", type="unconnected_port", severity="warning",
                                position=_round_pt(next(p.xy for p in sym_by_id[sid].ports if p.port_id == pid)),
                                object_refs=[PortRef(device_id=dev_id[sid], port_id=pid)],
                                message=f"Cổng {pid} của {dev_id[sid]} không có dây nối"))

        # ---------------- chữ
        text_objs = self._texts(texts, labels, symbols, dev_id, bus_id, ctx.area)
        devices_by_id = {d.id: d for d in devices}
        # chữ thông số / tên lộ không vào labelTextIds (chỉ mã thiết bị), nhưng vẫn assignedTo

        # ---------------- đồ thị + ngăn + net
        page = Page(id=ctx.page_id, source_file=ctx.source_file, width=int(img.pixels.shape[1]),
                    height=int(img.pixels.shape[0]), px_per_mm=round(img.px_per_mm, 6),
                    px_per_mm_source=img.px_per_mm_source, normalization_scale=round(img.scale_from_original, 6))
        graph = SubstationTopologyGraph(
            provenance=Provenance(source="pipeline", pipeline_version=PIPELINE_VERSION, config=ctx.config_path,
                                  created_at=datetime.now().astimezone().isoformat(timespec="seconds")),
            page=page, busbars=busbars, texts=text_objs, devices=devices, wires=wires, nodes=nodes)
        built = NetBuilder(self.lib).build(graph)
        graph.nets = built.nets
        graph.bays = self._bays(graph, devices_by_id)
        graph.issues = EVNRuleValidator(self.lib, self.grammar).validate(graph, issues + built.issues,
                                                                        end_tolerance_px=2 * w)
        return graph

    # ------------------------------------------------------------------ chữ

    def _texts(self, texts, labels, symbols, dev_id, bus_id, area: DrawingArea | None) -> list[Text]:
        by_text: dict[str, LabelAssignment] = {}
        for la in labels:
            by_text.setdefault(la.text_id, la)
        arrows = [s for s in symbols if self.lib[s.detector_class].type == LINE_TERMINAL_TYPE]
        r = 2 * text_height(texts) if texts else 0
        out = []
        for t in texts:
            la = by_text.get(t.id)
            cx, cy = _center(t.bbox)
            in_title = area is not None and (not area.contains(cx, cy) or (
                area.title_block is not None and area.title_block[1] <= cy))
            role = t.parsed.role if t.parsed is not None else "other"
            assigned = None
            if in_title:
                role = "title_block"
            elif la is not None and la.target_kind == "device":
                assigned = DeviceRef(id=dev_id[la.target_id])
            elif la is not None and la.target_kind == "busbar":
                assigned = BusbarRef(id=bus_id[la.target_id])
            elif la is not None and la.target_kind == "line_terminal":
                role = "line_name"
                assigned = DeviceRef(id=dev_id[la.target_id])
            if not in_title and role == "device_code" and assigned is None and arrows:
                # số cột 3 chữ số trùng dạng mã máy cắt (106 trên E18.9) nằm cạnh mũi tên đầu đường dây
                if min(box_gap(t.bbox, a.bbox) for a in arrows) <= r:
                    role = "pole_number"
            out.append(Text(id=t.id, raw=t.raw, normalized=t.normalized, bbox=tuple(round(v, 2) for v in t.bbox),
                            angle_deg=t.angle_deg if t.angle_deg in (0, 90, 180, 270) else 0,
                            confidence=round(t.score, 3), role=role, assigned_to=assigned))
        return out

    # ------------------------------------------------------------------ ngăn

    def _bays(self, graph: SubstationTopologyGraph, devices_by_id: dict[str, Device]) -> list[Bay]:
        bays: dict[str, Bay] = {}
        for d in graph.devices:
            if d.code is None or not d.code.bay_code:
                continue
            bc = d.code.bay_code
            if bc not in bays:
                parsed = self.grammar.parse(bc)
                kind = "unknown"
                if parsed is not None:
                    kind = BAY_KIND_BY_POSITION.get(parsed.details.get("position", ""),
                                                    BAY_KIND_BY_PATTERN.get(parsed.pattern_id, "unknown"))
                bays[bc] = Bay(id=f"bay-{bc}", code=bc, voltage_kv=parsed.voltage_kv if parsed else None, kind=kind)
            bays[bc].device_ids.append(d.id)
            d.bay_id = bays[bc].id
        # thiết bị không mã: theo ngăn của thiết bị có mã chung Net (một rồi hai bước)
        index = graph.net_index()
        net_devs: dict[str, set[str]] = {}
        for d in graph.devices:
            for p in d.ports:
                net_devs.setdefault(index[("port", d.id, p.id)], set()).add(d.id)
        bus_nets = {index[("busbar", b.id)] for b in graph.busbars}
        for d in graph.devices:
            if d.bay_id is not None:
                continue
            frontier, seen = {d.id}, {d.id}
            for _ in range(2):
                votes = Counter()
                nxt = set()
                for did in frontier:
                    for p in devices_by_id[did].ports:
                        net = index[("port", did, p.id)]
                        if net in bus_nets:
                            continue
                        for o in net_devs.get(net, ()):
                            if o in seen:
                                continue
                            nxt.add(o)
                            if devices_by_id[o].bay_id:
                                votes[devices_by_id[o].bay_id] += 1
                if votes:
                    bid = votes.most_common(1)[0][0]
                    d.bay_id = bid
                    next(b for b in bays.values() if b.id == bid).device_ids.append(d.id)
                    d.review_status = "pending"
                    break
                seen |= nxt
                frontier = nxt
        return list(bays.values())


def _center(b) -> tuple[float, float]:
    return b[0] + b[2] / 2, b[1] + b[3] / 2
