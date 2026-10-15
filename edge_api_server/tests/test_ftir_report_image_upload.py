from __future__ import annotations

import base64
from io import BytesIO
import json
from pathlib import Path
import time
from types import SimpleNamespace
import zipfile

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from app import preview_report
from app.ftir_web import create_ftir_preview_app
from test_ftir_web import synthetic_dpt, use_fake_pptx_pdf_converter, TINY_PNG_DATA_URL


def png_bytes(size=(800, 600), image_format="PNG") -> bytes:
    with Image.new("RGB", size, "white") as image, BytesIO() as output:
        image.save(output, format=image_format, compress_level=0)
        return output.getvalue()


def report_request(client, route, content):
    if route != "session":
        return "/api/v1/ftir/report" + route, {"files": ("sample.dpt", content)}
    session = client.post("/api/v1/ftir/report/upload-sessions")
    assert session.status_code == 201
    prefix = f"/api/v1/ftir/report/upload-sessions/{session.json()['uploadId']}"
    response = client.post(prefix + "/chunks", files={"file": ("chunk", content)}, data={
        "relative_path": "sample.dpt", "offset": "0", "total_size": str(len(content)),
        "chunk_index": "0", "chunk_count": "1",
    })
    assert response.status_code == 200
    return prefix + "/complete", {}


@pytest.mark.parametrize("route", ["", "/jobs", "session"])
def test_large_png_file_generates_report_on_all_ftir_routes(tmp_path: Path, monkeypatch, route):
    use_fake_pptx_pdf_converter(monkeypatch)
    app = create_ftir_preview_app(tmp_path / "libraries")
    app.state.settings = None  # No external LLM or production DB in this test.
    image = png_bytes()
    assert len(image) > 1024 * 1024
    content = synthetic_dpt()
    with TestClient(app) as client:
        analysis = client.post("/api/v1/ftir/analyze", files={"files": ("sample.dpt", content)}).json()
        figure = analysis.pop("figure")
        url, files = report_request(client, route, content)
        files["viewer_figure"] = ("viewer.json", json.dumps(figure), "application/json")
        fields = {"analysis_json": json.dumps(analysis)}
        # The old text transport fails in Starlette before any report job starts.
        rejected = client.post(url, files=files, data={**fields,
            "figure_image": "data:image/png;base64," + base64.b64encode(image).decode("ascii")})
        assert rejected.status_code == 400
        assert "Part exceeded maximum size" in rejected.json()["detail"]

        files["figure_image_file"] = ("current_graph.png", image, "image/png")
        result = client.post(url, files=files, data=fields)
        if route:
            assert result.status_code == 202, result.text
            job = result.json()
            if route == "session":
                retry = client.post(url, files=files, data=fields)
                assert retry.status_code == 202
                assert retry.json()["jobId"] == job["jobId"]
            for _ in range(300):
                job = client.get(f"/api/v1/ftir/report/jobs/{job['jobId']}").json()
                if job["status"] in {"completed", "failed"}:
                    break
                time.sleep(0.05)
            assert job["status"] == "completed", job
            result = client.get(job["downloadUrl"])
        assert result.status_code == 200, result.text[:200] if result.status_code != 200 else ""
        with zipfile.ZipFile(BytesIO(result.content)) as archive:
            assert archive.testzip() is None
            assert archive.read("current_graph.png") == image
            assert {"report.pptx", "report.pdf", "report-from-pptx.pdf", "report.html", "raw_data.xlsx"} <= set(archive.namelist())


@pytest.mark.parametrize("route", ["", "/jobs", "session"])
def test_invalid_image_file_returns_actionable_error_before_job(tmp_path: Path, route):
    app = create_ftir_preview_app(tmp_path / "libraries")
    app.state.settings = None
    with TestClient(app) as client:
        url, files = report_request(client, route, synthetic_dpt())
        files["figure_image_file"] = ("forged.png", b"not a PNG", "image/png")
        result = client.post(url, files=files, data={"analysis_json": "{}"})
        assert result.status_code == 400
        assert result.json()["code"] == "FTIR_REPORT_INVALID_PAYLOAD"
        assert "PNG" in result.json()["message"]


def test_image_reader_preserves_legacy_and_rejects_invalid_or_excessive_images(monkeypatch):
    assert preview_report.read_figure_image(TINY_PNG_DATA_URL) == preview_report.decode_figure_image(TINY_PNG_DATA_URL)
    with pytest.raises(ValueError, match="data:image"):
        preview_report.read_figure_image()
    for content, message in [(b"", "비어"), (png_bytes((2, 2), "JPEG"), "PNG"),
                             (png_bytes((2, 2))[:-20], "PNG")]:
        with pytest.raises(ValueError, match=message):
            preview_report.read_figure_image(TINY_PNG_DATA_URL, SimpleNamespace(file=BytesIO(content)))
    monkeypatch.setattr(preview_report, "MAX_FIGURE_IMAGE_PIXELS", 3)
    with pytest.raises(ValueError, match="해상도"):
        preview_report.read_figure_image(upload=SimpleNamespace(file=BytesIO(png_bytes((2, 2)))))
    monkeypatch.setattr(preview_report, "MAX_FIGURE_IMAGE_BYTES", 100)
    stream = BytesIO(b"x" * 1000)
    with pytest.raises(ValueError, match="16MB"):
        preview_report.read_figure_image(upload=SimpleNamespace(file=stream))
    assert stream.tell() == 101  # Bounded read, regardless of the submitted file size.
    with pytest.raises(ValueError, match="16MB"):
        preview_report.read_figure_image("data:image/png;base64," + "A" * 1000)
