"""CLI processor for AHN TEM/STEM/EDS/coating-layer report generation."""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from .analysis import collect_project, write_project_json


def _copy_spreadsheets(project, output_dir: Path) -> list[str]:
    package_dir = output_dir / "raw"
    package_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    root = Path(project.input_root)
    for item in project.spreadsheets:
        source = root / item.path
        if not source.exists():
            continue
        target = package_dir / source.name
        shutil.copy2(source, target)
        copied.append(target.relative_to(output_dir).as_posix())
    return copied


def build_outputs(
    *,
    input_dir: str | Path,
    output_dir: str | Path,
    pptx_path: str | Path | None = None,
    copy_raw_spreadsheets: bool = True,
) -> dict:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    project = collect_project(input_dir)
    copied_spreadsheets = _copy_spreadsheets(project, output) if copy_raw_spreadsheets else []
    analysis_path = write_project_json(project, output / "analysis-result.json")

    rendered_pptx = None
    if pptx_path is not None:
        from .ppt_report import build_pptx

        rendered_pptx = build_pptx(project, pptx_path)

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
