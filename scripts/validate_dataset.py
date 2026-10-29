"""Kiểm tra bộ dữ liệu tổng hợp theo 8 tiêu chí SDD 8.1.5, cộng round-trip DXF (mốc SDD 9.3-GĐ2).

    python scripts/validate_dataset.py data/synthetic/v001_n100

1. Graph JSON hợp lệ theo schema v1.1.
2. Mọi tham chiếu có kiểu đều tồn tại, không trùng id (bất biến 6.3-1).
3. Đầu dây trùng tọa độ cổng, ε < 1e-3 px.
4. Không có hai ký hiệu/nhãn chồng nhau (diện tích giao = 0).
5. Mọi ký hiệu nằm trọn trong trang và không bị khung tên che.
6. Mọi cổng có đúng 1 kết nối.
7. NetBuilder dựng lại đúng nets đã lưu (6.3-5), các bất biến còn lại của 6.3 không vi phạm.
8. ≥ 99% điểm mask rơi vào điểm tối của ảnh sạch; mọi bbox COCO chứa điểm tối.
DXF: đọc lại được, audit sạch, số INSERT = số thiết bị, sai số cổng < 0,01 mm.
Ghi kết quả vào validation_report.json; mã thoát khác 0 nếu có mẫu trượt.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import ezdxf
import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.net_builder import check_nets_consistent  # noqa: E402
from src.core.schema import PortRef, SubstationTopologyGraph, check_invariants  # noqa: E402
from src.core.symbol_lib import default_library  # noqa: E402
from src.exporters.dxf_exporter import read_back  # noqa: E402
from src.generator.layout_composer import find_overlaps  # noqa: E402
from src.generator.renderer import DARK  # noqa: E402

CRITERIA = ("1_schema", "2_references", "3_wire_ends", "4_no_overlap", "5_inside_page", "6_port_one_connection",
            "7_nets_rebuild", "8_mask_matches_image", "dxf_round_trip")
END_TOL_PX = 1e-3
MASK_DARK_RATIO = 0.99
PORT_TOL_MM = 0.01


def _xyxy(b):
    x, y, w, h = b
    return (x, y, x + w, y + h)


def check_sample(root: Path, image: dict, annotations: list[dict], lib=None) -> dict[str, str | None]:
    """Trả về {tiêu chí: None nếu đạt, chuỗi lý do nếu trượt}."""
    lib = lib or default_library()
    name = Path(image["file_name"]).stem
    out: dict[str, str | None] = {c: None for c in CRITERIA}
    try:
        g = SubstationTopologyGraph.from_json((root / "graphs" / f"{name}_graph.json").read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        return {c: f"không đọc được graph: {e}" for c in CRITERIA}

    inv = check_invariants(g, end_tolerance_px=END_TOL_PX)
    bad = [v for v in inv if v.code in ("broken_reference", "duplicate_id")]
    out["2_references"] = bad[0].message if bad else None
    bad = [v for v in inv if v.code == "wire_end_off_port"]
    out["3_wire_ends"] = bad[0].message if bad else None

    boxes = [(d.id, _xyxy(d.bbox)) for d in g.devices] + [(t.id, _xyxy(t.bbox)) for t in g.texts]
    hits = find_overlaps(boxes)
    out["4_no_overlap"] = f"{len(hits)} cặp, ví dụ {hits[0]}" if hits else None

    tb = _xyxy(image["titleBlockBbox"])
    for d in g.devices:
        x0, y0, x1, y1 = _xyxy(d.bbox)
        if x0 < 0 or y0 < 0 or x1 > g.page.width or y1 > g.page.height:
            out["5_inside_page"] = f"{d.id} ra ngoài trang"
            break
        if min(x1, tb[2]) > max(x0, tb[0]) and min(y1, tb[3]) > max(y0, tb[1]):
            out["5_inside_page"] = f"{d.id} bị khung tên che"
            break

    count: dict[tuple, int] = {(d.id, p.id): 0 for d in g.devices for p in d.ports}
    for w in g.wires:
        for e in (w.from_, w.to):
            if isinstance(e, PortRef):
                count[(e.device_id, e.port_id)] = count.get((e.device_id, e.port_id), 0) + 1
    wrong = [k for k, c in count.items() if c != 1]
    out["6_port_one_connection"] = f"{len(wrong)} cổng, ví dụ {wrong[0]}: {count[wrong[0]]}" if wrong else None

    other = [v for v in inv if v.code in ("net_not_partition", "port_multiple_wires", "voltage_conflict")]
    if other:
        out["7_nets_rebuild"] = other[0].message
    elif not check_nets_consistent(g, lib):
        out["7_nets_rebuild"] = "NetBuilder dựng lại khác nets đã lưu"

    clean = np.asarray(Image.open(root / "clean" / f"{name}.png").convert("L"))
    mask = np.asarray(Image.open(root / "masks" / f"{name}_wire.png").convert("L")) > 127
    dark = clean < DARK
    n_mask = int(mask.sum())
    ratio = float((mask & dark).sum()) / n_mask if n_mask else 0.0
    if ratio < MASK_DARK_RATIO:
        out["8_mask_matches_image"] = f"chỉ {ratio:.4f} điểm mask rơi vào điểm tối"
    else:
        for a in annotations:
            x0, y0, x1, y1 = (int(np.floor(v)) for v in _xyxy(a["bbox"]))
            if not dark[max(y0, 0):y1 + 2, max(x0, 0):x1 + 2].any():
                out["8_mask_matches_image"] = f"bbox của {a['deviceId']} không chứa điểm tối"
                break

    try:
        doc = ezdxf.readfile(root / "dxf" / f"{name}.dxf")
        if doc.audit().has_errors:
            out["dxf_round_trip"] = "audit có lỗi"
        else:
            back = read_back(doc, g)
            if back["inserts"] != len(g.devices):
                out["dxf_round_trip"] = f"{back['inserts']} INSERT ≠ {len(g.devices)} thiết bị"
            else:
                ppm = g.page.px_per_mm
                worst = max(max(abs(back["ports"][d.id][p.id][0] - p.x), abs(back["ports"][d.id][p.id][1] - p.y)) / ppm
                            for d in g.devices for p in d.ports)
                if worst >= PORT_TOL_MM:
                    out["dxf_round_trip"] = f"sai số cổng {worst:.4f} mm"
    except Exception as e:  # noqa: BLE001
        out["dxf_round_trip"] = f"lỗi đọc DXF: {e}"
    return out


def validate_dataset(root: Path, log=print) -> dict:
    root = Path(root)
    coco = json.loads((root / "annotations_coco.json").read_text(encoding="utf-8"))
    by_image: dict[int, list[dict]] = {}
    for a in coco["annotations"]:
        by_image.setdefault(a["image_id"], []).append(a)
    lib = default_library()
    samples = {}
    for im in coco["images"]:
        res = check_sample(root, im, by_image.get(im["id"], []), lib)
        name = Path(im["file_name"]).stem
        samples[name] = res
        failed = {k: v for k, v in res.items() if v}
        if failed:
            log(f"{name}: TRƯỢT {failed}")
    passed = [n for n, r in samples.items() if not any(r.values())]
    per_criterion = {c: sum(1 for r in samples.values() if r[c] is None) for c in CRITERIA}
    report = {"dataset": str(root), "total": len(samples), "passed": len(passed), "perCriterion": per_criterion,
              "failures": {n: {k: v for k, v in r.items() if v} for n, r in samples.items() if any(r.values())}}
    (root / "validation_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dataset", type=Path)
    args = parser.parse_args()
    report = validate_dataset(args.dataset)
    print(f"\n{report['passed']}/{report['total']} mẫu qua đủ tiêu chí")
    for c, k in report["perCriterion"].items():
        print(f"  {c:24s} {k}/{report['total']}")
    sys.exit(0 if report["passed"] == report["total"] else 1)


if __name__ == "__main__":
    main()
