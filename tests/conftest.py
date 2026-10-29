import pytest

from src.core.evn_codes import default_grammar
from src.core.geometry import Placement
from src.core.schema import (Busbar, BusbarRef, Device, DeviceCode, DevicePort, DeviceRef, Node, NodeRef, Page,
                             PortRef, Provenance, SubstationTopologyGraph, Text, Transform, Wire, WireRef)
from src.core.symbol_lib import default_library
from src.core.text_metrics import find_font, text_bbox


class GraphBuilder:
    """Dựng đồ thị nhỏ bằng tay cho test: đặt ký hiệu theo thư viện, nối dây đúng tọa độ cổng."""

    def __init__(self):
        self.lib = default_library()
        self.grammar = default_grammar()
        self.devices: list[Device] = []
        self.busbars: list[Busbar] = []
        self.wires: list[Wire] = []
        self.nodes: list[Node] = []

    def device(self, id: str, cls: str, x: float, y: float, rotation: int = 0, mirror: bool = False,
               code: str | None = None, state: str | None = None) -> Device:
        sym = self.lib[cls]
        placement = Placement(x, y, rotation, mirror, 1.0)
        ports = [DevicePort(id=p.port_id, role=p.role, x=p.xy[0], y=p.xy[1])
                 for p in self.lib.project_ports(cls, placement)]
        dev_code = None
        if code is not None:
            parsed = self.grammar.parse(code)
            dev_code = DeviceCode(normalized=parsed.normalized, voltage_kv=parsed.voltage_kv,
                                  bay_code=parsed.bay_code, suffix=parsed.suffix)
        drawing_state = sym.drawing_state if sym.drawing_state in ("open", "closed", "unknown") else None
        dev = Device(id=id, detector_class=cls, type=sym.type, code=dev_code, bbox=self.lib.page_bbox(cls, placement),
                     transform=Transform(x=x, y=y, rotation_deg=rotation, mirror_x=mirror), ports=ports,
                     drawing_state=drawing_state, operational_state=state or drawing_state,
                     review_status="confirmed")
        self.devices.append(dev)
        return dev

    def busbar(self, id: str, code: str, polyline: list[tuple[float, float]]) -> Busbar:
        parsed = self.grammar.parse(code)
        b = Busbar(id=id, code=code, voltage_kv=parsed.voltage_kv, polyline=polyline, stroke_width_px=4,
                   review_status="confirmed")
        self.busbars.append(b)
        return b

    def node(self, id: str, type: str, style: str, position: tuple[float, float],
             connections: list) -> Node:
        n = Node(id=id, type=type, style=style, position=position, connections=connections,
                 review_status="confirmed")
        self.nodes.append(n)
        return n

    def _point(self, ref) -> tuple[float, float]:
        if isinstance(ref, PortRef):
            dev = next(d for d in self.devices if d.id == ref.device_id)
            p = dev.port(ref.port_id)
            return (p.x, p.y)
        raise ValueError("Cần truyền points khi đầu dây không phải cổng")

    def wire(self, id: str, frm, to, points: list[tuple[float, float]] | None = None,
             status: str = "confirmed") -> Wire:
        if points is None:
            points = [self._point(frm), self._point(to)]
        w = Wire(id=id, points=points, stroke_width_px=2, from_=frm, to=to, connection_status=status)
        self.wires.append(w)
        return w

    def graph(self, with_nets: bool = True) -> SubstationTopologyGraph:
        from src.core.net_builder import NetBuilder

        g = SubstationTopologyGraph(
            provenance=Provenance(source="synthetic_gt"),
            page=Page(id="test", width=2000, height=1500, px_per_mm=8.0, px_per_mm_source="synthetic"),
            devices=self.devices, busbars=self.busbars, wires=self.wires, nodes=self.nodes,
        )
        if with_nets:
            g.nets = NetBuilder(self.lib).build(g).nets
        return g


def port(device_id: str, port_id: str) -> PortRef:
    return PortRef(device_id=device_id, port_id=port_id)


def wire_ref(id: str) -> WireRef:
    return WireRef(id=id)


def busbar_ref(id: str) -> BusbarRef:
    return BusbarRef(id=id)


def node_ref(id: str) -> NodeRef:
    return NodeRef(id=id)


@pytest.fixture
def builder() -> GraphBuilder:
    return GraphBuilder()


@pytest.fixture
def line_bay(builder: GraphBuilder) -> GraphBuilder:
    """Ngăn 171 rút gọn: C11 — DCL 171-1 — MC 171 — TI171 — DTĐ 171-76 — mũi tên đường dây.

    Các ký hiệu xếp dọc trên trục x = 500, thanh cái ở y = 600, đường dây đi lên phía trên.
    """
    b = builder
    b.busbar("bus-C11", "C11", [(300, 600), (700, 600)])
    b.device("dev-171-1", "disconnector_open", 500, 560, code="171-1", state="closed")
    b.device("dev-171", "circuit_breaker", 500, 500, code="171", state="closed")
    b.device("dev-TI171", "current_transformer", 500, 450, code="TI171")
    b.device("dev-171-76", "earthing_switch_open", 540, 380, rotation=270, code="171-76")  # cổng quay sang trái
    b.device("dev-line", "line_feeder_arrow", 500, 300, rotation=180)
    b.node("node-bus", "junction", "t_on_busbar", (500, 600), [wire_ref("w-bus"), busbar_ref("bus-C11")])
    b.wire("w-bus", node_ref("node-bus"), port("dev-171-1", "p2"), points=[(500, 600), (500, 580)])
    b.wire("w-dcl-cb", port("dev-171-1", "p1"), port("dev-171", "p2"))
    b.wire("w-cb-ti", port("dev-171", "p1"), port("dev-TI171", "p2"))
    b.node("node-es", "junction", "t_plain", (500, 380), [wire_ref("w-ti-es"), wire_ref("w-es"), wire_ref("w-line")])
    b.wire("w-ti-es", port("dev-TI171", "p1"), node_ref("node-es"), points=[(500, 440), (500, 380)])
    b.wire("w-es", node_ref("node-es"), port("dev-171-76", "p1"), points=[(500, 380), (516, 380)])
    b.wire("w-line", node_ref("node-es"), port("dev-line", "p1"), points=[(500, 380), (500, 324)])
    return b


@pytest.fixture
def slice_graph(line_bay: GraphBuilder) -> SubstationTopologyGraph:
    """Lát cắt dọc (SDD 1.1-5): thanh cái C11 110 kV, ngăn đường dây 171 đi lên, ngăn MBA 131 đi xuống
    tới T1 110/22 kV, phía 22 kV ra một đầu xuất tuyến. Mỗi thiết bị có mã kèm một nhãn bên phải."""
    b = line_bay
    b.device("dev-131-1", "disconnector_closed", 700, 640, code="131-1", state="closed")
    b.device("dev-131", "circuit_breaker", 700, 700, code="131", state="closed")
    b.device("dev-TI131", "current_transformer", 700, 750, code="TI131")
    b.device("dev-T1", "transformer_2w", 700, 830, code="T1")
    b.device("dev-471", "line_feeder_arrow", 700, 940)
    b.busbars[0] = b.busbars[0].model_copy(update={"polyline": [(300, 600), (900, 600)]})
    b.node("node-131", "junction", "t_on_busbar", (700, 600), [busbar_ref("bus-C11"), wire_ref("w-131-bus")])
    b.wire("w-131-bus", node_ref("node-131"), port("dev-131-1", "p1"), points=[(700, 600), (700, 620)])
    b.wire("w-131-cb", port("dev-131-1", "p2"), port("dev-131", "p1"))
    b.wire("w-131-ti", port("dev-131", "p2"), port("dev-TI131", "p1"))
    b.wire("w-131-tx", port("dev-TI131", "p2"), port("dev-T1", "p_hv"))
    b.wire("w-T1-lv", port("dev-T1", "p_lv"), port("dev-471", "p1"))
    g = b.graph()
    font = str(find_font("arial.ttf"))
    for d in g.devices:
        if d.code is None:
            continue
        x, y, w, h = d.bbox
        tid = f"txt-{d.id}"
        g.texts.append(Text(id=tid, raw=d.code.normalized, normalized=d.code.normalized,
                            bbox=text_bbox(d.code.normalized, font, 14, (x + w + 6, y + h / 2 + 5)),
                            role="device_code", assigned_to=DeviceRef(id=d.id)))
        d.label_text_ids = [tid]
    return g


@pytest.fixture(scope="session")
def synthetic_layout():
    """Một bố cục tổng hợp cỡ vừa (2 MBA, có đi dây liên cấp), seed cố định."""
    import random

    from src.generator.layout_composer import LayoutComposer, LayoutFailed
    from src.generator.topology_sampler import TopologySampler, load_configs
    gen, presets = load_configs()
    lib, grammar = default_library(), default_grammar()
    sampler = TopologySampler(grammar, gen, presets)
    composer = LayoutComposer(lib, grammar, gen, presets)
    rng = random.Random(3)
    topo = sampler.sample(rng, difficulty="medium", preset="E18_9_like")
    for _ in range(10):
        try:
            return composer.compose(topo, rng)
        except LayoutFailed:
            sampler.drop_one_feeder(topo, rng)
    pytest.fail("không bố cục được topology thử")
