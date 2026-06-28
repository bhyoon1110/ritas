#!/usr/bin/env python3
"""Build bundled FT-IR peak assignment libraries from reference spectra."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import re
from typing import Any

import numpy as np
import pandas as pd
from scipy.signal import find_peaks, peak_prominences


SUNE_DIR = Path(__file__).resolve().parents[2]
SOURCE_ROOT = SUNE_DIR / "data" / "RIST_FTIR_Library"
MANIFEST_PATH = SOURCE_ROOT / "manifest.csv"
OUTPUT_DIR = SUNE_DIR / "ftir" / "resources" / "assignment_libraries"

MAX_PEAKS_PER_SPECTRUM = 5
MIN_PEAK_SPACING_CM = 18.0
MIN_PROMINENCE = 0.08
MIN_HEIGHT = 0.10
TOLERANCE_CM = 10.0

MARKER_PALETTE = [
    "#2563eb",
    "#dc2626",
    "#16a34a",
    "#9333ea",
    "#ea580c",
    "#0891b2",
    "#c026d3",
    "#65a30d",
    "#d97706",
    "#4f46e5",
    "#0d9488",
    "#be123c",
    "#7c2d12",
    "#0369a1",
    "#15803d",
    "#a21caf",
    "#b45309",
    "#4338ca",
]

CATEGORY_META = {
    "01_battery/01_electrolyte_solvents": {
        "id": "battery-electrolyte-peaks",
        "name": "Battery Electrolyte Peak Library",
        "description": "대표 배터리 전해질/용매 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#0f766e",
    },
    "01_battery/02_binders_polymers": {
        "id": "battery-binder-polymer-peaks",
        "name": "Battery Binder/Polymer Peak Library",
        "description": "대표 배터리 바인더/폴리머 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#047857",
    },
    "02_steel_coating/01_corrosion_inhibitors": {
        "id": "steel-corrosion-inhibitor-peaks",
        "name": "Steel Corrosion Inhibitor Peak Library",
        "description": "방청제/부식억제제 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#b45309",
    },
    "02_steel_coating/02_lubricants": {
        "id": "steel-lubricant-peaks",
        "name": "Steel Lubricant Peak Library",
        "description": "윤활제 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#c2410c",
    },
    "02_steel_coating/03_coatings_resins": {
        "id": "steel-coating-resin-peaks",
        "name": "Steel Coating/Resin Peak Library",
        "description": "코팅 수지/용매 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#a16207",
    },
    "03_engineering_plastic/01_commodity": {
        "id": "engineering-plastic-commodity-peaks",
        "name": "Commodity Plastic Peak Library",
        "description": "범용 플라스틱 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#2563eb",
    },
    "03_engineering_plastic/02_engineering": {
        "id": "engineering-plastic-engineering-peaks",
        "name": "Engineering Plastic Peak Library",
        "description": "엔지니어링 플라스틱 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#4f46e5",
    },
    "03_engineering_plastic/03_bioplastics": {
        "id": "engineering-plastic-bioplastic-peaks",
        "name": "Bioplastic Peak Library",
        "description": "바이오/셀룰로오스계 플라스틱 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#7c3aed",
    },
    "04_elastomers_seals": {
        "id": "elastomer-seal-peaks",
        "name": "Elastomer/Seal Peak Library",
        "description": "엘라스토머와 씰 소재 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#be123c",
    },
    "05_ceramic_inorganic": {
        "id": "ceramic-inorganic-peaks",
        "name": "Ceramic/Inorganic Peak Library",
        "description": "세라믹/무기 소재 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#475569",
    },
    "06_natural_fibers": {
        "id": "natural-fiber-peaks",
        "name": "Natural Fiber Peak Library",
        "description": "천연 섬유 FT-IR 기준 스펙트럼에서 자동 추출한 피크 후보",
        "color": "#15803d",
    },
}


def safe_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def stable_color(value: str, offset: int = 0) -> str:
    total = 0
    for index, char in enumerate(value.casefold(), start=1):
        total = (total + index * ord(char)) % 104729
    return MARKER_PALETTE[(total + offset) % len(MARKER_PALETTE)]


def load_manifest() -> dict[str, dict[str, str]]:
    if not MANIFEST_PATH.exists():
        return {}
    with MANIFEST_PATH.open(newline="", encoding="utf-8") as handle:
        return {row["file"]: row for row in csv.DictReader(handle)}


def subgroup_for(relative_path: Path) -> str | None:
    parts = relative_path.parts
    if len(parts) < 2:
        return None
    if parts[0] in {"04_elastomers_seals", "05_ceramic_inorganic", "06_natural_fibers"}:
        return parts[0]
    if len(parts) >= 3:
        return "/".join(parts[:2])
    return None


def read_spectrum(path: Path) -> tuple[np.ndarray, np.ndarray]:
    frame = pd.read_csv(path)
    if {"wavenumber", "absorbance"}.issubset(frame.columns):
        wn = frame["wavenumber"]
        y = frame["absorbance"]
    else:
        numeric = frame.select_dtypes(include=["number"])
        if numeric.shape[1] < 2:
            return np.array([]), np.array([])
        wn = numeric.iloc[:, 0]
        y = numeric.iloc[:, 1]
    data = pd.DataFrame({"wn": wn, "y": y}).apply(pd.to_numeric, errors="coerce")
    data = data.dropna()
    data = data[(data["wn"] >= 400) & (data["wn"] <= 4000)]
    if len(data) < 20:
        return np.array([]), np.array([])
    data = data.sort_values("wn").drop_duplicates("wn")
    wn_values = data["wn"].to_numpy(dtype=float)
    y_values = data["y"].to_numpy(dtype=float)
    y_values = y_values - np.nanmin(y_values)
    ymax = float(np.nanmax(y_values)) if len(y_values) else 0.0
    if ymax <= 0:
        return np.array([]), np.array([])
    return wn_values, y_values / ymax


def peak_distance_points(wn: np.ndarray) -> int:
    if len(wn) < 2:
        return 1
    step = float(np.nanmedian(np.abs(np.diff(wn))))
    if step <= 0:
        return 1
    return max(1, int(round(MIN_PEAK_SPACING_CM / step)))


def detect_representative_peaks(path: Path) -> list[float]:
    wn, y = read_spectrum(path)
    if len(wn) < 20:
        return []
    indexes, _ = find_peaks(
        y,
        height=MIN_HEIGHT,
        prominence=MIN_PROMINENCE,
        distance=peak_distance_points(wn),
    )
    if len(indexes) == 0:
        indexes, _ = find_peaks(
            y,
            height=max(0.05, MIN_HEIGHT / 2),
            prominence=max(0.04, MIN_PROMINENCE / 2),
            distance=peak_distance_points(wn),
        )
    if len(indexes) == 0:
        return []
    prominences = peak_prominences(y, indexes)[0]
    strongest = sorted(
        zip(indexes, prominences),
        key=lambda item: (-float(item[1]), float(wn[item[0]])),
    )[:MAX_PEAKS_PER_SPECTRUM]
    return sorted(round(float(wn[index]), 1) for index, _ in strongest)


def material_from_filename(path: Path) -> str:
    stem = path.stem
    stem = re.sub(r"^(figshare|openspecy|nist|existing|zenodo|lit|hap)_", "", stem)
    stem = re.sub(r"_(idx|m|n)\d+$", "", stem)
    stem = re.sub(r"[-_]\d+$", "", stem)
    return safe_text(stem.replace("_", " "))


def build_libraries() -> dict[str, int]:
    manifest = load_manifest()
    grouped: dict[str, list[dict[str, Any]]] = {
        key: [] for key in CATEGORY_META
    }
    skipped = 0
    for path in sorted(SOURCE_ROOT.rglob("*.csv")):
        if path.name == "manifest.csv":
            continue
        relative = path.relative_to(SOURCE_ROOT)
        group_key = subgroup_for(relative)
        if group_key not in grouped:
            skipped += 1
            continue
        manifest_row = manifest.get(str(relative), {})
        material = safe_text(manifest_row.get("material")) or material_from_filename(path)
        source = safe_text(manifest_row.get("source"))
        peaks = detect_representative_peaks(path)
        if not peaks:
            skipped += 1
            continue
        meta = CATEGORY_META[group_key]
        marker_color = stable_color(material, list(CATEGORY_META).index(group_key))
        for wn in peaks:
            grouped[group_key].append({
                "centerWavenumber": wn,
                "tolerance": TOLERANCE_CM,
                "name": f"{material} marker @ {wn:g} cm-1",
                "color": marker_color,
                "note": f"Auto-derived from {relative}" + (f" ({source})" if source else ""),
            })

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {"skipped": skipped}
    for group_key, assignments in grouped.items():
        if not assignments:
            continue
        meta = CATEGORY_META[group_key]
        payload = {
            "name": meta["name"],
            "description": (
                f"{meta['description']}. "
                f"Generated from sune/data/RIST_FTIR_Library; "
                f"{MAX_PEAKS_PER_SPECTRUM} strongest peaks per spectrum."
            ),
            "assignments": assignments,
        }
        target = OUTPUT_DIR / f"{meta['id']}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        counts[meta["id"]] = len(assignments)
    return counts


def main() -> None:
    counts = build_libraries()
    for name, count in sorted(counts.items()):
        print(f"{name}: {count}")


if __name__ == "__main__":
    main()
