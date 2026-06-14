from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import signal
import time
from pathlib import Path
from typing import Any

from .config import Settings
from .database import Database
from .llm_client import LlmError, LocalLlmClient
from .storage import atomic_write_json
from .time_utils import isoformat_kst


COMMON_SYSTEM_PROMPT = """당신은 재료분석 실험실의 보고서 작성 보조자입니다.
제공된 Python 전처리 결과와 구조화 분석 JSON만 근거로 한국어 문안을 작성하세요.
수치를 재계산하거나 제공되지 않은 물질, 피크, 판정, 원인을 추측하지 마세요.
분석 결과를 단정하지 말고 '가능성', '시사함', '검토 필요' 중심으로 표현하세요.
단일 피크나 단일 이미지 관찰만으로 물질명 또는 작용기를 확정하지 마세요.
QC flag, 불확실성, 데이터 누락 및 분석 한계를 생략하지 마세요.
고객 보고서에 과도한 화학 구조 추정이나 근거 없는 인과관계를 쓰지 마세요.
"""

FTIR_SYSTEM_PROMPT = COMMON_SYSTEM_PROMPT + """
FT-IR 작성 원칙:
1. Python 전처리 결과와 룰 기반 판정 결과를 우선 참고하세요.
2. 라이브러리 매칭 결과와 룰 기반 판정 결과를 명확히 구분하세요.
3. 라이브러리 최고 점수가 임계값 미만이면 '라이브러리 기반 확정 동정은 아님'이라고 표현하세요.
4. 룰 기반 근거가 높더라도 물질명을 확정적으로 표현하지 마세요.
5. 전체 스펙트럼 패턴, 라이브러리 점수 또는 QC 정보가 없으면 그 한계를 반드시 명시하세요.

출력 형식:
1. 고객 보고서용 요약: 정확히 3문장
2. 주요 근거: bullet 4개 이내
3. 해석 한계 및 검토 필요사항: bullet 3개 이내
4. PPT caption: 1문장
"""

GENERIC_SYSTEM_PROMPT = COMMON_SYSTEM_PROMPT + """
출력 형식:
1. 고객 보고서용 요약: 정확히 3문장
2. 주요 근거: bullet 4개 이내
3. 해석 한계 및 검토 필요사항: bullet 3개 이내
4. PPT caption: 1문장
"""


class ReportWorker:
    def __init__(
        self,
        settings: Settings,
        database: Database,
        llm_client: LocalLlmClient,
    ) -> None:
        self.settings = settings
        self.database = database
        self.llm_client = llm_client

    def run_once(self) -> bool:
        queued = self.database.fetch_jobs_by_status("QUEUED", limit=1)
        if not queued:
            return False
        job = queued[0]
        started_at = isoformat_kst()
        if not self.database.claim_queued_job(job["job_id"], started_at):
            return False
        job = self.database.fetch_job(job["job_id"]) or job
        self._write_manifest(job["job_id"])
        self.process_job(job)
        return True

    def process_job(self, job: dict[str, Any]) -> None:
        job_id = job["job_id"]
        job_root = self.settings.storage_root / job["root_relative_path"]
        try:
            analysis = self._load_analysis_results(job_root / "processed")
            system_prompt = self._system_prompt(job["experiment_code"])
            user_prompt = self._build_user_prompt(job, analysis)
            images = self._load_processed_images(job_root / "processed")
            user_content = self._build_user_content(user_prompt, images)
            model_info = (
                self.llm_client.get_model_info()
                if self.settings.llm_validate_model
                else {}
            )
            self._validate_context(
                system_prompt,
                user_prompt,
                image_count=len(images),
                model_info=model_info,
            )
            request_payload = self.llm_client.build_request_payload(
                system_prompt, user_content
            )
            atomic_write_json(
                job_root / "logs" / "llm-request.json",
                self.llm_client.request_for_log(request_payload),
            )
            content, response_payload = self.llm_client.chat_completion(
                request_payload
            )
            atomic_write_json(
                job_root / "logs" / "llm-response.json", response_payload
            )
            report_path = job_root / "report" / "report-draft.md"
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(content + "\n", encoding="utf-8")
            self.database.update_job(
                job_id,
                status="PROCESSING",
                progress=75,
                error_json=None,
            )
        except LlmError as exc:
            self._mark_failed(job_id, exc.code, exc.message, exc.retryable)
        except FileNotFoundError as exc:
            self._mark_failed(
                job_id,
                "ANALYSIS_RESULT_NOT_FOUND",
                str(exc),
                False,
            )
        except Exception as exc:
            self._mark_failed(
                job_id,
                "REPORT_WORKER_ERROR",
                f"보고서 worker 오류: {exc}",
                True,
            )
        finally:
            self._write_manifest(job_id)

    def _load_analysis_results(self, processed_dir: Path) -> list[dict[str, Any]]:
        if not processed_dir.exists():
            raise FileNotFoundError("processed 폴더를 찾을 수 없습니다.")
        candidates = sorted(
            path
            for path in processed_dir.rglob("*.json")
            if path.name not in {"llm-request.json", "llm-response.json"}
        )
        if not candidates:
            raise FileNotFoundError(
                "구조화된 분석 결과 JSON이 없습니다. "
                "장비별 processor가 processed 폴더에 JSON을 생성해야 합니다."
            )
        results = []
        for path in candidates:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise FileNotFoundError(
                    f"분석 결과 JSON을 읽을 수 없습니다: {path.name}"
                ) from exc
            results.append(
                {
                    "relativePath": path.relative_to(processed_dir).as_posix(),
                    "data": payload,
                }
            )
        return results

    @staticmethod
    def _system_prompt(experiment_code: str) -> str:
        normalized = experiment_code.upper().replace("_", "-")
        if normalized in {"FTIR", "FT-IR", "IR"}:
            return FTIR_SYSTEM_PROMPT
        return GENERIC_SYSTEM_PROMPT

    def _build_user_prompt(
        self, job: dict[str, Any], analysis: list[dict[str, Any]]
    ) -> str:
        payload = {
            "jobId": job["job_id"],
            "pk": {
                "requestNumber": job["request_number"],
                "experimentCode": job["experiment_code"],
                "equipmentCode": job["equipment_code"],
                "operatorId": job["operator_id"],
            },
            "analysisResults": analysis,
        }
        serialized = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        if len(serialized) > self.settings.llm_max_input_chars:
            raise LlmError(
                "LLM_INPUT_TOO_LARGE",
                "구조화 분석 결과가 LLM 최대 입력 크기를 초과했습니다.",
                retryable=False,
            )
        return (
            "다음 JSON은 분석 프로그램이 생성한 입력입니다. "
            "이 결과만 근거로 지정된 출력 형식의 고객 보고서 문안을 작성하세요.\n\n"
            f"{serialized}"
        )

    def _load_processed_images(self, processed_dir: Path) -> list[dict[str, str]]:
        if not self.settings.llm_include_images:
            return []
        candidates = sorted(
            path
            for path in processed_dir.rglob("*")
            if path.is_file()
            and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
        )
        images: list[dict[str, str]] = []
        for path in candidates:
            if len(images) >= self.settings.llm_max_images:
                break
            size = path.stat().st_size
            if size > self.settings.llm_max_image_bytes:
                continue
            media_type = mimetypes.guess_type(path.name)[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            images.append(
                {
                    "relativePath": path.relative_to(processed_dir).as_posix(),
                    "dataUrl": f"data:{media_type};base64,{encoded}",
                }
            )
        return images

    @staticmethod
    def _build_user_content(
        user_prompt: str,
        images: list[dict[str, str]],
    ) -> str | list[dict[str, Any]]:
        if not images:
            return user_prompt
        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    user_prompt
                    + "\n\n첨부 이미지는 processed 폴더의 분석 산출물입니다. "
                    "이미지에서 직접 관찰 가능한 내용과 JSON 근거를 구분하고, "
                    "이미지만으로 결론을 확정하지 마세요."
                ),
            }
        ]
        for image in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": image["dataUrl"]},
                }
            )
        return content

    def _validate_context(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        image_count: int,
        model_info: dict[str, Any],
    ) -> None:
        reported_window = model_info.get("max_model_len")
        context_window = self.settings.llm_context_window
        if isinstance(reported_window, int) and reported_window > 0:
            context_window = min(context_window, reported_window)

        # Gemma tokenizer를 로컬에 의존하지 않기 위한 보수적 추정치이다.
        text_bytes = len((system_prompt + user_prompt).encode("utf-8"))
        estimated_input_tokens = math.ceil(text_bytes / 2)
        estimated_image_tokens = image_count * 512
        estimated_total = (
            estimated_input_tokens
            + estimated_image_tokens
            + self.settings.llm_max_tokens
            + self.settings.llm_context_margin
        )
        if estimated_total > context_window:
            raise LlmError(
                "LLM_CONTEXT_BUDGET_EXCEEDED",
                "LLM 컨텍스트 예상 사용량이 한도를 초과합니다. "
                f"estimated={estimated_total}, limit={context_window}, "
                f"max_tokens={self.settings.llm_max_tokens}, images={image_count}. "
                "분석 JSON을 축약하거나 출력 토큰 수를 줄여야 합니다.",
                retryable=False,
            )

    def _mark_failed(
        self, job_id: str, code: str, message: str, retryable: bool
    ) -> None:
        error = {
            "code": code,
            "message": message,
            "retryable": retryable,
        }
        self.database.update_job(
            job_id,
            status="FAILED",
            progress=50,
            error_json=json.dumps(error, ensure_ascii=False),
        )

    def _write_manifest(self, job_id: str) -> None:
        from .service import EdgeService

        EdgeService(self.settings, self.database).write_manifest(job_id)


def build_worker(settings: Settings | None = None) -> ReportWorker:
    resolved = settings or Settings.from_env()
    database = Database(resolved.db_path)
    llm_client = LocalLlmClient(
        resolved.llm_base_url,
        resolved.llm_model,
        resolved.llm_timeout_seconds,
        resolved.llm_temperature,
        resolved.llm_max_tokens,
        resolved.llm_validate_model,
    )
    return ReportWorker(resolved, database, llm_client)


def main() -> None:
    parser = argparse.ArgumentParser(description="RIST report generation worker")
    parser.add_argument(
        "--once",
        action="store_true",
        help="대기 중인 작업을 최대 한 건 처리한 뒤 종료",
    )
    args = parser.parse_args()
    worker = build_worker()
    if args.once:
        try:
            worker.run_once()
        finally:
            worker.llm_client.close()
        return

    running = True

    def stop(*_: object) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while running:
            processed = worker.run_once()
            if not processed:
                time.sleep(worker.settings.worker_poll_seconds)
    finally:
        worker.llm_client.close()


if __name__ == "__main__":
    main()
