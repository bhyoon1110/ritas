"""Keep plotted measurements separate from peak-detection preprocessing."""

import numpy as np


def peaks_on_raw_points(candidates, raw_x, raw_y, *, x_key):
    """Snap analysis candidates to measured points; never invent displayed values.

    Inputs are sorted by X by the loaders. Several analysis-grid candidates can
    map to one measured point: combine their visibility/sensitivity, not peaks.
    FWHM remains an analysis estimate, not a width measured on the raw trace.
    """
    xs, ys = np.asarray(raw_x, dtype=float), np.asarray(raw_y, dtype=float)
    merged = {}
    for candidate in candidates:
        x = candidate[x_key]
        if not len(xs) or x < xs[0] or x > xs[-1]:
            continue
        right = min(int(np.searchsorted(xs, x)), len(xs) - 1)
        left = max(0, right - 1)
        index = left if abs(xs[left] - x) <= abs(xs[right] - x) else right
        if index in merged:
            item = merged[index]
            item["initial"] = item["initial"] or candidate["initial"]
            item["sensitivity_min"] = min(item["sensitivity_min"], candidate["sensitivity_min"])
            item["levels"] = sorted(set(item["levels"]) | set(candidate["levels"]))
            continue
        merged[index] = {
            **candidate,
            "index": index,
            x_key: float(xs[index]),
            "raw_value": float(ys[index]),
        }
    return list(merged.values())
