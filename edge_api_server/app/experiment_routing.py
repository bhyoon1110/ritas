"""Resolve LIMS experiment/equipment identifiers to an Edge analysis project."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any


PROJECT_CODES = frozenset({"FTIR", "RAMAN", "XRD", "TEM"})

_ALIASES: dict[str, str] = {
    "FTIR": "FTIR",
    "FTIRQUAL": "FTIR",
    "IR": "FTIR",
    "RAMAN": "RAMAN",
    "RAMANQUAL": "RAMAN",
    "RIN": "RAMAN",
    "XRD": "XRD",
    "XRAYDIFFRACTION": "XRD",
    "TEM": "TEM",
    "STEM": "TEM",
    "AHN": "TEM",
    "AHNTEM": "TEM",
}


def normalize_identifier(value: object) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(value or "").upper())


def normalize_project_code(value: object) -> str | None:
    normalized = normalize_identifier(value)
    if not normalized:
        return None
    direct = _ALIASES.get(normalized)
    if direct:
        return direct
    if "RAMAN" in normalized:
        return "RAMAN"
    if "FTIR" in normalized:
        return "FTIR"
    if "XRD" in normalized:
        return "XRD"
    if "STEM" in normalized or "TEM" in normalized or "AHN" in normalized:
        return "TEM"
    return None


def parse_analysis_type_map(value: str) -> tuple[tuple[str, str], ...]:
    """Parse `LIMS-CODE=XRD,EQUIPMENT=TEM` environment configuration."""

    resolved: dict[str, str] = {}
    for raw_item in str(value or "").split(","):
        item = raw_item.strip()
        if not item:
            continue
        separator = "=" if "=" in item else ":" if ":" in item else None
        if separator is None:
            raise ValueError(
                "RIST_ANALYSIS_TYPE_MAP 항목은 SOURCE=XRD 또는 SOURCE=TEM 형식이어야 합니다."
            )
        source, target = (part.strip() for part in item.split(separator, 1))
        source_key = normalize_identifier(source)
        project = normalize_project_code(target)
        if not source_key or project not in PROJECT_CODES:
            raise ValueError(
                f"RIST_ANALYSIS_TYPE_MAP 항목이 올바르지 않습니다: {item}"
            )
        resolved[source_key] = project
    return tuple(sorted(resolved.items()))


def resolve_analysis_type(
    *,
    experiment_code: object,
    equipment_code: object = "",
    configured_map: Mapping[str, str] | Iterable[tuple[str, str]] = (),
) -> str | None:
    """Resolve a stable project without replacing the original LIMS code."""

    mapped = resolve_mapped_analysis_type(
        experiment_code=experiment_code,
        equipment_code=equipment_code,
        configured_map=configured_map,
    )
    if mapped is not None:
        return mapped
    for candidate in (experiment_code, equipment_code):
        project = normalize_project_code(candidate)
        if project:
            return project
    return None


def resolve_mapped_analysis_type(
    *,
    experiment_code: object,
    equipment_code: object = "",
    configured_map: Mapping[str, str] | Iterable[tuple[str, str]] = (),
) -> str | None:
    """Resolve only identifiers explicitly registered in the environment map."""

    mapping = {
        normalize_identifier(source): normalize_project_code(target) or ""
        for source, target in dict(configured_map).items()
    }
    for candidate in (experiment_code, equipment_code):
        mapped = mapping.get(normalize_identifier(candidate))
        if mapped in PROJECT_CODES:
            return mapped
    return None


def analysis_type_for_job(job: Mapping[str, Any], settings: Any | None = None) -> str | None:
    return resolve_analysis_type(
        experiment_code=job.get("experiment_code"),
        equipment_code=job.get("equipment_code"),
        configured_map=getattr(settings, "analysis_type_map", ()),
    )


def mapped_analysis_type_for_job(
    job: Mapping[str, Any],
    settings: Any | None = None,
) -> str | None:
    return resolve_mapped_analysis_type(
        experiment_code=job.get("experiment_code"),
        equipment_code=job.get("equipment_code"),
        configured_map=getattr(settings, "analysis_type_map", ()),
    )
