from __future__ import annotations

import pytest

from app.database import FILE_COLUMNS, JOB_COLUMNS, _validate_columns


def test_validate_columns_rejects_unknown_job_column() -> None:
    with pytest.raises(ValueError) as raised:
        _validate_columns("jobs", {"job_id": "1", "status; DROP TABLE jobs": "x"}, JOB_COLUMNS)

    assert "허용되지 않은 컬럼" in str(raised.value)


def test_validate_columns_accepts_known_file_columns() -> None:
    _validate_columns(
        "files",
        {"file_id": "f1", "job_id": "j1", "relative_path": "raw/a.txt"},
        FILE_COLUMNS,
    )
