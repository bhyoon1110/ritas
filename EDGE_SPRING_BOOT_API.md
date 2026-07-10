# Edge - Local Spring Boot 결과 전달 API

## 목적

Edge 보고서 worker가 최종 사용자용 보고서 ZIP을 같은 Edge 서버의 Spring Boot
서비스로 전달한다. 분석 결과 JSON과 LLM 요청/응답 JSON은 Edge 내부 데이터이며
이 인터페이스로 전송하지 않는다.

XRD/TEM C# 전송 프로그램은 Edge 서버까지만 파일과 보고서 생성 요청을 보낸다.
Spring Boot 결과 전달 API의 호출 주체는 항상 Edge 보고서 worker이며,
XRD/TEM C# 프로그램은 이 API를 직접 호출하지 않는다.

## 설정

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RIST_SPRING_CALLBACK_URL` | `http://127.0.0.1:8080/api/v1/edge/reports` | Spring Boot 전체 수신 URL |
| `RIST_SPRING_CALLBACK_TIMEOUT_SECONDS` | `60` | 요청 제한 시간 |
| `RIST_SPRING_CALLBACK_MAX_ATTEMPTS` | `3` | 재시도 포함 최대 전송 횟수 |

```bash
export RIST_SPRING_CALLBACK_URL=http://127.0.0.1:8080/api/v1/edge/reports
export RIST_SPRING_CALLBACK_TIMEOUT_SECONDS=60
export RIST_SPRING_CALLBACK_MAX_ATTEMPTS=3
```

운영 서버에서는 `/home/rist/ritas/edge.env`에 위 값을 넣고 API/worker 서비스를
재시작한다.

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

`requestNumber`, `experimentCode`, `equipmentCode`, `operatorId`는 Edge 작업 등록
시 확정된 업무 식별값이다. XRD/TEM C# 연동에서는 C# 프로그램이
`GET /api/v1/requests`에서 선택한 의뢰번호/실험코드와 실험 PC별 기본
`equipmentCode`, 사용자 로그인/선택값의 `operatorId`를 `POST /api/v1/jobs`에
전달하고, Edge worker가 동일 값을 이 API로 전달한다.

현재 공통 작업 명세는 `experimentCode` 단일 필드를 사용한다. Spring Boot가
LIMS 시험코드(`testMethodCode`)를 반드시 받아야 하고 Edge 내부 processor는
`XRD`/`TEM` 같은 장비 구분명으로 라우팅해야 한다면, C# 계약 확정 전에
`analysisType` 같은 별도 라우팅 필드 추가 여부를 결정해야 한다.

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
email_body.md                 # LLM/규칙 기반 메일 문안이 생성된 경우
raw/                         # includeRawFiles=true일 때만 포함
  원본 bundle의 상대 경로
```

PDF/PPTX/HTML 중 요청하지 않은 보고서 형식은 ZIP에 없다. `report.md`는 공통
요약 산출물로 항상 포함하고, `email_body.md`는 메일 문안이 생성된 경우에만
포함한다. `processed/*.json`, `report.json`, `logs/llm-request.json`,
`logs/llm-response.json`은 포함하지 않는다.

XRD/TEM C# 연동에서 Spring Boot가 받는 ZIP의 기준 구성은 다음과 같다.

| 프로젝트 | C# 보고서 생성 요청 | ZIP 필수 사용자 산출물 |
|---|---|---|
| XRD | `reportFormats: ["HTML"]` | `report.html`, `report.md` |
| TEM | `reportFormats: ["PPTX"]` | `report.pptx`, `report.md` |

`includeRawFiles=true`인 경우 두 프로젝트 모두 원본 bundle이 `raw/` 아래에
추가된다.

## 상태 전환

```text
PROCESSING -> CALLBACK_PENDING -> COMPLETED
                              -> FAILED
```

`RIST_SPRING_CALLBACK_URL`을 빈 문자열로 명시하면 전달을 비활성화하고 ZIP 생성
후 바로 `COMPLETED`로 처리한다. 운영 환경에서는 프로파일 기본 주소 또는 전체
콜백 URL을 반드시 설정한다.
