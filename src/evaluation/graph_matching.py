"""So khớp đồ thị dự đoán với đáp án (SDD 9.2).

1. Thiết bị: Hungarian trên 1 − IoU(bbox), chỉ nhận cặp IoU ≥ 0,5; khác `type` vẫn ghép nhưng là lỗi
   phân loại.
2. Cổng: theo id khi cùng lớp, theo `role` khi khác lớp; thiết bị đối xứng điện (`orientationSensitive`
   = false, 2 cổng a/b) thử cả hai hoán vị, chọn hoán vị khớp nhiều cặp hơn.
3. Terminal-pair F1: tập cặp đầu cực cùng Net; thanh cái là "cổng ảo" của chính nó.
4. Ngăn lộ đúng hoàn toàn.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from src.core.schema import SubstationTopologyGraph
from src.core.symbol_lib import SymbolLibrary, default_library

Terminal = tuple   # ("port", deviceId, portId) | ("busbar", busbarId)


def iou(a, b) -> float:
    ix = max(0.0, min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = a[2] * a[3] + b[2] * b[3] - inter
    return inter / union if union > 0 else 0.0


def hungarian_pairs(cost: np.ndarray, max_cost: float) -> list[tuple[int, int]]:
    if cost.size == 0:
        return []
    rows, cols = linear_sum_assignment(cost)
    return [(int(i), int(j)) for i, j in zip(rows, cols) if cost[i, j] <= max_cost]


def busbar_distance(a: list, b: list) -> float:
    """Khoảng cách giữa hai thanh cái: trung bình khoảng cách từ hai đầu mỗi bên tới đoạn bên kia."""
    from src.perception.busbar_classifier import nearest_on_polyline
    ds = [nearest_on_polyline(p, b)[0] for p in (a[0], a[-1])] + [nearest_on_polyline(p, a)[0] for p in (b[0], b[-1])]
    return float(np.mean(ds))


@dataclass
class GraphMatch:
    devices: dict[str, str] = field(default_factory=dict)         # pred id → gt id
    busbars: dict[str, str] = field(default_factory=dict)
    port_map: dict[tuple, tuple] = field(default_factory=dict)    # pred terminal → gt terminal
    unmatched_pred: list[str] = field(default_factory=list)
    unmatched_gt: list[str] = field(default_factory=list)
    type_errors: list[tuple[str, str]] = field(default_factory=list)


def match_devices(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph, iou_thr: float = 0.5) -> dict[str, str]:
    P, G = pred.devices, gt.devices
    if not P or not G:
        return {}
    cost = np.ones((len(P), len(G)))
    gb = np.array([g.bbox for g in G])
    for i, p in enumerate(P):
        x, y, w, h = p.bbox
        ix = np.clip(np.minimum(x + w, gb[:, 0] + gb[:, 2]) - np.maximum(x, gb[:, 0]), 0, None)
        iy = np.clip(np.minimum(y + h, gb[:, 1] + gb[:, 3]) - np.maximum(y, gb[:, 1]), 0, None)
        inter = ix * iy
        cost[i] = 1 - inter / np.maximum(w * h + gb[:, 2] * gb[:, 3] - inter, 1e-9)
    return {P[i].id: G[j].id for i, j in hungarian_pairs(cost, 1 - iou_thr)}


def match_busbars(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph, max_dist_px: float) -> dict[str, str]:
    P, G = pred.busbars, gt.busbars
    if not P or not G:
        return {}
    cost = np.array([[busbar_distance(p.polyline, g.polyline) for g in G] for p in P])
    return {P[i].id: G[j].id for i, j in hungarian_pairs(cost, max_dist_px)}


def net_terminals(graph: SubstationTopologyGraph) -> dict[str, list[Terminal]]:
    out = {}
    for n in graph.nets:
        out[n.id] = [m.key() for m in n.members if m.kind in ("port", "busbar")]
    return out


def terminal_pairs(nets: dict[str, list[Terminal]]) -> set[frozenset]:
    pairs = set()
    for terms in nets.values():
        for a, b in itertools.combinations(sorted(set(terms)), 2):
            pairs.add(frozenset((a, b)))
    return pairs


def build_match(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph, lib: SymbolLibrary | None = None,
                iou_thr: float = 0.5) -> GraphMatch:
    lib = lib or default_library()
    m = GraphMatch()
    m.devices = match_devices(pred, gt, iou_thr)
    ppm = gt.page.px_per_mm
    m.busbars = match_busbars(pred, gt, 3.0 * ppm)
    gdev = {d.id: d for d in gt.devices}
    m.unmatched_pred = [d.id for d in pred.devices if d.id not in m.devices]
    matched_gt = set(m.devices.values())
    m.unmatched_gt = [d.id for d in gt.devices if d.id not in matched_gt]
    gt_pairs = terminal_pairs(net_terminals(gt))

    for pb, gb in m.busbars.items():
        m.port_map[("busbar", pb)] = ("busbar", gb)
    swappable = []
    for d in pred.devices:
        gid = m.devices.get(d.id)
        if gid is None:
            continue
        g = gdev[gid]
        if g.type != d.type:
            m.type_errors.append((d.id, gid))
        if d.detector_class == g.detector_class:
            for p in d.ports:
                if any(q.id == p.id for q in g.ports):
                    m.port_map[("port", d.id, p.id)] = ("port", gid, p.id)
        else:
            by_role = {q.role: q.id for q in g.ports}
            for p in d.ports:
                if p.role in by_role:
                    m.port_map[("port", d.id, p.id)] = ("port", gid, by_role[p.role])
        sym = lib[d.detector_class]
        if not sym.orientation_sensitive and len(d.ports) == 2 and len(g.ports) == 2:
            swappable.append(d)

    # hoán vị a/b của thiết bị đối xứng điện: chọn cách khớp nhiều cặp đáp án hơn (tham lam, 2 lượt)
    pred_nets = net_terminals(pred)
    term_net = {t: n for n, ts in pred_nets.items() for t in ts}
    for _ in range(2):
        for d in swappable:
            k0, k1 = ("port", d.id, d.ports[0].id), ("port", d.id, d.ports[1].id)
            if k0 not in m.port_map or k1 not in m.port_map:
                continue

            def agree(mapping) -> int:
                n = 0
                for k in (k0, k1):
                    mk = mapping[k]
                    for t in pred_nets.get(term_net.get(k), ()):
                        if t != k and t in mapping and frozenset((mk, mapping[t])) in gt_pairs:
                            n += 1
                return n

            swapped = dict(m.port_map)
            swapped[k0], swapped[k1] = m.port_map[k1], m.port_map[k0]
            if agree(swapped) > agree(m.port_map):
                m.port_map = swapped
    return m


def mapped_pairs(pred: SubstationTopologyGraph, m: GraphMatch) -> set[frozenset]:
    """Cặp đầu cực của đồ thị dự đoán, đổi sang id đáp án (đầu cực không ghép giữ id riêng → luôn là FP)."""
    out = set()
    for terms in net_terminals(pred).values():
        mapped = sorted({m.port_map.get(t, ("pred",) + t) for t in terms}, key=str)
        for a, b in itertools.combinations(mapped, 2):
            out.add(frozenset((a, b)))
    return out


def prf(tp: int, n_pred: int, n_gt: int) -> dict:
    p = tp / n_pred if n_pred else 0.0
    r = tp / n_gt if n_gt else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": p, "recall": r, "f1": f, "tp": tp, "nPred": n_pred, "nGt": n_gt}


def terminal_pair_f1(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph, m: GraphMatch) -> dict:
    P = mapped_pairs(pred, m)
    G = terminal_pairs(net_terminals(gt))
    return prf(len(P & G), len(P), len(G))


def bay_accuracy(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph, m: GraphMatch) -> dict:
    """Ngăn đúng hoàn toàn: mọi thiết bị ghép đúng loại, đúng mã; mọi cặp cổng cùng/khác Net như đáp án."""
    inv = {g: p for p, g in m.devices.items()}
    pdev = {d.id: d for d in pred.devices}
    gidx, pidx = gt.net_index(), pred.net_index()
    inv_ports = {g: p for p, g in m.port_map.items()}
    correct, details = 0, {}
    for bay in gt.bays:
        ok, reason = True, ""
        ports = []
        for gid in bay.device_ids:
            g = gt.device(gid)
            pid = inv.get(gid)
            if pid is None:
                ok, reason = False, f"thiếu {gid}"
                break
            p = pdev[pid]
            gcode = g.code.normalized if g.code else None
            pcode = p.code.normalized if p.code else None
            if p.type != g.type or gcode != pcode:
                ok, reason = False, f"{gid}: loại/mã {p.type}/{pcode} ≠ {g.type}/{gcode}"
                break
            ports += [("port", gid, q.id) for q in g.ports]
        if ok:
            for a, b in itertools.combinations(ports, 2):
                same_gt = gidx.get(a) == gidx.get(b)
                pa, pb = inv_ports.get(a), inv_ports.get(b)
                same_pred = pa is not None and pb is not None and pidx.get(pa) is not None \
                    and pidx.get(pa) == pidx.get(pb)
                if same_gt != same_pred:
                    ok, reason = False, f"cặp {a[1]}/{a[2]}–{b[1]}/{b[2]} sai đấu nối"
                    break
        correct += ok
        details[bay.id] = reason or "ok"
    n = len(gt.bays)
    return {"correct": correct, "total": n, "ratio": correct / n if n else 0.0, "details": details}


def net_split_merge(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph, m: GraphMatch) -> dict:
    """Đứt đoạn: Net đáp án bị tách vào ≥ 2 Net dự đoán; dính: Net dự đoán chứa đầu cực của ≥ 2 Net đáp án."""
    gidx = {t: n for n, ts in net_terminals(gt).items() for t in ts}
    pidx = {t: n for n, ts in net_terminals(pred).items() for t in ts}
    inv = {g: p for p, g in m.port_map.items()}
    split = 0
    gt_nets = net_terminals(gt)
    for n, ts in gt_nets.items():
        pn = {pidx.get(inv[t]) for t in ts if t in inv and inv[t] in pidx}
        split += len(pn) > 1
    merge = 0
    for n, ts in net_terminals(pred).items():
        gn = {gidx.get(m.port_map[t]) for t in ts if t in m.port_map and m.port_map[t] in gidx}
        merge += len(gn) > 1
    return {"falseSplits": split, "falseMerges": merge, "gtNets": len(gt_nets), "predNets": len(pred.nets)}


def nearest(points_a, points_b, max_d: float) -> list[tuple[int, int]]:
    if not points_a or not points_b:
        return []
    A, B = np.asarray(points_a, float), np.asarray(points_b, float)
    cost = np.sqrt(((A[:, None, :] - B[None, :, :]) ** 2).sum(-1))
    return hungarian_pairs(cost, max_d)


def to_original_scale(graph: SubstationTopologyGraph) -> SubstationTopologyGraph:
    """Đồ thị pipeline nằm ở hệ ảnh đã chuẩn hóa tỉ lệ (page.normalizationScale = k); đưa về hệ ảnh gốc để so
    với đáp án. k = 1 thì trả nguyên."""
    k = graph.page.normalization_scale
    if abs(k - 1.0) < 1e-9:
        return graph
    g = graph.model_copy(deep=True)
    f = 1.0 / k

    def pt(p):
        return (p[0] * f, p[1] * f)

    g.page.width, g.page.height = round(g.page.width * f), round(g.page.height * f)
    g.page.px_per_mm *= f
    g.page.normalization_scale = 1.0
    for d in g.devices:
        d.bbox = tuple(v * f for v in d.bbox)
        d.transform.x, d.transform.y, d.transform.scale = d.transform.x * f, d.transform.y * f, d.transform.scale * f
        for p in d.ports:
            p.x, p.y = p.x * f, p.y * f
    for w in g.wires:
        w.points = [pt(p) for p in w.points]
        w.stroke_width_px *= f
        for attr in ("from_", "to"):
            ref = getattr(w, attr)
            if ref.kind == "dangling":
                setattr(w, attr, ref.model_copy(update={"position": pt(ref.position)}))
    for b in g.busbars:
        b.polyline = [pt(p) for p in b.polyline]
        b.stroke_width_px *= f
    for n in g.nodes:
        n.position = pt(n.position)
    for t in g.texts:
        t.bbox = tuple(v * f for v in t.bbox)
    for i in g.issues:
        if i.position is not None:
            i.position = pt(i.position)
    return g
