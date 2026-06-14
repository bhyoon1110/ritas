# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: FTIR 분석 CLI의 이전 호환용 진입점. ftir.cli.main 을 그대로 호출한다.
# 실행 방법: python ftir_analyze.py <DPT파일> [옵션]
#            예) python ftir_analyze.py sample.dpt --origin --top 10
#            (상세 옵션은 ftir/cli.py 의 argparse 정의 참조)
# ─────────────────────────────────────────────────────────────────────────────
"""Backward-compatible CLI entry point for FTIR analysis."""

from ftir.cli import main


if __name__ == "__main__":
    main()
