import hashlib
import io
import json
import zipfile
from copy import deepcopy
from ..core.email import email_markdown

from ..render.html import render_html
from ..render.layout import archive_path
from ..render.posco_preview import report_pages
from ..render.pdf import render_pdf
from ..render.posco_pptx import render_report_pptx
from ..render.viewer_export import export_html
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
    # Keep exclusions/unit warnings visible to the recipient.
    filtered['warnings'] = list(project.get('warnings', []))
    filtered['id'] = hashlib.sha256((project['id'] + ','.join(included)).encode()).hexdigest()[:24]
    display = prepare_state(project, viewer_state, filtered)
    display_views = prepare_views(project, views, filtered)
    output = output_path if output_path is not None else io.BytesIO()
    manifest = []
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr('보고서.pdf', render_pdf(pages, document))
        archive.writestr('보고서.pptx', render_report_pptx(project, document, resolve_source,
                                                    pages=pages, include_sources=include_sources))
        archive.writestr('보고서.html', render_html(pages, document, 'package'))
        archive.writestr('뷰어.html', export_html(filtered, display, display_views))
        mail = email_markdown(document)
        if mail:
            archive.writestr('email_body.md', mail)
        # Explicit directory entries even when a selected sample has no PDF.
        archive.writestr('Report/', '')
        archive.writestr('Measured Data/', '')
        seen = set()
        for record in filtered['sources']:
            if not include_sources:
                break
            if record['role'] not in ('phase_report', 'wppf_report', 'measurement'):
                continue
            target = archive_path(record)
            if target in seen:
                continue
            seen.add(target)
            data = resolve_source(record['id'])
            if hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('원본 파일 내용이 등록 후 변경되었습니다. 자료를 다시 확인하세요.')
            archive.writestr(target, data)
            manifest.append(
                dict(path=target, original_filename=record['filename'], sha256=record['sha256'])
            )
        archive.writestr(
            '자료목록.json',
            json.dumps(
                {
                    'document_revision': document['revision'],
                    'analysis_revision': project.get('analysis_revision', 0),
                    'files': manifest,
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            '분석검토.json',
            json.dumps(
                {
                    'analysis_revision': project.get('analysis_revision', 0),
                    'samples': [
                        {
                            'sample_id': s['id'],
                            'name': s['name'],
                            'review': s.get('analysis_review', {}),
                        }
                        for s in filtered['samples']
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
        )
        archive.writestr(
            '읽어주세요.txt',
            'ZIP을 먼저 압축 해제하고 보고서.html을 여세요.\n보고서 PDF·편집 가능한 PPTX와 Multi Profile Viewer를 열 수 있습니다.\nPPTX의 제목·본문·캡션은 텍스트 개체이며, 원본 이미지는 별도 그림입니다. 그림 안의 글자·곡선은 개별 편집되지 않습니다.\n원본 PDF Report: Report 폴더\n원본 TXT: Measured Data 폴더\nPDF/PowerPoint마다 상대 링크 정책이 다를 수 있으므로 원본 링크는 보고서.html에서 여세요.\n'
            + ('email_body.md: 작성자가 저장한 이메일 제목·본문이며, 실제 발송 여부를 의미하지 않습니다.\n' if mail else ''),
        )
    return output_path if output_path is not None else output.getvalue()
