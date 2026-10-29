"""Dựng đáp án bản vẽ thật bán tự động (SDD 8.6, kế hoạch GĐ3 bước 9).

Hai pha:
1. Chốt danh sách thiết bị: lấy thiết bị của bản nháp (đầu ra pipeline), áp sửa đổi (bỏ, đổi lớp, thêm
   mới bằng cách đặt tại một điểm — script so khớp mẫu cục bộ để lấy bbox). Chữ: OCR đã lưu + sửa chuỗi.
2. Chạy lại pipeline với detector "given" (đúng danh sách trên) để khâu che ký hiệu, trích dây và ghép
   cổng tự nối dây; rồi áp sửa đấu nối (chỉ định bằng tọa độ điểm, không phụ thuộc id dây), dựng lại
   `nets` bằng NetBuilder, kiểm bất biến, vẽ ảnh soát.

    python scripts/build_real_gold.py --id E18.9

Đọc data/real_gold/dev/<id>_draft.json, <id>_ocr.json, <id>_edits.yaml; ghi <id>_graph.json và ảnh soát
output/gold/<id>_review.png. File sửa đổi (mọi khóa đều tùy chọn):

    remove_devices: [dev-..]
    devices:
      - {id: dev-175-15, class: earthing_switch_open, at: [x, y], code: "175-15"}     # đặt theo điểm
      - {id: dev-TU175, rotation: 0}                                                  # sửa tư thế
      - {id: dev-x, class: voltage_transformer, bbox: [x, y, w, h], rotation: 0, mirror: false}
    texts: {txt-0012: "175-15"}          # sửa chuỗi OCR
    remove_texts: [txt-0003]
    add_texts: [{id: txt-a01, raw: "TI 175", bbox: [x, y, w, h], angle: 0}]
    text_roles: {txt-0101: pole_number}  # đặt vai trò (tiêu đề bản vẽ → title_block, số cột → pole_number)
    busbars: [{at: [x, y], code: C11}]
    remove_wires: [[x, y], ...]          # dây đi qua gần điểm (≤ 8 px)
    remove_wires_in: [[x0, y0, x1, y1]]  # mọi dây nằm trọn trong hình chữ nhật (nét chữ dọc còn sót)
    connect:                             # dây thẳng giữa hai đầu cực
      - [dev-175-15:p1, "@612,575"]      # cổng → điểm trên dây/thanh cái gần nhất (rẽ chữ T)
      - [dev-A:p1, dev-B:p2]             # cổng → cổng
    disconnect: [[dev-A, p1]]            # cắt dây khỏi cổng (đầu dây thành treo)

Mã thiết bị trong `devices[].code` thắng mã do LabelMatcher ghép. Đầu ra mang
`provenance.source = "human_reviewed"`; `config` ghi rõ là bản bán tự động CHỜ KỸ SƯ DUYỆT.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import numpy as np
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.evn_codes import default_grammar  # noqa: E402
from src.core.net_builder import NetBuilder  # noqa: E402
from src.core.schema import (BusbarRef, DanglingRef, DeviceCode, DeviceRef, Node, NodeRef, PortRef,  # noqa: E402
                             Provenance, SubstationTopologyGraph, Wire, WireRef, check_invariants)
from src.core.symbol_lib import default_library  # noqa: E402
from src.perception.busbar_classifier import nearest_on_polyline, point_segment_distance  # noqa: E402
from src.perception.ocr_engine import make_text_item  # noqa: E402
from src.reconstruction.assembler import DefaultGraphAssembler  # noqa: E402
from src.reconstruction.graph_validator import EVNRuleValidator  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
LIB, GRAMMAR = default_library(), default_grammar()


# ---------------------------------------------------------------------------- pha 1: thiết bị

def snap(ink: np.ndarray, w: float, ppm: float, cls: str, at, rotation=None, mirror=None, scales=None,
         radius: int = 30):
    """Đặt ký hiệu tại điểm gần đúng: so NCC mọi tư thế (hoặc tư thế cho trước) quanh `at`."""
    from src.perception.symbol_detector import _ncc
    from src.perception.templates import poses, render_template
    s_exp = ppm * LIB.library_unit_mm
    best = (-2.0, None)
    for rot, mir in poses(LIB, cls):
        if (rotation is not None and rot != rotation) or (mirror is not None and mir != mirror):
            continue
        for k in scales or (0.8, 0.9, 1.0, 1.1):
            t = render_template(LIB, cls, rot, mir, s_exp * k, w, 2.5 * w, blur_sigma=w)
            th, tw = t.image.shape
            x0, y0 = int(at[0] - tw / 2 - radius), int(at[1] - th / 2 - radius)
            if x0 < 0 or y0 < 0:
                continue
            win = ink[y0:y0 + th + 2 * radius, x0:x0 + tw + 2 * radius]
            if win.shape[0] < th or win.shape[1] < tw:
                continue
            r = _ncc(win, t.image, min_ink_ratio=0.0)
            iy, ix = np.unravel_index(int(r.argmax()), r.shape)
            if r[iy, ix] > best[0]:
                bx, by, bw, bh = t.bbox_in_template
                best = (float(r[iy, ix]), ((x0 + ix + bx, y0 + iy + by, bw, bh), rot, mir))
    if best[1] is None:
        raise ValueError(f"Không đặt được {cls} tại {at}")
    (x, y, bw, bh), rot, mir = best[1]
    return [round(x, 2), round(y, 2), round(bw, 2), round(bh, 2)], rot, mir, best[0]


def device_list(draft: SubstationTopologyGraph, ed: dict, ink, w, ppm) -> tuple[list[dict], dict[str, str]]:
    removed = set(ed.get("remove_devices") or [])
    items = {d.id: {"id": d.id, "class": d.detector_class, "bbox": list(d.bbox),
                    "rotation": d.transform.rotation_deg, "mirror": d.transform.mirror_x}
             for d in draft.devices if d.id not in removed}
    codes = {}
    for spec in ed.get("devices") or []:
        it = items.setdefault(spec["id"], {"id": spec["id"]})
        if "class" in spec:
            it["class"] = spec["class"]
        if "at" in spec:
            it["bbox"], it["rotation"], it["mirror"], sc = snap(ink, w, ppm, it["class"], spec["at"],
                                                                spec.get("rotation"), spec.get("mirror"),
                                                                spec.get("scales"), spec.get("radius", 30))
            if sc < 0.5:
                print(f"  chú ý: {spec['id']} đặt tại {spec['at']} chỉ khớp NCC {sc:.2f}")
        else:
            for k in ("bbox", "rotation", "mirror"):
                if k in spec:
                    it[k] = spec[k]
        if spec.get("code"):
            codes[spec["id"]] = str(spec["code"])
    return list(items.values()), codes


def text_items(ocr: list[dict], ed: dict):
    fixes = {k: str(v) for k, v in (ed.get("texts") or {}).items()}
    rm = set(ed.get("remove_texts") or [])
    rows = [t for t in ocr if t["id"] not in rm] + list(ed.get("add_texts") or [])
    out = []
    for t in rows:
        item = make_text_item(0, fixes.get(t["id"], t["raw"]), t["bbox"], 1.0, t.get("angle", 0), GRAMMAR)
        item.id = t["id"]
        out.append(item)
    return out


# ---------------------------------------------------------------------------- pha 2: đấu nối

def _nearest_wire(g: SubstationTopologyGraph, p, max_d: float = 8.0):
    best = (max_d, None, None)
    for w in g.wires:
        d, foot = nearest_on_polyline(p, w.points)
        if d < best[0]:
            best = (d, ("wire", w.id), foot)
    for b in g.busbars:
        d, foot = nearest_on_polyline(p, b.polyline)
        if d < best[0] + b.stroke_width_px / 2:
            best = (d, ("busbar", b.id), foot)
    return best


def _split_wire(g: SubstationTopologyGraph, wid: str, foot, nid: str) -> Node:
    w = next(x for x in g.wires if x.id == wid)
    best_i, best_d = 0, math.inf
    for i, (a, b) in enumerate(zip(w.points, w.points[1:])):
        d, _ = point_segment_distance(foot, a, b)
        if d < best_d:
            best_i, best_d = i, d
    foot = (round(foot[0], 2), round(foot[1], 2))
    first, second = list(w.points[:best_i + 1]) + [foot], [foot] + list(w.points[best_i + 1:])
    a = w.model_copy(update={"id": f"{wid}a", "points": first, "to": NodeRef(id=nid)})
    b = w.model_copy(update={"id": f"{wid}b", "points": second, "from_": NodeRef(id=nid)})
    g.wires = [x for x in g.wires if x.id != wid] + [a, b]
    for n in g.nodes:
        if any(c.kind == "wire" and c.id == wid for c in n.connections):
            keep = a.id if nearest_on_polyline(n.position, first)[0] <= nearest_on_polyline(n.position, second)[0] \
                else b.id
            n.connections = [WireRef(id=keep) if (c.kind == "wire" and c.id == wid) else c for c in n.connections]
    node = Node(id=nid, type="junction", style="t_plain", position=foot,
                connections=[WireRef(id=a.id), WireRef(id=b.id)], confidence=1.0, review_status="confirmed")
    g.nodes.append(node)
    return node


def _terminal(g: SubstationTopologyGraph, spec: str, tag: str):
    """Đặc tả đầu cực → (ref cho đầu dây mới, tọa độ)."""
    if spec.startswith("@"):
        p = tuple(float(v) for v in spec[1:].split(","))
        d, hit, foot = _nearest_wire(g, p, 12.0)
        if hit is None:
            raise ValueError(f"Không có dây/thanh cái gần {spec}")
        nid = f"node-g{tag}"
        if hit[0] == "busbar":
            n = Node(id=nid, type="junction", style="t_on_busbar", position=(round(foot[0], 2), round(foot[1], 2)),
                     connections=[BusbarRef(id=hit[1])], confidence=1.0, review_status="confirmed")
            g.nodes.append(n)
            return NodeRef(id=nid), n.position
        w = next(x for x in g.wires if x.id == hit[1])
        for attr, idx in (("from_", 0), ("to", -1)):
            end = w.points[idx]
            if math.dist(end, foot) <= 4.0:            # sát đầu dây: nối vào đầu đó
                ref = getattr(w, attr)
                if isinstance(ref, NodeRef):
                    return ref, tuple(end)
                if isinstance(ref, PortRef):           # đầu dây đang ở cổng: rẽ ở điểm lùi vào trong dây
                    nxt = w.points[1] if idx == 0 else w.points[-2]
                    L = math.dist(end, nxt)
                    k = min(5.0, L / 2) / L if L > 0 else 0.0
                    foot = (end[0] + (nxt[0] - end[0]) * k, end[1] + (nxt[1] - end[1]) * k)
                    break
                n = Node(id=nid, type="junction", style="t_plain", position=tuple(end),
                         connections=[WireRef(id=w.id)], confidence=1.0, review_status="confirmed")
                g.nodes.append(n)
                setattr(w, attr, NodeRef(id=nid))
                return NodeRef(id=nid), tuple(end)
        n = _split_wire(g, w.id, foot, nid)
        return NodeRef(id=nid), n.position
    did, pid = spec.split(":")
    p = g.device(did).port(pid)
    return PortRef(device_id=did, port_id=pid), (p.x, p.y)


def apply_wiring(g: SubstationTopologyGraph, ed: dict) -> None:
    for x0, y0, x1, y1 in ed.get("remove_wires_in") or []:
        g.wires = [w for w in g.wires
                   if not all(x0 <= px <= x1 and y0 <= py <= y1 for px, py in w.points)]
    for x, y in ed.get("remove_wires") or []:
        d, hit, _ = _nearest_wire(g, (x, y), 8.0)
        if hit is None or hit[0] != "wire":
            print(f"  chú ý: không có dây gần ({x}, {y}) để bỏ")
            continue
        g.wires = [w for w in g.wires if w.id != hit[1]]
    for did, pid in ed.get("disconnect") or []:
        for w in g.wires:
            for attr, idx in (("from_", 0), ("to", -1)):
                ref = getattr(w, attr)
                if isinstance(ref, PortRef) and ref.device_id == did and ref.port_id == pid:
                    setattr(w, attr, DanglingRef(position=w.points[idx]))
    for i, (a, b) in enumerate(ed.get("connect") or [], start=1):
        try:
            ref_a, xa = _terminal(g, a, f"{i:03d}a")
            ref_b, xb = _terminal(g, b, f"{i:03d}b")
        except (ValueError, KeyError) as exc:
            print(f"  chú ý: bỏ qua connect {a} — {b}: {exc}")
            continue
        wid = f"wire-g{i:03d}"
        g.wires.append(Wire(id=wid, points=[tuple(xa), tuple(xb)], stroke_width_px=3.0, from_=ref_a, to=ref_b,
                            connection_status="confirmed", confidence=1.0))
        for r in (ref_a, ref_b):
            if isinstance(r, NodeRef):
                next(n for n in g.nodes if n.id == r.id).connections.append(WireRef(id=wid))
    wires = {w.id for w in g.wires}
    for n in g.nodes:
        n.connections = [c for c in n.connections if c.kind != "wire" or c.id in wires]
    g.nodes = [n for n in g.nodes if any(c.kind == "wire" for c in n.connections)]
    nodes = {n.id for n in g.nodes}
    for w in g.wires:
        for attr, idx in (("from_", 0), ("to", -1)):
            ref = getattr(w, attr)
            if isinstance(ref, NodeRef) and ref.id not in nodes:
                setattr(w, attr, DanglingRef(position=w.points[idx]))


def finalize(g: SubstationTopologyGraph, ed: dict, codes: dict[str, str], edits_file: str) -> None:
    for spec in ed.get("busbars") or []:
        d, hit, _ = _nearest_wire(g, tuple(spec["at"]), 30.0)
        b = next(x for x in g.busbars if hit and x.id == hit[1])
        b.code = spec["code"]
        b.voltage_kv = GRAMMAR.parse(spec["code"]).voltage_kv
    for did, code in codes.items():
        p = GRAMMAR.parse(code)
        g.device(did).code = DeviceCode(normalized=p.normalized, voltage_kv=p.voltage_kv, bay_code=p.bay_code,
                                        suffix=p.suffix) if p else DeviceCode(normalized=code)
    # chữ mã thiết bị: ghép lại theo mã cuối cùng (chữ trùng chuỗi gần thiết bị nhất)
    for t in g.texts:
        if t.role == "device_code":
            t.assigned_to = None
    for d in g.devices:
        d.label_text_ids = []
        if d.code is None:
            continue
        cands = [t for t in g.texts if t.normalized == d.code.normalized and t.role in ("device_code", "pole_number")]
        if cands:
            t = min(cands, key=lambda t: math.dist((t.bbox[0] + t.bbox[2] / 2, t.bbox[1] + t.bbox[3] / 2),
                                                   (d.transform.x, d.transform.y)))
            t.role, t.assigned_to = "device_code", DeviceRef(id=d.id)
            d.label_text_ids = [t.id]
    for tid, role in (ed.get("text_roles") or {}).items():
        t = next(x for x in g.texts if x.id == tid)
        t.role = role
        if role in ("title_block", "pole_number", "other"):
            t.assigned_to = None
    for t in g.texts:
        if t.role == "busbar_code":
            bs = [b for b in g.busbars if b.code == t.normalized]
            t.assigned_to = BusbarRef(id=bs[0].id) if bs else None
            if bs:
                bs[0].label_text_ids = [t.id]
        t.confidence = 1.0
    for d in g.devices:
        d.review_status, d.confidence = "confirmed", 1.0
        d.bay_id = None
    for w in g.wires:
        w.connection_status, w.confidence = "confirmed", 1.0
        for attr, idx in (("from_", 0), ("to", -1)):
            ref = getattr(w, attr)
            if isinstance(ref, PortRef):
                p = g.device(ref.device_id).port(ref.port_id)
                pts = list(w.points)
                pts[idx] = (p.x, p.y)
                w.points = pts
    for n in g.nodes:
        n.review_status, n.confidence = "confirmed", 1.0
    for b in g.busbars:
        b.review_status, b.confidence = "confirmed", 1.0
    g.nets = NetBuilder(LIB).build(g).nets
    g.bays = DefaultGraphAssembler()._bays(g, {d.id: d for d in g.devices})
    for d in g.devices:
        d.review_status = "confirmed"
    g.issues = []
    g.issues = EVNRuleValidator(LIB, GRAMMAR).validate(g, end_tolerance_px=6.0)
    g.provenance = Provenance(source="human_reviewed", pipeline_version=g.provenance.pipeline_version,
                              config=f"Đáp án bán tự động: nháp pipeline + hiệu chỉnh {edits_file}; CHỜ KỸ SƯ DUYỆT",
                              created_at=g.provenance.created_at)


def review_image(g: SubstationTopologyGraph, image: Path, out: Path) -> None:
    import colorsys

    import cv2
    from PIL import Image
    img = np.asarray(Image.open(image).convert("RGB")).copy()
    img = (255 - (255 - img.astype(np.float32)) * 0.35).astype(np.uint8)
    idx = g.net_index()

    def col(n):
        r, gg, b = colorsys.hsv_to_rgb((hash(n) % 997) / 997, 0.9, 0.8)
        return int(r * 255), int(gg * 255), int(b * 255)

    for b in g.busbars:
        cv2.polylines(img, [np.round(np.asarray(b.polyline)).astype(np.int32)], False, col(idx[("busbar", b.id)]), 5)
        cv2.putText(img, b.code or b.id, (int(b.polyline[0][0]), int(b.polyline[0][1]) - 4),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 160), 1)
    for w in g.wires:
        cv2.polylines(img, [np.round(np.asarray(w.points)).astype(np.int32)], False, col(idx[("wire", w.id)]), 2)
        for e, p in ((w.from_, w.points[0]), (w.to, w.points[-1])):
            if isinstance(e, DanglingRef):
                cv2.drawMarker(img, (int(p[0]), int(p[1])), (255, 0, 0), cv2.MARKER_TILTED_CROSS, 9, 2)
    for n in g.nodes:
        if n.type == "crossing_no_connect":
            cv2.circle(img, (int(n.position[0]), int(n.position[1])), 6, (200, 0, 200), 1)
    for d in g.devices:
        x, y, w, h = map(int, d.bbox)
        cv2.rectangle(img, (x, y), (x + w, y + h), (90, 90, 90), 1)
        label = d.id.removeprefix("dev-") + (f" [{d.code.normalized}]" if d.code else "")
        cv2.putText(img, label, (x, y - 2), cv2.FONT_HERSHEY_SIMPLEX, 0.36, (0, 0, 160), 1)
        for p in d.ports:
            cv2.circle(img, (int(p.x), int(p.y)), 3, col(idx[("port", d.id, p.id)]), -1)
    Image.fromarray(img).save(out)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--id", required=True)
    ap.add_argument("--split", default="dev")
    ap.add_argument("--image", type=Path, help="mặc định data/processed/<id>/page.png")
    ap.add_argument("--review", type=Path, help="mặc định output/gold/<id>_review.png")
    args = ap.parse_args()
    base = ROOT / "data" / "real_gold" / args.split
    image = args.image or ROOT / "data" / "processed" / args.id / "page.png"
    edits_path = base / f"{args.id}_edits.yaml"
    ed = yaml.safe_load(edits_path.read_text(encoding="utf-8")) or {}
    draft = SubstationTopologyGraph.from_json((base / f"{args.id}_draft.json").read_text(encoding="utf-8"))
    ocr = json.loads((base / f"{args.id}_ocr.json").read_text(encoding="utf-8"))

    from src.perception.scale_normalizer import line_stroke_width
    from src.perception.templates import ink_image
    from src.pipeline import Pipeline, load_input
    inp = load_input(image, args.id)
    gray = inp.image if inp.image.ndim == 2 else inp.image[..., 0]
    w = line_stroke_width(gray)
    symbols, codes = device_list(draft, ed, ink_image(gray, w), w, draft.page.px_per_mm)
    texts = text_items(ocr, ed)
    pipe = Pipeline(overrides={"pipeline": {"symbol_detector": "given", "orientation_estimator": "given"},
                               "symbol_detector_args": {"symbols": symbols}})
    res = pipe.run(inp.image, args.id, inp.source_file, inp.page_size_mm, texts=texts)
    g = res.graph
    apply_wiring(g, ed)
    finalize(g, ed, codes, edits_path.relative_to(ROOT).as_posix())
    bad = [v for v in check_invariants(g, 6.0) if v.code != "wire_end_off_port"]
    for v in bad:
        print("BẤT BIẾN:", v.code, v.message)
    conn = {(e.device_id, e.port_id) for wi in g.wires for e in (wi.from_, wi.to) if isinstance(e, PortRef)}
    free = [f"{d.id}:{p.id}" for d in g.devices for p in d.ports if (d.id, p.id) not in conn]
    if free:
        print("Cổng chưa có dây (soát lại, hoặc ghi chú là chủ ý):", ", ".join(free))
    out = base / f"{args.id}_graph.json"
    out.write_text(g.to_json(), encoding="utf-8")
    from collections import Counter
    print(f"{out.relative_to(ROOT)}: {len(g.devices)} thiết bị, {len(g.wires)} dây, {len(g.nets)} Net, "
          f"{len(g.bays)} ngăn; issue {dict(Counter(i.type for i in g.issues))}")
    review = args.review or ROOT / "output" / "gold" / f"{args.id}_review.png"
    review.parent.mkdir(parents=True, exist_ok=True)
    review_image(g, image, review)
    print("ảnh soát:", review.relative_to(ROOT))


if __name__ == "__main__":
    main()
