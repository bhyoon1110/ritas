import hashlib
import io
import zipfile
from copy import deepcopy

from ..render.html import render_html
from ..render.layout import archive_path
from ..render.items import sample_viewer_path
from ..render.link_audit import check_package_links, ppt_click_targets, read_linked_source, report_sources
from ..render.posco_preview import report_pages
from ..render.pdf import render_pdf
from ..render.posco_pptx import render_report_pptx
from ..render.viewer_export import export_html, sample_entry_html
from ..render.viewer_state import prepare_state, prepare_views


def build_package(project, document, resolve_source, viewer_state=None, views=None, *, output_path=None, include_sources=True):
    pages = report_pages(project, document, resolve_source, include_sources=include_sources)
    if not include_sources:
        for page in pages:
            page['items'] = [item for item in page['items'] if item['type'] != 'link']
    included = [entry['sample_id'] for entry in document['samples'] if entry['included']]
    samples = {s['id']: s for s in project['samples']}
    filtered = deepcopy(project)
    filtered['samples'] = [samples[sid] for sid in included]
    filtered['sources'] = [r for s in filtered['samples'] for r in s['sources']]
    # Retain source/review metadata in the embedded viewer without separate delivery sidecars.
    filtered['warnings'] = list(project.get('warnings', []))
    filtered['id'] = hashlib.sha256((project['id'] + ','.join(included)).encode()).hexdigest()[:24]
    display = prepare_state(project, viewer_state, filtered)
    display_views = prepare_views(project, views, filtered)
    output = output_path if output_path is not None else io.BytesIO()
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        # Recipient package: reports, viewer and original-data folders only.
        # Mail drafts and review history stay in server-side project/report storage.
        archive.writestr('보고서.pdf', render_pdf(pages, document))
        pptx = render_report_pptx(project, document, resolve_source, pages=pages, include_sources=include_sources)
        archive.writestr('보고서.pptx', pptx)
        archive.writestr('보고서.html', render_html(pages, document, 'package'))
        archive.writestr('뷰어.html', export_html(filtered, display, display_views))
        if include_sources:
            for sample in filtered['samples']:
                archive.writestr(sample_viewer_path(sample['id']),
                                 sample_entry_html(sample['id'], sample['name']))
        # Explicit directory entries even when a selected sample has no PDF.
        archive.writestr('Report/', '')
        archive.writestr('Measured Data/', '')
        seen = set()
        for record in report_sources(project, document) if include_sources else []:
            target = archive_path(record)
            if target in seen:
                raise ValueError(f'보고서 패키지에 파일명이 중복되었습니다: {target}')
            seen.add(target)
            data = read_linked_source(record, resolve_source)
            archive.writestr(target, data)
        check_package_links(ppt_click_targets(pptx), archive)
    return output_path if output_path is not None else output.getvalue()
