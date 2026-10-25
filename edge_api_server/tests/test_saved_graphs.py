import base64
from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import zipfile

from fastapi import UploadFile
from PIL import Image
import pytest

from app import preview_report, report_editor
from app.report.builders import get_builder
from app.report.editable import document_from_dict, editor_document, transfer_values
from app.report.saved_graphs import collection_document, figure_filename, graph_raw_series, read_graphs
from app.report.spectrum_viewer import viewer_html
from edge_api_server.tests.test_edited_report_analysis import edited_payload
from edge_api_server.tests.test_ftir_web import use_fake_pptx_pdf_converter
from edge_api_server.tests.test_ftir_raw_xlsx import xlsx_rows


def graph_inputs():
    result = []
    for index, color in enumerate(("blue", "orange")):
        png = BytesIO()
        Image.new("RGB", (240, 160), color).save(png, format="PNG")
        analysis = edited_payload()
        figure = analysis.pop("viewerFigure")
        for trace in figure["data"]:
            trace["x"] = [x + index * 100 for x in trace["x"]]
            trace["name"] = trace["name"] + str(index)
        figure["layout"]["annotations"][0]["text"] = "편집 피크 " + str(index)
        result.append({"id":"../../unsafe", "title":f"비교 그래프 {index + 1}", "analysis":analysis,
                       "viewerFigure":figure, "image":"data:image/png;base64," + base64.b64encode(png.getvalue()).decode()})
    return result


def parsed_payload():
    items = graph_inputs()
    figure = deepcopy(items[0]["viewerFigure"])
    figure["reportGraphs"] = items
    upload = UploadFile(filename="figures.json", file=BytesIO(json.dumps(figure).encode()))
    return preview_report.parse_analysis_payload(json.dumps(items[0]["analysis"]), viewer_figure=upload)


@pytest.mark.parametrize("experiment", ["FT-IR", "RAMAN"])
def test_collection_preserves_each_graphs_final_peaks_and_conditions(experiment, tmp_path):
    payload = parsed_payload()
    graphs = payload["reportGraphs"]
    assert [g["id"] for g in graphs] == ["saved-graph-1", "saved-graph-2"]
    assert "reportGraphs" not in payload["viewerFigure"]
    job = dict(job_id="test", request_number="REQ", experiment_code=experiment, equipment_code="", operator_id="")
    document, spec = collection_document(get_builder(experiment), job, graphs, transfer_values(payload), tmp_path)
    assert len({s.section_id for s in document.sections}) == len(document.sections)
    assert len(document.figures) == 2
    assert [Path(f.path).name for f in document.figures] == ["saved-graph-1.png", "saved-graph-2.png"]
    assert spec.facts["visible_peak_count"] == 2
    assert [p["position_cm-1"] for p in spec.facts["current_peaks"]] == [1700, 1800]
    assert all(p["intensity"] == 3 for p in spec.facts["current_peaks"])
    assert len({p["id"] for p in spec.facts["current_peaks"]}) == 2
    assert "DELETED" not in json.dumps(spec.facts)
    assert "비교 그래프 1" in spec.fallback["email_body"] and "비교 그래프 2" in spec.fallback["email_body"]
    text = document.to_markdown()
    assert "1700" in text and "1800" in text
    assert "DELETED CHEMISTRY" not in text
    raw = graph_raw_series(graphs)
    assert [s.values for s in raw] == [[1, 3, 1], [1, 3, 1]]
    assert raw[0].label != raw[1].label
    values = editor_document(document)
    for section in values["sections"]:
        section.pop("source"); section.pop("columns")
    report_editor._validate_changes(report_editor.ReportEdit.model_validate(values), document)


@pytest.mark.parametrize("mutation", ["empty", "too_many", "bad_png", "bad_coordinates", "bad_shape", "nested", "long_title", "meta", "offset", "annotations", "shapes"])
def test_collection_input_is_bounded_and_rejected_before_background_render(mutation):
    values = graph_inputs()
    if mutation == "empty": values = []
    if mutation == "too_many": values *= 4
    if mutation == "bad_png": values[0]["image"] = "data:image/png;base64,YmFk"
    if mutation == "bad_coordinates": values[0]["viewerFigure"]["data"][0]["x"] = [1]
    if mutation == "bad_shape": values[0]["viewerFigure"]["data"] = [None]
    if mutation == "nested": values[0]["viewerFigure"]["data"][0]["x"][0] = {"x":1}
    if mutation == "long_title": values[0]["title"] = "x" * 101
    if mutation == "meta": values[0]["viewerFigure"]["data"][0]["meta"] = "bad"
    if mutation == "offset": values[0]["viewerFigure"]["data"][0]["meta"]["rist_view_offset"] = "bad"
    if mutation == "annotations": values[0]["viewerFigure"]["layout"]["annotations"] = [None]
    if mutation == "shapes": values[0]["viewerFigure"]["layout"]["shapes"] = "bad"
    with pytest.raises(ValueError): read_graphs(values)


def test_no_client_path_deserialization_and_no_unvalidated_text_collection(tmp_path):
    assert figure_filename("../../secret") == "current_graph.png"
    assert figure_filename("saved-graph-99") == "current_graph.png"
    assert "reportGraphs" not in preview_report.parse_analysis_payload('{"reportGraphs":[{}]}')


@pytest.mark.parametrize("experiment", ["FT-IR", "RAMAN"])
def test_multi_graph_packages_and_immutable_revision_keep_distinct_images(experiment, monkeypatch):
    use_fake_pptx_pdf_converter(monkeypatch)
    from app.report import spectrum_viewer
    monkeypatch.setattr(spectrum_viewer, "get_plotlyjs", lambda: "")
    payload = parsed_payload()
    root, package = preview_report.build_preview_report_package(experiment_code=experiment, analysis_payload=payload,
        raw_series=[preview_report.RawSeries("full RAW", [4000, 1700, 400], [1, 3, 1])],
        figure_image=base64.b64decode(graph_inputs()[0]["image"].split(",")[1]))
    store = preview_report.PreviewReportJobStore()
    old = store.create(filename="ftir-report-package.zip" if experiment == "FT-IR" else "raman-report-package.zip")
    store.complete(old.job_id, tmp_root=root, package_path=package)
    try:
        original = package.read_bytes()
        saved = json.loads((root / "job/report/report.json").read_text())
        restored = document_from_dict(saved, image_path=root / "job/report/current_graph.png")
        assert len(restored.figures) == 2
        assert Path(restored.figures[0].path).read_bytes() != Path(restored.figures[1].path).read_bytes()
        values = editor_document(restored)
        values["title"] = "수정 완료 · 복수 그래프"
        for section in values["sections"]:
            section.pop("source"); section.pop("columns")
        values = report_editor._validate_changes(report_editor.ReportEdit.model_validate(values), restored)
        monkeypatch.setattr(report_editor, "register_generated_report_package", lambda **kw: kw["package_path"])
        new = store.create(filename=old.filename, parent_job_id=old.job_id)
        assert report_editor._REVISION_SLOT.acquire(False)
        report_editor._write_revision(store, new, old, values, experiment, None, None)
        assert new.status == "completed", new.error
        assert package.read_bytes() == original
        with zipfile.ZipFile(new.package_path) as archive:
            with zipfile.ZipFile(BytesIO(original)) as old_archive:
                assert archive.read("raw_data.xlsx") == old_archive.read("raw_data.xlsx")
            rows = xlsx_rows(archive.read("raw_data.xlsx"))
            if experiment == "FT-IR":
                assert rows == [
                    {"A": "wavenumber(cm-1)/샘플명", "B": "full RAW"},
                    {"A": 400, "B": 1}, {"A": 1700, "B": 3}, {"A": 4000, "B": 1},
                ]
                assert "DPT 원본 측정값" in saved["traceability"]["sourceSummary"]
                from edge_api_server.tests.test_ftir_graph_exports import html_payload
                with zipfile.ZipFile(BytesIO(original)) as old_archive:
                    for name in spectrum_viewer.FTIR_GRAPH_EXPORTS:
                        assert archive.read(name) == old_archive.read(name)
                raw = html_payload(archive.read("ftir-raw-all.html").decode())
                assert raw["figure"]["data"][0]["x"] == [4000, 1700, 400]
                exported = html_payload(archive.read("ftir-edited.html").decode())
                assert [g["title"] for g in exported["graphs"]] == ["비교 그래프 1", "비교 그래프 2"]
                assert exported["graphs"][1]["figure"]["layout"]["annotations"][0]["text"] == "편집 피크 1"
            else:
                assert list(rows[0].values()) == ["Axis", *[s.label for s in graph_raw_series(payload["reportGraphs"])]]
                assert not set(spectrum_viewer.FTIR_GRAPH_EXPORTS) & set(archive.namelist())
            images = [archive.read(f"saved-graph-{i}.png") for i in (1, 2)]
            assert images[0] != images[1]
            html = archive.read("report.html").decode()
            assert 'id=\'graph-choice\'' in html
            assert "비교 그래프 1" in html and "비교 그래프 2" in html
            assert "XMLHttpRequest" not in html and "contenteditable" not in html
            with zipfile.ZipFile(BytesIO(archive.read("report.pptx"))) as ppt:
                media = [ppt.read(name) for name in ppt.namelist() if name.startswith("ppt/media/image")]
                assert all(image in media for image in images)
    finally:
        for job_id in list(store._jobs): store.remove(job_id)


def test_multi_graph_viewer_escapes_titles_and_strips_edit_capabilities(monkeypatch):
    from app.report import spectrum_viewer
    monkeypatch.setattr(spectrum_viewer, "get_plotlyjs", lambda: "")
    graphs = read_graphs(graph_inputs())
    graphs[0]["title"] = "</script><script>alert(1)</script>"
    html = viewer_html(title="Test", figure=graphs[0]["viewerFigure"], graphs=graphs, slides=[])
    assert "<script>alert(1)" not in html
    assert "_ristPeakEditor" not in html
