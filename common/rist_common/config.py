from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_DIR = PROJECT_ROOT / "config" / "environments"
VALID_ENVIRONMENTS = {"development", "production"}


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"환경 설정 파일을 찾을 수 없습니다: {path}")
    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ValueError(f"{path}:{line_number} 형식이 KEY=VALUE가 아닙니다.")
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if not key:
            raise ValueError(f"{path}:{line_number} 환경 변수 이름이 비어 있습니다.")
        values[key] = value
    return values


def _int_value(values: dict[str, str], key: str) -> int:
    try:
        return int(values[key])
    except (KeyError, ValueError) as exc:
        raise ValueError(f"{key}는 정수여야 합니다.") from exc


@dataclass(frozen=True)
class EnvironmentConfig:
    environment: str
    edge_server_scheme: str
    edge_server_host: str
    edge_server_port: int
    edge_server_base_url: str
    edge_bind_host: str
    local_llm_base_url: str
    local_llm_model: str
    local_llm_temperature: float
    local_llm_max_tokens: int
    local_llm_context_window: int
    local_llm_context_margin: int
    local_llm_validate_model: bool
    local_llm_include_images: bool
    local_llm_max_images: int
    local_llm_max_image_bytes: int
    public_domain: str
    source_file: Path


def _bool_value(values: dict[str, str], key: str) -> bool:
    value = values[key].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{key}는 true 또는 false여야 합니다.")


def load_environment(environment: str | None = None) -> EnvironmentConfig:
    selected = (environment or os.getenv("RIST_ENV", "development")).strip().lower()
    if selected not in VALID_ENVIRONMENTS:
        allowed = ", ".join(sorted(VALID_ENVIRONMENTS))
        raise ValueError(f"RIST_ENV는 다음 중 하나여야 합니다: {allowed}")

    config_dir = Path(
        os.getenv("RIST_CONFIG_DIR", str(DEFAULT_CONFIG_DIR))
    ).expanduser()
    source_file = config_dir / f"{selected}.env"
    file_values = _parse_env_file(source_file)
    values = {
        key: os.getenv(key, value)
        for key, value in file_values.items()
    }

    required = {
        "EDGE_SERVER_SCHEME",
        "EDGE_SERVER_HOST",
        "EDGE_SERVER_PORT",
        "EDGE_SERVER_BASE_URL",
        "EDGE_BIND_HOST",
        "LOCAL_LLM_BASE_URL",
        "LOCAL_LLM_MODEL",
        "LOCAL_LLM_TEMPERATURE",
        "LOCAL_LLM_MAX_TOKENS",
        "LOCAL_LLM_CONTEXT_WINDOW",
        "LOCAL_LLM_CONTEXT_MARGIN",
        "LOCAL_LLM_VALIDATE_MODEL",
        "LOCAL_LLM_INCLUDE_IMAGES",
        "LOCAL_LLM_MAX_IMAGES",
        "LOCAL_LLM_MAX_IMAGE_BYTES",
        "PUBLIC_DOMAIN",
    }
    missing = sorted(key for key in required if not values.get(key))
    if missing:
        raise ValueError(f"필수 환경 설정이 없습니다: {', '.join(missing)}")

    base_url = values["EDGE_SERVER_BASE_URL"].rstrip("/")
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("EDGE_SERVER_BASE_URL은 유효한 http(s) URL이어야 합니다.")

    return EnvironmentConfig(
        environment=selected,
        edge_server_scheme=values["EDGE_SERVER_SCHEME"],
        edge_server_host=values["EDGE_SERVER_HOST"],
        edge_server_port=_int_value(values, "EDGE_SERVER_PORT"),
        edge_server_base_url=base_url,
        edge_bind_host=values["EDGE_BIND_HOST"],
        local_llm_base_url=values["LOCAL_LLM_BASE_URL"].rstrip("/"),
        local_llm_model=values["LOCAL_LLM_MODEL"],
        local_llm_temperature=float(values["LOCAL_LLM_TEMPERATURE"]),
        local_llm_max_tokens=_int_value(values, "LOCAL_LLM_MAX_TOKENS"),
        local_llm_context_window=_int_value(values, "LOCAL_LLM_CONTEXT_WINDOW"),
        local_llm_context_margin=_int_value(values, "LOCAL_LLM_CONTEXT_MARGIN"),
        local_llm_validate_model=_bool_value(values, "LOCAL_LLM_VALIDATE_MODEL"),
        local_llm_include_images=_bool_value(values, "LOCAL_LLM_INCLUDE_IMAGES"),
        local_llm_max_images=_int_value(values, "LOCAL_LLM_MAX_IMAGES"),
        local_llm_max_image_bytes=_int_value(values, "LOCAL_LLM_MAX_IMAGE_BYTES"),
        public_domain=values["PUBLIC_DOMAIN"],
        source_file=source_file,
    )
