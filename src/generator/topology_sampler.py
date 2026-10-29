"""Lấy mẫu cấu trúc trạm (SDD 8.1.2): cấp áp, MBA, thanh cái, danh sách ngăn và mã.

Đầu ra là TopologySpec thuần dữ liệu (chưa có tọa độ). Cùng một TopologySpec có thể được bố
cục và vẽ nhiều lần (biến thể); `topology_id` băm từ cấu trúc nên các biến thể chung một id và
split.json chia train/val theo id này.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, field
from pathlib import Path

import yaml

from src.core.evn_codes import EVNCodeGrammar

ROOT = Path(__file__).resolve().parents[2]
GENERATOR_CONFIG = ROOT / "configs" / "generator_config.yaml"
LAYOUT_PRESETS = ROOT / "configs" / "layout_presets.yaml"

# Tên lộ dùng làm nhãn line_name (địa danh chung, có dấu để thử OCR tiếng Việt)
PLACE_NAMES = (
    "Cẩm Xuyên", "Thiên Cầm", "Cẩm Nhượng", "Cẩm Minh", "Cẩm Trung", "Cẩm Thăng", "Cẩm Hòa", "Cẩm Hưng",
    "Kỳ Anh", "Thạch Linh", "Thạch Hội", "Thạch Tiến", "Lộc Hà", "Can Lộc", "Hồng Lĩnh", "Đức Thọ",
    "Hương Khê", "Vũ Quang", "Nghi Xuân", "Thạch Khê", "Cầu Phủ", "Tỉnh Ủy", "Thành Phố", "Thạch Việt",
    "Xuân Thành", "Thạch Hà", "Bắc Hồng", "Nam Hồng", "Trung Lương", "Đồng Môn", "Phù Việt", "Hộ Độ",
)
SPARE = "DỰ PHÒNG"


@dataclass
class BaySpec:
    kind: str                      # line | mv_feeder | tx_hv | incomer | coupler | bus_vt | aux | capacitor
    args: dict = field(default_factory=dict)


@dataclass
class BusSpec:
    code: str
    index: int
    outer: list[BaySpec] = field(default_factory=list)    # phía xuất tuyến
    inner: list[BaySpec] = field(default_factory=list)    # phía về MBA


@dataclass
class LevelSpec:
    role: str                      # hv | bottom | side
    kv: int
    buses: list[BusSpec]
    coupler: BaySpec | None = None


@dataclass
class TopologySpec:
    difficulty: str
    preset: str
    levels: list[LevelSpec]
    transformers: list[dict]       # {n, windings, hv_bus, sides: {role: (levelRole, busIndex)}}
    station_name: str
    topology_id: str = ""

    def level(self, role: str) -> LevelSpec | None:
        return next((lv for lv in self.levels if lv.role == role), None)

    def feeder_bay_count(self) -> int:
        n = 0
        for lv in self.levels:
            n += sum(1 for b in lv.buses for s in b.outer + b.inner if s.kind not in ("tx_hv", "incomer"))
            n += lv.coupler is not None
        return n

    def compute_id(self) -> str:
        data = asdict(self)
        data.pop("topology_id")
        data.pop("station_name")
        data.pop("preset")
        blob = json.dumps(data, sort_keys=True, ensure_ascii=False)
        self.topology_id = "topo-" + hashlib.sha1(blob.encode("utf-8")).hexdigest()[:10]
        return self.topology_id


def load_configs() -> tuple[dict, dict]:
    gen = yaml.safe_load(GENERATOR_CONFIG.read_text(encoding="utf-8"))
    presets = yaml.safe_load(LAYOUT_PRESETS.read_text(encoding="utf-8"))
    return gen, presets


def _weighted(rng: random.Random, weights: dict[str, float]) -> str:
    keys = list(weights)
    return rng.choices(keys, weights=[weights[k] for k in keys])[0]


class TopologySampler:
    def __init__(self, grammar: EVNCodeGrammar, gen_cfg: dict, presets_cfg: dict):
        self.grammar, self.cfg, self.presets = grammar, gen_cfg, presets_cfg

    def _name(self, rng: random.Random, used: set[str]) -> str:
        if rng.random() < self.cfg["topology"]["spare_name_p"]:
            return SPARE
        free = [n for n in PLACE_NAMES if n not in used] or list(PLACE_NAMES)
        name = rng.choice(free)
        used.add(name)
        return name

    def sample(self, rng: random.Random, difficulty: str | None = None, preset: str | None = None) -> TopologySpec:
        cfg, t = self.cfg, self.cfg["topology"]
        difficulty = difficulty or _weighted(rng, {k: v["weight"] for k, v in cfg["difficulty"].items()})
        d = cfg["difficulty"][difficulty]
        preset = preset or _weighted(rng, cfg["preset_weights"])
        p = self.presets["presets"][preset]
        kv = cfg["voltages"]
        n_tx = rng.randint(*d["transformers"])
        n_levels = rng.randint(*d["levels"])
        n_feeders = rng.randint(*d["feeder_bays"])
        used_names: set[str] = set()

        # Cấp áp: bottom luôn có; side chỉ khi 3 cấp
        bottom_role = p["bottom_level"]
        side_role = p["side_level"] if n_levels == 3 else None
        if n_levels == 2:
            bottom_role = rng.choice(("mv", "lv"))
        level_kv = {"hv": kv["hv"], "bottom": kv[bottom_role]}
        if side_role:
            level_kv["side"] = kv[side_role]

        # Ngân sách ngăn: đường dây 110 kV, phần còn lại chia cho cấp trung áp
        n_lines = min(rng.randint(*t["hv_lines"]), max(2, n_feeders // 3))
        two_hv = n_tx == 2 or (n_lines >= 3 and rng.random() < t["hv_two_buses_p"])
        mv_roles = ["bottom"] + (["side"] if side_role else [])
        two_mv = n_tx == 2

        levels: list[LevelSpec] = []
        hv = LevelSpec("hv", kv["hv"], [BusSpec(self.grammar.busbar(kv["hv"], i), i) for i in (1, 2)[:1 + two_hv]])
        levels.append(hv)
        for role in mv_roles:
            lv_kv = level_kv[role]
            levels.append(LevelSpec(role, lv_kv,
                                    [BusSpec(self.grammar.busbar(lv_kv, i), i) for i in (1, 2)[:1 + two_mv]]))

        budget = n_feeders
        # Liên lạc giữa hai thanh cái
        for lv in levels:
            if len(lv.buses) == 2:
                breaker = lv.role != "hv" or rng.random() < t["hv_coupler_breaker_p"]
                lv.coupler = BaySpec("coupler", {"kv": lv.kv, "breaker": breaker})
                budget -= 1
        # TU thanh cái
        for lv in levels:
            for bus in lv.buses:
                if budget > 2 and rng.random() < t["bus_vt_p"]:
                    is_hv = lv.role == "hv"
                    bus.inner.append(BaySpec("bus_vt", {"kv": lv.kv, "bus_n": bus.index, "disconnector": is_hv,
                                                        "fuse": not is_hv, "arrester": rng.random() < 0.3}))
                    budget -= 1
        # Đường dây 110 kV, đánh số lẻ cho C11, chẵn cho C12
        counters = {1: 1, 2: 2}
        for i in range(n_lines):
            bus = hv.buses[i % len(hv.buses)]
            idx = counters[bus.index]
            counters[bus.index] += 2
            if idx > 19:
                break
            turn = rng.choice((-1, 1)) if rng.random() < t["line_exit_turn_p"] else 0
            remote = f"{rng.choice((171, 172, 173, 174))} E18.{rng.randint(1, 30)} " if rng.random() < 0.4 else ""
            bus.outer.append(BaySpec("line", {
                "code": self.grammar.breaker(kv["hv"], "line", idx), "kv": kv["hv"], "bus_index": bus.index,
                "has_vt": rng.random() < t["line_vt_p"], "exit_turn": turn,
                "line_name": remote + self._name(rng, used_names),
                "pole": f"{rng.randint(1, 99):02d}" if rng.random() < t["pole_number_p"] else None,
                "earthing": tuple(e for e in ("15", "75", "76") if e == "76" or rng.random() < 0.7)}))
            budget -= 1

        # MBA
        transformers = []
        for n in range(1, n_tx + 1):
            # trạm 3 cấp áp: T1 luôn 3 cuộn để mọi cấp đều có nguồn
            windings = 3 if side_role and (n == 1 or rng.random() < t["windings3_p"]) else 2
            hv_bus = hv.buses[(n - 1) % len(hv.buses)].index
            sides = {"lv": ("bottom", 1)}
            if windings == 3:
                sides = {"lv": ("bottom", 1), "mv": ("side", 1)}
            elif side_role:
                sides = {"lv": (rng.choice(("bottom", "side")), 1)}
            sides = {r: (lvl, min(n, len(next(x for x in levels if x.role == lvl).buses))) for r, (lvl, _) in sides.items()}
            side_kvs = {r: level_kv[lvl] for r, (lvl, _) in sides.items()}
            transformers.append({"n": n, "windings": windings, "hv_bus": hv_bus, "sides": sides})
            mva = rng.choice((25, 40, 63))
            params = [f"{mva}/{mva}/{mva} MVA" if windings == 3 else f"{mva} MVA",
                      "115±9x1,78%/" + ("38,5±2x2,5%/" if windings == 3 else "") + f"{'23' if 22 in side_kvs.values() else '38,5'}kV"]
            next(b for b in hv.buses if b.index == hv_bus).inner.append(BaySpec("tx_hv", {
                "n": n, "hv_kv": kv["hv"], "side_kvs": side_kvs, "bus_index": hv_bus, "windings": windings,
                "arresters": rng.random() < 0.8, "cable": rng.random() < 0.7, "params": params}))
            for role, (lvl, bus_i) in sides.items():
                level = next(x for x in levels if x.role == lvl)
                bus = next(b for b in level.buses if b.index == bus_i)
                same = rng.random() < self.presets["presets"][preset]["incomer_same_side_p"]
                spec = BaySpec("incomer", {"n": n, "kv": level.kv, "bus_index": bus_i, "tx_side": role,
                                           "breaker": "drawout" if rng.random() < t["mv_breaker_drawout_p"] else "cb"})
                (bus.outer if same else bus.inner).insert(0, spec)

        # Tự dùng, tụ bù
        mv_levels = [x for x in levels if x.role != "hv"]
        if budget > 2 and rng.random() < t["aux_p"]:
            lv = rng.choice(mv_levels)
            kva = rng.choice((100, 160, 250))
            lv.buses[0].outer.append(BaySpec("aux", {"kv": lv.kv, "n": 1, "kind": "aux",
                                                     "breaker": rng.choice(("drawout", "fuse")),
                                                     "params": [f"{kva}kVA"]}))
            budget -= 1
        cap_levels = [x for x in mv_levels if x.kv == kv["mv"]]
        if cap_levels and budget > 2 and rng.random() < t["capacitor_p"]:
            lv = cap_levels[0]
            lv.buses[-1].outer.append(BaySpec("aux", {"kv": lv.kv, "n": 1, "kind": "capacitor",
                                                      "params": [f"{rng.choice((2, 4, 6))}MVAr"]}))
            budget -= 1

        # Xuất tuyến trung áp: đánh số 71, 72, … theo từng cấp
        feeder_idx = {x.role: 1 for x in mv_levels}
        k = 0
        while budget > 0 and mv_levels:
            lv = mv_levels[k % len(mv_levels)]
            bus = lv.buses[(feeder_idx[lv.role] - 1) % len(lv.buses)]
            idx = feeder_idx[lv.role]
            feeder_idx[lv.role] += 1
            k += 1
            if idx > 19:
                if all(v > 19 for v in feeder_idx.values()):
                    break
                continue
            drawout = rng.random() < t["mv_breaker_drawout_p"]
            bus.outer.append(BaySpec("mv_feeder", {
                "code": self.grammar.breaker(lv.kv, "line", idx), "kv": lv.kv, "bus_index": bus.index,
                "breaker": "drawout" if drawout else "cb", "cable": rng.random() < 0.7,
                "line_name": self._name(rng, used_names)}))
            budget -= 1

        spec = TopologySpec(difficulty=difficulty, preset=preset, levels=levels, transformers=transformers,
                            station_name=rng.choice(PLACE_NAMES))
        spec.compute_id()
        return spec

    @staticmethod
    def drop_one_feeder(spec: TopologySpec, rng: random.Random) -> bool:
        """Bỏ một ngăn xuất tuyến/đường dây (khi bố cục không vừa trang). False nếu không còn gì để bỏ."""
        candidates = [(bus, i) for lv in spec.levels for bus in lv.buses for i, s in enumerate(bus.outer)
                      if s.kind in ("mv_feeder", "line", "aux")]
        lines = [c for c in candidates if c[0].outer[c[1]].kind == "line"]
        if len(lines) <= 1:
            candidates = [c for c in candidates if c[0].outer[c[1]].kind != "line"]
        if not candidates:
            return False
        bus, i = rng.choice(candidates)
        bus.outer.pop(i)
        spec.compute_id()
        return True
