"""Analyst-editable report content; never included in recipient HTML programs."""
from copy import deepcopy

from .builders import _FTIR_CONDITION_ORDER, _RAMAN_CONDITION_ORDER, _raman_experiment_condition_rows
from .model import ReportDocument, ReportFigure, ReportSection, ReportTable

TRANSFER_FIELDS = {"requestNumber": "의뢰번호", "limsExperimentCode": "실험코드",
                   "equipmentCode": "실험장비", "operatorId": "실험자"}


def blank_value(value):
    value = str(value or "").strip()
    return "" if value in {"-", "WEB-PREVIEW", "web-preview", "SSO-PENDING", "(미기재)"} else value


def transfer_values(payload, request_number="", equipment_code="", operator_id=""):
    context = payload.get("reportContext") or {}
    return {"requestNumber": blank_value(request_number or context.get("requestNumber")),
            "limsExperimentCode": blank_value(context.get("limsExperimentCode") or context.get("experimentCode")),
            "equipmentCode": blank_value(equipment_code or context.get("equipmentCode")),
            "operatorId": blank_value(operator_id or context.get("operatorId"))}


def apply_transfer(document, transfer, project):
    document.pk.update(requestNumber=transfer["requestNumber"], experimentCode=transfer["limsExperimentCode"],
                       equipmentCode=transfer["equipmentCode"], operatorId=transfer["operatorId"])
    # Rendering/experiment behavior uses the project, not the opaque LIMS test
    # method code. The latter belongs in pk and the authoritative transfer table.
    document.experiment_code = project
    section = document.section("report_transfer")
    table = ReportTable(["항목", "내용"], [[label, transfer[key]] for key, label in TRANSFER_FIELDS.items()],
                        column_widths=[0.28, 0.72])
    if section:
        section.table = table
    else:
        document.sections.insert(0, ReportSection("report_transfer", "보고서 전송 정보", table=table))
    sample = document.section("sample_info")
    if sample:
        # Transfer details have one authoritative section, not a stale duplicate.
        sample.bullets = [line for line in sample.bullets
                          if not line.startswith(("의뢰번호:", "실험코드:", "장비:", "실험자:"))]


def ensure_editable_fields(document, payload, project, transfer):
    apply_transfer(document, transfer, project)
    section = document.section("experiment_conditions")
    rows = deepcopy(section.table.rows) if section and section.table else []
    is_raman = project in {"RAMAN", "RIN", "RIN-RAMAN"}
    if is_raman:
        rows = _raman_experiment_condition_rows(payload)
    # Explicit form values win over raw metadata for the same target/label.
    ordered = {}
    for target, label, value in rows:
        ordered[(target, label)] = value
    explicit = payload.get("reportConditionFields")
    if isinstance(explicit, list) and not is_raman:
        for row in explicit:
            if isinstance(row, dict) and row.get("label"):
                target, label = str(row.get("target") or "공통"), str(row["label"])
                if target == "공통":
                    ordered = {key: value for key, value in ordered.items() if key[1] != label}
                ordered[(target, label)] = str(row.get("value") or "")
    labels = list(_FTIR_CONDITION_ORDER if project == "FT-IR" else _RAMAN_CONDITION_ORDER) + ["실험환경/비고"]
    for label in labels:
        if not any(key[1] == label for key in ordered):
            ordered[("공통", label)] = ""
    rows = [[target, label, value] for (target, label), value in ordered.items()]
    if section is None:
        section = ReportSection("experiment_conditions", "실험조건 및 실험환경")
        document.sections.insert(1, section)
    section.paragraphs = []
    if is_raman:
        section.heading = "공통 실험환경/조건"
    section.table = ReportTable(["대상", "항목", "값"], rows, merge_columns=[0],
                                column_widths=[0.14, 0.28, 0.58])


def document_from_dict(value, *, image_path):
    """Deserialize only server-owned report JSON, with a fixed local figure path."""
    sections = []
    for item in value["sections"]:
        table = item.get("table")
        if table:
            table = ReportTable(table["columns"], table["rows"], table.get("mergeColumns", []),
                                table.get("alignments", []), table.get("verticalAlignment", "top"),
                                table.get("columnWidths", []))
        sections.append(ReportSection(item["sectionId"], item["heading"], item.get("paragraphs", []),
                                      item.get("bullets", []), table, item.get("source", "rule")))
    return ReportDocument(job_id=value["jobId"], title=value["title"], experiment_code=value["experimentCode"],
                          pk=value["pk"], generated_at=value["generatedAt"], sections=sections,
                          figures=[ReportFigure(f["figureId"], f["title"], str(image_path), f.get("captionSlot", "caption"))
                                   for f in value.get("figures", [])],
                          auxiliary_texts=value.get("auxiliary", {}), llm_used=value.get("llm", {}).get("used", False),
                          llm_error=value.get("llm", {}).get("error"))


def editor_document(document):
    return {"title": document.title, "transfer": {key: document.pk.get("experimentCode" if key == "limsExperimentCode" else key, "")
                                                   for key in TRANSFER_FIELDS},
            "sections": [{"sectionId": s.section_id, "heading": s.heading, "paragraphs": s.paragraphs,
                          "bullets": s.bullets, "columns": s.table.columns if s.table else None,
                          "rows": s.table.rows if s.table else None, "source": s.source}
                         for s in document.sections if s.section_id != "report_transfer"],
            "auxiliary": dict(document.auxiliary_texts)}


def apply_edits(document, changes, project):
    """Shape/limits are validated separately. Scientific graph data is untouched."""
    document.title = changes["title"]
    apply_transfer(document, changes["transfer"], project)
    for item in changes["sections"]:
        section = document.section(item["sectionId"])
        before = (section.heading, section.paragraphs, section.bullets, section.table.rows if section.table else None)
        after = (item["heading"], item["paragraphs"], item["bullets"], item["rows"])
        if before != after:
            section.source = "user"
        section.heading, section.paragraphs, section.bullets = after[:3]
        if section.table:
            section.table.rows = item["rows"]
    document.auxiliary_texts = changes["auxiliary"]
