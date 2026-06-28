from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.raman_web import _blank_figure, build_raman_page, create_raman_preview_app
from rin.raman.preprocess import load_raman_raw, load_raman_raw_samples


SAMPLE_TXT = (
    Path(__file__).resolve().parents[2]
    / "rin"
    / "data"
    / "LMR"
    / "LMR1.txt"
)
MULTI_SAMPLE_TXT = (
    Path(__file__).resolve().parents[2]
    / "rin"
    / "data"
    / "대기민감성 샘플"
    / "LiOH.txt"
)


def test_raman_workspace_contains_upload_controls() -> None:
    page = build_raman_page()

    assert 'id="raman-file-input"' in page
    assert 'id="raman-file-list"' in page
    assert 'id="raman-drop-zone"' in page
    assert 'id="raman-drop-prompt"' in page
    assert 'id="raman-library-list"' in page
    assert 'id="raman-library-filter"' in page
    assert 'id="raman-library-new"' in page
    assert 'id="raman-library-modal"' in page
    assert "/api/v1/raman/analyze" in page
    assert "/api/v1/raman/assignment-libraries" in page
    assert "/raman/assets/plotly.min.js" in page
    assert "RIN Raman" in page
    assert "rist-peak-sensitivity-control" in page
    assert "rist-raman-stack-control" in page
    assert "rist-raman-ratio-control" in page
    assert "ristRamanStack" in page
    assert "I(num)/I(den)" in page
    assert "분자 선택" in page
    assert "분모 선택" in page
    assert "Y 이동" in page
    assert "rist-raman-tools-toggle" in page
    assert "rist-raman-tools-open" in page
    assert "rist-raman-tools-head" in page
    assert "rist-raman-tools-opacity" in page
    assert "setToolPanelAlphaFromPointer" in page
    assert "setPanelPosition" in page
    assert "setOpen(!gd.classList.contains" in page
    assert "max-width: calc(100% - 16px)" in page
    assert "@media (max-width: 760px)" in page
    assert "@media (max-width: 1440px)" in page
    assert "@media (max-width: 420px)" in page
    assert "var compact = window.innerWidth <= 760" in page
    assert '"margin.t": 170' in page
    assert '"margin.t": 120' in page
    assert "rist-legend-edit-button" in page
    assert "rist-shape-editor-panel" in page
    assert "SNAP_PX = 24" in page
    assert "scrollZoom" in page
    assert "dispatchDataReplaced" in page
    assert "rist-plot-data-replaced" in page
    assert "gd._context" in page
    assert "rist-raman-stack-change" in page
    assert "annotationPosition" in page
    assert "annotationTail" in page
    assert "raman-file-remove" in page
    assert "raman-library-row-remove" in page
    assert "assignment_library_ids" in page
    assert "assignment_library_selection_explicit" in page
    assert "raman-drop-zone" in page
    assert "Raman raw 파일을 선택하거나 여기에 놓으세요" in page
    assert "prompt.style.display = files.length ? \"none\" : \"inline\"" in page
    assert "files.splice(index, 1)" in page
    assert "else resetPlot()" in page


def test_raman_blank_figure_starts_without_reserved_legend_margin() -> None:
    figure = _blank_figure()

    assert len(figure.data) == 0
    assert figure.layout.margin.r == 70


def test_raman_analyze_api_stacks_multiple_samples() -> None:
    content = SAMPLE_TXT.read_bytes()
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files=[
                ("files", (SAMPLE_TXT.name, content, "text/plain")),
                ("files", (SAMPLE_TXT.name, content, "text/plain")),
            ],
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    figure = response.json()["figure"]
    stack = figure["layout"]["meta"]["ristRamanStack"]
    assert stack["enabled"] is True
    assert stack["sampleOffsets"]["sample:0"] == 0
    assert stack["sampleOffsets"]["sample:1"] > 0

    parent_traces = [
        trace for trace in figure["data"]
        if trace.get("meta", {}).get("rist_sample_parent")
    ]
    assert len(parent_traces) == 2
    assert parent_traces[0]["meta"]["rist_raman_stack_offset"] == 0
    assert parent_traces[1]["meta"]["rist_raman_stack_offset"] > 0
    assert parent_traces[1]["y"][0] > parent_traces[0]["y"][0]


def test_raman_raw_loader_reads_instrument_txt() -> None:
    frame = load_raman_raw(SAMPLE_TXT.name, SAMPLE_TXT.read_bytes())

    assert len(frame) > 100
    assert list(frame.columns) == ["shift", "intensity"]
    assert frame["shift"].is_monotonic_increasing


def test_raman_raw_loader_reads_multi_sample_txt() -> None:
    samples = load_raman_raw_samples(
        MULTI_SAMPLE_TXT.name,
        MULTI_SAMPLE_TXT.read_bytes(),
    )

    assert [sample.label for sample in samples] == [
        "LiOH_4",
        "LiOH_3",
        "LiOH_2",
        "LiOH",
    ]
    assert [len(sample.frame) for sample in samples] == [1340, 1340, 1340, 1340]
    assert all(sample.frame["shift"].is_monotonic_increasing for sample in samples)


def test_raman_raw_loader_reads_shared_shift_multi_sample_csv() -> None:
    rows = ["Raman Shift,Sample A,Sample B"]
    for index in range(20):
        shift = 100 + index * 5
        rows.append(f"{shift},{index + 1},{(index + 1) * 2}")

    samples = load_raman_raw_samples(
        "shared-shift.csv",
        ("\n".join(rows) + "\n").encode(),
    )

    assert [sample.label for sample in samples] == ["Sample A", "Sample B"]
    assert [len(sample.frame) for sample in samples] == [20, 20]
    assert samples[0].frame["intensity"].iloc[-1] == 20
    assert samples[1].frame["intensity"].iloc[-1] == 40


def test_raman_analyze_api_accepts_txt_sample() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    SAMPLE_TXT.name,
                    SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["samples"][0]["fileName"] == SAMPLE_TXT.name
    assert payload["samples"][0]["pointCount"] > 100
    assert payload["samples"][0]["peakCount"] >= 1
    assert payload["settings"]["sensitivity"] == 25
    assert payload["figure"]["data"]
    assert any(
        trace.get("meta", {}).get("rist_peak")
        for trace in payload["figure"]["data"]
    )
    assert any(
        isinstance(trace.get("meta", {}).get("rist_peak", {}).get("base_y"), float)
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    )


def test_raman_analyze_api_expands_multi_sample_txt() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    MULTI_SAMPLE_TXT.name,
                    MULTI_SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={"sensitivity": "25"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert [sample["label"] for sample in payload["samples"]] == [
        "LiOH_4",
        "LiOH_3",
        "LiOH_2",
        "LiOH",
    ]
    assert all(sample["fileName"] == MULTI_SAMPLE_TXT.name for sample in payload["samples"])
    parent_traces = [
        trace for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_sample_parent")
    ]
    assert [trace["name"] for trace in parent_traces] == [
        "LiOH_4",
        "LiOH_3",
        "LiOH_2",
        "LiOH",
    ]
    assert payload["figure"]["layout"]["meta"]["ristRamanStack"]["enabled"] is True


def test_raman_assignment_library_api_defaults_and_assigns_sample() -> None:
    with TestClient(create_raman_preview_app()) as client:
        libraries_response = client.get("/api/v1/raman/assignment-libraries")
        assert libraries_response.status_code == 200
        libraries = libraries_response.json()["libraries"]
        by_id = {library["id"]: library for library in libraries}
        assert by_id["general-raman"]["defaultSelected"] is True
        assert by_id["carbon-graphite-raman"]["assignmentCount"] == 8
        assert by_id["lithium-compound-raman"]["assignmentCount"] == 23
        assert by_id["lmr-layered-oxide-raman"]["assignmentCount"] == 6

        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    SAMPLE_TXT.name,
                    SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={
                "sensitivity": "25",
                "assignment_library_ids": ["general-raman"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["assignmentLibraries"][0]["id"] == "general-raman"
    peak_names = [
        trace.get("name", "")
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    ]
    assert any("D band" in name or "G band" in name for name in peak_names)


def test_raman_pptx_lmr_assignment_library_assigns_sample() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={
                "files": (
                    SAMPLE_TXT.name,
                    SAMPLE_TXT.read_bytes(),
                    "text/plain",
                )
            },
            data={
                "sensitivity": "25",
                "assignment_library_ids": ["lmr-layered-oxide-raman"],
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["settings"]["assignmentLibraries"][0]["id"] == "lmr-layered-oxide-raman"
    peak_names = [
        trace.get("name", "")
        for trace in payload["figure"]["data"]
        if trace.get("meta", {}).get("rist_peak")
    ]
    assert any("LMR A1g mode" in name for name in peak_names)


def test_raman_analyze_api_rejects_unknown_extension() -> None:
    with TestClient(create_raman_preview_app()) as client:
        response = client.post(
            "/api/v1/raman/analyze",
            files={"files": ("sample.bin", b"1 2\n3 4\n", "application/octet-stream")},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_RAMAN_EXTENSION"
