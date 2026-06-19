#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
COMPOSE_FILE="${PROJECT_DIR}/deploy/docker-compose.test-db.yml"
PYTHON_BIN="${PYTHON_BIN:-python3}"
KEEP_DB="${KEEP_DB:-0}"

cleanup() {
  if [[ "${KEEP_DB}" != "1" ]]; then
    docker compose -f "${COMPOSE_FILE}" down --volumes --remove-orphans
  fi
}
trap cleanup EXIT

docker compose -f "${COMPOSE_FILE}" up -d

for _ in {1..60}; do
  if docker compose -f "${COMPOSE_FILE}" exec -T mariadb \
    mariadb -uroot -prist_test -e "SELECT 1" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done

docker compose -f "${COMPOSE_FILE}" exec -T mariadb \
  mariadb -uroot -prist_test -e "SELECT 1" >/dev/null

export RIST_TEST_DB_HOST="${RIST_TEST_DB_HOST:-127.0.0.1}"
export RIST_TEST_DB_PORT="${RIST_TEST_DB_PORT:-3307}"
export RIST_TEST_DB_USER="${RIST_TEST_DB_USER:-root}"
export RIST_TEST_DB_PASSWORD="${RIST_TEST_DB_PASSWORD:-rist_test}"

cd "${PROJECT_DIR}"
"${PYTHON_BIN}" -m pytest tests/test_api.py "$@"
