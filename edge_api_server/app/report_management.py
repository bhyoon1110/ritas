from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import mimetypes
from pathlib import Path
import shutil
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .database import Database


router = APIRouter()
KST = timezone(timedelta(hours=9))
ACTIVE_TRANSFER_STATUSES = {"PENDING", "PROCESSING", "RETRY_WAIT"}


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


def _retention_deadline(row: dict[str, Any], settings: Any) -> tuple[datetime | None, str]:
    explicit = _parse_datetime(row.get("retention_until"))
    if explicit is not None:
        return explicit, "직접 지정"
    status = str(row.get("transfer_status") or "NOT_QUEUED").upper()
    generated = _parse_datetime(row.get("generated_at") or row.get("created_at"))
    completed = _parse_datetime(row.get("transfer_completed_at") or row.get("updated_at"))
    if bool(row.get("is_test")) and status == "NOT_QUEUED" and generated:
        return generated + timedelta(days=max(1, int(settings.report_test_retention_days))), "미전송 테스트"
    if status in {"FAILED", "CANCELLED"} and completed:
        return completed + timedelta(days=max(1, int(settings.report_failed_retention_days))), status
    if status == "COMPLETED" and completed:
        return completed + timedelta(days=max(1, int(settings.report_completed_retention_days))), "LIMS 전송 완료"
    return None, "자동 정리 제외"


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


def _decorate_row(row: dict[str, Any], settings: Any, *, include_artifacts: bool) -> dict[str, Any]:
    deadline, policy = _retention_deadline(row, settings)
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
        "cleanupEligible": eligible,
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
) -> dict[str, Any]:
    decorated = _decorate_row(row, settings, include_artifacts=True)
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
    for summary in database.list_report_management(include_deleted=False, limit=1000):
        if not _decorate_row(summary, settings, include_artifacts=False)["cleanupEligible"]:
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
                )
            )
    _purge_old_trash(settings)
    return results


def _purge_old_trash(settings: Any) -> int:
    root = _storage_root(settings) / ".report-trash"
    if not root.is_dir():
        return 0
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=max(1, int(settings.report_trash_retention_days))
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


@router.get("/api/v1/report-management", tags=["report-management"])
def list_reports(
    request: Request,
    q: str = Query(default=""),
    experiment_code: str = Query(default="", alias="experimentCode"),
    transfer_status: str = Query(default="", alias="transferStatus"),
    include_deleted: bool = Query(default=False, alias="includeDeleted"),
    limit: int = Query(default=300, ge=1, le=1000),
) -> dict[str, Any]:
    rows = _database(request).list_report_management(
        query=q,
        experiment_code=experiment_code,
        transfer_status=transfer_status,
        include_deleted=include_deleted,
        limit=limit,
    )
    items = [_decorate_row(row, _settings(request), include_artifacts=False) for row in rows]
    return {"items": items, "count": len(items)}


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
    items = [
        _decorate_row(row, settings, include_artifacts=False)
        for row in _database(request).list_report_management(include_deleted=False, limit=1000)
    ]
    candidates = [item for item in items if item["cleanupEligible"]]
    return {
        "items": candidates,
        "count": len(candidates),
        "sizeBytes": sum(int(item.get("artifact_size_bytes") or 0) for item in candidates),
    }


@router.get("/api/v1/report-management/artifacts/{artifact_id}/download", tags=["report-management"])
def download_artifact(request: Request, artifact_id: str) -> FileResponse:
    artifact = _database(request).fetch_report_artifact(artifact_id)
    if artifact is None or artifact.get("deleted_at"):
        raise HTTPException(status_code=404, detail="보고서 파일을 찾을 수 없습니다.")
    path = _safe_storage_path(_settings(request), str(artifact["relative_path"]))
    if not path.is_file():
        raise HTTPException(status_code=404, detail="DB에는 기록되어 있으나 실제 파일이 없습니다.")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=str(artifact.get("file_name") or path.name))


@router.get("/api/v1/report-management/{report_id}", tags=["report-management"])
def report_detail(request: Request, report_id: str) -> dict[str, Any]:
    row = _database(request).fetch_report_management(report_id)
    if row is None:
        raise HTTPException(status_code=404, detail="보고서 생성 기록을 찾을 수 없습니다.")
    return _decorate_row(row, _settings(request), include_artifacts=True)


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
    return _decorate_row(row or {}, _settings(request), include_artifacts=True)


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
    rows: list[dict[str, Any]] = []
    blocked: list[str] = []
    for report_id in dict.fromkeys(payload.report_ids):
        row = database.fetch_report_management(report_id)
        if row is None:
            raise HTTPException(status_code=404, detail=f"{report_id}: 보고서 기록을 찾을 수 없습니다.")
        decorated = _decorate_row(row, settings, include_artifacts=False)
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
            )
        )
    purged = _purge_old_trash(settings)
    return {"items": results, "count": len(results), "purgedTrashDirectories": purged}
