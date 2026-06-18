#!/usr/bin/env python3
"""RIST Edge API 전체 엔드포인트 스모크 테스트.

실행 중인 Edge API 서버를 대상으로 모든 엔드포인트를 순서대로 호출하여
정상 동작 여부를 확인한다(헬스체크 → 작업 생성 → 파일 업로드 → 업로드 완료
→ 보고서 요청 → 상태 폴링).

사용 예:
    python scripts/smoke_test_api.py
    python scripts/smoke_test_api.py --base-url http://127.0.0.1:8000
    python scripts/smoke_test_api.py --skip-report          # 보고서/LLM 생략
    python scripts/smoke_test_api.py --poll-timeout 120

의존성: httpx (edge_api_server requirements 에 포함).
종료 코드: 모든 검사 통과 시 0, 하나라도 실패하면 1.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import httpx


# --- 출력 헬퍼 -----------------------------------------------------------

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
RESET = "\033[0m"


class Reporter:
    """검사 결과를 누적하고 요약을 출력한다."""

    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def ok(self, name: str, detail: str = "") -> None:
        self.passed += 1
        suffix = f" {DIM}{detail}{RESET}" if detail else ""
        print(f"  {GREEN}PASS{RESET} {name}{suffix}")

    def fail(self, name: str, detail: str = "") -> None:
        self.failed += 1
        suffix = f" {DIM}{detail}{RESET}" if detail else ""
        print(f"  {RED}FAIL{RESET} {name}{suffix}")

    def skip(self, name: str, detail: str = "") -> None:
        suffix = f" {DIM}{detail}{RESET}" if detail else ""
        print(f"  {YELLOW}SKIP{RESET} {name}{suffix}")

    def summary(self) -> int:
        total = self.passed + self.failed
        color = GREEN if self.failed == 0 else RED
        print()
        print(
            f"{color}결과: {self.passed}/{total} 통과"
            f"{', ' + str(self.failed) + ' 실패' if self.failed else ''}{RESET}"
        )
        return 0 if self.failed == 0 else 1


# --- 요청 헬퍼 -----------------------------------------------------------


def headers(idempotency_key: str | None = None) -> dict[str, str]:
    result = {"X-Request-Id": str(uuid4())}
    if idempotency_key:
        result["Idempotency-Key"] = idempotency_key
    return result


def expect_status(
    reporter: Reporter,
    name: str,
    response: httpx.Response,
    expected: int | tuple[int, ...],
) -> bool:
    allowed = (expected,) if isinstance(expected, int) else expected
    if response.status_code in allowed:
        reporter.ok(name, f"HTTP {response.status_code}")
        return True
    body = response.text
    if len(body) > 300:
        body = body[:300] + "…"
    reporter.fail(
        name,
        f"HTTP {response.status_code} (기대 {allowed}) {body}",
    )
    return False


# --- 검사 단계 -----------------------------------------------------------


def check_health(client: httpx.Client, reporter: Reporter) -> None:
    print("[1] 시스템 헬스체크")
    try:
        resp = client.get("/health")
    except httpx.HTTPError as exc:
        reporter.fail("GET /health", str(exc))
        raise SystemExit(reporter.summary())
    if expect_status(reporter, "GET /health", resp, 200):
        data = resp.json()
        reporter.ok(
            "  └ 응답 필드",
            f"env={data.get('environment')} model={data.get('llmModel')}",
        )


def check_llm_health(
    client: httpx.Client, reporter: Reporter, skip: bool
) -> None:
    print("[2] LLM 헬스체크")
    if skip:
        reporter.skip("GET /health/llm", "--skip-llm")
        return
    try:
        resp = client.get("/health/llm")
    except httpx.HTTPError as exc:
        reporter.fail("GET /health/llm", str(exc))
        return
    # LLM 미기동 시 503 일 수 있으므로 200/503 모두 표시한다.
    if resp.status_code == 200:
        data = resp.json()
        reporter.ok(
            "GET /health/llm",
            f"model={data.get('model')} maxLen={data.get('maxModelLength')}",
        )
    elif resp.status_code == 503:
        reporter.fail(
            "GET /health/llm",
            "LLM 미응답(503). vLLM 컨테이너 상태를 확인하세요.",
        )
    else:
        expect_status(reporter, "GET /health/llm", resp, 200)


def check_create_job(
    client: httpx.Client,
    reporter: Reporter,
    args: argparse.Namespace,
    content_size: int,
) -> tuple[str | None, str]:
    print("[3] 작업 생성")
    # 활성 작업 PK 충돌을 피하기 위해 고유 requestNumber 를 생성한다.
    request_number = args.request_number or f"SMOKE-{int(time.time())}"
    payload = {
        "pk": {
            "requestNumber": request_number,
            "experimentCode": args.experiment_code,
            "equipmentCode": args.equipment_code,
            "operatorId": args.operator_id,
        },
        "sourcePc": {
            "hostName": "smoke-test-client",
            "declaredIpAddress": "127.0.0.1",
            "clientVersion": "smoke-1.0",
        },
        "bundle": {"fileCount": 1, "totalSizeBytes": content_size},
    }
    key = f"smoke:create:{request_number}"
    try:
        resp = client.post("/api/v1/jobs", json=payload, headers=headers(key))
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs", str(exc))
        return None, request_number
    if not expect_status(reporter, "POST /api/v1/jobs", resp, 201):
        return None, request_number
    job_id = resp.json().get("jobId")
    reporter.ok("  └ jobId 발급", str(job_id))

    # 멱등성: 동일 키 재요청 시 같은 jobId 가 반환되어야 한다.
    retry = client.post("/api/v1/jobs", json=payload, headers=headers(key))
    if retry.status_code == 201 and retry.json().get("jobId") == job_id:
        reporter.ok("  └ 멱등성(동일 키 재요청)", "동일 jobId 반환")
    else:
        reporter.fail(
            "  └ 멱등성(동일 키 재요청)",
            f"HTTP {retry.status_code}",
        )
    return job_id, request_number


def check_upload_file(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
    content: bytes,
    digest: str,
) -> bool:
    print("[4] 파일 업로드")
    rel_path = "raw/smoke_sample.txt"
    try:
        resp = client.post(
            f"/api/v1/jobs/{job_id}/files",
            files={"file": ("smoke_sample.txt", content, "text/plain")},
            data={
                "relativePath": rel_path,
                "sizeBytes": str(len(content)),
                "sha256": digest,
            },
            headers=headers(f"smoke:{job_id}:{rel_path}:{digest}"),
        )
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs/{id}/files", str(exc))
        return False
    return expect_status(
        reporter, "POST /api/v1/jobs/{id}/files", resp, 201
    )


def check_complete_upload(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
    content: bytes,
    digest: str,
) -> bool:
    print("[5] 업로드 완료(검증)")
    try:
        resp = client.post(
            f"/api/v1/jobs/{job_id}/uploads/complete",
            json={
                "fileCount": 1,
                "totalSizeBytes": len(content),
                "files": [
                    {
                        "relativePath": "raw/smoke_sample.txt",
                        "sizeBytes": len(content),
                        "sha256": digest,
                    }
                ],
            },
            headers=headers(f"smoke:{job_id}:complete"),
        )
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs/{id}/uploads/complete", str(exc))
        return False
    if not expect_status(
        reporter, "POST /api/v1/jobs/{id}/uploads/complete", resp, 200
    ):
        return False
    status = resp.json().get("status")
    if status == "FILES_VERIFIED":
        reporter.ok("  └ 상태", status)
        return True
    reporter.fail("  └ 상태", f"기대 FILES_VERIFIED, 실제 {status}")
    return False


def check_request_report(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
) -> bool:
    print("[6] 보고서 생성 요청")
    try:
        resp = client.post(
            f"/api/v1/jobs/{job_id}/report",
            json={"options": {"reportFormat": "PPTX", "includeRawFiles": False}},
            headers=headers(f"smoke:{job_id}:report"),
        )
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs/{id}/report", str(exc))
        return False
    if not expect_status(
        reporter, "POST /api/v1/jobs/{id}/report", resp, 202
    ):
        return False
    reporter.ok("  └ 상태", resp.json().get("status", ""))
    return True


def check_job_status(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
    poll_timeout: float,
    poll_interval: float,
    wait_terminal: bool,
) -> None:
    print("[7] 작업 상태 조회" + (" + 폴링" if wait_terminal else ""))
    terminal = {"REPORT_READY", "COMPLETED", "FAILED"}
    deadline = time.monotonic() + poll_timeout
    last_status = None
    first = True
    while True:
        try:
            resp = client.get(
                f"/api/v1/jobs/{job_id}", headers=headers()
            )
        except httpx.HTTPError as exc:
            reporter.fail("GET /api/v1/jobs/{id}", str(exc))
            return
        if first:
            if not expect_status(reporter, "GET /api/v1/jobs/{id}", resp, 200):
                return
            first = False
        if resp.status_code != 200:
            reporter.fail("GET /api/v1/jobs/{id}", f"HTTP {resp.status_code}")
            return
        data = resp.json()
        status = data.get("status")
        progress = data.get("progress")
        if status != last_status:
            print(f"      {DIM}status={status} progress={progress}{RESET}")
            last_status = status
        if not wait_terminal or status in terminal:
            break
        if time.monotonic() >= deadline:
            reporter.skip(
                "  └ 종결 대기",
                f"타임아웃({poll_timeout:.0f}s) status={status}",
            )
            return
        time.sleep(poll_interval)

    if status == "FAILED":
        err = data.get("error") or {}
        reporter.fail(
            "  └ 최종 상태",
            f"FAILED code={err.get('code')} msg={err.get('message')}",
        )
    elif status in {"REPORT_READY", "COMPLETED"}:
        reporter.ok("  └ 최종 상태", status)
    else:
        reporter.ok("  └ 현재 상태", str(status))


# --- 엔트리포인트 --------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RIST Edge API 전체 엔드포인트 스모크 테스트",
    )
    parser.add_argument(
        "--base-url",
        default="http://127.0.0.1:8000",
        help="Edge API 베이스 URL (기본 http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--experiment-code", default="XRD", help="실험 코드 (기본 XRD)"
    )
    parser.add_argument(
        "--equipment-code", default="SMOKE-EQ-01", help="장비 코드"
    )
    parser.add_argument(
        "--operator-id", default="smoke-tester", help="작업자 ID"
    )
    parser.add_argument(
        "--request-number",
        default=None,
        help="요청 번호(미지정 시 SMOKE-<timestamp> 자동 생성)",
    )
    parser.add_argument(
        "--skip-llm",
        action="store_true",
        help="LLM 헬스체크 생략",
    )
    parser.add_argument(
        "--skip-report",
        action="store_true",
        help="보고서 요청/상태 폴링 생략(헬스·업로드까지만 검사)",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=90.0,
        help="보고서 종결 대기 최대 시간(초, 기본 90)",
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=3.0,
        help="상태 폴링 간격(초, 기본 3)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="개별 HTTP 요청 타임아웃(초, 기본 30)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    reporter = Reporter()

    stamp = datetime.now(timezone.utc).isoformat()
    content = (
        f"#Intensity_unit=cps\n# smoke-test {stamp}\n"
        "5.000 1115.000\n10.000 2230.000\n"
    ).encode("utf-8")
    digest = hashlib.sha256(content).hexdigest()

    print(f"대상 서버: {args.base_url}")
    print(f"실험 코드: {args.experiment_code}  장비: {args.equipment_code}")
    print("=" * 60)

    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=args.timeout
    ) as client:
        check_health(client, reporter)
        check_llm_health(client, reporter, args.skip_llm)

        job_id, _ = check_create_job(client, reporter, args, len(content))
        if not job_id:
            print(f"\n{RED}작업 생성 실패로 이후 단계를 중단합니다.{RESET}")
            return reporter.summary()

        uploaded = check_upload_file(client, reporter, job_id, content, digest)
        completed = (
            uploaded
            and check_complete_upload(client, reporter, job_id, content, digest)
        )

        if args.skip_report:
            print("[6] 보고서 생성 요청")
            reporter.skip("POST /api/v1/jobs/{id}/report", "--skip-report")
            check_job_status(
                client,
                reporter,
                job_id,
                args.poll_timeout,
                args.poll_interval,
                wait_terminal=False,
            )
        elif completed:
            if check_request_report(client, reporter, job_id):
                check_job_status(
                    client,
                    reporter,
                    job_id,
                    args.poll_timeout,
                    args.poll_interval,
                    wait_terminal=True,
                )
        else:
            print("[6] 보고서 생성 요청")
            reporter.skip(
                "POST /api/v1/jobs/{id}/report", "이전 단계 실패로 생략"
            )

    return reporter.summary()


if __name__ == "__main__":
    sys.exit(main())
