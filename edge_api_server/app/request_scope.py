"""One experiment predicate for local SQL and paginated LIMS results.

Exact lookup mappings take priority over names. Ambiguous EDS/EDX, coating and
'electron microscope' names alone do not establish a TEM experiment.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from .errors import ApiException

PROJECT_ALIASES = {"FTIR": "FTIR", "FT-IR": "FTIR", "FT_IR": "FTIR", "FT IR": "FTIR",
                   "RAMAN": "RAMAN", "XRD": "XRD", "TEM": "TEM", "STEM": "TEM"}
LABELS = {"FTIR": "FT-IR", "RAMAN": "Raman", "XRD": "XRD", "TEM": "TEM·STEM·EDS·코팅층"}
# Delimiters work in both MariaDB REGEXP and Python re. Do not match SYSTEM as TEM,
# or generic X-ray/SEM-EDS/electron-microscopy names as XRD/TEM.
PATTERNS = {
    "FTIR": r"(^|[^a-z0-9])ft[ _-]?ir([^a-z0-9]|$)|fourier[ _-]+transform[ _-]+infrared|푸리에|적외선",
    "RAMAN": r"(^|[^a-z0-9])raman([^a-z0-9]|$)|라만",
    "XRD": r"(^|[^a-z0-9])xrd([^a-z0-9]|$)|x[ _-]?ray[ _-]+diffraction|x[ _-]?선[ _-]*회절|엑스선[ _-]*회절",
    "TEM": r"(^|[^a-z0-9])(s?tem|hr[ _-]?tem)([^a-z0-9]|$)|transmission[ _-]+electron|투과[ _-]*전자",
}
OTHER_MICROSCOPY = r"(^|[^a-z0-9])((fe[ _-]?)?sem|epma)([^a-z0-9]|$)|scanning[ _-]+electron|주사[ _-]*전자"


def project_code(value: object) -> str:
    project = PROJECT_ALIASES.get(str(value or "").strip().upper())
    if not project:
        raise ApiException(400, "INVALID_REQUEST_EXPERIMENT", "의뢰 조회 실험은 FTIR·Raman·XRD·TEM 중에서 선택하세요.")
    return project


def parse_request_map(value: str, name: str) -> tuple[tuple[str, str], ...]:
    """Preserve real LIMS identifiers (including hyphens and leading zeros)."""
    result: dict[str, tuple[str, str]] = {}
    for entry in value.split(","):
        if not entry.strip():
            continue
        source, sep, target = entry.partition("=")
        source = source.strip()
        if not sep or not source or len(source) > 200 or any(ord(c) < 32 for c in source):
            raise ValueError(f"{name}: CODE=FTIR/RAMAN/XRD/TEM 형식으로 설정하세요.")
        try:
            project = project_code(target)
        except ApiException as exc:
            raise ValueError(f"{name}: 지원하지 않는 실험 종류입니다.") from exc
        previous = result.get(source.casefold())
        if previous and previous[1] != project:
            raise ValueError(f"{name}: 같은 코드에 서로 다른 실험을 지정할 수 없습니다.")
        result[source.casefold()] = (source, project)
    if len(result) > 200:
        raise ValueError(f"{name}: 최대 200개 코드를 등록할 수 있습니다.")
    return tuple(result.values())


def _legacy_pattern(code: str) -> str:
    # The existing analysis-routing map has normalized alphanumeric keys.
    key = re.sub(r"[^a-z0-9]", "", code.lower())
    return "^[^a-z0-9]*" + "[^a-z0-9]*".join(key) + "[^a-z0-9]*$" if key else r"a^"


@dataclass(frozen=True)
class RequestScope:
    project: str
    method_map: tuple[tuple[str, str], ...] = ()
    asset_map: tuple[tuple[str, str], ...] = ()
    legacy_map: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_settings(cls, project: str, settings: Any) -> RequestScope:
        return cls(project_code(project), tuple(getattr(settings, "request_method_map", ())),
                   tuple(getattr(settings, "request_asset_map", ())),
                   tuple(getattr(settings, "analysis_type_map", ())))

    @property
    def methods(self) -> tuple[str, ...]:
        return tuple(code for code, target in self.method_map if target == self.project)

    @property
    def assets(self) -> tuple[str, ...]:
        return tuple(code for code, target in self.asset_map if target == self.project)

    def note(self, *, assets: bool = False) -> str:
        label = LABELS[self.project]
        if self.methods:
            return f"{label}: 등록된 시험방법 {len(self.methods)}종 기준으로 조회합니다."
        if assets:
            return f"{label}: 등록된 장비의 수행 가능 시험을 조회합니다."
        return (f"{label}: 등록 코드와 명확한 시험명을 기준으로 조회합니다. "
                "EDS·코팅층처럼 이름만으로 실험을 구분할 수 없는 항목은 관리자 시험코드 매핑이 필요합니다.")

    def mapped_project(self, code: str) -> str | None:
        key = code.strip().casefold()
        explicit = next((p for c, p in self.method_map if c.casefold() == key), None)
        if explicit:
            return explicit
        return next((p for c, p in self.legacy_map if re.search(_legacy_pattern(c), key)), None)

    def matches(self, code: object, name: object) -> bool:
        code, name = str(code or "").strip().lower(), str(name or "").lower()
        if self.methods:
            return code in {c.lower() for c in self.methods}
        mapped = self.mapped_project(code)
        if mapped is not None:
            return mapped == self.project
        found = {p for p, pattern in PATTERNS.items() if re.search(pattern, code) or re.search(pattern, name)}
        if self.project == "TEM" and (re.search(OTHER_MICROSCOPY, code) or re.search(OTHER_MICROSCOPY, name)):
            return False
        return found == {self.project}

    def sql(self) -> tuple[str, list[Any]]:
        code = "LOWER(TRIM(COALESCE(test_mtd_code, '')))"
        name = "LOWER(COALESCE(test_mtd_name, ''))"
        params: list[Any] = []
        def exact(values):
            values = [v.lower() for v in values]
            params.extend(values)
            return f"{code} IN ({', '.join('?' for _ in values)})" if values else "0=1"
        if self.methods:
            return exact(self.methods), params
        own = exact([c for c, p in self.method_map if p == self.project])
        all_explicit = exact([c for c, _ in self.method_map])
        legacy_own = "|".join(_legacy_pattern(c) for c, p in self.legacy_map if p == self.project) or r"a^"
        legacy_all = "|".join(_legacy_pattern(c) for c, _ in self.legacy_map) or r"a^"
        params.extend([legacy_own, legacy_all])
        def named(pattern):
            params.extend([pattern, pattern])
            return f"({code} REGEXP ? OR {name} REGEXP ?)"
        own_name = named(PATTERNS[self.project])
        others = "|".join(pattern for p, pattern in PATTERNS.items() if p != self.project)
        if self.project == "TEM":
            others += "|" + OTHER_MICROSCOPY
        other_name = named(others)
        return (f"({own} OR (NOT ({all_explicit}) AND ({code} REGEXP ? OR "
                f"(NOT ({code} REGEXP ?) AND {own_name} AND NOT {other_name}))))"), params

    def query_filters(self, filters: dict[str, Any]) -> tuple[list[dict[str, Any]], bool]:
        """Use exact server-side conditions when configured; never widen user filters."""
        method = filters.get("testMtdCode")
        asset = filters.get("assetNumber")
        if method and ((self.methods and method.casefold() not in {c.casefold() for c in self.methods})
                       or self.mapped_project(method) not in (None, self.project)):
            raise ApiException(400, "REQUEST_SCOPE_MISMATCH", "현재 실험에 속하지 않는 시험방법 코드입니다.")
        asset_owner = next((p for c, p in self.asset_map if c.casefold() == str(asset or "").casefold()), None)
        if asset_owner and asset_owner != self.project:
            raise ApiException(400, "REQUEST_SCOPE_MISMATCH", "현재 실험에 속하지 않는 장비 자산번호입니다.")
        # One condition per relay call, OR within a mapped list, AND between fields.
        methods = [method] if method else list(self.methods) or [None]
        assets = [asset] if asset else list(self.assets) or [None]
        if len(methods) * len(assets) > 32:
            raise ApiException(422, "REQUEST_SCOPE_TOO_BROAD", "연결된 조회 조건이 많습니다. 장비 또는 시험방법을 선택해 범위를 좁혀 주세요.")
        queries = [{**filters, **({"testMtdCode": m} if m else {}), **({"assetNumber": a} if a else {})}
                   for m in methods for a in assets]
        trusted_assets = bool(self.assets) and all(
            str(q.get("assetNumber") or "").casefold() in {a.casefold() for a in self.assets} for q in queries)
        return queries, trusted_assets
