"""Shared request lookup controls for the full Edge app and preview apps."""
from pathlib import Path

from fastapi import APIRouter, Response

router = APIRouter()


@router.get("/assets/request_filters.js", include_in_schema=False)
def request_filters_script() -> Response:
    return Response(
        content=Path(__file__).with_name("request_filters.js").read_text(encoding="utf-8"),
        media_type="application/javascript",
        headers={"Cache-Control": "no-cache"},
    )
