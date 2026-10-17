"""Report fonts come from settings; the bundled Noto Sans KR is the portable default."""

import pytest

from app.xrd_workbench.config import BUNDLED_FONTS, ROOT, load_settings
from app.xrd_workbench.render import fonts


@pytest.fixture
def no_font_env(monkeypatch):
    for key in (
        'XRD_FONT_REGULAR',
        'XRD_FONT_BOLD',
        'XRD_KOREAN_FONT',
        'XRD_KOREAN_BOLD_FONT',
        'XRD_PPTX_FONT_FAMILY',
    ):
        monkeypatch.delenv(key, raising=False)
    return monkeypatch


def test_bundled_noto_sans_kr_is_the_default(no_font_env):
    settings = load_settings()
    assert settings.font_regular == BUNDLED_FONTS / 'NotoSansKR-Regular.ttf'
    assert settings.font_bold == BUNDLED_FONTS / 'NotoSansKR-Bold.ttf'
    assert settings.pptx_font_family is None  # derived from the font file by pptx_family()
    assert settings.font_regular.is_file() and settings.font_bold.is_file()
    assert (BUNDLED_FONTS / 'OFL.txt').is_file()


def test_new_and_legacy_variables_select_fonts(no_font_env, tmp_path):
    no_font_env.setenv('XRD_KOREAN_FONT', str(tmp_path / 'legacy.ttf'))
    assert load_settings().font_regular == tmp_path / 'legacy.ttf'
    no_font_env.setenv('XRD_FONT_REGULAR', 'fonts/new.ttf')
    assert load_settings().font_regular == ROOT / 'fonts' / 'new.ttf'


def test_registration_name_follows_the_font_file(no_font_env):
    regular, bold = fonts.font_names()
    assert regular != bold
    assert fonts.wrap_lines('한국어 보고서 문장', 30, 10)
    no_font_env.setenv('XRD_FONT_BOLD', str(BUNDLED_FONTS / 'NotoSansKR-Regular.ttf'))
    assert fonts.font_names() == (regular, regular)


def test_missing_font_file_names_the_setting(no_font_env, tmp_path):
    no_font_env.setenv('XRD_FONT_REGULAR', str(tmp_path / 'missing.ttf'))
    with pytest.raises(ValueError, match='XRD_FONT_REGULAR'):
        fonts.font_names()


def test_a_file_that_is_not_a_truetype_font_is_a_clear_setting_error(no_font_env, tmp_path):
    fake = tmp_path / 'not-a-font.ttf'
    fake.write_bytes(b'not a font')
    no_font_env.setenv('XRD_FONT_REGULAR', str(fake))
    with pytest.raises(ValueError, match='XRD_FONT_REGULAR'):
        fonts.font_names()


def test_an_explicit_bold_font_that_is_missing_is_an_error_not_a_silent_fallback(
    no_font_env, tmp_path
):
    no_font_env.setenv('XRD_FONT_BOLD', str(tmp_path / 'missing-bold.ttf'))
    with pytest.raises(ValueError, match='XRD_FONT_BOLD'):
        fonts.font_names()


def test_pptx_family_follows_the_configured_regular_font_unless_overridden(no_font_env):
    assert fonts.pptx_family() == 'Noto Sans KR'
    no_font_env.setenv('XRD_PPTX_FONT_FAMILY', 'Custom Family')
    assert fonts.pptx_family() == 'Custom Family'


def test_measuring_text_does_not_look_the_font_files_up_again_each_time(no_font_env, monkeypatch):
    from pathlib import Path

    fonts.text_width('warm up 한글', 10)  # first use registers the fonts
    calls = {'resolve': 0, 'is_file': 0}
    real_resolve, real_is_file = Path.resolve, Path.is_file

    def resolve(self, *a, **k):
        calls['resolve'] += 1
        return real_resolve(self, *a, **k)

    def is_file(self, *a, **k):
        calls['is_file'] += 1
        return real_is_file(self, *a, **k)

    monkeypatch.setattr(Path, 'resolve', resolve)
    monkeypatch.setattr(Path, 'is_file', is_file)

    def measure(times):
        for _ in range(times):
            fonts.text_width('시료 비교 그래프 PhaseA ₂θ', 10)
            fonts.text_width('굵게', 10, bold=True)

    measure(5)
    settled = dict(calls)
    measure(200)
    assert calls == settled  # no file lookup per measurement


def test_a_new_font_path_is_still_registered_and_a_bad_one_still_fails(no_font_env, tmp_path):
    good = tmp_path / 'copy.ttf'
    good.write_bytes((BUNDLED_FONTS / 'NotoSansKR-Regular.ttf').read_bytes())
    no_font_env.setenv('XRD_FONT_REGULAR', str(good))
    assert fonts.text_width('abc', 10) > 0
    bad = tmp_path / 'bad.ttf'
    bad.write_bytes(b'not a font')
    no_font_env.setenv('XRD_FONT_REGULAR', str(bad))
    with pytest.raises(ValueError):
        fonts.text_width('abc', 10)
    no_font_env.setenv('XRD_FONT_REGULAR', str(tmp_path / 'missing.ttf'))
    with pytest.raises(ValueError):
        fonts.text_width('abc', 10)
