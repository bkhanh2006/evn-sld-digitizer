"""NetBuilder (dựng Net tĩnh) và Energizer (tô vùng mang điện) — SDD 8.3.3.

Dùng chung cho bộ sinh dữ liệu (tính nets của đáp án) và pipeline (tính nets của kết quả),
nên định nghĩa Net của hai bên luôn giống nhau. Hành vi của từng lớp lấy từ thư viện ký hiệu
(netBehavior, conduction), không viết cứng tên lớp trong mã.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from src.core.schema import (BusbarRef, Issue, Net, NodeRef, PortRef, SubstationTopologyGraph, WireRef)
from src.core.symbol_lib import SymbolLibrary

# Cổng không tham gia dẫn điện khi tô vùng (trung tính MBA nối đất qua thiết bị riêng)
NON_CONDUCTING_PORT_ROLES = frozenset({"neutral"})
# Loại thiết bị đổi cấp điện áp: không lan voltageKv của mã sang các Net của nó
VOLTAGE_CHANGING_TYPES = frozenset({"transformer"})
# Loại thiết bị làm nguồn mặc định khi tô vùng mang điện (SDD 8.3.3)
SOURCE_DEVICE_TYPE = "line_terminal"


class _UnionFind:
    def __init__(self) -> None:
        self.parent: dict[tuple, tuple] = {}

    def add(self, k: tuple) -> None:
        self.parent.setdefault(k, k)

    def find(self, k: tuple) -> tuple:
        root = k
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[k] != root:
            self.parent[k], k = root, self.parent[k]
        return root

    def union(self, a: tuple, b: tuple) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # gốc nhỏ hơn làm đại diện, để kết quả không phụ thuộc thứ tự duyệt
            if rb < ra:
                ra, rb = rb, ra
            self.parent[rb] = ra


def _member_ref(k: tuple) -> WireRef | BusbarRef | PortRef:
    if k[0] == "wire":
        return WireRef(id=k[1])
    if k[0] == "busbar":
        return BusbarRef(id=k[1])
    return PortRef(device_id=k[1], port_id=k[2])


@dataclass
class NetBuildResult:
    nets: list[Net]
    issues: list[Issue] = field(default_factory=list)


class NetBuilder:
    def __init__(self, symbol_lib: SymbolLibrary):
        self.lib = symbol_lib

    def build(self, graph: SubstationTopologyGraph) -> NetBuildResult:
        uf = _UnionFind()
        for w in graph.wires:
            uf.add(("wire", w.id))
        for b in graph.busbars:
            uf.add(("busbar", b.id))
        for d in graph.devices:
            for p in d.ports:
                uf.add(("port", d.id, p.id))

        pending_keys: set[tuple] = set()
        node_by_id = {n.id: n for n in graph.nodes}

        # 2. dây ↔ cổng (confirmed hoặc candidate); dây ↔ nút junction
        for w in graph.wires:
            wk = ("wire", w.id)
            for end in (w.from_, w.to):
                if isinstance(end, PortRef) and w.connection_status in ("confirmed", "candidate"):
                    uf.union(wk, end.key())
                    if w.connection_status == "candidate":
                        pending_keys.add(wk)
                elif isinstance(end, NodeRef):
                    node = node_by_id.get(end.id)
                    if node is not None and node.type == "junction":
                        for c in node.connections:
                            uf.union(wk, c.key())

        # 3. mọi phần tử nối vào junction; 4. crossing_no_connect: không làm gì
        for n in graph.nodes:
            if n.type == "junction" and n.connections:
                first = n.connections[0].key()
                for c in n.connections[1:]:
                    uf.union(first, c.key())

        # 5. hành vi theo lớp
        for d in graph.devices:
            if self.lib[d.detector_class].net_behavior == "passthrough" and len(d.ports) > 1:
                first = ("port", d.id, d.ports[0].id)
                for p in d.ports[1:]:
                    uf.union(first, ("port", d.id, p.id))

        groups: dict[tuple, list[tuple]] = {}
        for k in uf.parent:
            groups.setdefault(uf.find(k), []).append(k)

        ground_ports = {("port", d.id, p.id) for d in graph.devices
                        if self.lib[d.detector_class].type == "ground" for p in d.ports}
        voltage_votes = self._voltage_votes(graph)

        issues: list[Issue] = []
        nets: list[Net] = []
        for i, root in enumerate(sorted(groups), start=1):
            members = sorted(groups[root])
            net_id = f"net-{i:04d}"
            kvs = sorted({kv for k in members for kv in voltage_votes.get(k, ())})
            if len(kvs) > 1:
                issues.append(Issue(id=f"issue-net-{i:04d}-voltage", type="voltage_conflict", severity="error",
                                    object_refs=[_member_ref(k) for k in members],
                                    message=f"Net {net_id} nhận nhiều cấp điện áp từ mã: {kvs} kV"))
            nets.append(Net(
                id=net_id,
                kind="ground" if any(k in ground_ports for k in members) else "power",
                voltage_kv=kvs[0] if len(kvs) == 1 else None,
                members=[_member_ref(k) for k in members],
                review_status="pending" if any(k in pending_keys for k in members) else "confirmed",
            ))

        issues.extend(self._bypassed_devices(graph, nets))
        return NetBuildResult(nets=nets, issues=issues)

    def _voltage_votes(self, graph: SubstationTopologyGraph) -> dict[tuple, set[int]]:
        """Cấp điện áp từ mã của thanh cái và thiết bị (trừ thiết bị đổi cấp áp)."""
        votes: dict[tuple, set[int]] = {}
        for b in graph.busbars:
            if b.voltage_kv is not None:
                votes.setdefault(("busbar", b.id), set()).add(b.voltage_kv)
        for d in graph.devices:
            if d.code is None or d.code.voltage_kv is None:
                continue
            if self.lib[d.detector_class].type in VOLTAGE_CHANGING_TYPES:
                continue
            for p in d.ports:
                votes.setdefault(("port", d.id, p.id), set()).add(d.code.voltage_kv)
        return votes

    def _bypassed_devices(self, graph: SubstationTopologyGraph, nets: list[Net]) -> list[Issue]:
        index = {m.key(): n.id for n in nets for m in n.members}
        out = []
        for d in graph.devices:
            if self.lib[d.detector_class].net_behavior != "split" or len(d.ports) < 2:
                continue
            port_nets = [index[("port", d.id, p.id)] for p in d.ports]
            if len(set(port_nets)) < len(port_nets):
                out.append(Issue(id=f"issue-bypass-{d.id}", type="device_bypassed", severity="info",
                                 object_refs=[PortRef(device_id=d.id, port_id=p.id) for p in d.ports],
                                 message=f"Hai cổng của {d.id} thuộc cùng một Net (có thể là DCL nối tắt hợp lệ)"))
        return out


def nets_as_sets(nets: list[Net]) -> set[frozenset[tuple]]:
    """Dạng so sánh được của nets, bỏ qua id và thứ tự."""
    return {frozenset(m.key() for m in n.members) for n in nets}


def check_nets_consistent(graph: SubstationTopologyGraph, symbol_lib: SymbolLibrary) -> bool:
    """Bất biến 6.3-5: dựng lại nets từ wires/nodes/devices phải ra đúng nets đang lưu."""
    return nets_as_sets(NetBuilder(symbol_lib).build(graph).nets) == nets_as_sets(graph.nets)


# ---------------------------------------------------------------------------- Energizer

@dataclass(frozen=True)
class NetEnergization:
    energized: bool | None      # None = không xác định (đi qua thiết bị trạng thái unknown)
    earthed: bool


@dataclass
class EnergizationResult:
    states: dict[str, NetEnergization]
    conflicts: list[str]        # netId vừa có điện vừa nối đất (energized_earthed_conflict)


class Energizer:
    """BFS trên đồ thị thiết bị: đỉnh là Net, cạnh là thiết bị split (SDD 8.3.3)."""

    def __init__(self, symbol_lib: SymbolLibrary):
        self.lib = symbol_lib

    def default_sources(self, graph: SubstationTopologyGraph) -> list[str]:
        """Các Net chứa đầu đường dây ở cấp điện áp cao nhất."""
        index = graph.net_index()
        kv_of = {n.id: n.voltage_kv for n in graph.nets}
        terminals = [index[("port", d.id, p.id)] for d in graph.devices
                     if self.lib[d.detector_class].type == SOURCE_DEVICE_TYPE for p in d.ports]
        known = [kv_of[n] for n in terminals if kv_of.get(n) is not None]
        if not known:
            return sorted(set(terminals))
        top = max(known)
        return sorted({n for n in terminals if kv_of.get(n) == top})

    def energize(self, graph: SubstationTopologyGraph, sources: list[str] | None = None) -> EnergizationResult:
        index = graph.net_index()
        if sources is None:
            sources = self.default_sources(graph)

        # cạnh: (tập Net của thiết bị, trạng thái dẫn: True / False / None)
        edges: list[tuple[list[str], bool | None]] = []
        earthed: set[str] = set()
        for d in graph.devices:
            sym = self.lib[d.detector_class]
            port_nets = [index[("port", d.id, p.id)] for p in d.ports if p.role not in NON_CONDUCTING_PORT_ROLES]
            state = d.operational_state or d.drawing_state or "unknown"
            if sym.conduction == "earthing_by_state" and state == "closed":
                earthed.update(port_nets)
            if sym.net_behavior != "split" or len(port_nets) < 2:
                continue
            if sym.conduction == "always":
                edges.append((port_nets, True))
            elif sym.conduction == "by_state":
                edges.append((port_nets, {"closed": True, "open": False}.get(state)))
        for n in graph.nets:
            if n.kind == "ground":
                earthed.add(n.id)

        adjacency: dict[str, list[tuple[list[str], bool | None]]] = {}
        for nets_of_dev, conducts in edges:
            for n in nets_of_dev:
                adjacency.setdefault(n, []).append((nets_of_dev, conducts))

        def reach(start: list[str], allow_unknown: bool) -> set[str]:
            seen = set(start)
            queue = deque(start)
            while queue:
                cur = queue.popleft()
                for nets_of_dev, conducts in adjacency.get(cur, ()):
                    if conducts is True or (allow_unknown and conducts is None):
                        for nxt in nets_of_dev:
                            if nxt not in seen:
                                seen.add(nxt)
                                queue.append(nxt)
            return seen

        definite = reach(list(sources), allow_unknown=False)
        maybe = reach(list(sources), allow_unknown=True)

        states: dict[str, NetEnergization] = {}
        conflicts: list[str] = []
        for n in graph.nets:
            if n.id in definite:
                energized: bool | None = True
            elif n.id in maybe:
                energized = None
            else:
                energized = False
            states[n.id] = NetEnergization(energized=energized, earthed=n.id in earthed)
            if energized is True and n.id in earthed:
                conflicts.append(n.id)
        return EnergizationResult(states=states, conflicts=conflicts)
