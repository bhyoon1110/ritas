from __future__ import annotations

from pathlib import Path

from app.config import Settings
from rist_common.config import load_environment


def write_profile(path: Path, host: str, environment: str, storage_root: str | None = None) -> None:
    lines = [
        f"APP_ENV={environment}",
        "EDGE_SERVER_SCHEME=http",
        f"EDGE_SERVER_HOST={host}",
        "EDGE_SERVER_PORT=8000",
        f"EDGE_SERVER_BASE_URL=http://{host}:8000",
        "EDGE_BIND_HOST=0.0.0.0",
        "LOCAL_LLM_BASE_URL=http://127.0.0.1:8001",
        "LOCAL_LLM_MODEL=test-model",
        "LOCAL_LLM_TEMPERATURE=0.1",
        "LOCAL_LLM_MAX_TOKENS=1200",
        "LOCAL_LLM_CONTEXT_WINDOW=8192",
        "LOCAL_LLM_CONTEXT_MARGIN=256",
        "LOCAL_LLM_VALIDATE_MODEL=true",
        "LOCAL_LLM_INCLUDE_IMAGES=true",
        "LOCAL_LLM_MAX_IMAGES=3",
        "LOCAL_LLM_MAX_IMAGE_BYTES=2097152",
        "PUBLIC_DOMAIN=bhyoon.me",
        f"EDGE_STORAGE_ROOT={storage_root or path.parent / 'edge-jobs'}",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_loads_development_profile(monkeypatch, tmp_path: Path) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("EDGE_SERVER_BASE_URL", raising=False)

    config = load_environment("development")

    assert config.environment == "development"
    assert config.edge_server_base_url == "http://192.168.0.10:8000"
    assert config.local_llm_base_url == "http://127.0.0.1:8001"
    assert config.local_llm_model == "test-model"
    assert config.local_llm_max_tokens == 1200
    assert config.local_llm_context_window == 8192


def test_settings_switch_to_production(monkeypatch, tmp_path: Path) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "production")
    for key in (
        "RIST_EDGE_PUBLIC_BASE_URL",
        "RIST_EDGE_BIND_HOST",
        "RIST_EDGE_API_PORT",
        "RIST_LLM_BASE_URL",
        "RIST_LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)

    settings = Settings.from_env()

    assert settings.environment == "production"
    assert settings.edge_public_base_url == "http://bhyoon.me:8000"
    assert settings.api_port == 8000


def test_storage_root_from_profile(monkeypatch, tmp_path: Path) -> None:
    abs_root = tmp_path / "edge-jobs"
    write_profile(
        tmp_path / "development.env",
        "192.168.0.10",
        "development",
        storage_root=str(abs_root),
    )
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.delenv("RIST_STORAGE_ROOT", raising=False)

    settings = Settings.from_env()

    assert settings.storage_root == abs_root


def test_storage_root_env_overrides_profile(monkeypatch, tmp_path: Path) -> None:
    write_profile(
        tmp_path / "development.env",
        "192.168.0.10",
        "development",
        storage_root=str(tmp_path / "from-profile"),
    )
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    override = tmp_path / "from-env"
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(override))

    settings = Settings.from_env()

    assert settings.storage_root == override
    assert settings.llm_base_url == "http://127.0.0.1:8001"
    assert settings.llm_model == "test-model"
    assert settings.llm_temperature == 0.1
    assert settings.llm_max_tokens == 1200
