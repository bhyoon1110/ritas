# RIST Edge 서버 배포 (Ubuntu, systemd)

터미널을 띄워둘 필요 없이 **백그라운드 상시 실행**으로 운영하기 위한 배포
자료이다. 부팅 시 자동 시작, 비정상 종료 시 자동 재시작, `journalctl` 로그
관리를 제공한다.

## 구성 서비스

| 서비스 | 역할 | 포트 |
|---|---|---|
| `rist-vllm.service` (docker compose) | 로컬 LLM(vLLM, OpenAI 호환) 스택 관리 | `127.0.0.1:8001` |
| `rist-edge-api.service` | FastAPI/Uvicorn API 서버 | `0.0.0.0:8000` |
| `rist-edge-worker.service` | 보고서 생성 worker | - |

`rist-edge-worker.service` 는 `rist-vllm.service` 를 `Wants/After` 로 참조하므로,
worker 를 start 하면 vLLM compose 스택도 함께 올라온다(선택 의존이라 vLLM 이 떠
있지 않아도 worker 는 기동하며, 이 경우 보조 문안만 규칙 기본값으로 대체된다).

## 전제 조건

서버의 다음 경로에 분석 프로젝트와 Edge 폴더가 함께 배포되어 있어야 한다.

```text
/home/rist/ritas/
  .venv/            # 저장소 루트 단일 가상환경(common, edge_api_server 공용)
  common/
  config/
  sune/
  rin/
  lim/
  ahn/
  edge_api_server/
```

`requirements.txt` 가 `-e ../common` 을 사용하므로 `common/` 이 없으면 설치가
실패한다. 가상환경은 저장소 루트(`/home/rist/ritas/.venv`)에 하나만 두고
`common` 과 `edge_api_server` 가 공유한다.

### 보고서 전송 큐 DB 생성

최초 설치처럼 기존 큐 데이터를 보존하면서 없는 테이블만 생성할 때는 일반 DDL을
실행한다.

```bash
cd /home/rist/ritas/edge_api_server/deploy
mysql --default-character-set=utf8mb4 \
  -h "$RIST_DB_HOST" -P "$RIST_DB_PORT" \
  -u "$RIST_DB_USER" -p "$RIST_DB_NAME" \
  < mariadb_report_queue.sql
```

개발 DB의 기존 보고서/전송 큐 이력을 모두 삭제하고, 최신 컬럼 및 테이블 COMMENT가
포함된 구조로 새로 만들 때만 재생성 전용 DDL을 실행한다. 이 스크립트는
`report_transfer_attempts`, `report_transfers`, `report_artifacts`,
`report_retention_policies`, `report_runs`를 자식부터 순서대로 삭제하며 기존
`jobs` 테이블은 보존한다.

```bash
cd /home/rist/ritas/edge_api_server/deploy
mysql --default-character-set=utf8mb4 \
  -h "$RIST_DB_HOST" -P "$RIST_DB_PORT" \
  -u "$RIST_DB_USER" -p "$RIST_DB_NAME" \
  < mariadb_report_queue_recreate.sql
```

운영 DB에서는 재생성 DDL을 실행하지 말고 별도 `ALTER TABLE` migration을 적용한다.
재생성 전에 필요한 큐 이력을 반드시 백업한다.

### 폐쇄망 HTTPS 자동 설정

기존 Edge 설치 후 [`setup_https.sh`](setup_https.sh)로 Nginx HTTPS 앞단을 추가할 수
있다. 기본 주소는 `https://10.10.1.31`, Python은 `127.0.0.1:8000`에만 바인딩한다.
**API 재시작과 실험 PC/C# 접속 주소 변경이 필요하므로 진행 중인 업로드가 없는
유지보수 시간에 적용한다.** worker, Spring, DB, LLM은 재시작하지 않는다.

스크립트는 인터넷 접속, 패키지 다운로드, 방화벽 변경을 하지 않는다. Ubuntu에
`nginx`, `openssl` 1.1.1 이상, `curl`, `iproute2`(`ss`), `util-linux`(`flock`),
systemd가 있어야 한다. 없으면 동일 Ubuntu 버전/CPU 아키텍처의 의존 패키지를
사내 패키지 서버 또는 USB로 먼저 설치한다. 443이 다른 Spring/Nginx 서비스에
이미 사용되면 기존 서비스를 변경하지 않고 중단한다. 필요한 경우
`--https-port 8443`을 지정하고 클라이언트 주소·방화벽도 그 포트로 맞춘다.

#### A. 사내 인증서 사용 (권장)

```bash
cd /home/rist/ritas

# 기본 동작은 계획만 출력하며 파일/서비스를 변경하지 않는다.
bash edge_api_server/deploy/setup_https.sh plan

# Edge에서 키/CSR 생성. 키는 서버 밖으로 전달하지 않는다.
sudo bash edge_api_server/deploy/setup_https.sh csr --host 10.10.1.31
```

`/etc/rist/tls/edge.csr`만 사내 발급기관에 전달한다. 서버 인증서는 **SAN에
`IP:10.10.1.31`**, 서버 인증 용도를 포함해야 한다. DNS 이름을 사용할 때는
`--host`에 그 이름을 지정하고 DNS SAN으로 발급한다. 서버 인증서부터 중간 인증서
순서의 PEM 체인과 **루트 CA 인증서 한 개의 PEM 파일**을 받는다. CA의 폐기 확인
정책(CRL/OCSP)도 폐쇄망에서 충족 가능한지 확인한다.

```bash
# 발급받은 공개 인증서만 USB에서 반입한다. 아래 파일명은 예시다.
sudo bash edge_api_server/deploy/setup_https.sh apply \
  --host 10.10.1.31 \
  --cert /media/usb/edge-chain.pem \
  --ca-cert /media/usb/root-ca.crt

# 설치된 HTTPS/프록시를 다시 검사한다.
sudo bash edge_api_server/deploy/setup_https.sh check --host 10.10.1.31
```

기존 키를 이용한다면 `csr`를 실행하지 않고 `apply --key /절대경로/edge.key`를
지정한다. 갱신 시에는 새 디렉터리에서 CSR을 만들고 새 키/체인으로 `apply`한다.
`init-ca`, `csr`, `sign`은 기존 키/CSR/발급 인증서를 덮어쓰지 않는다.

#### B. 사내 CA가 없는 경우: 별도 Linux 관리 PC에서 내부 CA 발급

보안 담당자의 승인을 받은 내부 CA만 사용한다. **CA 개인키를 Edge나 Git, 실험실
PC에 복사하지 않는다.** 아래 CA 작업은 별도의 Linux 관리 PC에서 실행한다.
명령은 OpenSSL이 CA 암호를 직접 질문한다. 비대화형 발급은 root/관리자만 읽는
권한 `600` 또는 `400` 파일을 `--ca-pass-file /절대경로/암호파일`로 지정한다.
암호/개인키를 명령행 인수 값, 채팅, Git에 넣지 않는다.

```bash
# 별도 관리 PC: 암호화된 CA 개인키 + 10년 루트 인증서 (최초 한 번)
sudo bash setup_https.sh init-ca --ca-dir /opt/rist-offline-ca

# Edge에서 A의 csr 명령 실행 후, 공개 CSR만 관리 PC로 옮긴다.
# 관리 PC: 서버 인증서 발급 (기본 365일, SAN/서버 용도 포함)
sudo bash setup_https.sh sign \
  --ca-dir /opt/rist-offline-ca \
  --host 10.10.1.31 \
  --csr /media/usb/edge.csr \
  --out /var/tmp/rist-edge.crt
```

발급된 `rist-edge.crt`와 `/opt/rist-offline-ca/root-ca.crt` 공개 파일만 Edge로
옮겨 A의 `apply --cert ... --ca-cert ...`를 실행한다. 생성된 CA 개인키는
암호화되며 발급 임시 디렉터리도 권한 700이다. CA/발급 이력은 관리 PC에서
보안 정책에 따라 백업·보관한다. 자동 갱신 스케줄러는 포함하지 않는다.

#### 자동 처리와 복원

- 인증서 만료까지 30일 이상, SAN 주소, 서버 인증 용도, CA 체인, 키 일치를 검사한다.
- `edge.env`를 실행하지 않고 HTTPS/보안 쿠키/로그인/바인드 주소/포트/프록시 신뢰
  6개 키만 갱신한다. 기존 DB 비밀번호, SSO URL/SID/CA, 저장소, LLM 값과 파일
  소유자·권한은 보존한다. 백업 내용을 로그에 출력하지 않는다.
- Nginx 설정은 별도 관리 파일에만 기록한다. 기본 업로드 요청 한도 2100MiB,
  본문/프록시 대기 900초, 요청 버퍼링 해제로 기존 청크 전송을 유지한다.
  앱 자체의 업로드·압축 해제 한도는 변경하지 않는다.
- 신뢰할 프록시는 `127.0.0.1`만 허용한다. 사용자 제공 전달 헤더는 Nginx에서
  덮어쓴다. 공개 프록시의 보고서 재생성 제어 API는 403으로 차단하며 같은 호스트의
  Spring은 내부 `127.0.0.1:8000`을 사용한다. 이 차단은 앱 자체의 M2M 인증 구현을
  대신하지 않는다.
- 설정 문법뿐 아니라 실제 HTTPS `/health`, 내부 API의 loopback 바인드,
  인증정보 없는 SSO 요청의 HTTPS 인식(401/403/422, **426은 실패**)을 검사한다.
  실패하면 이전 파일과 Nginx 실행·활성화 상태를 자동 복원한다.
- `/var/backups/rist-https/backup.*`에는 **DB 비밀번호·서버 키**도 포함된다.
  root 소유/권한 700으로 보관하며 공개 인증서 배포 폴더에 복사하지 않는다.
  명시적 rollback 직전 상태도 추가 백업하므로 복원 작업 자체도 되돌릴 수 있다.

```bash
# apply 출력에 표시된 정확한 백업 디렉터리로 복원한다.
sudo bash edge_api_server/deploy/setup_https.sh rollback \
  --backup /var/backups/rist-https/backup.XXXXXXXX
```

최초 설치 복원에서는 새로 만든 관리 파일만 제거하고 기존 파일을 돌려놓는다.
발급 원본 및 적용/복원 백업은 보존한다. 심볼릭 링크 경로, Git 내부 개인키/백업,
다른 Nginx 설정 파일, 충돌하는 포트에는 자동 적용하지 않는다.
TLS/CA/백업 디렉터리가 이미 있으면 실행 사용자 소유·권한 700이어야 한다.
공용 디렉터리를 자동 chmod하지 않으며 `/etc/ssl` 같은 시스템 PKI 루트는 대상으로
허용하지 않는다. 전용 디렉터리 경로를 사용한다.

#### 별도로 필요한 현장 작업

1. 실험 PC에서 Edge의 TCP 443(또는 지정 포트)으로 통신을 허용한다. 스크립트는
   UFW/망 방화벽 규칙을 변경하지 않는다.
2. `/etc/rist/tls/client-ca.cer` **공개 루트 인증서만** 실험실 PC로 배포한다.
   발급기관/Edge 출력의 SHA-256 지문을 대조한 뒤 Windows의 신뢰할 수 있는 루트
   인증기관 저장소에 등록한다. 중간 CA가 필요하면 중간 인증기관 저장소에 등록한다.
   서버 개인키·CA 개인키·`edge.env`·백업은 배포하지 않는다.
3. Supermium 북마크와 C# API Base URL을 `https://10.10.1.31`로 바꾼다.
   8000의 외부 접속은 더 이상 허용되지 않는다. C#의 TLS/인증서 신뢰도 별도 확인한다.
4. 실제 XP/Supermium에서 경고 없는 TLS 접속, 로그인/가입, 실제 SSO, 네 실험의
   업로드/보고서/다운로드를 검증한다. OS/PC 시간도 확인한다. 인증서 검증을 끄거나
   TLS 1.0/1.1로 낮추지 않는다.
5. Edge→SSO TCP 443, Edge→LIMS 지정 포트, SID/출발지 IP 등록과 실제 LIMS 전송은
   별도 작업이다. 스크립트 성공은 실제 SSO/LIMS 연동 성공을 의미하지 않는다.

개발 검증은 운영 서버가 아닌 일회용 Ubuntu 컨테이너에서
`python3 -m pytest edge_api_server/deploy/tests/test_setup_https.py`로 수행한다.
테스트는 root 권한과 `openssl`, `nginx`, `curl`, `iproute2`, `util-linux`, pytest가
필요하며 임시 경로/테스트 서비스 대역만 사용한다. 운영 Edge에서 테스트하지 않는다.

인터넷이 있는 개발 PC에서는 다음과 같이 재현할 수 있다. 패키지 다운로드는 **테스트
이미지 빌드에서만** 일어나며 운영 설치 스크립트는 다운로드하지 않는다.

```bash
docker build -t rist-https-tests edge_api_server/deploy/tests
docker run --rm --mount type=bind,source="$PWD",target=/src,readonly rist-https-tests
```

기존 `report_runs`와 전송 이력을 유지하면서 보고서 파일 관리 컬럼,
`report_artifacts`, `report_retention_policies`를 추가하려면 다음 비파괴
마이그레이션을 실행한다. 이미 `report_artifacts`가 있어도 반복 실행할 수 있다.

```bash
cd /home/rist/ritas/edge_api_server/deploy
mysql --default-character-set=utf8mb4 \
  -h "$RIST_DB_HOST" -P "$RIST_DB_PORT" \
  -u "$RIST_DB_USER" -p "$RIST_DB_NAME" \
  < mariadb_report_artifacts_migration.sql
```

### VOC 게시판 DB 추가

기존 인증 스키마(`app_users`)가 있는 Edge DB에 `voc_requests`, `voc_events`를 추가한다.
일반적인 앱 재시작에서는 자동 생성된다. DB 변경을 별도로 관리하는 환경은 다음
비파괴 SQL을 먼저 적용한다. 반복 실행할 수 있고 기존 회원·실험·보고서 데이터는 보존한다.

```bash
cd /home/rist/ritas/edge_api_server/deploy
mysql --default-character-set=utf8mb4 \
  -h "$RIST_DB_HOST" -P "$RIST_DB_PORT" \
  -u "$RIST_DB_USER" -p "$RIST_DB_NAME" \
  < mariadb_voc_migration.sql
```

새 코드 배포와 Edge API 재시작 후 동일 실험 회원 간 조회, 다른 실험 접근 차단,
관리자 조치 완료, 작성자 본인 확인을 점검한다. `RIST_AUTH_ENABLED=true`가 필요하며
VOC만을 위한 SSO 인증이나 새 환경변수는 없다. DB 백업에는 두 VOC 테이블도 포함한다.

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
  ./common ./config ./sune ./rin ./lim ./ahn ./edge_api_server ./edge.env.example \
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
5. `rist-edge-api`, `rist-edge-worker`, `rist-vllm` 서비스 등록 및 시작
   (worker 가 `rist-vllm.service` 를 끌어오므로 vLLM compose 스택도 함께 기동)
6. 방화벽 8000 포트 개방

## 3. vLLM(로컬 LLM) — rist-vllm.service(docker compose)

로컬 LLM 은 **docker compose 컨테이너**로 구동하되, `rist-vllm.service` systemd
유닛이 이를 관리한다(`up -d`/`down`). worker 가 이 유닛을 `Wants/After` 로
참조하므로 `systemctl start rist-edge-worker.service` 시 함께 올라온다.
GPU 드라이버 + NVIDIA Container Toolkit 가 설치되어 있어야 한다.
컴포즈 정의는 [`deploy/docker-compose.vllm.yml`](docker-compose.vllm.yml) 에 있다.

운영 서버에서 먼저 다음 항목을 확인한다.

```bash
uname -m
which docker
docker compose version
nvidia-smi
test -d /data/models/gemma-4-E4B-it
```

- `docker-compose.vllm.yml` 의 이미지 태그는 서버 CPU 아키텍처와 CUDA/Ubuntu
  조합에 맞아야 한다. 현재 기본값은 ARM64 + CUDA 12.9 + Ubuntu 24.04 계열이다.
- Docker CLI 경로가 `/usr/bin/docker`가 아니면 `rist-vllm.service`의
  `ExecStart`/`ExecStop` 경로를 서버 환경에 맞게 수정한다.
- 모델 디렉터리(`/data/models/gemma-4-E4B-it`)가 다르면 compose의 volume과
  `--model` 경로를 함께 수정한다.

```bash
# 모델 배치: /data/models/gemma-4-E4B-it

# install.sh 가 유닛을 등록하므로 보통은 worker 기동만으로 함께 올라온다.
# 수동으로 vLLM 만 올리거나 내릴 때:
sudo systemctl start rist-vllm.service
sudo systemctl stop rist-vllm.service

# (systemd 없이 직접 compose 로 띄울 수도 있다)
cd /home/rist/ritas/edge_api_server/deploy
sudo docker compose -f docker-compose.vllm.yml up -d
```

- 호스트 `127.0.0.1:8001` → 컨테이너 `8000` 으로 매핑되며, 엣지 API/worker 는
  이 엔드포인트(`RIST_LLM_BASE_URL=http://127.0.0.1:8001`, 기본값)로 접속한다.
- 모델명은 `gemma4-e4b`(`RIST_LLM_MODEL` 기본값)로 제공된다.
- 외부 노출 금지: 컴포즈의 포트 매핑을 반드시 `127.0.0.1:8001:8000` 으로 유지한다.

## 4. 운영 명령

```bash
# 상태 확인
systemctl status rist-edge-api.service
systemctl status rist-edge-worker.service
systemctl status rist-vllm.service
sudo docker compose -f deploy/docker-compose.vllm.yml ps

# 실시간 로그
journalctl -u rist-edge-api.service -f
journalctl -u rist-edge-worker.service -f

# 프로젝트 통합 운영 관리 화면 (사용 기록 | 오류 기록 | 보고서/파일 관리)
# http://<Edge 서버 주소>:8000/operations
# http://<Edge 서버 주소>:8000/report-management
# 보고서/파일 관리의 `보존 정책 설정`에서 미전송 테스트, 실패·취소,
# LIMS 전송 완료, 휴지통 보존 기간과 자동 정리 여부를 변경할 수 있다.
# 저장값은 DB에 즉시 반영되며 이후 조회/정리부터 적용되므로 서비스 재시작은 필요 없다.
sudo docker logs -f vllm-gemma4-e4b

# 보고서 보존 정책 자동 정리 상태 및 수동 실행
systemctl status rist-report-cleanup.timer
sudo systemctl start rist-report-cleanup.service
journalctl -u rist-report-cleanup.service -n 100 --no-pager

# 보존 정책 API 확인
curl http://127.0.0.1:8000/api/v1/report-management/policies

# 재시작 / 중지
sudo systemctl restart rist-edge-api.service
sudo systemctl stop rist-edge-worker.service
sudo systemctl restart rist-vllm.service   # vLLM 스택만 재기동

# 헬스 체크
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/health/llm
```

## 5. 코드 업데이트 후 반영

git clone 으로 배포한 경우:

```bash
cd /home/rist/ritas
sudo -u rist git pull
cd edge_api_server
sudo -u rist ../.venv/bin/pip install -r requirements.txt   # 의존성 변경 시
sudo systemctl restart rist-edge-api.service rist-edge-worker.service
```

TEM/AHN 보고서 기능이 `python-pptx`, `pytesseract`, `opencv-python-headless`를
사용하므로, `TEM_REPORT_BUILD_FAILED`와 함께 `python-pptx 패키지가 필요합니다`
오류가 나오면 같은 가상환경에 의존성을 다시 설치한다.

```bash
cd /home/rist/ritas/edge_api_server
sudo -u rist ../.venv/bin/pip install -r requirements.txt
sudo -u rist ../.venv/bin/python -c "import pptx, pytesseract, cv2; print('AHN deps OK')"
sudo systemctl restart rist-edge-api.service
```

XRD 보고서의 PDF Export는 서버에서 Chrome/Chromium headless 렌더러로 PDF를 만든다.
`서버 PDF 생성에 실패했습니다` 또는 `XRD_PDF_RENDERER_NOT_AVAILABLE` 오류가
나오면 Chromium 실행 파일을 설치하고 `/home/rist/ritas/edge.env`에 경로를 지정한다.

```bash
# 설치 여부 확인
which chromium || which chromium-browser || which google-chrome || which google-chrome-stable || true
ls -l /snap/bin/chromium /usr/bin/chromium /usr/bin/chromium-browser 2>/dev/null || true

# Ubuntu snap chromium 예시
sudo snap install chromium

# systemd 환경에서 확실히 잡히도록 edge.env에 실제 경로 지정
printf '\nRIST_PDF_CHROME_BIN=/snap/bin/chromium\n' | sudo tee -a /home/rist/ritas/edge.env
sudo -u rist mkdir -p /home/rist/ritas/edge_api_server/data/pdf_renders
printf '\nRIST_XRD_PDF_RENDER_DIR=/home/rist/ritas/edge_api_server/data/pdf_renders\n' | sudo tee -a /home/rist/ritas/edge.env
sudo systemctl restart rist-edge-api.service

# 반영 확인
journalctl -u rist-edge-api.service -n 80 --no-pager
```

Chrome이 `exit=0`으로 종료됐는데 PDF 파일을 만들지 못했다면 snap Chromium이
systemd의 `/tmp` 네임스페이스를 제대로 쓰지 못한 경우가 많다. 이때는
`RIST_XRD_PDF_RENDER_DIR`을 `/home/rist/...` 하위처럼 snap이 접근 가능한 경로로
지정하고 해당 디렉터리를 `rist` 계정이 쓸 수 있게 만든다.

Snap Chromium을 사용할 때 `snap-confine is packaged without necessary permissions`
또는 `cap_dac_override not found` 오류가 나오면 `rist-edge-api.service`의
`NoNewPrivileges` 제한 때문에 snap-confine이 필요한 capability를 얻지 못한 것이다.
업데이트된 서비스 유닛을 systemd에 다시 설치한 뒤 재시작한다.

```bash
cd /home/rist/ritas
git pull
sudo install -m 644 edge_api_server/deploy/rist-edge-api.service /etc/systemd/system/rist-edge-api.service
sudo systemctl daemon-reload
sudo systemctl restart rist-edge-api.service
systemctl show rist-edge-api.service -p NoNewPrivileges
```

rsync 로 배포한 경우는 다시 복사한 뒤 동일하게 의존성 설치/재시작을 수행한다.

### git pull 충돌 해결

서버에서 추적 파일(특히 `deploy/*.service`)을 직접 수정했다면 `git pull` 이
충돌하거나 거부된다. 비밀값은 `/home/rist/ritas/edge.env` 로 분리되어 있으므로 서버의
추적 파일 수정분은 버리고 원격을 따르면 된다.

```bash
cd /home/rist/ritas

# 1) 서버 로컬 수정 내용 확인
git status

# 2) 추적 파일의 로컬 수정 되돌리기(비밀값은 edge.env 에 있으니 안전)
git checkout -- edge_api_server/deploy/rist-edge-api.service \
                edge_api_server/deploy/rist-edge-worker.service \
                edge_api_server/deploy/rist-vllm.service
#   또는 전체 되돌리기: git restore .

# 3) 원격 반영
sudo -u rist git pull

# 4) 유닛이 갱신되었으면 다시 설치/적용
cd edge_api_server
sudo install -m 644 deploy/rist-edge-api.service /etc/systemd/system/rist-edge-api.service
sudo install -m 644 deploy/rist-edge-worker.service /etc/systemd/system/rist-edge-worker.service
sudo install -m 644 deploy/rist-vllm.service /etc/systemd/system/rist-vllm.service
sudo systemctl daemon-reload
sudo systemctl restart rist-edge-api.service rist-edge-worker.service rist-vllm.service
```

> 앞으로는 서버에서 추적 파일을 편집하지 않는다. 환경/비밀값 변경은
> `/home/rist/ritas/edge.env`, 로그/레벨 변경은 `systemctl edit` 드롭인을 사용하면
> `git pull` 이 항상 깨끗하게 동작한다.

## 주의 사항

- 작업 파일 큐가 로컬 디스크를 사용하므로 **Uvicorn worker 는 1개**로 고정한다.
  여러 서버로 수평 확장하려면 작업 큐를 공유 스토리지/서비스로 전환해야 한다.
- 실제 엣지 서버 공개 주소가 기본 production 프로파일과 다르면
  `/home/rist/ritas/edge.env` 에 `RIST_EDGE_PUBLIC_BASE_URL`을 지정한다.
- 로컬 LLM 은 인증이 없으므로 반드시 docker-compose 의 `127.0.0.1:8001:8000`
  포트 매핑을 유지해 호스트 루프백에만 바인딩하고 외부에 노출하지 않는다.

## MariaDB (필수)

운영 기본 데이터베이스는 MariaDB 이다. 서비스 시작 전에 MariaDB 가 동작 중이어야
한다.

> **중요 — 비밀값은 서비스 파일에 넣지 않는다.**
> DB 비밀번호 등 접속 정보는 git 추적 밖의 `/home/rist/ritas/edge.env`
> (프로젝트 루트, `.gitignore` 등록)에서 읽는다. 서비스 파일
> (`rist-edge-*.service`)은 이 파일을 `EnvironmentFile=` 로 참조만 하므로,
> dev PC 에서 코드를 수정해 push 해도 `git pull` 충돌이 발생하지 않고
> 비밀번호가 git 히스토리에 남지 않는다.

`install.sh` 가 `/home/rist/ritas/edge.env` 를 생성한다(이미 있으면 보존).
실제 접속 정보로 수정한다.

```bash
sudo nano /home/rist/ritas/edge.env
```

```ini
# Edge API 공개 주소/저장소 override
# RIST_EDGE_PUBLIC_BASE_URL=http://192.168.0.10:8000
RIST_STORAGE_ROOT=/home/rist/ritas/edge_api_server/data/jobs

RIST_DB_HOST=127.0.0.1
RIST_DB_PORT=3306
RIST_DB_NAME=rist_edge
RIST_DB_USER=rist
RIST_DB_PASSWORD=실제비밀번호
RIST_DB_POOL_SIZE=8
RIST_DB_POOL_TIMEOUT_SECONDS=10

# 공유 저장소 / DB 보고서 전송 큐
RIST_REPORT_STORAGE_KEY=RIST_REPORTS
RIST_REPORT_TRANSFER_MAX_ATTEMPTS=5
# 아래 값은 report_retention_policies 행이 없을 때 사용하는 최초 생성 기본값이다.
RIST_REPORT_TEST_RETENTION_DAYS=7
RIST_REPORT_FAILED_RETENTION_DAYS=30
RIST_REPORT_COMPLETED_RETENTION_DAYS=90
RIST_REPORT_TRASH_RETENTION_DAYS=7

# 로컬 LLM
RIST_LLM_BASE_URL=http://127.0.0.1:8001
RIST_LLM_MODEL=gemma4-e4b
RIST_LLM_TIMEOUT_SECONDS=180
RIST_LLM_TEMPERATURE=0.1
RIST_LLM_MAX_TOKENS=1200
RIST_LLM_CONTEXT_WINDOW=8192
RIST_LLM_CONTEXT_MARGIN=256
RIST_LLM_VALIDATE_MODEL=true
RIST_LLM_INCLUDE_IMAGES=true
RIST_LLM_MAX_IMAGES=3
RIST_LLM_MAX_IMAGE_BYTES=2097152
RIST_LLM_MAX_INPUT_CHARS=200000

# PDF 한글 깨짐 방지를 위해 한글 TTF/OTF/TTC를 지정한다.
# 운영 서버는 fonts-nanum 설치 후 NanumGothic.ttf 명시 지정을 권장한다.
RIST_PDF_FONT_PATH=/usr/share/fonts/truetype/nanum/NanumGothic.ttf
```

- API 서비스와 worker 가 동일한 `/home/rist/ritas/edge.env` 를 공유하므로
  **한 곳만** 수정하면 된다.
- Spring Boot/LLM 주소나 timeout/retry 값을 바꾼 뒤에는 API와 worker를
  재시작하면 반영된다.
- 파일 권한은 `640`(rist 소유)으로 두어 다른 사용자가 읽지 못하게 한다.
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
