#!/usr/bin/env bash
# OS packages, not pip; default is a read-only installation plan.
set -euo pipefail
RIST_PREVIEW_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RIST_PREVIEW_PYTHON=""
for RIST_PREVIEW_CANDIDATE in /usr/bin/python3 /usr/bin/python3.12; do
    if [[ -x "$RIST_PREVIEW_CANDIDATE" ]]; then
        RIST_PREVIEW_PYTHON="$RIST_PREVIEW_CANDIDATE"
        break
    fi
done
if [[ -z "$RIST_PREVIEW_PYTHON" ]]; then
    echo 'Ubuntu 시스템 python3가 필요합니다. 서비스 .venv나 pip에는 설치하지 않습니다.' >&2
    exit 1
fi
exec "$RIST_PREVIEW_PYTHON" -I "$RIST_PREVIEW_DIR/offline_report_preview.py" "$@"
