"""So khớp đồ thị và chỉ số đánh giá (SDD 9.2, 10.5)."""
import pytest

from src.core.net_builder import NetBuilder
from src.core.schema import PortRef
from src.core.symbol_lib import default_library
from src.evaluation.graph_matching import build_match, terminal_pair_f1
from src.evaluation.metrics import aggregate, average_precision, evaluate_graph


def _rebuild(g):
    g.nets = NetBuilder(default_library()).build(g).nets
    return g


def test_self_match_is_perfect(slice_graph):
    agg = aggregate([evaluate_graph(slice_graph, slice_graph.model_copy(deep=True))])
    assert agg["level4_topology"]["terminalPair"]["f1"] == 1.0
    assert agg["level1_symbols"]["mAP50"] == pytest.approx(1.0)
    assert agg["level2_text_labels"]["labelPairs"]["f1"] == 1.0
    assert agg["level3_wires"]["dice"] == pytest.approx(1.0)
    assert agg["level3_wires"]["falseSplits"] == 0 and agg["level3_wires"]["falseMerges"] == 0


def test_removing_wire_drops_expected_pairs(slice_graph):
    gt = slice_graph
    pred = gt.model_copy(deep=True)
    pred.wires = [w for w in pred.wires if w.id != "w-cb-ti"]
    _rebuild(pred)
    m = build_match(pred, gt)
    r = terminal_pair_f1(pred, gt, m)
    # Net {171.p1, TI171.p1, TI171.p2, 171-76, mũi tên} có 10 cặp; bỏ dây thì 171.p1 tách riêng: mất 4 cặp
    assert r["nGt"] - r["tp"] == 4
    assert r["precision"] == 1.0


def test_swapping_symmetric_ports_keeps_f1(slice_graph):
    gt = slice_graph
    pred = gt.model_copy(deep=True)
    cb = pred.device("dev-171")
    p1, p2 = cb.port("p1"), cb.port("p2")
    p1.x, p1.y, p2.x, p2.y = p2.x, p2.y, p1.x, p1.y
    swap = {"p1": "p2", "p2": "p1"}
    for w in pred.wires:
        for attr in ("from_", "to"):
            ref = getattr(w, attr)
            if isinstance(ref, PortRef) and ref.device_id == "dev-171":
                setattr(w, attr, PortRef(device_id="dev-171", port_id=swap[ref.port_id]))
    _rebuild(pred)
    m = build_match(pred, gt)
    assert terminal_pair_f1(pred, gt, m)["f1"] == 1.0


def test_wrong_class_still_matched_and_counted(slice_graph):
    gt = slice_graph
    pred = gt.model_copy(deep=True)
    d = pred.device("dev-171-1")
    d.detector_class, d.type = "fuse_disconnector", "fuse"
    _rebuild(pred)
    r = evaluate_graph(pred, gt)
    assert r["deviceMatch"]["typeErrors"] == 1
    assert r["terminalPair"]["f1"] == 1.0          # đấu nối vẫn đúng (cổng ghép theo vai trò a/b)


def test_average_precision():
    assert average_precision([(0.9, True), (0.8, True)], 2) == pytest.approx(1.0)
    assert average_precision([(0.9, False), (0.8, True)], 1) == pytest.approx(0.5, abs=0.01)
    assert average_precision([], 3) == 0.0
