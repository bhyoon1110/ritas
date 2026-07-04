#!/usr/bin/env python3
"""Insert synthetic LIMS request rows for FT-IR/Raman preview testing.

The script reads MariaDB settings from RIST_DB_* environment variables by default.
Use --env-file ../edge.env on the edge server to reuse the service configuration.
"""

from __future__ import annotations

import argparse
import getpass
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pymysql


SEED_OWNER = "edge-seed"
SEED_REQUEST_NUMBERS = ("TEST-FTIR-20260704-001", "TEST-RAMAN-20260704-001")


@dataclass(frozen=True)
class DbConfig:
    host: str
    port: int
    database: str
    user: str
    password: str


def _parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip().strip("'").strip('"')
    return key, value


def load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)


def db_config(args: argparse.Namespace) -> DbConfig:
    password = args.password or os.getenv("RIST_DB_PASSWORD", "")
    if args.password_prompt:
        password = getpass.getpass("MariaDB password: ")
    return DbConfig(
        host=args.host or os.getenv("RIST_DB_HOST", "127.0.0.1"),
        port=args.port or int(os.getenv("RIST_DB_PORT", "3306")),
        database=args.database or os.getenv("RIST_DB_NAME", "rist_edge"),
        user=args.user or os.getenv("RIST_DB_USER", "rist"),
        password=password,
    )


def seed_rows() -> list[dict[str, Any]]:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return [
        {
            "req_result_no": 990001,
            "req_number": SEED_REQUEST_NUMBERS[0],
            "req_date": "2026-07-04",
            "req_state": 8,
            "req_state_name": "접수",
            "req_type_no": 9001,
            "req_type_code": "TEST",
            "req_type_name": "테스트 분석의뢰",
            "project_code": "TEST-FTIR",
            "cust_req_name": "FT-IR 화면 테스트용 가상 의뢰",
            "customer_no": 900001,
            "customer_name": "RIST 테스트 고객",
            "req_user_no": 900101,
            "req_user_name": SEED_OWNER,
            "smp_result_no": 991001,
            "smp_result_name": "FT-IR 테스트 시료",
            "smp_result_state": 3,
            "test_mtd_result_no": 992001,
            "test_mtd_no": 993001,
            "test_mtd_code": "FTIR-TEST",
            "test_mtd_name": "FT-IR 정성분석 테스트",
            "test_state": 2,
            "test_charger_name": "테스트 실험자",
            "output_order": 1,
            "synced_at": now,
        },
        {
            "req_result_no": 990002,
            "req_number": SEED_REQUEST_NUMBERS[1],
            "req_date": "2026-07-04",
            "req_state": 8,
            "req_state_name": "접수",
            "req_type_no": 9002,
            "req_type_code": "TEST",
            "req_type_name": "테스트 분석의뢰",
            "project_code": "TEST-RAMAN",
            "cust_req_name": "Raman 화면 테스트용 가상 의뢰",
            "customer_no": 900001,
            "customer_name": "RIST 테스트 고객",
            "req_user_no": 900102,
            "req_user_name": SEED_OWNER,
            "smp_result_no": 991002,
            "smp_result_name": "Raman 테스트 시료",
            "smp_result_state": 3,
            "test_mtd_result_no": 992002,
            "test_mtd_no": 993002,
            "test_mtd_code": "RAMAN-TEST",
            "test_mtd_name": "Raman 정성분석 테스트",
            "test_state": 2,
            "test_charger_name": "테스트 실험자",
            "output_order": 1,
            "synced_at": now,
        },
    ]


def insert_seed_rows(config: DbConfig) -> None:
    rows = seed_rows()
    columns = tuple(rows[0].keys())
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)
    delete_placeholders = ", ".join(["%s"] * len(SEED_REQUEST_NUMBERS))
    with pymysql.connect(
        host=config.host,
        port=config.port,
        user=config.user,
        password=config.password,
        database=config.database,
        charset="utf8mb4",
        autocommit=False,
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                DELETE FROM lims_req_ax_search
                WHERE req_number IN ({delete_placeholders})
                  AND req_user_name = %s
                """,
                (*SEED_REQUEST_NUMBERS, SEED_OWNER),
            )
            deleted = cursor.rowcount
            cursor.executemany(
                f"""
                INSERT INTO lims_req_ax_search ({column_sql})
                VALUES ({placeholders})
                """,
                [tuple(row[column] for column in columns) for row in rows],
            )
            inserted = cursor.rowcount
        connection.commit()
    print(
        "seed complete: "
        f"deleted={deleted}, inserted={inserted}, requests={', '.join(SEED_REQUEST_NUMBERS)}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Insert FT-IR/Raman synthetic rows into lims_req_ax_search."
    )
    parser.add_argument("--env-file", type=Path, help="Optional edge.env path")
    parser.add_argument("--host", help="MariaDB host, defaults to RIST_DB_HOST")
    parser.add_argument("--port", type=int, help="MariaDB port, defaults to RIST_DB_PORT")
    parser.add_argument("--database", help="MariaDB database, defaults to RIST_DB_NAME")
    parser.add_argument("--user", help="MariaDB user, defaults to RIST_DB_USER")
    parser.add_argument("--password", help=argparse.SUPPRESS)
    parser.add_argument(
        "--password-prompt",
        action="store_true",
        help="Prompt for MariaDB password instead of reading RIST_DB_PASSWORD.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.env_file:
        load_env_file(args.env_file.expanduser())
    insert_seed_rows(db_config(args))


if __name__ == "__main__":
    main()
