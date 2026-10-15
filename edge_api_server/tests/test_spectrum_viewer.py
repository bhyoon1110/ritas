from copy import deepcopy
from io import BytesIO
import json
import zipfile

from fastapi import UploadFile
from PIL import Image
import pytest
from reportlab.pdfgen.canvas import Canvas

from app.errors import ApiException
from app.preview_report import parse_analysis_payload
from app.report import spectrum_viewer as viewer


def sample_figure():
    return {
        "data": [
            {"x": [4000, 1700, 400], "y": [11, 13, 12], "name": "Sample A", "mode": "lines",
             "line": {"width": 3.5, "dash": "dash", "color": "#123456"},
             "meta": {"rist_sample_group": "sample:0", "rist_sample_parent": True, "rist_view_offset": 10}},
            {"x": [1700], "y": [13], "name": "C=O", "mode": "markers",
             "meta": {"rist_sample_group": "sample:0", "rist_peak": {"label_key": "carbonyl"}, "rist_view_offset": 10}},
            {"x": [4000, 1700, 400], "y": [1, 5, 2], "name": "Sample B", "mode": "lines",
             "meta": {"rist_sample_group": "sample:1", "rist_sample_parent": True, "rist_view_offset": 0}},
        ],
        "layout": {
            "title": {"text": "Published spectrum"}, "width": 1000, "height": 750,
            "xaxis": {"range": [4000, 400], "fixedrange": True}, "yaxis": {"range": [0, 16]},
            "annotations": [{"x": 1700, "y": 14, "text": "C=O", "captureevents": True},
                            {"x": 1700, "y": 15, "text": "Ratio: 2.5", "name": "rist_raman_ratio:r1"}],
            "shapes": [{"type": "line", "x0": 1700, "x1": 1700, "y0": 13, "y1": 14, "editable": True}],
            "meta": {"ristPeakLabels": [{"traceIndex": 1, "annotationIndex": 0, "shapeIndex": 0}],
                     "ristRamanRatios": [{"id": "r1", "numeratorKey": '["sample:0",1700]'}],
                     "ristPlotSize": {"width": 1000, "ratio": "4:3"}},
        },
    }


def test_readonly_snapshot_preserves_issued_values_and_no_edit_programs(monkeypatch):
    original = sample_figure()
    before = deepcopy(original)
    result = viewer.readonly_figure(original)
    assert original == before
    for old, new in zip(original["data"], result["data"]):
        assert old["x"] == new["x"] and old["y"] == new["y"]
        assert old.get("line") == new.get("line")
    assert result["layout"]["shapes"][0]["editable"] is False
    assert result["layout"]["annotations"][0]["captureevents"] is False
    assert result["data"][1]["showlegend"] is False
    assert result["layout"]["xaxis"]["range"] == [4000, 400]
    assert result["layout"]["xaxis"]["fixedrange"] is False
    assert result["layout"]["meta"]["ratioGroups"] == {"rist_raman_ratio:r1": "sample:0"}
    monkeypatch.setattr(viewer, "get_plotlyjs", lambda: "")
    html = viewer.viewer_html(title="</script><script>bad()</script>", figure=original, slides=["data:image/jpeg;base64,test"])
    assert "<script>bad()" not in html
    assert 'id=\'page-select\'' in html
    for token in ["_ristPeakEditor", "fetch(", "XMLHttpRequest", "contenteditable", "피크 추가", "피크 삭제"]:
        assert token not in html


def test_hidden_peak_labels_are_not_published():
    source = sample_figure()
    source["data"][1]["visible"] = False
    figure = viewer.readonly_figure(source)
    assert len(figure["data"]) == 2
    assert figure["layout"]["meta"]["labels"] == []
    assert not figure["layout"].get("shapes")
    assert [a["text"] for a in figure["layout"]["annotations"]] == ["Ratio: 2.5"]


@pytest.mark.parametrize("key", ["ristFtirStack", "ristRamanStack"])
def test_readonly_snapshot_keeps_only_stack_axis_display_fields(key):
    source = sample_figure()
    saved = {"ticks": {"exists": True, "value": "outside"},
             "minorTicklen": {"exists": True, "value": 4}, "unrelated": {"value": "omit"}}
    source["layout"]["meta"][key] = {"enabled": True, "yAxisDisplay": saved}
    result = viewer.readonly_figure(source)
    assert result["layout"]["meta"]["stacked"] is True
    assert result["layout"]["meta"]["yAxisDisplay"] == {k: saved[k] for k in ("ticks", "minorTicklen")}
    assert source["layout"]["meta"][key]["yAxisDisplay"] == saved


def test_peak_display_switch_omits_peak_graphics_but_keeps_sample_and_other_notes():
    source = sample_figure()
    source["layout"]["meta"]["ristPeaksVisible"] = False
    before = deepcopy(source)
    result = viewer.readonly_figure(source)
    assert source == before
    assert [item["name"] for item in result["data"]] == ["Sample A", "Sample B"]
    assert result["layout"]["meta"]["labels"] == []
    assert [a["text"] for a in result["layout"]["annotations"]] == ["Ratio: 2.5"]
    assert result["layout"].get("shapes", []) == []


@pytest.mark.parametrize("visible", [False, True])
def test_sample_legend_visibility_font_and_exported_colors_survive_readonly_snapshot(visible):
    source = sample_figure()
    source["layout"].update(showlegend=visible, legend={"font": {"size": 22, "weight": 700}})
    source["data"][0]["name"] = "<span style='color:#e11d48'>Sample A</span>"
    result = viewer.readonly_figure(source)
    assert result["layout"]["showlegend"] is visible
    assert result["layout"]["legend"]["font"] == {"size": 22, "weight": 700}
    assert result["data"][0]["name"] == source["data"][0]["name"]
    assert result["data"][1]["showlegend"] is False


def test_snapshot_file_and_limits(monkeypatch):
    figure = sample_figure()
    upload = UploadFile(filename="figure.json", file=BytesIO(json.dumps(figure).encode()))
    payload = parse_analysis_payload('{}', '{"data":[]}', upload)
    assert payload["viewerFigure"] == figure
    assert payload["figure"] == {"data": []}
    assert viewer.read_viewer_snapshot(None) is None
    monkeypatch.setattr(viewer, "MAX_SNAPSHOT_BYTES", 3)
    with pytest.raises(ValueError, match="50MB"):
        viewer.read_viewer_snapshot(UploadFile(file=BytesIO(b"1234")))


@pytest.mark.parametrize("source", [b"invalid", b"[]", b'{"data": [1]}', b'{"data": [], "layout": 42}'])
def test_reject_invalid_snapshot(source):
    with pytest.raises(ValueError):
        viewer.read_viewer_snapshot(UploadFile(file=BytesIO(source)))


def test_slide_previews_render_actual_pdf_pages(tmp_path):
    path = tmp_path / "report-from-pptx.pdf"
    canvas = Canvas(str(path), pagesize=(800, 450))
    for text in ("Slide one", "Slide two"):
        canvas.drawString(40, 400, text)
        canvas.showPage()
    canvas.save()
    images = viewer.render_slide_images(path)
    assert len(images) == 2 and images[0] != images[1]
    import base64
    for image in images:
        with Image.open(BytesIO(base64.b64decode(image.split(",", 1)[1]))) as decoded:
            assert decoded.width == 1280 and decoded.height == 720


def test_preview_response_is_sandboxed_offline_and_bounded(tmp_path, monkeypatch):
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("report.html", viewer.VIEWER_MARKER + "<p>report</p>")
    response = viewer.spectrum_preview_response(package)
    assert response.headers["content-security-policy"] == "sandbox allow-scripts allow-downloads"
    assert response.headers["cache-control"] == "no-store"
    monkeypatch.setattr(viewer, "MAX_VIEWER_BYTES", 1)
    with pytest.raises(ApiException) as error:
        viewer.spectrum_preview_response(package)
    assert error.value.status_code == 413


def test_old_package_has_actionable_preview_error(tmp_path):
    package = tmp_path / "old.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("report.html", "<p>Old report</p>")
    with pytest.raises(ApiException) as error:
        viewer.spectrum_preview_response(package)
    assert error.value.status_code == 404


def test_compact_old_request_has_static_fallback():
    source = sample_figure()
    for trace in source["data"]:
        del trace["x"], trace["y"]
    assert viewer.readonly_figure(source)["data"] == []


@pytest.mark.parametrize("kind", ["ftir", "raman"])
def test_upload_file_snapshot_reaches_report_job(tmp_path, monkeypatch, kind):
    from importlib import import_module
    from fastapi.testclient import TestClient
    from edge_api_server.tests.test_ftir_web import TINY_PNG_DATA_URL, synthetic_dpt
    from edge_api_server.tests.test_raman_web import MULTI_SAMPLE_TXT
    module = import_module("app." + kind + "_web")
    captured = {}

    monkeypatch.setattr(module, "start_preview_report_job", lambda *args, **kwargs: captured.update(kwargs))
    app = module.create_ftir_preview_app(tmp_path / "lib") if kind == "ftir" else module.create_raman_preview_app()
    snapshot = sample_figure()
    # File parts bypass Starlette's 1MB text-field limit, while our own bound remains.
    snapshot["padding"] = "x" * (1024 * 1024 + 1)
    raw = ("sample.dpt", synthetic_dpt()) if kind == "ftir" else (MULTI_SAMPLE_TXT.name, MULTI_SAMPLE_TXT.read_bytes())
    with TestClient(app) as client:
        from app.preview_report import preview_report_job_store
        wrong_job = preview_report_job_store(app).create(filename=("raman" if kind == "ftir" else "ftir") + "-report-package.zip")
        assert client.get(f"/api/v1/{kind}/report/jobs/{wrong_job.job_id}/preview").status_code == 404
        response = client.post(f"/api/v1/{kind}/report/jobs", files={
            "files": (*raw, "application/octet-stream"),
            "viewer_figure": ("snapshot.json", json.dumps(snapshot), "application/json"),
        }, data={"analysis_json": "{}", "figure_json": '{"data":[]}', "figure_image": TINY_PNG_DATA_URL})
    assert response.status_code == 202, response.text
    assert captured["analysis_payload"]["viewerFigure"]["data"] == snapshot["data"]
    assert captured["analysis_payload"]["figure"] == {"data": []}


def test_persistent_preview_checks_project_permission(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from starlette.requests import Request
    from app import report_delivery as delivery
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("report.html", viewer.VIEWER_MARKER)
    settings = SimpleNamespace(auth_enabled=True, analysis_type_map=())
    database = SimpleNamespace(fetch_report_run=lambda _: {"report_id": "report-1", "experiment_code": "FT-IR"})
    request = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace())})
    monkeypatch.setattr(delivery, "_settings", lambda _: settings)
    monkeypatch.setattr(delivery, "_database", lambda _: database)
    monkeypatch.setattr(delivery, "require_context", lambda _: SimpleNamespace(is_admin=False, projects={"TEM"}))
    with pytest.raises(ApiException) as error:
        delivery.preview_generated_report(request, "report-1")
    assert error.value.status_code == 403
    monkeypatch.setattr(delivery, "require_context", lambda _: SimpleNamespace(is_admin=False, projects={"FTIR"}))
    monkeypatch.setattr(delivery, "_package_path", lambda *_: package)
    assert delivery.preview_generated_report(request, "report-1").status_code == 200
