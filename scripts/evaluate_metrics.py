"""Đánh giá pipeline theo 4 mức (SDD 9.1–9.2) trên dữ liệu tổng hợp hoặc real_gold.

    python scripts/evaluate_metrics.py --split synthetic/v001_n100:val --limit 10 --text-reader ground_truth
    python scripts/evaluate_metrics.py --split synthetic/v001_n100:val --limit 10
    python scripts/evaluate_metrics.py --split real_gold/dev

Đầu ra (mặc định output/eval/<tên>): preds/<id>_graph.json, metrics.json (từng bản vẽ + tổng hợp),
report.md (bảng 4 mức). --reuse dùng lại dự đoán đã có. `--text-reader ground_truth` lấy chữ từ đáp án
để đo riêng phần ký hiệu + dây + lắp ráp (không phải kết quả OCR).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.schema import SubstationTopologyGraph  # noqa: E402
from src.evaluation.metrics import aggregate, evaluate_graph  # noqa: E402
from src.perception.ocr_engine import GroundTruthTextReader  # noqa: E402
from src.pipeline import CONFIG_PATH, Pipeline, load_input  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def samples(split: str) -> list[tuple[str, Path, Path]]:
    """(id, ảnh, đồ thị đáp án) của một split: `synthetic/<bộ>:<train|val>` hoặc `real_gold/<dev|test>`."""
    name, _, part = split.partition(":")
    base = ROOT / "data" / name
    if name.startswith("synthetic/"):
        coco = json.loads((base / "annotations_coco.json").read_text(encoding="utf-8"))
        topo = set(json.loads((base / "split.json").read_text(encoding="utf-8"))[part or "val"])
        out = []
        for im in coco["images"]:
            if im["topologyId"] in topo:
                stem = Path(im["file_name"]).stem
                out.append((f"{base.name}_{stem}", base / im["file_name"], base / "graphs" / f"{stem}_graph.json"))
        return out
    out = []
    for g in sorted(base.glob("*_graph.json")):
        pid = g.name.removesuffix("_graph.json")
        out.append((pid, ROOT / "data" / "processed" / pid / "page.png", g))
    return out


def fmt(v, pct: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{100 * v:.1f}%" if pct else (f"{v:.3f}" if isinstance(v, float) else str(v))


def report_md(agg: dict, title: str, meta: dict) -> str:
    l1, l2, l3, l4 = (agg[k] for k in ("level1_symbols", "level2_text_labels", "level3_wires", "level4_topology"))
    tp, lp = l4["terminalPair"], l2["labelPairs"]
    lines = [f"# {title}", "", f"- Số bản vẽ: {agg['nDrawings']}"]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += ["", "| Mức | Chỉ số | Giá trị |", "|---|---|---|",
              f"| 1. Ký hiệu | mAP@0.5 | {fmt(l1['mAP50'])} |",
              f"| | mAP@0.5:0.95 | {fmt(l1['mAP50_95'])} |",
              f"| | Độ chính xác hướng (lớp bất đối xứng) | {fmt(l1['orientationAccuracy'], True)} (n={l1['orientationN']}) |",
              f"| | Ghép được nhưng sai loại | {l1['typeErrors']} |",
              f"| 2. Chữ & nhãn | Exact match mã (thiết bị + thanh cái) | {fmt(l2['codeExactMatch'], True)} (n={l2['codeTotal']}) |",
              f"| | Exact match mọi chữ (trừ khung tên) | {fmt(l2['allTextExactMatch'], True)} |",
              f"| | Ghép nhãn P / R / F1 | {fmt(lp['precision'])} / {fmt(lp['recall'])} / {fmt(lp['f1'])} |",
              f"| | Thiết bị đúng mã | {fmt(l2['deviceCodeAccuracy'], True)} |",
              f"| 3. Dây | Dice / IoU mask | {fmt(l3['dice'])} / {fmt(l3['iou'])} |",
              f"| | Net bị tách / bị dính (trên {l3['gtNets']} Net đáp án) | {l3['falseSplits']} / {l3['falseMerges']} |",
              f"| | Recall nút / độ chính xác loại nút | {fmt(l3['junctionRecall'], True)} / {fmt(l3['junctionTypeAccuracy'], True)} |",
              f"| | Recall giao không nối | {fmt(l3['crossingRecall'], True)} (n={l3['crossingGt']}) |",
              f"| 4. Đồ thị | Terminal-pair P / R / F1 | {fmt(tp['precision'])} / {fmt(tp['recall'])} / {fmt(tp['f1'])} |",
              f"| | Ngăn lộ đúng hoàn toàn | {fmt(l4['bayFullyCorrect'], True)} ({l4['baysCorrect']}/{l4['baysTotal']}) |",
              "", "## Recall và AP@0.5 theo lớp", "", "| Lớp | Đáp án | Dự đoán | Recall | AP@0.5 |", "|---|---|---|---|---|"]
    for cls in sorted(l1["nGtPerClass"], key=lambda c: -l1["nGtPerClass"][c]):
        lines.append(f"| `{cls}` | {l1['nGtPerClass'][cls]} | {l1['nPredPerClass'][cls]} | "
                     f"{fmt(l1['recallPerClass50'][cls], True)} | {fmt(l1['apPerClass50'][cls])} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", required=True)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--text-reader", help="Ghi đè pipeline.text_reader (ground_truth: chữ lấy từ đáp án)")
    ap.add_argument("--config", type=Path, default=CONFIG_PATH)
    ap.add_argument("--out", type=Path)
    ap.add_argument("--reuse", action="store_true", help="Dùng lại preds/ đã có")
    args = ap.parse_args()

    items = samples(args.split)[: args.limit]
    tag = args.split.replace("/", "_").replace(":", "_") + (f"_{args.text_reader}" if args.text_reader else "")
    out = args.out or ROOT / "output" / "eval" / tag
    (out / "preds").mkdir(parents=True, exist_ok=True)
    pipe = None
    per, times = {}, {}
    for pid, image, gt_path in items:
        pred_path = out / "preds" / f"{pid}_graph.json"
        if not (args.reuse and pred_path.exists()):
            if pipe is None:
                overrides = {}
                if args.text_reader:
                    overrides["pipeline"] = {"text_reader": args.text_reader}
                    if args.text_reader == "ground_truth":
                        overrides["text_reader_args"] = {"graph_path": str(gt_path)}
                pipe = Pipeline(args.config, overrides)        # nạp cấu hình một lần cho cả lượt
            if args.text_reader == "ground_truth":
                pipe.reader = GroundTruthTextReader(gt_path)
            inp = load_input(image, pid)
            t0 = time.perf_counter()
            res = pipe.run(inp.image, pid, inp.source_file, inp.page_size_mm)
            times[pid] = round(time.perf_counter() - t0, 1)
            pred_path.write_text(res.graph.to_json(), encoding="utf-8")
        pred = SubstationTopologyGraph.from_json(pred_path.read_text(encoding="utf-8"))
        gt = SubstationTopologyGraph.from_json(gt_path.read_text(encoding="utf-8"))
        per[pid] = evaluate_graph(pred, gt)
        tpf = per[pid]["terminalPair"]["f1"]
        print(f"{pid}: Terminal-pair F1 {tpf:.3f}, ngăn đúng {per[pid]['bays']['correct']}/{per[pid]['bays']['total']}"
              + (f", {times[pid]} s" if pid in times else ""))
    agg = aggregate(list(per.values()))
    meta = {"split": args.split, "text_reader": args.text_reader or "theo cấu hình",
            "config": args.config.as_posix() if not args.config.is_absolute() else args.config.name}
    if times:
        meta["thời gian trung bình / bản vẽ"] = f"{sum(times.values()) / len(times):.1f} s"
    slim = {pid: {k: v for k, v in r.items() if k != "detection"} for pid, r in per.items()}
    (out / "metrics.json").write_text(json.dumps({"meta": meta, "aggregate": agg, "perDrawing": slim},
                                                 ensure_ascii=False, indent=1, default=float), encoding="utf-8")
    (out / "report.md").write_text(report_md(agg, f"Đánh giá baseline GĐ3 — {args.split}", meta), encoding="utf-8")
    print((out / "report.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
