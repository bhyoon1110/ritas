# Edge - Local Spring Boot 결과 전달 API

## 목적

Edge 보고서 worker가 최종 사용자용 보고서 ZIP을 같은 Edge 서버의 Spring Boot
서비스로 전달한다. 분석 결과 JSON과 LLM 요청/응답 JSON은 Edge 내부 데이터이며
이 인터페이스로 전송하지 않는다.

## 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `LOCAL_SPRING_BOOT_BASE_URL` | `http://127.0.0.1:8080` | 환경 프로파일의 Spring Boot API 서버 주소 |
| `RIST_SPRING_CALLBACK_URL` | `{BASE_URL}/api/v1/edge/reports` | 기본 경로를 덮어쓸 전체 수신 URL |
| `RIST_SPRING_CALLBACK_TIMEOUT_SECONDS` | `60` | 요청 제한 시간 |
| `RIST_SPRING_CALLBACK_MAX_ATTEMPTS` | `3` | 재시도 포함 최대 전송 횟수 |

```bash
export LOCAL_SPRING_BOOT_BASE_URL=http://127.0.0.1:8080
```

## 요청

```http
POST /api/v1/edge/reports
Content-Type: multipart/form-data
Idempotency-Key: {jobId}:report-package
```

| multipart 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `jobId` | string | Y | Edge 작업 UUID |
| `requestNumber` | string | Y | 의뢰번호 |
| `experimentCode` | string | Y | 실험 코드 |
| `equipmentCode` | string | Y | 장비 코드 |
| `operatorId` | string | Y | 작업자 ID |
| `packageSha256` | string | Y | ZIP 전체의 소문자 64자리 SHA-256 |
| `package` | binary | Y | `application/zip` 최종 결과 ZIP |

성공은 모든 `2xx` 응답으로 판단한다. `408`, `429`, `500`, `502`, `503`, `504`와
연결/시간 초과 오류는 설정된 횟수까지 같은 `Idempotency-Key`로 재시도한다.
그 외 `4xx`와 재시도 소진 오류는 Edge 작업을 `FAILED`로 기록한다.

## 성공 응답

Spring Boot는 ZIP을 저장하고 `packageSha256` 검증을 마친 뒤 `200 OK` 또는
`201 Created`를 반환한다. 응답 본문은 선택이며 Edge는 본문을 해석하지 않는다.
동일한 `Idempotency-Key` 재수신 시에도 이미 저장한 결과를 재사용하고 성공 `2xx`를
반환해야 한다.

## ZIP 구성

ZIP 루트에는 사용자용 산출물만 포함한다.

```text
report.pdf
report.pptx
report.html
report.md
raw/                         # includeRawFiles=true일 때만 포함
  원본 bundle의 상대 경로
```

요청하지 않은 보고서 형식은 ZIP에 없다. `processed/*.json`, `report.json`,
`logs/llm-request.json`, `logs/llm-response.json`은 포함하지 않는다.

## 상태 전환

```text
PROCESSING -> CALLBACK_PENDING -> COMPLETED
                              -> FAILED
```

`RIST_SPRING_CALLBACK_URL`을 빈 문자열로 명시하면 전달을 비활성화하고 ZIP 생성
후 바로 `COMPLETED`로 처리한다. 운영 환경에서는 프로파일 기본 주소 또는 전체
콜백 URL을 반드시 설정한다.
