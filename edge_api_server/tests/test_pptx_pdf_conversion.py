from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from app.report import renderers


@pytest.fixture
def clean_font_env(monkeypatch):
    for name in ("SAL_FONTPATH", "FONTCONFIG_FILE", "FONTCONFIG_PATH", "RIST_PDF_FONT_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_macos_converter_discovers_fonts_with_private_config(tmp_path, monkeypatch, clean_font_env):
    monkeypatch.setattr(renderers.sys, "platform", "darwin")
    fonts = tmp_path / "한글 & Fonts"
    fonts.mkdir()
    monkeypatch.setattr(renderers, "_macos_pptx_font_dirs", lambda: [fonts, tmp_path / "missing"])

    env = renderers._pptx_pdf_converter_env(tmp_path)

    assert env["SAL_FONTPATH"] == str(fonts)
    config = ET.parse(env["FONTCONFIG_FILE"]).getroot()
    assert [node.text for node in config.findall("dir")] == [str(fonts)]
    assert config.findtext("cachedir") == str(tmp_path / "font-cache")
    assert (tmp_path / "font-cache").is_dir()
    assert "FONTCONFIG_FILE" not in os.environ
    assert "SAL_FONTPATH" not in os.environ


@pytest.mark.parametrize("config_key", ["FONTCONFIG_FILE", "FONTCONFIG_PATH"])
def test_converter_preserves_explicit_fontconfig(tmp_path, monkeypatch, clean_font_env, config_key):
    monkeypatch.setattr(renderers.sys, "platform", "darwin")
    monkeypatch.setattr(renderers, "_macos_pptx_font_dirs", lambda: [])
    monkeypatch.setenv(config_key, "/operator/fontconfig")

    env = renderers._pptx_pdf_converter_env(tmp_path)

    assert env[config_key] == "/operator/fontconfig"
    assert not (tmp_path / "fonts.conf").exists()


def test_converter_reuses_configured_pdf_font_directory(tmp_path, monkeypatch, clean_font_env):
    monkeypatch.setattr(renderers.sys, "platform", "linux")
    fonts = tmp_path / "custom-fonts"
    fonts.mkdir()
    font = fonts / "NanumGothic.ttf"
    font.write_bytes(b"test font")
    monkeypatch.setenv("RIST_PDF_FONT_PATH", str(font))
    monkeypatch.setenv("SAL_FONTPATH", os.pathsep.join(["/operator/fonts", str(fonts)]))

    env = renderers._pptx_pdf_converter_env(tmp_path)

    assert env["SAL_FONTPATH"].split(os.pathsep) == ["/operator/fonts", str(fonts)]
    assert "FONTCONFIG_FILE" not in env
    assert not (tmp_path / "fonts.conf").exists()


def test_linux_converter_leaves_default_font_discovery_unchanged(tmp_path, monkeypatch, clean_font_env):
    monkeypatch.setattr(renderers.sys, "platform", "linux")
    assert renderers._pptx_pdf_converter_env(tmp_path) == dict(os.environ)


def test_conversion_passes_child_environment_and_cleans_it_up(tmp_path, monkeypatch, clean_font_env):
    monkeypatch.setattr(renderers.sys, "platform", "darwin")
    monkeypatch.setattr(renderers, "_macos_pptx_font_dirs", lambda: [])
    monkeypatch.setenv("RIST_PPTX_TO_PDF_CONVERTER", "soffice-test")
    source = tmp_path / "source.pptx"
    source.write_bytes(b"fixture")
    output = tmp_path / "result.pdf"
    captured = {}

    def run(command, **kwargs):
        captured.update(kwargs)
        assert Path(kwargs["env"]["FONTCONFIG_FILE"]).is_file()
        out_dir = Path(command[command.index("--outdir") + 1])
        (out_dir / "source.pdf").write_bytes(b"%PDF-1.4\n%%EOF\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(renderers.subprocess, "run", run)
    assert renderers.convert_pptx_to_pdf(source, output) == output
    assert output.read_bytes().startswith(b"%PDF")
    assert not Path(captured["env"]["FONTCONFIG_FILE"]).exists()
    assert captured["timeout"] == 120
