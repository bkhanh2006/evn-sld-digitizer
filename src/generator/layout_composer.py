"""LayoutComposer (SDD 8.1.3): đặt thanh cái, ngăn lộ, MBA, nhãn; đi dây liên cấp; tính nets.

Bố cục làm trong hệ trang đơn vị mm (Y xuống), cuối cùng nhân pxPerMm để ra đồ thị px. Mỗi ngăn
được biến đổi từ hệ chuẩn bằng (gốc, xoay φ, lật) rồi chuẩn hóa tư thế ký hiệu về góc cho phép
(ký hiệu đối xứng điện chỉ dùng 0/90°: xoay 180° = hoán đổi hai cổng).

Preset (configs/layout_presets.yaml):
- hv: thanh cái ngang ở dải trên; đường dây đi lên, ngăn MBA/TU đi xuống.
- bottom: thanh cái ngang dưới MBA, xuất tuyến đi xuống.
- side: thanh cái dọc ở mép phải, xuất tuyến sang phải.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from src.core.evn_codes import EVNCodeGrammar
from src.core.geometry import Placement, rotate_mirror
from src.core.net_builder import NetBuilder
from src.core.schema import (Bay, BusbarRef, Busbar, Device, DevicePort, DeviceRef, Node, NodeRef, Page, PortRef,
                             Provenance, SubstationTopologyGraph, Text, Wire, WireRef)
from src.core.symbol_lib import SymbolDef, SymbolLibrary
from src.core.text_metrics import find_font, text_bbox
from src.exporters.svg_exporter import FontSpec
from src.generator import bay_templates as bt
from src.generator.router import OrthogonalRouter
from src.generator.topology_sampler import BaySpec, LevelSpec, TopologySpec

Vec = tuple[float, float]
Rect = tuple[float, float, float, float]          # x0, y0, x1, y1
GENERATOR_VERSION = "0.2.0"


class LayoutFailed(Exception):
    """Bố cục không hợp lệ (va chạm, tràn trang, không đi được dây); thử lại với tham số khác."""


@dataclass
class RenderStyle:
    font: FontSpec
    bold: FontSpec
    size_mm: dict[str, float]
    wire_mm: float
    busbar_mm: float
    symbol_stroke_scale: float
    hop_arc_p: float
    label_gap_mm: float
    bay_margin_mm: float
    px_per_mm: float
    bay_style: bt.BayStyle


@dataclass
class Layout:
    graph: SubstationTopologyGraph               # đơn vị px
    style: RenderStyle
    text_fonts: dict[str, FontSpec]
    page_name: str
    frame_px: Rect
    title_block_px: Rect
    title_cells_px: list[Rect]
    signature_cells_px: list[Rect]
    topology: TopologySpec


def canonical_pose(sym: SymbolDef, rotation: int, mirror: bool) -> tuple[int, bool]:
    """Đưa (θ, lật) về tư thế cho phép của lớp: R(θ)M = R(θ−180)·M(¬m)·lật Y (lật Y = đổi hai cổng)."""
    if rotation not in sym.allowed_rotations_deg:
        rotation, mirror = (rotation + 180) % 360, not mirror
        if rotation not in sym.allowed_rotations_deg:
            raise ValueError(f"{sym.detector_class}: không đưa được góc {rotation} về góc cho phép")
    if mirror and not sym.allow_mirror:
        if any(abs(p.local_x) > 1e-9 for p in sym.ports):
            raise ValueError(f"{sym.detector_class}: bỏ lật làm lệch cổng")
        mirror = False
    return rotation, mirror


def _union(rects: list[Rect]) -> Rect:
    return (min(r[0] for r in rects), min(r[1] for r in rects), max(r[2] for r in rects), max(r[3] for r in rects))


def _xyxy(b) -> Rect:
    x, y, w, h = b
    return (x, y, x + w, y + h)


def overlaps(a: Rect, b: Rect, tol: float = 1e-6) -> bool:
    return min(a[2], b[2]) - max(a[0], b[0]) > tol and min(a[3], b[3]) - max(a[1], b[1]) > tol


def find_overlaps(boxes: list[tuple[str, Rect]]) -> list[tuple[str, str]]:
    """Các cặp hộp chồng nhau (diện tích giao > 0), quét theo trục x."""
    order = sorted(boxes, key=lambda b: b[1][0])
    out = []
    active: list[tuple[str, Rect]] = []
    for name, r in order:
        active = [a for a in active if a[1][2] > r[0]]
        for other, ro in active:
            if overlaps(r, ro):
                out.append((other, name))
        active.append((name, r))
    return out


# ---------------------------------------------------------------------------- ngăn đã đặt

@dataclass
class RealBay:
    bay: Bay
    devices: list[Device]
    wires: list[Wire]
    nodes: list[Node]
    texts: list[Text]
    text_fonts: dict[str, FontSpec]
    attach: list[str]                         # id nút nối thanh cái
    external: dict[str, tuple[PortRef, Vec]]  # cổng ngoài + hướng ra (hệ trang)

    def boxes(self) -> list[tuple[str, Rect]]:
        return [(d.id, _xyxy(d.bbox)) for d in self.devices] + [(t.id, _xyxy(t.bbox)) for t in self.texts]

    def footprint(self) -> Rect:
        rects = [r for _, r in self.boxes()]
        rects += [(x, y, x, y) for w in self.wires for x, y in w.points]
        return _union(rects)

    def translate(self, dx: float, dy: float) -> "RealBay":
        def mv(p):
            return (p[0] + dx, p[1] + dy)
        devs = []
        for d in self.devices:
            t = d.transform.model_copy(update={"x": d.transform.x + dx, "y": d.transform.y + dy})
            ports = [p.model_copy(update={"x": p.x + dx, "y": p.y + dy}) for p in d.ports]
            x, y, w, h = d.bbox
            devs.append(d.model_copy(update={"transform": t, "ports": ports, "bbox": (x + dx, y + dy, w, h)}))
        self.devices = devs
        self.wires = [w.model_copy(update={"points": [mv(p) for p in w.points]}) for w in self.wires]
        self.nodes = [n.model_copy(update={"position": mv(n.position)}) for n in self.nodes]
        self.texts = [t.model_copy(update={"bbox": (t.bbox[0] + dx, t.bbox[1] + dy, t.bbox[2], t.bbox[3])})
                      for t in self.texts]
        return self

    def node(self, nid: str) -> Node:
        return next(n for n in self.nodes if n.id == nid)

    def device(self, did: str) -> Device:
        return next(d for d in self.devices if d.id == did)


class LayoutComposer:
    def __init__(self, lib: SymbolLibrary, grammar: EVNCodeGrammar, gen_cfg: dict, presets_cfg: dict):
        self.lib, self.grammar, self.cfg, self.presets = lib, grammar, gen_cfg, presets_cfg
        self._text_n = 0

    # ------------------------------------------------------------------ kiểu vẽ

    def sample_style(self, rng: random.Random, page_name: str) -> RenderStyle:
        st = self.cfg["style"]
        fonts = st["fonts"]
        f = rng.choices(fonts, weights=[x["weight"] for x in fonts])[0]
        u = lambda r: rng.uniform(*r)  # noqa: E731
        bay_style = bt.BayStyle(gap=u(st["gap_mm"]), tap_stub=u(st["tap_stub_mm"]),
                                junction_style="dot" if rng.random() < st["junction_dot_p"] else "t_plain",
                                switch_closed_p=u(st["switch_closed_p"]), earthing_closed_p=u(st["earthing_closed_p"]))
        return RenderStyle(
            font=FontSpec(f["family"], f["file"]), bold=FontSpec(f["family"], f["bold"], "bold"),
            size_mm={k: u(v) for k, v in st["size_mm"].items()}, wire_mm=u(st["wire_mm"]),
            busbar_mm=u(st["busbar_mm"]), symbol_stroke_scale=u(st["symbol_stroke_scale"]),
            hop_arc_p=st["hop_arc_p"], label_gap_mm=u(st["label_gap_mm"]), bay_margin_mm=u(st["bay_margin_mm"]),
            px_per_mm=u(self.cfg["render"]["px_per_mm"][page_name]), bay_style=bay_style)

    # ------------------------------------------------------------------ dựng ngăn

    def build_bay(self, spec: BaySpec, style: RenderStyle, rng: random.Random) -> bt.BaySubGraph:
        a = dict(spec.args)
        lib, g, s = self.lib, self.grammar, style.bay_style
        if spec.kind == "line":
            return bt.line_bay(lib, g, style=s, rng=rng, **a)
        if spec.kind == "mv_feeder":
            return bt.mv_feeder_bay(lib, g, style=s, rng=rng, **a)
        if spec.kind == "tx_hv":
            return bt.transformer_bay(lib, g, style=s, rng=rng, **a)
        if spec.kind == "incomer":
            a.pop("tx_side")
            return bt.incomer_bay(lib, g, style=s, rng=rng, **a)
        if spec.kind == "coupler":
            return bt.bus_coupler_bay(lib, g, style=s, rng=rng, **a)
        if spec.kind == "bus_vt":
            return bt.bus_vt_bay(lib, g, style=s, rng=rng, **a)
        if spec.kind == "aux":
            return bt.aux_or_capacitor_bay(lib, g, style=s, rng=rng, **a)
        raise ValueError(f"Loại ngăn lạ: {spec.kind}")

    def _new_text_id(self) -> str:
        self._text_n += 1
        return f"txt-{self._text_n:04d}"

    def make_text(self, text: str, role: str, font: FontSpec, size: float, box_xy: Vec | None = None,
                  anchor: Vec | None = None, angle: int = 0, assigned=None) -> tuple[Text, FontSpec]:
        """Text có bbox đo bằng font thật; neo theo góc trên-trái bbox (box_xy) hoặc điểm neo chữ."""
        path = str(find_font(font.file))
        x0, y0, w, h = text_bbox(text, path, size, (0.0, 0.0), angle)
        if box_xy is not None:
            bbox = (box_xy[0], box_xy[1], w, h)
        else:
            bbox = (anchor[0] + x0, anchor[1] + y0, w, h)
        parsed = self.grammar.parse(text) if role in ("device_code", "busbar_code") else None
        t = Text(id=self._new_text_id(), raw=text, normalized=parsed.normalized if parsed else text, bbox=bbox,
                 angle_deg=angle, confidence=1.0, role=role, assigned_to=assigned)
        return t, font

    def realize(self, sub: bt.BaySubGraph, origin: Vec, rot: int, mirror: bool, style: RenderStyle) -> RealBay:
        """Biến đổi ngăn từ hệ chuẩn ra trang: X = gốc + R(φ)·M·x; đặt nhãn đứng thẳng."""
        def tp(p) -> Vec:
            u, v = rotate_mirror(p[0], p[1], rot, mirror)
            return (origin[0] + u, origin[1] + v)

        def tdir(d) -> Vec:
            u, v = rotate_mirror(d[0], d[1], rot, mirror)
            return (round(u), round(v))

        rename: dict[tuple[str, str], str] = {}
        devices = []
        for d in sub.devices:
            sym = self.lib[d.detector_class]
            t = d.transform
            theta = (rot - t.rotation_deg) % 360 if mirror else (rot + t.rotation_deg) % 360
            theta, m = canonical_pose(sym, theta, mirror != t.mirror_x)
            c = tp((t.x, t.y))
            pl = Placement(c[0], c[1], theta, m, t.scale)
            proj = self.lib.project_ports(d.detector_class, pl)
            ports = []
            for old in d.ports:
                want = tp((old.x, old.y))
                match = min(proj, key=lambda q: (q.xy[0] - want[0]) ** 2 + (q.xy[1] - want[1]) ** 2)
                if (match.xy[0] - want[0]) ** 2 + (match.xy[1] - want[1]) ** 2 > 1e-12:
                    raise ValueError(f"{d.id}: cổng {old.id} lệch sau khi chuẩn hóa tư thế")
                rename[(d.id, old.id)] = match.port_id
            ports = [DevicePort(id=q.port_id, role=q.role, x=q.xy[0], y=q.xy[1]) for q in proj]
            devices.append(d.model_copy(update={
                "transform": t.model_copy(update={"x": c[0], "y": c[1], "rotation_deg": theta, "mirror_x": m}),
                "ports": ports, "bbox": self.lib.page_bbox(d.detector_class, pl)}))
        dev_by_id = {d.id: d for d in devices}

        def end(e):
            if isinstance(e, PortRef):
                return PortRef(device_id=e.device_id, port_id=rename[(e.device_id, e.port_id)])
            return e

        wires = []
        for w in sub.wires:
            pts = [tp(p) for p in w.points]
            a, b = end(w.from_), end(w.to)
            for i, e in ((0, a), (-1, b)):
                if isinstance(e, PortRef):
                    p = dev_by_id[e.device_id].port(e.port_id)
                    pts[i] = (p.x, p.y)
            wires.append(w.model_copy(update={"points": pts, "from_": a, "to": b,
                                              "stroke_width_px": style.wire_mm}))
        nodes = [n.model_copy(update={"position": tp(n.position)}, deep=True) for n in sub.nodes]
        external = {}
        for role, ref in sub.external_ports.items():
            r = end(ref)
            sym = self.lib[dev_by_id[r.device_id].detector_class]
            dv = dev_by_id[r.device_id]
            pdef = sym.port(r.port_id)
            dvec = rotate_mirror(*pdef.direction, dv.transform.rotation_deg, dv.transform.mirror_x)
            external[role] = (r, (round(dvec[0]), round(dvec[1])))

        bay_axis = tdir((0, 1))
        # thanh cái đi qua gốc, vuông góc trục ngăn: nhãn không được đè lên (trừ ngăn liên lạc nằm dọc thanh cái)
        bus_line = None
        if sub.bay.kind != "bus_coupler":
            o = tp((0.0, 0.0))
            ax, ay = abs(bay_axis[1]), abs(bay_axis[0])          # hướng thanh cái
            h = style.busbar_mm
            bus_line = (o[0] - 60 * ax - h, o[1] - 60 * ay - h, o[0] + 60 * ax + h, o[1] + 60 * ay + h)
        texts, fonts = self._labels(sub.labels, dev_by_id, wires, tdir, bay_axis, style, bus_line)
        for t in texts:
            if t.assigned_to is not None and t.role == "device_code":
                dev_by_id[t.assigned_to.id].label_text_ids.append(t.id)
        return RealBay(bay=sub.bay.model_copy(deep=True), devices=devices, wires=wires, nodes=nodes, texts=texts,
                       text_fonts=fonts, attach=list(sub.bus_attach_nodes), external=external)

    def _labels(self, labels: list[bt.LabelRequest], devs: dict[str, Device], wires: list[Wire], tdir,
                bay_axis: Vec, style: RenderStyle, bus_line: Rect | None = None) -> tuple[list[Text], dict[str, FontSpec]]:
        """Đặt nhãn theo hướng yêu cầu; nếu chạm ký hiệu, nhãn trước hoặc dây thì thử dời ngang/dọc
        rồi thử phía đối diện. Không tìm được chỗ trống thì giữ vị trí gốc (kiểm tra toàn trang sẽ loại)."""
        groups: dict[tuple, list[bt.LabelRequest]] = {}
        for lr in labels:
            groups.setdefault((lr.anchor, lr.side, lr.along_bay), []).append(lr)
        texts, fonts = [], {}
        gap = style.label_gap_mm
        taken: list[Rect] = [_xyxy(d.bbox) for d in devs.values()] + ([bus_line] if bus_line else [])
        half = style.wire_mm
        for w in wires:
            for (ax, ay), (bx, by) in zip(w.points, w.points[1:]):
                taken.append((min(ax, bx) - half, min(ay, by) - half, max(ax, bx) + half, max(ay, by) + half))

        def block_at(side_p: Vec, dev: Rect, bw: float, bh: float, shift: float) -> Rect:
            x0, y0, x1, y1 = dev
            if side_p[0] > 0:
                bx = x1 + gap
            elif side_p[0] < 0:
                bx = x0 - gap - bw
            else:
                bx = 0.5 * (x0 + x1) - bw / 2 + shift
            if side_p[1] > 0:
                by = y1 + gap
            elif side_p[1] < 0:
                by = y0 - gap - bh
            else:
                by = 0.5 * (y0 + y1) - bh / 2 + (shift if side_p[0] != 0 else 0.0)
            return (bx, by, bx + bw, by + bh)

        for (anchor, side, along), items in groups.items():
            items.sort(key=lambda r: r.stack)
            side_p = tdir(side)
            angle = 270 if along and bay_axis[0] == 0 else 0
            made = []
            for lr in items:
                font = style.bold if lr.bold else style.font
                assigned = DeviceRef(id=anchor) if lr.assign else None
                t, f = self.make_text(lr.text, lr.role, font, style.size_mm[lr.size_key], box_xy=(0.0, 0.0),
                                      angle=angle, assigned=assigned)
                made.append((t, f))
            spacing = 0.25 * max(t.bbox[3] for t, _ in made)
            bw = max(t.bbox[2] for t, _ in made)
            bh = sum(t.bbox[3] for t, _ in made) + spacing * (len(made) - 1)
            dev = _xyxy(devs[anchor].bbox)
            along_len = bw if side_p[0] == 0 else bh
            cands = [(side_p, 0.0)]
            for k in (0.5, 1.0):
                cands += [(side_p, k * along_len), (side_p, -k * along_len)]
            cands.append(((-side_p[0], -side_p[1]), 0.0))
            chosen = None
            for sp, shift in cands:
                r = block_at(sp, dev, bw, bh, shift)
                if not any(overlaps(r, o) for o in taken):
                    chosen = (sp, r)
                    break
            sp, (bx, by, _, _) = chosen or (side_p, block_at(side_p, dev, bw, bh, 0.0))
            cy = by
            for t, f in made:
                if sp[0] > 0 or (sp[0] == 0 and sp[1] == 0):
                    tx = bx
                elif sp[0] < 0:
                    tx = bx + bw - t.bbox[2]
                else:
                    tx = bx + (bw - t.bbox[2]) / 2
                t.bbox = (tx, cy, t.bbox[2], t.bbox[3])
                cy += t.bbox[3] + spacing
                texts.append(t)
                fonts[t.id] = f
                taken.append(_xyxy(t.bbox))
        return texts, fonts

    # ------------------------------------------------------------------ thanh cái và các ngăn trên nó

    def _place_level(self, level: LevelSpec, orient: str, style: RenderStyle, rng: random.Random):
        """Đặt các phân đoạn thanh cái của một cấp tại trục 0 (ngang: y=0, dọc: x=0), chạy từ tọa độ 0.

        Trả về (các RealBay, các Busbar, danh sách nút nối thanh cái theo busbar, chiều dài).
        """
        horizontal = orient == "horizontal"
        # φ cho phía ngoài/phía trong và cho ngăn liên lạc (chạy dọc theo thanh cái)
        if level.role == "hv":
            rot_outer, rot_inner = 180, 0
        elif level.role == "bottom":
            rot_outer, rot_inner = 0, 180
        else:
            rot_outer, rot_inner = 270, 90
        margin = style.bay_margin_mm
        reals: list[RealBay] = []
        busbars: list[Busbar] = []
        attach: list[tuple[str, str]] = []      # (busbar id, node id)
        cursor = 0.0
        bus_w = style.busbar_mm

        def along(r: Rect) -> tuple[float, float]:
            return (r[0], r[2]) if horizontal else (r[1], r[3])

        for si, bus in enumerate(level.buses):
            bus_id = f"bus-{bus.code}"
            if si > 0 and level.coupler is not None:
                sub = self.build_bay(level.coupler, style, rng)
                rot, mir = (270, True) if horizontal else (0, False)
                rb = self.realize(sub, (cursor, 0.0) if horizontal else (0.0, cursor), rot, mir, style)
                reals.append(rb)
                prev_bus = busbars[-1]
                attach.append((prev_bus.id, rb.attach[0]))
                attach.append((bus_id, rb.attach[1]))
                end_node = rb.node(rb.attach[1]).position
                cursor = end_node[0] if horizontal else end_node[1]
            label_t, label_f = self.make_text(bus.code, "busbar_code", style.bold, style.size_mm["bus"],
                                              box_xy=(0.0, 0.0), assigned=BusbarRef(id=bus_id))
            label_len = (label_t.bbox[2] if horizontal else label_t.bbox[3]) + 2 * margin
            start = cursor
            label_at_start = si == 0 and len(level.buses) > 1
            pos_start = start + (label_len if label_at_start else margin * 2)
            ends = []
            for side_list, rot in ((bus.outer, rot_outer), (bus.inner, rot_inner)):
                pos = pos_start
                for spec in side_list:
                    sub = self.build_bay(spec, style, rng)
                    # ngăn MBA không lật: cuộn mv phải quay về phía cấp áp ở mép phải
                    mirror = spec.kind != "tx_hv" and rng.random() < 0.5
                    rb = self.realize(sub, (0.0, 0.0), rot, mirror, style)
                    a0, a1 = along(rb.footprint())
                    shift = pos - a0
                    rb.translate(shift, 0.0) if horizontal else rb.translate(0.0, shift)
                    reals.append(rb)
                    for nid in rb.attach:
                        attach.append((bus_id, nid))
                    pos = shift + a1 + margin
                ends.append(pos)
            stop = max(ends) + (margin if label_at_start else label_len)
            pts = [(start, 0.0), (stop, 0.0)] if horizontal else [(0.0, start), (0.0, stop)]
            bus_obj = Busbar(id=bus_id, code=bus.code, voltage_kv=level.kv, label_text_ids=[label_t.id],
                             polyline=pts, stroke_width_px=bus_w, confidence=1.0, review_status="confirmed")
            # nhãn thanh cái: trong khoảng trống ở đầu/cuối, phía trên (ngang) hoặc bên trái (dọc)
            lw, lh = label_t.bbox[2], label_t.bbox[3]
            gap = style.label_gap_mm + bus_w
            p0 = start + margin if label_at_start else stop - margin - (lw if horizontal else lh)
            label_t.bbox = (p0, -gap - lh, lw, lh) if horizontal else (-gap - lw, p0, lw, lh)
            busbars.append(bus_obj)
            reals.append(RealBay(bay=None, devices=[], wires=[], nodes=[], texts=[label_t],  # type: ignore[arg-type]
                                 text_fonts={label_t.id: label_f}, attach=[], external={}))
            cursor = stop
        return reals, busbars, attach, cursor

    # ------------------------------------------------------------------ bố cục toàn trang

    def compose(self, topo: TopologySpec, rng: random.Random) -> Layout:
        preset = self.presets["presets"][topo.preset]
        last: Exception | None = None
        for page_name in preset["pages"]:
            size_fails = 0
            for _ in range(self.cfg["compose"]["max_attempts"]):
                style = self.sample_style(rng, page_name)
                try:
                    return self._compose_once(topo, rng, style, page_name)
                except LayoutFailed as e:
                    last = e
                    size_fails += str(e).startswith("size:")
                    if size_fails >= 3:           # trang quá nhỏ cho topology này: sang khổ lớn hơn
                        break
        raise LayoutFailed(f"Không bố cục được {topo.topology_id}: {last}")

    def _compose_once(self, topo: TopologySpec, rng: random.Random, style: RenderStyle, page_name: str) -> Layout:
        self._text_n = 0
        W, H = self.presets["pages"][page_name]
        fr = self.presets["frame"]
        ml, mt, mr, mb = fr["margin_mm"]
        frame = (ml, mt, W - mr, H - mb)
        tb_h = rng.uniform(*fr["title_block_height_mm"])
        title_block = (frame[0], frame[3] - tb_h, frame[2], frame[3])
        pad = fr["inner_pad_mm"]
        area = (frame[0] + pad, frame[1] + pad + fr["title_height_mm"], frame[2] - pad, title_block[1] - pad)
        preset = self.presets["presets"][topo.preset]

        reals: list[RealBay] = []
        busbars: list[Busbar] = []
        attach: list[tuple[str, str]] = []

        def place(level: LevelSpec, orient: str, dx: float, dy: float):
            return self._place_level(level, orient, style, rng)

        def parts_of(rs: list[RealBay], bs: list[Busbar]) -> list[Rect]:
            return [r.footprint() for r in rs if r.devices or r.texts] + [_bus_rect(b) for b in bs]

        def commit(rs, bs, at, dx, dy):
            for r in rs:
                r.translate(dx, dy)
            bs = [b.model_copy(update={"polyline": [(x + dx, y + dy) for x, y in b.polyline]}) for b in bs]
            reals.extend(rs)
            busbars.extend(bs)
            attach.extend(at)
            occupied.extend(_inflate(r, half_gap) for r in parts_of(rs, bs))

        rc = self.cfg["router"]
        route_gap = 3 * rc["cell_mm"] + 2 * rc["clearance_mm"]
        half_gap = route_gap / 2
        occupied: list[Rect] = []

        def fit(parts: list[Rect], xs: list[float], y_lo: float, step: float = 1.5) -> tuple[float, float] | None:
            """Dịch nhỏ nhất theo y (với từng x thử) để các phần không chạm vùng đã chiếm."""
            ext = _union(parts)
            y_lo = max(y_lo, area[1] - ext[1])
            best = None
            for x in xs:
                y = y_lo
                while y + ext[3] <= area[3]:
                    moved = [_inflate((a + x, b + y, c + x, d + y), half_gap) for a, b, c, d in parts]
                    if not any(overlaps(m, o) for m in moved for o in occupied):
                        if best is None or y < best[1]:
                            best = (x, y)
                        break
                    y += step
            return best

        # --- hv: căn trên cùng vùng vẽ
        hv = topo.level("hv")
        rs, bs, at, _ = place(hv, "horizontal", 0.0, 0.0)
        ext = _union(parts_of(rs, bs))
        slack = (area[2] - area[0]) - (ext[2] - ext[0])
        if slack < 0:
            raise LayoutFailed(f"size: cấp 110 kV rộng {ext[2] - ext[0]:.0f} mm, vùng vẽ {area[2] - area[0]:.0f} mm")
        hv_bus_y = area[1] - ext[1]
        commit(rs, bs, at, area[0] - ext[0] + rng.uniform(0, slack * 0.3), hv_bus_y)

        # --- side: thanh cái dọc mép phải, hạ dần tới khi không chạm cấp 110 kV
        side = topo.level("side")
        side_left = area[2]
        if side is not None:
            rs, bs, at, _ = place(side, "vertical", 0.0, 0.0)
            parts = parts_of(rs, bs)
            ext = _union(parts)
            pos = fit(parts, [area[2] - ext[2]], hv_bus_y + rng.uniform(*preset["side_bus_drop_mm"]))
            if pos is None:                       # không vừa ở độ hạ ưa thích: thử từ đầu vùng vẽ
                pos = fit(parts, [area[2] - ext[2]], area[1])
            if pos is None:
                raise LayoutFailed("size: không đặt được thanh cái dọc")
            commit(rs, bs, at, *pos)
            side_left = ext[0] + pos[0]

        # --- bottom: thanh cái ngang, thử vài vị trí ngang, chọn vị trí cao nhất không va chạm
        bottom = topo.level("bottom")
        rs, bs, at, _ = place(bottom, "horizontal", 0.0, 0.0)
        parts = parts_of(rs, bs)
        ext = _union(parts)
        right_limit = side_left - route_gap if side is not None else area[2]
        lo, hi = area[0] - ext[0], right_limit - ext[2]
        if any(s.kind == "incomer" for bus in bottom.buses for s in bus.outer):
            lo += 2 * route_gap                    # chừa lối cho dây MBA vòng qua đầu thanh cái
        if hi < lo:
            raise LayoutFailed(f"size: cấp dưới rộng {ext[2] - ext[0]:.0f} mm, còn {right_limit - area[0]:.0f} mm")
        xs = [lo + (hi - lo) * k / 5 for k in range(6)]
        rng.shuffle(xs)
        pos = fit(parts, xs, hv_bus_y + 5.0)
        if pos is None:
            raise LayoutFailed("size: cấp dưới không vừa chiều cao vùng vẽ")
        commit(rs, bs, at, *pos)

        # --- gom đồ thị (mm)
        devices = [d for r in reals for d in r.devices]
        wires = [w for r in reals for w in r.wires]
        nodes = {n.id: n for r in reals for n in r.nodes}
        texts = [t for r in reals for t in r.texts]
        text_fonts = {k: v for r in reals for k, v in r.text_fonts.items()}
        bays = [r.bay for r in reals if r.bay is not None]
        bus_by_id = {b.id: b for b in busbars}
        for bus_id, nid in attach:
            n = nodes[nid]
            b = bus_by_id[bus_id]
            # điểm nối phải nằm trên thanh cái
            (x0, y0), (x1, y1) = b.polyline[0], b.polyline[-1]
            px, py = n.position
            if not (min(x0, x1) - 1e-6 <= px <= max(x0, x1) + 1e-6 and min(y0, y1) - 1e-6 <= py <= max(y0, y1) + 1e-6):
                raise LayoutFailed(f"nút {nid} không nằm trên {bus_id}")
            n.connections.append(BusbarRef(id=bus_id))

        # --- đi dây MBA ↔ ngăn tổng
        ext_ports = {}
        for r in reals:
            if r.bay is None:
                continue
            for role, val in r.external.items():
                ext_ports[(r.bay.code, role)] = val
        rcfg = self.cfg["router"]
        router = OrthogonalRouter(area, rcfg["cell_mm"], rcfg["clearance_mm"], rcfg["bend_cost"], rcfg["cross_cost"])
        for d in devices:
            router.add_obstacle(_xyxy(d.bbox))
        for t in texts:
            router.add_obstacle(_xyxy(t.bbox))
        for b in busbars:
            (x0, y0), (x1, y1) = b.polyline[0], b.polyline[-1]
            router.add_obstacle((x0, y0, x1, y1), pad=rcfg["clearance_mm"] + b.stroke_width_px)
        for w in wires:
            router.add_wire(w.id, list(w.points))
        crossings_n = 0
        for tx in topo.transformers:
            hv_code = self.grammar.breaker(hv.kv, "transformer", tx["n"])
            for role, (lvl_role, _) in tx["sides"].items():
                lvl = topo.level(lvl_role)
                inc_code = self.grammar.breaker(lvl.kv, "transformer", tx["n"])
                (ra, da), (rb_, db) = ext_ports[(hv_code, role)], ext_ports[(inc_code, "to_transformer")]
                pa = next(d for d in devices if d.id == ra.device_id).port(ra.port_id)
                pb = next(d for d in devices if d.id == rb_.device_id).port(rb_.port_id)
                path = router.route((pa.x, pa.y), da, (pb.x, pb.y), db)
                if path is None:
                    raise LayoutFailed(f"không đi được dây {hv_code}.{role} → {inc_code}")
                wid = f"w-T{tx['n']}-{role}"
                for c in router.crossings(path):
                    crossings_n += 1
                    nid = f"x-{crossings_n:03d}"
                    style_x = "hop_arc" if rng.random() < style.hop_arc_p else "plain_cross"
                    nodes[nid] = Node(id=nid, type="crossing_no_connect", style=style_x, position=c.position,
                                      connections=[WireRef(id=wid), WireRef(id=c.wire_id)], review_status="confirmed")
                wires.append(Wire(id=wid, points=path, stroke_width_px=style.wire_mm, from_=ra, to=rb_,
                                  connection_status="confirmed"))
                router.add_wire(wid, path)

        # --- tiêu đề và khung tên
        title_texts, title_cells, sig_cells = self._title_block(topo, style, frame, title_block, area, rng)
        for t, f in title_texts:
            texts.append(t)
            text_fonts[t.id] = f

        # --- kiểm tra va chạm và biên trang (tiêu chí 4–5)
        boxes = [(d.id, _xyxy(d.bbox)) for d in devices] + [(t.id, _xyxy(t.bbox)) for t in texts]
        boxes += [(b.id, _bus_rect(b)) for b in busbars]
        hits = find_overlaps(boxes)
        if hits:
            raise LayoutFailed(f"{len(hits)} cặp chồng nhau, ví dụ {hits[0]}")
        for d in devices:
            r = _xyxy(d.bbox)
            if r[0] < area[0] or r[1] < area[1] - fr["title_height_mm"] or r[2] > area[2] or r[3] > area[3]:
                raise LayoutFailed(f"{d.id} ra ngoài vùng vẽ")

        page = Page(id="pending", width=1, height=1, px_per_mm=1.0, px_per_mm_source="synthetic")
        graph = SubstationTopologyGraph(
            provenance=Provenance(source="synthetic_gt", generator_version=GENERATOR_VERSION),
            page=page, bays=bays, busbars=busbars, texts=texts, devices=devices, wires=wires,
            nodes=list(nodes.values()), nets=[], issues=[])
        graph = scale_graph(graph, style.px_per_mm, W, H)
        graph.nets = NetBuilder(self.lib).build(graph).nets
        ppm = graph.page.px_per_mm

        def px(r: Rect) -> Rect:
            return tuple(v * ppm for v in r)  # type: ignore[return-value]
        return Layout(graph=graph, style=style, text_fonts=text_fonts, page_name=page_name, frame_px=px(frame),
                      title_block_px=px(title_block), title_cells_px=[px(c) for c in title_cells],
                      signature_cells_px=[px(c) for c in sig_cells], topology=topo)

    # ------------------------------------------------------------------ khung tên

    def _title_block(self, topo: TopologySpec, style: RenderStyle, frame: Rect, tb: Rect, area: Rect,
                     rng: random.Random):
        """Tên sơ đồ ở đầu trang + khung tên dưới cùng (chữ có role title_block, không gán thiết bị)."""
        out = []
        name = topo.station_name.upper()
        title = f"SƠ ĐỒ NHẤT THỨ TRẠM 110KV {name}"
        t, f = self.make_text(title, "title_block", style.bold, style.size_mm["title"], box_xy=(0, 0))
        t.bbox = ((frame[0] + frame[2]) / 2 - t.bbox[2] / 2, frame[1] + 2.0, t.bbox[2], t.bbox[3])
        out.append((t, f))
        x0, y0, x1, y1 = tb
        w = x1 - x0
        cols = [0.0, 0.30, 0.42, 0.54, 0.64, 0.88, 1.0]
        cells = [(x0 + w * a, y0, x0 + w * b, y1) for a, b in zip(cols, cols[1:])]
        size = style.size_mm["title_block"]

        def put(text, cell, rel_y, bold=False):
            fnt = style.bold if bold else style.font
            tt, ff = self.make_text(text, "title_block", fnt, size, box_xy=(0, 0))
            cx = (cell[0] + cell[2]) / 2 - tt.bbox[2] / 2
            cy = cell[1] + (cell[3] - cell[1]) * rel_y - tt.bbox[3] / 2
            if tt.bbox[2] < cell[2] - cell[0] - 1:
                tt.bbox = (cx, cy, tt.bbox[2], tt.bbox[3])
                out.append((tt, ff))

        put("CÔNG TY ĐIỆN LỰC", cells[0], 0.35, bold=True)
        put(f"ĐỘI QUẢN LÝ VẬN HÀNH LƯỚI {rng.randint(1, 9)}", cells[0], 0.65, bold=True)
        for cell, head in zip(cells[1:4], ("P.GIÁM ĐỐC", "TR. PHÒNG ĐIỀU ĐỘ", "NGƯỜI VẼ")):
            put(head, cell, 0.15, bold=True)
            put(_fake_person(rng), cell, 0.88)
        put(f"SƠ ĐỒ NHẤT THỨ TRẠM 110 {name}", cells[4], 0.5, bold=True)
        put(f"SBV: E{rng.randint(10, 30)}.{rng.randint(1, 20)}-01", cells[5], 0.3)
        put(f"{rng.randint(1, 28):02d}/{rng.randint(1, 12):02d}/20{rng.randint(20, 26)}", cells[5], 0.7)
        sig_cells = [(c[0], c[1] + (c[3] - c[1]) * 0.28, c[2], c[1] + (c[3] - c[1]) * 0.75) for c in cells[1:4]]
        return out, cells, sig_cells


def _inflate(r: Rect, p: float) -> Rect:
    return (r[0] - p, r[1] - p, r[2] + p, r[3] + p)


def _bus_rect(b: Busbar) -> Rect:
    (x0, y0), (x1, y1) = b.polyline[0], b.polyline[-1]
    h = b.stroke_width_px / 2
    return (min(x0, x1) - h, min(y0, y1) - h, max(x0, x1) + h, max(y0, y1) + h)


def _fake_person(rng: random.Random) -> str:
    ho = ("NGUYỄN", "TRẦN", "LÊ", "PHẠM", "HOÀNG", "PHAN", "VŨ", "ĐẶNG", "BÙI", "ĐỖ")
    dem = ("VĂN", "THỊ", "HỮU", "QUỐC", "THANH", "MINH", "ĐỨC")
    ten = ("AN", "BÌNH", "CƯỜNG", "DŨNG", "HÀ", "HẢI", "KHÁNH", "LINH", "NAM", "SƠN", "TÙNG", "THẮNG")
    return f"{rng.choice(ho)} {rng.choice(dem)} {rng.choice(ten)}"


def scale_graph(graph: SubstationTopologyGraph, px_per_mm: float, width_mm: float,
                height_mm: float) -> SubstationTopologyGraph:
    """Đồ thị mm → px: nhân mọi tọa độ, kích thước, độ dày nét và scale ký hiệu với pxPerMm."""
    wpx = round(width_mm * px_per_mm)
    k = wpx / width_mm
    hpx = round(height_mm * k)

    def p(v):
        return (v[0] * k, v[1] * k)

    def box(b):
        return tuple(x * k for x in b)

    g = graph.model_copy(deep=True)
    g.page = Page(id=graph.page.id, width=wpx, height=hpx, px_per_mm=k, px_per_mm_source="synthetic")
    for d in g.devices:
        d.transform = d.transform.model_copy(update={"x": d.transform.x * k, "y": d.transform.y * k,
                                                     "scale": d.transform.scale * k})
        d.ports = [q.model_copy(update={"x": q.x * k, "y": q.y * k}) for q in d.ports]
        d.bbox = box(d.bbox)
    for w in g.wires:
        w.points = [p(q) for q in w.points]
        w.stroke_width_px *= k
    for b in g.busbars:
        b.polyline = [p(q) for q in b.polyline]
        b.stroke_width_px *= k
    for n in g.nodes:
        n.position = p(n.position)
    for t in g.texts:
        t.bbox = box(t.bbox)
    return g
