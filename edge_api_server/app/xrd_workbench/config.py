"""Runtime settings from environment variables and an optional, untracked .env.local."""

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BUNDLED_FONTS = ROOT / 'render' / 'assets'


def resolve_path(value: str) -> Path:
    """Resolve a configured path; relative paths are anchored at the repository root."""
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_local_env(path: Path = ROOT / '.env.local') -> None:
    """Load KEY=VALUE lines into os.environ without overriding values already set."""
    if os.environ.get('XRD_NO_LOCAL_ENV') == '1' or not path.is_file():
        return
    for line in path.read_text(encoding='utf-8-sig').splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        os.environ.setdefault(key.strip(), value.strip())


def _optional_path(name: str) -> Path | None:
    value = os.environ.get(name, '').strip()
    return resolve_path(value) if value else None


@dataclass(frozen=True)
class Settings:
    example_dir: Path | None
    example_mapping: Path | None
    data_fixtures: Path | None
    snapshot_dir: Path | None
    data_dir: Path
    host: str
    port: int
    allowed_origin_hosts: tuple[str, ...]
    font_regular: Path
    font_bold: Path
    pptx_font_family: str | None
    pptx_template: Path | None
    pptx_layout: str | None
    pptx_title_color: str


def _origin_hosts() -> tuple[str, ...]:
    """Hostnames allowed as Origin for write requests, normalised like urlsplit().hostname."""
    value = os.environ.get('XRD_ALLOWED_ORIGINS', '')
    hosts = tuple(
        host.strip().lower().strip('[]') for host in value.split(',') if host.strip(' []')
    )
    return hosts or ('localhost', '127.0.0.1', '::1')


def _port() -> int:
    """TCP port from XRD_PORT (default 8765); anything else is a setting error, not a traceback."""
    value = os.environ.get('XRD_PORT', '').strip()
    if not value:
        return 8765
    if not value.isdigit() or not 1 <= int(value) <= 65535:
        raise ValueError(f'XRD_PORT는 1–65535 사이의 숫자여야 합니다: {value!r}')
    return int(value)


def _title_color() -> str:
    """PPTX title colour from XRD_PPTX_TITLE_COLOR (#RRGGBB); anything else is a setting error."""
    value = os.environ.get('XRD_PPTX_TITLE_COLOR', '').strip()
    if not value:
        return '#1b3555'
    if not re.fullmatch('#[0-9a-fA-F]{6}', value):
        raise ValueError(f'XRD_PPTX_TITLE_COLOR는 #RRGGBB 형식이어야 합니다: {value!r}')
    return value


def load_settings() -> Settings:
    """Read settings from the current environment (no caching, so tests can change it)."""
    data_fixtures = _optional_path('XRD_DATA_FIXTURES')
    snapshot_dir = _optional_path('XRD_SNAPSHOT_DIR')
    if snapshot_dir is None and data_fixtures is not None:
        # Never write into the (confidential) data folder itself.
        snapshot_dir = ROOT / '_local' / 'snapshots'
    return Settings(
        example_dir=_optional_path('XRD_EXAMPLE_DIR'),
        example_mapping=_optional_path('XRD_EXAMPLE_MAPPING'),
        data_fixtures=data_fixtures,
        snapshot_dir=snapshot_dir,
        data_dir=_optional_path('XRD_DATA_DIR') or ROOT / 'data',
        host=os.environ.get('XRD_HOST', '').strip() or '127.0.0.1',
        port=_port(),
        allowed_origin_hosts=_origin_hosts(),
        font_regular=_optional_path('XRD_FONT_REGULAR')
        or _optional_path('XRD_KOREAN_FONT')
        or BUNDLED_FONTS / 'NotoSansKR-Regular.ttf',
        font_bold=_optional_path('XRD_FONT_BOLD')
        or _optional_path('XRD_KOREAN_BOLD_FONT')
        or BUNDLED_FONTS / 'NotoSansKR-Bold.ttf',
        pptx_font_family=os.environ.get('XRD_PPTX_FONT_FAMILY', '').strip() or None,
        pptx_template=_optional_path('XRD_PPTX_TEMPLATE'),
        pptx_layout=os.environ.get('XRD_PPTX_LAYOUT', '').strip() or None,
        pptx_title_color=_title_color(),
    )
