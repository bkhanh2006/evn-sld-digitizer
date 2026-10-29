"""Mẫu ngăn lộ (SDD 8.1.1): mỗi hàm dựng một BaySubGraph trong hệ chuẩn của ngăn.

Hệ chuẩn: đơn vị mm, điểm nối thanh cái tại gốc, ngăn chạy theo chiều +y. LayoutComposer áp phép
xoay/lật để có ngăn đi lên, đi xuống hoặc nằm ngang. Mã thiết bị sinh bằng EVNCodeGrammar, nên
luôn đọc lại được bằng cùng ngữ pháp mà pipeline dùng.

Nhãn chưa được đặt ở đây (chữ phải đứng thẳng sau khi xoay ngăn): mẫu chỉ ghi LabelRequest với
hướng đặt nhãn trong hệ chuẩn; LayoutComposer tính bbox chữ sau khi biến đổi.
"""
from __future__ import annotations

import random
from dataclasses import dataclass, field

from src.core.evn_codes import EVNCodeGrammar
from src.core.geometry import Placement, rotate_mirror
from src.core.schema import (Bay, Device, DeviceCode, DevicePort, Node, NodeRef, PortRef, Transform, Wire,
                             WireRef)
from src.core.symbol_lib import SymbolLibrary

Vec = tuple[float, float]
# Góc xoay để trục +y cục bộ của ký hiệu trùng hướng d (hệ ảnh, θ theo chiều kim đồng hồ)
ROT_FOR_DIR = {(0, 1): 0, (-1, 0): 90, (0, -1): 180, (1, 0): 270}


@dataclass
class LabelRequest:
    text: str
    role: str                         # device_code | line_name | parameter | pole_number | bay_label
    anchor: str                       # id thiết bị đặt nhãn cạnh nó
    side: Vec                         # hướng đặt nhãn so với bbox thiết bị (hệ chuẩn); thành phần 0 = căn giữa
    assign: bool = True               # gán nhãn cho thiết bị anchor (assignedTo)
    size_key: str = "code"            # khóa cỡ chữ trong RenderStyle
    bold: bool = False
    along_bay: bool = False           # chữ chạy dọc theo ngăn (tên lộ trên ngăn dọc → 270°)
    stack: int = 0                    # thứ tự dòng khi nhiều nhãn cùng anchor và cùng side


@dataclass
class BaySubGraph:
    bay: Bay
    devices: list[Device]
    wires: list[Wire]
    nodes: list[Node]
    labels: list[LabelRequest]
    bus_attach_nodes: list[str]                    # nút nối thanh cái; nút đầu ở gốc, (coupler) nút sau ở cuối
    external_ports: dict[str, PortRef] = field(default_factory=dict)   # "lv", "mv", "to_transformer"…


@dataclass
class BayStyle:
    """Tham số hình học của mẫu (mm), lấy mẫu ngẫu nhiên theo generator_config."""
    gap: float = 2.5                 # khe dây giữa hai thiết bị liên tiếp
    tap_stub: float = 3.0            # đoạn dây từ điểm rẽ tới thiết bị nhánh
    junction_style: str = "t_plain"  # dot | t_plain
    switch_closed_p: float = 0.5     # xác suất vẽ dao cách ly ở hình đóng
    earthing_closed_p: float = 0.1


def make_code(grammar: EVNCodeGrammar, code: str) -> DeviceCode:
    parsed = grammar.parse(code)
    if parsed is None:
        raise ValueError(f"Mã sinh ra không đọc lại được: {code}")
    return DeviceCode(normalized=parsed.normalized, voltage_kv=parsed.voltage_kv, bay_code=parsed.bay_code,
                      suffix=parsed.suffix)


class BayBuilder:
    """Dựng thiết bị, dây, nút trong hệ chuẩn; tự giữ danh sách connections của nút."""

    def __init__(self, lib: SymbolLibrary, grammar: EVNCodeGrammar, bay: Bay, style: BayStyle, rng: random.Random):
        self.lib, self.grammar, self.bay, self.style, self.rng = lib, grammar, bay, style, rng
        self.s = lib.library_unit_mm               # mm / lu
        self.devices: dict[str, Device] = {}
        self.wires: list[Wire] = []
        self.nodes: dict[str, Node] = {}
        self.labels: list[LabelRequest] = []
        self.bus_attach_nodes: list[str] = []
        self.external_ports: dict[str, PortRef] = {}
        self._n_wire = 0
        self._n_node = 0

    @property
    def tag(self) -> str:
        return self.bay.code

    def place(self, key: str, cls: str, x: float, y: float, rotation: int, mirror: bool = False,
              code: str | None = None, state: str | None = None) -> Device:
        sym = self.lib[cls]
        if mirror and not sym.allow_mirror:
            mirror = False
        p = Placement(x, y, rotation, mirror, self.s)
        ports = [DevicePort(id=q.port_id, role=q.role, x=q.xy[0], y=q.xy[1]) for q in self.lib.project_ports(cls, p)]
        drawing = sym.drawing_state if sym.drawing_state in ("open", "closed", "unknown") else None
        if state is None:
            state = "closed" if sym.conduction == "by_state" else drawing
        dev = Device(id=f"dev-{self.tag}-{key}", detector_class=cls, type=sym.type, bay_id=self.bay.id,
                     code=make_code(self.grammar, code) if code else None, bbox=self.lib.page_bbox(cls, p),
                     transform=Transform(x=x, y=y, rotation_deg=rotation, mirror_x=mirror, scale=self.s),
                     ports=ports, drawing_state=drawing, operational_state=state, confidence=1.0,
                     review_status="confirmed")
        if dev.id in self.devices:
            raise ValueError(f"Trùng id thiết bị {dev.id}")
        self.devices[dev.id] = dev
        self.bay.device_ids.append(dev.id)
        return dev

    def port_xy(self, ref: PortRef) -> Vec:
        p = self.devices[ref.device_id].port(ref.port_id)
        return (p.x, p.y)

    def node(self, pos: Vec, style: str | None = None) -> str:
        self._n_node += 1
        nid = f"n-{self.tag}-{self._n_node}"
        self.nodes[nid] = Node(id=nid, type="junction", style=style or self.style.junction_style, position=pos,
                               connections=[], review_status="confirmed")
        return nid

    def wire(self, a: PortRef | NodeRef, b: PortRef | NodeRef, points: list[Vec]) -> str:
        self._n_wire += 1
        wid = f"w-{self.tag}-{self._n_wire}"
        self.wires.append(Wire(id=wid, points=points, stroke_width_px=1.0, from_=a, to=b,
                               connection_status="confirmed", confidence=1.0))
        for end in (a, b):
            if isinstance(end, NodeRef):
                self.nodes[end.id].connections.append(WireRef(id=wid))
        return wid

    def label(self, dev: Device, text: str | None = None, side: Vec = (1, 0), **kw) -> None:
        text = text or (dev.code.normalized if dev.code else None)
        if text:
            self.labels.append(LabelRequest(text=text, role=kw.pop("role", "device_code"), anchor=dev.id, side=side,
                                            **kw))

    def switch_class(self, base: str, closed_p: float) -> str:
        return f"{base}_closed" if self.rng.random() < closed_p else f"{base}_open"

    def result(self) -> BaySubGraph:
        return BaySubGraph(bay=self.bay, devices=list(self.devices.values()), wires=self.wires,
                           nodes=list(self.nodes.values()), labels=self.labels,
                           bus_attach_nodes=self.bus_attach_nodes, external_ports=self.external_ports)


class Chain:
    """Chuỗi thiết bị nối tiếp theo một hướng trục; giữ 'đầu hiện tại' (cổng hoặc nút) để nối tiếp."""

    def __init__(self, b: BayBuilder, start: PortRef | NodeRef, start_xy: Vec, direction: Vec):
        self.b, self.end, self.xy, self.d = b, start, start_xy, direction
        self.via: list[Vec] = []        # đỉnh gấp khúc chờ đưa vào dây kế tiếp

    def _advance(self, dist: float) -> Vec:
        return self.xy[0] + self.d[0] * dist, self.xy[1] + self.d[1] * dist

    def bend(self, dist: float, direction: Vec) -> "Chain":
        """Đi thẳng thêm dist rồi rẽ sang hướng mới (chỉ là đỉnh của dây, không tạo nút)."""
        self.xy = self._advance(dist)
        self.via.append(self.xy)
        self.d = direction
        return self

    def _connect(self, ref: PortRef | NodeRef, xy: Vec) -> None:
        self.b.wire(self.end, ref, [self.xy_start(), *self.via, xy])
        self.via = []

    def xy_start(self) -> Vec:
        e = self.end
        if isinstance(e, PortRef):
            return self.b.port_xy(e)
        return tuple(self.b.nodes[e.id].position)

    def inline(self, key: str, cls: str, gap: float | None = None, code: str | None = None,
               state: str | None = None, mirror: bool = False, exit_port: str | None = None) -> Device:
        """Thiết bị nối tiếp: cổng vào (localY nhỏ nhất) quay về phía đầu chuỗi."""
        b = self.b
        gap = b.style.gap if gap is None else gap
        sym = b.lib[cls]
        rot = ROT_FOR_DIR[self.d]
        p_in = min(sym.ports, key=lambda p: p.local_y)
        target = self._advance(gap)
        du, dv = rotate_mirror(p_in.local_x, p_in.local_y, rot, mirror and sym.allow_mirror)
        dev = b.place(key, cls, target[0] - b.s * du, target[1] - b.s * dv, rot, mirror, code, state)
        self._connect(PortRef(device_id=dev.id, port_id=p_in.id), target)
        out = exit_port or max(sym.ports, key=lambda p: p.local_y).id
        if out == p_in.id:
            raise ValueError(f"{cls} không có cổng ra cho chuỗi")
        self.end = PortRef(device_id=dev.id, port_id=out)
        self.xy = b.port_xy(self.end)
        return dev

    def terminal(self, key: str, cls: str, gap: float | None = None, code: str | None = None,
                 state: str | None = None, mirror: bool = False) -> Device:
        """Thiết bị một cổng ở cuối chuỗi; chuỗi kết thúc."""
        b = self.b
        gap = b.style.gap if gap is None else gap
        sym = b.lib[cls]
        rot = ROT_FOR_DIR[self.d]
        port = sym.ports[0]
        target = self._advance(gap)
        du, dv = rotate_mirror(port.local_x, port.local_y, rot, mirror and sym.allow_mirror)
        dev = b.place(key, cls, target[0] - b.s * du, target[1] - b.s * dv, rot, mirror, code, state)
        self._connect(PortRef(device_id=dev.id, port_id=port.id), target)
        self.end = None
        return dev

    def junction(self, gap: float | None = None, style: str | None = None) -> str:
        gap = self.b.style.gap if gap is None else gap
        pos = self._advance(gap)
        nid = self.b.node(pos, style)
        self._connect(NodeRef(id=nid), pos)
        self.end, self.xy = NodeRef(id=nid), pos
        return nid

    def branch(self, direction: Vec) -> "Chain":
        """Chuỗi mới rẽ từ nút hiện tại."""
        if not isinstance(self.end, NodeRef):
            raise ValueError("Chỉ rẽ nhánh được từ một nút junction")
        return Chain(self.b, self.end, self.xy, direction)

    def tap(self, side: Vec, key: str, cls: str, code: str | None = None, state: str | None = None,
            stub: float | None = None, label_side: Vec | None = None, mirror: bool = False) -> Device:
        """Thiết bị một cổng treo bên cạnh nút hiện tại (dao tiếp địa, chống sét, TU)."""
        stub = self.b.style.tap_stub if stub is None else stub
        dev = self.branch(side).terminal(key, cls, stub, code, state, mirror)
        self.b.label(dev, side=label_side or side)
        return dev


# ---------------------------------------------------------------------------- 6 mẫu ngăn (SDD 8.1.1)

def _start(b: BayBuilder) -> Chain:
    nid = b.node((0.0, 0.0), "t_on_busbar")
    b.bus_attach_nodes.append(nid)
    return Chain(b, NodeRef(id=nid), (0.0, 0.0), (0, 1))


def _new(lib, grammar, kind: str, code: str, kv: int, style: BayStyle, rng) -> BayBuilder:
    bay = Bay(id=f"bay-{code}", code=code, voltage_kv=kv, kind=kind, device_ids=[])
    return BayBuilder(lib, grammar, bay, style, rng)


def line_bay(lib: SymbolLibrary, grammar: EVNCodeGrammar, code: str, kv: int, style: BayStyle, rng: random.Random,
             bus_index: int = 1, has_vt: bool = False, earthing: tuple[str, ...] = ("15", "75", "76"),
             exit_turn: int = 0, line_name: str | None = None, pole: str | None = None) -> BaySubGraph:
    """Đường dây: DCL -1/-2 → DTĐ -15 → MC → TI → DTĐ -75 → DCL -7 → DTĐ -76 → mũi tên.

    exit_turn: 0 = mũi tên thẳng theo ngăn; ±1 = đường dây rẽ ngang ở đầu ngăn (kiểu E18.9), khi đó
    nhánh TU kiểu tụ (tụ – TU – tụ – đất) đi về phía ngược lại.
    """
    b = _new(lib, grammar, "line", code, kv, style, rng)
    side = exit_turn or rng.choice((-1, 1))         # phía treo dao tiếp địa; ngăn rẽ ngang: cùng phía mũi tên
    es, lab = (side, 0), (-side, 0)
    bus = f"busbar_{bus_index}"
    ch = _start(b)
    d = ch.inline("dcl_bus", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, bus), mirror=side > 0)
    b.label(d, side=lab)
    if "15" in earthing:
        ch.junction()
        ch.tap(es, "es_bus", b.switch_class("earthing_switch", style.earthing_closed_p),
               code=grammar.earthing(code, bus, "breaker_side"), mirror=side < 0)
    d = ch.inline("cb", "circuit_breaker", code=code)
    b.label(d, side=es if "15" not in earthing else lab, size_key="breaker", bold=True)
    d = ch.inline("ct", "current_transformer", code=grammar.ct(code))
    b.label(d, side=lab)
    if "75" in earthing:
        ch.junction()
        ch.tap(es, "es_cb", b.switch_class("earthing_switch", style.earthing_closed_p),
               code=grammar.earthing(code, "line", "breaker_side"), mirror=side < 0)
    d = ch.inline("dcl_line", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, "line"), mirror=side > 0)
    b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es_line", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, "line", "line_or_capacitor_side"), mirror=side < 0)

    if exit_turn == 0:
        if has_vt:
            # TU lệch tâm (bbox x ∈ [-18, 48] lu): lật để phần lồi quay ra xa thanh cái
            ch.junction(gap=style.gap * 2.5)
            ch.tap((-side, 0), "vt", "voltage_transformer", code=grammar.line_vt(code), stub=style.tap_stub * 1.5,
                   mirror=side < 0)
        arrow = ch.terminal("line", "line_feeder_arrow", gap=style.gap * 2)
        name_side, along = (0, 1), False             # tên đường dây 110 kV viết ngang (E18.1, E18.9)
    else:
        turn = (exit_turn, 0)
        if has_vt:
            ch.junction(gap=style.gap * 2, style="t_plain")
            vt = ch.branch((-exit_turn, 0))
            vt.inline("cc1", "coupling_capacitor", gap=style.gap * 3)
            vt.junction()
            vt.tap((0, -1), "vt", "voltage_transformer", code=grammar.line_vt(code), stub=style.tap_stub,
                   label_side=(0, -1), mirror=exit_turn < 0)
            vt.inline("cc2", "coupling_capacitor")
            vt.terminal("gnd", "ground_symbol", gap=style.gap)
            arrow = ch.branch(turn).terminal("line", "line_feeder_arrow", gap=style.gap * 6)
        else:
            ch.bend(style.gap * 2, turn)
            arrow = ch.terminal("line", "line_feeder_arrow", gap=style.gap * 6)
        name_side, along = (exit_turn, 1), False
    if line_name:
        b.label(arrow, line_name, side=name_side, role="line_name", assign=False, size_key="name", bold=True,
                along_bay=along)
    if pole:
        b.label(arrow, pole, side=(-name_side[0] if name_side[0] else 1, 0), role="pole_number", assign=False)
    return b.result()


def mv_feeder_bay(lib, grammar, code: str, kv: int, style: BayStyle, rng: random.Random,
                  breaker: str = "drawout", cable: bool = True, line_name: str | None = None,
                  bus_index: int = 1) -> BaySubGraph:
    """Xuất tuyến trung áp: MC hợp bộ (hoặc DCL + MC + DCL) → TI → DTĐ -76 → đầu cáp → mũi tên."""
    b = _new(lib, grammar, "line", code, kv, style, rng)
    side = rng.choice((-1, 1))
    es, lab = (side, 0), (-side, 0)
    ch = _start(b)
    if breaker == "drawout":
        d = ch.inline("cb", "drawout_breaker", code=code)
    else:
        d0 = ch.inline("dcl_bus", b.switch_class("disconnector", style.switch_closed_p),
                       code=grammar.disconnector(code, f"busbar_{bus_index}"), mirror=side > 0)
        b.label(d0, side=lab)
        d = ch.inline("cb", "circuit_breaker", code=code)
    b.label(d, side=lab, size_key="breaker", bold=True)
    d = ch.inline("ct", "current_transformer", code=grammar.ct(code))
    b.label(d, side=lab)
    if breaker != "drawout":
        d = ch.inline("dcl_line", b.switch_class("disconnector", style.switch_closed_p),
                      code=grammar.disconnector(code, "line"), mirror=side > 0)
        b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es_line", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, "line", "line_or_capacitor_side"), mirror=side < 0)
    if cable:
        ch.inline("cable", "cable_termination")
    arrow = ch.terminal("line", "line_feeder_arrow", gap=style.gap * 2)
    if line_name:
        b.label(arrow, line_name, side=(0, 1), role="line_name", assign=False, size_key="name", along_bay=True)
    return b.result()


def transformer_bay(lib, grammar, n: int, hv_kv: int, side_kvs: dict[str, int], style: BayStyle,
                    rng: random.Random, bus_index: int = 1, windings: int = 3, arresters: bool = True,
                    cable: bool = True, params: list[str] | None = None, mv_dir: int = 1) -> BaySubGraph:
    """Ngăn MBA phía cao áp: DCL -1 → DTĐ -15 → MC → TI → DTĐ -35 → DCL -3 → DTĐ -38 → MBA.

    Trung tính (MBA 3 cuộn): DTĐ -08 + chống sét CS0T. Mỗi cuộn còn lại (mv, lv) ra: chống sét → đầu
    cáp → cổng ngoài external_ports[role], để router nối tới ngăn tổng ở cấp áp khác.
    mv_dir: phía (±x hệ chuẩn) của cuộn mv; dao tiếp địa treo phía ngược lại.
    """
    code = grammar.breaker(hv_kv, "transformer", n)
    tx = grammar.main_transformer(n)
    b = _new(lib, grammar, "transformer", code, hv_kv, style, rng)
    side = -mv_dir if windings == 3 else rng.choice((-1, 1))
    es, lab = (side, 0), (-side, 0)
    bus = f"busbar_{bus_index}"
    ch = _start(b)
    d = ch.inline("dcl_bus", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, bus), mirror=side > 0)
    b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es_bus", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, bus, "breaker_side"), mirror=side < 0)
    d = ch.inline("ct", "current_transformer", code=grammar.ct(code))
    b.label(d, side=lab)
    d = ch.inline("cb", "circuit_breaker", code=code)
    b.label(d, side=lab, size_key="breaker", bold=True)
    ch.junction()
    ch.tap(es, "es_cb", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, "transformer", "breaker_side"), mirror=side < 0)
    d = ch.inline("dcl_tx", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, "transformer"), mirror=side > 0)
    b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es_tx", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, "transformer", "transformer_reactor_vt_side"), mirror=side < 0)
    if arresters:
        ch.junction()
        ch.tap(es, "cs_hv", "surge_arrester", code=grammar.arrester(tx, hv_kv), stub=style.tap_stub)

    cls = "transformer_3w" if windings == 3 else "transformer_2w"
    # MBA 3 cuộn: cuộn "mv" ở bên phải (+x) → lật để quay về phía không có dao tiếp địa
    t = ch.inline("tx", cls, gap=style.gap * 1.5, code=tx, mirror=(windings == 3 and side > 0),
                  exit_port="p_lv")
    lines = [tx] + list(params or [])
    for i, text in enumerate(lines):
        b.labels.append(LabelRequest(text=text, role="device_code" if i == 0 else "parameter", anchor=t.id,
                                     side=(-side, -1) if windings == 3 else (-side, 0), assign=i == 0,
                                     size_key="param", bold=True, stack=i))

    ext_roles = {"lv": "p_lv"} if windings == 2 else {"mv": "p_mv", "lv": "p_lv"}
    for role, pid in ext_roles.items():
        ref = PortRef(device_id=t.id, port_id=pid)
        sym_port = lib[cls].port(pid)
        direction = rotate_mirror(*sym_port.direction, 0, windings == 3 and side > 0)
        sc = Chain(b, ref, b.port_xy(ref), (round(direction[0]), round(direction[1])))
        if arresters:
            sc.junction(gap=style.gap * 1.5)
            perp = (side, 0) if sc.d[0] == 0 else (0, 1)
            sc.tap(perp, f"cs_{role}", "surge_arrester", code=grammar.arrester(tx, side_kvs[role]),
                   stub=style.tap_stub)
        if cable or isinstance(sc.end, NodeRef):     # cổng ngoài phải là cổng thiết bị, không phải nút
            sc.inline(f"cable_{role}", "cable_termination")
        b.external_ports[role] = sc.end

    if windings == 3:
        ref = PortRef(device_id=t.id, port_id="p_n")
        sp = lib[cls].port("p_n")
        direction = rotate_mirror(*sp.direction, 0, side > 0)
        nc = Chain(b, ref, b.port_xy(ref), (round(direction[0]), round(direction[1])))
        nc.junction(gap=style.gap * 2)
        nc.tap((0, 1), "es_n", b.switch_class("earthing_switch", style.earthing_closed_p),
               code=grammar.earthing_neutral(code), label_side=(0, 1))
        nc.terminal("cs_n", "surge_arrester", code=grammar.arrester(tx, neutral=True))
        b.label(b.devices[f"dev-{code}-cs_n"], side=(0, -1))
    return b.result()


def incomer_bay(lib, grammar, n: int, kv: int, style: BayStyle, rng: random.Random, breaker: str = "drawout",
                bus_index: int = 1) -> BaySubGraph:
    """Ngăn tổng phía trung áp của MBA (331, 431): MC → TI → DTĐ -38 → đầu cáp → cổng ngoài to_transformer."""
    code = grammar.breaker(kv, "transformer", n)
    b = _new(lib, grammar, "transformer", code, kv, style, rng)
    side = rng.choice((-1, 1))
    es, lab = (side, 0), (-side, 0)
    ch = _start(b)
    if breaker == "drawout":
        d = ch.inline("cb", "drawout_breaker", code=code)
        b.label(d, side=lab, size_key="breaker", bold=True)
    else:
        d = ch.inline("dcl_bus", b.switch_class("disconnector", style.switch_closed_p),
                      code=grammar.disconnector(code, f"busbar_{bus_index}"), mirror=side > 0)
        b.label(d, side=lab)
        d = ch.inline("cb", "circuit_breaker", code=code)
        b.label(d, side=lab, size_key="breaker", bold=True)
    d = ch.inline("ct", "current_transformer", code=grammar.ct(code))
    b.label(d, side=lab)
    if breaker != "drawout":
        d = ch.inline("dcl_tx", b.switch_class("disconnector", style.switch_closed_p),
                      code=grammar.disconnector(code, "transformer"), mirror=side > 0)
        b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es_tx", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, "transformer", "transformer_reactor_vt_side"), mirror=side < 0)
    ch.inline("cable", "cable_termination")         # luôn có thiết bị ở cuối để cổng ngoài là một cổng
    b.external_ports["to_transformer"] = ch.end
    return b.result()


def bus_coupler_bay(lib, grammar, kv: int, style: BayStyle, rng: random.Random, bus_a: int = 1, bus_b: int = 2,
                    breaker: bool = True, earthing: bool = True) -> BaySubGraph:
    """Liên lạc / phân đoạn: DCL -1 → (DTĐ -15) → MC → TI → (DTĐ -25) → DCL -2, hai đầu nối hai thanh cái."""
    code = grammar.bus_coupler(kv, bus_a, bus_b)
    b = _new(lib, grammar, "bus_coupler", code, kv, style, rng)
    es, lab = (1, 0), (-1, 0)                      # sau khi xoay ngang: DTĐ ở dưới, nhãn ở trên
    ch = _start(b)
    d = ch.inline("dcl_a", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, f"busbar_{bus_a}"))
    b.label(d, side=lab)
    if earthing:
        ch.junction()
        ch.tap(es, "es_a", b.switch_class("earthing_switch", style.earthing_closed_p),
               code=grammar.earthing(code, f"busbar_{bus_a}", "breaker_side"))
    if breaker:
        d = ch.inline("cb", "circuit_breaker", code=code)
        b.label(d, side=lab, size_key="breaker", bold=True)
        d = ch.inline("ct", "current_transformer", code=grammar.ct(code))
        b.label(d, side=es)
    if earthing:
        ch.junction()
        ch.tap(es, "es_b", b.switch_class("earthing_switch", style.earthing_closed_p),
               code=grammar.earthing(code, f"busbar_{bus_b}", "breaker_side"))
    d = ch.inline("dcl_b", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, f"busbar_{bus_b}"))
    b.label(d, side=lab)
    end = ch._advance(style.gap)
    nid = b.node(end, "t_on_busbar")
    ch._connect(NodeRef(id=nid), end)
    b.bus_attach_nodes.append(nid)
    return b.result()


def bus_vt_bay(lib, grammar, kv: int, bus_n: int, style: BayStyle, rng: random.Random,
               disconnector: bool = True, fuse: bool = False, arrester: bool = False) -> BaySubGraph:
    """TU thanh cái: (DCL) → DTĐ -14 → (cầu chì) → TU; tùy chọn chống sét CS-TUC."""
    host = grammar.bus_vt(kv, bus_n)
    b = _new(lib, grammar, "bus_vt", host, kv, style, rng)
    side = rng.choice((-1, 1))
    es, lab = (side, 0), (-side, 0)
    ch = _start(b)
    if disconnector:
        d = ch.inline("dcl", b.switch_class("disconnector", style.switch_closed_p),
                      code=grammar.disconnector(host, f"busbar_{bus_n}"), mirror=side > 0)
        b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(host, f"busbar_{bus_n}", "busbar_side"), mirror=side < 0)
    if fuse:
        d = ch.inline("fuse", "fuse_disconnector", code=grammar.fuse(host))
        b.label(d, side=lab)
    if arrester:
        ch.junction()
        ch.tap(lab, "cs", "surge_arrester", code=grammar.arrester(host))
    vt = ch.terminal("vt", "voltage_transformer", gap=style.gap * 1.5, code=host, mirror=side < 0)
    b.label(vt, side=(0, 1))
    return b.result()


def aux_or_capacitor_bay(lib, grammar, kv: int, n: int, style: BayStyle, rng: random.Random, kind: str = "aux",
                         breaker: str = "drawout", params: list[str] | None = None) -> BaySubGraph:
    """Tự dùng: MC (hoặc cầu chì) → TI → DTĐ -38 → MBA tự dùng → đầu ra 0,4 kV.
    Tụ bù: DCL -1 → MC T → TI → kháng → tụ."""
    side = rng.choice((-1, 1))
    es, lab = (side, 0), (-side, 0)
    if kind == "aux":
        host = grammar.aux_transformer(kv, n)
        code = grammar.breaker(kv, "aux_transformer", n) if breaker != "fuse" else host
        b = _new(lib, grammar, "aux", code, kv, style, rng)
        ch = _start(b)
        if breaker == "fuse":
            d = ch.inline("fuse", "fuse_disconnector", code=grammar.fuse(host))
            b.label(d, side=lab)
        else:
            d = ch.inline("cb", "drawout_breaker", code=code)
            b.label(d, side=lab, size_key="breaker", bold=True)
            d = ch.inline("ct", "current_transformer", code=grammar.ct(code))
            b.label(d, side=lab)
            ch.junction()
            ch.tap(es, "es", b.switch_class("earthing_switch", style.earthing_closed_p),
                   code=grammar.earthing(code, "transformer", "transformer_reactor_vt_side"), mirror=side < 0)
        t = ch.inline("tx", "transformer_2w", gap=style.gap * 1.5, code=host)
        for i, text in enumerate([host] + list(params or [])):
            b.labels.append(LabelRequest(text=text, role="device_code" if i == 0 else "parameter", anchor=t.id,
                                         side=lab, assign=i == 0, size_key="code", stack=i))
        ch.terminal("out", "line_feeder_arrow", gap=style.gap * 1.5)
        return b.result()

    code = grammar.capacitor_breaker(kv, n)
    b = _new(lib, grammar, "capacitor", code, kv, style, rng)
    ch = _start(b)
    d = ch.inline("dcl", b.switch_class("disconnector", style.switch_closed_p),
                  code=grammar.disconnector(code, "busbar_1"), mirror=side > 0)
    b.label(d, side=lab)
    d = ch.inline("cb", "circuit_breaker", code=code)
    b.label(d, side=lab, size_key="breaker", bold=True)
    d = ch.inline("ct", "current_transformer", code=grammar.ct(code[1:]))
    b.label(d, side=lab)
    ch.junction()
    ch.tap(es, "es", b.switch_class("earthing_switch", style.earthing_closed_p),
           code=grammar.earthing(code, "busbar_1", "breaker_side"), mirror=side < 0)
    d = ch.inline("reactor", "shunt_reactor", code=grammar.reactor(kv, n), mirror=side > 0)
    b.label(d, side=lab)
    d = ch.terminal("cap", "shunt_capacitor", code=grammar.capacitor(kv, n))
    b.label(d, side=lab)
    for i, text in enumerate(params or []):
        b.labels.append(LabelRequest(text=text, role="parameter", anchor=d.id, side=(0, 1), assign=False,
                                     stack=i))
    return b.result()
