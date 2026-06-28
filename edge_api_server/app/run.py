from __future__ import annotations

import uvicorn

from .path_bootstrap import add_project_package_paths

add_project_package_paths()

from rist_common import get_logger

from .config import Settings

logger = get_logger(__name__)


def main() -> None:
    settings = Settings.from_env()
    logger.info(
        "Edge API 서버를 시작합니다 (env=%s, host=%s, port=%s)",
        settings.environment,
        settings.bind_host,
        settings.api_port,
    )
    uvicorn.run(
        "app.main:create_app",
        host=settings.bind_host,
        port=settings.api_port,
        workers=1,
        factory=True,
    )


if __name__ == "__main__":
    main()
