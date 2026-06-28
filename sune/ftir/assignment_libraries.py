"""File-backed FT-IR peak assignment libraries."""

from __future__ import annotations

import csv
from dataclasses import dataclass
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
from typing import Any


SUPPORTED_SUFFIXES = {".csv", ".json"}
MAX_LIBRARY_BYTES = 2 * 1024 * 1024
MAX_ASSIGNMENTS = 2_000
DEFAULT_LIBRARY_ID = "general-ftir"
_LIBRARY_LOCK = threading.RLock()
_HEX_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_SAFE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,79}$")


class AssignmentLibraryError(ValueError):
    """A user-correctable assignment-library error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PeakAssignment:
    center_wn: float
    tolerance: float
    name: str
    color: str = "#64748b"
    note: str = ""


@dataclass(frozen=True)
class AssignmentLibrary:
    library_id: str
    name: str
    description: str
    filename: str
    assignments: tuple[PeakAssignment, ...]

    def summary(
        self,
        *,
        valid: bool = True,
        error: str = "",
        default_library_id: str = DEFAULT_LIBRARY_ID,
    ) -> dict[str, Any]:
        return {
            "id": self.library_id,
            "name": self.name,
            "description": self.description,
            "fileName": self.filename,
            "assignmentCount": len(self.assignments),
            "defaultSelected": self.library_id == default_library_id,
            "valid": valid,
            "error": error,
        }

    def as_func_groups(self) -> list[tuple]:
        return [
            (
                item.center_wn,
                item.tolerance,
                item.name,
                item.color,
                item.note,
                self.library_id,
                self.name,
            )
            for item in self.assignments
        ]

    def detail(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "assignments": [
                {
                    "centerWavenumber": item.center_wn,
                    "tolerance": item.tolerance,
                    "name": item.name,
                    "color": item.color,
                    "note": item.note,
                }
                for item in self.assignments
            ],
        }


def _library_id(filename: str) -> str:
    stem = Path(filename).stem.casefold()
    value = re.sub(r"[^a-z0-9]+", "-", stem).strip("-")
    if not value or not _SAFE_ID.fullmatch(value):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY_FILENAME",
            "라이브러리 파일명은 영문자, 숫자, 하이픈 조합이어야 합니다.",
        )
    return value


def _inferred_name(library_id: str) -> str:
    return " ".join(part.upper() if part == "ftir" else part.title()
                    for part in library_id.split("-"))


def _number(value: Any, field: str, row_number: int) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"{row_number}번 항목의 {field} 값이 숫자가 아닙니다.",
        ) from exc


def _plain_text(value: Any, field: str, max_length: int) -> str:
    text = str(value or "").strip()
    if len(text) > max_length or any(char in text for char in "<\x00"):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"{field}에는 HTML 시작 문자(<)를 사용할 수 없고 {max_length}자 이하여야 합니다.",
        )
    return text


def _assignment(item: dict[str, Any], row_number: int) -> PeakAssignment:
    center = _number(
        item.get("centerWavenumber", item.get("center_wn")),
        "centerWavenumber",
        row_number,
    )
    tolerance = _number(item.get("tolerance"), "tolerance", row_number)
    name = _plain_text(item.get("name", ""), "피크 이름", 200)
    color = str(item.get("color", "#64748b")).strip() or "#64748b"
    note = _plain_text(item.get("note", ""), "피크 note", 1_000)
    if not 0 < center <= 10_000:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"{row_number}번 항목의 중심 파수는 0보다 크고 10000 이하여야 합니다.",
        )
    if not 0 < tolerance <= 1_000:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"{row_number}번 항목의 tolerance는 0보다 크고 1000 이하여야 합니다.",
        )
    if not name:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"{row_number}번 항목의 피크 이름은 1~200자여야 합니다.",
        )
    if not _HEX_COLOR.fullmatch(color):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"{row_number}번 항목의 color는 #RRGGBB 형식이어야 합니다.",
        )
    return PeakAssignment(center, tolerance, name, color.lower(), note)


def _parse_json(filename: str, text: str) -> AssignmentLibrary:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            f"JSON 형식이 올바르지 않습니다: {exc.msg}",
        ) from exc
    if not isinstance(payload, dict):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            "라이브러리 JSON 최상위 값은 객체여야 합니다.",
        )
    rows = payload.get("assignments")
    if not isinstance(rows, list):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            "라이브러리 JSON에 assignments 배열이 필요합니다.",
        )
    library_id = _library_id(filename)
    name = (
        _plain_text(payload.get("name", ""), "라이브러리 이름", 120)
        or _inferred_name(library_id)
    )
    description = _plain_text(
        payload.get("description", ""),
        "라이브러리 설명",
        1_000,
    )
    assignments = tuple(
        _assignment(row, index)
        for index, row in enumerate(rows, start=1)
        if isinstance(row, dict)
    )
    return _validated_library(
        library_id, name, description, filename, assignments, len(rows)
    )


def _parse_csv(filename: str, text: str) -> AssignmentLibrary:
    filtered = "\n".join(
        line for line in text.splitlines()
        if not line.lstrip().startswith(("#", "//"))
    )
    reader = csv.DictReader(StringIO(filtered))
    required = {"center_wn", "tolerance", "name"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            "CSV에는 center_wn, tolerance, name 컬럼이 필요합니다.",
        )
    rows = list(reader)
    assignments = tuple(
        _assignment(row, index)
        for index, row in enumerate(rows, start=1)
    )
    library_id = _library_id(filename)
    return _validated_library(
        library_id,
        _inferred_name(library_id),
        "",
        filename,
        assignments,
        len(rows),
    )


def _validated_library(
    library_id: str,
    name: str,
    description: str,
    filename: str,
    assignments: tuple[PeakAssignment, ...],
    source_count: int,
) -> AssignmentLibrary:
    if source_count != len(assignments):
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            "assignments의 모든 항목은 객체여야 합니다.",
        )
    if not assignments:
        raise AssignmentLibraryError(
            "EMPTY_ASSIGNMENT_LIBRARY",
            "피크 assignment 항목이 하나 이상 필요합니다.",
        )
    if len(assignments) > MAX_ASSIGNMENTS:
        raise AssignmentLibraryError(
            "TOO_MANY_ASSIGNMENTS",
            f"라이브러리 하나에는 최대 {MAX_ASSIGNMENTS}개 항목을 넣을 수 있습니다.",
        )
    if not name or len(name) > 120:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            "라이브러리 이름은 1~120자여야 합니다.",
        )
    if len(description) > 1_000:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY",
            "라이브러리 설명은 1000자 이하여야 합니다.",
        )
    return AssignmentLibrary(
        library_id=library_id,
        name=name,
        description=description,
        filename=filename,
        assignments=assignments,
    )


def parse_assignment_library(filename: str, content: bytes) -> AssignmentLibrary:
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY_EXTENSION",
            "피크 assignment 라이브러리는 JSON 또는 CSV 파일이어야 합니다.",
        )
    if not content:
        raise AssignmentLibraryError(
            "EMPTY_ASSIGNMENT_LIBRARY_FILE",
            "빈 라이브러리 파일은 업로드할 수 없습니다.",
        )
    if len(content) > MAX_LIBRARY_BYTES:
        raise AssignmentLibraryError(
            "ASSIGNMENT_LIBRARY_TOO_LARGE",
            "라이브러리 파일은 2MB 이하여야 합니다.",
        )
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise AssignmentLibraryError(
            "INVALID_ASSIGNMENT_LIBRARY_ENCODING",
            "라이브러리 파일은 UTF-8 인코딩이어야 합니다.",
        ) from exc
    if suffix == ".json":
        return _parse_json(filename, text)
    return _parse_csv(filename, text)


class AssignmentLibraryStore:
    """Manage assignment-library files in one writable directory."""

    def __init__(
        self,
        root: Path,
        default_csv: Path,
        *,
        default_library_id: str = DEFAULT_LIBRARY_ID,
    ):
        self.root = Path(root)
        self.default_csv = Path(default_csv)
        self.default_library_id = default_library_id

    def initialize(self) -> None:
        with _LIBRARY_LOCK:
            self.root.mkdir(parents=True, exist_ok=True)
            marker = self.root / ".initialized"
            seeded = self._seeded_defaults(marker)
            bundled_hashes = self._seeded_default_hashes(marker)
            existing_ids = set()
            for path in self.root.iterdir():
                if not path.is_file() or path.suffix.casefold() not in SUPPORTED_SUFFIXES:
                    continue
                try:
                    existing_ids.add(_library_id(path.name))
                except AssignmentLibraryError:
                    continue
            for source, target_name in self._bundled_defaults():
                target = self.root / target_name
                source_hash = _file_sha256(source)
                if target_name in seeded:
                    previous_hash = bundled_hashes.get(target_name)
                    if target.exists() and (
                        previous_hash is None or _file_sha256(target) == previous_hash
                    ):
                        shutil.copyfile(source, target)
                    bundled_hashes[target_name] = source_hash
                    continue
                try:
                    library_id = _library_id(target_name)
                except AssignmentLibraryError:
                    continue
                if library_id in existing_ids:
                    seeded.add(target_name)
                    continue
                shutil.copyfile(source, target)
                existing_ids.add(library_id)
                seeded.add(target_name)
                bundled_hashes[target_name] = source_hash
            marker.write_text(
                json.dumps(
                    {
                        "version": 3,
                        "seeded": sorted(seeded),
                        "bundledHashes": {
                            key: bundled_hashes[key]
                            for key in sorted(bundled_hashes)
                            if key in seeded
                        },
                    },
                    ensure_ascii=True,
                    indent=2,
                )
                + "\n",
                encoding="ascii",
            )

    def _bundled_defaults(self) -> list[tuple[Path, str]]:
        bundled_dir = self.default_csv.parent / "assignment_libraries"
        paths = [(self.default_csv, f"{self.default_library_id}.csv")]
        if bundled_dir.is_dir():
            paths.extend(
                (path, path.name) for path in sorted(bundled_dir.iterdir())
                if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
            )
        return paths

    def _seeded_defaults(self, marker: Path) -> set[str]:
        if not marker.exists():
            return set()
        try:
            payload = json.loads(marker.read_text(encoding="ascii"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {f"{self.default_library_id}.csv"}
        if isinstance(payload, dict):
            seeded = payload.get("seeded")
            if isinstance(seeded, list):
                return {str(item) for item in seeded}
        return {f"{self.default_library_id}.csv"}

    def _seeded_default_hashes(self, marker: Path) -> dict[str, str]:
        if not marker.exists():
            return {}
        try:
            payload = json.loads(marker.read_text(encoding="ascii"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        if isinstance(payload, dict):
            hashes = payload.get("bundledHashes")
            if isinstance(hashes, dict):
                return {
                    str(name): str(value)
                    for name, value in hashes.items()
                    if isinstance(value, str)
                }
        return {}

    def _paths(self) -> list[Path]:
        self.initialize()
        return sorted(
            (
                path for path in self.root.iterdir()
                if path.is_file() and path.suffix.casefold() in SUPPORTED_SUFFIXES
            ),
            key=lambda path: path.name.casefold(),
        )

    def summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for path in self._paths():
            try:
                library = parse_assignment_library(path.name, path.read_bytes())
                summaries.append(library.summary(
                    default_library_id=self.default_library_id,
                ))
            except AssignmentLibraryError as exc:
                try:
                    library_id = _library_id(path.name)
                except AssignmentLibraryError:
                    library_id = path.stem
                summaries.append({
                    "id": library_id,
                    "name": _inferred_name(library_id),
                    "description": "",
                    "fileName": path.name,
                    "assignmentCount": 0,
                    "defaultSelected": False,
                    "valid": False,
                    "error": exc.message,
                })
        return summaries

    def load(self, library_ids: list[str]) -> list[AssignmentLibrary]:
        wanted = list(dict.fromkeys(library_ids))
        if not wanted:
            return []
        available = {}
        for path in self._paths():
            try:
                library = parse_assignment_library(path.name, path.read_bytes())
            except AssignmentLibraryError:
                continue
            available[library.library_id] = library
        missing = [library_id for library_id in wanted if library_id not in available]
        if missing:
            raise AssignmentLibraryError(
                "ASSIGNMENT_LIBRARY_NOT_FOUND",
                f"선택한 라이브러리를 찾을 수 없습니다: {', '.join(missing)}",
            )
        return [available[library_id] for library_id in wanted]

    def get(self, library_id: str) -> AssignmentLibrary:
        return self.load([library_id])[0]

    def default_ids(self) -> list[str]:
        return [
            item["id"] for item in self.summaries()
            if item["valid"] and item["defaultSelected"]
        ]

    def save(self, filename: str, content: bytes) -> AssignmentLibrary:
        safe_filename = Path(filename.replace("\\", "/")).name
        library = parse_assignment_library(safe_filename, content)
        suffix = Path(safe_filename).suffix.casefold()
        with _LIBRARY_LOCK:
            existing_ids = {item["id"] for item in self.summaries()}
            if library.library_id in existing_ids:
                raise AssignmentLibraryError(
                    "ASSIGNMENT_LIBRARY_EXISTS",
                    f"같은 ID의 라이브러리가 이미 있습니다: {library.library_id}",
                )
            target = self.root / f"{library.library_id}{suffix}"
            fd, temporary_name = tempfile.mkstemp(
                prefix=".upload-",
                suffix=suffix,
                dir=self.root,
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, target)
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return parse_assignment_library(target.name, target.read_bytes())

    def write(
        self,
        library_id: str,
        payload: dict[str, Any],
        *,
        create_only: bool,
    ) -> AssignmentLibrary:
        if not _SAFE_ID.fullmatch(library_id):
            raise AssignmentLibraryError(
                "INVALID_ASSIGNMENT_LIBRARY_ID",
                "라이브러리 ID는 영문 소문자, 숫자, 하이픈 조합이어야 합니다.",
            )
        filename = f"{library_id}.json"
        content = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")
        library = parse_assignment_library(filename, content)
        with _LIBRARY_LOCK:
            existing_paths = []
            for path in self._paths():
                try:
                    if _library_id(path.name) == library_id:
                        existing_paths.append(path)
                except AssignmentLibraryError:
                    continue
            if create_only and existing_paths:
                raise AssignmentLibraryError(
                    "ASSIGNMENT_LIBRARY_EXISTS",
                    f"같은 ID의 라이브러리가 이미 있습니다: {library_id}",
                )
            if not create_only and not existing_paths:
                raise AssignmentLibraryError(
                    "ASSIGNMENT_LIBRARY_NOT_FOUND",
                    f"라이브러리를 찾을 수 없습니다: {library_id}",
                )
            target = self.root / filename
            fd, temporary_name = tempfile.mkstemp(
                prefix=".edit-",
                suffix=".json",
                dir=self.root,
            )
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary_name, target)
                for path in existing_paths:
                    if path != target:
                        path.unlink()
            finally:
                if os.path.exists(temporary_name):
                    os.unlink(temporary_name)
        return parse_assignment_library(target.name, target.read_bytes())

    def delete(self, library_id: str) -> None:
        if not _SAFE_ID.fullmatch(library_id):
            raise AssignmentLibraryError(
                "INVALID_ASSIGNMENT_LIBRARY_ID",
                "유효하지 않은 라이브러리 ID입니다.",
            )
        with _LIBRARY_LOCK:
            for path in self._paths():
                try:
                    current_id = _library_id(path.name)
                except AssignmentLibraryError:
                    continue
                if current_id == library_id:
                    path.unlink()
                    return
        raise AssignmentLibraryError(
            "ASSIGNMENT_LIBRARY_NOT_FOUND",
            f"라이브러리를 찾을 수 없습니다: {library_id}",
        )


def flatten_assignment_libraries(
    libraries: list[AssignmentLibrary],
) -> list[tuple]:
    return [
        item
        for library in libraries
        for item in library.as_func_groups()
    ]


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()
