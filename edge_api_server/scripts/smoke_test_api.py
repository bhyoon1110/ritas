#!/usr/bin/env python3
"""RIST Edge API 전체 엔드포인트 스모크/통합 테스트.

실행 중인 Edge API 서버를 대상으로 모든 엔드포인트와 파일 송수신(업로드 +
서버측 무결성 검증)을 점검한다. 정상 플로우뿐 아니라 에러/무결성 케이스
(해시·크기 불일치, 잘못된 sha256, 중복 작업, 멱등성 위반, 헤더 누락,
없는 작업 조회 등)도 검증한다.

사용 예:
    python scripts/smoke_test_api.py
    python scripts/smoke_test_api.py --base-url http://bhyoon.me:8000
    python scripts/smoke_test_api.py --files 5            # 5개 파일 업로드
    python scripts/smoke_test_api.py --skip-ftir          # FT-IR 웹 분석 생략
    python scripts/smoke_test_api.py --skip-report        # 보고서/LLM 생략
    python scripts/smoke_test_api.py --skip-negative      # 에러 케이스 생략

의존성: httpx (edge_api_server requirements 에 포함).
종료 코드: 모든 검사 통과 시 0, 하나라도 실패하면 1.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import uuid4

import httpx


# --- 출력 헬퍼 -----------------------------------------------------------

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
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

    def section(self, title: str) -> None:
        print(f"{BOLD}{title}{RESET}")

    def summary(self) -> int:
        total = self.passed + self.failed
        color = GREEN if self.failed == 0 else RED
        print()
        print(
            f"{color}결과: {self.passed}/{total} 통과"
            f"{', ' + str(self.failed) + ' 실패' if self.failed else ''}{RESET}"
        )
        return 0 if self.failed == 0 else 1


# --- 데이터 모델 ---------------------------------------------------------


@dataclass
class SampleFile:
    relative_path: str
    content: bytes

    @property
    def size(self) -> int:
        return len(self.content)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.content).hexdigest()


def build_sample_files(count: int) -> list[SampleFile]:
    stamp = datetime.now(timezone.utc).isoformat()
    files: list[SampleFile] = []
    for i in range(count):
        body = (
            f"#Intensity_unit=cps\n# smoke-test {stamp} file {i}\n"
            + "".join(
                f"{5.0 + j:.3f} {1115.0 * (i + 1) + j:.3f}\n" for j in range(5)
            )
        )
        files.append(
            SampleFile(f"raw/smoke_sample_{i:02d}.txt", body.encode("utf-8"))
        )
    return files


def build_dpt_sample(center: float) -> bytes:
    rows = []
    for index in range(241):
        wn = 400.0 + index * 15.0
        peak = math.exp(-((wn - center) ** 2) / (2 * 55.0**2))
        shoulder = 0.55 * math.exp(-((wn - 1250.0) ** 2) / (2 * 80.0**2))
        rows.append(f"{wn:.3f},{0.05 + peak + shoulder:.8f}")
    return ("\n".join(rows) + "\n").encode()


# --- 요청 헬퍼 -----------------------------------------------------------


def headers(idempotency_key: str | None = None) -> dict[str, str]:
    result = {"X-Request-Id": str(uuid4())}
    if idempotency_key:
        result["Idempotency-Key"] = idempotency_key
    return result


def ikey(label: str) -> str:
    """라벨로부터 결정적이면서 128자 이내(서버 컬럼 한계)인 멱등키를 만든다.

    동일 라벨은 동일 키를 만들어 재요청 멱등성 검증에 사용할 수 있다.
    """
    return "smk-" + hashlib.sha1(label.encode("utf-8")).hexdigest()


def _short_body(response: httpx.Response) -> str:
    body = response.text
    return body if len(body) <= 300 else body[:300] + "…"


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
    reporter.fail(
        name,
        f"HTTP {response.status_code} (기대 {allowed}) {_short_body(response)}",
    )
    return False


def expect_error(
    reporter: Reporter,
    name: str,
    response: httpx.Response,
    status: int,
    code: str | None = None,
) -> bool:
    """에러 응답의 HTTP 상태(및 선택적으로 code 필드)를 검증한다."""
    if response.status_code != status:
        reporter.fail(
            name,
            f"HTTP {response.status_code} (기대 {status}) {_short_body(response)}",
        )
        return False
    if code is not None:
        actual = ""
        try:
            actual = response.json().get("code", "")
        except Exception:
            pass
        if actual != code:
            reporter.fail(name, f"code={actual} (기대 {code})")
            return False
        reporter.ok(name, f"HTTP {status} code={code}")
        return True
    reporter.ok(name, f"HTTP {status}")
    return True


def job_payload(request_number: str, args: argparse.Namespace) -> dict:
    return {
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
    }


# --- 정상 플로우 검사 ----------------------------------------------------


def check_health(client: httpx.Client, reporter: Reporter) -> None:
    reporter.section("[1] 시스템 헬스체크")
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
    reporter.section("[2] LLM 헬스체크")
    if skip:
        reporter.skip("GET /health/llm", "--skip-llm")
        return
    try:
        resp = client.get("/health/llm")
    except httpx.HTTPError as exc:
        reporter.fail("GET /health/llm", str(exc))
        return
    if resp.status_code == 200:
        data = resp.json()
        reporter.ok(
            "GET /health/llm",
            f"model={data.get('model')} maxLen={data.get('maxModelLength')}",
        )
    elif resp.status_code == 503:
        reporter.fail(
            "GET /health/llm", "LLM 미응답(503). vLLM 컨테이너 상태 확인 필요"
        )
    else:
        expect_status(reporter, "GET /health/llm", resp, 200)


def check_ftir_preview(
    client: httpx.Client,
    reporter: Reporter,
    skip: bool,
) -> None:
    reporter.section("[FT-IR] 웹 분석")
    if skip:
        reporter.skip("GET /ftir + POST /api/v1/ftir/analyze", "--skip-ftir")
        return
    page = client.get("/ftir")
    if not expect_status(reporter, "GET /ftir", page, 200):
        return
    if 'id="ftir-file-input"' in page.text:
        reporter.ok("  └ DPT 업로드 화면")
    else:
        reporter.fail("  └ DPT 업로드 화면", "파일 입력 요소가 없습니다.")

    response = client.post(
        "/api/v1/ftir/analyze",
        files=[
            ("files", ("smoke-a.dpt", build_dpt_sample(1700.0), "application/octet-stream")),
            ("files", ("smoke-b.dpt", build_dpt_sample(1550.0), "application/octet-stream")),
        ],
        data={"sensitivity": "25"},
    )
    if not expect_status(reporter, "POST /api/v1/ftir/analyze", response, 200):
        return
    payload = response.json()
    samples = payload.get("samples", [])
    figure = payload.get("figure", {})
    if len(samples) == 2 and figure.get("data"):
        reporter.ok(
            "  └ 다중 DPT 분석",
            f"samples=2 traces={len(figure['data'])}",
        )
    else:
        reporter.fail("  └ 다중 DPT 분석", "시료 또는 그래프 데이터가 없습니다.")


def check_create_job(
    client: httpx.Client,
    reporter: Reporter,
    args: argparse.Namespace,
    files: list[SampleFile],
) -> str | None:
    reporter.section("[3] 작업 생성")
    request_number = args.request_number or f"SMOKE-{int(time.time())}"
    payload = job_payload(request_number, args)
    key = ikey(f"create:{request_number}")
    try:
        resp = client.post("/api/v1/jobs", json=payload, headers=headers(key))
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs", str(exc))
        return None
    if not expect_status(reporter, "POST /api/v1/jobs", resp, 201):
        return None
    job_id = resp.json().get("jobId")
    reporter.ok("  └ jobId 발급", str(job_id))

    retry = client.post("/api/v1/jobs", json=payload, headers=headers(key))
    if retry.status_code == 201 and retry.json().get("jobId") == job_id:
        reporter.ok("  └ 멱등성(동일 키 재요청)", "동일 jobId 반환")
    else:
        reporter.fail("  └ 멱등성(동일 키 재요청)", f"HTTP {retry.status_code}")
    return job_id


def check_upload_files(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
    files: list[SampleFile],
) -> bool:
    reporter.section(f"[4] 파일 업로드 ({len(files)}개)")
    all_ok = True
    for sample in files:
        name = sample.relative_path.split("/")[-1]
        try:
            resp = client.post(
                f"/api/v1/jobs/{job_id}/files",
                files={"file": (name, sample.content, "text/plain")},
                data={
                    "relativePath": sample.relative_path,
                    "sizeBytes": str(sample.size),
                    "sha256": sample.sha256,
                },
                headers=headers(
                    ikey(f"{job_id}:{sample.relative_path}:{sample.sha256}")
                ),
            )
        except httpx.HTTPError as exc:
            reporter.fail(f"  업로드 {sample.relative_path}", str(exc))
            all_ok = False
            continue
        if not expect_status(
            reporter, f"  업로드 {sample.relative_path}", resp, 201
        ):
            all_ok = False

    # 동일 파일 재업로드 시 멱등(같은 fileId)으로 처리되는지 검증
    if files:
        first = files[0]
        name = first.relative_path.split("/")[-1]
        again = client.post(
            f"/api/v1/jobs/{job_id}/files",
            files={"file": (name, first.content, "text/plain")},
            data={
                "relativePath": first.relative_path,
                "sizeBytes": str(first.size),
                "sha256": first.sha256,
            },
            headers=headers(
                ikey(f"{job_id}:{first.relative_path}:{first.sha256}")
            ),
        )
        if again.status_code == 201:
            reporter.ok("  └ 동일 파일 재업로드(멱등)", "HTTP 201")
        else:
            reporter.fail(
                "  └ 동일 파일 재업로드(멱등)", f"HTTP {again.status_code}"
            )
            all_ok = False
    return all_ok


def check_file_crud(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
    files: list[SampleFile],
) -> bool:
    reporter.section("[5] 파일 목록·교체·삭제")
    if not files:
        reporter.skip("파일 CRUD", "업로드 파일 없음")
        return True
    listed = client.get(f"/api/v1/jobs/{job_id}/files", headers=headers())
    if not expect_status(reporter, "GET /api/v1/jobs/{id}/files", listed, 200):
        return False
    if len(listed.json().get("files", [])) != len(files):
        reporter.fail("  └ 파일 목록", "업로드 개수와 다름")
        return False
    reporter.ok("  └ 파일 목록", f"{len(files)}개")

    first = files[0]
    replacement = SampleFile(first.relative_path, first.content + b"# replaced\n")
    replaced = client.put(
        f"/api/v1/jobs/{job_id}/files/{first.relative_path}",
        files={"file": (first.relative_path.split("/")[-1], replacement.content, "text/plain")},
        data={"sizeBytes": str(replacement.size), "sha256": replacement.sha256},
        headers=headers(ikey(f"{job_id}:replace:{replacement.sha256}")),
    )
    if not expect_status(reporter, "PUT /api/v1/jobs/{id}/files/{path}", replaced, 201):
        return False
    files[0] = replacement

    temporary = SampleFile("raw/smoke_delete_me.txt", b"delete-me\n")
    created = client.post(
        f"/api/v1/jobs/{job_id}/files",
        files={"file": ("smoke_delete_me.txt", temporary.content, "text/plain")},
        data={
            "relativePath": temporary.relative_path,
            "sizeBytes": str(temporary.size),
            "sha256": temporary.sha256,
        },
        headers=headers(ikey(f"{job_id}:create-delete")),
    )
    if not expect_status(reporter, "  └ 삭제용 파일 업로드", created, 201):
        return False
    deleted = client.delete(
        f"/api/v1/jobs/{job_id}/files/{temporary.relative_path}",
        headers=headers(ikey(f"{job_id}:delete:{temporary.relative_path}")),
    )
    return expect_status(reporter, "DELETE /api/v1/jobs/{id}/files/{path}", deleted, 200)


def check_request_list(
    client: httpx.Client, reporter: Reporter, request_number: str
) -> None:
    reporter.section("[6] 의뢰 번호 목록 조회")
    response = client.get("/api/v1/requests?page=1&pageSize=50", headers=headers())
    if not expect_status(reporter, "GET /api/v1/requests", response, 200):
        return
    found = any(
        item.get("requestNumber") == request_number
        for item in response.json().get("items", [])
    )
    if found:
        reporter.ok("  └ 생성한 의뢰 포함", request_number)
    else:
        reporter.fail("  └ 생성한 의뢰 포함", request_number)


def check_complete_upload(
    client: httpx.Client,
    reporter: Reporter,
    job_id: str,
    files: list[SampleFile],
) -> bool:
    reporter.section("[7] 업로드 완료(검증)")
    try:
        resp = client.post(
            f"/api/v1/jobs/{job_id}/uploads/complete",
            json={
                "fileCount": len(files),
                "totalSizeBytes": sum(f.size for f in files),
                "files": [
                    {
                        "relativePath": f.relative_path,
                        "sizeBytes": f.size,
                        "sha256": f.sha256,
                    }
                    for f in files
                ],
            },
            headers=headers(ikey(f"{job_id}:complete")),
        )
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs/{id}/uploads/complete", str(exc))
        return False
    if not expect_status(
        reporter, "POST /api/v1/jobs/{id}/uploads/complete", resp, 200
    ):
        return False
    data = resp.json()
    if data.get("status") == "FILES_VERIFIED" and data.get(
        "verifiedFileCount"
    ) == len(files):
        reporter.ok(
            "  └ 검증 결과",
            f"FILES_VERIFIED ({data.get('verifiedFileCount')}개)",
        )
        return True
    reporter.fail(
        "  └ 검증 결과",
        f"status={data.get('status')} count={data.get('verifiedFileCount')}",
    )
    return False


def check_request_report(
    client: httpx.Client, reporter: Reporter, job_id: str
) -> bool:
    reporter.section("[8] 보고서 생성 요청")
    try:
        resp = client.post(
            f"/api/v1/jobs/{job_id}/report",
            json={"options": {"reportFormat": "PPTX", "includeRawFiles": False}},
            headers=headers(ikey(f"{job_id}:report")),
        )
    except httpx.HTTPError as exc:
        reporter.fail("POST /api/v1/jobs/{id}/report", str(exc))
        return False
    if not expect_status(reporter, "POST /api/v1/jobs/{id}/report", resp, 202):
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
    reporter.section("[9] 작업 상태 조회" + (" + 폴링" if wait_terminal else ""))
    terminal = {"REPORT_READY", "COMPLETED", "FAILED"}
    deadline = time.monotonic() + poll_timeout
    last_status = None
    first = True
    data: dict = {}
    status = None
    while True:
        try:
            resp = client.get(f"/api/v1/jobs/{job_id}", headers=headers())
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
        if status != last_status:
            print(
                f"      {DIM}status={status} "
                f"progress={data.get('progress')}{RESET}"
            )
            last_status = status
        if not wait_terminal or status in terminal:
            break
        if time.monotonic() >= deadline:
            reporter.skip(
                "  └ 종결 대기", f"타임아웃({poll_timeout:.0f}s) status={status}"
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


# --- 에러/무결성 검사 ----------------------------------------------------


def check_negative(
    client: httpx.Client,
    reporter: Reporter,
    args: argparse.Namespace,
) -> None:
    reporter.section("[N] 에러/무결성 검사")

    # (1) 없는 작업 조회 → 404
    resp = client.get(f"/api/v1/jobs/{uuid4()}", headers=headers())
    expect_error(reporter, "없는 작업 조회", resp, 404, "JOB_NOT_FOUND")

    req_no = f"NEG-{int(time.time())}-{uuid4().hex[:6]}"

    # (2) Idempotency-Key 누락 → 400
    payload = job_payload(req_no, args)
    resp = client.post(
        "/api/v1/jobs", json=payload, headers={"X-Request-Id": str(uuid4())}
    )
    expect_error(
        reporter, "Idempotency-Key 누락", resp, 400, "MISSING_IDEMPOTENCY_KEY"
    )

    # (3) X-Request-Id 누락 → 400
    resp = client.post(
        "/api/v1/jobs", json=payload, headers={"Idempotency-Key": "neg-no-reqid"}
    )
    expect_error(reporter, "X-Request-Id 누락", resp, 400, "MISSING_REQUEST_ID")

    # (4) 잘못된 payload(필수 필드 누락) → 400 REQUEST_VALIDATION_FAILED
    resp = client.post(
        "/api/v1/jobs", json={"pk": {}}, headers=headers("neg-bad-payload")
    )
    expect_error(reporter, "잘못된 payload", resp, 400, "REQUEST_VALIDATION_FAILED")

    # 에러 업로드 검증용 작업 생성(파일 1개 선언)
    neg_content = b"negative-test-payload-0123456789"
    neg_payload = job_payload(req_no, args)
    create_key = f"neg:create:{req_no}"
    created = client.post(
        "/api/v1/jobs", json=neg_payload, headers=headers(create_key)
    )
    if created.status_code != 201:
        reporter.fail(
            "에러 검증용 작업 생성",
            f"HTTP {created.status_code} {_short_body(created)}",
        )
        return
    neg_job = created.json()["jobId"]
    reporter.ok("에러 검증용 작업 생성", neg_job)

    # (5) 동일 PK 중복 작업 생성 → 409
    dup = client.post(
        "/api/v1/jobs", json=neg_payload, headers=headers(f"neg:dup:{req_no}")
    )
    expect_error(
        reporter, "동일 PK 중복 작업", dup, 409, "ACTIVE_JOB_ALREADY_EXISTS"
    )

    # (6) 멱등성 키 재사용(다른 본문) → 409
    other = job_payload(req_no, args)
    other["sourcePc"]["clientVersion"] = "smoke-1.0-different"
    reuse = client.post("/api/v1/jobs", json=other, headers=headers(create_key))
    expect_error(
        reporter, "멱등성 키 재사용(다른 본문)", reuse, 409, "IDEMPOTENCY_KEY_REUSED"
    )

    digest = hashlib.sha256(neg_content).hexdigest()

    # (7) 잘못된 sha256 형식 → 400
    resp = client.post(
        f"/api/v1/jobs/{neg_job}/files",
        files={"file": ("neg.txt", neg_content, "text/plain")},
        data={
            "relativePath": "raw/neg.txt",
            "sizeBytes": str(len(neg_content)),
            "sha256": "not-a-hash",
        },
        headers=headers(f"neg:{neg_job}:badsha"),
    )
    expect_error(reporter, "잘못된 sha256 형식", resp, 400, "INVALID_SHA256")

    # (8) 해시 불일치 → 422
    wrong_digest = hashlib.sha256(neg_content + b"x").hexdigest()
    resp = client.post(
        f"/api/v1/jobs/{neg_job}/files",
        files={"file": ("neg.txt", neg_content, "text/plain")},
        data={
            "relativePath": "raw/neg.txt",
            "sizeBytes": str(len(neg_content)),
            "sha256": wrong_digest,
        },
        headers=headers(f"neg:{neg_job}:hashmismatch"),
    )
    expect_error(reporter, "파일 해시 불일치", resp, 422, "FILE_HASH_MISMATCH")

    # (9) 크기 불일치 → 422
    resp = client.post(
        f"/api/v1/jobs/{neg_job}/files",
        files={"file": ("neg.txt", neg_content, "text/plain")},
        data={
            "relativePath": "raw/neg.txt",
            "sizeBytes": str(len(neg_content) + 99),
            "sha256": digest,
        },
        headers=headers(f"neg:{neg_job}:sizemismatch"),
    )
    expect_error(reporter, "파일 크기 불일치", resp, 422, "FILE_SIZE_MISMATCH")

    # (10) FILES_VERIFIED 이전 보고서 요청 → 409
    resp = client.post(
        f"/api/v1/jobs/{neg_job}/report",
        json={"options": {"reportFormat": "PPTX"}},
        headers=headers(f"neg:{neg_job}:earlyreport"),
    )
    expect_error(reporter, "검증 전 보고서 요청", resp, 409, "JOB_STATE_CONFLICT")

    # (11) 완료 요청 파일 개수 불일치 → 422
    resp = client.post(
        f"/api/v1/jobs/{neg_job}/uploads/complete",
        json={
            "fileCount": 2,
            "totalSizeBytes": 20,
            "files": [
                {"relativePath": "raw/neg.txt", "sizeBytes": 20, "sha256": digest},
            ],
        },
        headers=headers(f"neg:{neg_job}:badcount"),
    )
    expect_error(
        reporter, "완료 파일 개수 불일치", resp, 422, "BUNDLE_FILE_COUNT_MISMATCH"
    )

    # (12) 128자 초과 Idempotency-Key → 400 (DB 오류 대신 경계 검증)
    too_long = "k" * 129
    resp = client.post(
        "/api/v1/jobs",
        json=job_payload(f"NEG-LONG-{uuid4().hex[:6]}", args),
        headers={"X-Request-Id": str(uuid4()), "Idempotency-Key": too_long},
    )
    expect_error(
        reporter, "과길이 Idempotency-Key", resp, 400, "INVALID_IDEMPOTENCY_KEY"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RIST Edge API 전체 엔드포인트 스모크/통합 테스트",
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000", help="Edge API 베이스 URL"
    )
    parser.add_argument(
        "--experiment-code", default="XRD", help="실험 코드 (기본 XRD)"
    )
    parser.add_argument("--equipment-code", default="SMOKE-EQ-01", help="장비 코드")
    parser.add_argument("--operator-id", default="smoke-tester", help="작업자 ID")
    parser.add_argument(
        "--request-number", default=None, help="요청 번호(미지정 시 SMOKE-<ts>)"
    )
    parser.add_argument(
        "--files", type=int, default=3, help="업로드할 파일 개수 (기본 3)"
    )
    parser.add_argument("--skip-llm", action="store_true", help="LLM 헬스체크 생략")
    parser.add_argument(
        "--skip-ftir", action="store_true", help="FT-IR 웹 분석 검사 생략"
    )
    parser.add_argument(
        "--skip-report", action="store_true", help="보고서 요청/폴링 생략"
    )
    parser.add_argument(
        "--skip-negative", action="store_true", help="에러/무결성 검사 생략"
    )
    parser.add_argument(
        "--poll-timeout", type=float, default=90.0, help="보고서 종결 대기(초)"
    )
    parser.add_argument(
        "--poll-interval", type=float, default=3.0, help="상태 폴링 간격(초)"
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="HTTP 요청 타임아웃(초)"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.request_number is None:
        args.request_number = f"SMOKE-{int(time.time())}"
    reporter = Reporter()
    files = build_sample_files(max(1, args.files))

    print(f"대상 서버: {args.base_url}")
    print(
        f"실험 코드: {args.experiment_code}  장비: {args.equipment_code}  "
        f"파일: {len(files)}개"
    )
    print("=" * 60)

    with httpx.Client(
        base_url=args.base_url.rstrip("/"), timeout=args.timeout
    ) as client:
        check_health(client, reporter)
        check_ftir_preview(client, reporter, args.skip_ftir)
        check_llm_health(client, reporter, args.skip_llm)

        job_id = check_create_job(client, reporter, args, files)
        if not job_id:
            print(f"\n{RED}작업 생성 실패로 정상 플로우를 중단합니다.{RESET}")
        else:
            uploaded = check_upload_files(client, reporter, job_id, files)
            crud_ok = uploaded and check_file_crud(client, reporter, job_id, files)
            check_request_list(
                client,
                reporter,
                args.request_number,
            )
            completed = crud_ok and check_complete_upload(
                client, reporter, job_id, files
            )

            if args.skip_report:
                reporter.section("[8] 보고서 생성 요청")
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
                reporter.section("[8] 보고서 생성 요청")
                reporter.skip(
                    "POST /api/v1/jobs/{id}/report", "이전 단계 실패로 생략"
                )

        if not args.skip_negative:
            check_negative(client, reporter, args)

    return reporter.summary()


if __name__ == "__main__":
    sys.exit(main())
