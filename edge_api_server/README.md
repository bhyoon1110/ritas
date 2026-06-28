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

보고서 생성 API는 요청을 작업 폴더의 `queue` 영역에 기록한다. 별도 worker는
`processed` 폴더에 장비별 분석 코드가 생성한 JSON을 읽고 규칙 기반 보고서를
작성한 뒤, 로컬 LLM으로 자유서술 슬롯만 보강한다. 요청한 PDF/PPTX/HTML과
Markdown을 `report-package.zip`으로 패키징하며, `includeRawFiles=true`이면
원본 bundle도 함께 넣는다. 분석 결과와 LLM용 JSON은 Edge 내부 데이터로 ZIP에
포함하지 않는다. Spring Boot 전달 계약은 루트의 `EDGE_SPRING_BOOT_API.md`를 따른다.

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

## FT-IR 웹 분석

`/ftir`은 빈 Plotly 그래프에서 시작하며 DPT 파일 선택과 드래그 앤 드롭을
지원한다. 파일을 추가하면 별도 저장 파일을 만들지 않고 업로드 바이트를
메모리에서 전처리·피크 분석한 뒤 그래프를 갱신한다.

- 한 번에 최대 10개 DPT 파일
- 파일당 최대 20MB
- 한 요청의 파일 총합 최대 50MB
- 여러 파일을 샘플별 trace와 피크로 한 그래프에 표시
- 민감도, 범례, 피크 추가·삭제·그룹, 도형 편집 기능 지원
- 파일 제거 시 남은 샘플을 자동 재분석

웹 화면이 사용하는 API:

```text
POST /api/v1/ftir/analyze
Content-Type: multipart/form-data

files: DPT 파일(복수)
sensitivity: 0~100, 기본값 25
```

운영 Edge 앱과 같은 라우터를 DB 없이 화면만 개발할 때는 다음 명령을 사용할
수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.ftir_web:create_ftir_preview_app --factory --host 127.0.0.1 --port 8010
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
| `RIST_WORKER_POLL_SECONDS` | `2` | worker 큐 조회 간격 |
| `RIST_SPRING_CALLBACK_URL` | 프로파일 기본 URL | 로컬 Spring Boot 결과 수신 URL 전체 재정의 |
| `RIST_SPRING_CALLBACK_TIMEOUT_SECONDS` | `60` | Spring Boot 전달 제한 시간 |
| `RIST_SPRING_CALLBACK_MAX_ATTEMPTS` | `3` | Spring Boot 전달 최대 시도 횟수 |

`LOCAL_SPRING_BOOT_BASE_URL`은 환경 프로파일의 Spring Boot 기본 주소이며,
Edge는 `{BASE_URL}/api/v1/edge/reports`로 전달한다. 다른 경로를 쓰면
`RIST_SPRING_CALLBACK_URL`에 전체 URL을 지정한다.

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

PDF는 ReportLab으로 생성한다. 개발 환경에서 `RIST_PDF_FONT_PATH`를 비우면
호환용 CJK CID 폰트를 사용한다. 외부 전달용 PDF는 대상 환경에 설치된 폰트에
의존하지 않도록, 배포 권한이 있는 한글 TrueType 폰트 경로를 지정해 임베드한다.

```bash
export RIST_PDF_FONT_PATH=/opt/rist/fonts/NotoSansKR-Regular.ttf
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
