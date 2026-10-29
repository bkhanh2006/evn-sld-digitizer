"""Ánh xạ tên cài đặt trong configs/inference_config.yaml → class (SDD 7.2).

    @register("wire_extractor", "classical")
    class ClassicalWireExtractor: ...

    extractor = create("wire_extractor", "classical", **params)
"""
from __future__ import annotations

from typing import Callable, TypeVar

from src.core.interfaces import STAGES

T = TypeVar("T")
_REGISTRY: dict[str, dict[str, type]] = {stage: {} for stage in STAGES}


def register(stage: str, name: str) -> Callable[[type[T]], type[T]]:
    if stage not in _REGISTRY:
        raise KeyError(f"Khâu không tồn tại: {stage}. Các khâu: {', '.join(_REGISTRY)}")

    def deco(cls: type[T]) -> type[T]:
        if name in _REGISTRY[stage]:
            raise ValueError(f"'{name}' đã được đăng ký cho khâu {stage}")
        _REGISTRY[stage][name] = cls
        return cls

    return deco


def create(stage: str, name: str, **params):
    try:
        cls = _REGISTRY[stage][name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY.get(stage, {}))) or "(chưa có)"
        raise KeyError(f"Chưa có cài đặt '{name}' cho khâu {stage}. Đang có: {available}") from None
    return cls(**params)


def available(stage: str) -> list[str]:
    return sorted(_REGISTRY[stage])
