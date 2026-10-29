from src.core.net_builder import Energizer, NetBuilder, check_nets_consistent, nets_as_sets
from src.core.schema import SubstationTopologyGraph, check_invariants

from tests.conftest import busbar_ref, node_ref, port, wire_ref


def _net_of(graph, key):
    return graph.net_index()[key]


def test_line_bay_is_valid(line_bay):
    g = line_bay.graph()
    assert check_invariants(g) == []
    assert check_nets_consistent(g, line_bay.lib)
    # C11 + dây xuống DCL | DCL–MC | MC–TI–DTĐ–đường dây (TI hợp hai phía) | đất không có
    assert len(g.nets) == 3


def test_breaker_sides_are_two_nets(line_bay):
    g = line_bay.graph()
    assert _net_of(g, ("port", "dev-171", "p1")) != _net_of(g, ("port", "dev-171", "p2"))


def test_current_transformer_merges_both_sides(line_bay):
    g = line_bay.graph()
    assert _net_of(g, ("port", "dev-TI171", "p1")) == _net_of(g, ("port", "dev-TI171", "p2"))
    assert _net_of(g, ("port", "dev-171", "p1")) == _net_of(g, ("port", "dev-line", "p1"))


def test_net_voltage_from_codes(line_bay):
    g = line_bay.graph()
    assert {n.voltage_kv for n in g.nets} == {110}


def test_voltage_conflict_issue(line_bay):
    line_bay.device("dev-371", "circuit_breaker", 900, 900, code="371")
    line_bay.wire("w-x", port("dev-371", "p1"), port("dev-171", "p1"),
                  points=[line_bay._point(port("dev-371", "p1")), line_bay._point(port("dev-171", "p1"))])
    g = line_bay.graph(with_nets=False)
    issues = NetBuilder(line_bay.lib).build(g).issues
    assert [i.type for i in issues] == ["voltage_conflict"]


def test_crossing_does_not_merge_but_junction_does(builder):
    b = builder
    for name, x, y in (("a", 100, 100), ("b", 300, 100), ("c", 200, 0), ("d", 200, 200)):
        b.device(f"dev-{name}", "line_feeder_arrow", x, y)
    b.node("x1", "crossing_no_connect", "plain_cross", (200, 100), [wire_ref("w-h"), wire_ref("w-v")])
    b.wire("w-h", port("dev-a", "p1"), port("dev-b", "p1"))
    b.wire("w-v", port("dev-c", "p1"), port("dev-d", "p1"))
    g = b.graph()
    assert _net_of(g, ("wire", "w-h")) != _net_of(g, ("wire", "w-v"))

    g.nodes[0] = g.nodes[0].model_copy(update={"type": "junction", "style": "dot"})
    rebuilt = NetBuilder(b.lib).build(g).nets
    assert frozenset({("wire", "w-h"), ("wire", "w-v"), ("port", "dev-a", "p1"), ("port", "dev-b", "p1"),
                      ("port", "dev-c", "p1"), ("port", "dev-d", "p1")}) in nets_as_sets(rebuilt)


def test_two_closed_earthing_switches_do_not_join_nets(builder):
    b = builder
    b.device("dev-es1", "earthing_switch_closed", 100, 100, code="171-76", state="closed")
    b.device("dev-es2", "earthing_switch_closed", 400, 100, code="173-76", state="closed")
    b.device("dev-l1", "line_feeder_arrow", 100, 0, rotation=180)
    b.device("dev-l2", "line_feeder_arrow", 400, 0, rotation=180)
    b.wire("w1", port("dev-l1", "p1"), port("dev-es1", "p1"))
    b.wire("w2", port("dev-l2", "p1"), port("dev-es2", "p1"))
    g = b.graph()
    assert len(g.nets) == 2
    assert all(n.kind == "power" for n in g.nets)


def test_ground_symbol_net_kind(builder):
    b = builder
    b.device("dev-g", "ground_symbol", 100, 100)
    b.device("dev-l", "line_feeder_arrow", 100, 0, rotation=180)
    b.wire("w", port("dev-l", "p1"), port("dev-g", "p1"))
    assert [n.kind for n in b.graph().nets] == ["ground"]


def test_disconnector_bypass_is_reported(builder):
    b = builder
    b.busbar("bus-C11", "C11", [(0, 300), (400, 300)])
    b.device("dev-171-1", "disconnector_open", 200, 200, code="171-1")
    b.node("j", "junction", "t_on_busbar", (200, 300), [busbar_ref("bus-C11"), wire_ref("w-low")])
    b.node("j2", "junction", "t_on_busbar", (260, 300), [busbar_ref("bus-C11"), wire_ref("w-up")])
    b.wire("w-low", node_ref("j"), port("dev-171-1", "p2"), points=[(200, 300), (200, 220)])
    b.wire("w-up", port("dev-171-1", "p1"), node_ref("j2"), points=[(200, 180), (260, 180), (260, 300)])
    issues = NetBuilder(b.lib).build(b.graph(with_nets=False)).issues
    assert [i.type for i in issues] == ["device_bypassed"]


def test_candidate_connection_marks_net_pending(builder):
    b = builder
    b.device("dev-a", "line_feeder_arrow", 100, 100)
    b.device("dev-b", "current_transformer", 100, 0)
    b.wire("w", port("dev-a", "p1"), port("dev-b", "p2"), status="candidate")
    statuses = {n.review_status for n in b.graph().nets}
    assert statuses == {"pending"}


def test_json_round_trip_uses_sdd_field_names(line_bay):
    g = line_bay.graph()
    text = g.to_json()
    assert '"from"' in text and '"deviceId"' in text and '"connectionStatus"' in text
    assert "from_" not in text and "device_id" not in text
    again = SubstationTopologyGraph.from_json(text)
    assert again == g


# ------------------------------------------------------------------ Energizer

def _energize(b, breaker_state):
    g = b.graph()
    g.device("dev-171").operational_state = breaker_state
    return g, Energizer(b.lib).energize(g)


def test_energizer_breaker_closed(line_bay):
    g, res = _energize(line_bay, "closed")
    assert all(s.energized is True for s in res.states.values())
    assert res.conflicts == []


def test_energizer_breaker_open(line_bay):
    g, res = _energize(line_bay, "open")
    assert res.states[_net_of(g, ("port", "dev-171", "p1"))].energized is True
    assert res.states[_net_of(g, ("port", "dev-171", "p2"))].energized is False
    assert res.states[_net_of(g, ("busbar", "bus-C11"))].energized is False


def test_energizer_breaker_unknown(line_bay):
    g, res = _energize(line_bay, "unknown")
    assert res.states[_net_of(g, ("port", "dev-171", "p2"))].energized is None
    assert res.states[_net_of(g, ("busbar", "bus-C11"))].energized is None


def test_energizer_earthed_and_energized_conflict(line_bay):
    g = line_bay.graph()
    g.device("dev-171-76").operational_state = "closed"
    res = Energizer(line_bay.lib).energize(g)
    line_net = _net_of(g, ("port", "dev-line", "p1"))
    assert res.states[line_net].earthed is True
    assert res.conflicts == [line_net]


def test_default_source_is_line_terminal(line_bay):
    g = line_bay.graph()
    assert Energizer(line_bay.lib).default_sources(g) == [_net_of(g, ("port", "dev-line", "p1"))]
