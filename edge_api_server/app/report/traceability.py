"""Cover bindings to authoritative transfer, editable facts and saved source state."""
from ahn.report_traceability import FIELDS, brief, factual
from .model import ReportSection, ReportTable

SECTION = "report_traceability"


def ensure_fields(document, payload):
    if document.section(SECTION) is None:
        context = payload.get("reportContext") or {}
        # Explicitly provided facts only; never use a session user as a reviewer.
        document.sections.insert(1, ReportSection(SECTION, "표지 · 분석 이력 정보",
            table=ReportTable(["항목", "내용"], [[label, factual(context.get(key))] for key,label in FIELDS],
                              column_widths=[.28,.72])))


def values_and_refs(document):
    values = dict(document.pk, completionDate=document.completion_date or "",
                  experimentCode=document.pk.get("experimentCode", ""))
    refs = {key: ["transfer/"+key] for key in ("requestNumber","equipmentCode","operatorId")}
    refs["experimentCode"] = ["transfer/limsExperimentCode"]
    refs["completionDate"] = ["completionDate"]
    section = document.section(SECTION)
    if section and section.table:
        for index,(key,_) in enumerate(FIELDS):
            if index < len(section.table.rows):
                values[key] = section.table.rows[index][1]
                refs[key] = [f"section/{SECTION}/rows/{index}/1"]
    section = document.section("experiment_conditions")
    conditions = []
    if section and section.table:
        for index,row in enumerate(section.table.rows):
            if len(row) >= 2 and factual(row[-1]):
                conditions.append(" / ".join(map(str,row[:-1]))+": "+str(row[-1]))
            refs.setdefault("conditions",[]).append(f"section/experiment_conditions/rows/{index}/{len(row)-1}")
    values["conditions"] = brief(conditions,150)
    trace = document.traceability
    values["sampleNames"] = brief(trace.get("samples",[]))
    values["sourceSummary"] = trace.get("sourceSummary", "")
    values["recordId"] = document.job_id if document.job_id != "web-preview-report" else ""
    parent = trace.get("revisionOf")
    values["revision"] = "수정본 · 이전 작업 "+parent if parent else "최초 생성본"
    return values, refs
