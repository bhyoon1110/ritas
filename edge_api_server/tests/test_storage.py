from __future__ import annotations

from io import BytesIO

import pytest

from app.errors import ApiException
from app.storage import stream_to_temp


def test_stream_to_temp_removes_partial_file_on_size_error(tmp_path) -> None:
    temp_path = tmp_path / "upload.tmp"

    with pytest.raises(ApiException) as raised:
        stream_to_temp(BytesIO(b"abcdef"), temp_path, max_bytes=3)

    assert raised.value.code == "FILE_TOO_LARGE"
    assert not temp_path.exists()
