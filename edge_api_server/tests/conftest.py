from __future__ import annotations

import os
from uuid import uuid4

import pymysql
import pytest


@pytest.fixture
def tem_unit_runtime(monkeypatch, tmp_path):
    """Offline unit tests never launch Office or write into operational storage."""
    from app import tem_report_editor
    from reportlab.pdfgen.canvas import Canvas
    from zipfile import ZipFile

    monkeypatch.setenv("RIST_AUTH_ENABLED", "false")
    monkeypatch.setenv("RIST_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setenv("RIST_ERROR_ARCHIVE_ROOT", str(tmp_path / "errors"))
    monkeypatch.setenv("RIST_USAGE_LOG_ROOT", str(tmp_path / "usage"))

    def convert(source, target):
        with ZipFile(source) as archive:
            count = len([n for n in archive.namelist() if n.startswith("ppt/slides/slide") and n.endswith(".xml")])
        canvas = Canvas(str(target))
        for _ in range(count):
            canvas.drawString(40, 40, "Unit-test preview placeholder")
            canvas.showPage()
        canvas.save()
        return target

    monkeypatch.setattr(tem_report_editor, "convert_pptx_to_pdf", convert)


def _admin_params() -> dict[str, object]:
    return {
        "host": os.getenv("RIST_TEST_DB_HOST", "127.0.0.1"),
        "port": int(os.getenv("RIST_TEST_DB_PORT", "3306")),
        "user": os.getenv("RIST_TEST_DB_USER", "root"),
        "password": os.getenv("RIST_TEST_DB_PASSWORD", ""),
    }


@pytest.fixture
def mariadb() -> dict[str, object]:
    """테스트마다 격리된 MariaDB 데이터베이스를 생성하고 종료 시 삭제한다.

    접속 정보는 RIST_TEST_DB_* 환경 변수로 재정의할 수 있다. 테스트용
    MariaDB 에 연결할 수 없으면 해당 테스트를 건너뛴다.
    """
    params = _admin_params()
    db_name = f"rist_test_{uuid4().hex[:12]}"

    try:
        admin = pymysql.connect(charset="utf8mb4", autocommit=True, **params)
    except pymysql.err.MySQLError as exc:  # pragma: no cover - 환경 의존
        pytest.skip(f"테스트용 MariaDB에 연결할 수 없습니다: {exc}")

    try:
        with admin.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE `{db_name}` CHARACTER SET utf8mb4 "
                "COLLATE utf8mb4_unicode_ci"
            )
    finally:
        admin.close()

    yield {
        "host": params["host"],
        "port": params["port"],
        "user": params["user"],
        "password": params["password"],
        "name": db_name,
    }

    cleanup = pymysql.connect(charset="utf8mb4", autocommit=True, **params)
    try:
        with cleanup.cursor() as cursor:
            cursor.execute(f"DROP DATABASE IF EXISTS `{db_name}`")
    finally:
        cleanup.close()
