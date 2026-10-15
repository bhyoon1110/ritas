"""Bounded, evidence-based email generation, independent of report prose."""
from copy import deepcopy
from dataclasses import replace
import json

import httpx
import pytest

from app.config import Settings
from app.llm_client import LlmError, LocalLlmClient
from app.report import annotator
from app.report.builders import get_builder
from edge_api_server.tests.test_edited_report_analysis import build_spec, edited_payload


MAIL_BODY = (
    "안녕하십니까.\n\n의뢰번호 REQ-EDIT의 Edited sample 분석 결과를 안내드립니다. "
    "첨부 보고서는 분석자가 편집한 최종 그래프의 피크와 관심 영역을 기준으로 작성되었습니다.\n\n"
    "주요 관찰 사항은 다음과 같습니다.\n"
    "- Edited sample의 1700 cm⁻¹에서 '수정 C=O 후보'로 표시된 피크를 확인했습니다.\n"
    "- 해당 피크는 사용자가 추가하고 이름을 수정한 항목으로, 강조 영역에 포함됩니다.\n"
    "- 그래프 메모에는 산화 가능성 비교가 기록되어 있으나, 메모만으로 산화를 확정할 수는 없습니다.\n\n"
    "위 피크의 명칭은 분석자가 지정한 후보 해석이며 물질의 확정 동정 결과를 의미하지 않습니다. "
    "관심 영역 표시와 Y축 이동은 가독성을 위한 설정이므로, 화면상 높이를 시료의 농도나 함량으로 "
    "해석하지 않도록 유의해 주십시오. 피크 위치와 형상을 중심으로 결과를 검토하고 필요시 표준품 "
    "비교 또는 반복 측정을 통해 후보 해석을 확인하는 것이 좋겠습니다.\n\n"
    "첨부 보고서에서 상세 그래프와 분석 내용을 확인해 주시기 바랍니다. "
    "추가로 확인할 사항이 있으면 말씀해 주십시오. 감사합니다."
)


def run_annotation(tmp_path, spec, *, email=None, fail_analysis=False, settings=None):
    calls = []
    def handler(request):
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "test", "max_model_len": 8192}]})
        call = json.loads(request.content)
        calls.append(call)
        if fail_analysis or (len(calls) == 2 and email == "offline"):
            return httpx.Response(503, json={"error": {"message": "model unavailable"}})
        if len(calls) == 1:
            result = {key: "1700 cm⁻¹의 수정 C=O 후보는 확정 동정이 아닌 검토 대상입니다."
                      for key in spec.requested_slots if key not in {"email_subject", "email_body"}}
        else:
            result = email if email is not None else {
                "email_subject": "[RIST] Edited sample 분석 결과\n및 피크 검토 안내", "email_body": MAIL_BODY}
        content = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    client = LocalLlmClient("http://127.0.0.1:8001", "test", 2, .1, 1200, True,
                            transport=httpx.MockTransport(handler))
    try:
        slots = annotator.annotate(settings or Settings(storage_root=tmp_path, llm_include_images=False), client, spec,
                                   processed_dir=tmp_path, logs_dir=tmp_path / "logs")
        return slots, calls
    finally:
        client.close()


@pytest.mark.parametrize("experiment", ["FT-IR", "RAMAN"])
def test_email_has_own_output_budget_and_final_evidence(tmp_path, experiment):
    _, document, spec = build_spec(edited_payload(), experiment)
    before = deepcopy(spec)
    slots, calls = run_annotation(tmp_path, spec)
    assert len(calls) == 2 and spec == before
    analysis_prompt = calls[0]["messages"][-1]["content"]
    email_prompt = calls[1]["messages"][-1]["content"]
    assert "email_subject" not in analysis_prompt and "250자" in analysis_prompt
    assert "250자" not in email_prompt and "email_body" in email_prompt
    assert "500~900자" in calls[1]["messages"][0]["content"]
    evidence = json.loads(email_prompt.split("\n\n", 1)[1])
    assert evidence["current_peaks"][0]["label"] == "수정 C=O 후보"
    assert evidence["current_peaks"][0]["position_cm-1"] == 1700
    assert evidence["current_peaks"][0]["intensity"] == 3
    assert evidence["report_context"]["request_number"] == "REQ-EDIT"
    assert evidence["report_analysis"]["summary"] == slots["summary"]
    assert "DELETED" not in email_prompt and "STALE" not in email_prompt
    assert calls[1]["max_tokens"] == 1200
    assert len(slots["email_body"]) > 500 and "\n" not in slots["email_subject"]
    document.ensure_auxiliary_texts(spec.fallback)
    document.apply_llm_slots(slots)
    assert document.auxiliary_texts["email_body"] == MAIL_BODY
    assert (tmp_path / "logs/llm-request.json").is_file()
    assert (tmp_path / "logs/email/llm-request.json").is_file()


@pytest.mark.parametrize("email", ["offline", "invalid JSON", {}, {"email_subject": "제목"},
    {"email_subject": "제목", "email_body": "라이브러리에서 물질이 확인됨"},
    {"email_subject": "긴 제목" * 100, "email_body": "본문"},
    {"email_subject": "제목", "email_body": "본문" * 2000}])
def test_mail_failure_retains_analysis_and_default_email(tmp_path, email):
    _, document, spec = build_spec(edited_payload())
    slots, calls = run_annotation(tmp_path, spec, email=email)
    assert len(calls) == 2
    assert "summary" in slots and "email_subject" not in slots and "email_body" not in slots
    document.ensure_auxiliary_texts(spec.fallback)
    document.apply_llm_slots(slots)
    assert document.section("summary").source == "llm"
    assert document.auxiliary_texts["email_body"] == spec.fallback["email_body"]


def test_unavailable_analysis_does_not_start_another_llm_request(tmp_path, monkeypatch):
    _, _, spec = build_spec(edited_payload())
    original = annotator._spectrum_email_spec
    def unexpected(*args):
        pytest.fail("Mail must not run after analysis failure")
    monkeypatch.setattr(annotator, "_spectrum_email_spec", unexpected)
    with pytest.raises(LlmError):
        run_annotation(tmp_path, spec, fail_analysis=True)
    monkeypatch.setattr(annotator, "_spectrum_email_spec", original)


@pytest.mark.parametrize("experiment", ["FT-IR", "RAMAN"])
def test_zero_peaks_and_small_budget_are_honest_and_bounded(tmp_path, experiment):
    payload = edited_payload()
    payload["viewerFigure"]["data"] = payload["viewerFigure"]["data"][:1]
    _, _, spec = build_spec(payload, experiment)
    slots, calls = run_annotation(tmp_path, spec, settings=Settings(storage_root=tmp_path, llm_include_images=False, llm_max_tokens=640))
    assert all(call["max_tokens"] <= 640 for call in calls)
    evidence = json.loads(calls[1]["messages"][-1]["content"].split("\n\n", 1)[1])
    assert evidence["visible_peak_count"] == 0 and evidence["current_peaks"] == []
    assert "피크 기반 해석을 할 수 없음" in calls[1]["messages"][0]["content"]


def test_email_does_not_resend_images_and_keeps_omission_counts(tmp_path, monkeypatch):
    _, _, spec = build_spec(edited_payload())
    facts = deepcopy(spec.facts)
    facts["current_peaks"] = [{**facts["current_peaks"][0], "id": "p" + str(i), "label": "긴 피크 " * 30} for i in range(48)]
    facts.update(visible_peak_count=48, included_peak_count=48, omitted_peak_count=0)
    spec = replace(spec, facts=facts)
    mail = annotator._spectrum_email_spec(spec, {"summary": "후보 해석 " * 1000})
    def unexpected(*args):
        pytest.fail("Email must not reload graph images")
    monkeypatch.setattr(annotator, "_load_images", unexpected)
    class Client:
        def get_model_info(self):
            return {"max_model_len": 8192}
    content, tokens = annotator._fit_edited_evidence(Settings(storage_root=tmp_path), Client(), mail, tmp_path)
    evidence = json.loads(content.split("\n\n", 1)[1])
    assert len(evidence["current_peaks"]) < 48
    assert evidence["included_peak_count"] + evidence["omitted_peak_count"] == 48
    assert len(evidence["report_analysis"]["summary"]) <= 350
    assert 768 <= tokens <= 1200
    assert len(spec.facts["current_peaks"]) == 48


@pytest.mark.parametrize("experiment", ["FT-IR", "RAMAN"])
def test_email_context_uses_only_available_request_id(experiment):
    payload = edited_payload()
    payload["reportContext"] = {"requestNumber": "REQ-FORM", "operatorId": "SECRET-USER", "password": "SECRET"}
    job = {"request_number": "WEB-PREVIEW", "operator_id": "SECRET-USER"}
    builder = get_builder(experiment)
    spec = builder.llm_slots(job, [{"data": payload}])
    assert spec.facts["report_context"] == {"request_number": "REQ-FORM"}
    assert "SECRET" not in json.dumps(spec.facts)
    payload["reportContext"]["requestNumber"] = ""
    assert builder.llm_slots(job, [{"data": payload}]).facts["report_context"] == {"request_number": ""}


@pytest.mark.parametrize("zero_denominator", [False, True])
def test_raman_email_uses_current_ratio_pairs_not_stale_labels(tmp_path, zero_denominator):
    from edge_api_server.tests.test_raman_report_tables import payload_for_tables
    payload = payload_for_tables(1)
    payload["viewerFigure"]["data"][1]["name"] = "편집 D"
    payload["viewerFigure"]["data"][1]["y"] = [13]
    if zero_denominator:
        payload["viewerFigure"]["data"][2]["y"] = [10]
    payload["viewerFigure"]["layout"]["annotations"][0]["text"] = "STALE_RATIO=999"
    _, document, spec = build_spec(payload, "RAMAN")
    _, calls = run_annotation(tmp_path, spec)
    content = calls[1]["messages"][-1]["content"]
    evidence = json.loads(content.split("\n\n", 1)[1])
    ratio = evidence["graph_notes"]["ratio_measurements"][0]
    assert ratio["sample"] == "시료 1" and ratio["numerator"]["label"] == "편집 D"
    assert ratio["value"] == (None if zero_denominator else .75)
    assert ratio["value_text"] == document.section("raman_ratios").table.rows[0][-1]
    assert "STALE_RATIO" not in content
