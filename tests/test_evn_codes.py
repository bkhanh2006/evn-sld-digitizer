"""SDD 10.2: nhãn có thật trên E18.1 và E18.9 phải được phân tích đúng vai trò, loại, cấp áp."""
import pytest

from src.core.evn_codes import default_grammar
from src.core.symbol_lib import default_library

G = default_grammar()

DCL = {"disconnector_open", "disconnector_closed"}
DTD = {"earthing_switch_open", "earthing_switch_closed"}
MC = {"circuit_breaker", "drawout_breaker"}

# (chuỗi OCR, pattern_id, role, expected_types, voltage_kv, details phải có)
CASES = [
    # E18.1
    ("171", "main_switch", "device_code", MC, 110, {"position": "line"}),
    ("131", "main_switch", "device_code", MC, 110, {"position": "transformer"}),
    ("112", "main_switch", "device_code", MC, 110, {"position": "bus_coupler", "busbars": "C11,C12"}),
    ("471", "main_switch", "device_code", MC, 22, {"position": "line"}),
    ("441", "main_switch", "device_code", MC, 22, {"position": "aux_transformer"}),
    ("171-1", "bay_switch", "device_code", DCL, 110, {"side": "busbar_1"}),
    ("171-7", "bay_switch", "device_code", DCL, 110, {"side": "line"}),
    ("171-15", "bay_switch", "device_code", DTD, 110, {"dcl_side": "busbar_1", "position": "breaker_side"}),
    ("171-76", "bay_switch", "device_code", DTD, 110, {"dcl_side": "line", "position": "line_or_capacitor_side"}),
    ("131-08", "bay_switch", "device_code", DTD, 110, {"position": "transformer_neutral"}),
    ("431-38", "bay_switch", "device_code", DTD, 22, {"dcl_side": "transformer"}),
    ("TI 171", "ct", "device_code", {"current_transformer"}, 110, {}),
    ("TU173", "line_vt", "device_code", {"voltage_transformer"}, 110, {}),
    ("TUC11", "bus_vt", "device_code", {"voltage_transformer"}, 110, {}),
    ("TUC32-28", "host_switch", "device_code", DTD, 35, {"dcl_side": "busbar_2"}),
    ("CS1T1", "arrester", "device_code", {"surge_arrester"}, 110, {}),
    ("CS-TUC32", "arrester", "device_code", {"surge_arrester"}, 35, {}),
    ("CC-TD31", "fuse", "device_code", {"fuse_disconnector"}, 35, {}),
    ("TD41", "aux_transformer", "device_code", {"transformer_2w"}, 22, {}),
    ("TBN302", "capacitor", "device_code", {"shunt_capacitor"}, 35, {}),
    ("L302", "reactor", "device_code", {"shunt_reactor"}, 35, {}),
    ("T302", "capacitor_bay_breaker", "device_code", MC, 35, {}),
    ("T1", "main_transformer", "device_code", {"transformer_2w", "transformer_3w"}, None, {}),
    ("DP", "spare_bay", "bay_label", set(), None, {}),
    ("63/63/63MVA", "parameter", "parameter", set(), None, {}),
    ("115±9x1,78%/38,5±2x2,5%/23kV", "parameter", "parameter", set(), None, {}),
    # E18.9
    ("131-35", "bay_switch", "device_code", DTD, 110, {"dcl_side": "transformer", "position": "breaker_side"}),
    ("171-14", "bay_switch", "device_code", DTD, 110, {"position": "busbar_side"}),
    ("172-24", "bay_switch", "device_code", DTD, 110, {"dcl_side": "busbar_2"}),
    ("TUC31-14", "host_switch", "device_code", DTD, 35, {}),
    ("CCTUC31", "fuse", "device_code", {"fuse_disconnector"}, 35, {}),
    ("CS0T1", "arrester", "device_code", {"surge_arrester"}, None, {"side": "neutral"}),
    ("CS4T1", "arrester", "device_code", {"surge_arrester"}, 22, {}),
    ("CS TD31", "arrester", "device_code", {"surge_arrester"}, 35, {}),
    ("SI-TD31", "aux_unknown", "device_code", {"fuse_disconnector"} | DCL, 35, {}),
    ("02", "pole_number", "pole_number", set(), None, {}),
    ("Dự phòng", "spare_bay", "bay_label", set(), None, {}),
    ("22/0,4kV", "parameter", "parameter", set(), None, {}),
    # lỗi OCR
    ("I71-1", "bay_switch", "device_code", DCL, 110, {}),
    ("171 - 7", "bay_switch", "device_code", DCL, 110, {}),
    ("17I", "main_switch", "device_code", MC, 110, {}),
    ("TUC3I", "bus_vt", "device_code", {"voltage_transformer"}, 35, {}),
]


@pytest.mark.parametrize("raw,pattern_id,role,types,kv,details", CASES)
def test_real_labels(raw, pattern_id, role, types, kv, details):
    r = G.parse(raw)
    assert r is not None
    assert (r.pattern_id, r.role, set(r.expected_types), r.voltage_kv) == (pattern_id, role, types, kv)
    for k, v in details.items():
        assert r.details.get(k) == v
    assert not r.unknown_suffix


@pytest.mark.parametrize("raw", ["Cầu Phủ", "Thạch Tiến", "TG Cẩm Xuyên", "SOS", "BIO", ""])
def test_free_text_is_not_a_code(raw):
    assert G.parse(raw) is None


def test_unknown_suffix_keeps_both_switch_types():
    r = G.parse("171-99")
    assert r.unknown_suffix
    assert set(r.expected_types) == DCL | DTD


def test_expected_types_are_library_classes():
    classes = set(default_library().classes)
    for raw, *_ in CASES:
        assert set(G.parse(raw).expected_types) <= classes, raw


GENERATED = [
    (lambda: G.busbar(110, 1), "busbar", "C11"),
    (lambda: G.breaker(110, "line", 1), "main_switch", "171"),
    (lambda: G.breaker(22, "line", 12), "main_switch", "482"),
    (lambda: G.breaker(110, "transformer", 1), "main_switch", "131"),
    (lambda: G.bus_coupler(35, 1, 2), "main_switch", "312"),
    (lambda: G.capacitor_breaker(35, 1), "capacitor_bay_breaker", "T301"),
    (lambda: G.disconnector("171", "busbar_2"), "bay_switch", "171-2"),
    (lambda: G.earthing("171", "line", "line_or_capacitor_side"), "bay_switch", "171-76"),
    (lambda: G.earthing("TUC41", "busbar_1", "busbar_side"), "host_switch", "TUC41-14"),
    (lambda: G.earthing_neutral("131"), "bay_switch", "131-08"),
    (lambda: G.ct("171"), "ct", "TI171"),
    (lambda: G.line_vt("173"), "line_vt", "TU173"),
    (lambda: G.bus_vt(35, 2), "bus_vt", "TUC32"),
    (lambda: G.arrester("T1", 110), "arrester", "CS1T1"),
    (lambda: G.arrester("T1", neutral=True), "arrester", "CS0T1"),
    (lambda: G.arrester("TUC32"), "arrester", "CS-TUC32"),
    (lambda: G.fuse("TD31"), "fuse", "CC-TD31"),
    (lambda: G.aux_transformer(22, 1), "aux_transformer", "TD41"),
    (lambda: G.capacitor(35, 2), "capacitor", "TBN302"),
    (lambda: G.reactor(35, 2), "reactor", "L302"),
    (lambda: G.main_transformer(2), "main_transformer", "T2"),
]


@pytest.mark.parametrize("make,pattern_id,expected", GENERATED)
def test_generated_codes_parse_back(make, pattern_id, expected):
    code = make()
    assert code == expected
    r = G.parse(code)
    assert r.pattern_id == pattern_id and not r.unknown_suffix


def test_generator_rejects_unknown_voltage():
    with pytest.raises(ValueError):
        G.busbar(132, 1)
