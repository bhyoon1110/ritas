from __future__ import annotations

from fastapi.testclient import TestClient

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
