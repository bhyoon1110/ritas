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
- 로컬 LLM 모델 조회 및 설정 모델 검증(`/v1/models`)
- LLM 컨텍스트 길이 사전 검증과 vLLM 오류 변환
- 전처리 JSON 및 선택적 분석 이미지 입력
- 작업 상태 조회
- MariaDB 기반 작업, 파일, 멱등 요청 저장
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
- 로컬 LLM 상태 확인: `http://192.168.0.10:8000/health/llm`

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
| `RIST_WORKER_POLL_SECONDS` | `2` | worker 큐 조회 간격 |

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
  자동 생성하고 필요한 테이블을 만든다. 따라서 DB 사용자에게 `CREATE`
  권한이 있어야 한다.
- 드라이버는 순수 파이썬 `PyMySQL`을 사용하므로 시스템 라이브러리 설치가
  필요 없다.
- 작업 파일 큐는 로컬 디스크를 사용한다. 여러 서버로 수평 확장하려면 공유
  스토리지가 필요하다.

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
{jobRoot}/report/report-draft.md
```

LLM에는 원본 bundle을 보내지 않고 `processed` 폴더의 JSON과 허용된 분석
이미지만 전달한다. 요청 로그에는 이미지의 base64 본문을 기록하지 않는다.
FT-IR 작업은 단정적 해석을 피하고 규칙 기반 결과, 라이브러리 매칭, QC 및
전체 스펙트럼의 한계를 구분하는 전용 프롬프트를 사용한다.

설정된 모델의 컨텍스트 길이 8,192 안에서 입력, 이미지 예약량, 출력
`max_tokens`, 안전 여유를 계산한다. 초과가 예상되면 LLM을 호출하기 전에
`LLM_CONTEXT_BUDGET_EXCEEDED`로 실패시켜 vLLM의 길이 초과 오류를 방지한다.

LLM 완료 후 작업은 `PROCESSING`, 진행률 75%로 유지된다. `PPTX/PDF` 렌더링과
Spring Boot 완료 콜백이 구현된 뒤 `CALLBACK_PENDING`, `COMPLETED`로
전환해야 한다.
