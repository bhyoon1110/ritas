#!/usr/bin/env bash
#
# RIST Edge 서버 배포 스크립트 (Ubuntu)
# - rist 전용 계정 생성
# - Python(>=3.11) 가상환경 구성 및 의존성 설치
# - systemd 서비스 등록(rist-edge-api, rist-edge-worker)
#
# 사용법:  sudo bash deploy/install.sh
#
# 주의: vLLM 은 docker-compose 컨테이너(deploy/docker-compose.vllm.yml)로 별도 구동한다.
#       이 스크립트는 API 서버와 worker만 설정한다.

set -euo pipefail

PROJECT_ROOT="/home/rist/ritas"
SERVICE_USER="rist"
EDGE_DIR="${PROJECT_ROOT}/edge_api_server"
# 저장소 루트의 단일 가상환경을 사용한다(common, edge_api_server 공용).
VENV_DIR="${PROJECT_ROOT}/.venv"

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

echo "==> 2. Python(>=3.11) 확인"
# requires-python >= 3.11. 배포판 기본 파이썬을 우선 사용한다.
# (Ubuntu 22.04=3.10, 24.04=3.12 이므로 버전을 고정하지 않는다.)
PYTHON_BIN=""
for candidate in python3.13 python3.12 python3.11 python3; do
    if command -v "${candidate}" &>/dev/null; then
        if "${candidate}" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
            PYTHON_BIN="$(command -v "${candidate}")"
            break
        fi
    fi
done

if [[ -z "${PYTHON_BIN}" ]]; then
    echo "    Python 3.11 이상이 없습니다. 설치를 진행합니다."
    apt-get update
    # 기본 python3 가 3.11 이상이면 venv 패키지만 설치하면 된다.
    apt-get install -y python3 python3-venv
    if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)'; then
        PYTHON_BIN="$(command -v python3)"
    else
        echo "    오류: 시스템 python3 가 3.11 미만입니다. python3.11+ 를 설치한 뒤 다시 실행하세요." >&2
        echo "          (예: deadsnakes PPA 또는 배포판 패키지로 python3.12 설치)" >&2
        exit 1
    fi
fi

# 선택된 파이썬에 맞는 venv 모듈이 없으면 설치한다.
if ! "${PYTHON_BIN}" -m venv --help &>/dev/null; then
    PYVER="$("${PYTHON_BIN}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    echo "    venv 모듈이 없어 python${PYVER}-venv 를 설치합니다."
    apt-get update
    apt-get install -y "python${PYVER}-venv" || apt-get install -y python3-venv
fi
echo "    사용할 파이썬: ${PYTHON_BIN} ($("${PYTHON_BIN}" --version))"

echo "==> 3. 프로젝트 디렉터리 소유권 정리"
if [[ ! -d "${PROJECT_ROOT}/common" || ! -d "${PROJECT_ROOT}/config" || ! -d "${EDGE_DIR}" ]]; then
    echo "    오류: ${PROJECT_ROOT} 아래에 common/ config/ edge_api_server/ 세 폴더가 모두 있어야 합니다." >&2
    exit 1
fi
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${PROJECT_ROOT}"

echo "==> 4. 가상환경 생성 및 의존성 설치"
sudo -u "${SERVICE_USER}" "${PYTHON_BIN}" -m venv "${VENV_DIR}"
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install --upgrade pip
sudo -u "${SERVICE_USER}" "${VENV_DIR}/bin/pip" install -r "${EDGE_DIR}/requirements.txt"

echo "==> 5. 데이터 디렉터리 준비"
sudo -u "${SERVICE_USER}" mkdir -p "${EDGE_DIR}/data/jobs"
sudo -u "${SERVICE_USER}" mkdir -p "${EDGE_DIR}/data/logs"

echo "==> 6. 비밀/환경 파일(${PROJECT_ROOT}/edge.env) 준비"
# 비밀값(DB 비밀번호 등)은 git 추적 밖(.gitignore)의 이 파일에서 읽는다.
# 이미 있으면 보존하여 운영자가 입력한 값을 덮어쓰지 않는다.
if [[ ! -f "${PROJECT_ROOT}/edge.env" ]]; then
    install -m 640 -o "${SERVICE_USER}" -g "${SERVICE_USER}" \
        "${PROJECT_ROOT}/edge.env.example" "${PROJECT_ROOT}/edge.env"
    echo "    ${PROJECT_ROOT}/edge.env 생성 완료. 실제 DB 접속 정보로 수정 후 서비스를 재시작하세요."
else
    echo "    이미 존재함(보존): ${PROJECT_ROOT}/edge.env"
fi

echo "==> 7. systemd 서비스 등록"
install -m 644 "${EDGE_DIR}/deploy/rist-edge-api.service" /etc/systemd/system/rist-edge-api.service
install -m 644 "${EDGE_DIR}/deploy/rist-edge-worker.service" /etc/systemd/system/rist-edge-worker.service
# vLLM 은 docker-compose 로 별도 구동한다(아래 README 의 "3. vLLM" 참고):
#   sudo docker compose -f "${EDGE_DIR}/deploy/docker-compose.vllm.yml" up -d

systemctl daemon-reload
systemctl enable --now rist-edge-api.service
systemctl enable --now rist-edge-worker.service

echo "==> 8. 방화벽(8000 포트) 개방"
if command -v ufw &>/dev/null; then
    ufw allow 8000/tcp || true
fi

echo
echo "완료. 상태 확인:"
echo "  systemctl status rist-edge-api.service"
echo "  systemctl status rist-edge-worker.service"
echo "  journalctl -u rist-edge-api.service -f"
echo "  curl http://127.0.0.1:8000/health"
