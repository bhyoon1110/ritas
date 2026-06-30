"""보고서 문서/섹션 데이터 구조와 직렬화(JSON/Markdown)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# LLM이 채울 수 있는 자유서술 슬롯 섹션 ID.
LLM_SLOT_IDS: frozenset[str] = frozenset(
    {
        "summary",
        "key_findings",
        "interpretation",
        "qc_notes",
        "narrative",
        "caption",
    }
)
LLM_AUXILIARY_IDS: frozenset[str] = frozenset({"email_subject", "email_body"})


def _markdown_table_cell(value: str) -> str:
    """Markdown table 셀이 행/열 구조를 깨지 않도록 이스케이프한다."""
    return str(value).replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


@dataclass
class ReportTable:
    columns: list[str]
    rows: list[list[str]]

    def to_dict(self) -> dict[str, Any]:
        return {"columns": list(self.columns), "rows": [list(r) for r in self.rows]}


@dataclass
class ReportFigure:
    figure_id: str
    title: str
    path: str
    caption_slot: str = "caption"

    def to_dict(self) -> dict[str, Any]:
        return {
            "figureId": self.figure_id,
            "title": self.title,
            "path": self.path,
            "captionSlot": self.caption_slot,
        }


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
    figures: list[ReportFigure] = field(default_factory=list)
    auxiliary_texts: dict[str, str] = field(default_factory=dict)
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
            if not text or not text.strip():
                continue
            if section_id in LLM_AUXILIARY_IDS:
                self.auxiliary_texts[section_id] = text.strip()
                continue
            section = self.section(section_id)
            if section is None:
                continue
            section.paragraphs = [text.strip()]
            section.source = "llm"

    def ensure_auxiliary_texts(self, defaults: dict[str, str]) -> None:
        for key in LLM_AUXILIARY_IDS:
            value = defaults.get(key)
            if value and key not in self.auxiliary_texts:
                self.auxiliary_texts[key] = value

    def to_dict(self) -> dict[str, Any]:
        return {
            "jobId": self.job_id,
            "title": self.title,
            "experimentCode": self.experiment_code,
            "pk": dict(self.pk),
            "generatedAt": self.generated_at,
            "llm": {"used": self.llm_used, "error": self.llm_error},
            "figures": [figure.to_dict() for figure in self.figures],
            "auxiliary": dict(self.auxiliary_texts),
            "sections": [section.to_dict() for section in self.sections],
        }

    def to_markdown(self) -> str:
        lines: list[str] = [f"# {self.title}", ""]
        meta = (
            f"- 의뢰번호: {self.pk.get('requestNumber', '')}\n"
            f"- 실험코드: {self.experiment_code}  장비: {self.pk.get('equipmentCode', '')}"
            f"  실험자: {self.pk.get('operatorId', '')}\n"
            f"- 생성시각: {self.generated_at}"
        )
        lines.append(meta)
        lines.append("")
        for section in self.sections:
            lines.append(f"## {section.heading}")
            for paragraph in section.paragraphs:
                lines.append(paragraph)
                lines.append("")
            for bullet in section.bullets:
                lines.append(f"- {bullet}")
            if section.bullets:
                lines.append("")
            if section.table is not None:
                columns = [_markdown_table_cell(column) for column in section.table.columns]
                lines.append("| " + " | ".join(columns) + " |")
                lines.append(
                    "| " + " | ".join("---" for _ in section.table.columns) + " |"
                )
                for row in section.table.rows:
                    cells = [_markdown_table_cell(cell) for cell in row]
                    lines.append("| " + " | ".join(cells) + " |")
                lines.append("")
        return "\n".join(lines).rstrip() + "\n"
