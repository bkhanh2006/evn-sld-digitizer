"""Pipeline đầu–cuối (SDD 10.6–10.7).

- Smoke test trên ảnh E18.1 độ phân giải thấp (1070 × 765): chạy hết, JSON hợp lệ theo schema, có issue
  `low_resolution_input`, xuất được SVG + DXF. Không kiểm độ chính xác.
- Accuracy test trên đáp án thật E18.9 (real_gold/dev): không tụt quá 2 điểm so với tests/baselines/.
- Round-trip tổng hợp: mẫu val → PNG → pipeline (chữ lấy từ đáp án) → so với đáp án theo SDD 9.2.
  Mặc định 3 mẫu; đặt SLD_ROUNDTRIP_N=10 để chạy đủ 10 mẫu như SDD 10.7.

Các test cần dữ liệu trong data/ tự bỏ qua khi thiếu file.
"""
import os
from pathlib import Path

import ezdxf
import pytest

from src.core.schema import SubstationTopologyGraph, check_invariants
from src.pipeline import Pipeline, export_result, load_input

ROOT = Path(__file__).resolve().parents[1]
E18_1 = ROOT / "data" / "raw_images" / "E18.1_low_res.png"
SYN = ROOT / "data" / "synthetic" / "v001_n100"


def _has_paddle() -> bool:
    try:
        import paddleocr  # noqa: F401
        return True
    except Exception:
        return False


@pytest.mark.skipif(not E18_1.exists(), reason="thiếu data/raw_images/E18.1_low_res.png")
@pytest.mark.skipif(not _has_paddle(), reason="chưa cài paddleocr")
def test_smoke_e18_1_low_res(tmp_path):
    inp = load_input(E18_1)
    res = Pipeline().run(inp.image, inp.page_id, inp.source_file, inp.page_size_mm)
    g = res.graph
    assert not res.failed, res.failed
    assert any(i.type == "low_resolution_input" and i.severity == "error" for i in g.issues)
    assert g.page.px_per_mm_source == "estimated" and g.page.normalization_scale > 1.5
    assert g.devices and g.wires and g.texts
    again = SubstationTopologyGraph.from_json(g.to_json())
    assert not [v for v in check_invariants(again, 2 * res.image.median_stroke_px)
                if v.code in ("broken_reference", "duplicate_id", "net_not_partition")]
    paths = export_result(res, tmp_path, inp.page_id)
    doc = ezdxf.readfile(paths["dxf"])
    assert not doc.audit().has_errors
    assert sum(1 for e in doc.modelspace() if e.dxftype() == "INSERT") == len(g.devices)
    assert paths["svg"].read_text(encoding="utf-8").count("<use ") == len(g.devices)


E18_9_GOLD = ROOT / "data" / "real_gold" / "dev" / "E18.9_graph.json"
E18_9_PAGE = ROOT / "data" / "processed" / "E18.9" / "page.png"
BASELINE = ROOT / "tests" / "baselines" / "gd3_real_dev.json"


@pytest.mark.skipif(not (E18_9_GOLD.exists() and E18_9_PAGE.exists()), reason="thiếu đáp án/ảnh E18.9")
@pytest.mark.skipif(not _has_paddle(), reason="chưa cài paddleocr")
def test_accuracy_e18_9_no_regression():
    """Độ chính xác trên đáp án thật (SDD 10.6): in chỉ số 4 mức, không tụt quá 2 điểm so với baseline đã ghi.

    E18.1 bản độ phân giải cao chưa có, nên dùng E18.9 (real_gold/dev)."""
    import json
    from src.evaluation.metrics import aggregate, evaluate_graph
    inp = load_input(E18_9_PAGE)
    res = Pipeline().run(inp.image, inp.page_id, inp.source_file, inp.page_size_mm)
    gt = SubstationTopologyGraph.from_json(E18_9_GOLD.read_text(encoding="utf-8"))
    agg = aggregate([evaluate_graph(res.graph, gt)])
    now = {"mAP50": agg["level1_symbols"]["mAP50"],
           "codeExactMatch": agg["level2_text_labels"]["codeExactMatch"],
           "labelF1": agg["level2_text_labels"]["labelPairs"]["f1"],
           "wireDice": agg["level3_wires"]["dice"],
           "terminalPairF1": agg["level4_topology"]["terminalPair"]["f1"]}
    print(json.dumps(now, indent=1))
    base = json.loads(BASELINE.read_text(encoding="utf-8"))["E18.9"]
    for k, v in base.items():
        assert now[k] >= v - 0.02, f"{k} tụt từ {v:.3f} xuống {now[k]:.3f}"


def _val_samples(n: int):
    import json
    coco = json.loads((SYN / "annotations_coco.json").read_text(encoding="utf-8"))
    val = set(json.loads((SYN / "split.json").read_text(encoding="utf-8"))["val"])
    stems = [Path(im["file_name"]).stem for im in coco["images"] if im["topologyId"] in val]
    return stems[:n]


@pytest.mark.skipif(not (SYN / "split.json").exists(), reason="thiếu data/synthetic/v001_n100")
def test_synthetic_roundtrip():
    from src.evaluation.metrics import aggregate, evaluate_graph
    n = int(os.environ.get("SLD_ROUNDTRIP_N", "3"))
    results = []
    for stem in _val_samples(n):
        gt_path = SYN / "graphs" / f"{stem}_graph.json"
        pipe = Pipeline(overrides={"pipeline": {"text_reader": "ground_truth"},
                                   "text_reader_args": {"graph_path": str(gt_path)}})
        inp = load_input(SYN / "images" / f"{stem}.png")
        res = pipe.run(inp.image, inp.page_id, inp.source_file, inp.page_size_mm)
        gt = SubstationTopologyGraph.from_json(gt_path.read_text(encoding="utf-8"))
        results.append(evaluate_graph(res.graph, gt))
    agg = aggregate(results)
    # hồi quy: không tụt quá 2 điểm so với baseline GĐ3 (3 mẫu đầu: mAP50 0,932; Terminal-pair F1 0,722;
    # đủ 10 mẫu: 0,945 / 0,725 — docs/bao_cao_baseline_gd3.md)
    assert agg["level1_symbols"]["mAP50"] >= 0.91
    assert agg["level4_topology"]["terminalPair"]["f1"] >= 0.70
