# Environment Configuration

공통 환경은 `RIST_ENV` 값으로 선택한다.

```bash
export RIST_ENV=development
# 또는
export RIST_ENV=production
```

프로파일 파일:

- `environments/development.env`
- `environments/production.env`

systemd 배포에서는 `/home/rist/ritas/edge.env`가 운영체제 환경 변수로
주입된다. 실제 서버마다 달라지는 값은 `edge.env` 한 곳에서 관리한다.

```bash
export RIST_ENV=production
export RIST_LLM_MODEL=gemma4-e4b
```

## 설정 계층

| 계층 | 파일/변수 | 역할 |
|---|---|---|
| 환경 선택 | `RIST_ENV` | `development` 또는 `production` 프로파일 선택 |
| 공유 기본값 | `config/environments/*.env` | git으로 관리하는 Edge scheme/host/port/bind 기본값 |
| 서버별 런타임 설정 | `/home/rist/ritas/edge.env` | DB, 저장소, LLM, Spring Boot, 폰트처럼 서버마다 달라지는 값 |

## 항목별 위치 기준

| 항목 | 기본 위치 | 서버별 변경 위치 | 비고 |
|---|---|---|---|
| Edge 공개 주소 | `EDGE_SERVER_SCHEME/HOST/PORT` | `RIST_EDGE_PUBLIC_BASE_URL` | base URL은 scheme/host/port에서 자동 조합 |
| Edge bind host/port | `EDGE_BIND_HOST`, `EDGE_SERVER_PORT` | `RIST_EDGE_BIND_HOST`, `RIST_EDGE_API_PORT` | systemd 기본은 production 프로파일 사용 |
| 작업 저장소 | 앱 기본값 | `RIST_STORAGE_ROOT` | 기본값은 `<edge_api_server>/data/jobs` |
| 오류 보관소 | 작업 저장소 하위 `errors` | `RIST_ERROR_*` | 로그·스택 트레이스·실패 파일을 `/errors`에서 통합 조회 |
| 보고서 공유 저장소 | `RIST_REPORTS` | `RIST_STORAGE_ROOT`, `RIST_REPORT_STORAGE_KEY` | DB에는 storage key와 ZIP 상대 경로만 등록하고 Spring Boot가 공유 저장소에서 직접 읽는다 |
| LIMS 전송 재시도 | `5` | `RIST_REPORT_TRANSFER_MAX_ATTEMPTS` | Spring Boot가 소비하는 DB 전송 큐의 최대 시도 횟수 |
| LLM 주소/모델 | 앱 기본값 | `RIST_LLM_*` | 기본값은 로컬 vLLM `http://127.0.0.1:8001`, `gemma4-e4b` |
| DB 접속 | 없음 | `RIST_DB_*` | 비밀번호가 포함되므로 `edge.env`에만 둔다 |
| PDF 한글 폰트 | 없음 | `RIST_PDF_FONT_PATH` | 서버 설치 폰트 경로 |

오류 관리 화면은 `http://<Edge 주소>:8000/errors`이다. 기본적으로 오류 기록은
30일간 보관하며, 실패 당시의 업로드 파일도 함께 복사한다. 민감 파일을 보관하지
않으려면 `RIST_ERROR_CAPTURE_FILES=false`로 설정하고 서비스를 재시작한다.

현재 로컬 LLM 기본값은 `http://127.0.0.1:8001`, 모델
`gemma4-e4b`, 컨텍스트 길이 `8192`, 출력 `max_tokens=1200`,
`temperature=0.1`이다. `/v1/models`에서 모델 존재 여부를 확인하고
`processed` 폴더의 분석 이미지를 최대 3개까지 vision 입력으로 사용할 수
있다. 운영 값은 `/home/rist/ritas/edge.env`의 `RIST_LLM_*` 항목에서 관리한다.

기본 설정 디렉터리는 프로젝트 루트의 `config/environments`이다. 배포 위치가
다르면 `RIST_CONFIG_DIR`에 프로파일 파일이 있는 디렉터리를 지정한다.

```bash
export RIST_CONFIG_DIR=/opt/rist/config/environments
```
