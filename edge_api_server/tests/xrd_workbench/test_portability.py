"""Spec G2: every output works with `pip install` alone (no Node, Codex runtime or Windows fonts)."""

import io
import re
import zipfile

from app.xrd_workbench.config import ROOT
from app.xrd_workbench.core.project import build_project
from app.xrd_workbench.core.report import new_document
from app.xrd_workbench.core.review import apply_review
from app.xrd_workbench.render.layout import build_pages
from app.xrd_workbench.render.package import build_package
from app.xrd_workbench.render.pdf import render_pdf
from app.xrd_workbench.render.pptx import render_pptx

TXT = '#Intensity_unit=cps\n' + ''.join(f'{10 + i * 0.02:.2f} {100 + i % 7}\n' for i in range(200))
FORBIDDEN = re.compile(r'codex|artifact_tool|@oai|C:/Windows|\.cache|node\.exe', re.I)


def test_all_outputs_work_with_only_bundled_fonts(monkeypatch):
    for key in (
        'XRD_FONT_REGULAR',
        'XRD_FONT_BOLD',
        'XRD_KOREAN_FONT',
        'XRD_KOREAN_BOLD_FONT',
        'XRD_PPTX_FONT_FAMILY',
    ):
        monkeypatch.delenv(key, raising=False)
    project = apply_review(build_project([('시료A.txt', TXT.encode())]))
    project['storage_id'] = 'example'
    document = new_document(project)
    document['revision'] = 1
    document['metadata']['title'] = '이식성 확인 보고서'
    sources = {r['id']: TXT.encode() for r in project['sources']}
    pages = build_pages(project, document, sources.__getitem__)
    assert render_pdf(pages, document).startswith(b'%PDF')
    with zipfile.ZipFile(io.BytesIO(render_pptx(pages, document))) as deck:
        assert 'Noto Sans KR' in deck.read('ppt/slides/slide1.xml').decode('utf-8')
    package_bytes = build_package(project, document, sources.__getitem__)
    with zipfile.ZipFile(io.BytesIO(package_bytes)) as package:
        assert {'보고서.pdf', '보고서.pptx', '보고서.html', '뷰어.html'} <= set(package.namelist())


def test_application_code_has_no_codex_runtime_or_os_specific_paths():
    offenders = [
        f'{path.relative_to(ROOT)}:{n}'
        for path in ROOT.rglob('*.py')
        for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1)
        if FORBIDDEN.search(line)
    ]
    assert not offenders, offenders


def test_deck_is_a_clean_a4_deliverable_without_template_leftovers():
    from pptx import Presentation

    project = apply_review(build_project([('시료A.txt', TXT.encode())]))
    document = new_document(project)
    document['revision'] = 1
    document['metadata']['author'] = '담당자'
    sources = {r['id']: TXT.encode() for r in project['sources']}
    pages = build_pages(project, document, sources.__getitem__)
    with zipfile.ZipFile(io.BytesIO(render_pptx(pages, document))) as deck:
        assert 'type="screen4x3"' not in deck.read('ppt/presentation.xml').decode('utf-8')
    props = Presentation(io.BytesIO(render_pptx(pages, document))).core_properties
    assert props.last_modified_by in ('', '담당자')
    assert 'python-pptx' not in (props.comments or '')
    assert props.created.year >= 2026 and props.modified.year >= 2026
