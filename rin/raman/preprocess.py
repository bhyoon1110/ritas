"""Raman raw file loading and preprocessing helpers."""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.interpolate import interp1d
from scipy.signal import savgol_filter


class RamanRawError(ValueError):
    """User-correctable Raman raw parsing error."""


SUPPORTED_SUFFIXES = {".txt", ".csv", ".tsv", ".xlsx", ".xlsm"}


@dataclass(frozen=True)
class RamanRawSample:
    label: str | None
    frame: pd.DataFrame
    metadata: dict[str, str] | None = None


def _safe_window(length: int, requested: int, polyorder: int) -> int:
    if length <= polyorder + 2:
        return 0
    window = min(requested, length if length % 2 else length - 1)
    window = max(polyorder + 2, window)
    if window % 2 == 0:
        window += 1
    return window if window <= length else window - 2


def _clean_numeric_pair(shift: pd.Series, intensity: pd.Series) -> pd.DataFrame:
    result = pd.DataFrame({"shift": shift, "intensity": intensity})
    result = result.dropna()
    result = result.replace([np.inf, -np.inf], np.nan).dropna()
    if len(result) < 10:
        raise RamanRawError("유효한 Raman 데이터 포인트가 부족합니다.")
    return result.sort_values("shift").drop_duplicates("shift")


def _sample_label_from_columns(shift_name: str, intensity_name: str) -> str | None:
    shift_name = str(shift_name).strip()
    intensity_name = str(intensity_name).strip()
    if intensity_name and not intensity_name.startswith("Unnamed:"):
        return intensity_name
    if shift_name.startswith("wn(") and shift_name.endswith(")"):
        return shift_name[3:-1].strip() or None
    return None


def _looks_like_shift_column(name: str) -> bool:
    normalized = str(name).strip().casefold()
    return (
        normalized.startswith("wn")
        or "shift" in normalized
        or "wavenumber" in normalized
        or normalized in {"x", "raman"}
    )


def _numeric_samples(
    frame: pd.DataFrame,
    *,
    metadata: dict[str, str] | None = None,
) -> list[RamanRawSample]:
    numeric: list[tuple[str, pd.Series]] = []
    for column in frame.columns:
        series = pd.to_numeric(frame[column], errors="coerce")
        if series.notna().sum() >= 10:
            numeric.append((str(column), series))
    if len(numeric) < 2:
        raise RamanRawError("숫자형 Raman shift/intensity 2열을 찾을 수 없습니다.")

    samples: list[RamanRawSample] = []
    if (
        len(numeric) > 2
        and _looks_like_shift_column(numeric[0][0])
        and not any(_looks_like_shift_column(name) for name, _series in numeric[1:])
    ):
        shift_name, shift = numeric[0]
        for intensity_name, intensity in numeric[1:]:
            try:
                sample = _clean_numeric_pair(shift, intensity)
            except RamanRawError:
                continue
            samples.append(
                RamanRawSample(
                    label=_sample_label_from_columns(shift_name, intensity_name),
                    frame=sample,
                    metadata=metadata,
                )
            )
        if samples:
            return samples

    for index in range(0, len(numeric) - 1, 2):
        shift_name, shift = numeric[index]
        intensity_name, intensity = numeric[index + 1]
        try:
            sample = _clean_numeric_pair(shift, intensity)
        except RamanRawError:
            continue
        samples.append(
            RamanRawSample(
                label=_sample_label_from_columns(shift_name, intensity_name),
                frame=sample,
                metadata=metadata,
            )
        )
    if not samples:
        raise RamanRawError("유효한 Raman 데이터 포인트가 부족합니다.")
    return samples


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949", "latin1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return content.decode("utf-8", errors="replace")


def _parse_text_metadata(content: bytes) -> dict[str, str]:
    metadata: dict[str, str] = {}
    for line in _decode_text(content).splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if not stripped.startswith("#"):
            break
        item = stripped.lstrip("#").strip()
        if not item or set(item) <= {"-"}:
            continue
        if ":" in item:
            key, value = item.split(":", 1)
            key = key.strip()
            value = value.strip()
            if key:
                metadata[key] = value or "(미기재)"
        else:
            metadata.setdefault("Notes", item)
    return metadata


def _read_text_table(content: bytes) -> list[RamanRawSample]:
    metadata = _parse_text_metadata(content)
    last_error: Exception | None = None
    for kwargs in (
        {"sep": "\t", "engine": "python", "comment": "#"},
        {"sep": r"\s+", "engine": "python", "comment": "#"},
        {"sep": None, "engine": "python", "comment": "#"},
        {"sep": r"\s+", "engine": "python", "comment": "#", "header": None},
        {"sep": ",", "engine": "python", "comment": "#"},
    ):
        try:
            frame = pd.read_csv(BytesIO(content), **kwargs)
            return _numeric_samples(frame, metadata=metadata)
        except Exception as exc:
            last_error = exc
    raise RamanRawError(f"텍스트 Raman raw 파일을 읽을 수 없습니다: {last_error}")


def _read_excel_table(content: bytes) -> list[RamanRawSample]:
    try:
        import openpyxl  # noqa: F401
    except ModuleNotFoundError as exc:
        raise RamanRawError(
            "Excel Raman 파일을 읽으려면 openpyxl 의존성이 필요합니다."
        ) from exc
    try:
        frame = pd.read_excel(BytesIO(content), sheet_name=0)
    except Exception as exc:
        raise RamanRawError(f"Excel Raman raw 파일을 읽을 수 없습니다: {exc}") from exc
    return _numeric_samples(frame, metadata={})


def load_raman_raw_samples(
    filename: str,
    content: bytes,
    *,
    shift_min: float = 0.0,
    shift_max: float = 4000.0,
) -> list[RamanRawSample]:
    suffix = Path(filename).suffix.casefold()
    if suffix not in SUPPORTED_SUFFIXES:
        raise RamanRawError(
            "지원하지 않는 Raman raw 확장자입니다. txt/csv/tsv/xlsx를 사용하세요."
        )
    raw_samples = (
        _read_excel_table(content)
        if suffix in {".xlsx", ".xlsm"}
        else _read_text_table(content)
    )
    samples: list[RamanRawSample] = []
    for sample in raw_samples:
        frame = sample.frame
        frame = frame[(frame["shift"] >= shift_min) & (frame["shift"] <= shift_max)]
        if len(frame) >= 10:
            samples.append(
                RamanRawSample(
                    label=sample.label,
                    frame=frame,
                    metadata=sample.metadata,
                )
            )
    if not samples:
        raise RamanRawError("분석 범위 안의 Raman 데이터 포인트가 부족합니다.")
    return samples


def load_raman_raw(
    filename: str,
    content: bytes,
    *,
    shift_min: float = 0.0,
    shift_max: float = 4000.0,
) -> pd.DataFrame:
    return load_raman_raw_samples(
        filename,
        content,
        shift_min=shift_min,
        shift_max=shift_max,
    )[0].frame


def baseline_als(y: np.ndarray, lam: float = 1e5, p: float = 0.01, n_iter: int = 10):
    from scipy.sparse import diags
    from scipy.sparse.linalg import spsolve

    length = len(y)
    if length < 3:
        return np.zeros_like(y)
    diagonal = np.ones(length)
    matrix = diags(
        [diagonal, -2 * diagonal, diagonal],
        [0, 1, 2],
        shape=(length - 2, length),
        format="csc",
    )
    penalty = lam * matrix.T.dot(matrix)
    weights = np.ones(length)
    baseline = np.zeros(length)
    for _ in range(n_iter):
        weight_matrix = diags(weights, 0, shape=(length, length), format="csc")
        baseline = spsolve(weight_matrix + penalty, weights * y)
        weights = p * (y > baseline) + (1 - p) * (y <= baseline)
    return baseline


def preprocess_raman(
    shift: np.ndarray,
    intensity: np.ndarray,
    grid: np.ndarray,
    *,
    smooth: bool = True,
    baseline: bool = True,
    normalize: bool = True,
    smooth_window: int = 11,
    smooth_poly: int = 3,
) -> np.ndarray:
    order = np.argsort(shift)
    shift = np.asarray(shift, dtype=float)[order]
    intensity = np.asarray(intensity, dtype=float)[order]
    interpolator = interp1d(
        shift,
        intensity,
        kind="linear",
        bounds_error=False,
        fill_value=np.nan,
    )
    y_grid = interpolator(grid)
    valid = np.isfinite(y_grid)
    if valid.any():
        y_grid = np.interp(grid, grid[valid], y_grid[valid])
    else:
        y_grid = np.zeros_like(grid)

    if smooth:
        window = _safe_window(len(y_grid), smooth_window, smooth_poly)
        if window:
            y_grid = savgol_filter(y_grid, window_length=window, polyorder=smooth_poly)
    if baseline:
        y_grid = y_grid - baseline_als(y_grid)
    if normalize:
        y_min = float(np.nanmin(y_grid))
        y_max = float(np.nanmax(y_grid))
        if y_max - y_min > 1e-12:
            y_grid = (y_grid - y_min) / (y_max - y_min)
    return y_grid
