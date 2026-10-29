"""Bất biến của đồ thị (SDD 6.3) phải bắt được từng loại lỗi."""
import pytest
from pydantic import ValidationError

from src.core.schema import (DanglingRef, Net, PortRef, SubstationTopologyGraph, TextRef, WireRef,
                             check_invariants)

from tests.conftest import port


def _codes(graph):
    return sorted({v.code for v in check_invariants(graph)})


def test_valid_graph_has_no_violation(line_bay):
    assert _codes(line_bay.graph()) == []


def test_broken_reference(line_bay):
    g = line_bay.graph()
    g.devices[0].label_text_ids.append("txt-missing")
    assert _codes(g) == ["broken_reference"]


def test_net_not_partition(line_bay):
    g = line_bay.graph()
    g.nets[0].members.append(WireRef(id="w-line"))      # w-line nằm ở hai net
    assert "net_not_partition" in _codes(g)
    g.nets = g.nets[1:]                                   # và có phần tử không thuộc net nào
    assert "net_not_partition" in _codes(g)


def test_wire_end_off_port(line_bay):
    g = line_bay.graph()
    w = next(w for w in g.wires if w.id == "w-dcl-cb")
    w.points[0] = (w.points[0][0] + 0.5, w.points[0][1])
    assert _codes(g) == ["wire_end_off_port"]


def test_port_with_two_wires(line_bay):
    line_bay.wire("w-dup", port("dev-171", "p1"), DanglingRef(position=(600, 480)),
                  points=[(500, 480), (600, 480)], status="dangling")
    g = line_bay.graph()
    assert "port_multiple_wires" in _codes(g)


def test_duplicate_id(line_bay):
    g = line_bay.graph()
    g.wires.append(g.wires[0].model_copy())
    assert "duplicate_id" in _codes(g)


def test_voltage_conflict_on_net(line_bay):
    g = line_bay.graph()
    net = next(n for n in g.nets if any(m.key() == ("busbar", "bus-C11") for m in n.members))
    net.voltage_kv = 22
    assert _codes(g) == ["voltage_conflict"]


def test_reject_unknown_fields_and_bad_kinds():
    with pytest.raises(ValidationError):
        PortRef.model_validate({"kind": "port", "deviceId": "d", "portId": "p", "extra": 1})
    with pytest.raises(ValidationError):
        Net.model_validate({"id": "n", "members": [{"kind": "text", "id": "t"}]})   # text không là thành viên net


def test_refs_are_hashable_and_typed():
    assert PortRef(device_id="d", port_id="p1").key() == ("port", "d", "p1")
    assert {TextRef(id="a"), TextRef(id="a")} == {TextRef(id="a")}


def test_sdd_example_fragment_parses():
    """Đoạn ví dụ ở SDD 6.2 (rút gọn) phải đọc được bằng schema."""
    doc = {
        "schemaVersion": "1.1",
        "provenance": {"source": "pipeline", "pipelineVersion": "0.3.0"},
        "page": {"id": "E18.1", "width": 3500, "height": 2480, "unit": "px", "origin": "top-left",
                 "pxPerMm": 8.0, "pxPerMmSource": "pdf_page_size"},
        "texts": [{"id": "txt-0187", "raw": "Cầu Phủ", "normalized": "Cầu Phủ", "bbox": [228, 2060, 20, 70],
                   "angleDeg": 270, "confidence": 0.91, "role": "line_name", "assignedTo": None}],
        "nodes": [{"id": "node-x01", "type": "crossing_no_connect", "style": "hop_arc", "position": [1990, 1345],
                   "connections": [{"kind": "wire", "id": "wire-088"}, {"kind": "wire", "id": "wire-102"}],
                   "confidence": 0.88, "reviewStatus": "pending"}],
        "issues": [{"id": "issue-001", "type": "ambiguous_port_connection", "severity": "warning",
                    "position": [1510, 1120], "objectRefs": [{"kind": "port", "deviceId": "dev-CS1T1", "portId": "p1"}],
                    "message": "cần xác nhận", "resolution": None}],
    }
    g = SubstationTopologyGraph.model_validate(doc)
    assert g.texts[0].angle_deg == 270
    assert "broken_reference" in _codes(g)    # ví dụ rút gọn nên các dây được trỏ tới không tồn tại
