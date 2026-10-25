"""DPT-only RAW workbook: preserve measured values independently of editing."""
from copy import deepcopy
from io import BytesIO
import xml.etree.ElementTree as ET
import zipfile

from app.ftir_web import _build_ftir_raw_series
from app.preview_report import RawSeries, write_ftir_raw_data_xlsx, write_raw_data_xlsx


def xlsx_rows(content):
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(BytesIO(content)) as archive:
        root = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
    result = []
    for row in root.findall("s:sheetData/s:row", ns):
        values = {}
        for cell in row.findall("s:c", ns):
            assert cell.find("s:f", ns) is None  # Filenames must never become formulas.
            column = cell.attrib["r"].rstrip("0123456789")
            number = cell.find("s:v", ns)
            values[column] = float(number.text) if number is not None else cell.findtext("s:is/s:t", namespaces=ns)
        result.append(values)
    return result


def test_dpt_merge_keeps_exact_axes_duplicates_missing_points_and_original_values(tmp_path):
    uploaded = [
        ("시료 A.DPT", b"# Resolution: 4 cm-1\n4000,0\n1700.1234567891,-0.123456789012345\n1700.1234567892,83.1234567890123\n1000,2\n1000,3\n"),
        ("시료 B.dpt", b"4000\t99.5\n2000\t3\n1000\t1\n300\t-4.5\n"),
    ]
    series = _build_ftir_raw_series(uploaded)
    original = deepcopy(series)
    path = tmp_path / "raw_data.xlsx"
    write_ftir_raw_data_xlsx(path, series)
    rows = xlsx_rows(path.read_bytes())
    assert rows == [
        {"A": "wavenumber(cm-1)/샘플명", "B": "시료 A", "C": "시료 B"},
        {"A": 300, "C": -4.5},
        {"A": 1000, "B": 2, "C": 1},
        {"A": 1000, "B": 3},
        {"A": 1700.1234567891, "B": -0.123456789012345},
        {"A": 1700.1234567892, "B": 83.1234567890123},
        {"A": 2000, "C": 3},
        {"A": 4000, "B": 0, "C": 99.5},
    ]
    assert series == original


def test_dpt_duplicate_filenames_get_distinct_literal_headers(tmp_path):
    path = tmp_path / "raw_data.xlsx"
    series = [RawSeries(label, [1000], [i]) for i, label in enumerate(
        ["sample", "sample", "sample (2)", "SAMPLE", "=1+1 <&>"]
    )]
    write_ftir_raw_data_xlsx(path, series)
    rows = xlsx_rows(path.read_bytes())
    assert list(rows[0].values())[1:] == ["sample", "sample (2)", "sample (2) (2)", "SAMPLE (3)", "=1+1 <&>"]
    assert list(rows[1].values()) == [1000, 0, 1, 2, 3, 4]


def test_dpt_empty_sources_do_not_invent_data_and_sixty_files_remain_separate(tmp_path):
    path = tmp_path / "raw_data.xlsx"
    write_ftir_raw_data_xlsx(path, [])
    assert xlsx_rows(path.read_bytes()) == [{"A": "wavenumber(cm-1)/샘플명"}]
    write_ftir_raw_data_xlsx(path, [RawSeries(f"sample-{i}", [1000], [i]) for i in range(60)])
    rows = xlsx_rows(path.read_bytes())
    assert len(rows[0]) == 61
    assert rows[0]["BI"] == "sample-59"
    assert rows[1]["BI"] == 59


def test_other_experiments_keep_existing_raw_workbook_policy(tmp_path):
    path = tmp_path / "raw_data.xlsx"
    write_raw_data_xlsx(path, [RawSeries("Raman", [1000.123456789], [0.123456789012345])])
    assert xlsx_rows(path.read_bytes()) == [
        {"A": "Axis", "B": "Raman"}, {"A": 1000.12345679, "B": 0.123456789012},
    ]
