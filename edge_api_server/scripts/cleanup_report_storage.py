#!/usr/bin/env python3
"""Apply report retention policies and purge expired report trash."""

from __future__ import annotations

import json
import logging

from app.config import Settings
from app.database import Database
from app.report_management import cleanup_expired_reports


def main() -> int:
    settings = Settings.from_env()
    database = Database.from_settings(settings)
    results = cleanup_expired_reports(
        settings=settings,
        database=database,
        actor="report-retention-timer",
    )
    print(
        json.dumps(
            {
                "cleanedReportCount": len(results),
                "reports": results,
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
