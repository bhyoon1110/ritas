# ─────────────────────────────────────────────────────────────────────────────
# 파일 설명: YAML 룰 로딩 및 스키마 검증(PyYAML 필요). 표준 룰 스키마 정의.
# 실행 방법: 모듈 — 직접 실행하지 않고 import해서 사용 (ftir_analyze.py / ftir.cli 를 통해 동작)
# ─────────────────────────────────────────────────────────────────────────────
"""YAML rule loading and schema validation. PyYAML is required.

표준 룰 스키마 (v2.4+):

    compound:       str          (필수) 화합물 정식 명칭
    aliases:        list[str]    (선택) 별칭/한글명/약어
    category:       str          (선택) 라이브러리 카테고리 경로
    description:    str          (선택) 자유 텍스트
    peaks:          list[Peak]   (필수) 피크 정의 — 분류는 role 필드로만 결정
    scoring:        dict         (선택) 채점 파라미터

    Peak 항목:
      id, role, wavenumber_min, wavenumber_max, vibration_mode,
      assignment, intensity (strong|medium|weak), tolerance
      [warning/hard_forbidden 전용] min_intensity
      [context_marker 전용]        marker_type, interpretation

    role 값:
      required        — 채점 시 필수
      supporting      — 있으면 가산, 없어도 무감점
      warning         — 강하게 나타나면 감점 (soft forbidden)
      hard_forbidden  — 나타나면 큰 폭 감점
      context_marker  — 채점 미반영 (첨가제/공정 해석용)

후방 호환: v1(섹션형 peaks dict)과 v2.3 이하(role + required/forbidden 플래그)도
계속 로드 가능.
"""

import yaml


def load_yaml_safe(path):
    """YAML 파일을 로드 (PyYAML 필수)."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def validate_rule(rule: dict, path: str):
    """YAML 룰의 필수 구조를 최소 검증한다."""
    if not isinstance(rule, dict):
        raise ValueError("YAML 최상위가 dict가 아닙니다")
    if not rule.get("compound"):
        raise ValueError("compound 필드가 없습니다")
    peaks = rule.get("peaks")
    if not peaks:
        raise ValueError("peaks 필드가 비어 있습니다")

    allowed_roles = {
        "required", "supporting", "warning", "forbidden", "hard_forbidden",
        "context_marker", "additive", "process_marker", "modifier", ""
    }
    if isinstance(peaks, list):
        for idx, peak in enumerate(peaks, start=1):
            if not isinstance(peak, dict):
                raise ValueError(f"peaks[{idx}] 항목이 dict가 아닙니다")
            role = str(peak.get("role", "")).lower()
            if role not in allowed_roles:
                raise ValueError(f"peaks[{idx}] role 값이 잘못되었습니다: {role}")
            has_range = (
                "range" in peak or
                ("wavenumber_min" in peak and "wavenumber_max" in peak)
            )
            if not has_range:
                raise ValueError(f"peaks[{idx}]에 range 또는 wavenumber_min/max가 없습니다")
    elif isinstance(peaks, dict):
        for section in peaks:
            if section not in {"required", "supporting", "warning", "forbidden"}:
                print(f"  [룰 스키마 경고] {path}: 알 수 없는 peaks 섹션 {section}")
    else:
        raise ValueError("peaks 필드는 list 또는 dict여야 합니다")

    # subtypes 가 있으면 최소 구조 검증
    subtypes = rule.get("subtypes")
    if subtypes is not None:
        if not isinstance(subtypes, list):
            raise ValueError("subtypes 필드는 list여야 합니다")
        for idx, st in enumerate(subtypes, start=1):
            if not isinstance(st, dict):
                raise ValueError(f"subtypes[{idx}] 항목이 dict가 아닙니다")
            if not st.get("name"):
                raise ValueError(f"subtypes[{idx}] name 필드가 없습니다")
            st_peaks = st.get("peaks")
            if not st_peaks or not isinstance(st_peaks, list):
                raise ValueError(f"subtypes[{idx}] peaks가 비어 있거나 list가 아닙니다")
