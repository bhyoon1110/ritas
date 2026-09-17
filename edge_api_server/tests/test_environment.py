from __future__ import annotations

from pathlib import Path

from app.config import Settings
from rist_common.config import load_environment


def write_profile(path: Path, host: str, environment: str) -> None:
    lines = [
        "EDGE_SERVER_SCHEME=http",
        f"EDGE_SERVER_HOST={host}",
        "EDGE_SERVER_PORT=8000",
        "EDGE_BIND_HOST=0.0.0.0",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def test_loads_development_profile(monkeypatch, tmp_path: Path) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))

    config = load_environment("development")

    assert config.environment == "development"
    assert config.edge_server_base_url == "http://192.168.0.10:8000"


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
    assert settings.report_storage_key == "RIST_REPORTS"
    assert settings.report_transfer_max_attempts == 5


def test_storage_root_defaults_to_edge_data_jobs(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.delenv("RIST_STORAGE_ROOT", raising=False)

    settings = Settings.from_env()

    assert settings.storage_root.name == "jobs"
    assert settings.storage_root.parent.name == "data"


def test_storage_root_env_overrides_profile(monkeypatch, tmp_path: Path) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    override = tmp_path / "from-env"
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(override))

    settings = Settings.from_env()

    assert settings.storage_root == override
    assert settings.llm_base_url == "http://127.0.0.1:8001"
    assert settings.llm_model == "gemma4-e4b"
    assert settings.llm_temperature == 0.1
    assert settings.llm_max_tokens == 1200


def test_usage_log_settings_follow_storage_root_and_allow_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    storage_root = tmp_path / "jobs"
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(storage_root))
    monkeypatch.delenv("RIST_USAGE_LOG_ROOT", raising=False)
    monkeypatch.setenv("RIST_USAGE_LOG_RETENTION_DAYS", "45")

    settings = Settings.from_env()

    assert settings.usage_log_root == storage_root / "usage"
    assert settings.usage_log_retention_days == 45

    override = tmp_path / "audit"
    monkeypatch.setenv("RIST_USAGE_LOG_ROOT", str(override))
    assert Settings.from_env().usage_log_root == override


def test_llm_runtime_env(monkeypatch, tmp_path: Path) -> None:
    write_profile(tmp_path / "development.env", "192.168.0.10", "development")
    write_profile(tmp_path / "production.env", "bhyoon.me", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.setenv("RIST_LLM_BASE_URL", "http://127.0.0.1:18001")
    monkeypatch.setenv("RIST_LLM_MODEL", "custom-model")
    monkeypatch.setenv("RIST_LLM_MAX_TOKENS", "640")

    settings = Settings.from_env()

    assert settings.llm_base_url == "http://127.0.0.1:18001"
    assert settings.llm_model == "custom-model"
    assert settings.llm_max_tokens == 640


def test_ftir_assignment_library_dir_env_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_profile(tmp_path / "development.env", "bhyoon.me", "development")
    write_profile(tmp_path / "production.env", "192.168.0.10", "production")
    library_dir = tmp_path / "peak-libraries"
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.setenv(
        "RIST_FTIR_ASSIGNMENT_LIBRARY_DIR",
        str(library_dir),
    )

    settings = Settings.from_env()

    assert settings.ftir_assignment_library_dir == library_dir


def test_ftir_assignment_library_delete_flag_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_profile(tmp_path / "development.env", "bhyoon.me", "development")
    write_profile(tmp_path / "production.env", "192.168.0.10", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.setenv("RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED", "true")

    settings = Settings.from_env()

    assert settings.ftir_assignment_library_delete_enabled is True


def test_report_transfer_settings_override_profile(monkeypatch, tmp_path: Path) -> None:
    write_profile(tmp_path / "development.env", "bhyoon.me", "development")
    write_profile(tmp_path / "production.env", "192.168.0.10", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.setenv("RIST_REPORT_STORAGE_KEY", "RIST_SHARED_REPORTS")
    monkeypatch.setenv("RIST_REPORT_TRANSFER_MAX_ATTEMPTS", "7")

    settings = Settings.from_env()

    assert settings.report_storage_key == "RIST_SHARED_REPORTS"
    assert settings.report_transfer_max_attempts == 7


def test_posco_sso_settings_are_loaded_from_runtime_env(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_profile(tmp_path / "development.env", "bhyoon.me", "development")
    write_profile(tmp_path / "production.env", "192.168.0.10", "production")
    ca_bundle = tmp_path / "posco-ca.pem"
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.setenv("RIST_SSO_MODE", "POSCO")
    monkeypatch.setenv(
        "RIST_SSO_VALIDATION_URL",
        "https://uswpsso.posco.net/idms/U61/jsp/userValidSSOM.jsp",
    )
    monkeypatch.setenv("RIST_SSO_SID", "issued-sid")
    monkeypatch.setenv("RIST_SSO_CA_BUNDLE", str(ca_bundle))
    monkeypatch.setenv("RIST_SSO_CONNECT_TIMEOUT_SECONDS", "2.5")
    monkeypatch.setenv("RIST_SSO_READ_TIMEOUT_SECONDS", "4.5")

    settings = Settings.from_env()

    assert settings.sso_mode == "posco"
    assert settings.sso_validation_url.startswith("https://uswpsso.posco.net/")
    assert settings.sso_sid == "issued-sid"
    assert settings.sso_ca_bundle == ca_bundle
    assert settings.sso_connect_timeout_seconds == 2.5
    assert settings.sso_read_timeout_seconds == 4.5


def test_existing_oidc_settings_select_oidc_when_mode_is_not_explicit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    write_profile(tmp_path / "development.env", "bhyoon.me", "development")
    write_profile(tmp_path / "production.env", "192.168.0.10", "production")
    monkeypatch.setenv("RIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("RIST_ENV", "development")
    monkeypatch.delenv("RIST_SSO_MODE", raising=False)
    monkeypatch.setenv("RIST_SSO_ISSUER_URL", "https://sso.example.com")
    monkeypatch.setenv("RIST_SSO_CLIENT_ID", "client-id")
    monkeypatch.setenv("RIST_SSO_CLIENT_SECRET", "client-secret")

    assert Settings.from_env().sso_mode == "oidc"
