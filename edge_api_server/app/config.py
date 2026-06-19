from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from rist_common.config import load_environment


PROJECT_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Settings:
    storage_root: Path
    environment: str = "development"
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "rist_edge"
    db_user: str = "rist"
    db_password: str = ""
    edge_public_base_url: str = "http://192.168.0.10:8000"
    bind_host: str = "0.0.0.0"
    api_port: int = 8000
    upload_expiry_hours: float = 24.0
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    supported_experiment_codes: frozenset[str] = frozenset()
    llm_base_url: str = "http://127.0.0.1:8001"
    llm_model: str = "gemma4-e4b"
    llm_timeout_seconds: float = 180.0
    llm_temperature: float = 0.1
    llm_max_tokens: int = 1200
    llm_context_window: int = 8192
    llm_context_margin: int = 256
    llm_validate_model: bool = True
    llm_include_images: bool = True
    llm_max_images: int = 3
    llm_max_image_bytes: int = 2 * 1024 * 1024
    llm_max_input_chars: int = 200_000
    processor_timeout_seconds: float = 600.0
    worker_poll_seconds: float = 2.0

    @classmethod
    def from_env(cls) -> "Settings":
        common = load_environment()
        default_storage_root = (
            common.edge_storage_root or str(PROJECT_DIR / "data" / "jobs")
        )
        storage_root = Path(
            os.getenv("RIST_STORAGE_ROOT", default_storage_root)
        ).expanduser()
        supported = frozenset(
            code.strip().upper()
            for code in os.getenv("RIST_SUPPORTED_EXPERIMENT_CODES", "").split(",")
            if code.strip()
        )
        return cls(
            storage_root=storage_root,
            environment=common.environment,
            db_host=os.getenv("RIST_DB_HOST", "127.0.0.1").strip(),
            db_port=int(os.getenv("RIST_DB_PORT", "3306")),
            db_name=os.getenv("RIST_DB_NAME", "rist_edge"),
            db_user=os.getenv("RIST_DB_USER", "rist"),
            db_password=os.getenv("RIST_DB_PASSWORD", ""),
            edge_public_base_url=os.getenv(
                "RIST_EDGE_PUBLIC_BASE_URL", common.edge_server_base_url
            ).rstrip("/"),
            bind_host=os.getenv("RIST_EDGE_BIND_HOST", common.edge_bind_host),
            api_port=int(os.getenv("RIST_EDGE_API_PORT", common.edge_server_port)),
            upload_expiry_hours=float(
                os.getenv("RIST_UPLOAD_EXPIRY_HOURS", "24")
            ),
            max_upload_bytes=int(
                os.getenv("RIST_MAX_UPLOAD_BYTES", str(2 * 1024 * 1024 * 1024))
            ),
            supported_experiment_codes=supported,
            llm_base_url=os.getenv(
                "RIST_LLM_BASE_URL", common.local_llm_base_url
            ).rstrip("/"),
            llm_model=os.getenv("RIST_LLM_MODEL", common.local_llm_model),
            llm_timeout_seconds=float(
                os.getenv("RIST_LLM_TIMEOUT_SECONDS", "180")
            ),
            llm_temperature=float(
                os.getenv(
                    "RIST_LLM_TEMPERATURE",
                    str(common.local_llm_temperature),
                )
            ),
            llm_max_tokens=int(
                os.getenv(
                    "RIST_LLM_MAX_TOKENS",
                    str(common.local_llm_max_tokens),
                )
            ),
            llm_context_window=int(
                os.getenv(
                    "RIST_LLM_CONTEXT_WINDOW",
                    str(common.local_llm_context_window),
                )
            ),
            llm_context_margin=int(
                os.getenv(
                    "RIST_LLM_CONTEXT_MARGIN",
                    str(common.local_llm_context_margin),
                )
            ),
            llm_validate_model=os.getenv(
                "RIST_LLM_VALIDATE_MODEL",
                str(common.local_llm_validate_model),
            ).lower()
            in {"1", "true", "yes", "on"},
            llm_include_images=os.getenv(
                "RIST_LLM_INCLUDE_IMAGES",
                str(common.local_llm_include_images),
            ).lower()
            in {"1", "true", "yes", "on"},
            llm_max_images=int(
                os.getenv(
                    "RIST_LLM_MAX_IMAGES",
                    str(common.local_llm_max_images),
                )
            ),
            llm_max_image_bytes=int(
                os.getenv(
                    "RIST_LLM_MAX_IMAGE_BYTES",
                    str(common.local_llm_max_image_bytes),
                )
            ),
            llm_max_input_chars=int(
                os.getenv("RIST_LLM_MAX_INPUT_CHARS", "200000")
            ),
            processor_timeout_seconds=float(
                os.getenv("RIST_PROCESSOR_TIMEOUT_SECONDS", "600")
            ),
            worker_poll_seconds=float(
                os.getenv("RIST_WORKER_POLL_SECONDS", "2")
            ),
        )
