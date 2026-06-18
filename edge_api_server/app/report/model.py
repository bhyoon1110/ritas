"""보고서 문서/섹션 데이터 구조와 직렬화(JSON/Markdown)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# LLM이 채울 수 있는 자유서술 슬롯 섹션 ID.
LLM_SLOT_IDS: frozenset[str] = frozenset({"summary", "narrative", "caption"})


@dataclass
class ReportTable:
    columns: list[str]
    rows: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {"columns": list(self.columns), "rows": [list(r) for r in self.rows]}


@dataclass
class ReportSection:
    section_id: str
    heading: str
    paragraphs: list[str] = field(default_factory=list)
    bullets: list[str] = field(default_factory=list)
    table: ReportTable | None = None
    # "rule": 규칙이 결정론적으로 작성, "llm": LLM이 채운 자유서술
    source: str = "rule"

    def to_dict(self) -> dict[str, Any]:
        return {
            "sectionId": self.section_id,
            "heading": self.heading,
            "paragraphs": list(self.paragraphs),
            "bullets": list(self.bullets),
            "table": self.table.to_dict() if self.table else None,
            "source": self.source,
        }


@dataclass
class ReportDocument:
    job_id: str
    title: str
    experiment_code: str
    pk: dict[str, str]
    generated_at: str
    sections: list[ReportSection] = field(default_factory=list)
    llm_used: bool = False
    llm_error: str | None = None

    def section(self, section_id: str) -> ReportSection | None:
        for section in self.sections:
            if section.section_id == section_id:
                return section
        return None

    def apply_llm_slots(self, slots: dict[str, str]) -> None:
        """LLM이 반환한 슬롯 텍스트를 해당 섹션에 채운다."""
        for section_id, text in slots.items():
            section = self.section(section_id)
            if section is None or not text or not text.strip():
                continue
            section.paragraphs = [text.strip()]
            section.source = "llm"

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "title": self.title,
            "experimentCode": self.experiment_code,
            "pk": dict(self.pk),
            "generatedAt": self.generated_at,
            "llm": {"used": self.llm_used, "error": self.llm_error},
            "sections": [section.to_dict() for section in self.sections],
        }

    def to_markdown(self) -> str:
        lines: list[str] = [f"# {self.title}", ""]
        meta = (
            f"- 요청번호: {self.pk.get('requestNumber', '')}\n"
            f"- 실험: {self.experiment_code}  장비: {self.pk.get('equipmentCode', '')}"
            f"  작업자: {self.pk.get('operatorId', '')}\n"
            f"- 생성시각: {self.generated_at}"
        )
        lines.append(meta)
        lines.append("")
        for section in self.sections:
            lines.append(f"## {section.heading}")
            if section.source == "llm":
                lines.append("<!-- LLM 보조 작성 -->")
            for paragraph in section.paragraphs:
                lines.append(paragraph)
                lines.append("")
            for bullet in section.bullets:
                lines.append(f"- {bullet}")
            if section.bullets:
                lines.append("")
            if section.table is not None:
                lines.append("| " + " | ".join(section.table.columns) + " |")
                lines.append(
                    "| " + " | ".join("---" for _ in section.table.columns) + " |"
                )
                for row in section.table.rows:
                    lines.append("| " + " | ".join(row) + " |")
                lines.append("")
        if self.llm_error:
            lines.append(f"> LLM 보조 설명 생성 실패: {self.llm_error} (규칙 기반 문안 사용)")
            lines.append("")
        return "\n".join(lines).rstrip() + "\n"
