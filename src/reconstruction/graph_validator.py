"""EVNRuleValidator (SDD 8.3.4): bất biến 6.3 + luật nghiệp vụ → danh sách issue.

Luật chỉ đánh dấu mâu thuẫn, không tự sửa hay tạo kết nối (SDD 1.1-4).
"""
from __future__ import annotations

from collections import defaultdict

from src.core.evn_codes import EVNCodeGrammar, default_grammar
from src.core.schema import BusbarRef, DeviceRef, Issue, PortRef, SubstationTopologyGraph, check_invariants
from src.core.symbol_lib import SymbolLibrary, default_library

# Loại thiết bị bắt buộc có mã vận hành (SDD 8.3.4)
LABEL_REQUIRED_TYPES = frozenset({"circuit_breaker", "drawout_breaker", "disconnector", "earthing_switch"})
SEVERITY = {
    "broken_reference": "error", "duplicate_code": "error", "low_resolution_input": "error",
    "busbar_suffix_mismatch": "error", "voltage_conflict": "error", "stage_failed": "error",
    "dangling_wire_end": "warning", "unconnected_port": "warning", "ambiguous_port_connection": "warning",
    "ambiguous_crossing": "warning", "ambiguous_orientation": "warning", "class_ambiguity": "warning",
    "missing_label": "warning", "bay_chain_break": "warning",
    "device_bypassed": "info", "unknown_code_suffix": "info", "unexpected_symbol_scale": "info",
}


class EVNRuleValidator:
    def __init__(self, symbol_lib: SymbolLibrary | None = None, grammar: EVNCodeGrammar | None = None,
                 crossing_confidence: float = 0.6):
        self.lib = symbol_lib or default_library()
        self.grammar = grammar or default_grammar()
        self.crossing_confidence = crossing_confidence

    def validate(self, graph: SubstationTopologyGraph, issues: list[Issue] | None = None,
                 end_tolerance_px: float = 1e-3) -> list[Issue]:
        out: list[Issue] = list(issues or [])

        def add(kind: str, key: str, message: str, refs=(), position=None) -> None:
            out.append(Issue(id=f"issue-{kind}-{key}", type=kind, severity=SEVERITY[kind], position=position,
                             object_refs=list(refs), message=message))

        for v in check_invariants(graph, end_tolerance_px):
            if v.code == "broken_reference":
                add("broken_reference", str(len(out)), v.message)

        # mã trùng
        by_code = defaultdict(list)
        for d in graph.devices:
            if d.code is not None:
                by_code[d.code.normalized].append(d)
        for code, devs in by_code.items():
            if len(devs) > 1:
                add("duplicate_code", code, f"{len(devs)} thiết bị cùng mã {code}",
                    [DeviceRef(id=d.id) for d in devs])

        # thiếu mã, hậu tố lạ
        for d in graph.devices:
            if d.type in LABEL_REQUIRED_TYPES and d.code is None:
                add("missing_label", d.id, f"{d.id} ({d.type}) chưa có mã vận hành", [DeviceRef(id=d.id)],
                    (d.transform.x, d.transform.y))
            if d.code is not None:
                parsed = self.grammar.parse(d.code.normalized)
                if parsed is not None and parsed.unknown_suffix:
                    add("unknown_code_suffix", d.id, f"Hậu tố của mã {d.code.normalized} không theo luật Điều 59–60",
                        [DeviceRef(id=d.id)])

        # nút giao độ tin cậy thấp
        for n in graph.nodes:
            if n.type == "crossing_no_connect" and n.confidence < self.crossing_confidence:
                add("ambiguous_crossing", n.id, f"Điểm giao {n.id} chưa chắc là giao không nối", position=n.position)

        index = graph.net_index()
        out += self._busbar_suffix(graph, index, add)
        self._bay_chain(graph, index, add)
        return _dedupe(out)

    def _busbar_suffix(self, graph, index, add) -> list[Issue]:
        """DCL hậu tố -1/-2 phải có cổng cùng Net với thanh cái C{v}1/C{v}2 (khi cấp đó có ≥ 2 thanh cái)."""
        buses_by_kv = defaultdict(dict)
        for b in graph.busbars:
            if b.code and b.voltage_kv is not None:
                buses_by_kv[b.voltage_kv][b.code[-1]] = b
        for d in graph.devices:
            if d.type != "disconnector" or d.code is None or d.code.voltage_kv is None:
                continue
            buses = buses_by_kv.get(d.code.voltage_kv, {})
            if len(buses) < 2 or d.code.suffix not in buses:
                continue
            target = index.get(("busbar", buses[d.code.suffix].id))
            port_nets = {index.get(("port", d.id, p.id)) for p in d.ports}
            if target not in port_nets:
                add("busbar_suffix_mismatch", d.id,
                    f"{d.code.normalized} có hậu tố -{d.code.suffix} nhưng không nối thanh cái "
                    f"{buses[d.code.suffix].code}",
                    [DeviceRef(id=d.id), BusbarRef(id=buses[d.code.suffix].id)], (d.transform.x, d.transform.y))
        return []

    def _bay_chain(self, graph, index, add) -> None:
        """Thiết bị cùng ngăn phải liên thông trong đồ thị thiết bị khi bỏ qua thanh cái."""
        bus_nets = {index[("busbar", b.id)] for b in graph.busbars if ("busbar", b.id) in index}
        parent: dict[str, str] = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for d in graph.devices:
            nets = [index.get(("port", d.id, p.id)) for p in d.ports]
            nets = [n for n in nets if n is not None and n not in bus_nets]
            for n in nets[1:]:
                parent[find(n)] = find(nets[0])
        for bay in graph.bays:
            comps = set()
            for did in bay.device_ids:
                d = graph.device(did)
                nets = [index.get(("port", d.id, p.id)) for p in d.ports]
                nets = [n for n in nets if n is not None and n not in bus_nets]
                if nets:
                    comps.add(find(nets[0]))
            if len(comps) > 1:
                add("bay_chain_break", bay.id, f"Ngăn {bay.code} bị đứt thành {len(comps)} phần",
                    [DeviceRef(id=did) for did in bay.device_ids])


def _dedupe(issues: list[Issue]) -> list[Issue]:
    seen: dict[str, int] = {}
    out = []
    for i in issues:
        if i.id in seen:
            seen[i.id] += 1
            i = i.model_copy(update={"id": f"{i.id}-{seen[i.id]}"})
        else:
            seen[i.id] = 0
        out.append(i)
    return out
