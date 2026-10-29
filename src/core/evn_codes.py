"""EVNCodeGrammar: chuẩn hóa, phân tích và sinh mã vận hành EVN (SDD 8.2.4).

Mọi mẫu và bảng tra nằm trong configs/evn_codes.yaml (Thông tư 44/2014/TT-BCT, Chương IV).
Bộ sinh dữ liệu dùng các hàm sinh mã ở cuối file, nên mã sinh ra luôn đọc lại được bằng
chính ngữ pháp này.
"""
from __future__ import annotations

import itertools
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from src.core.types import ParsedCode

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "configs" / "evn_codes.yaml"

_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class _Pattern:
    id: str
    regex: re.Pattern
    role: str
    types: frozenset[str]
    suffix_grammar: str | None
    voltage_from: tuple[str, ...]
    match_on_raw: bool


class EVNCodeGrammar:
    def __init__(self, cfg: dict):
        self.voltage_digit: dict[str, int] = {str(k): int(v) for k, v in cfg["voltage_digit"].items()}
        self.breaker_position_digit: dict[str, str] = {str(k): v for k, v in cfg["breaker_position_digit"].items()}
        self.disconnector_suffix: dict[str, str] = {str(k): v for k, v in cfg["disconnector_suffix"].items()}
        self.earthing_position_digit: dict[str, str] = {str(k): v for k, v in cfg["earthing_position_digit"].items()}
        self.earthing_special: dict[str, str] = {str(k): v for k, v in cfg["earthing_special_suffixes"].items()}
        self.suffix_types: dict[str, frozenset[str]] = {k: frozenset(v) for k, v in cfg["suffix_grammar_types"].items()}
        norm = cfg["normalize"]
        self.dashes: tuple[str, ...] = tuple(norm["dashes"])
        self.ocr_fixes: dict[str, str] = dict(norm["ocr_digit_fixes"])
        self.max_ocr_fixes: int = int(norm["max_ocr_fixes"])
        self.patterns: list[_Pattern] = []
        for p in cfg["patterns"]:
            vf = p.get("voltage_from", ())
            self.patterns.append(_Pattern(
                id=p["id"],
                regex=re.compile(p["regex"], re.IGNORECASE if p.get("ignore_case") else 0),
                role=p["role"],
                types=frozenset(p.get("types", ())),
                suffix_grammar=p.get("suffix_grammar"),
                voltage_from=(vf,) if isinstance(vf, str) else tuple(vf),
                match_on_raw=bool(p.get("match_on") == "raw"),
            ))
        self._digit_for_kv = {kv: d for d, kv in self.voltage_digit.items()}

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "EVNCodeGrammar":
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    # ---------------------------------------------------------------- phân tích

    def normalize(self, raw: str) -> str:
        """Viết hoa, bỏ mọi khoảng trắng, thống nhất dấu gạch. Chưa sửa lỗi OCR."""
        s = _WHITESPACE.sub("", raw).upper()
        for d in self.dashes:
            s = s.replace(d, "-")
        return s

    def _ocr_candidates(self, s: str):
        """Sinh các chuỗi đã sửa nhầm chữ↔số, ít ký tự sửa trước.

        Chỉ sửa khi chuỗi đã có chữ số thật, và số ký tự sửa không vượt số chữ số thật, để
        chữ thường (tên lộ) không bị biến thành mã.
        """
        digits = sum(ch.isdigit() for ch in s)
        positions = [i for i, ch in enumerate(s) if ch in self.ocr_fixes]
        limit = min(self.max_ocr_fixes, digits, len(positions))
        for k in range(1, limit + 1):
            for combo in itertools.combinations(positions, k):
                chars = list(s)
                for i in combo:
                    chars[i] = self.ocr_fixes[chars[i]]
                yield "".join(chars)

    def parse(self, raw: str) -> ParsedCode | None:
        """Chuỗi OCR → ParsedCode, hoặc None nếu không khớp mẫu nào (tên lộ, chữ khác)."""
        s = self.normalize(raw)
        if not s:
            return None
        code_patterns = [p for p in self.patterns if not p.match_on_raw]
        for candidate in itertools.chain((s,), self._ocr_candidates(s)):
            for p in code_patterns:
                m = p.regex.match(candidate)
                if m:
                    return self._build(p, m, candidate)
        for p in self.patterns:
            if p.match_on_raw and p.regex.search(raw):
                return ParsedCode(pattern_id=p.id, role=p.role, expected_types=p.types, voltage_kv=None,
                                  bay_code=None, suffix=None, normalized=raw.strip())
        return None

    def _voltage(self, p: _Pattern, m: re.Match) -> int | None:
        for g in p.voltage_from:
            d = m.groupdict().get(g)
            if d is not None:
                return self.voltage_digit.get(d)
        return None

    def _build(self, p: _Pattern, m: re.Match, normalized: str) -> ParsedCode:
        groups = m.groupdict()
        details: dict[str, str] = {}
        types = p.types
        suffix = groups.get("s")
        unknown_suffix = False
        bay_code = groups.get("bay") or groups.get("host")

        if p.id == "main_switch":
            bay_code = normalized
            t = groups["t"]
            if t in self.breaker_position_digit:
                details["position"] = self.breaker_position_digit[t]
            else:
                details["position"] = "bus_coupler"
                details["busbars"] = f"C{groups['v']}{t},C{groups['v']}{groups['n']}"
        elif p.id == "arrester" and groups.get("v") == "0":
            details["side"] = "neutral"

        if p.suffix_grammar == "switch":
            kind, info = self.classify_switch_suffix(suffix)
            details.update(info)
            if kind is None:
                unknown_suffix = True
                types = frozenset().union(*self.suffix_types.values())
            else:
                details["device"] = kind
                types = self.suffix_types[kind]

        return ParsedCode(pattern_id=p.id, role=p.role, expected_types=types, voltage_kv=self._voltage(p, m),
                          bay_code=bay_code, suffix=suffix, normalized=normalized, details=details,
                          unknown_suffix=unknown_suffix)

    def classify_switch_suffix(self, suffix: str) -> tuple[str | None, dict[str, str]]:
        """Điều 59–60: hậu tố → ("disconnector" | "earthing_switch" | None, ý nghĩa)."""
        if suffix in self.earthing_special:
            return "earthing_switch", {"position": self.earthing_special[suffix]}
        if len(suffix) == 1 and suffix in self.disconnector_suffix:
            return "disconnector", {"side": self.disconnector_suffix[suffix]}
        if len(suffix) == 2 and suffix[0] in self.disconnector_suffix and suffix[1] in self.earthing_position_digit:
            return "earthing_switch", {"dcl_side": self.disconnector_suffix[suffix[0]],
                                       "position": self.earthing_position_digit[suffix[1]]}
        return None, {}

    # ---------------------------------------------------------------- sinh mã (cho bộ sinh dữ liệu)

    def digit(self, voltage_kv: int) -> str:
        try:
            return self._digit_for_kv[voltage_kv]
        except KeyError:
            raise ValueError(f"Cấp điện áp {voltage_kv} kV không có trong voltage_digit") from None

    @staticmethod
    def _key_for(table: dict[str, str], meaning: str) -> str:
        for k, v in table.items():
            if v == meaning:
                return k
        raise ValueError(f"Không có mã cho '{meaning}'. Các giá trị: {sorted(set(table.values()))}")

    def busbar(self, voltage_kv: int, n: int) -> str:
        return f"C{self.digit(voltage_kv)}{n}"

    def breaker(self, voltage_kv: int, position: str, index: int) -> str:
        """position: line | transformer | aux_transformer | shunt_or_generator. Đường dây quá 10 ngăn dùng số 8."""
        if not 0 <= index <= 19:
            raise ValueError("index phải trong 0..19")
        if position == "line":
            pos, index = ("7", index) if index <= 9 else ("8", index - 10)
        else:
            if index > 9:
                raise ValueError("index phải trong 0..9")
            pos = self._key_for(self.breaker_position_digit, position)
        return f"{self.digit(voltage_kv)}{pos}{index}"

    def bus_coupler(self, voltage_kv: int, bus_a: int, bus_b: int) -> str:
        return f"{self.digit(voltage_kv)}{bus_a}{bus_b}"

    def capacitor_breaker(self, voltage_kv: int, index: int) -> str:
        return f"T{self.digit(voltage_kv)}0{index}"

    def disconnector(self, host: str, side: str) -> str:
        return f"{host}-{self._key_for(self.disconnector_suffix, side)}"

    def earthing(self, host: str, dcl_side: str, position: str) -> str:
        return (f"{host}-{self._key_for(self.disconnector_suffix, dcl_side)}"
                f"{self._key_for(self.earthing_position_digit, position)}")

    def earthing_neutral(self, transformer_breaker: str) -> str:
        return f"{transformer_breaker}-{self._key_for(self.earthing_special, 'transformer_neutral')}"

    @staticmethod
    def ct(host: str) -> str:
        return f"TI{host}"

    @staticmethod
    def line_vt(bay: str) -> str:
        return f"TU{bay}"

    def bus_vt(self, voltage_kv: int, n: int) -> str:
        return f"TUC{self.digit(voltage_kv)}{n}"

    def arrester(self, host: str, voltage_kv: int | None = None, neutral: bool = False) -> str:
        """CS + cấp áp + MBA (CS1T1), CS0 + MBA cho trung tính (CS0T1), CS- + thiết bị khác (CS-TUC32)."""
        if neutral:
            return f"CS0{host}"
        if voltage_kv is not None:
            return f"CS{self.digit(voltage_kv)}{host}"
        return f"CS-{host}"

    @staticmethod
    def fuse(host: str) -> str:
        return f"CC-{host}"

    def aux_transformer(self, voltage_kv: int, n: int) -> str:
        return f"TD{self.digit(voltage_kv)}{n}"

    def capacitor(self, voltage_kv: int, index: int) -> str:
        return f"TBN{self.digit(voltage_kv)}0{index}"

    def reactor(self, voltage_kv: int, index: int) -> str:
        return f"L{self.digit(voltage_kv)}0{index}"

    @staticmethod
    def main_transformer(n: int) -> str:
        return f"T{n}"


@lru_cache(maxsize=1)
def default_grammar() -> EVNCodeGrammar:
    return EVNCodeGrammar.load()
