#!/usr/bin/env bash
#
# RIST Edge 데이터 초기화 스크립트
# - jobs / files / idempotency_records 테이블을 비우고
# - 업로드 저장소(storage_root)의 파일을 모두 삭제한다.
#
# 사용법:
#   bash scripts/reset_edge_data.sh                # 확인 프롬프트 후 초기화
#   bash scripts/reset_edge_data.sh --yes          # 프롬프트 없이 실행
#   bash scripts/reset_edge_data.sh --drop         # 테이블 TRUNCATE 대신 DB 재생성
#   bash scripts/reset_edge_data.sh --keep-services# 서비스 정지/재시작 생략
#
# DB 접속 정보는 edge.env(RIST_DB_*) 또는 환경변수에서 읽는다.
# 저장소 경로는 RIST_STORAGE_ROOT > EDGE_STORAGE_ROOT(profile) > 기본값 순으로 결정한다.

set -euo pipefail

# --- 경로 기준 ---------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
EDGE_DIR="$(dirname "${SCRIPT_DIR}")"
PROJECT_ROOT="$(dirname "${EDGE_DIR}")"

# --- 옵션 파싱 ---------------------------------------------------------
ASSUME_YES=0
DROP_DB=0
KEEP_SERVICES=0
for arg in "$@"; do
    case "${arg}" in
        --yes|-y) ASSUME_YES=1 ;;
        --drop) DROP_DB=1 ;;
        --keep-services) KEEP_SERVICES=1 ;;
        --help|-h)
            sed -n '3,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "알 수 없는 옵션: ${arg}" >&2
            exit 1
            ;;
    esac
done

# --- 설정 로드 ---------------------------------------------------------
# edge.env(있으면)에서 RIST_* 변수 로드
if [[ -f "${PROJECT_ROOT}/edge.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "${PROJECT_ROOT}/edge.env"
    set +a
fi

RIST_ENV="${RIST_ENV:-production}"
PROFILE_FILE="${PROJECT_ROOT}/config/environments/${RIST_ENV}.env"

# profile에서 EDGE_STORAGE_ROOT 추출(RIST_STORAGE_ROOT가 없을 때만 사용)
profile_storage_root=""
if [[ -f "${PROFILE_FILE}" ]]; then
    profile_storage_root="$(
        grep -E '^EDGE_STORAGE_ROOT=' "${PROFILE_FILE}" 2>/dev/null \
            | tail -n1 | cut -d= -f2- | tr -d '"'\' || true
    )"
fi

# 저장소 루트 결정
STORAGE_ROOT="${RIST_STORAGE_ROOT:-}"
if [[ -z "${STORAGE_ROOT}" ]]; then
    STORAGE_ROOT="${profile_storage_root}"
fi
if [[ -z "${STORAGE_ROOT}" ]]; then
    STORAGE_ROOT="${EDGE_DIR}/data/jobs"
fi
# ~ 확장
STORAGE_ROOT="${STORAGE_ROOT/#\~/${HOME}}"

# DB 접속 정보
DB_HOST="${RIST_DB_HOST:-127.0.0.1}"
DB_PORT="${RIST_DB_PORT:-3306}"
DB_NAME="${RIST_DB_NAME:-rist_edge}"
DB_USER="${RIST_DB_USER:-rist}"
DB_PASSWORD="${RIST_DB_PASSWORD:-}"

SERVICES=("rist-edge-api.service" "rist-edge-worker.service")

# --- 안전장치 ----------------------------------------------------------
case "${STORAGE_ROOT}" in
    ""|"/"|"/home"|"/root"|"${HOME}")
        echo "안전을 위해 거부: STORAGE_ROOT='${STORAGE_ROOT}' 은(는) 삭제할 수 없습니다." >&2
        exit 1
        ;;
esac

# --- 요약 출력 ---------------------------------------------------------
echo "============================================================"
echo " RIST Edge 데이터 초기화"
echo "------------------------------------------------------------"
echo " 환경(RIST_ENV) : ${RIST_ENV}"
echo " DB             : ${DB_USER}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
echo " 저장소 루트    : ${STORAGE_ROOT}"
if [[ ${DROP_DB} -eq 1 ]]; then
    echo " DB 작업        : DROP DATABASE 후 재생성"
else
    echo " DB 작업        : jobs/files/idempotency_records TRUNCATE"
fi
echo " 서비스 정지    : $([[ ${KEEP_SERVICES} -eq 1 ]] && echo '생략' || echo '예')"
echo "============================================================"

# --- 확인 프롬프트 -----------------------------------------------------
if [[ ${ASSUME_YES} -ne 1 ]]; then
    echo "이 작업은 위 데이터를 영구 삭제합니다. 계속하려면 RESET 을 입력하세요."
    read -r -p "> " answer
    if [[ "${answer}" != "RESET" ]]; then
        echo "취소되었습니다."
        exit 1
    fi
fi

# --- mysql 헬퍼 --------------------------------------------------------
run_mysql() {
    # stdin으로 SQL을 받아 실행한다.
    if [[ -n "${DB_PASSWORD}" ]]; then
        MYSQL_PWD="${DB_PASSWORD}" mysql \
            --host="${DB_HOST}" --port="${DB_PORT}" --user="${DB_USER}" "$@"
    else
        mysql --host="${DB_HOST}" --port="${DB_PORT}" --user="${DB_USER}" "$@"
    fi
}

# --- 1. 서비스 정지 ----------------------------------------------------
if [[ ${KEEP_SERVICES} -ne 1 ]] && command -v systemctl &>/dev/null; then
    echo "==> 1. 서비스 정지"
    sudo systemctl stop "${SERVICES[@]}" 2>/dev/null || true
else
    echo "==> 1. 서비스 정지 생략"
fi

# --- 2. DB 초기화 ------------------------------------------------------
echo "==> 2. DB 초기화"
if [[ ${DROP_DB} -eq 1 ]]; then
    run_mysql <<SQL
DROP DATABASE IF EXISTS \`${DB_NAME}\`;
CREATE DATABASE \`${DB_NAME}\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
SQL
    echo "    DB 재생성 완료 (테이블은 서비스 시작 시 자동 생성됨)"
else
    run_mysql "${DB_NAME}" <<'SQL'
SET FOREIGN_KEY_CHECKS = 0;
TRUNCATE TABLE idempotency_records;
TRUNCATE TABLE files;
TRUNCATE TABLE jobs;
SET FOREIGN_KEY_CHECKS = 1;
SQL
    echo "    테이블 비움 완료"
fi

# --- 3. 저장소 파일 삭제 ----------------------------------------------
echo "==> 3. 저장소 파일 삭제 (${STORAGE_ROOT})"
if [[ -d "${STORAGE_ROOT}" ]]; then
    find "${STORAGE_ROOT}" -mindepth 1 -delete
    echo "    삭제 완료"
else
    echo "    경로가 없어 건너뜀(서비스 시작 시 자동 생성됨)"
fi

# --- 4. 서비스 재시작 --------------------------------------------------
if [[ ${KEEP_SERVICES} -ne 1 ]] && command -v systemctl &>/dev/null; then
    echo "==> 4. 서비스 재시작"
    sudo systemctl start "${SERVICES[@]}"
    echo "    재시작 완료"
else
    echo "==> 4. 서비스 재시작 생략"
fi

echo "------------------------------------------------------------"
echo " 초기화 완료."
echo "============================================================"
