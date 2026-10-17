"""Source-linked review API and bounded original PDF page rasterization."""

import io
import json
import threading

import pdfplumber
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response

from ..core.errors import RevisionConflict
from ..core.parsing.pdf_report import header_type
from ..core.review import apply_review, new_review, report_sources

_render_lock = threading.Lock()


def render_page(data, page_number):
    with _render_lock, pdfplumber.open(io.BytesIO(data)) as pdf:
        if not 1 <= page_number <= len(pdf.pages):
            raise ValueError('원본 PDF 페이지 범위를 확인하세요.')
        page = pdf.pages[page_number - 1]
        width, height = float(page.width), float(page.height)
        if width <= 0 or height <= 0:
            raise ValueError('PDF 페이지 크기가 올바르지 않습니다.')
        dpi = min(120, 1800 * 72 / max(width, height))
        original = page.to_image(resolution=dpi).original
        buffer = io.BytesIO()
        original.save(buffer, format='PNG')
        return buffer.getvalue(), width, height


def build_analysis_router(project_loader, source_loader, store):
    router = APIRouter()

    def context(sid):
        try:
            project = project_loader(sid)
            review = store.load(sid) or new_review(project)
            if review['project_id'] != project['id']:
                raise RevisionConflict('원본과 분석 검토 프로젝트가 다릅니다.')
            return project, review
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, FileNotFoundError, KeyError) as exc:
            raise HTTPException(404, str(exc)) from exc

    def payload(project, review):
        effective = apply_review(project, review)
        summary = [
            {
                key: s[key]
                for key in (
                    'id',
                    'name',
                    'phases',
                    'quantities',
                    'pdf_quantities',
                    'warnings',
                    'analysis_review',
                )
            }
            for s in effective['samples']
        ]
        headers = {
            sid: {
                str(i): [j for j, row in enumerate(t['rows']) if header_type(row)]
                for i, t in enumerate(report['raw_tables'])
            }
            for sid, (_, report) in report_sources(project).items()
        }
        return {
            'project': project,
            'review': review,
            'effective_samples': summary,
            'header_rows': headers,
        }

    @router.get('/api/analysis/{storage_id}')
    def get_review(storage_id: str):
        with store.lock:
            project, review = context(storage_id)
            return payload(project, review)

    @router.put('/api/analysis/{storage_id}')
    async def save_review(storage_id: str, request: Request):
        raw = await request.body()
        if len(raw) > 8 * 1024 * 1024:
            raise HTTPException(422, '분석 검토 요청은 8MB 이하로 저장하세요.')
        try:
            value = json.loads(raw)
            with store.lock:
                project, _ = context(storage_id)
                # A review must not combine old extraction with changed files.
                for record in project['sources']:
                    if record['role'] in ('phase_report', 'wppf_report', 'quantity_image'):
                        source_loader(storage_id, record['id'])
                saved = store.save(storage_id, project, value['review'], value['expected_revision'])
                return payload(project, saved)
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        except OSError as exc:
            raise HTTPException(503, '검토 파일 저장 권한·여유 공간을 확인하세요.') from exc

    @router.get('/api/analysis/{storage_id}/pages/{source_id}/{page_number}')
    def original_page(storage_id: str, source_id: str, page_number: int):
        project, _ = context(storage_id)
        if source_id not in report_sources(project):
            raise HTTPException(404, '등록된 PDF 출처가 아닙니다.')
        try:
            data, width, height = render_page(source_loader(storage_id, source_id), page_number)
            return Response(
                data,
                media_type='image/png',
                headers={
                    'X-PDF-Width': str(width),
                    'X-PDF-Height': str(height),
                    'Cache-Control': 'private, max-age=300',
                },
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        except Exception as exc:
            raise HTTPException(
                422, '원본 PDF 페이지를 표시하지 못했습니다. 원본 파일을 확인하세요.'
            ) from exc

    return router
