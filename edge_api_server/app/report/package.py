from __future__ import annotations

import zipfile
from pathlib import Path


_INTERNAL_REPORT_SUFFIXES = {".json", ".zip"}


def build_report_package(
    report_dir: Path,
    input_dir: Path,
    *,
    include_raw_files: bool,
) -> Path:
    """Spring Boot 전달용 최종 결과 ZIP을 만들고 내부 JSON은 제외한다."""
    package_path = report_dir / "report-package.zip"
    with zipfile.ZipFile(package_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(report_dir.rglob("*")):
            if not path.is_file() or path == package_path:
                continue
            if path.suffix.lower() in _INTERNAL_REPORT_SUFFIXES:
                continue
            archive.write(path, path.relative_to(report_dir).as_posix())
        if include_raw_files and input_dir.exists():
            for path in sorted(input_dir.rglob("*")):
                if path.is_file():
                    archive.write(
                        path,
                        (Path("raw") / path.relative_to(input_dir)).as_posix(),
                    )
    return package_path
