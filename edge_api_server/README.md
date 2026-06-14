# RIST Edge API Server

`EXPERIMENT_PC_EDGE_API.md`를 구현한 실험 PC - Edge 서버 간 FastAPI
프로젝트이다.

## 제공 기능

- 복합 PK 기반 작업 등록 및 UUID `jobId` 발급
- timestamp와 PK 기반 작업 폴더 생성
- multipart 파일 업로드
- 파일 크기 및 SHA-256 검증
- 전체 bundle 검증
- 보고서 생성 요청을 디스크 큐에 적재
- OpenAI 호환 로컬 LLM(`/v1/chat/completions`) 호출 worker
- 작업 상태 조회
- SQLite 기반 작업, 파일, 멱등 요청 저장
- 업로드 유효기간 만료 처리
- `manifest.json` 생성 및 갱신

보고서 생성 API는 요청을 작업 폴더의 `queue` 영역에 기록한다. 별도 worker는
`processed` 폴더에 장비별 분석 코드가 생성한 JSON을 읽고 로컬 LLM을 호출해
Markdown 보고서 초안을 만든다.

## 설치 및 실행

Python 3.11 이상이 필요하다.

Edge 서버에는 프로젝트 루트의 다음 세 폴더를 같은 부모 경로 아래에
배포해야 한다.

```text
RIST/
  common/
  config/
  edge_api_server/
```

```bash
cd edge_api_server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

export RIST_ENV=development
python -m app.run
```

현재 구현은 SQLite와 로컬 디스크 큐를 사용하므로 Uvicorn worker는 1개로
실행한다. 다중 worker 또는 여러 서버 인스턴스가 필요해지면 DB와 작업 큐를
공유 서비스로 전환해야 한다.

환경 전환:

```bash
# 개발 환경: http://192.168.0.10:8000
export RIST_ENV=development
python -m app.run

# 운영 환경: http://bhyoon.me:8000
export RIST_ENV=production
python -m app.run
```

공통 프로파일은 각각 다음 파일에 있다.

```text
../config/environments/development.env
../config/environments/production.env
```

API 문서:

- 개발 Swagger UI: `http://192.168.0.10:8000/docs`
- 개발 OpenAPI JSON: `http://192.168.0.10:8000/openapi.json`
- 개발 상태 확인: `http://192.168.0.10:8000/health`

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RIST_ENV` | `development` | `development` 또는 `production` |
| `RIST_CONFIG_DIR` | `config/environments` | 공통 프로파일 파일 경로 |
| `RIST_EDGE_PUBLIC_BASE_URL` | 프로파일 값 | Edge 공개 Base URL 재정의 |
| `RIST_EDGE_BIND_HOST` | 프로파일 값 | Uvicorn bind 주소 재정의 |
| `RIST_EDGE_API_PORT` | 프로파일 값 | Uvicorn 포트 재정의 |
| `RIST_STORAGE_ROOT` | `edge_api_server/data/jobs` | 작업 파일 저장 루트 |
| `RIST_DB_PATH` | `edge_api_server/data/edge_api.db` | SQLite 파일 |
| `RIST_UPLOAD_EXPIRY_HOURS` | `24` | 업로드 유효시간 |
| `RIST_MAX_UPLOAD_BYTES` | `2147483648` | 개별 파일 최대 크기 |
| `RIST_SUPPORTED_EXPERIMENT_CODES` | 빈 값 | 쉼표 구분 허용 실험코드. 빈 값이면 제한 없음 |
| `RIST_LLM_BASE_URL` | `http://127.0.0.1:8001` | OpenAI 호환 로컬 LLM 주소 |
| `RIST_LLM_MODEL` | `local-model` | `/v1/chat/completions` 요청의 model 값 |
| `RIST_LLM_TIMEOUT_SECONDS` | `180` | LLM 요청 제한 시간 |
| `RIST_LLM_TEMPERATURE` | `0.2` | 보고서 작성 temperature |
| `RIST_LLM_MAX_INPUT_CHARS` | `200000` | 구조화 분석 JSON 최대 문자 수 |
| `RIST_WORKER_POLL_SECONDS` | `2` | worker 큐 조회 간격 |

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

## 로컬 LLM 및 보고서 worker

로컬 LLM은 다음 주소에서 OpenAI 호환 API를 제공해야 한다.

```text
http://127.0.0.1:8001/v1/chat/completions
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
{jobRoot}/report/report-draft.md
```

LLM에는 원본 파일을 보내지 않고 `processed` 폴더의 JSON만 전달한다. LLM
완료 후 작업은 `PROCESSING`, 진행률 75%로 유지된다. `PPTX/PDF` 렌더링과
Spring Boot 완료 콜백이 구현된 뒤 `CALLBACK_PENDING`, `COMPLETED`로
전환해야 한다.
