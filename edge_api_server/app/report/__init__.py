"""규칙 기반 보고서 파이프라인 패키지.

- model: 보고서 문서/섹션 데이터 구조
- builders: 실험 종류별 규칙 기반 보고서 작성기(verdict JSON -> 고정 양식)
- annotator: LLM이 캡션/요약/보조설명 슬롯만 채우는 보조 단계(실패 허용)
- pipeline: 위 단계를 조합해 report.json / report.md 산출
"""

from __future__ import annotations

from .model import ReportDocument, ReportSection, ReportTable
from .pipeline import generate_report

__all__ = [
    "ReportDocument",
    "ReportSection",
    "ReportTable",
    "generate_report",
]
