from io import BytesIO, StringIO

import numpy as np
import pandas as pd
import pytest

from ftir.preprocess import load_dpt


@pytest.mark.parametrize("separator", [",", "\t", " ", "   ", " \t "])
@pytest.mark.parametrize("source_type", ["path", "bytes", "text"])
def test_dpt_delimiters_preserve_numeric_pairs(tmp_path, separator, source_type):
    # Descending wavenumbers, CRLF and scientific notation match instrument exports.
    pairs = [
        (4100.0, 1.0), (3996.32866, 0.0047721), (1000.0, -0.002),
        (698.20028, 0.0129913), (400.0, 0.0), (399.0, 2.0),
    ]
    text = "\ufeff# Resolution: 4 cm-1\r\nMeasurement Mode: ATR\r\n\r\n"
    text += "\r\n".join(f"{x:.8e}{separator}{y:.8e}" for x, y in pairs)
    if source_type == "path":
        source = tmp_path / "sample.DPT"
        source.write_bytes(text.encode("utf-8"))
    elif source_type == "bytes":
        source = BytesIO(text.encode("utf-8"))
    else:
        source = StringIO(text)

    result = load_dpt(source, 400, 4000)

    np.testing.assert_allclose(result.to_numpy(), sorted(pairs[1:-1]), rtol=1e-14)
    assert len(result) == 4
    if source_type != "path":
        assert not source.closed


def test_dpt_keeps_csv_quoting_and_does_not_shift_missing_intensities():
    result = load_dpt(BytesIO(b'"1000","0.1"\n1100,\n1200,0.3\n'), 400, 4000)
    np.testing.assert_array_equal(result.to_numpy(), [[1000, 0.1], [1200, 0.3]])


@pytest.mark.parametrize("separator", [",", "\t", "   "])
def test_dpt_rejects_extra_columns_instead_of_shifting_axes(separator):
    content = f"1000{separator}0.1\n1100{separator}0.2{separator}0.3\n"
    with pytest.raises(pd.errors.ParserError):
        load_dpt(BytesIO(content.encode()), 400, 4000)


@pytest.mark.parametrize("separator", [",", "\t", "   "])
def test_dpt_filters_nonfinite_pairs(separator):
    content = f"1000{separator}0.1\n1100{separator}nan\n1200{separator}inf\n"
    result = load_dpt(BytesIO(content.encode()), 400, 4000)
    np.testing.assert_array_equal(result.to_numpy(), [[1000, 0.1]])


@pytest.mark.parametrize("separator", [",", "\t", "   "])
def test_dpt_keeps_first_pair_with_inline_comment(separator):
    content = f"1000{separator}0.1 # measurement\n1100{separator}0.2\n"
    result = load_dpt(BytesIO(content.encode()), 400, 4000)
    np.testing.assert_array_equal(result.to_numpy(), [[1000, 0.1], [1100, 0.2]])


@pytest.mark.parametrize("content", [b"", b"# metadata only\n", b"400\n500\n"])
def test_dpt_without_numeric_pairs_returns_empty_frame(content):
    result = load_dpt(BytesIO(content), 400, 4000)
    assert result.empty
    assert list(result.columns) == ["wn", "y"]
