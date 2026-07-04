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

운영체제 환경 변수는 프로파일 파일보다 우선한다. systemd 배포에서는
`/home/rist/ritas/edge.env`가 운영체제 환경 변수로 주입되므로, 실제 서버마다
달라지는 값은 이 파일에서 재정의한다.

```bash
export RIST_ENV=production
export RIST_LLM_MODEL=gemma4-e4b
```

## 설정 계층

| 계층 | 파일/변수 | 역할 |
|---|---|---|
| 환경 선택 | `RIST_ENV` | `development` 또는 `production` 프로파일 선택 |
| 공유 기본값 | `config/environments/*.env` | git으로 관리하는 개발/운영 기본 토폴로지 |
| 서버별 override | `/home/rist/ritas/edge.env` | DB, LLM 주소, Spring Boot 주소, 폰트처럼 서버마다 달라지는 값 |

## 항목별 위치 기준

| 항목 | 기본 위치 | 서버별 변경 위치 | 비고 |
|---|---|---|---|
| Edge 공개 주소 | `EDGE_SERVER_BASE_URL` | `RIST_EDGE_PUBLIC_BASE_URL` | 개발은 `bhyoon.me`, 운영은 `192.168.0.10` 기본값 |
| Edge bind host/port | `EDGE_BIND_HOST`, `EDGE_SERVER_PORT` | `RIST_EDGE_BIND_HOST`, `RIST_EDGE_API_PORT` | systemd 기본은 production 프로파일 사용 |
| 작업 저장소 | `EDGE_STORAGE_ROOT` | `RIST_STORAGE_ROOT` | 운영 디스크 경로가 다르면 `RIST_STORAGE_ROOT` 사용 |
| Spring Boot 기본 주소 | `LOCAL_SPRING_BOOT_BASE_URL` | `LOCAL_SPRING_BOOT_BASE_URL` | 같은 키를 `edge.env`에 쓰면 프로파일보다 우선 |
| Spring Boot 전체 수신 URL | 없음 | `RIST_SPRING_CALLBACK_URL` | 기본 path와 다를 때만 사용. 설정 시 `LOCAL_SPRING_BOOT_BASE_URL`보다 우선 |
| LLM 주소/모델 | `LOCAL_LLM_*` | `RIST_LLM_*` | 운영 서버의 vLLM 주소/모델이 다르면 `RIST_LLM_*` 사용 |
| DB 접속 | 없음 | `RIST_DB_*` | 비밀번호가 포함되므로 `edge.env`에만 둔다 |
| PDF 한글 폰트 | 없음 | `RIST_PDF_FONT_PATH` | 서버 설치 폰트 경로 |

현재 로컬 LLM 기본값은 `http://127.0.0.1:8001`, 모델
`gemma4-e4b`, 컨텍스트 길이 `8192`, 출력 `max_tokens=1200`,
`temperature=0.1`이다. `/v1/models`에서 모델 존재 여부를 확인하고
`processed` 폴더의 분석 이미지를 최대 3개까지 vision 입력으로 사용할 수
있다. 공유 기본값은 각 프로파일의 `LOCAL_LLM_*` 항목에서 관리하고,
서버별 override는 `/home/rist/ritas/edge.env`의 `RIST_LLM_*` 항목을 사용한다.

기본 설정 디렉터리는 프로젝트 루트의 `config/environments`이다. 배포 위치가
다르면 `RIST_CONFIG_DIR`에 프로파일 파일이 있는 디렉터리를 지정한다.

```bash
export RIST_CONFIG_DIR=/opt/rist/config/environments
```
