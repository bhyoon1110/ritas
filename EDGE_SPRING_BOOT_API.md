# Edge 분석 서버 - Spring Boot 로컬 서버 REST API 협의안

## 1. 목적

Edge 분석 서버가 실험 결과 보고서 생성을 완료하거나 최종 실패했을 때,
동일 Edge 서버에 배포된 Spring Boot 로컬 서버로 결과 상태와 파일 위치를
전달하기 위한 REST API를 정의한다.

이 문서의 호출 주체는 Edge 분석 서버이고, 수신 주체는 Spring Boot
로컬 서버이다.

본 문서는 Spring Boot 개발자와 협의하기 위한 Edge 측 요구사항 초안이다.
최종 엔드포인트, HTTP 메서드, 요청·응답 필드 및 오류 코드는 Spring Boot
개발자가 제공하는 확정 명세에 맞춰 변경할 수 있다.

## 2. 기본 정보

| 항목 | 값 |
|---|---|
| API 버전 | `v1` |
| Base URL | `http://{spring-server}/api/v1` |
| 문자 인코딩 | UTF-8 |
| 요청 형식 | `application/json` |
| 시간 형식 | ISO 8601, KST 포함 (`2026-06-13T14:35:22.431+09:00`) |

별도의 애플리케이션 인증은 적용하지 않는다. 두 서비스가 같은 서버에
배포되는 경우 Spring Boot의 해당 API 포트는 `localhost` 또는 내부 전용
인터페이스에만 바인딩한다.

## 3. 식별자

완료 통보에는 반드시 Edge의 `jobId`와 업무 복합 PK를 함께 포함한다.

```json
{
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "pk": {
    "requestNumber": "REQ-2026-00123",
    "experimentCode": "XRD",
    "equipmentCode": "XRD-01",
    "operatorId": "user01"
  }
}
```

Spring Boot는 `jobId`를 이벤트 중복 방지 키로 사용하고, 복합 PK를 업무
데이터 연결 및 검증에 사용한다.

동일 복합 PK의 이전 작업이 종료된 후 재시험이 등록될 수 있으므로
Spring Boot는 복합 PK만으로 완료 이벤트를 중복 판단하지 않는다. 서로 다른
`jobId`는 별도 실행 회차로 처리하며, 사용자 조회 시에는 가장 최근에 완료된
회차를 기본 결과로 사용할 수 있다.

## 4. 결과 파일 위치 규칙

완료 통보에는 운영체제 절대 경로가 아닌 Edge 저장소 루트 기준 상대 경로를
전달한다.

```text
2026/06/13/
20260613T143025123+0900_e575b716-25d6-49c6-a7c0-3e2b7136fb2c/
REQ-2026-00123_XRD_XRD-01_user01/report/result.pptx
```

- 경로 구분자는 `/`로 통일한다.
- 경로에는 `..`를 허용하지 않는다.
- Spring Boot가 실제 파일을 읽어야 한다면 두 서비스가 합의한 동일
  `storageRoot`에 상대 경로를 결합한다.
- 서버가 분리될 가능성이 있으면 `downloadUrl`을 추가하고 공유 파일시스템
  경로 의존을 제거한다.

## 5. 공통 헤더

| 헤더 | 필수 | 설명 |
|---|---:|---|
| `X-Request-Id` | Y | 호출 추적용 UUID |
| `Idempotency-Key` | Y | `{jobId}:{eventType}` 형식 |
| `Content-Type` | Y | `application/json` |

## 6. API

### 6.1 보고서 생성 완료 통보

```http
POST /api/v1/analysis-results/completed
Content-Type: application/json
Idempotency-Key: e575b716-25d6-49c6-a7c0-3e2b7136fb2c:REPORT_COMPLETED
```

#### 요청

```json
{
  "eventId": "9958d736-553f-4fa1-bee8-8bbcb9f9a6dc",
  "eventType": "REPORT_COMPLETED",
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "pk": {
    "requestNumber": "REQ-2026-00123",
    "experimentCode": "XRD",
    "equipmentCode": "XRD-01",
    "operatorId": "user01"
  },
  "completedAt": "2026-06-13T14:35:22.431+09:00",
  "resultFiles": [
    {
      "fileType": "REPORT",
      "fileName": "REQ-2026-00123_XRD_report.pptx",
      "relativePath": "2026/06/13/20260613T143025123+0900_e575b716-25d6-49c6-a7c0-3e2b7136fb2c/REQ-2026-00123_XRD_XRD-01_user01/report/REQ-2026-00123_XRD_report.pptx",
      "mediaType": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      "sizeBytes": 2481304,
      "sha256": "9ca0d0d3af4514711de0439df04be924babe90fb4214f3a7644971b4f23cbac1"
    },
    {
      "fileType": "ANALYSIS_DATA",
      "fileName": "Mix2.html",
      "relativePath": "2026/06/13/20260613T143025123+0900_e575b716-25d6-49c6-a7c0-3e2b7136fb2c/REQ-2026-00123_XRD_XRD-01_user01/report/Mix2.html",
      "mediaType": "text/html",
      "sizeBytes": 738402,
      "sha256": "e97fcf73098504d2197536ac68d37f634de4d8139171683012f342ca254489c5"
    }
  ],
  "summary": {
    "processor": "xrd",
    "processorVersion": "1.0.0",
    "inputFileCount": 6,
    "warningCount": 0
  }
}
```

#### 필드 정의

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `eventId` | UUID | Y | 완료 이벤트 식별자 |
| `eventType` | string | Y | 항상 `REPORT_COMPLETED` |
| `jobId` | UUID | Y | Edge 작업 식별자 |
| `pk` | object | Y | 네 필드로 구성된 업무 복합 PK |
| `completedAt` | datetime | Y | Edge 처리 완료 시각 |
| `resultFiles` | array | Y | 한 개 이상의 결과 파일 |
| `summary` | object | N | processor 및 처리 요약 |

보고서 생성 요청에서 선택한 `reportFormat`에 따라 `REPORT` 파일은 다음
형식 중 하나로 전달한다.

| reportFormat | 확장자 | mediaType |
|---|---|---|
| `PPTX` | `.pptx` | `application/vnd.openxmlformats-officedocument.presentationml.presentation` |
| `PDF` | `.pdf` | `application/pdf` |

`resultFiles[].fileType` 권장 값:

- `REPORT`: 최종 보고서
- `ANALYSIS_DATA`: 분석 결과 데이터 또는 HTML
- `CHART`: 그래프 이미지
- `TABLE`: 결과표
- `LOG`: 전달이 필요한 처리 로그
- `OTHER`: 기타 산출물

#### 성공 응답: `200 OK`

```json
{
  "accepted": true,
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "resultId": "AR-20260613-000018",
  "receivedAt": "2026-06-13T14:35:22.519+09:00",
  "message": "분석 결과가 등록되었습니다."
}
```

`200 OK`는 Spring Boot가 요청 스키마 검증과 업무 DB 반영을 모두 완료했음을
의미한다. 단순 수신만 완료된 `202 Accepted`는 본 협의안에서 사용하지 않는다.
Spring Boot가 비동기 수신 방식을 요구하는 경우에는 최종 반영 결과를 확인할
별도 상태 조회 API를 함께 협의해야 하며, 그 전까지 Edge는 작업을
`COMPLETED`로 변경하지 않는다.

#### 중복 요청

동일한 `jobId`와 `eventType`의 요청이 이미 성공한 경우 새 결과를 만들지
않고 최초 성공 응답과 동일한 `resultId`를 반환한다.

결과 파일 목록 또는 해시가 기존 요청과 다르면 `409 Conflict`를 반환한다.

#### 오류

- `400 Bad Request`: 필수 필드 또는 경로 형식 오류
- `404 Not Found`: 복합 PK에 해당하는 의뢰를 찾지 못함
- `409 Conflict`: PK 불일치 또는 기존 결과와 다른 중복 요청
- `422 Unprocessable Entity`: 파일이 없거나 SHA-256 검증 실패
- `500 Internal Server Error`: DB 반영 실패

### 6.2 보고서 생성 실패 통보

최종 재시도 후에도 보고서를 생성할 수 없는 경우 호출한다.

```http
POST /api/v1/analysis-results/failed
Content-Type: application/json
Idempotency-Key: e575b716-25d6-49c6-a7c0-3e2b7136fb2c:REPORT_FAILED
```

#### 요청

```json
{
  "eventId": "c26dac2e-d46e-4c5a-9669-276ba27f7b07",
  "eventType": "REPORT_FAILED",
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "pk": {
    "requestNumber": "REQ-2026-00123",
    "experimentCode": "XRD",
    "equipmentCode": "XRD-01",
    "operatorId": "user01"
  },
  "failedAt": "2026-06-13T14:34:01.203+09:00",
  "error": {
    "code": "PROCESSOR_INPUT_INVALID",
    "message": "ICDD PDF 파일을 찾을 수 없습니다.",
    "retryable": false
  }
}
```

#### 성공 응답: `200 OK`

```json
{
  "accepted": true,
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "receivedAt": "2026-06-13T14:34:01.281+09:00",
  "message": "분석 실패 상태가 등록되었습니다."
}
```

## 7. Spring Boot 처리 요구사항

완료 API를 수신한 Spring Boot 서버는 다음 순서로 처리한다.

1. 요청 스키마를 검증한다.
2. `jobId + eventType`의 중복 여부를 확인한다.
3. 복합 PK에 해당하는 의뢰 및 실험 정보를 확인한다.
4. 모든 결과 경로가 허용된 저장소 하위인지 검증한다.
5. 필요한 경우 파일 존재 여부, 크기 및 SHA-256을 검증한다.
6. 트랜잭션으로 결과 파일과 완료 상태를 저장한다.
7. 커밋 성공 후에만 `200 OK`를 반환한다.

PK가 존재하지만 요청의 실험코드, 장비 또는 실험자가 기존 정보와 다르면
자동으로 덮어쓰지 않고 `409 Conflict`를 반환한다.

동일 복합 PK에 서로 다른 `jobId`가 존재하면 새 실행 회차로 등록한다. 최신
결과 연결이 필요한 경우 `completedAt`과 Spring Boot의 수신 시각을 함께
기록하며, 단순 문자열 PK 비교만으로 이전 결과를 덮어쓰지 않는다.

## 8. Edge 처리 요구사항

- 보고서와 결과 파일을 모두 안전하게 기록한 후 완료 API를 호출한다.
- Spring Boot가 성공 응답하기 전까지 Edge 작업 상태는
  `CALLBACK_PENDING`으로 유지한다.
- 성공 응답을 받으면 `COMPLETED`로 변경하고 응답의 `resultId`를 저장한다.
- 분석 실패가 발생한 작업은 작업 상태를 `FAILED`로 유지하고 콜백 전송
  상태를 별도 필드인 `callbackStatus=PENDING`으로 관리한다.
- 실패 통보 성공 시 `callbackStatus=DELIVERED`로 변경한다.
- 콜백 요청과 응답 전문은 민감정보를 제외하고 감사 로그에 기록한다.

## 9. 공통 오류 응답

```json
{
  "timestamp": "2026-06-13T14:35:22.519+09:00",
  "status": 409,
  "code": "RESULT_ALREADY_EXISTS_WITH_DIFFERENT_HASH",
  "message": "기존 결과와 요청한 결과 파일 해시가 다릅니다.",
  "requestId": "4412df00-eb23-4993-874a-9eed69d8145a",
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "retryable": false
}
```

## 10. 재시도 및 장애 처리

- 연결 타임아웃: 3초 권장
- 읽기 타임아웃: 30초 권장
- 재시도 대상: 네트워크 오류, `408`, `429`, `502`, `503`, `504`
- 최초 자동 재시도: 1초, 2초, 4초, 8초, 16초
- 이후 백그라운드 재시도: 1분, 5분, 15분, 30분 간격
- POST 재시도 시 동일한 `eventId`와 `Idempotency-Key`를 사용한다.
- 재시도 요청의 이벤트 발생 시각(`completedAt` 또는 `failedAt`), PK 및
  결과 내용을 변경하지 않는다.
- 최대 재시도 횟수 초과 시 운영 알림을 발생시키고
  완료 작업은 `CALLBACK_PENDING`, 실패 작업은 `FAILED`와
  `callbackStatus=PENDING` 상태로 보존한다.

`400`, `404`, `409`, `422`는 요청 또는 데이터 확인이
필요하므로 무한 자동 재시도하지 않는다.

## 11. 보안 및 감사

- 외부 인터페이스에 결과 파일의 서버 절대 경로를 노출하지 않는다.
- 완료/실패 API는 `localhost` 또는 Edge 분석 서비스의 고정 IP에서만
  접근할 수 있도록 제한한다.
- 각 요청에 `X-Request-Id`, `eventId`, `jobId`, PK 및 처리 결과를 기록한다.
- 보고서 파일의 조회 권한은 Spring Boot의 업무 권한 정책을 따른다.
- 원본 실험 파일은 완료 통보 대상에서 제외하는 것을 기본 원칙으로 한다.

## 12. Spring Boot 개발자와 확정할 항목

다음 항목은 Spring Boot 측 최종 API 명세를 받은 뒤 확정한다.

1. 완료 및 실패 통보의 실제 엔드포인트와 HTTP 메서드
2. 요청 필드명과 복합 PK 전달 구조
3. 결과 파일을 상대 경로로 전달할지, 다운로드 URL로 전달할지 여부
4. 동기 DB 반영 후 `200 OK`를 반환할지, 비동기 `202 Accepted`를 사용할지 여부
5. 성공 응답의 `resultId` 제공 여부와 필드명
6. 동일 `jobId` 재요청에 대한 멱등 처리 기준
7. 오류 응답 형식과 Edge가 재시도해야 하는 HTTP 상태 코드
8. 분석 실패 통보 API 제공 여부
