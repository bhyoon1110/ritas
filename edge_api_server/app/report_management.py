from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import mimetypes
from pathlib import Path
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

from .database import Database
from .xrd_portable_html import make_xrd_html_portable


router = APIRouter()
KST = timezone(timedelta(hours=9))
ACTIVE_TRANSFER_STATUSES = {"PENDING", "PROCESSING", "RETRY_WAIT"}
RETENTION_POLICY_DEFINITIONS = {
    "UNSENT_TEST": {
        "label": "미전송 테스트 보고서",
        "description": "전송 큐에 등록되지 않은 테스트 보고서를 생성 시점부터 보존합니다.",
    },
    "FAILED_OR_CANCELLED": {
        "label": "실패·취소 보고서",
        "description": "FAILED 또는 CANCELLED 전송 보고서를 마지막 처리 시점부터 보존합니다.",
    },
    "COMPLETED": {
        "label": "LIMS 전송 완료 보고서",
        "description": "LIMS 전송 완료가 확인된 보고서를 완료 시점부터 보존합니다.",
    },
    "TRASH": {
        "label": "휴지통 파일",
        "description": "휴지통으로 이동한 파일을 실제로 삭제하기 전까지 보존합니다.",
    },
}


class ReportLifecycleUpdate(BaseModel):
    is_test: bool | None = Field(default=None, alias="isTest")
    pinned: bool | None = None
    retention_until: datetime | None = Field(default=None, alias="retentionUntil")
    update_retention: bool = Field(default=False, alias="updateRetention")

    model_config = {"populate_by_name": True}


class ReportCleanupRequest(BaseModel):
    report_ids: list[str] = Field(alias="reportIds", min_length=1, max_length=500)
    actor: str = Field(default="운영 관리자", max_length=100)
    reason: str = Field(default="보존 정책에 따른 정리", max_length=500)

    model_config = {"populate_by_name": True}


class RetentionPolicyValue(BaseModel):
    retention_days: int = Field(alias="retentionDays", ge=1, le=3650)
    auto_cleanup_enabled: bool = Field(alias="autoCleanupEnabled")

    model_config = {"populate_by_name": True}


class RetentionPolicyUpdateRequest(BaseModel):
    policies: dict[str, RetentionPolicyValue]
    actor: str = Field(default="운영 관리자", min_length=1, max_length=100)

    model_config = {"populate_by_name": True}


def _database(request: Request) -> Database:
    database = getattr(request.app.state, "database", None)
    if database is None:
        raise HTTPException(
            status_code=503,
            detail="보고서 관리 데이터베이스가 연결되어 있지 않습니다.",
        )
    return database


def _settings(request: Request) -> Any:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        raise HTTPException(status_code=503, detail="서버 설정을 찾을 수 없습니다.")
    return settings


def _storage_root(settings: Any) -> Path:
    return Path(settings.storage_root).expanduser().resolve()


def _safe_storage_path(settings: Any, relative_path: str) -> Path:
    root = _storage_root(settings)
    candidate = (root / str(relative_path)).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="저장소 상대 경로가 올바르지 않습니다.") from exc
    return candidate


def _parse_datetime(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=KST)
    return parsed.astimezone(KST)


def _default_retention_policies(settings: Any) -> dict[str, dict[str, Any]]:
    return {
        "UNSENT_TEST": {
            "retention_days": max(1, int(settings.report_test_retention_days)),
            "auto_cleanup_enabled": True,
            "description": RETENTION_POLICY_DEFINITIONS["UNSENT_TEST"]["description"],
        },
        "FAILED_OR_CANCELLED": {
            "retention_days": max(1, int(settings.report_failed_retention_days)),
            "auto_cleanup_enabled": True,
            "description": RETENTION_POLICY_DEFINITIONS["FAILED_OR_CANCELLED"]["description"],
        },
        "COMPLETED": {
            "retention_days": max(1, int(settings.report_completed_retention_days)),
            "auto_cleanup_enabled": True,
            "description": RETENTION_POLICY_DEFINITIONS["COMPLETED"]["description"],
        },
        "TRASH": {
            "retention_days": max(1, int(settings.report_trash_retention_days)),
            "auto_cleanup_enabled": True,
            "description": RETENTION_POLICY_DEFINITIONS["TRASH"]["description"],
        },
    }


def _load_retention_policies(settings: Any, database: Database) -> dict[str, dict[str, Any]]:
    defaults = _default_retention_policies(settings)
    database.ensure_report_retention_policies(defaults)
    policies = defaults.copy()
    for row in database.list_report_retention_policies():
        policy_key = str(row.get("policy_key") or "").upper()
        if policy_key not in defaults:
            continue
        policies[policy_key] = {
            **defaults[policy_key],
            **row,
            "retention_days": max(1, int(row.get("retention_days") or 1)),
            "auto_cleanup_enabled": bool(row.get("auto_cleanup_enabled")),
        }
    return policies


def _retention_policy_response(settings: Any, database: Database) -> dict[str, Any]:
    policies = _load_retention_policies(settings, database)
    items: list[dict[str, Any]] = []
    for policy_key, definition in RETENTION_POLICY_DEFINITIONS.items():
        policy = policies[policy_key]
        items.append(
            {
                "policyKey": policy_key,
                "label": definition["label"],
                "description": definition["description"],
                "retentionDays": int(policy["retention_days"]),
                "autoCleanupEnabled": bool(policy["auto_cleanup_enabled"]),
                "updatedAt": policy.get("updated_at"),
                "updatedBy": policy.get("updated_by"),
            }
        )
    return {
        "policies": items,
        "fixedRules": [
            {
                "label": "활성 전송 삭제 금지",
                "description": "PENDING, PROCESSING, RETRY_WAIT 상태의 보고서는 삭제할 수 없습니다.",
            },
            {
                "label": "보존 지정 자동 정리 제외",
                "description": "보존 지정한 보고서는 기간과 관계없이 자동 정리하지 않습니다.",
            },
        ],
    }


def _retention_deadline(
    row: dict[str, Any],
    settings: Any,
    policies: dict[str, dict[str, Any]] | None = None,
) -> tuple[datetime | None, str, str | None, bool]:
    resolved = policies or _default_retention_policies(settings)
    explicit = _parse_datetime(row.get("retention_until"))
    if explicit is not None:
        return explicit, "직접 지정", "EXPLICIT", True
    status = str(row.get("transfer_status") or "NOT_QUEUED").upper()
    generated = _parse_datetime(row.get("generated_at") or row.get("created_at"))
    completed = _parse_datetime(row.get("transfer_completed_at") or row.get("updated_at"))
    if bool(row.get("is_test")) and status == "NOT_QUEUED" and generated:
        policy = resolved["UNSENT_TEST"]
        return (
            generated + timedelta(days=int(policy["retention_days"])),
            "미전송 테스트",
            "UNSENT_TEST",
            bool(policy["auto_cleanup_enabled"]),
        )
    if status in {"FAILED", "CANCELLED"} and completed:
        policy = resolved["FAILED_OR_CANCELLED"]
        return (
            completed + timedelta(days=int(policy["retention_days"])),
            status,
            "FAILED_OR_CANCELLED",
            bool(policy["auto_cleanup_enabled"]),
        )
    if status == "COMPLETED" and completed:
        policy = resolved["COMPLETED"]
        return (
            completed + timedelta(days=int(policy["retention_days"])),
            "LIMS 전송 완료",
            "COMPLETED",
            bool(policy["auto_cleanup_enabled"]),
        )
    return None, "자동 정리 제외", None, False


def _artifact_state(settings: Any, artifact: dict[str, Any]) -> dict[str, Any]:
    relative_path = str(artifact.get("relative_path") or "")
    try:
        path = _safe_storage_path(settings, relative_path)
    except HTTPException:
        return {
            **artifact,
            "exists": False,
            "actualSizeBytes": None,
            "sizeMatches": False,
            "invalidPath": True,
            "downloadUrl": None,
        }
    exists = path.is_file()
    actual_size = path.stat().st_size if exists else None
    expected_size = int(artifact.get("size_bytes") or 0)
    return {
        **artifact,
        "exists": exists,
        "actualSizeBytes": actual_size,
        "sizeMatches": exists and actual_size == expected_size,
        "downloadUrl": (
            f"/api/v1/report-management/artifacts/{artifact['artifact_id']}/download"
            if exists and not artifact.get("deleted_at")
            else None
        ),
    }


def _decorate_row(
    row: dict[str, Any],
    settings: Any,
    *,
    include_artifacts: bool,
    policies: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    deadline, policy, policy_key, auto_cleanup_enabled = _retention_deadline(
        row, settings, policies
    )
    now = datetime.now(KST)
    transfer_status = str(row.get("transfer_status") or "NOT_QUEUED").upper()
    pinned = bool(row.get("pinned"))
    active = transfer_status in ACTIVE_TRANSFER_STATUSES
    eligible = bool(deadline and deadline <= now and not active and not pinned and not row.get("deleted_at"))
    output = {
        **row,
        "transfer_status": transfer_status,
        "retentionDeadline": deadline.isoformat() if deadline else None,
        "retentionPolicy": policy,
        "retentionPolicyKey": policy_key,
        "autoCleanupEnabled": auto_cleanup_enabled,
        "cleanupEligible": eligible,
        "autoCleanupEligible": eligible and auto_cleanup_enabled,
        "deleteBlockedReason": (
            "활성 전송 작업" if active else "보존 지정" if pinned else "보존 기한 미도래" if deadline else "자동 정리 제외"
        ) if not eligible else None,
    }
    if include_artifacts:
        artifacts = [_artifact_state(settings, item) for item in row.get("artifacts", [])]
        output["artifacts"] = artifacts
        output["missingArtifactCount"] = sum(
            1 for item in artifacts if not item.get("deleted_at") and not item["exists"]
        )
        output["sizeMismatchCount"] = sum(
            1 for item in artifacts if item["exists"] and not item["sizeMatches"]
        )
    return output


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _trash_report(
    *,
    settings: Any,
    database: Database,
    row: dict[str, Any],
    actor: str,
    reason: str,
    policies: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    decorated = _decorate_row(
        row,
        settings,
        include_artifacts=True,
        policies=policies,
    )
    if not decorated["cleanupEligible"]:
        raise HTTPException(
            status_code=409,
            detail=f"{row['report_id']}: {decorated['deleteBlockedReason']} 때문에 정리할 수 없습니다.",
        )
    root = _storage_root(settings)
    trash_root = root / ".report-trash" / str(row["report_id"])
    report_id = str(row["report_id"])
    records: list[tuple[str, str]] = []
    destinations: dict[Path, str] = {}
    physically_moved: list[tuple[Path, Path]] = []
    retained_shared = 0
    try:
        for artifact in decorated.get("artifacts", []):
            if artifact.get("deleted_at"):
                continue
            artifact_id = str(artifact["artifact_id"])
            relative_path = str(artifact["relative_path"])
            source = _safe_storage_path(settings, relative_path)
            if not source.is_file():
                records.append((artifact_id, ""))
                continue
            if source in destinations:
                records.append((artifact_id, destinations[source]))
                continue
            reference_counter = getattr(database, "count_active_artifact_references", None)
            other_references = (
                int(reference_counter(relative_path, excluding_report_id=report_id))
                if reference_counter is not None
                else 0
            )
            if other_references:
                destinations[source] = ""
                records.append((artifact_id, ""))
                retained_shared += 1
                continue
            destination = trash_root / artifact_id / source.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(destination))
            trash_relative = destination.relative_to(root).as_posix()
            destinations[source] = trash_relative
            physically_moved.append((source, destination))
            records.append((artifact_id, trash_relative))
        database.mark_report_trashed(
            report_id,
            deleted_by=actor.strip() or "운영 관리자",
            reason=reason.strip() or "보존 정책에 따른 정리",
            artifacts=records,
        )
    except Exception:
        for source, destination in reversed(physically_moved):
            if destination.is_file() and not source.exists():
                source.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(destination), str(source))
        raise
    return {
        "reportId": report_id,
        "movedFiles": len(physically_moved),
        "retainedSharedFiles": retained_shared,
        "trashed": True,
    }


def cleanup_expired_reports(*, settings: Any, database: Database, actor: str = "system") -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    policies = _load_retention_policies(settings, database)
    for summary in database.list_report_management(include_deleted=False, limit=1000):
        if not _decorate_row(
            summary,
            settings,
            include_artifacts=False,
            policies=policies,
        )["autoCleanupEligible"]:
            continue
        detail = database.fetch_report_management(str(summary["report_id"]))
        if detail:
            results.append(
                _trash_report(
                    settings=settings,
                    database=database,
                    row=detail,
                    actor=actor,
                    reason="자동 보존 정책 만료",
                    policies=policies,
                )
            )
    _purge_old_trash(settings, policies=policies)
    return results


def _purge_old_trash(
    settings: Any,
    *,
    policies: dict[str, dict[str, Any]] | None = None,
) -> int:
    trash_policy = (policies or _default_retention_policies(settings))["TRASH"]
    if not bool(trash_policy["auto_cleanup_enabled"]):
        return 0
    root = _storage_root(settings) / ".report-trash"
    if not root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(1, int(trash_policy["retention_days"]))
    )
    removed = 0
    for child in root.iterdir():
        try:
            modified = datetime.fromtimestamp(child.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                shutil.rmtree(child, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed


@router.get("/report-management", response_class=HTMLResponse, include_in_schema=False)
def report_management_console() -> HTMLResponse:
    html_path = Path(__file__).with_name("report_management.html")
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@router.get("/api/v1/report-management/policies", tags=["report-management"])
def retention_policies(request: Request) -> dict[str, Any]:
    return _retention_policy_response(_settings(request), _database(request))


@router.put("/api/v1/report-management/policies", tags=["report-management"])
def update_retention_policies(
    request: Request,
    payload: RetentionPolicyUpdateRequest,
) -> dict[str, Any]:
    expected = set(RETENTION_POLICY_DEFINITIONS)
    normalized = {key.upper(): value for key, value in payload.policies.items()}
    supplied = set(normalized)
    if supplied != expected or len(payload.policies) != len(expected):
        missing = sorted(expected - supplied)
        unknown = sorted(supplied - expected)
        details: list[str] = []
        if missing:
            details.append("누락: " + ", ".join(missing))
        if unknown:
            details.append("지원하지 않음: " + ", ".join(unknown))
        raise HTTPException(
            status_code=422,
            detail="보존 정책 구성이 올바르지 않습니다. " + "; ".join(details),
        )
    settings = _settings(request)
    database = _database(request)
    _load_retention_policies(settings, database)
    database.update_report_retention_policies(
        {
            key: {
                "retention_days": value.retention_days,
                "auto_cleanup_enabled": value.auto_cleanup_enabled,
            }
            for key, value in normalized.items()
        },
        actor=payload.actor.strip() or "운영 관리자",
    )
    return _retention_policy_response(settings, database)


@router.get("/api/v1/report-management", tags=["report-management"])
def list_reports(
    request: Request,
    q: str = Query(default=""),
    experiment_code: str = Query(default="", alias="experimentCode"),
    transfer_status: str = Query(default="", alias="transferStatus"),
    include_deleted: bool = Query(default=False, alias="includeDeleted"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=25, alias="pageSize", ge=1, le=100),
    limit: int | None = Query(default=None, ge=1, le=1000),
) -> dict[str, Any]:
    database = _database(request)
    settings = _settings(request)
    policies = _load_retention_policies(settings, database)
    effective_page = 1 if limit is not None else page
    effective_size = limit if limit is not None else page_size
    filters = {
        "query": q,
        "experiment_code": experiment_code,
        "transfer_status": transfer_status,
        "include_deleted": include_deleted,
    }
    total = database.count_report_management(**filters)
    rows = database.list_report_management(
        **filters,
        limit=effective_size,
        offset=(effective_page - 1) * effective_size,
    )
    items = [
        _decorate_row(
            row,
            settings,
            include_artifacts=False,
            policies=policies,
        )
        for row in rows
    ]
    total_pages = max(1, (total + effective_size - 1) // effective_size)
    return {
        "items": items,
        "count": len(items),
        "total": total,
        "page": effective_page,
        "pageSize": effective_size,
        "totalPages": total_pages,
    }


@router.get("/api/v1/report-management/summary", tags=["report-management"])
def report_summary(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    database = _database(request)
    rows = database.list_report_management(include_deleted=False, limit=1000)
    projects: dict[str, dict[str, int]] = {}
    total_size = 0
    missing = 0
    known: set[str] = set()
    for row in rows:
        code = str(row.get("experiment_code") or "UNKNOWN")
        size = int(row.get("artifact_size_bytes") or 0)
        total_size += size
        bucket = projects.setdefault(code, {"count": 0, "sizeBytes": 0})
        bucket["count"] += 1
        bucket["sizeBytes"] += size
        detail = database.fetch_report_management(str(row["report_id"]))
        for artifact in (detail or {}).get("artifacts", []):
            if artifact.get("deleted_at"):
                continue
            relative = str(artifact.get("relative_path") or "")
            known.add(relative)
            try:
                exists = _safe_storage_path(settings, relative).is_file()
            except HTTPException:
                exists = False
            if not exists:
                missing += 1
    orphan_files = _orphan_files(settings, known)
    physical_size, trash_size = _storage_usage(settings)
    return {
        "reportCount": len(rows),
        "totalSizeBytes": total_size,
        "physicalStorageBytes": physical_size,
        "trashSizeBytes": trash_size,
        "missingFileCount": missing,
        "orphanFileCount": len(orphan_files),
        "projects": projects,
        "orphanFiles": orphan_files[:200],
    }


def _storage_usage(settings: Any) -> tuple[int, int]:
    root = _storage_root(settings)
    trash_root = root / ".report-trash"
    total = 0
    trash = 0
    if not root.is_dir():
        return total, trash
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
            total += size
            if trash_root == path or trash_root in path.parents:
                trash += size
        except OSError:
            continue
    return total, trash


def _orphan_files(settings: Any, known: set[str]) -> list[dict[str, Any]]:
    root = _storage_root(settings)
    reports_root = root / "web-reports"
    if not reports_root.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for path in reports_root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        if relative not in known:
            results.append({"relativePath": relative, "sizeBytes": path.stat().st_size})
    return results


@router.get("/api/v1/report-management/cleanup-preview", tags=["report-management"])
def cleanup_preview(request: Request) -> dict[str, Any]:
    settings = _settings(request)
    database = _database(request)
    policies = _load_retention_policies(settings, database)
    items = [
        _decorate_row(
            row,
            settings,
            include_artifacts=False,
            policies=policies,
        )
        for row in database.list_report_management(include_deleted=False, limit=1000)
    ]
    candidates = [item for item in items if item["autoCleanupEligible"]]
    return {
        "items": candidates,
        "count": len(candidates),
        "sizeBytes": sum(int(item.get("artifact_size_bytes") or 0) for item in candidates),
    }


@router.get("/api/v1/report-management/artifacts/{artifact_id}/download", tags=["report-management"])
def download_artifact(request: Request, artifact_id: str) -> Response:
    artifact = _database(request).fetch_report_artifact(artifact_id)
    if artifact is None or artifact.get("deleted_at"):
        raise HTTPException(status_code=404, detail="보고서 파일을 찾을 수 없습니다.")
    path = _safe_storage_path(_settings(request), str(artifact["relative_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="DB에는 기록되어 있으나 실제 파일이 없습니다.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if path.suffix.lower() in {".html", ".htm"}:
        html_text = path.read_text(encoding="utf-8")
        portable_html = make_xrd_html_portable(html_text)
        if portable_html != html_text:
            file_name = str(artifact.get("file_name") or path.name).replace('"', "")
            return HTMLResponse(
                portable_html,
                headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
            )
    return FileResponse(path, media_type=media_type, filename=str(artifact.get("file_name") or path.name))


@router.get("/api/v1/report-management/{report_id}", tags=["report-management"])
def report_detail(request: Request, report_id: str) -> dict[str, Any]:
    database = _database(request)
    settings = _settings(request)
    row = database.fetch_report_management(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="보고서 생성 기록을 찾을 수 없습니다.")
    return _decorate_row(
        row,
        settings,
        include_artifacts=True,
        policies=_load_retention_policies(settings, database),
    )


@router.patch("/api/v1/report-management/{report_id}", tags=["report-management"])
def update_report(request: Request, report_id: str, payload: ReportLifecycleUpdate) -> dict[str, Any]:
    database = _database(request)
    if database.fetch_report_management(report_id) is None:
        raise HTTPException(status_code=404, detail="보고서 생성 기록을 찾을 수 없습니다.")
    database.update_report_lifecycle(
        report_id,
        is_test=payload.is_test,
        pinned=payload.pinned,
        retention_until=payload.retention_until,
        update_retention=payload.update_retention,
    )
    row = database.fetch_report_management(report_id)
    settings = _settings(request)
    return _decorate_row(
        row or {},
        settings,
        include_artifacts=True,
        policies=_load_retention_policies(settings, database),
    )


@router.post("/api/v1/report-management/{report_id}/verify", tags=["report-management"])
def verify_report(request: Request, report_id: str) -> dict[str, Any]:
    row = _database(request).fetch_report_management(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="보고서 생성 기록을 찾을 수 없습니다.")
    results: list[dict[str, Any]] = []
    for artifact in row.get("artifacts", []):
        if artifact.get("deleted_at"):
            continue
        path = _safe_storage_path(_settings(request), str(artifact["relative_path"]))
        actual = _sha256_file(path) if path.is_file() else None
        expected = artifact.get("sha256")
        results.append(
            {
                "artifactId": artifact["artifact_id"],
                "fileName": artifact["file_name"],
                "exists": path.is_file(),
                "expectedSha256": expected,
                "actualSha256": actual,
                "valid": bool(path.is_file() and expected and actual == expected),
            }
        )
    return {
        "reportId": report_id,
        "valid": bool(results) and all(item["valid"] for item in results),
        "artifacts": results,
    }


@router.post("/api/v1/report-management/{report_id}/retry", tags=["report-management"])
def retry_transfer(request: Request, report_id: str) -> dict[str, Any]:
    if not _database(request).retry_report_transfer(report_id):
        raise HTTPException(status_code=409, detail="FAILED 또는 CANCELLED 전송만 재시도할 수 있습니다.")
    return {"reportId": report_id, "status": "PENDING"}


@router.post("/api/v1/report-management/{report_id}/cancel", tags=["report-management"])
def cancel_transfer(request: Request, report_id: str) -> dict[str, Any]:
    if not _database(request).cancel_report_transfer(report_id):
        raise HTTPException(status_code=409, detail="PENDING 또는 RETRY_WAIT 전송만 취소할 수 있습니다.")
    return {"reportId": report_id, "status": "CANCELLED"}


@router.post("/api/v1/report-management/cleanup", tags=["report-management"])
def cleanup_reports(request: Request, payload: ReportCleanupRequest) -> dict[str, Any]:
    settings = _settings(request)
    database = _database(request)
    policies = _load_retention_policies(settings, database)
    rows: list[dict[str, Any]] = []
    blocked: list[str] = []
    for report_id in dict.fromkeys(payload.report_ids):
        row = database.fetch_report_management(report_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"{report_id}: 보고서 기록을 찾을 수 없습니다.")
        decorated = _decorate_row(
            row,
            settings,
            include_artifacts=False,
            policies=policies,
        )
        if not decorated["cleanupEligible"]:
            blocked.append(f"{report_id}: {decorated['deleteBlockedReason']}")
        rows.append(row)
    if blocked:
        raise HTTPException(
            status_code=409,
            detail="선택한 보고서 중 정리할 수 없는 항목이 있습니다. 파일은 이동하지 않았습니다. "
            + "; ".join(blocked),
        )
    results: list[dict[str, Any]] = []
    for row in rows:
        results.append(
            _trash_report(
                settings=settings,
                database=database,
                row=row,
                actor=payload.actor,
                reason=payload.reason,
                policies=policies,
            )
        )
    purged = _purge_old_trash(settings, policies=policies)
    return {"items": results, "count": len(results), "purgedTrashDirectories": purged}
