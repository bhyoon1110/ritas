# 실험 PC - Edge 분석 서버 REST API 명세

## 1. 목적

실험 PC에서 생성된 실험 결과 파일 bundle을 Edge 분석 서버로 전송하고,
전송 완료 후 보고서 생성을 요청하기 위한 REST API를 정의한다.

이 문서의 호출 주체는 실험 PC 프로그램이고, 수신 주체는 Edge 분석 서버이다.

## 2. 기본 정보

| 항목 | 값 |
|---|---|
| API 버전 | `v1` |
| Base URL | `http://{edge-server}/api/v1` |
| 문자 인코딩 | UTF-8 |
| 시간 형식 | ISO 8601, KST 포함 (`2026-06-13T14:30:25.123+09:00`) |
| 메타데이터 형식 | `application/json` |
| 파일 전송 형식 | `multipart/form-data` |

본 인터페이스는 사내 폐쇄망에서만 사용하며 별도의 애플리케이션 인증은
적용하지 않는다. Edge 서버의 API 포트는 방화벽 또는 서버 접근 정책을 통해
등록된 실험 PC에서만 접근할 수 있도록 제한한다.

환경별 기본 주소는 루트 공통 설정을 따른다.

| 환경 | Base URL |
|---|---|
| 개발 | `http://bhyoon.me:8000/api/v1` |
| 운영 | `http://{192.xxx.xxx.xxx:port}/api/v1` |

환경 선택과 주소 변경은 `config/environments/development.env` 및
`config/environments/production.env`에서 관리한다.

## 3. 식별자

### 3.1 업무 복합 PK

하나의 실험 작업은 다음 네 필드의 조합으로 식별한다.

| 필드 | 타입 | 필수 | 설명 | 예시 |
|---|---|---:|---|---|
| `requestNumber` | string | Y | 의뢰번호 | `REQ-2026-00123` |
| `experimentCode` | string | Y | 실험 종류 코드 | `XRD` |
| `equipmentCode` | string | Y | 실험장비 식별 코드 | `XRD-01` |
| `operatorId` | string | Y | 실험자 식별자 | `user01` |

동일 복합 PK로 재시험을 허용해야 하는 경우 PK 값을 임의로 변경하지 않고
서버가 내부 회차를 부여해 관리한다. 기본 명세에서는 활성 작업이 존재하는
동일 복합 PK의 중복 등록을 허용하지 않는다. 활성 작업이 종료된 뒤 동일
복합 PK로 재등록되면 최신 결과만 조회 대상으로 사용한다. 이전 결과에 대한
원상복구 또는 이력 조회 기능은 제공하지 않는다.

### 3.2 기술 식별자

Edge 서버는 작업 등록 시 UUID 형식의 `jobId`를 발급한다. 이후 파일 전송,
보고서 생성 요청 및 상태 조회는 `jobId`를 사용한다.

`jobId`는 업무 복합 PK를 대체하지 않으며 API 재시도와 내부 추적을 위한
기술 식별자이다.

## 4. Edge 저장 규칙

Edge 서버는 작업 등록 시 서버 시간을 기준으로 작업 폴더를 생성한다.
사용자 입력값은 파일시스템 안전 문자로 정규화해야 한다.

```text
{storageRoot}/
  2026/06/13/
    20260613T143025123+0900_{jobId}/
      REQ-2026-00123_XRD_XRD-01_user01/
        input/
        processed/
        report/
        logs/
        manifest.json
```

- `input`: 실험 PC에서 전송한 원본 bundle
- `processed`: 전처리 및 분석 중간 산출물
- `report`: 최종 보고서와 결과 파일
- `logs`: 작업별 처리 로그
- `manifest.json`: PK, 파일 목록, 해시, 상태 및 시간 기록
- timestamp는 Edge 서버의 수신 시각을 사용한다.
- 사용자 관점에서는 동일 실험 재업로드 시 최신 회차 결과를 기본으로
  표시할 수 있다.
- 본 명세 범위에서는 파일 원상복구 및 파일 형상관리 기능을 제공하지 않는다.

## 5. 전체 처리 순서

1. 실험 PC가 작업 등록 API를 호출한다.
2. Edge 서버가 `jobId`와 업로드 대상 경로를 반환한다.
3. 실험 PC가 파일을 하나씩 업로드한다.
4. 실험 PC가 파일 전송 완료 API를 호출한다.
5. Edge 서버가 파일 개수, 크기 및 SHA-256을 검증한다.
6. 실험 PC가 보고서 생성 API를 호출한다.
7. 실험 PC는 상태 조회 API로 처리 결과를 확인할 수 있다.

보고서 생성 요청은 파일 검증 완료 후에만 허용한다.

## 6. 공통 헤더

| 헤더 | 필수 | 설명 |
|---|---:|---|
| `X-Request-Id` | Y | 호출 추적용 UUID. 재시도 시 같은 값 사용 |
| `Idempotency-Key` | POST/PUT/DELETE | 중복 처리 방지 키. 재시도 시 같은 값 사용 |
| `Content-Type` | 본문 전송 시 | JSON 또는 multipart 요청의 콘텐츠 타입 |

## 7. API

### 7.1 작업 등록

```http
POST /api/v1/jobs
Content-Type: application/json
Idempotency-Key: 771e92ae-d06d-42e3-b2c8-d1846619987c
```

#### 요청

```json
{
  "pk": {
    "requestNumber": "REQ-2026-00123",
    "experimentCode": "XRD",
    "equipmentCode": "XRD-01",
    "operatorId": "user01"
  },
  "sourcePc": {
    "hostName": "LAB-PC-XRD-01",
    "declaredIpAddress": "10.10.20.31",
    "clientVersion": "1.0.0"
  }
}
```

파일 수와 전체 크기는 이 시점에 선언하지 않는다. 파일 업로드·교체·삭제를 마친
뒤 `uploads/complete`에서 최종 파일 목록과 함께 확정한다.

기존 PC 호환용 `bundle.fileCount`, `bundle.totalSizeBytes`는 당분간 허용하지만
완료 검증 기준으로 사용하지 않는 deprecated 필드이다.

`sourcePc.declaredIpAddress`는 실험 PC가 자체적으로 확인한 참고 정보이다.
접근 통제와 감사 로그에는 Edge 서버가 TCP 연결에서 확인한 원격 IP를
`observedRemoteIp`로 별도 기록하고 이를 기준으로 사용한다.

#### 성공 응답: `201 Created`

```json
{
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "status": "CREATED",
  "createdAt": "2026-06-13T14:30:25.123+09:00",
  "uploadExpiresAt": "2026-06-14T14:30:25.123+09:00"
}
```

동일한 `Idempotency-Key`와 동일한 요청 본문으로 작업 등록을 재호출하면
새 작업을 만들지 않고 기존 `jobId`와 최초 성공 응답을 반환한다. 동일한
`Idempotency-Key`로 다른 요청 본문을 보내면 `409 Conflict`를 반환한다.

#### 오류

- `400 Bad Request`: PK 또는 sourcePc 정보 오류
- `409 Conflict`: 동일 복합 PK의 활성 작업이 존재하거나 멱등키 요청 내용이 다름
- `500 Internal Server Error`: 폴더 또는 작업 생성 실패

### 7.2 파일 업로드

bundle 내 파일을 한 개씩 전송한다. 하위 폴더 구조가 필요한 경우
`relativePath`에 bundle 루트 기준 상대 경로를 전달한다.

```http
POST /api/v1/jobs/{jobId}/files
Content-Type: multipart/form-data
Idempotency-Key: {jobId}:{relativePath}:{sha256}
```

#### multipart 필드

| 필드 | 타입 | 필수 | 설명 |
|---|---|---:|---|
| `file` | binary | Y | 전송 파일 |
| `relativePath` | string | Y | bundle 기준 상대 경로 |
| `sizeBytes` | integer | Y | 파일 크기 |
| `sha256` | string | Y | 소문자 64자리 SHA-256 |
| `lastModifiedAt` | string | N | 실험 PC 기준 수정 시각 |

`relativePath`에는 절대 경로, `..`, 드라이브 문자 및 null 문자를 허용하지
않는다.

#### 성공 응답: `201 Created`

```json
{
  "fileId": "3fa4be94-2501-4cf7-b60d-616db3e628b8",
  "relativePath": "raw/Mix2.txt",
  "sizeBytes": 532481,
  "sha256": "4f0c93b48e4e8b95c2767a06c45020f3efc3bf45a172838807f90a8d8b43c26d",
  "status": "UPLOADED",
  "uploadedAt": "2026-06-13T14:31:02.410+09:00"
}
```

같은 `Idempotency-Key`와 동일한 파일을 다시 전송하면 기존 성공 결과를
반환한다. 경로는 같지만 해시가 다르면 `409 Conflict`를 반환한다.

#### 파일 교체·조회·삭제

```http
PUT /api/v1/jobs/{jobId}/files/{relativePath}
GET /api/v1/jobs/{jobId}/files
DELETE /api/v1/jobs/{jobId}/files/{relativePath}
```

- `PUT`은 같은 `relativePath`의 파일을 새 파일로 교체한다. multipart 필드는
  `POST`와 같되 경로는 URL에서 받으므로 `relativePath`를 보내지 않는다.
- `GET`은 현재 업로드된 파일의 경로, 크기, SHA-256, 업로드 시각을 반환한다.
- `DELETE`는 Edge 저장 파일과 메타데이터를 함께 삭제한다.
- 세 작업은 `CREATED`, `UPLOADING` 상태에서만 허용한다. `FILES_VERIFIED` 이후
  입력 파일은 불변이며 변경 요청은 `409 Conflict`를 반환한다.

#### 오류

- `400 Bad Request`: 잘못된 상대 경로 또는 메타데이터
- `404 Not Found`: 존재하지 않는 `jobId`
- `409 Conflict`: 이미 다른 내용으로 등록된 경로
- `410 Gone`: 업로드 유효기간 만료
- `413 Payload Too Large`: 서버 제한을 초과한 파일
- `422 Unprocessable Entity`: 크기 또는 SHA-256 불일치

### 7.3 파일 전송 완료

실험 PC가 모든 파일 업로드·교체·삭제를 마친 뒤 호출한다. 이 요청은 이번
분석에 사용할 최종 입력 파일 집합을 확정하는 선언이며, Edge 서버는 실제
업로드 파일의 개수, 전체 크기, 개별 해시를 검증한다.

```http
POST /api/v1/jobs/{jobId}/uploads/complete
Content-Type: application/json
Idempotency-Key: {jobId}:uploads-complete
```

#### 요청

```json
{
  "fileCount": 3,
  "totalSizeBytes": 8392401,
  "files": [
    {
      "relativePath": "raw/Mix2.txt",
      "sizeBytes": 532481,
      "sha256": "4f0c93b48e4e8b95c2767a06c45020f3efc3bf45a172838807f90a8d8b43c26d"
    },
    {
      "relativePath": "cards/TiO2-00-021-1276.pdf",
      "sizeBytes": 3926040,
      "sha256": "3500ca035e6d223cb2cfb7b5ed1e4cb75268ee7ae38c50be50e72d6e282fd204"
    },
    {
      "relativePath": "cards/TiO2-00-064-0863.pdf",
      "sizeBytes": 3933880,
      "sha256": "67c7fdb4b554d171373b45a1db42319a425dc543cd1080698af5ec11df742032"
    }
  ]
}
```

`files`에는 실제 bundle의 전체 파일을 전달한다. `fileCount`는 `files` 배열
길이와 같아야 하고, `totalSizeBytes`는 파일 크기의 합계와 같아야 한다.

동일한 `Idempotency-Key`와 동일한 요청 본문으로 재호출하면 기존
`FILES_VERIFIED` 응답을 반환한다. 이미 검증이 끝난 작업에 다른 파일 목록을
보내면 `409 Conflict`를 반환한다.

#### 성공 응답: `200 OK`

```json
{
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "status": "FILES_VERIFIED",
  "verifiedFileCount": 3,
  "verifiedAt": "2026-06-13T14:31:10.032+09:00"
}
```

#### 오류

- `409 Conflict`: 아직 업로드 중이거나 이미 보고서 생성이 시작됨
- `410 Gone`: 업로드 유효기간 만료
- `422 Unprocessable Entity`: 누락 파일, 개수, 크기 또는 해시 불일치

### 7.4 보고서 생성 요청

```http
POST /api/v1/jobs/{jobId}/report
Content-Type: application/json
Idempotency-Key: {jobId}:generate-report
```

#### 요청

```json
{
  "requestedAt": "2026-06-13T14:31:15.000+09:00",
  "options": {
    "reportFormats": ["PDF", "PPTX", "HTML"],
    "includeRawFiles": true
  }
}
```

`options`는 선택 필드이다. `reportFormat`은 기존 실험 PC 호환을 위해 유지하며,
새 구현은 `reportFormats` 배열을 사용한다. 두 필드를 함께 보내면
`reportFormats`가 우선한다.

- `reportFormats` 지원값: `PPTX`, `PDF`, `HTML` (중복 불가)
- 두 형식을 모두 생략한 경우 기본값: `PPTX`
- `includeRawFiles`를 생략한 경우 기본값: `false`
- `includeRawFiles=true`이면 Edge가 Spring Boot로 전달하는 최종 ZIP에 원본
  bundle을 `raw/` 경로로 함께 넣는다. `false`여도 원본은 Edge `input/`에
  보관하며 ZIP에만 포함하지 않는다.

#### 성공 응답: `202 Accepted`

```json
{
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "status": "QUEUED",
  "acceptedAt": "2026-06-13T14:31:15.120+09:00"
}
```

이 API는 비동기 처리 시작만 보장한다. 최종 완료 여부는 상태 조회 응답으로
확인한다.

worker는 요청한 사용자용 `report.pdf`, `report.pptx`, `report.html` 및
`report.md`를 만든 뒤 `{jobRoot}/report/report-package.zip`으로 묶는다.
분석 결과 JSON, LLM 요청/응답 JSON, 내부 `report.json`은 Edge 내부 처리용이며
Spring Boot 전달 ZIP에는 포함하지 않는다. 전달 API는
[`EDGE_SPRING_BOOT_API.md`](EDGE_SPRING_BOOT_API.md)를 따른다.

#### 오류

- `404 Not Found`: 존재하지 않는 `jobId`
- `409 Conflict`: 파일 검증 미완료 또는 현재 상태에서 실행 불가
- `422 Unprocessable Entity`: 해당 실험/장비 processor를 찾을 수 없음

### 7.5 작업 상태 조회

```http
GET /api/v1/jobs/{jobId}
X-Request-Id: 771e92ae-d06d-42e3-b2c8-d1846619987c
```

#### 성공 응답: `200 OK`

```json
{
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "pk": {
    "requestNumber": "REQ-2026-00123",
    "experimentCode": "XRD",
    "equipmentCode": "XRD-01",
    "operatorId": "user01"
  },
  "status": "PROCESSING",
  "progress": 65,
  "createdAt": "2026-06-13T14:30:25.123+09:00",
  "processingStartedAt": "2026-06-13T14:31:16.003+09:00",
  "completedAt": null,
  "error": null
}
```

실패한 경우:

```json
{
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "status": "FAILED",
  "progress": 50,
  "error": {
    "code": "SPRING_CALLBACK_CONNECTION_FAILED",
    "message": "Spring Boot 결과 전달 연결에 실패했습니다.",
    "retryable": true
  }
}
```

### 7.6 의뢰 번호 목록 조회

```http
GET /api/v1/requests?page=1&pageSize=50
X-Request-Id: 771e92ae-d06d-42e3-b2c8-d1846619987c
```

동일 `requestNumber`를 가진 장비별 작업을 집계해 반환한다. 이 API는 조회용이며
의뢰 단위 통합 보고서를 생성하지 않는다.
Edge DB의 `request_summary` View를 조회하므로 파일·상태 변경이 즉시 반영된다.

```json
{
  "page": 1,
  "pageSize": 50,
  "items": [
    {
      "requestNumber": "REQ-2026-00123",
      "jobCount": 2,
      "completedJobCount": 1,
      "failedJobCount": 0,
      "statuses": ["COMPLETED", "PROCESSING"],
      "experiments": ["FT-IR", "XRD"],
      "equipmentCodes": ["FTIR-01", "XRD-01"],
      "createdAt": "2026-06-13T14:30:25.123+09:00",
      "updatedAt": "2026-06-13T14:35:25.123+09:00"
    }
  ]
}
```

`page`는 1 이상, `pageSize`는 1~200이다.

## 8. 작업 상태

| 상태 | 설명 |
|---|---|
| `CREATED` | 작업과 저장 폴더 생성 |
| `UPLOADING` | 파일 수신 중 |
| `UPLOAD_EXPIRED` | 업로드 유효기간이 지나 작업 등록이 만료됨 |
| `FILES_VERIFIED` | 파일 수신 및 무결성 검증 완료 |
| `QUEUED` | 보고서 생성 대기 |
| `PROCESSING` | 전처리, 분석 또는 보고서 생성 중 |
| `CALLBACK_PENDING` | 최종 ZIP 생성 완료, Spring Boot 전달 중 |
| `COMPLETED` | Spring Boot가 ZIP을 성공적으로 수신 |
| `FAILED` | 복구되지 않은 오류로 작업 실패 |

상태는 이전 단계로 되돌리지 않는다.

`uploadExpiresAt` 이후 검증이 완료되지 않은 작업은 `UPLOAD_EXPIRED`로
전환한다. 만료된 작업에 대한 파일 업로드와 업로드 완료 요청은
`410 Gone`을 반환한다. 실험 PC는 새 `Idempotency-Key`로 작업 등록부터
다시 시작해야 하며, 만료된 `jobId`는 재사용하지 않는다.

## 9. 공통 오류 응답

```json
{
  "timestamp": "2026-06-13T14:31:10.032+09:00",
  "status": 422,
  "code": "FILE_HASH_MISMATCH",
  "message": "파일 SHA-256이 요청값과 일치하지 않습니다.",
  "requestId": "dd5c9c31-bec3-40cd-96aa-2aec9dd6fb11",
  "jobId": "e575b716-25d6-49c6-a7c0-3e2b7136fb2c",
  "retryable": true
}
```

## 10. 재시도 및 타임아웃

- 연결 타임아웃: 5초 권장
- 파일 업로드 읽기 타임아웃: 파일 크기에 따라 5분 이상
- 일반 API 읽기 타임아웃: 30초
- 재시도 대상: 네트워크 오류, `408`, `429`, `502`, `503`, `504`
- 재시도 간격: 1초, 2초, 4초, 8초, 16초의 지수 백오프
- `400`, `409`, `410`, `422`는 요청 수정 없이 자동 재시도하지 않는다.
- POST 재시도 시 최초 요청과 같은 `Idempotency-Key`를 사용한다.

## 11. 파일 및 보안 규칙

- 파일명과 상대 경로는 원본을 보존하되 서버에서 경로 탐색 공격을 차단한다.
- 실행 파일과 스크립트의 허용 여부는 장비별 allowlist로 제한한다.
- 수신 파일은 보고서 처리 전에 SHA-256을 검증한다. 악성코드 검사는 Edge 운영
  환경의 별도 보안 정책으로 적용한다.
- Edge API 포트는 등록된 실험 PC의 고정 IP에서만 접근하도록 제한한다.
- API 로그에는 PK, `jobId`, 요청 시각, 호출 PC 및 결과 코드가 포함되어야 한다.
- 원본 실험 파일과 보고서의 보존 및 삭제 기간은 운영 정책으로 별도 관리한다.
