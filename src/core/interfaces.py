"""Interface của từng khâu pipeline (SDD mục 7.2).

Cài đặt cổ điển và học sâu cùng tuân theo một Protocol, chọn qua configs/inference_config.yaml.
"""
from __future__ import annotations

from typing import Protocol

import numpy as np

from src.core.schema import SubstationTopologyGraph
from src.core.types import (DetectedSymbol, LabelAssignment, NormalizedImage, TextItem, WireExtraction,
                            WireSegment)


class ScaleNormalizer(Protocol):
    def normalize(self, image: np.ndarray, page_size_mm: tuple[float, float] | None) -> NormalizedImage: ...


class SymbolDetector(Protocol):
    def detect(self, img: NormalizedImage) -> list[DetectedSymbol]: ...


class OrientationEstimator(Protocol):
    def estimate(self, img: NormalizedImage, symbols: list[DetectedSymbol]) -> list[DetectedSymbol]: ...


class TextReader(Protocol):
    def read(self, img: NormalizedImage) -> list[TextItem]: ...


class LabelMatcher(Protocol):
    def match(self, texts: list[TextItem], symbols: list[DetectedSymbol],
              busbars: list[WireSegment]) -> list[LabelAssignment]: ...


class WireExtractor(Protocol):
    def extract(self, img: NormalizedImage, symbols: list[DetectedSymbol],
                texts: list[TextItem]) -> WireExtraction: ...


class JunctionClassifier(Protocol):
    def classify(self, img: NormalizedImage, extraction: WireExtraction) -> WireExtraction: ...


class GraphAssembler(Protocol):
    def assemble(self, img: NormalizedImage, symbols: list[DetectedSymbol], texts: list[TextItem],
                 labels: list[LabelAssignment], extraction: WireExtraction) -> SubstationTopologyGraph: ...


STAGES = {
    "scale_normalizer": ScaleNormalizer,
    "symbol_detector": SymbolDetector,
    "orientation_estimator": OrientationEstimator,
    "text_reader": TextReader,
    "label_matcher": LabelMatcher,
    "wire_extractor": WireExtractor,
    "junction_classifier": JunctionClassifier,
    "graph_assembler": GraphAssembler,
}
