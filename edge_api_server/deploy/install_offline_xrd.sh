#!/usr/bin/env bash
# Uses the service's virtualenv, never the system pip. Does not restart services.
set -euo pipefail
RIST_OFFLINE_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RIST_OFFLINE_PYTHON="${RIST_OFFLINE_PYTHON:-/home/rist/ritas/.venv/bin/python}"
if [[ ! -x "$RIST_OFFLINE_PYTHON" ]]; then
    echo "서비스 Python을 찾을 수 없습니다: $RIST_OFFLINE_PYTHON" >&2
    echo "RIST_OFFLINE_PYTHON=/실제/.venv/bin/python 환경변수로 지정하세요." >&2
    exit 1
fi
exec "$RIST_OFFLINE_PYTHON" "$RIST_OFFLINE_SCRIPT_DIR/offline_xrd.py" "$@"
