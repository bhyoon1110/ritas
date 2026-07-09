"""CLI processor for AHN TEM/STEM/EDS/coating-layer report generation."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Any, Callable

from .analysis import collect_project, write_project_json

ProgressCallback = Callable[[str, int, str], None]


def _emit_progress(
    callback: ProgressCallback | None,
    stage: str,
    progress_pct: int,
    message: str,
) -> None:
    if callback is not None:
        callback(stage, progress_pct, message)


def _copy_spreadsheets(project, output_dir: Path) -> list[str]:
    package_dir = output_dir / "raw"
    package_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    root = Path(project.input_root)
    for item in project.spreadsheets:
        source = root / item.path
        if not source.exists():
            continue
        target = package_dir / Path(item.path)
        target.parent.mkdir(parents=True, exist_ok=True)
        base_target = target
        index = 1
        while target.exists():
            target = base_target.with_name(f"{base_target.stem}_{index}{base_target.suffix}")
            index += 1
        shutil.copy2(source, target)
        copied.append(target.relative_to(output_dir).as_posix())
    return copied


def build_outputs(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    pptx_path: str | Path | None = None,
    copy_raw_spreadsheets: bool = True,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    _emit_progress(
        progress_callback,
        "collect",
        35,
        "TEM/STEM/EDS 폴더 구조와 코팅층 두께 라벨을 분석하는 중입니다.",
    )
    project = collect_project(input_dir)
    _emit_progress(
        progress_callback,
        "json",
        58,
        "분석 JSON과 원본 엑셀 첨부파일을 정리하는 중입니다.",
    )
    copied_spreadsheets = _copy_spreadsheets(project, output) if copy_raw_spreadsheets else []
    analysis_path = write_project_json(project, output / "analysis-result.json")

    rendered_pptx = None
    if pptx_path is not None:
        from .ppt_report import build_pptx

        _emit_progress(
            progress_callback,
            "pptx",
            72,
            "PowerPoint 보고서를 렌더링하는 중입니다.",
        )
        rendered_pptx = build_pptx(project, pptx_path)
        _emit_progress(
            progress_callback,
            "pptx",
            86,
            "PowerPoint 보고서 저장을 완료했습니다.",
        )

    manifest = {
        "analysisJson": analysis_path.relative_to(output).as_posix(),
        "pptx": str(rendered_pptx) if rendered_pptx else None,
        "copiedSpreadsheets": copied_spreadsheets,
        "summary": project.summary,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build AHN TEM/STEM/EDS report inputs.")
    parser.add_argument("--input", required=True, help="AHN bundle input directory")
    parser.add_argument("--output", required=True, help="Output/processed directory")
    parser.add_argument(
        "--pptx",
        help="Optional output PPTX path. If omitted, only analysis-result.json is written.",
    )
    parser.add_argument(
        "--no-copy-raw-spreadsheets",
        action="store_true",
        help="Do not copy EDS raw spreadsheet files into the output/raw folder.",
    )
    args = parser.parse_args(argv)

    manifest = build_outputs(
        input_dir=args.input,
        output_dir=args.output,
        pptx_path=args.pptx,
        copy_raw_spreadsheets=not args.no_copy_raw_spreadsheets,
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
