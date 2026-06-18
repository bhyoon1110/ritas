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
  common/
  config/
  edge_api_server/
```

`requirements.txt` 가 `-e ../common` 을 사용하므로 `common/` 이 없으면 설치가
실패한다.

## 1. 코드 배포

로컬에서 서버로 복사(예시):

```bash
rsync -av --exclude '.venv' --exclude 'data/jobs' \
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
2. `python3.11` 확인/설치
3. `.venv` 생성 및 `requirements.txt` 설치
4. `data/jobs` 디렉터리 준비
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

```bash
cd /home/rist/ritas/edge_api_server
sudo -u rist .venv/bin/pip install -r requirements.txt   # 의존성 변경 시
sudo systemctl restart rist-edge-api.service rist-edge-worker.service
```

## 주의 사항

- SQLite + 로컬 디스크 큐 구조이므로 **Uvicorn worker 는 1개**로 고정한다.
  여러 인스턴스가 필요하면 DB/큐를 공유 서비스로 전환해야 한다.
- `config/environments/production.env` 의 `EDGE_SERVER_HOST` 등을 실제 엣지
  서버 도메인/주소에 맞게 수정한다.
- 로컬 LLM 은 인증이 없으므로 반드시 `127.0.0.1:8001` 바인딩을 유지하고
  외부에 노출하지 않는다.
