# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: ftir 분석 패키지의 초기화 모듈. 패키지 경계를 정의한다.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (import ftir)
# ─────────────────────────────────────────────────────────────────────────────
"""FTIR analysis package."""

import sys
from pathlib import Path


COMMON_DIR = Path(__file__).resolve().parents[2] / "common"
if str(COMMON_DIR) not in sys.path:
    sys.path.insert(0, str(COMMON_DIR))
