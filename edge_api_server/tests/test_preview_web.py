from __future__ import annotations

from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.preview_web import create_preview_app


def test_combined_preview_app_serves_all_preview_pages() -> None:
    with TestClient(create_preview_app()) as client:
        home = client.get("/")
        ftir = client.get("/ftir")
        raman = client.get("/raman")
        xrd = client.get("/xrd")
        tem = client.get("/tem")
        health = client.get("/health")

    assert home.status_code == 200
    assert 'href="/ftir"' in home.text
    assert 'href="/raman"' in home.text
    assert 'href="/xrd"' in home.text
    assert 'href="/tem"' in home.text
    assert ftir.status_code == 200
    assert raman.status_code == 200
    assert xrd.status_code == 200
    assert tem.status_code == 200
    assert health.json() == {"status": "ok", "mode": "preview"}


def test_edge_app_root_serves_workspace_index(tmp_path) -> None:
    settings = Settings(
        storage_root=tmp_path / "jobs",
        error_archive_root=tmp_path / "errors",
    )

    database = Mock()
    with patch("app.main.Database.from_settings", return_value=database):
        with TestClient(create_app(settings)) as client:
            home = client.get("/")
            errors = client.get("/errors")

    assert home.status_code == 200
    assert 'href="/ftir"' in home.text
    assert 'href="/raman"' in home.text
    assert 'href="/xrd"' in home.text
    assert 'href="/tem"' in home.text
    assert errors.status_code == 200
    assert '<a href="/">작업 화면</a>' in errors.text
