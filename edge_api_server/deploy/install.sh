#!/usr/bin/env bash
#
# RIST Edge 서버 배포 스크립트 (Ubuntu)
# - rist 전용 계정 생성
# - Python 3.11 가상환경 구성 및 의존성 설치
# - systemd 서비스 등록(rist-edge-api, rist-edge-worker)
#
# 사용법:  sudo bash deploy/install.sh
#
# 주의: vLLM(rist-vllm.service)은 GPU/모델 환경에 따라 별도로 구성한다.
#       이 스크립트는 API 서버와 worker만 설정한다.

set -euo pipefail

PROJECT_ROOT="/home/rist/ritas"
SERVICE_USER="rist"
EDGE_DIR="${PROJECT_ROOT}/edge_api_server"
VENV_DIR="${EDGE_DIR}/.venv"

if [[ $EUID -ne 0 ]]; then
    echo "이 스크립트는 root 권한으로 실행해야 합니다: sudo bash deploy/install.sh" >&2
    exit 1
fi

echo "==> 1. 전용 계정(${SERVICE_USER}) 확인/생성"
if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
    echo "    계정 생성 완료"
else
    echo "    이미 존재함"
fi

echo "==> 2. Python 3.11 확인"
if ! command -v python3.11 &>/dev/null; then
    echo "    python3.11 이 없습니다. 설치를 진행합니다."
    apt-get update
    apt-get install -y python3.11 python3.11-venv
fi

echo "==> 3. 프로젝트 디렉터리 소유권 정리"
if [[ ! -d "${PROJECT_ROOT}/common" || ! -d "${PROJECT_ROOT}/config" || ! -d "${EDGE_DIR}" ]]; then
    echo "    오류: ${PROJECT_ROOT} 아래에 common/ config/ edge_api_server/ 세 폴더가 모두 있어야 합니다." >&2
    exit 1
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${PROJECT_ROOT}"

echo "==> 4. 가상환경 생성 및 의존성 설치"
sudo -u "${SERVICE_USER}" python3.11 -m venv "${VENV_DIR}"
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install -r "${EDGE_DIR}/requirements.txt"

echo "==> 5. 데이터 디렉터리 준비"
sudo -u "${SERVICE_USER}" mkdir -p "${EDGE_DIR}/data/jobs"
sudo -u "${SERVICE_USER}" mkdir -p "${EDGE_DIR}/data/logs"

echo "==> 6. systemd 서비스 등록"
install -m 644 "${EDGE_DIR}/deploy/rist-edge-api.service" /etc/systemd/system/rist-edge-api.service
install -m 644 "${EDGE_DIR}/deploy/rist-edge-worker.service" /etc/systemd/system/rist-edge-worker.service
# vLLM 서비스는 환경 구성 후 수동 등록 권장(아래 README 참고)
# install -m 644 "${EDGE_DIR}/deploy/rist-vllm.service" /etc/systemd/system/rist-vllm.service

systemctl daemon-reload
systemctl enable --now rist-edge-api.service
systemctl enable --now rist-edge-worker.service

echo "==> 7. 방화벽(8000 포트) 개방"
if command -v ufw &>/dev/null; then
    ufw allow 8000/tcp || true
fi

echo
echo "완료. 상태 확인:"
echo "  systemctl status rist-edge-api.service"
echo "  systemctl status rist-edge-worker.service"
echo "  journalctl -u rist-edge-api.service -f"
echo "  curl http://127.0.0.1:8000/health"
