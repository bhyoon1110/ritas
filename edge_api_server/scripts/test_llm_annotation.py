#!/usr/bin/env python3
"""실제 vLLM(gemma4-e4b)으로 보고서 LLM 슬롯 주석을 검증하는 스크립트.

엣지 호스트에서 실행한다. vLLM(기본 http://127.0.0.1:8001)은 루프백에만
바인딩되므로 개발 PC가 아닌 엣지에서 돌려야 한다. DB는 사용하지 않는다.

동작:
  1) Settings.from_env() 로 운영 LLM 설정을 읽는다.
  2) 임시 storage_root 아래에 가짜 작업 폴더를 만들고, 샘플 분석 결과
     JSON(FTIR verdict)과 (선택) 작은 이미지를 processed/ 에 넣는다.
  3) generate_report() 로 규칙 기반 보고서 + 실제 LLM 슬롯 주석을 생성한다.
  4) LLM 사용 여부/슬롯 문안/이미지 첨부 여부와 report.json/report.md 경로를
     출력한다.

실행 예 (엣지):
    cd /home/rist/ritas/edge_api_server
    sudo -u rist RIST_ENV=production PYTHONPATH="$PWD" \
        ../.venv/bin/python scripts/test_llm_annotation.py
    # 이미지 없이:           --no-image
    # 다른 실험코드:         --experiment-code XRD
    # 임시 폴더 보존 경로만:  (기본 보존, 종료 시 경로 출력)
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

# scripts/ 에서 직접 실행할 때도 app 패키지를 찾도록 보강한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import Settings  # noqa: E402
from app.llm_client import LocalLlmClient  # noqa: E402
from app.report import generate_report  # noqa: E402
from app.time_utils import isoformat_kst  # noqa: E402

GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
DIM = "\033[2m"
BOLD = "\033[1m"
RESET = "\033[0m"

# 1x1 투명 PNG(멀티모달 요청 경로 검증용 최소 이미지).
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYPhfDwAChwGA60e6"
    "kgAAAABJRU5ErkJggg=="
)


def sample_ftir_verdict() -> dict:
    """FtirReportBuilder 가 고정 양식으로 매핑하는 형태의 샘플 verdict."""
    return {
        "sample": "5_Melamine Cyanurate.0",
        "tier": "미동정 (No reliable match)",
        "reason": "최고 종합 점수 64.5% < 임계 65%",
        "is_identified": True,
        "is_library_identified": False,
        "library_size": 589,
        "top_candidate": {
            "material": "m-Xylene",
            "category": "Steel Coating",
            "composite_pct": 64.54,
            "cosine_pct": 92.3,
            "deriv_pct": 65.46,
            "peak_pct": 26.67,
            "overlap_pct": 99.9,
        },
        "findings": {
            "functional_groups": [
                {
                    "group": "트라이아진(멜라민계) 고리",
                    "confidence_pct": 88.0,
                    "evidence": "약 1550, 810 cm-1 흡수",
                },
                {
                    "group": "N-H 신축",
                    "confidence_pct": 75.0,
                    "evidence": "3100~3300 cm-1 폭넓은 흡수",
                },
            ]
        },
        "combined_verdict": {
            "verdict": "미동정(추가 검토 필요)",
            "confidence": "중",
            "action": "정성 재측정 및 라이브러리 보강 권고",
            "explanation": "라이브러리 신뢰 후보가 임계값 미만",
        },
    }


def sample_generic_verdict(experiment_code: str) -> dict:
    return {
        "sample": "SMOKE-SAMPLE",
        "experiment": experiment_code,
        "peakCount": 5,
        "finding": "주요 피크 3개 검출(참고용)",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="실제 vLLM 으로 보고서 LLM 슬롯 주석을 검증한다.",
    )
    parser.add_argument(
        "--experiment-code",
        default="FT-IR",
        help="실험 코드 (기본 FT-IR; FTIR 외에는 Generic 빌더 사용)",
    )
    parser.add_argument(
        "--no-image",
        action="store_true",
        help="processed 에 이미지(멀티모달)를 넣지 않는다",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        default=True,
        help="임시 폴더를 보존한다(기본 보존). 경로를 출력한다.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="LLM 베이스 URL 강제 지정(기본: 설정값, 보통 http://127.0.0.1:8001)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        settings = Settings.from_env()
    except Exception as exc:  # 설정 로드 실패
        print(f"{RED}설정 로드 실패: {exc}{RESET}")
        print("RIST_ENV=production 과 PYTHONPATH 를 지정했는지 확인하세요.")
        return 1

    if args.base_url:
        settings = replace(settings, llm_base_url=args.base_url.rstrip("/"))

    tmp_root = Path(tempfile.mkdtemp(prefix="rist-llm-test-"))
    settings = replace(settings, storage_root=tmp_root)

    root_relative = "llm-test/job-0001"
    job = {
        "job_id": "llm-test-0001",
        "request_number": "LLM-TEST-0001",
        "experiment_code": args.experiment_code,
        "equipment_code": "LLM-TEST-EQ",
        "operator_id": "llm-tester",
        "root_relative_path": root_relative,
    }

    job_root = tmp_root / root_relative
    processed = job_root / "processed"
    processed.mkdir(parents=True, exist_ok=True)

    code = args.experiment_code.upper().replace("_", "-")
    if code in {"FTIR", "FT-IR", "IR"}:
        verdict = sample_ftir_verdict()
    else:
        verdict = sample_generic_verdict(args.experiment_code)
    (processed / "analysis-result.json").write_text(
        json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    image_attached_intent = not args.no_image and settings.llm_include_images
    if not args.no_image:
        (processed / "spectrum.png").write_bytes(_TINY_PNG)

    print(f"{BOLD}대상 LLM:{RESET} {settings.llm_base_url}  "
          f"model={settings.llm_model}  temp={settings.llm_temperature}")
    print(f"{BOLD}실험 코드:{RESET} {args.experiment_code}  "
          f"이미지 첨부 시도={image_attached_intent}")
    print(f"{BOLD}임시 작업 폴더:{RESET} {job_root}")
    print("=" * 60)

    client = LocalLlmClient(
        settings.llm_base_url,
        settings.llm_model,
        settings.llm_timeout_seconds,
        settings.llm_temperature,
        settings.llm_max_tokens,
        settings.llm_validate_model,
    )

    try:
        document = generate_report(
            settings,
            job,
            llm_client=client,
            generated_at=isoformat_kst(),
        )
    except Exception as exc:  # 파이프라인 자체 실패
        print(f"{RED}보고서 생성 실패: {type(exc).__name__}: {exc}{RESET}")
        client.close()
        return 1
    finally:
        client.close()

    print()
    if document.llm_used:
        print(f"{GREEN}LLM 사용: 성공{RESET} (슬롯을 실제 모델이 채움)")
    else:
        print(f"{YELLOW}LLM 사용: 안 함/실패{RESET} "
              f"(규칙 기본 문안 사용) error={document.llm_error}")

    # 슬롯 섹션 출력
    for slot_id in ("summary", "narrative", "caption"):
        section = document.section(slot_id)
        if section is None:
            continue
        tag = (
            f"{GREEN}LLM{RESET}" if section.source == "llm"
            else f"{YELLOW}rule{RESET}"
        )
        text = section.paragraphs[0] if section.paragraphs else "(빈 문안)"
        print(f"\n{BOLD}[{slot_id}]{RESET} ({tag})\n  {text}")

    # 멀티모달(이미지) 첨부 여부를 요청 로그에서 확인
    request_log = job_root / "logs" / "llm-request.json"
    image_in_request = False
    if request_log.exists():
        try:
            logged = json.loads(request_log.read_text(encoding="utf-8"))
            user_msg = logged["messages"][1]["content"]
            if isinstance(user_msg, list):
                image_in_request = any(
                    part.get("type") == "image_url" for part in user_msg
                )
        except Exception:
            pass
    print()
    if image_attached_intent and image_in_request:
        print(f"{GREEN}이미지 멀티모달 요청 확인됨{RESET} "
              f"(요청 로그에 image_url 포함, base64 는 로그에서 가려짐)")
    elif image_attached_intent:
        print(f"{YELLOW}이미지 첨부를 시도했으나 요청에 포함되지 않음{RESET} "
              f"(크기 제한/설정 확인)")
    else:
        print(f"{DIM}이미지 첨부 안 함{RESET}")

    print()
    print(f"{BOLD}산출물:{RESET}")
    print(f"  report.json : {job_root / 'report' / 'report.json'}")
    print(f"  report.md   : {job_root / 'report' / 'report.md'}")
    print(f"  llm 로그    : {job_root / 'logs'}")
    print()
    print(f"{DIM}정리하려면: rm -rf {tmp_root}{RESET}")

    return 0 if document.llm_used else 2


if __name__ == "__main__":
    sys.exit(main())
