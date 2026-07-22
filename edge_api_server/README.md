# RIST Edge API Server

`EXPERIMENT_PC_EDGE_API.md`를 구현한 실험 PC - Edge 서버 간 FastAPI
프로젝트이다.

## 제공 기능

- 복합 PK 기반 작업 등록 및 UUID `jobId` 발급
- timestamp와 PK 기반 작업 폴더 생성
- multipart 파일 업로드
- 검증 전 업로드 파일 교체·삭제·목록 조회
- 파일 크기 및 SHA-256 검증
- 전체 bundle 검증
- 의뢰 번호별 장비 작업 집계 조회
- 보고서 생성 요청을 디스크 큐에 적재
- OpenAI 호환 로컬 LLM(`/v1/chat/completions`) 호출 worker
- 로컬 LLM 모델 조회 및 설정 모델 검증(`/v1/models`)
- LLM 입력 크기 제한과 vLLM 오류 변환
- 전처리 JSON 및 선택적 분석 이미지 입력
- 작업 상태 조회
- MariaDB 기반 작업, 파일, 멱등 요청 저장
- 업로드 유효기간 만료 처리
- `manifest.json` 생성 및 갱신
- DPT 다중 업로드·드래그 앤 드롭 FT-IR 웹 분석
- 업로드 바이트 기반 전처리·피크 분석과 Plotly Figure JSON 응답
- TEM/STEM/EDS/코팅층 raw 폴더 업로드와 PowerPoint 보고서 생성

보고서 생성 API는 요청을 작업 폴더의 `queue` 영역에 기록한다. 별도 worker는
`processed` 폴더에 장비별 분석 코드가 생성한 JSON을 읽고 규칙 기반 보고서를
작성한 뒤, 로컬 LLM으로 자유서술 슬롯만 보강한다. 요청한 PDF/PPTX/HTML과
Markdown을 `report-package.zip`으로 패키징하며, `includeRawFiles=true`이면
원본 bundle도 함께 넣는다. 분석 결과와 LLM용 JSON은 Edge 내부 데이터로 ZIP에
포함하지 않는다. 공유 저장소 및 DB 전송 큐 계약은 루트의
`EDGE_SPRING_BOOT_API.md`를 따른다.

## 설치 및 실행

Python 3.11 이상이 필요하다.

Edge 서버에는 프로젝트 루트의 다음 세 폴더를 같은 부모 경로 아래에
배포해야 한다.

```text
RIST/
  common/
  config/
  sune/
  edge_api_server/
```

```bash
cd edge_api_server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 운영 기본 DB는 MariaDB이다. 접속 정보를 먼저 지정한다.
export RIST_DB_HOST=127.0.0.1
export RIST_DB_USER=rist
export RIST_DB_PASSWORD=********

export RIST_ENV=development
python -m app.run
```

작업 파일 큐가 로컬 디스크를 사용하므로 Uvicorn worker는 1개로 실행한다.
여러 서버 인스턴스로 수평 확장하려면 작업 큐를 공유 스토리지/서비스로
전환해야 한다.

환경 전환:

```bash
# 개발 환경: http://bhyoon.me:8000
export RIST_ENV=development
python -m app.run

# 운영 환경: http://192.168.0.10:8000
export RIST_ENV=production
python -m app.run
```

공통 프로파일은 각각 다음 파일에 있다.

```text
../config/environments/development.env
../config/environments/production.env
```

API 문서:

- 개발 Swagger UI: `http://bhyoon.me:8000/docs`
- 개발 OpenAPI JSON: `http://bhyoon.me:8000/openapi.json`
- 개발 상태 확인: `http://bhyoon.me:8000/health`
- 로컬 LLM 상태 확인: `http://bhyoon.me:8000/health/llm`
- FT-IR 웹 분석: `http://bhyoon.me:8000/ftir`
- XRD 웹 미리보기: `http://bhyoon.me:8000/xrd`
- TEM/STEM 보고서 생성: `http://bhyoon.me:8000/tem`

## FT-IR 웹 분석

`/ftir`은 빈 Plotly 그래프에서 시작하며 DPT 파일 선택과 드래그 앤 드롭을
지원한다. 파일을 추가하면 별도 저장 파일을 만들지 않고 업로드 바이트를
메모리에서 전처리·피크 분석한 뒤 그래프를 갱신한다.

- 한 번에 최대 10개 DPT 파일
- 파일당 최대 20MB
- 한 요청의 파일 총합 최대 50MB
- 여러 파일을 샘플별 trace와 피크로 한 그래프에 표시
- 재료·분석 목적별 피크 assignment 라이브러리 다중 선택
- 화면 편집창에서 라이브러리 생성·수정 및 JSON/CSV 파일 가져오기
- 민감도, 범례, 피크 추가·삭제·그룹, 도형 편집 기능 지원
- 파일 제거 시 남은 샘플을 자동 재분석

피크 assignment 라이브러리는 기본적으로 다음 폴더에서 관리한다.

```text
edge_api_server/data/ftir_assignment_libraries/
```

폴더가 처음 생성될 때 기존 일반 작용기표 `general-ftir.csv`와
`sune/data/RIST_FTIR_Library/`의 590개 기준 스펙트럼에서 자동 추출한
카테고리별 marker 피크 JSON들이 함께 복사된다. 예전 설치처럼
`.initialized` marker가 이미 있는 환경도 새 bundled 기본 라이브러리는 한 번
추가된다. 이후 사용자가 삭제한 bundled 라이브러리는 재시작 때 다시 만들지
않는다.

기본으로 제공되는 marker 피크 라이브러리는 배터리 전해질/바인더, 철강
방청제/윤활제/코팅수지, 범용·엔지니어링·바이오 플라스틱, 엘라스토머,
세라믹/무기, 천연섬유 묶음으로 나뉜다. 이 라이브러리들은 각 기준
스펙트럼에서 대표 피크를 최대 5개씩 추출해 `Material marker @ 1234 cm-1`
형태의 후보 이름을 제공한다. 일반 작용기표와 목적이 다르므로 화면에서
필요한 묶음만 선택해 사용한다.

이후에는 폴더에 JSON/CSV 파일을 직접 배치하거나 `/ftir` 화면에서 파일을
가져올 수 있다. `새 라이브러리`를 누르면 빈 편집창에서 직접 만들 수 있고,
라이브러리 이름을 누르면 중심 파수, tolerance, 피크 이름, 색상과 비고를
수정할 수 있다. 체크된 라이브러리만 그래프 assignment에 사용하며 선택
상태는 `적용`/`미적용`으로 표시한다. 서버 파일의 실수 삭제를 막기 위해
기본 설정에서는 라이브러리 전체 삭제를 제공하지 않는다.
`RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED=true`로 설정하고 Edge API
서비스를 재시작하면 웹 화면과 API에서 서버 라이브러리 파일 삭제를 허용한다.
파일명 stem이 API에서 사용하는 라이브러리 ID가 되므로
`melamine.json`, `phenolic-resin.csv`처럼 영문자·숫자·하이픈으로 작성한다.

JSON 형식:

```json
{
  "name": "Melamine",
  "description": "멜라민 분석용 피크 assignment",
  "assignments": [
    {
      "centerWavenumber": 3460,
      "tolerance": 35,
      "name": "N-H stretch",
      "color": "#db2777",
      "note": "멜라민 N-H 후보"
    }
  ]
}
```

CSV 형식:

```csv
center_wn,tolerance,name,color,note
3460,35,N-H stretch,#db2777,멜라민 N-H 후보
```

여러 라이브러리를 선택하면 각 라이브러리 안에서 해당 파수와 일치하는 가장
구체적인 assignment를 하나씩 고른다. 서로 다른 이름이 나오면 그래프에
`후보 A / 후보 B`로 함께 표시하고 hover에 라이브러리 출처를 표시한다.

기본 marker 피크 JSON을 다시 만들 때는 다음 명령을 사용한다.

```bash
cd edge_api_server
.venv/bin/python ../sune/scripts/library/build_assignment_libraries.py
```

웹 화면이 사용하는 API:

```text
POST /api/v1/ftir/analyze
Content-Type: multipart/form-data

files: DPT 파일(복수)
sensitivity: 0~100, 기본값 25
assignment_library_ids: 선택한 라이브러리 ID(복수)

GET    /api/v1/ftir/assignment-libraries
POST   /api/v1/ftir/assignment-libraries
POST   /api/v1/ftir/assignment-libraries/create
GET    /api/v1/ftir/assignment-libraries/{libraryId}
PUT    /api/v1/ftir/assignment-libraries/{libraryId}
DELETE /api/v1/ftir/assignment-libraries/{libraryId}
```

`DELETE`는 `RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED=true`일 때만 성공한다.
기본값에서는 `403 ASSIGNMENT_LIBRARY_DELETE_DISABLED`를 반환한다.

운영 Edge 앱과 같은 라우터를 DB 없이 화면만 개발할 때는 다음 명령을 사용할
수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.ftir_web:create_ftir_preview_app --factory --host 127.0.0.1 --port 8010
```

## XRD 웹 미리보기

`/xrd`는 LIM XRD 보고서 양식을 브라우저에서 바로 확인하기 위한 미리보기
화면이다. raw TXT, ICDD Card PDF, 선택 Excel/CSV/TSV, 선택 이미지 파일을
한 번에 업로드하면 서버가 확장자로 자동 분류하고 기존 `lim.xrd_plot`
렌더러를 사용해 HTML 보고서를 생성해 화면 안에 표시한다. Chrome 계열
브라우저에서는 하나의 `XRD 번들 추가` 영역에 파일과 폴더를 함께 드래그하거나
bundle 폴더를 선택할 수 있다.

- raw TXT: XRD 측정 패턴 그래프
- ICDD Card PDF: 2θ 피크 overlay와 결정상 후보 정보
- Excel/CSV/TSV: 보고서의 `피크 정보` 영역에 제공 표로 표시
- 이미지: 보고서의 `그래프/상매칭 보조 이미지` 영역에 표시

웹 화면이 사용하는 API:

```text
GET  /xrd
POST /api/v1/xrd/analyze
GET  /api/v1/xrd/example
```

`POST /api/v1/xrd/analyze`는 multipart `files` 필드 하나에 raw/PDF/표/이미지
파일을 함께 담아 보낸다. 서버는 `.txt/.dat/.xy/.asc`, `.pdf`,
`.xlsx/.csv/.tsv`, `.png/.jpg/.jpeg/.webp/.gif`를 자동으로 구분한다.

DB 없이 XRD 화면만 개발할 때는 다음 명령을 사용할 수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.xrd_web:create_xrd_preview_app --factory --host 127.0.0.1 --port 8010
```

## TEM/STEM 웹 보고서

`/tem`은 AHN 프로젝트의 TEM/STEM/EDS/코팅층 raw bundle을 브라우저에서
폴더째 업로드하고, 기존 `ahn.processor`와 PowerPoint 템플릿 렌더러를 사용해
PPTX 보고서를 생성한다. Chrome 계열 브라우저에서는 `tem`, `stem`, `report`/`reports`,
`scale` 폴더가 들어 있는 상위 폴더를 드래그하거나 폴더 선택으로 올릴 수 있다.
상위 폴더명이 함께 올라와도 서버가 실제 입력 루트를 자동으로 찾는다.

- `tem`: TEM 이미지 분석 슬라이드
- `stem`: STEM/BF-STEM 이미지 분석 슬라이드
- `report`/`reports`: EDS Word 보고서와 raw spreadsheet
- `scale`: 코팅층 두께 OCR 및 표 슬라이드

웹 화면이 사용하는 API:

```text
GET  /tem
POST /api/v1/tem/upload-sessions
POST /api/v1/tem/upload-sessions/{uploadId}/chunks
POST /api/v1/tem/upload-sessions/{uploadId}/complete
POST /api/v1/tem/analyze
GET  /api/v1/tem/example
GET  /api/v1/tem/report/jobs/{jobId}
GET  /api/v1/tem/report/jobs/{jobId}/download/pptx
GET  /api/v1/tem/report/jobs/{jobId}/download/package
GET  /api/v1/tem/report/jobs/{jobId}/download/analysis-json
```

브라우저는 업로드 세션을 만들고 파일을 조각 단위로 전송한 뒤 `complete`를 호출한다.
브라우저와 서버는 각 1MB 조각의 CRC32를 대조하고, 서버는 모든 조각과 실제 저장
크기가 일치하는지 다시 확인한다. 이어서 이미지 디코딩과
ZIP/DOCX/XLSX/XLSM/XLSB 내부 구조 및 CRC를 검사한다. 암호화 ZIP이나 암호·DRM으로
보호된 Office 파일, 확장자와 실제 형식이 다른 파일은 문제 파일명을 포함한 오류로
차단하며 이 검증이 모두 끝난 뒤에만 `jobId`를 발급한다.

`POST /api/v1/tem/analyze`는 같은 검증을 사용하는 단일 multipart 호환 API다.
서버는 `.tif/.tiff/.png/.jpg/.jpeg/.bmp/.webp`, `.docx`,
`.xlsx/.xls/.xlsm/.xlsb/.csv/.tsv`, `.zip`을 지원한다. 브라우저는 발급된 `jobId`로
`GET /api/v1/tem/report/jobs/{jobId}`를 폴링해 `completed` 상태가 되면 PPTX,
보고서 ZIP, 분석 JSON 다운로드 링크를 표시한다.

DB 없이 TEM 화면만 개발할 때는 다음 명령을 사용할 수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.ahn_web:create_tem_preview_app --factory --host 127.0.0.1 --port 8010
```

FT-IR, Raman, XRD, TEM 화면을 DB 없이 같은 포트에서 함께 확인하려면 통합 preview
앱을 사용한다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.preview_web:create_preview_app --factory --host 127.0.0.1 --port 8000
```

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RIST_ENV` | `development` | `development` 또는 `production` |
| `RIST_CONFIG_DIR` | `config/environments` | 공통 프로파일 파일 경로 |
| `RIST_EDGE_PUBLIC_BASE_URL` | 프로파일 값 | Edge 공개 Base URL 재정의 |
| `RIST_EDGE_BIND_HOST` | 프로파일 값 | Uvicorn bind 주소 재정의 |
| `RIST_EDGE_API_PORT` | 프로파일 값 | Uvicorn 포트 재정의 |
| `RIST_STORAGE_ROOT` | `edge_api_server/data/jobs` | 작업 파일 저장 루트 |
| `RIST_USAGE_LOG_ROOT` | `<RIST_STORAGE_ROOT>/usage` | 운영 관리 사용 기록 저장 루트 |
| `RIST_USAGE_LOG_RETENTION_DAYS` | `90` | 사용 기록 자동 보관 기간(일) |
| `RIST_ERROR_ARCHIVE_ROOT` | `<RIST_STORAGE_ROOT>/errors` | 통합 오류 로그와 실패 파일 보관 루트 |
| `RIST_ERROR_RETENTION_DAYS` | `30` | 오류 기록 자동 보관 기간(일) |
| `RIST_ERROR_CAPTURE_FILES` | `true` | 오류 당시 업로드/입력 파일 보관 여부 |
| `RIST_ERROR_MAX_FILE_BYTES` | `536870912` | 오류 보관 개별 파일 최대 크기 |
| `RIST_ERROR_MAX_TOTAL_BYTES` | `2147483648` | 오류 한 건당 보관 파일 총크기 상한 |
| `RIST_FTIR_ASSIGNMENT_LIBRARY_DIR` | `edge_api_server/data/ftir_assignment_libraries` | FT-IR 피크 assignment 라이브러리 폴더 |
| `RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED` | `false` | `true`일 때 FT-IR 라이브러리 파일 삭제 API/UI 활성화. 서비스 재시작 후 반영 |
| `RIST_DB_HOST` | `127.0.0.1` | MariaDB 호스트 |
| `RIST_DB_PORT` | `3306` | MariaDB 포트 |
| `RIST_DB_NAME` | `rist_edge` | MariaDB 데이터베이스명(없으면 자동 생성) |
| `RIST_DB_USER` | `rist` | MariaDB 사용자 |
| `RIST_DB_PASSWORD` | 빈 값 | MariaDB 비밀번호 |
| `RIST_DB_POOL_SIZE` | `8` | API/worker 프로세스당 MariaDB 최대 연결 수 |
| `RIST_DB_POOL_TIMEOUT_SECONDS` | `10` | 풀 연결 대기 제한 시간(초) |
| `RIST_PDF_FONT_PATH` | 없음 | 외부 전달용 PDF에 임베드할 한글 TrueType 폰트 경로 |
| `RIST_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `RIST_LOG_FORMAT` | `text` | `text` 또는 `json`(구조화 로그) |
| `RIST_LOG_FILE` | 없음 | 지정 시 회전 파일 핸들러 추가 |
| `RIST_LOG_DIR` | 없음 | 디렉터리만 지정. `<DIR>/rist.log`로 기록 |
| `RIST_LOG_MAX_BYTES` | `10485760` | 회전 파일 한 개의 최대 크기 |
| `RIST_LOG_BACKUP_COUNT` | `5` | 보관할 회전 파일 개수 |
| `RIST_UPLOAD_EXPIRY_HOURS` | `24` | 업로드 유효시간 |
| `RIST_MAX_UPLOAD_BYTES` | `2147483648` | 개별 파일 최대 크기 |
| `RIST_SUPPORTED_EXPERIMENT_CODES` | 빈 값 | 쉼표 구분 허용 실험코드. 빈 값이면 제한 없음 |
| `RIST_LLM_BASE_URL` | `http://127.0.0.1:8001` | OpenAI 호환 로컬 LLM 주소 |
| `RIST_LLM_MODEL` | `gemma4-e4b` | `/v1/chat/completions` 요청의 model 값 |
| `RIST_LLM_TIMEOUT_SECONDS` | `180` | LLM 요청 제한 시간 |
| `RIST_LLM_TEMPERATURE` | `0.1` | 보고서 작성 temperature |
| `RIST_LLM_MAX_TOKENS` | `1200` | LLM 최대 출력 토큰 수 |
| `RIST_LLM_CONTEXT_WINDOW` | `8192` | 모델 컨텍스트 길이 |
| `RIST_LLM_CONTEXT_MARGIN` | `256` | 컨텍스트 계산 안전 여유 토큰 |
| `RIST_LLM_VALIDATE_MODEL` | `true` | 실행 전 `/v1/models`에서 모델 확인 |
| `RIST_LLM_INCLUDE_IMAGES` | `true` | 처리 결과 이미지의 vision 입력 사용 |
| `RIST_LLM_MAX_IMAGES` | `3` | 한 요청에 포함할 최대 이미지 수 |
| `RIST_LLM_MAX_IMAGE_BYTES` | `2097152` | 이미지 한 장의 최대 바이트 수 |
| `RIST_LLM_MAX_INPUT_CHARS` | `200000` | 구조화 분석 JSON 최대 문자 수 |
| `RIST_PROCESSOR_TIMEOUT_SECONDS` | `600` | 자동 processor 실행 제한 시간 |
| `RIST_PROCESSOR_COMMAND_<EXPERIMENT>` | 없음 | 분석 JSON이 없을 때 실행할 processor 명령 템플릿 |
| `RIST_TEM_REPORT_WORKERS` | `1` | TEM/STEM 웹 보고서 동시 생성 작업 수. PPT/OCR 메모리 사용량 때문에 기본은 순차 처리 |
| `RIST_TEM_OCR_WORKERS` | `2` | TEM 코팅층 두께 OCR 병렬 처리 수. CPU 여유가 있으면 최대 4까지 권장 |
| `RIST_WORKER_POLL_SECONDS` | `2` | worker 큐 조회 간격 |
| `RIST_REPORT_STORAGE_KEY` | `RIST_REPORTS` | DB 상대 경로를 해석할 공유 저장소 논리 키 |
| `RIST_REPORT_TRANSFER_MAX_ATTEMPTS` | `5` | LIMS 전송 큐 최대 시도 횟수 |
| `RIST_REPORT_TEST_RETENTION_DAYS` | `7` | 미전송 테스트 보고서 DB 정책 최초 생성 기본값 |
| `RIST_REPORT_FAILED_RETENTION_DAYS` | `30` | 실패·취소 보고서 DB 정책 최초 생성 기본값 |
| `RIST_REPORT_COMPLETED_RETENTION_DAYS` | `90` | LIMS 완료 보고서 DB 정책 최초 생성 기본값 |
| `RIST_REPORT_TRASH_RETENTION_DAYS` | `7` | 휴지통 DB 정책 최초 생성 기본값 |
| `RIST_AUTH_ENABLED` | `true` | 웹 회원 로그인과 프로젝트별 접근 제어 사용 여부 |
| `RIST_AUTH_SESSION_HOURS` | `12` | 로컬 로그인 세션 유지 시간 |
| `RIST_AUTH_RECENT_SSO_MINUTES` | `30` | 보고서 전송에 인정할 최근 SSO 인증 시간 |
| `RIST_AUTH_COOKIE_SECURE` | production `true` | HTTPS에서만 세션 쿠키를 보내는 보안 설정. HTTP 개발 서버는 `false` |
| `RIST_AUTH_BOOTSTRAP_ADMIN_IDS` | 빈 값 | 쉼표로 구분한 최초 관리자 로그인 ID. 빈 초기 설치는 첫 가입자가 관리자 |
| `RIST_SSO_PROVIDER_NAME` | `RIST SSO` | 화면과 DB에 표시할 사내 OIDC 공급자 이름 |
| `RIST_SSO_ISSUER_URL` | 없음 | OIDC issuer URL |
| `RIST_SSO_CLIENT_ID` | 없음 | Edge 웹용 OIDC client ID |
| `RIST_SSO_CLIENT_SECRET` | 없음 | Edge 웹용 OIDC client secret |
| `RIST_SSO_SCOPES` | `openid profile email` | OIDC 요청 scope |

`config/environments/*.env`는 git으로 공유하는 Edge scheme/host/port/bind 기본값만
담는다. 서버마다 달라지는 런타임 값은 `/home/rist/ritas/edge.env` 한 곳에서
관리한다.

- LLM 주소/모델은 `RIST_LLM_*`를 사용한다.
- 최종 ZIP은 `RIST_STORAGE_ROOT` 공유 저장소에 두고 DB에는
  `RIST_REPORT_STORAGE_KEY`와 상대 경로만 등록한다. Spring Boot는 큐를 조회해
  같은 공유 저장소에서 ZIP을 직접 읽는다.
- Edge 공개 주소는 프로파일의 `EDGE_SERVER_SCHEME/HOST/PORT`에서 조합하고,
  필요하면 `RIST_EDGE_PUBLIC_BASE_URL`로 재정의한다.

## 웹 회원과 사내 SSO

`RIST_AUTH_ENABLED=true`이면 가입한 회원은 관리자 승인과 프로젝트 권한을 받은
뒤 FTIR, Raman, XRD, TEM 화면에서 보고서를 생성할 수 있다. 보고서 생성에는
사내 SSO가 필요하지 않다. LIMS 전송에는 `REPORT_SENDER` 역할, 연결된 사내 SSO,
최근 SSO 재인증이 모두 필요하며 전송 작업자 값은 SSO 사용자 정보로 기록한다.

관리자는 운영 관리의 `회원 관리` 탭(`/admin/users`)에서 가입 승인 상태,
프로젝트별 접근권한, `ADMIN`·`REPORT_SENDER` 역할과 SSO 연결·최근 인증 상태를
확인하고 변경한다. 관리자 상태만 수동으로 활성화해 역할이 누락된 경우에는
`deploy/mariadb_grant_admin.sql`을 실행한 뒤 다시 로그인한다.

```text
/signup       로컬 회원 가입
/login        로그인
/account      회원정보·비밀번호 변경, 로그아웃, 프로젝트 및 SSO 상태 확인
/admin/users  회원 승인과 프로젝트·전송 역할 관리
```

DB 적용과 OIDC Redirect URI, 최초 관리자 생성 및 배포 순서는
[`documents/EDGE_WEB_AUTH.md`](../documents/EDGE_WEB_AUTH.md)를 참조한다. 실험 PC의
C# 작업 API는 브라우저 회원 쿠키 인증과 분리되어 기존 인터페이스를 유지한다.

## 운영 관리

FT-IR, Raman, XRD, TEM 및 공통 Edge API의 사용 기록, 오류 기록, 보고서와 파일
보존 상태를 한 화면에서 탭으로 나누어 확인한다.

```text
http://<Edge 서버>:8000/operations
```

보고서와 저장소 관리 탭은 다음 주소로 직접 열 수도 있다.

```text
http://<Edge 서버>:8000/report-management
```

- **사용 기록**: 화면 진입, 의뢰 조회, 업로드 완료, 보고서 생성·다운로드·전송 등
  실제 사용자 동작을 프로젝트, 처리 결과, 날짜, 의뢰번호, 작업 ID로 검색한다.
- 사용 기록, 오류 기록, 보고서/파일 관리 목록은 서버 측 페이징을 사용한다. 기본은
  페이지당 25건이며 화면에서 25·50·100건으로 변경하고 이전·다음 페이지를 이동한다.
- 기록 유형을 `화면 조회`, `정보 조회`, `파일 전송`, `보고서 생성 요청`,
  `보고서 완료`, `보고서 실패`, `다운로드`, `보고서 전송`으로 구분한다.
  `보고서 완료`는 요청을 접수한 시점이 아니라 PPTX/PDF/HTML/ZIP 산출물이
  백그라운드에서 실제 생성된 시점에만 기록된다.
- 공통 작업 API로 파일을 보내는 C#/.NET 실험 PC도 사용 기록 대상이다. 작업의
  프로젝트·의뢰번호·장비·실험자와 클라이언트명/버전/PC 이름, 업로드 파일의
  상대 경로·크기·SHA-256을 함께 조회할 수 있다. `X-Client-Type`,
  `X-Client-Name`, `X-Client-Version` 헤더를 보내면 프로그램을 명확히 구분한다.
- 목록의 `클라이언트 / 접속 위치`에는 클라이언트 종류·프로그램명, 실험 PC 이름,
  접속 IP가 함께 표시된다. 프록시를 사용하는 경우 상세 화면에서
  `X-Forwarded-For` 전달 경로와 서버가 직접 확인한 연결 IP도 구분해 조회한다.
- 업로드 청크와 보고서 상태 반복 조회처럼 운영 이력으로 의미가 낮은 내부 요청은
  사용 기록에서 제외한다.
- HTTP 요청 오류와 비동기 보고서 생성 오류를 프로젝트별로 모아 표시한다.
- 오류 코드, 메시지, 작업 ID, 요청 경로, 스택 트레이스를 기록한다.
- 실패 시점까지 업로드된 파일을 별도 오류 폴더에 복사하며, 개별 파일 또는
  사건 전체 ZIP으로 내려받을 수 있다.
- 목록에서 미해결/해결 상태를 변경하거나 보관 기록을 삭제할 수 있다.
- 오류 상세의 `고객 코멘트`에서 재현 상황과 추가 설명을 기록한다. 고객에게는
  `/error-feedback/{errorEventId}` 주소를 전달하면 오류 로그나 보관 파일을
  노출하지 않고 해당 오류에 코멘트만 남길 수 있다.
- API 오류 응답에는 추적용 `X-Error-Event-Id`와 고객 입력 주소인
  `X-Error-Comment-Url` 헤더가 포함된다. JSON 본문과 비동기 보고서 상태에도
  동일한 `errorEventId`, `errorFeedbackUrl`이 포함되므로 브라우저와 C# 클라이언트
  모두 실패 직후 고객 코멘트 화면을 열 수 있다.
- **보고서/파일 관리**에서는 의뢰번호, 프로젝트, 장비, 실험자, 보고서 생성·전송
  상태, 최근 오류, 보존 기한과 LIMS 완료 시각을 함께 조회한다.
- `report_artifacts`에 등록된 RAW/ZIP/PPTX/PDF/HTML/XLSX/IMAGE의 실제 존재 여부,
  크기, SHA-256, 공유 저장소 상대 경로를 확인하고 개별 파일을 내려받을 수 있다.
- 저장소 전체·프로젝트별 사용량, DB에만 있는 파일, DB 등록 없이 남은
  `web-reports` 파일을 표시한다.
- 실패·취소 전송 재시도, 대기 전송 취소, 테스트 표시, 보존 고정, 보존 기한 직접
  지정, 정리 대상 미리보기와 선택 일괄 정리를 지원한다.
- `보존 정책 설정`에서 미전송 테스트, 실패·취소, LIMS 완료, 휴지통 파일의
  보존일과 자동 정리 여부를 변경할 수 있다. 변경값은 DB에 저장되어 API와
  worker에 즉시 적용되므로 서비스 재시작이 필요하지 않다.
- 활성 전송(`PENDING`, `PROCESSING`, `RETRY_WAIT`) 삭제 금지와 보존 고정 보고서의
  자동 정리 제외 규칙은 안전 정책으로 고정되어 운영 화면에서 해제할 수 없다.

파일 정리는 즉시 삭제하지 않는다. 활성 전송(`PENDING`, `PROCESSING`,
`RETRY_WAIT`)과 보존 고정 보고서는 정리할 수 없으며, 선택 항목 중 하나라도
차단되면 일괄 정리 전체가 중단된다. 정리 대상 파일은 먼저
`<RIST_STORAGE_ROOT>/.report-trash`로 이동하고 DB에 작업자·사유·휴지통 경로를
기록한다. 다른 보고서가 같은 파일을 참조하면 해당 물리 파일은 유지한다.

기본 자동 보존 정책은 미전송 테스트 7일, 실패·취소 30일, LIMS 완료 90일이며
휴지통 파일은 7일 뒤 실제 삭제한다. 위 환경 변수는 DB에 정책 행이 없을 때만
사용하는 최초 생성 기본값이다. 이후 변경은 운영 관리 화면에서 수행한다.

사용 기록의 기본 보관 위치는 `<RIST_STORAGE_ROOT>/usage`, 기간은 90일이다.
오류 기록은 `<RIST_STORAGE_ROOT>/errors`에 30일간 보관한다. 대용량 raw 파일
때문에 디스크가 과도하게 사용되지 않도록 개별 파일 512 MiB, 오류 한 건 전체
2 GiB 상한을 적용한다. 기존 `/errors` 주소는 오류 기록 탭으로 바로 진입하는
호환 주소로 유지한다. 사용·오류 기록 관련 환경 설정을 변경한 경우에는 API와
worker 서비스를 함께 재시작한다.

## 데이터베이스

운영 기본 백엔드는 **MariaDB**이다. 서버 실행 전에 MariaDB가 동작 중이어야
하며, 접속 정보를 환경 변수로 지정한다.

```bash
export RIST_DB_HOST=127.0.0.1     # 엣지 서버 로컬 MariaDB
export RIST_DB_PORT=3306
export RIST_DB_NAME=rist_edge
export RIST_DB_USER=rist
export RIST_DB_PASSWORD=********
```

- 지정한 데이터베이스(`RIST_DB_NAME`)가 없으면 서버 시작 시 `utf8mb4`로
  자동 생성하고 필요한 테이블과 `request_summary` View를 만든다. 따라서 DB
  사용자에게 `CREATE`, `CREATE VIEW` 권한이 있어야 한다.
- 드라이버는 순수 파이썬 `PyMySQL`을 사용하므로 시스템 라이브러리 설치가
  필요 없다.
- 연결은 프로세스별로 재사용한다. 동시 요청 수와 MariaDB `max_connections`를
  고려해 `RIST_DB_POOL_SIZE`를 조정한다.
- 작업 파일 큐는 로컬 디스크를 사용한다. 여러 서버로 수평 확장하려면 공유
  스토리지가 필요하다.

## PDF 한글 폰트

PDF는 ReportLab으로 생성하며, 한글이 네모로 깨지지 않도록 한글 글리프가 있는
TTF/OTF/TTC 폰트만 임베드한다. `RIST_PDF_FONT_PATH`를 비우면 macOS 기본 한글
폰트, Ubuntu `fonts-nanum`/`fonts-noto-cjk` 계열, `/opt/rist/fonts` 순서로
자동 탐색한다. 자동 탐색에 실패하면 깨진 PDF를 만들지 않고 오류를 낸다.
Ubuntu의 `fonts-noto-cjk`는 `.ttc` 컬렉션이 ReportLab에서 등록되지 않는
환경이 있으므로 운영 서버는 `fonts-nanum`과 명시 경로 사용을 권장한다.

```bash
export RIST_PDF_FONT_PATH=/usr/share/fonts/truetype/nanum/NanumGothic.ttf
```

Ubuntu 서버에서 자동 탐색 또는 명시 경로를 사용하려면 한글 폰트를 설치한다.

```bash
sudo apt install -y fonts-nanum
```

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

테스트는 MariaDB(또는 MySQL) 인스턴스가 필요하다. 접속 정보는 다음 환경
변수로 지정하며, 각 테스트는 격리된 임시 데이터베이스를 생성·삭제한다.
접속할 수 없으면 해당 테스트는 건너뛴다.

```bash
export RIST_TEST_DB_HOST=127.0.0.1
export RIST_TEST_DB_PORT=3306
export RIST_TEST_DB_USER=root
export RIST_TEST_DB_PASSWORD=********
```

Docker가 있는 개발 장비에서는 임시 MariaDB를 자동으로 띄워 통합 테스트를
실행할 수 있다.

```bash
PYTHON_BIN=.venv/bin/python scripts/run_mariadb_tests.sh
```

## 로컬 LLM 및 보고서 worker

로컬 LLM은 다음 주소에서 OpenAI 호환 API를 제공해야 한다.

```text
http://127.0.0.1:8001/v1/models
http://127.0.0.1:8001/v1/chat/completions
```

연결과 모델 설정은 다음 API로 확인한다.

```bash
curl http://127.0.0.1:8000/health/llm
```

보고서 요청을 받으면 다음 파일이 생성된다.

```text
{jobRoot}/queue/report-request.json
```

장비별 processor는 LLM 실행 전에 구조화 분석 결과 JSON을 다음 위치에
하나 이상 생성해야 한다.

```text
{jobRoot}/processed/analysis-result.json
```

`processed` 폴더에 JSON이 없고 실험 코드별 processor 명령이 설정되어 있으면
worker가 보고서 생성 전에 해당 명령을 실행한다. 환경 변수 이름은 실험 코드를
대문자로 바꾸고 영숫자가 아닌 문자를 `_`로 치환한 값이다. 예를 들어 `FT-IR`은
`RIST_PROCESSOR_COMMAND_FT_IR`, `XRD`는 `RIST_PROCESSOR_COMMAND_XRD`를 사용한다.

명령 템플릿에는 다음 placeholder를 사용할 수 있다.

```text
{job_root}
{input_dir}
{processed_dir}
{report_dir}
{experiment_code}
{job_id}
```

예시:

```bash
export RIST_PROCESSOR_COMMAND_XRD='python -m lim.xrd.cli "{input_dir}/raw.txt" "{input_dir}/ICDD Card" -o "{processed_dir}/xrd.html"'
```

이미지 입력을 사용할 경우 `processed` 폴더에 `png`, `jpg`, `jpeg`, `webp`
파일을 둔다. 최대 3개, 파일당 2 MiB까지 data URL로 전달하며 이 값은 환경
변수로 변경할 수 있다.

worker 실행:

```bash
source .venv/bin/activate
export RIST_ENV=development
python -m app.report_worker
```

대기 작업 한 건만 처리하고 종료:

```bash
python -m app.report_worker --once
```

worker가 생성하는 파일:

```text
{jobRoot}/logs/llm-request.json
{jobRoot}/logs/llm-response.json
{jobRoot}/logs/processor-<experiment>.json
{jobRoot}/report/report.json
{jobRoot}/report/report.md
{jobRoot}/report/report.pptx 또는 report.pdf
```

LLM에는 원본 bundle을 보내지 않고 `processed` 폴더의 JSON과 허용된 분석
이미지만 전달한다. 요청 로그에는 이미지의 base64 본문을 기록하지 않는다.
보고서는 먼저 규칙 기반 작성기가 판정, 수치, 표를 결정론적으로 채운 뒤,
LLM이 `summary`, `narrative`, `caption` 자유서술 슬롯만 보조 작성한다.
LLM 호출이 실패해도 규칙 기반 기본 문안으로 `report.json`, `report.md`,
요청 포맷의 PPTX/PDF를 완성하며, 작업은 `COMPLETED`, 진행률 100%로 종료된다.

FT-IR 작업은 라이브러리 매칭 결과와 룰 기반 판정을 구분해 고정 섹션을 만들고,
단정적 해석을 피하는 전용 프롬프트로 자유서술 슬롯만 보강한다.
