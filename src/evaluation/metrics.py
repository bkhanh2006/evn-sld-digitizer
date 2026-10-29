"""Chỉ số đánh giá 4 mức (SDD 9.1, báo cáo định hướng mục 4).

1. Ký hiệu: AP@0.5, mAP@0.5:0.95 (nội suy 101 điểm kiểu COCO), recall theo lớp tại điểm làm việc,
   độ chính xác hướng (θ, mirrorX) trên lớp bất đối xứng.
2. Chữ & ghép nhãn: exact match chuỗi mã sau chuẩn hóa, P/R/F1 cặp (mã – thiết bị).
3. Dây: Dice/IoU mask (vẽ lại dây + thanh cái từ hai đồ thị cùng độ dày), Net bị tách/dính, độ chính
   xác loại điểm giao, recall giao không nối.
4. Đồ thị: Terminal-pair P/R/F1, tỉ lệ ngăn lộ đúng hoàn toàn.

`evaluate_graph` cho một bản vẽ; `aggregate` gộp nhiều bản vẽ (cộng dồn số đếm rồi mới chia).
"""
from __future__ import annotations

from collections import defaultdict

import cv2
import numpy as np

from src.core.schema import SubstationTopologyGraph
from src.core.symbol_lib import SymbolLibrary, default_library
from src.evaluation.graph_matching import (bay_accuracy, build_match, hungarian_pairs, iou, nearest, net_split_merge,
                                           prf, terminal_pair_f1, to_original_scale)

CODE_ROLES = ("device_code", "busbar_code")
IOU_THRESHOLDS = np.round(np.arange(0.5, 0.96, 0.05), 2)


# ---------------------------------------------------------------------------- mức 1: ký hiệu

def detection_records(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph,
                      iou_thr: float) -> dict[str, dict]:
    """Theo lớp detector: danh sách (điểm, đúng?) và số đáp án, ghép tham lam theo điểm giảm dần."""
    out: dict[str, dict] = defaultdict(lambda: {"records": [], "nGt": 0})
    by_cls_gt = defaultdict(list)
    for g in gt.devices:
        by_cls_gt[g.detector_class].append(g)
        out[g.detector_class]["nGt"] += 1
    by_cls_pred = defaultdict(list)
    for p in pred.devices:
        by_cls_pred[p.detector_class].append(p)
    for cls, preds in by_cls_pred.items():
        used: set[str] = set()
        for p in sorted(preds, key=lambda d: -d.confidence):
            best, bid = 0.0, None
            for g in by_cls_gt.get(cls, ()):
                if g.id in used:
                    continue
                v = iou(p.bbox, g.bbox)
                if v > best:
                    best, bid = v, g.id
            ok = best >= iou_thr
            if ok:
                used.add(bid)
            out[cls]["records"].append((p.confidence, ok))
    return dict(out)


def average_precision(records: list[tuple[float, bool]], n_gt: int) -> float:
    if n_gt == 0:
        return float("nan")
    if not records:
        return 0.0
    recs = sorted(records, key=lambda r: -r[0])
    tp = np.cumsum([r[1] for r in recs])
    fp = np.cumsum([not r[1] for r in recs])
    recall = tp / n_gt
    precision = tp / np.maximum(tp + fp, 1)
    for i in range(len(precision) - 2, -1, -1):
        precision[i] = max(precision[i], precision[i + 1])
    ap = 0.0
    for r in np.linspace(0, 1, 101):
        idx = np.searchsorted(recall, r, side="left")
        ap += precision[idx] if idx < len(precision) else 0.0
    return ap / 101


def orientation_counts(pred, gt, match: dict[str, str], lib: SymbolLibrary) -> tuple[int, int]:
    gdev = {d.id: d for d in gt.devices}
    ok = n = 0
    for p in pred.devices:
        gid = match.get(p.id)
        if gid is None:
            continue
        g = gdev[gid]
        if g.detector_class != p.detector_class or not lib[p.detector_class].orientation_sensitive:
            continue
        n += 1
        ok += (p.transform.rotation_deg == g.transform.rotation_deg and p.transform.mirror_x == g.transform.mirror_x)
    return ok, n


# ---------------------------------------------------------------------------- mức 2: chữ & nhãn

def match_texts(pred, gt, iou_thr: float = 0.3) -> dict[str, str]:
    P = [t for t in pred.texts if t.role != "title_block"]
    G = [t for t in gt.texts if t.role != "title_block"]
    if not P or not G:
        return {}
    cost = np.array([[1 - iou(p.bbox, g.bbox) for g in G] for p in P])
    return {P[i].id: G[j].id for i, j in hungarian_pairs(cost, 1 - iou_thr)}


def text_counts(pred, gt, tmatch: dict[str, str]) -> dict:
    inv = {g: p for p, g in tmatch.items()}
    ptext = {t.id: t for t in pred.texts}
    code_ok = code_n = all_ok = all_n = 0
    for g in gt.texts:
        if g.role == "title_block":
            continue
        p = ptext.get(inv.get(g.id, ""))
        hit = p is not None and p.normalized == g.normalized
        all_n += 1
        all_ok += hit
        if g.role in CODE_ROLES:
            code_n += 1
            code_ok += hit
    return {"codeExact": code_ok, "codeTotal": code_n, "allExact": all_ok, "allTotal": all_n}


def label_counts(pred, gt, dmatch: dict[str, str], tmatch: dict[str, str]) -> dict:
    gt_pairs = {(t.id, t.assigned_to.id) for t in gt.texts
                if t.role == "device_code" and t.assigned_to is not None and t.assigned_to.kind == "device"}
    pred_pairs = {(tmatch.get(t.id, "p:" + t.id), dmatch.get(t.assigned_to.id, "p:" + t.assigned_to.id))
                  for t in pred.texts
                  if t.role == "device_code" and t.assigned_to is not None and t.assigned_to.kind == "device"}
    tp = len(gt_pairs & pred_pairs)
    # mã của thiết bị: thiết bị đáp án có mã → thiết bị dự đoán ghép được có đúng mã không
    pdev = {d.id: d for d in pred.devices}
    inv = {g: p for p, g in dmatch.items()}
    code_ok = code_n = 0
    for g in gt.devices:
        if g.code is None:
            continue
        code_n += 1
        p = pdev.get(inv.get(g.id, ""))
        code_ok += p is not None and p.code is not None and p.code.normalized == g.code.normalized
    return {"labelTp": tp, "labelPred": len(pred_pairs), "labelGt": len(gt_pairs),
            "deviceCodeOk": code_ok, "deviceCodeTotal": code_n}


# ---------------------------------------------------------------------------- mức 3: dây & điểm giao

def wire_mask(graph: SubstationTopologyGraph, thickness: int) -> np.ndarray:
    m = np.zeros((graph.page.height, graph.page.width), np.uint8)
    for b in graph.busbars:
        pts = np.round(np.asarray(b.polyline) * 16).astype(np.int32)
        cv2.polylines(m, [pts], False, 1, thickness, cv2.LINE_8, 4)
    for w in graph.wires:
        pts = np.round(np.asarray(w.points) * 16).astype(np.int32)
        cv2.polylines(m, [pts], False, 1, thickness, cv2.LINE_8, 4)
    return m


def wire_counts(pred, gt) -> dict:
    strokes = [w.stroke_width_px for w in gt.wires] or [2.0]
    t = max(2, int(round(float(np.median(strokes)))))
    a, b = wire_mask(pred, t), wire_mask(gt, t)
    if a.shape != b.shape:
        a = cv2.resize(a, (b.shape[1], b.shape[0]), interpolation=cv2.INTER_NEAREST)
    inter = int((a & b).sum())
    sa, sb = int(a.sum()), int(b.sum())
    return {"maskInter": inter, "maskPred": sa, "maskGt": sb,
            "dice": 2 * inter / (sa + sb) if sa + sb else 0.0,
            "iou": inter / (sa + sb - inter) if sa + sb - inter else 0.0}


def junction_counts(pred, gt) -> dict:
    tol = 1.5 * gt.page.px_per_mm
    G, P = gt.nodes, pred.nodes
    pairs = nearest([n.position for n in G], [n.position for n in P], tol)
    type_ok = sum(G[i].type == P[j].type for i, j in pairs)
    gt_cross = [i for i, n in enumerate(G) if n.type == "crossing_no_connect"]
    cross_hit = sum(1 for i, j in pairs if G[i].type == "crossing_no_connect" and P[j].type == "crossing_no_connect")
    return {"nodeMatched": len(pairs), "nodeTypeOk": type_ok, "nodeGt": len(G), "nodePred": len(P),
            "crossGt": len(gt_cross), "crossHit": cross_hit}


# ---------------------------------------------------------------------------- tổng hợp

def evaluate_graph(pred: SubstationTopologyGraph, gt: SubstationTopologyGraph,
                   lib: SymbolLibrary | None = None) -> dict:
    lib = lib or default_library()
    pred = to_original_scale(pred)
    m = build_match(pred, gt, lib)
    det = {f"{t:.2f}": detection_records(pred, gt, t) for t in IOU_THRESHOLDS}
    o_ok, o_n = orientation_counts(pred, gt, m.devices, lib)
    tmatch = match_texts(pred, gt)
    return {
        "detection": det,
        "deviceMatch": {"matched": len(m.devices), "pred": len(pred.devices), "gt": len(gt.devices),
                        "typeErrors": len(m.type_errors)},
        "orientation": {"ok": o_ok, "n": o_n},
        "text": text_counts(pred, gt, tmatch),
        "label": label_counts(pred, gt, m.devices, tmatch),
        "wire": wire_counts(pred, gt),
        "netSplitMerge": net_split_merge(pred, gt, m),
        "junction": junction_counts(pred, gt),
        "terminalPair": terminal_pair_f1(pred, gt, m),
        "bays": bay_accuracy(pred, gt, m),
    }


def aggregate(results: list[dict]) -> dict:
    """Gộp kết quả nhiều bản vẽ thành bảng chỉ số 4 mức."""
    det_cls: dict[str, dict[str, dict]] = defaultdict(lambda: defaultdict(lambda: {"records": [], "nGt": 0}))
    for r in results:
        for thr, per_cls in r["detection"].items():
            for cls, v in per_cls.items():
                det_cls[thr][cls]["records"] += [tuple(x) for x in v["records"]]
                det_cls[thr][cls]["nGt"] += v["nGt"]
    ap50 = {cls: average_precision(v["records"], v["nGt"]) for cls, v in det_cls["0.50"].items()}
    recall = {cls: (sum(ok for _, ok in v["records"]) / v["nGt"] if v["nGt"] else float("nan"))
              for cls, v in det_cls["0.50"].items()}
    n_gt = {cls: v["nGt"] for cls, v in det_cls["0.50"].items()}
    n_pred = {cls: len(v["records"]) for cls, v in det_cls["0.50"].items()}
    maps = []
    for thr in det_cls:
        aps = [average_precision(v["records"], v["nGt"]) for v in det_cls[thr].values() if v["nGt"]]
        maps.append(float(np.mean(aps)) if aps else 0.0)
    valid50 = [v for v in ap50.values() if not np.isnan(v)]

    def s(path):
        tot = 0
        for r in results:
            v = r
            for k in path:
                v = v[k]
            tot += v
        return tot

    o_ok, o_n = s(("orientation", "ok")), s(("orientation", "n"))
    terminal = prf(s(("terminalPair", "tp")), s(("terminalPair", "nPred")), s(("terminalPair", "nGt")))
    label = prf(s(("label", "labelTp")), s(("label", "labelPred")), s(("label", "labelGt")))
    inter, sp, sg = s(("wire", "maskInter")), s(("wire", "maskPred")), s(("wire", "maskGt"))
    bays_c, bays_n = s(("bays", "correct")), s(("bays", "total"))
    return {
        "nDrawings": len(results),
        "level1_symbols": {
            "mAP50": float(np.mean(valid50)) if valid50 else 0.0,
            "mAP50_95": float(np.mean(maps)) if maps else 0.0,
            "apPerClass50": ap50, "recallPerClass50": recall, "nGtPerClass": n_gt, "nPredPerClass": n_pred,
            "orientationAccuracy": o_ok / o_n if o_n else float("nan"), "orientationN": o_n,
            "typeErrors": s(("deviceMatch", "typeErrors")),
        },
        "level2_text_labels": {
            "codeExactMatch": s(("text", "codeExact")) / max(1, s(("text", "codeTotal"))),
            "codeTotal": s(("text", "codeTotal")),
            "allTextExactMatch": s(("text", "allExact")) / max(1, s(("text", "allTotal"))),
            "labelPairs": label,
            "deviceCodeAccuracy": s(("label", "deviceCodeOk")) / max(1, s(("label", "deviceCodeTotal"))),
        },
        "level3_wires": {
            "dice": 2 * inter / (sp + sg) if sp + sg else 0.0,
            "iou": inter / (sp + sg - inter) if sp + sg - inter else 0.0,
            "falseSplits": s(("netSplitMerge", "falseSplits")), "falseMerges": s(("netSplitMerge", "falseMerges")),
            "gtNets": s(("netSplitMerge", "gtNets")),
            "junctionTypeAccuracy": s(("junction", "nodeTypeOk")) / max(1, s(("junction", "nodeMatched"))),
            "junctionRecall": s(("junction", "nodeMatched")) / max(1, s(("junction", "nodeGt"))),
            "crossingRecall": s(("junction", "crossHit")) / max(1, s(("junction", "crossGt"))),
            "crossingGt": s(("junction", "crossGt")),
        },
        "level4_topology": {
            "terminalPair": terminal,
            "bayFullyCorrect": bays_c / bays_n if bays_n else 0.0, "baysCorrect": bays_c, "baysTotal": bays_n,
        },
    }
