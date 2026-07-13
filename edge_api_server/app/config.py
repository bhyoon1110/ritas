from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from rist_common.config import load_environment


PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_LLM_MODEL = "gemma4-e4b"
DEFAULT_LLM_TEMPERATURE = 0.1
DEFAULT_LLM_MAX_TOKENS = 1200
DEFAULT_LLM_CONTEXT_WINDOW = 8192
DEFAULT_LLM_CONTEXT_MARGIN = 256
DEFAULT_LLM_VALIDATE_MODEL = True
DEFAULT_LLM_INCLUDE_IMAGES = True
DEFAULT_LLM_MAX_IMAGES = 3
DEFAULT_LLM_MAX_IMAGE_BYTES = 2 * 1024 * 1024
DEFAULT_SPRING_CALLBACK_URL = "http://127.0.0.1:8080/api/v1/edge/reports"


@dataclass(frozen=True)
class Settings:
    storage_root: Path
    error_archive_root: Path | None = None
    error_retention_days: int = 30
    error_capture_files: bool = True
    error_max_file_bytes: int = 512 * 1024 * 1024
    error_max_total_bytes: int = 2 * 1024 * 1024 * 1024
    usage_log_root: Path | None = None
    usage_log_retention_days: int = 90
    ftir_assignment_library_dir: Path = (
        PROJECT_DIR / "data" / "ftir_assignment_libraries"
    )
    ftir_assignment_library_delete_enabled: bool = False
    environment: str = "development"
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_name: str = "rist_edge"
    db_user: str = "rist"
    db_password: str = ""
    db_pool_size: int = 8
    db_pool_timeout_seconds: float = 10.0
    pdf_font_path: Path | None = None
    edge_public_base_url: str = "http://192.168.0.10:8000"
    bind_host: str = "0.0.0.0"
    api_port: int = 8000
    upload_expiry_hours: float = 24.0
    max_upload_bytes: int = 2 * 1024 * 1024 * 1024
    supported_experiment_codes: frozenset[str] = frozenset()
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_timeout_seconds: float = 180.0
    llm_temperature: float = DEFAULT_LLM_TEMPERATURE
    llm_max_tokens: int = DEFAULT_LLM_MAX_TOKENS
    llm_context_window: int = DEFAULT_LLM_CONTEXT_WINDOW
    llm_context_margin: int = DEFAULT_LLM_CONTEXT_MARGIN
    llm_validate_model: bool = DEFAULT_LLM_VALIDATE_MODEL
    llm_include_images: bool = DEFAULT_LLM_INCLUDE_IMAGES
    llm_max_images: int = DEFAULT_LLM_MAX_IMAGES
    llm_max_image_bytes: int = DEFAULT_LLM_MAX_IMAGE_BYTES
    llm_max_input_chars: int = 200_000
    processor_timeout_seconds: float = 600.0
    worker_poll_seconds: float = 2.0
    spring_callback_url: str = ""
    spring_callback_timeout_seconds: float = 60.0
    spring_callback_max_attempts: int = 3

    @classmethod
    def from_env(cls) -> "Settings":
        common = load_environment()
        default_storage_root = str(PROJECT_DIR / "data" / "jobs")
        storage_root = Path(
            os.getenv("RIST_STORAGE_ROOT", default_storage_root)
        ).expanduser()
        error_archive_root = Path(
            os.getenv("RIST_ERROR_ARCHIVE_ROOT", str(storage_root / "errors"))
        ).expanduser()
        usage_log_root = Path(
            os.getenv("RIST_USAGE_LOG_ROOT", str(storage_root / "usage"))
        ).expanduser()
        ftir_assignment_library_dir = Path(
            os.getenv(
                "RIST_FTIR_ASSIGNMENT_LIBRARY_DIR",
                str(PROJECT_DIR / "data" / "ftir_assignment_libraries"),
            )
        ).expanduser()
        configured_pdf_font = os.getenv("RIST_PDF_FONT_PATH", "").strip()
        supported = frozenset(
            code.strip().upper()
            for code in os.getenv("RIST_SUPPORTED_EXPERIMENT_CODES", "").split(",")
            if code.strip()
        )
        return cls(
            storage_root=storage_root,
            error_archive_root=error_archive_root,
            error_retention_days=max(
                1, int(os.getenv("RIST_ERROR_RETENTION_DAYS", "30"))
            ),
            error_capture_files=os.getenv(
                "RIST_ERROR_CAPTURE_FILES", "true"
            ).lower()
            in {"1", "true", "yes", "on"},
            error_max_file_bytes=max(
                1,
                int(
                    os.getenv(
                        "RIST_ERROR_MAX_FILE_BYTES",
                        str(512 * 1024 * 1024),
                    )
                ),
            ),
            error_max_total_bytes=max(
                1,
                int(
                    os.getenv(
                        "RIST_ERROR_MAX_TOTAL_BYTES",
                        str(2 * 1024 * 1024 * 1024),
                    )
                ),
            ),
            usage_log_root=usage_log_root,
            usage_log_retention_days=max(
                1, int(os.getenv("RIST_USAGE_LOG_RETENTION_DAYS", "90"))
            ),
            ftir_assignment_library_dir=ftir_assignment_library_dir,
            ftir_assignment_library_delete_enabled=os.getenv(
                "RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED",
                "false",
            ).lower()
            in {"1", "true", "yes", "on"},
            environment=common.environment,
            db_host=os.getenv("RIST_DB_HOST", "127.0.0.1").strip(),
            db_port=int(os.getenv("RIST_DB_PORT", "3306")),
            db_name=os.getenv("RIST_DB_NAME", "rist_edge"),
            db_user=os.getenv("RIST_DB_USER", "rist"),
            db_password=os.getenv("RIST_DB_PASSWORD", ""),
            db_pool_size=int(os.getenv("RIST_DB_POOL_SIZE", "8")),
            db_pool_timeout_seconds=float(
                os.getenv("RIST_DB_POOL_TIMEOUT_SECONDS", "10")
            ),
            pdf_font_path=(
                Path(configured_pdf_font).expanduser()
                if configured_pdf_font
                else None
            ),
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
                "RIST_LLM_BASE_URL", DEFAULT_LLM_BASE_URL
            ).rstrip("/"),
            llm_model=os.getenv("RIST_LLM_MODEL", DEFAULT_LLM_MODEL),
            llm_timeout_seconds=float(
                os.getenv("RIST_LLM_TIMEOUT_SECONDS", "180")
            ),
            llm_temperature=float(
                os.getenv(
                    "RIST_LLM_TEMPERATURE",
                    str(DEFAULT_LLM_TEMPERATURE),
                )
            ),
            llm_max_tokens=int(
                os.getenv(
                    "RIST_LLM_MAX_TOKENS",
                    str(DEFAULT_LLM_MAX_TOKENS),
                )
            ),
            llm_context_window=int(
                os.getenv(
                    "RIST_LLM_CONTEXT_WINDOW",
                    str(DEFAULT_LLM_CONTEXT_WINDOW),
                )
            ),
            llm_context_margin=int(
                os.getenv(
                    "RIST_LLM_CONTEXT_MARGIN",
                    str(DEFAULT_LLM_CONTEXT_MARGIN),
                )
            ),
            llm_validate_model=os.getenv(
                "RIST_LLM_VALIDATE_MODEL",
                str(DEFAULT_LLM_VALIDATE_MODEL),
            ).lower()
            in {"1", "true", "yes", "on"},
            llm_include_images=os.getenv(
                "RIST_LLM_INCLUDE_IMAGES",
                str(DEFAULT_LLM_INCLUDE_IMAGES),
            ).lower()
            in {"1", "true", "yes", "on"},
            llm_max_images=int(
                os.getenv(
                    "RIST_LLM_MAX_IMAGES",
                    str(DEFAULT_LLM_MAX_IMAGES),
                )
            ),
            llm_max_image_bytes=int(
                os.getenv(
                    "RIST_LLM_MAX_IMAGE_BYTES",
                    str(DEFAULT_LLM_MAX_IMAGE_BYTES),
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
            spring_callback_url=os.getenv(
                "RIST_SPRING_CALLBACK_URL", DEFAULT_SPRING_CALLBACK_URL
            ).rstrip("/"),
            spring_callback_timeout_seconds=float(
                os.getenv("RIST_SPRING_CALLBACK_TIMEOUT_SECONDS", "60")
            ),
            spring_callback_max_attempts=int(
                os.getenv("RIST_SPRING_CALLBACK_MAX_ATTEMPTS", "3")
            ),
        )
