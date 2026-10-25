"""User-facing download names only; never use them as storage paths."""
import re
from .errors import ApiException


def report_download_name(value: str | None, default: str) -> str:
    if value is None:
        return default
    name = value.strip()
    if not name or len(name) > 150 or re.search(r'[\\/:*?"<>|\x00-\x1f\x7f]', name):
        raise ApiException(400, "DOWNLOAD_NAME_INVALID", "경로 없이 파일 이름만 입력하세요 (150자 이하).")
    stem = name[:-4] if name.lower().endswith(".zip") else name
    stem = stem.strip(". ")
    if not stem or re.match(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)", stem, re.I):
        raise ApiException(400, "DOWNLOAD_NAME_INVALID", "사용할 수 없는 파일 이름입니다.")
    return stem + ".zip"
