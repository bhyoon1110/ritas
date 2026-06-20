from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx


class SpringCallbackError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class SpringCallbackClient:
    """로컬 Spring Boot에 최종 보고서 ZIP을 전달한다."""

    def __init__(
        self,
        callback_url: str,
        timeout_seconds: float,
        max_attempts: int,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.callback_url = callback_url
        self.max_attempts = max_attempts
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout_seconds), transport=transport
        )

    @property
    def enabled(self) -> bool:
        return bool(self.callback_url)

    def close(self) -> None:
        self.client.close()

    def deliver(self, job: dict[str, Any], package_path: Path) -> None:
        if not self.enabled:
            return
        if self.max_attempts < 1:
            raise SpringCallbackError(
                "SPRING_CALLBACK_CONFIG_INVALID",
                "Spring Boot 전송 재시도 횟수는 1 이상이어야 합니다.",
                retryable=False,
            )
        package_sha256 = _sha256_file(package_path)
        for attempt in range(1, self.max_attempts + 1):
            try:
                with package_path.open("rb") as package:
                    response = self.client.post(
                        self.callback_url,
                        data={
                            "jobId": job["job_id"],
                            "requestNumber": job["request_number"],
                            "experimentCode": job["experiment_code"],
                            "equipmentCode": job["equipment_code"],
                            "operatorId": job["operator_id"],
                            "packageSha256": package_sha256,
                        },
                        files={
                            "package": (
                                package_path.name,
                                package,
                                "application/zip",
                            )
                        },
                        headers={"Idempotency-Key": f"{job['job_id']}:report-package"},
                    )
                if 200 <= response.status_code < 300:
                    return
                retryable = response.status_code in {408, 429, 500, 502, 503, 504}
                if not retryable or attempt == self.max_attempts:
                    raise SpringCallbackError(
                        "SPRING_CALLBACK_HTTP_ERROR",
                        f"Spring Boot 결과 전달이 HTTP {response.status_code}를 반환했습니다.",
                        retryable=retryable,
                    )
            except httpx.TimeoutException as exc:
                if attempt == self.max_attempts:
                    raise SpringCallbackError(
                        "SPRING_CALLBACK_TIMEOUT",
                        "Spring Boot 결과 전달 시간이 초과되었습니다.",
                        retryable=True,
                    ) from exc
            except httpx.NetworkError as exc:
                if attempt == self.max_attempts:
                    raise SpringCallbackError(
                        "SPRING_CALLBACK_CONNECTION_FAILED",
                        "Spring Boot 결과 전달 연결에 실패했습니다.",
                        retryable=True,
                    ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
