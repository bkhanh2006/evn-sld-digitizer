"""Sinh bộ dữ liệu sơ đồ nhất thứ tổng hợp (SDD 8.1).

    python scripts/generate_substation_dataset.py --n 100
    python scripts/generate_substation_dataset.py --n 20 --seed 7 --out data/synthetic/thu

Mỗi topology được vẽ `variants_per_topology` lần (bố cục, font, nét khác nhau). Topology không bố
cục được thì bỏ bớt ngăn rồi thử lại. Kết quả: images/ clean/ svg/ dxf/ masks/ graphs/,
annotations_coco.json, split.json (chia theo topologyId), manifest.json (seed, git, cấu hình).
"""
from __future__ import annotations

import argparse
import datetime as dt
import random
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core.evn_codes import default_grammar  # noqa: E402
from src.core.symbol_lib import default_library  # noqa: E402
from src.generator.dataset_writer import DatasetWriter  # noqa: E402
from src.generator.layout_composer import GENERATOR_VERSION, LayoutComposer, LayoutFailed  # noqa: E402
from src.generator.renderer import SyntheticRenderer  # noqa: E402
from src.generator.topology_sampler import TopologySampler, load_configs  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
MAX_DROPS = 12


def git_info() -> dict:
    def run(*args):
        try:
            return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=10).stdout.strip()
        except OSError:
            return ""
    return {"commit": run("rev-parse", "HEAD"), "dirty": bool(run("status", "--porcelain"))}


def generate(out_dir: Path, n: int, seed: int, gen_cfg: dict | None = None, presets: dict | None = None,
             log=print) -> DatasetWriter:
    loaded_gen, loaded_presets = load_configs()
    gen_cfg, presets = gen_cfg or loaded_gen, presets or loaded_presets
    lib, grammar = default_library(), default_grammar()
    sampler = TopologySampler(grammar, gen_cfg, presets)
    composer = LayoutComposer(lib, grammar, gen_cfg, presets)
    renderer = SyntheticRenderer(lib, gen_cfg)
    writer = DatasetWriter(out_dir, lib)
    variants = gen_cfg["variants_per_topology"]
    failures: list[dict] = []
    t0 = time.time()
    index, t = 0, 0
    while index < n:
        trng = random.Random(f"{seed}-topo-{t}")
        topo = sampler.sample(trng)
        layout = None
        for k in range(MAX_DROPS):
            try:
                layout = composer.compose(topo, random.Random(f"{seed}-{t}-layout-0-{k}"))
                break
            except LayoutFailed as e:
                failures.append({"topology": t, "stage": "compose", "reason": str(e)[:200]})
                if not sampler.drop_one_feeder(topo, trng):
                    break
        if layout is None:
            t += 1
            continue
        for v in range(variants):
            if index >= n:
                break
            if v > 0:
                try:
                    layout = composer.compose(topo, random.Random(f"{seed}-{t}-layout-{v}"))
                except LayoutFailed as e:
                    failures.append({"topology": t, "stage": "variant", "reason": str(e)[:200]})
                    continue
            render = renderer.render(layout, random.Random(f"{seed}-{t}-render-{v}"))
            rec = writer.write(index, layout, render, seed)
            log(f"{rec['name']}  {topo.difficulty:7s} {topo.preset:10s} {layout.page_name}  "
                f"{rec['devices']:3d} thiết bị  {rec['crossings']} điểm giao  ({time.time() - t0:.0f}s)")
            index += 1
        t += 1

    manifest = {
        "generatorVersion": GENERATOR_VERSION, "seed": seed, "n": n, "createdAt": dt.datetime.now().isoformat(),
        "git": git_info(), "topologies": len({r["topologyId"] for r in writer.records}),
        "layoutFailures": failures, "seconds": round(time.time() - t0, 1),
        "generatorConfig": gen_cfg, "layoutPresets": presets,
    }
    writer.finalize(manifest)
    return writer


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--n", type=int, default=None, help="Số mẫu (mặc định n_samples trong cấu hình)")
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--out", type=Path, default=None, help="Mặc định data/synthetic/<version>_n<N>")
    args = parser.parse_args()
    gen_cfg, presets = load_configs()
    n = args.n or gen_cfg["n_samples"]
    seed = gen_cfg["seed"] if args.seed is None else args.seed
    out = args.out or ROOT / "data" / "synthetic" / f"{gen_cfg['version']}_n{n}"
    writer = generate(out, n, seed, gen_cfg, presets)
    print(f"Đã sinh {len(writer.records)} mẫu từ {len({r['topologyId'] for r in writer.records})} topology vào {out}")


if __name__ == "__main__":
    main()
