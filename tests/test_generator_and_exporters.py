"""SDD 10.4: bộ sinh nhỏ (seed cố định) → đủ 8 tiêu chí validate_dataset, DXF round-trip, SVG đếm được <use>."""
import json
import random
import xml.etree.ElementTree as ET

import pytest

from scripts.generate_substation_dataset import generate
from scripts.validate_dataset import CRITERIA, check_sample, validate_dataset
from src.core.evn_codes import default_grammar
from src.core.geometry import Placement
from src.core.net_builder import NetBuilder, check_nets_consistent
from src.core.schema import (Busbar, BusbarRef, NodeRef, Page, PortRef, Provenance, SubstationTopologyGraph,
                             check_invariants)
from src.core.symbol_lib import default_library
from src.core.symbol_svg import SVG_NS
from src.generator import bay_templates as bt
from src.generator.layout_composer import LayoutComposer, LayoutFailed, canonical_pose, find_overlaps
from src.generator.router import OrthogonalRouter
from src.generator.topology_sampler import TopologySampler, load_configs

LIB = default_library()
GRAMMAR = default_grammar()


@pytest.fixture(scope="module")
def dataset(tmp_path_factory):
    out = tmp_path_factory.mktemp("syn") / "t_n3"
    generate(out, 3, seed=11, log=lambda *_: None)
    return out


@pytest.fixture(scope="module")
def layout(synthetic_layout):
    return synthetic_layout


# ---------------------------------------------------------------------------- bộ dữ liệu

def test_dataset_passes_all_criteria(dataset):
    report = validate_dataset(dataset, log=lambda *_: None)
    assert report["total"] == 3
    assert report["failures"] == {}
    assert report["perCriterion"] == {c: 3 for c in CRITERIA}


def test_dataset_files_and_coco(dataset):
    coco = json.loads((dataset / "annotations_coco.json").read_text(encoding="utf-8"))
    assert len(coco["categories"]) == len(LIB.classes)
    for im in coco["images"]:
        name = im["file_name"].split("/")[-1][:-4]
        for rel in (f"images/{name}.png", f"clean/{name}.png", f"svg/{name}.svg", f"dxf/{name}.dxf",
                    f"masks/{name}_wire.png", f"graphs/{name}_graph.json"):
            assert (dataset / rel).is_file(), rel
        g = SubstationTopologyGraph.from_json((dataset / "graphs" / f"{name}_graph.json").read_text(encoding="utf-8"))
        anns = [a for a in coco["annotations"] if a["image_id"] == im["id"]]
        assert len(anns) == len(g.devices)
        root = ET.parse(dataset / "svg" / f"{name}.svg").getroot()
        layer = next(e for e in root.iter(f"{SVG_NS}g") if e.get("id") == "layer-devices")
        assert len(list(layer.iter(f"{SVG_NS}use"))) == len(g.devices)
        assert g.provenance.source == "synthetic_gt" and g.provenance.seed == 11


def test_split_by_topology(dataset):
    split = json.loads((dataset / "split.json").read_text(encoding="utf-8"))
    assert split["by"] == "topologyId"
    assert not set(split["train"]) & set(split["val"])
    manifest = json.loads((dataset / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["seed"] == 11 and manifest["n"] == 3 and "generatorConfig" in manifest


def test_validator_catches_broken_sample(dataset, tmp_path):
    """Làm hỏng một đầu dây và chồng một nhãn: validate phải báo trượt đúng tiêu chí."""
    coco = json.loads((dataset / "annotations_coco.json").read_text(encoding="utf-8"))
    im = coco["images"][0]
    name = im["file_name"].split("/")[-1][:-4]
    for sub in ("graphs", "clean", "masks", "dxf"):
        (tmp_path / sub).mkdir()
        for f in (dataset / sub).glob(f"{name}*"):
            (tmp_path / sub / f.name).write_bytes(f.read_bytes())
    path = tmp_path / "graphs" / f"{name}_graph.json"
    g = SubstationTopologyGraph.from_json(path.read_text(encoding="utf-8"))
    w = next(w for w in g.wires if isinstance(w.to, PortRef))
    w.points[-1] = (w.points[-1][0] + 0.5, w.points[-1][1])
    g.texts[1].bbox = g.texts[0].bbox
    path.write_text(g.to_json(), encoding="utf-8")
    res = check_sample(tmp_path, im, [a for a in coco["annotations"] if a["image_id"] == im["id"]])
    assert res["3_wire_ends"] and res["4_no_overlap"]


# ---------------------------------------------------------------------------- bố cục

def test_layout_graph_consistent(layout):
    g = layout.graph
    assert check_invariants(g) == []
    assert check_nets_consistent(g, LIB)
    boxes = [(d.id, (d.bbox[0], d.bbox[1], d.bbox[0] + d.bbox[2], d.bbox[1] + d.bbox[3])) for d in g.devices]
    assert find_overlaps(boxes) == []


def test_generated_codes_parse_to_device_class(layout):
    for d in layout.graph.devices:
        if d.code is None:
            continue
        parsed = GRAMMAR.parse(d.code.normalized)
        assert parsed is not None and d.detector_class in parsed.expected_types, (d.code.normalized, d.detector_class)


def test_layout_is_deterministic():
    gen, presets = load_configs()

    def once():
        sampler = TopologySampler(GRAMMAR, gen, presets)
        composer = LayoutComposer(LIB, GRAMMAR, gen, presets)
        rng = random.Random(5)
        topo = sampler.sample(rng, difficulty="simple", preset="E18_like")
        return composer.compose(topo, random.Random(9)).graph.to_json()

    assert once() == once()


def test_transformer_windings_reach_other_levels(layout):
    """Mọi Net có điện khi đóng hết: nguồn 110 kV qua MBA tới thanh cái trung áp."""
    from src.core.net_builder import Energizer
    g = layout.graph
    res = Energizer(LIB).energize(g)
    index = g.net_index()
    for b in g.busbars:
        assert res.states[index[("busbar", b.id)]].energized is not False, b.code


# ---------------------------------------------------------------------------- mẫu ngăn, tư thế, router

TEMPLATES = [
    (bt.line_bay, dict(code="171", kv=110, has_vt=True, exit_turn=1, line_name="Cẩm Hòa", pole="02")),
    (bt.line_bay, dict(code="173", kv=110, has_vt=True, exit_turn=0)),
    (bt.mv_feeder_bay, dict(code="371", kv=35, line_name="Thiên Cầm")),
    (bt.mv_feeder_bay, dict(code="471", kv=22, breaker="cb")),
    (bt.transformer_bay, dict(n=1, hv_kv=110, side_kvs={"mv": 22, "lv": 35})),
    (bt.transformer_bay, dict(n=2, hv_kv=110, side_kvs={"lv": 22}, windings=2)),
    (bt.incomer_bay, dict(n=1, kv=35)),
    (bt.bus_coupler_bay, dict(kv=110)),
    (bt.bus_vt_bay, dict(kv=110, bus_n=1, fuse=True, arrester=True)),
    (bt.aux_or_capacitor_bay, dict(kv=22, n=1)),
    (bt.aux_or_capacitor_bay, dict(kv=35, n=1, kind="capacitor")),
]


@pytest.mark.parametrize("fn,kw", TEMPLATES, ids=lambda v: getattr(v, "__name__", ""))
def test_bay_template_is_well_formed(fn, kw):
    """Mỗi cổng có đúng 1 dây (trừ cổng ngoài), mã đọc lại đúng loại, nút thanh cái ở gốc."""
    sub = fn(LIB, GRAMMAR, style=bt.BayStyle(), rng=random.Random(0), **kw)
    assert sub.bus_attach_nodes and next(n for n in sub.nodes if n.id == sub.bus_attach_nodes[0]).position == (0, 0)
    count = {(d.id, p.id): 0 for d in sub.devices for p in d.ports}
    for w in sub.wires:
        for end, pt in ((w.from_, w.points[0]), (w.to, w.points[-1])):
            if isinstance(end, PortRef):
                count[(end.device_id, end.port_id)] += 1
                p = next(d for d in sub.devices if d.id == end.device_id).port(end.port_id)
                assert (p.x, p.y) == pytest.approx(pt, abs=1e-9)
    external = {(r.device_id, r.port_id) for r in sub.external_ports.values()}
    assert {k for k, c in count.items() if c != 1} == external
    for d in sub.devices:
        if d.code:
            assert d.detector_class in GRAMMAR.parse(d.code.normalized).expected_types


def test_bay_template_builds_valid_graph():
    """Ngăn đường dây treo vào thanh cái → đồ thị hợp lệ, nets dựng lại đúng."""
    sub = bt.line_bay(LIB, GRAMMAR, "171", 110, bt.BayStyle(), random.Random(1), has_vt=True, exit_turn=-1)
    node = next(n for n in sub.nodes if n.id == sub.bus_attach_nodes[0])
    node.connections.append(BusbarRef(id="bus-C11"))
    g = SubstationTopologyGraph(provenance=Provenance(source="synthetic_gt"),
                                page=Page(id="t", width=100, height=100, px_per_mm=1, px_per_mm_source="synthetic"),
                                bays=[sub.bay], devices=sub.devices, wires=sub.wires, nodes=sub.nodes,
                                busbars=[Busbar(id="bus-C11", code="C11", voltage_kv=110,
                                                polyline=[(-20, 0), (20, 0)], stroke_width_px=0.5)])
    g.nets = NetBuilder(LIB).build(g).nets
    assert check_invariants(g) == []
    assert all(n.voltage_kv in (110, None) for n in g.nets)


@pytest.mark.parametrize("cls", [c for c in LIB.classes])
@pytest.mark.parametrize("rot", (0, 90, 180, 270))
@pytest.mark.parametrize("mirror", (False, True))
def test_canonical_pose_keeps_port_set(cls, rot, mirror):
    """Tư thế chuẩn hóa cho cùng tập vị trí cổng (có thể đổi tên giữa hai cổng tương đương)."""
    sym = LIB[cls]
    r2, m2 = canonical_pose(sym, rot, mirror)
    assert r2 in sym.allowed_rotations_deg and (not m2 or sym.allow_mirror)
    a = sorted(tuple(round(v, 9) for v in p.xy) for p in LIB.project_ports(cls, Placement(10, 20, rot, mirror, 1.0)))
    b = sorted(tuple(round(v, 9) for v in p.xy) for p in LIB.project_ports(cls, Placement(10, 20, r2, m2, 1.0)))
    assert a == b


def test_router_avoids_obstacles_and_records_crossing():
    r = OrthogonalRouter((0, 0, 100, 100), 1.0, 0.8, 5.0, 20.0)
    r.add_obstacle((40, 0, 45, 70))
    r.add_wire("w1", [(60, 10), (60, 90)])
    path = r.route((10, 50), (1, 0), (90, 50), (-1, 0))
    assert path[0] == (10, 50) and path[-1] == (90, 50)
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        assert ax == bx or ay == by
        if ay == by and min(ax, bx) < 45.8 and max(ax, bx) > 39.2:
            assert ay > 70.8                                   # đi vòng qua đỉnh vật cản
    cr = r.crossings(path)
    assert len(cr) == 1 and cr[0].wire_id == "w1" and cr[0].position[0] == 60


def test_router_returns_none_when_blocked():
    r = OrthogonalRouter((0, 0, 50, 50), 1.0, 0.5, 5.0, 20.0)
    r.add_obstacle((25, -5, 26, 55))
    assert r.route((5, 25), (1, 0), (45, 25), (-1, 0)) is None


def test_router_does_not_run_along_existing_wire():
    r = OrthogonalRouter((0, 0, 60, 60), 1.0, 0.8, 1.0, 1000.0)
    r.add_wire("w", [(10, 30), (50, 30)])
    path = r.route((5, 20), (0, 1), (55, 20), (0, 1))
    assert path is not None
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        if ay == by:
            assert abs(ay - 30) > 0.8
