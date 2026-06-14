from __future__ import annotations

import uvicorn

from .config import Settings


def main() -> None:
    settings = Settings.from_env()
    uvicorn.run(
        "app.main:app",
        host=settings.bind_host,
        port=settings.api_port,
        workers=1,
    )


if __name__ == "__main__":
    main()
