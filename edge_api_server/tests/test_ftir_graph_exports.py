"""Full source RAW and issued graph are distinct, offline recipient artifacts."""
from copy import deepcopy
from io import BytesIO
import json
import re
import time
import zipfile

from fastapi import UploadFile
from fastapi.testclient import TestClient
import pytest

from app import ftir_web, preview_report
from app.preview_report import RawSeries
from app.report import spectrum_viewer as viewer
from edge_api_server.tests.test_ftir_web import TINY_PNG_DATA_URL, synthetic_dpt, use_fake_pptx_pdf_converter
from edge_api_server.tests.test_spectrum_viewer import sample_figure


def html_payload(content):
    return json.loads(re.search(r"<script type='application/json' id='report-data'>(.*?)</script>", content, re.S)[1])


def test_standalone_exports_keep_full_raw_and_edited_coordinates_separate(tmp_path, monkeypatch):
    monkeypatch.setattr(viewer, "get_plotlyjs", lambda: "")
    original = [RawSeries("same", [4000, 1700, 400], [-1, 30, .125]),
                RawSeries("same", [4100, 1200, 300], [81, 98, 99])]
    edited = sample_figure()
    edited["data"][2]["visible"] = False
    edited["layout"]["xaxis"]["range"] = [1800, 1600]
    before = deepcopy(edited)
    viewer.build_ftir_graph_exports(tmp_path, raw_series=original, figure=edited)
    raw = html_payload((tmp_path / "ftir-raw-all.html").read_text())
    final = html_payload((tmp_path / "ftir-edited.html").read_text())
    assert raw["graphOnly"] and final["graphOnly"]
    assert raw["slides"] == final["slides"] == []
    assert [t["x"] for t in raw["figure"]["data"]] == [s.axis for s in original]
    assert [t["y"] for t in raw["figure"]["data"]] == [s.values for s in original]
    assert len({t["name"] for t in raw["figure"]["data"]}) == 2
    assert "range" not in raw["figure"]["layout"]["xaxis"]
    assert raw["figure"]["layout"]["xaxis"]["autorange"] == "reversed"
    assert not raw["figure"]["layout"].get("annotations")
    assert final["figure"]["layout"]["xaxis"]["range"] == [1800, 1600]
    assert len(final["figure"]["data"]) == 2  # hidden sample not in edited view
    assert final["figure"]["data"][0]["y"] == [11, 13, 12]
    assert final["figure"]["layout"]["annotations"][0]["text"] == "C=O"
    assert edited == before
    for name in viewer.FTIR_GRAPH_EXPORTS:
        content = (tmp_path / name).read_text()
        assert "connect-src 'none'" in content
        assert "_ristPeakEditor" not in content and "contenteditable" not in content
    monkeypatch.setattr(viewer, "MAX_VIEWER_BYTES", 1)
    with pytest.raises(ValueError, match="80MB"):
        viewer.build_ftir_graph_exports(tmp_path, raw_series=original, figure=edited)


@pytest.mark.parametrize("background", [False, True])
def test_full_raw_survives_hidden_samples_crop_and_background_job(tmp_path, monkeypatch, background):
    use_fake_pptx_pdf_converter(monkeypatch)
    monkeypatch.setattr(viewer, "get_plotlyjs", lambda: "")
    originals = [("visible.dpt", synthetic_dpt()), ("hidden.dpt", synthetic_dpt(2300))]
    series = ftir_web._build_ftir_raw_series(originals)
    edited = sample_figure()
    edited["data"] = edited["data"][:2]
    edited["layout"]["xaxis"]["range"] = [1800, 1600]
    payload = {"samples": [{"label": "visible", "fileName": "visible.dpt"}]}
    with TestClient(ftir_web.create_ftir_preview_app(tmp_path / "library")) as client:
        response = client.post("/api/v1/ftir/report" + ("/jobs" if background else ""),
            files=[("files", (name, data, "application/octet-stream")) for name, data in originals]
                  + [("viewer_figure", ("figure.json", json.dumps(edited), "application/json"))],
            data={"analysis_json": json.dumps(payload), "figure_json": json.dumps(edited), "figure_image": TINY_PNG_DATA_URL})
        assert response.status_code == (202 if background else 200), response.text
        if background:
            job = response.json()
            for _ in range(200):
                job = client.get(f"/api/v1/ftir/report/jobs/{job['jobId']}").json()
                if job["status"] in ("completed", "failed"): break
                time.sleep(.03)
            assert job["status"] == "completed", job
            response = client.get(job["downloadUrl"])
        with zipfile.ZipFile(BytesIO(response.content)) as archive:
            assert set(viewer.FTIR_GRAPH_EXPORTS) <= set(archive.namelist())
            raw = html_payload(archive.read("ftir-raw-all.html").decode())["figure"]
            assert [t["name"] for t in raw["data"]] == [s.label for s in series]
            for trace, source in zip(raw["data"], series):
                assert trace["x"] == source.axis and trace["y"] == source.values
            final = html_payload(archive.read("ftir-edited.html").decode())["figure"]
            assert final["layout"]["xaxis"]["range"] == [1800, 1600]
            assert final["data"][1]["name"] == "C=O"
            with zipfile.ZipFile(BytesIO(archive.read("raw_data.xlsx"))) as workbook:
                text = workbook.read("xl/worksheets/sheet1.xml").decode()
                assert "hidden" not in text  # Existing edited-data Excel policy stays unchanged.


def test_analysis_limit_unchanged_but_report_accepts_multiple_graph_sources():
    def uploads(count):
        return [UploadFile(filename=f"sample-{i}.dpt", file=BytesIO(b"400,1\n500,2")) for i in range(count)]
    with pytest.raises(Exception, match="10"):
        ftir_web._uploaded_dpt_files(uploads(11))
    assert len(ftir_web._uploaded_dpt_files(uploads(11), max_files=ftir_web.MAX_FTIR_REPORT_FILES)) == 11
    with pytest.raises(Exception, match="60"):
        ftir_web._uploaded_dpt_files(uploads(61), max_files=ftir_web.MAX_FTIR_REPORT_FILES)
