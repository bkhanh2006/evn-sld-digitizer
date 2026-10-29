"""Schema SubstationTopologyGraph v1.1 (SDD mục 6.2–6.3).

Tên trường trong JSON dùng camelCase; trong Python dùng snake_case. Luôn xuất bằng
`graph.to_json()` (by_alias) để file khớp SDD.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

SCHEMA_VERSION = "1.1"

ReviewStatus = Literal["pending", "confirmed", "rejected", "edited"]
SwitchState = Literal["open", "closed", "unknown"]
Point = tuple[float, float]
BBox = tuple[float, float, float, float]

ISSUE_TYPES = (
    "broken_reference", "duplicate_code", "low_resolution_input", "busbar_suffix_mismatch", "voltage_conflict",
    "dangling_wire_end", "unconnected_port", "ambiguous_port_connection", "ambiguous_crossing",
    "ambiguous_orientation", "class_ambiguity", "missing_label", "bay_chain_break", "device_bypassed",
    "unknown_code_suffix", "unexpected_symbol_scale", "stage_failed",
)
IssueType = Literal[ISSUE_TYPES]  # type: ignore[valid-type]


class _Model(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid")


class _Ref(_Model):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True)

    def key(self) -> tuple:
        """Khóa băm dùng cho chỉ mục và Union-Find."""
        raise NotImplementedError


# ------------------------------------------------------------------ tham chiếu có kiểu (SDD 2.4)

class DeviceRef(_Ref):
    kind: Literal["device"] = "device"
    id: str

    def key(self) -> tuple:
        return ("device", self.id)


class PortRef(_Ref):
    kind: Literal["port"] = "port"
    device_id: str
    port_id: str

    def key(self) -> tuple:
        return ("port", self.device_id, self.port_id)


class WireRef(_Ref):
    kind: Literal["wire"] = "wire"
    id: str

    def key(self) -> tuple:
        return ("wire", self.id)


class BusbarRef(_Ref):
    kind: Literal["busbar"] = "busbar"
    id: str

    def key(self) -> tuple:
        return ("busbar", self.id)


class NodeRef(_Ref):
    kind: Literal["node"] = "node"
    id: str

    def key(self) -> tuple:
        return ("node", self.id)


class TextRef(_Ref):
    kind: Literal["text"] = "text"
    id: str

    def key(self) -> tuple:
        return ("text", self.id)


class BayRef(_Ref):
    kind: Literal["bay"] = "bay"
    id: str

    def key(self) -> tuple:
        return ("bay", self.id)


class DanglingRef(_Ref):
    kind: Literal["dangling"] = "dangling"
    position: Point

    def key(self) -> tuple:
        return ("dangling", self.position)


AnyRef = Annotated[Union[DeviceRef, PortRef, WireRef, BusbarRef, NodeRef, TextRef, BayRef, DanglingRef],
                   Field(discriminator="kind")]
WireEnd = Annotated[Union[PortRef, NodeRef, DanglingRef], Field(discriminator="kind")]
NetMember = Annotated[Union[WireRef, BusbarRef, PortRef], Field(discriminator="kind")]
NodeConnection = Annotated[Union[WireRef, BusbarRef], Field(discriminator="kind")]
TextTarget = Annotated[Union[DeviceRef, BusbarRef, BayRef], Field(discriminator="kind")]


# ------------------------------------------------------------------ đối tượng

class Provenance(_Model):
    source: Literal["synthetic_gt", "pipeline", "human_reviewed"]
    pipeline_version: str | None = None
    generator_version: str | None = None
    config: str | None = None
    seed: int | None = None
    created_at: str | None = None


class Page(_Model):
    id: str
    source_file: str | None = None
    width: int
    height: int
    unit: Literal["px"] = "px"
    origin: Literal["top-left"] = "top-left"
    px_per_mm: float
    px_per_mm_source: Literal["pdf_page_size", "estimated", "synthetic"]
    normalization_scale: float = 1.0


class Bay(_Model):
    id: str
    code: str
    voltage_kv: int | None = None
    kind: Literal["line", "transformer", "bus_coupler", "bus_vt", "aux", "capacitor", "reactor", "spare", "unknown"]
    device_ids: list[str] = Field(default_factory=list)


class Busbar(_Model):
    id: str
    code: str | None = None
    voltage_kv: int | None = None
    label_text_ids: list[str] = Field(default_factory=list)
    polyline: list[Point]
    stroke_width_px: float
    confidence: float = 1.0
    review_status: ReviewStatus = "pending"


class Text(_Model):
    id: str
    raw: str
    normalized: str
    bbox: BBox
    angle_deg: Literal[0, 90, 180, 270] = 0
    confidence: float = 1.0
    role: Literal["device_code", "busbar_code", "parameter", "line_name", "bay_label", "pole_number",
                  "title_block", "other"]
    assigned_to: TextTarget | None = None


class DeviceCode(_Model):
    normalized: str
    voltage_kv: int | None = None
    bay_code: str | None = None
    suffix: str | None = None


class Transform(_Model):
    x: float
    y: float
    rotation_deg: Literal[0, 90, 180, 270] = 0
    mirror_x: bool = False
    scale: float = 1.0


class DevicePort(_Model):
    id: str
    role: str
    x: float
    y: float


class Device(_Model):
    id: str
    detector_class: str
    type: str
    bay_id: str | None = None
    code: DeviceCode | None = None
    label_text_ids: list[str] = Field(default_factory=list)
    bbox: BBox
    transform: Transform
    ports: list[DevicePort]
    drawing_state: SwitchState | None = None
    operational_state: SwitchState | None = None
    confidence: float = 1.0
    review_status: ReviewStatus = "pending"

    def port(self, port_id: str) -> DevicePort:
        for p in self.ports:
            if p.id == port_id:
                return p
        raise KeyError(f"{self.id} không có cổng {port_id}")


class Wire(_Model):
    id: str
    points: list[Point] = Field(min_length=2)
    stroke_width_px: float
    from_: WireEnd = Field(alias="from")
    to: WireEnd
    connection_status: Literal["confirmed", "candidate", "dangling"] = "confirmed"
    confidence: float = 1.0


class Node(_Model):
    id: str
    type: Literal["junction", "crossing_no_connect"]
    style: Literal["dot", "t_on_busbar", "t_plain", "plain_cross", "hop_arc"]
    position: Point
    connections: list[NodeConnection]
    confidence: float = 1.0
    review_status: ReviewStatus = "pending"


class Net(_Model):
    id: str
    kind: Literal["power", "ground"] = "power"
    voltage_kv: int | None = None
    members: list[NetMember]
    review_status: ReviewStatus = "pending"


class Issue(_Model):
    id: str
    type: IssueType
    severity: Literal["error", "warning", "info"]
    position: Point | None = None
    object_refs: list[AnyRef] = Field(default_factory=list)
    message: str
    resolution: str | None = None
    resolved_by: str | None = None


class SubstationTopologyGraph(_Model):
    schema_version: Literal["1.1"] = SCHEMA_VERSION
    provenance: Provenance
    page: Page
    bays: list[Bay] = Field(default_factory=list)
    busbars: list[Busbar] = Field(default_factory=list)
    texts: list[Text] = Field(default_factory=list)
    devices: list[Device] = Field(default_factory=list)
    wires: list[Wire] = Field(default_factory=list)
    nodes: list[Node] = Field(default_factory=list)
    nets: list[Net] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)

    # -------------------------------------------------------------- tiện ích

    def to_json(self, indent: int | None = 2) -> str:
        return self.model_dump_json(by_alias=True, exclude_none=True, indent=indent)

    @classmethod
    def from_json(cls, text: str) -> "SubstationTopologyGraph":
        return cls.model_validate(json.loads(text))

    def device(self, device_id: str) -> Device:
        for d in self.devices:
            if d.id == device_id:
                return d
        raise KeyError(device_id)

    def net_index(self) -> dict[tuple, str]:
        """Chỉ mục ngược ref → netId, dựng trong bộ nhớ (nets là nguồn chuẩn duy nhất)."""
        index: dict[tuple, str] = {}
        for net in self.nets:
            for m in net.members:
                index[m.key()] = net.id
        return index

    def existing_keys(self) -> set[tuple]:
        """Khóa của mọi đối tượng có thể được tham chiếu."""
        keys: set[tuple] = set()
        keys.update(("device", d.id) for d in self.devices)
        keys.update(("port", d.id, p.id) for d in self.devices for p in d.ports)
        keys.update(("wire", w.id) for w in self.wires)
        keys.update(("busbar", b.id) for b in self.busbars)
        keys.update(("node", n.id) for n in self.nodes)
        keys.update(("text", t.id) for t in self.texts)
        keys.update(("bay", b.id) for b in self.bays)
        return keys


# ------------------------------------------------------------------ bất biến (SDD 6.3)

class InvariantViolation(BaseModel):
    code: Literal["broken_reference", "duplicate_id", "net_not_partition", "wire_end_off_port",
                  "port_multiple_wires", "voltage_conflict"]
    message: str
    refs: list[tuple] = Field(default_factory=list)


def _all_refs(graph: SubstationTopologyGraph):
    """Sinh (ngữ cảnh, ref) cho mọi tham chiếu có kiểu trong đồ thị."""
    for b in graph.bays:
        for did in b.device_ids:
            yield f"bay {b.id}", DeviceRef(id=did)
    for b in graph.busbars:
        for tid in b.label_text_ids:
            yield f"busbar {b.id}", TextRef(id=tid)
    for t in graph.texts:
        if t.assigned_to is not None:
            yield f"text {t.id}", t.assigned_to
    for d in graph.devices:
        if d.bay_id is not None:
            yield f"device {d.id}", BayRef(id=d.bay_id)
        for tid in d.label_text_ids:
            yield f"device {d.id}", TextRef(id=tid)
    for w in graph.wires:
        for end in (w.from_, w.to):
            if not isinstance(end, DanglingRef):
                yield f"wire {w.id}", end
    for n in graph.nodes:
        for c in n.connections:
            yield f"node {n.id}", c
    for net in graph.nets:
        for m in net.members:
            yield f"net {net.id}", m
    for issue in graph.issues:
        for r in issue.object_refs:
            if not isinstance(r, DanglingRef):
                yield f"issue {issue.id}", r


def check_invariants(graph: SubstationTopologyGraph, end_tolerance_px: float = 1e-3) -> list[InvariantViolation]:
    """Kiểm tra bất biến 6.3-1, 2, 3, 4, 6 và trùng ID.

    Bất biến 6.3-5 (dựng lại nets cho đúng nets đang lưu) cần thư viện ký hiệu, nằm ở
    net_builder.check_nets_consistent.
    """
    out: list[InvariantViolation] = []

    groups = {"device": graph.devices, "wire": graph.wires, "busbar": graph.busbars, "node": graph.nodes,
              "text": graph.texts, "bay": graph.bays, "net": graph.nets, "issue": graph.issues}
    for kind, items in groups.items():
        seen: set[str] = set()
        for it in items:
            if it.id in seen:
                out.append(InvariantViolation(code="duplicate_id", message=f"Trùng id {kind} '{it.id}'",
                                              refs=[(kind, it.id)]))
            seen.add(it.id)
    for d in graph.devices:
        if len({p.id for p in d.ports}) != len(d.ports):
            out.append(InvariantViolation(code="duplicate_id", message=f"Thiết bị {d.id} có cổng trùng id",
                                          refs=[("device", d.id)]))

    # 6.3-1: tham chiếu tồn tại
    existing = graph.existing_keys()
    for ctx, ref in _all_refs(graph):
        if ref.key() not in existing:
            out.append(InvariantViolation(code="broken_reference", message=f"{ctx} trỏ tới {ref.key()} không tồn tại",
                                          refs=[ref.key()]))

    # 6.3-2: nets là phân hoạch của wires, busbars, ports
    must_cover = {("wire", w.id) for w in graph.wires} | {("busbar", b.id) for b in graph.busbars} \
        | {("port", d.id, p.id) for d in graph.devices for p in d.ports}
    count: dict[tuple, int] = {}
    for net in graph.nets:
        for m in net.members:
            count[m.key()] = count.get(m.key(), 0) + 1
    for k in sorted(must_cover, key=str):
        n = count.get(k, 0)
        if n != 1:
            out.append(InvariantViolation(code="net_not_partition", message=f"{k} thuộc {n} net (phải đúng 1)",
                                          refs=[k]))

    # 6.3-3 và 6.3-4: đầu dây trùng cổng; mỗi cổng tối đa 1 dây
    ports = {(d.id, p.id): p for d in graph.devices for p in d.ports}
    wires_per_port: dict[tuple, list[str]] = {}
    for w in graph.wires:
        for end, pt in ((w.from_, w.points[0]), (w.to, w.points[-1])):
            if not isinstance(end, PortRef):
                continue
            wires_per_port.setdefault((end.device_id, end.port_id), []).append(w.id)
            port = ports.get((end.device_id, end.port_id))
            if port is None:
                continue
            dist = ((pt[0] - port.x) ** 2 + (pt[1] - port.y) ** 2) ** 0.5
            if dist > end_tolerance_px:
                out.append(InvariantViolation(
                    code="wire_end_off_port",
                    message=f"Đầu dây {w.id} cách cổng {end.device_id}/{end.port_id} {dist:.4g} px",
                    refs=[("wire", w.id), end.key()]))
    for (did, pid), wids in wires_per_port.items():
        if len(wids) > 1:
            out.append(InvariantViolation(code="port_multiple_wires",
                                          message=f"Cổng {did}/{pid} nối trực tiếp {len(wids)} dây: {wids}",
                                          refs=[("port", did, pid)] + [("wire", w) for w in wids]))

    # 6.3-6: cấp điện áp trong một net power phải khớp
    busbar_kv = {b.id: b.voltage_kv for b in graph.busbars}
    for net in graph.nets:
        if net.kind != "power":
            continue
        kvs = {busbar_kv[m.id] for m in net.members if isinstance(m, BusbarRef) and busbar_kv.get(m.id)}
        if net.voltage_kv is not None:
            kvs.add(net.voltage_kv)
        if len(kvs) > 1:
            out.append(InvariantViolation(code="voltage_conflict",
                                          message=f"Net {net.id} có nhiều cấp điện áp {sorted(kvs)}",
                                          refs=[("net", net.id)]))
    return out
