# RIST Edge 서버 배포 (Ubuntu, systemd)

터미널을 띄워둘 필요 없이 **백그라운드 상시 실행**으로 운영하기 위한 배포
자료이다. 부팅 시 자동 시작, 비정상 종료 시 자동 재시작, `journalctl` 로그
관리를 제공한다.

## 구성 서비스

| 서비스 | 역할 | 포트 |
|---|---|---|
| `rist-vllm.service` | 로컬 LLM(vLLM, OpenAI 호환) | `127.0.0.1:8001` |
| `rist-edge-api.service` | FastAPI/Uvicorn API 서버 | `0.0.0.0:8000` |
| `rist-edge-worker.service` | 보고서 생성 worker | - |

## 전제 조건

서버의 다음 경로에 세 폴더가 함께 배포되어 있어야 한다.

```text
/home/rist/ritas/
  .venv/            # 저장소 루트 단일 가상환경(common, edge_api_server 공용)
  common/
  config/
  edge_api_server/
```

`requirements.txt` 가 `-e ../common` 을 사용하므로 `common/` 이 없으면 설치가
실패한다. 가상환경은 저장소 루트(`/home/rist/ritas/.venv`)에 하나만 두고
`common` 과 `edge_api_server` 가 공유한다.

## 1. 코드 배포

### 방법 A — git clone (권장)

서버에서 직접 저장소를 받는다. 저장소 루트 구조가 그대로
`/home/rist/ritas/` 와 일치하므로 추가 정리가 필요 없다.

```bash
# git 미설치 시
sudo apt-get update && sudo apt-get install -y git

# 저장소를 /home/rist/ritas 로 클론
sudo git clone https://github.com/bhyoon1110/ritas.git /home/rist/ritas
```

- 비공개 저장소라면 배포 토큰/SSH 키로 인증한다.
  - HTTPS: `https://<TOKEN>@github.com/bhyoon1110/ritas.git`
  - SSH: `git@github.com:bhyoon1110/ritas.git`
- 특정 브랜치/태그를 받으려면 `--branch <name>` 을 추가한다.

코드 갱신은 `git pull` 로 한다(아래 "5. 코드 업데이트 후 반영" 참고).

### 방법 B — rsync 복사

로컬에서 서버로 직접 복사한다.

```bash
rsync -av --exclude '.venv' --exclude 'data/jobs' --exclude 'data/logs' \
  ./common ./config ./edge_api_server \
  rist-server:/home/rist/ritas/
```

## 2. API 서버 + worker 설치

서버에서 실행:

```bash
cd /home/rist/ritas/edge_api_server
sudo bash deploy/install.sh
```

스크립트가 수행하는 작업:

1. `rist` 전용 계정 생성
2. `python3`(>=3.11) 확인/설치 (Ubuntu 24.04 기본 python3.12 사용 가능)
3. 저장소 루트의 `.venv` 생성 및 `requirements.txt` 설치
4. `data/jobs`, `data/logs` 디렉터리 준비
5. `rist-edge-api`, `rist-edge-worker` 서비스 등록 및 시작
6. 방화벽 8000 포트 개방

## 3. vLLM(로컬 LLM) 서비스

GPU/모델 환경에 의존하므로 별도로 구성한다.

```bash
# vLLM 전용 가상환경
python3.11 -m venv /home/rist/vllm/.venv
/home/rist/vllm/.venv/bin/pip install --upgrade pip
/home/rist/vllm/.venv/bin/pip install vllm

# 모델 배치: /models/gemma-4-E4B-it
```

서비스 등록:

```bash
sudo cp /home/rist/ritas/edge_api_server/deploy/rist-vllm.service \
  /etc/systemd/system/rist-vllm.service
sudo systemctl daemon-reload
sudo systemctl enable --now rist-vllm.service
```

`rist-vllm.service` 안의 가상환경 경로, 모델 경로, `CUDA_VISIBLE_DEVICES`,
`HF_HOME` 는 실제 환경에 맞게 수정한다.

## 4. 운영 명령

```bash
# 상태 확인
systemctl status rist-edge-api.service
systemctl status rist-edge-worker.service
systemctl status rist-vllm.service

# 실시간 로그
journalctl -u rist-edge-api.service -f
journalctl -u rist-edge-worker.service -f

# 재시작 / 중지
sudo systemctl restart rist-edge-api.service
sudo systemctl stop rist-edge-worker.service

# 헬스 체크
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/llm
```

## 5. 코드 업데이트 후 반영

git clone 으로 배포한 경우:

```bash
cd /home/rist/ritas
sudo -u rist git pull
sudo -u rist .venv/bin/pip install -r edge_api_server/requirements.txt   # 의존성 변경 시
sudo systemctl restart rist-edge-api.service rist-edge-worker.service
```

rsync 로 배포한 경우는 다시 복사한 뒤 동일하게 의존성 설치/재시작을 수행한다.

## 주의 사항

- 작업 파일 큐가 로컬 디스크를 사용하므로 **Uvicorn worker 는 1개**로 고정한다.
  여러 서버로 수평 확장하려면 작업 큐를 공유 스토리지/서비스로 전환해야 한다.
- `config/environments/production.env` 의 `EDGE_SERVER_HOST` 등을 실제 엣지
  서버 도메인/주소에 맞게 수정한다.
- 로컬 LLM 은 인증이 없으므로 반드시 `127.0.0.1:8001` 바인딩을 유지하고
  외부에 노출하지 않는다.

## MariaDB (필수)

운영 기본 데이터베이스는 MariaDB 이다. 서비스 시작 전에 MariaDB 가 동작 중이어야
하며, 서비스 파일의 `RIST_DB_*` 값을 실제 접속 정보로 수정한다.

```ini
Environment=RIST_DB_HOST=127.0.0.1
Environment=RIST_DB_PORT=3306
Environment=RIST_DB_NAME=rist_edge
Environment=RIST_DB_USER=rist
Environment=RIST_DB_PASSWORD=change-me
```

- `rist-edge-api.service` 와 `rist-edge-worker.service` 양쪽에 **동일한 접속
  정보**를 설정한다.
- 데이터베이스(`RIST_DB_NAME`)와 테이블은 서버 시작 시 자동 생성되므로 DB
  사용자에게 `CREATE` 권한을 부여한다.

```sql
CREATE USER 'rist'@'%' IDENTIFIED BY 'change-me';
GRANT ALL PRIVILEGES ON rist_edge.* TO 'rist'@'%';
-- DB 자동 생성을 위해 일시적으로 CREATE 권한이 필요하다.
GRANT CREATE ON *.* TO 'rist'@'%';
FLUSH PRIVILEGES;
```

- 변경 후 두 서비스를 재시작한다.

```bash
sudo systemctl restart rist-edge-api.service rist-edge-worker.service
```

## 로그 설정

애플리케이션은 공통 로깅 모듈(`rist_common.get_logger`)을 사용한다. 로그는
항상 **콘솔(stdout/stderr)** 로 출력되며, systemd 환경에서는 자동으로
`journald` 가 수집한다. 추가로 환경 변수를 지정하면 **회전 파일** 로도 함께
기록한다.

### 제어 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RIST_LOG_LEVEL` | `INFO` | `DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL` |
| `RIST_LOG_FORMAT` | `text` | `text` 또는 `json`(구조화 로그) |
| `RIST_LOG_FILE` | 없음 | 로그 파일 경로. 지정 시 회전 파일 핸들러 추가 |
| `RIST_LOG_DIR` | 없음 | 디렉터리만 지정. `<DIR>/rist.log` 로 기록 |
| `RIST_LOG_MAX_BYTES` | `10485760`(10MB) | 회전 파일 한 개의 최대 크기 |
| `RIST_LOG_BACKUP_COUNT` | `5` | 보관할 회전 파일 개수 |

`RIST_LOG_FILE` 과 `RIST_LOG_DIR` 을 모두 지정하면 `RIST_LOG_FILE` 이 우선한다.

### systemd 유닛 설정

두 서비스 파일에는 다음 로그 설정이 기본 포함되어 있다. API 서버는 디렉터리
방식(`data/logs/rist.log`), worker 는 파일 방식(`data/logs/worker.log`)으로
분리해 기록한다.

`rist-edge-api.service`:

```ini
Environment=RIST_LOG_LEVEL=INFO
Environment=RIST_LOG_FORMAT=text
Environment=RIST_LOG_DIR=/home/rist/ritas/edge_api_server/data/logs
```

`rist-edge-worker.service`:

```ini
Environment=RIST_LOG_LEVEL=INFO
Environment=RIST_LOG_FORMAT=text
Environment=RIST_LOG_FILE=/home/rist/ritas/edge_api_server/data/logs/worker.log
```

- 로그 디렉터리(`data/logs`)는 `install.sh` 가 `rist` 계정 소유로 생성한다.
  서비스 유닛에 `ProtectSystem=full` 이 설정되어 있어도 `/home` 하위는 쓰기
  가능하므로 파일 로깅에 문제가 없다.
- 로그 경로를 바꾸려면 유닛 파일의 `RIST_LOG_*` 값을 수정한 뒤 적용한다.

```bash
sudo cp deploy/rist-edge-api.service /etc/systemd/system/rist-edge-api.service
sudo cp deploy/rist-edge-worker.service /etc/systemd/system/rist-edge-worker.service
sudo systemctl daemon-reload
sudo systemctl restart rist-edge-api.service rist-edge-worker.service
```

### 로그 확인

```bash
# 콘솔 로그(journald) 실시간
journalctl -u rist-edge-api.service -f
journalctl -u rist-edge-worker.service -f

# 파일 로그 실시간
tail -f /home/rist/ritas/edge_api_server/data/logs/rist.log
tail -f /home/rist/ritas/edge_api_server/data/logs/worker.log
```

### 디버그 로그 일시 활성화

LLM 요청/응답 등 상세 로그를 보려면 `RIST_LOG_LEVEL=DEBUG` 로 올린다. 유닛
파일을 직접 수정하는 대신 드롭인(override)으로 적용할 수 있다.

```bash
sudo systemctl edit rist-edge-worker.service
# 열린 편집기에 아래 내용 입력 후 저장
# [Service]
# Environment=RIST_LOG_LEVEL=DEBUG

sudo systemctl restart rist-edge-worker.service
```

원래대로 되돌리려면 `sudo systemctl revert rist-edge-worker.service` 후 재시작한다.

