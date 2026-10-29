"""SDD 8.4.1: BFS tô vùng của SVG tương tác (JS) phải cho cùng kết quả với Energizer (Python).

Chạy đoạn script nhúng trong file SVG đã xuất bằng Node. Máy không có Node thì bỏ qua.
"""
import json
import random
import shutil
import subprocess

import pytest

from src.core.net_builder import Energizer
from src.core.symbol_lib import default_library
from src.exporters.svg_exporter import SVGInteractiveExporter, extract_embedded

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Máy không có Node.js để chạy phần JS của SVG")

HARNESS = r"""
const vm = require('vm'); const fs = require('fs');
const [scriptPath, casesPath] = process.argv.slice(2);
const ctx = {}; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(scriptPath, 'utf8'), ctx);
const cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
const out = cases.map((c) => ({
  result: ctx.SLD.energize(c.graph, c.meta, c.sources),
  defaultSources: ctx.SLD.defaultSources(c.graph, c.meta),
  next: c.toggle ? ctx.SLD.variantClass(c.meta, c.toggle, ctx.SLD.nextState(c.toggleState)) : null,
}));
process.stdout.write(JSON.stringify(out));
"""


def _python_result(graph, sources):
    res = Energizer(default_library()).energize(graph, sources)
    return {"states": {k: {"energized": v.energized, "earthed": v.earthed} for k, v in res.states.items()},
            "conflicts": res.conflicts}


def _state_variants(graph, n: int, seed: int):
    """Đồ thị gốc + n biến thể đổi ngẫu nhiên trạng thái thiết bị đóng/cắt."""
    lib = default_library()
    rng = random.Random(seed)
    switchable = [d.id for d in graph.devices if lib[d.detector_class].conduction in ("by_state", "earthing_by_state")]
    yield graph
    for _ in range(n):
        g = graph.model_copy(deep=True)
        for d in g.devices:
            if d.id in switchable:
                d.operational_state = rng.choice(["open", "closed", "closed", "unknown"])
        yield g


def run_cases(tmp_path, graphs, sources_list):
    exporter = SVGInteractiveExporter()
    svg = exporter.build(graphs[0])
    embedded = extract_embedded(svg)
    script = tmp_path / "sld.js"
    script.write_text(embedded["text/javascript"], encoding="utf-8")
    meta = json.loads(embedded["sld-meta"])
    cases = [{"graph": json.loads(g.to_json()), "meta": meta, "sources": s,
              "toggle": "disconnector_open", "toggleState": "open"} for g, s in zip(graphs, sources_list)]
    (tmp_path / "cases.json").write_text(json.dumps(cases, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "harness.js").write_text(HARNESS, encoding="utf-8")
    proc = subprocess.run([NODE, str(tmp_path / "harness.js"), str(script), str(tmp_path / "cases.json")],
                          capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def test_js_energizer_matches_python_on_slice(slice_graph, tmp_path):
    graphs = list(_state_variants(slice_graph, 25, seed=1))
    sources = [None] * len(graphs)
    out = run_cases(tmp_path, graphs, sources)
    lib = default_library()
    for g, s, js in zip(graphs, sources, out):
        assert js["result"] == _python_result(g, s)
        assert js["defaultSources"] == Energizer(lib).default_sources(g)
    assert out[0]["next"] == "disconnector_closed"


def test_js_energizer_explicit_sources(slice_graph, tmp_path):
    other = sorted(n.id for n in slice_graph.nets)[-1]
    graphs = list(_state_variants(slice_graph, 5, seed=2))
    sources = [[other]] * len(graphs)
    out = run_cases(tmp_path, graphs, sources)
    for g, s, js in zip(graphs, sources, out):
        assert js["result"] == _python_result(g, s)


def test_js_energizer_matches_python_on_synthetic(synthetic_layout, tmp_path):
    graphs = list(_state_variants(synthetic_layout.graph, 15, seed=3))
    sources = [None] * len(graphs)
    out = run_cases(tmp_path, graphs, sources)
    for g, s, js in zip(graphs, sources, out):
        assert js["result"] == _python_result(g, s)
