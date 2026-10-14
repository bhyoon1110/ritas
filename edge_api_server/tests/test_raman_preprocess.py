import codecs

import numpy as np
import pytest

from rin.raman.preprocess import RamanRawError, load_raman_raw_samples


ENCODINGS = [
    ("utf-8", b""), ("utf-8-sig", b""), ("cp949", b""),
    ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE),
    ("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", codecs.BOM_UTF32_BE),
]


def encoded_lines(lines, encoding, bom, newlines):
    endings = ["\r", "\n", "\r\n"] if newlines == "mixed" else [newlines]
    text = "".join(line + endings[index % len(endings)] for index, line in enumerate(lines))
    return bom + text.encode(encoding)


@pytest.mark.parametrize("encoding,bom", ENCODINGS)
@pytest.mark.parametrize("newlines", ["\n", "\r\n", "\r", "mixed"])
def test_raman_unicode_multi_sample_preserves_values_and_metadata(encoding, bom, newlines):
    # Repeated shift/intensity columns and a trailing header tab match lab exports.
    lines = [
        "# 시료: 양극", "# Excitation Wavelength: 531.85 nm", "",
        "wn(시료 A)\t시료 A\twn(시료 A_P)\t시료 A_P\t",
        *[f"{100+i*5}\t{i+0.125}\t{100+i*5}\t{i+0.375}" for i in range(20)],
    ]
    samples = load_raman_raw_samples(
        "sample.txt", encoded_lines(lines, encoding, bom, newlines),
    )
    assert [sample.label for sample in samples] == ["시료 A", "시료 A_P"]
    for index, sample in enumerate(samples):
        assert sample.metadata == {"시료": "양극", "Excitation Wavelength": "531.85 nm"}
        expected = [[100+i*5, i+0.125+index*0.25] for i in range(20)]
        np.testing.assert_array_equal(sample.frame.to_numpy(), expected)


@pytest.mark.parametrize("encoding,bom", ENCODINGS)
@pytest.mark.parametrize("newlines", ["\r\n", "\r"])
@pytest.mark.parametrize("separator", ["\t", ",", " ", ";"])
def test_raman_headerless_unicode_keeps_all_ten_measurements(encoding, bom, newlines, separator):
    lines = ["# 시료: 양극", *[f"{100+i*5}{separator}{i+1}" for i in range(10)]]
    samples = load_raman_raw_samples(
        "sample.txt", encoded_lines(lines, encoding, bom, newlines),
    )
    assert len(samples) == 1
    assert samples[0].label is None
    np.testing.assert_array_equal(samples[0].frame.to_numpy(), [[100+i*5, i+1] for i in range(10)])


@pytest.mark.parametrize("encoding,bom", ENCODINGS[3:])
def test_raman_rejects_truncated_bom_encoded_text(encoding, bom):
    content = bom + "100\t1\r\n".encode(encoding) + b"\x00"
    with pytest.raises(RamanRawError, match="손상되었거나 잘렸습니다"):
        load_raman_raw_samples("truncated.txt", content)
