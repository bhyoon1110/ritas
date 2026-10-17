import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, Response

from ..core.checklist import build_checklist
from ..core.errors import RevisionConflict
from ..core.figure import similarity_summary
from ..core.phase_class import check_percent, evaluate_trace, phase_ref
from ..core.quantity_table import build_quantity_table
from ..core.report import (
    default_figure,
    default_phase_classes,
    default_quantity_table,
    merge_notes,
    new_document_with_defaults,
    project_phase_names,
    sync_analysis,
)
from ..rist_llm import select_notes


def build_report_router(project_loader, source_loader, store):
    router = APIRouter()

    def context(storage_id):
        try:
            project = project_loader(storage_id)
            document = store.load(storage_id) or new_document_with_defaults(project)
            if document['project_id'] != project['id']:
                raise RevisionConflict('원자료와 저장 문서가 다릅니다.')
            return project, document
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, FileNotFoundError, KeyError) as exc:
            raise HTTPException(404, str(exc)) from exc

    def saved_context(storage_id, revision, require_review=True):
        project, document = context(storage_id)
        if not document['revision'] or revision != document['revision']:
            raise HTTPException(409, '현재 편집 내용을 저장한 뒤 같은 버전으로 내보내세요.')
        if document.get('analysis_revision', 0) != project.get('analysis_revision', 0):
            raise HTTPException(
                409, '분석 검토값이 변경되었습니다. 보고서를 다시 열어 문장을 검토·저장하세요.'
            )
        if require_review and any(
            s.get('analysis_review_required') for s in document['samples'] if s['included']
        ):
            raise HTTPException(
                409, '분석값이 바뀐 시료의 자료·문장 검토 완료를 확인하고 저장하세요.'
            )
        return project, document

    @router.get('/api/reports/{storage_id}')
    def get_document(storage_id: str):
        with store.lock:
            project, document = context(storage_id)
            document, updated = sync_analysis(project, document)
            # Reports saved before figures existed open with the figure off (pages unchanged).
            document.setdefault('figure', default_figure(False))
            document.setdefault('phase_classes', default_phase_classes())
            document.setdefault('quantity_table', default_quantity_table(False))
            return dict(project=project, document=document, analysis_update_required=updated)

    @router.put('/api/reports/{storage_id}')
    async def save_document(storage_id: str, request: Request):
        payload = await request.json()
        try:
            with store.lock:
                project, _ = context(storage_id)
                return store.save(
                    storage_id, project, payload['document'], payload['expected_revision']
                )
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post('/api/reports/{storage_id}/notes/{sample_id}')
    async def generate(storage_id: str, sample_id: str, request: Request):
        import asyncio

        payload = await request.json()
        project, document = saved_context(storage_id, payload.get('expected_revision'), False)
        sample = next((s for s in project['samples'] if s['id'] == sample_id), None)
        if sample is None:
            raise HTTPException(404, '등록 시료를 찾지 못했습니다.')
        result = await asyncio.to_thread(
            select_notes, sample, document['llm'], None, project_phase_names(project)
        )
        entry = next(s for s in document['samples'] if s['sample_id'] == sample_id)
        merge_notes(entry, result)
        try:
            with store.lock:
                latest_project, _ = context(storage_id)
                if latest_project.get('analysis_revision', 0) != project.get(
                    'analysis_revision', 0
                ):
                    raise RevisionConflict(
                        '생성 중 분석 검토값이 변경되었습니다. 다시 열어 확인하세요.'
                    )
                return store.save(storage_id, project, document, document['revision'])
        except RevisionConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get('/api/reports/{storage_id}/figure/similarity')
    def figure_similarity(storage_id: str, revision: int):
        project, document = saved_context(storage_id, revision)
        return similarity_summary(project, document)

    @router.get('/api/reports/{storage_id}/phase-classes')
    def phase_classes(storage_id: str, revision: int, percent: str | None = None):
        project, document = saved_context(storage_id, revision)
        limit = None
        if percent is not None:
            try:
                limit = float(percent)
                check_percent(limit)
            except ValueError as exc:
                raise HTTPException(
                    422, '미량상 기준 비율은 0 이상 100 이하의 숫자입니다.'
                ) from exc
        samples = {s['id']: s for s in project['samples']}
        result = []
        for entry in document['samples']:
            if not entry['included']:
                continue
            sample = samples[entry['sample_id']]
            verdicts = evaluate_trace(sample, limit) if limit is not None else {}
            phases = []
            for phase in sample['phases']:
                ref = phase_ref(phase)
                quantity = phase.get('quantity')
                phases.append(
                    dict(
                        phase=ref,
                        quantity=f"{quantity['raw']} {quantity['unit']}" if quantity else None,
                        trace=verdicts.get((ref['name'], ref['card'], ref['db'])),
                    )
                )
            result.append(dict(sample_id=sample['id'], phases=phases))
        return dict(samples=result)

    @router.get('/api/reports/{storage_id}/quantity-table')
    def quantity_table(storage_id: str, revision: int):
        project, document = saved_context(storage_id, revision)
        return build_quantity_table(project, document)

    @router.get('/api/reports/{storage_id}/checklist')
    def checklist(storage_id: str, revision: int):
        # A report that still needs review can be checked: that is what the checklist is for.
        project, document = saved_context(storage_id, revision, require_review=False)
        return build_checklist(project, document)

    @router.get('/api/reports/{storage_id}/preview')
    def preview(storage_id: str, revision: int):
        from ..render.html import render_html
        from ..render.layout import build_pages

        project, document = saved_context(storage_id, revision)
        try:
            return HTMLResponse(
                render_html(
                    build_pages(project, document, lambda fid: source_loader(storage_id, fid)),
                    document,
                    'app',
                    storage_id,
                )
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get('/api/reports/{storage_id}/pdf')
    def pdf(storage_id: str, revision: int, request: Request):
        from ..render.layout import build_pages
        from ..render.pdf import render_pdf

        project, document = saved_context(storage_id, revision)
        try:
            data = render_pdf(
                build_pages(project, document, lambda fid: source_loader(storage_id, fid)),
                document,
                link_base=str(request.base_url),
                storage_id=storage_id,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(
            data,
            media_type='application/pdf',
            headers={'Content-Disposition': 'attachment; filename="XRD-Report.pdf"'},
        )

    @router.get('/api/reports/{storage_id}/pptx')
    def pptx(storage_id: str, revision: int, request: Request):
        from ..render.layout import build_pages
        from ..render.pptx import render_pptx

        project, document = saved_context(storage_id, revision)
        try:
            data = render_pptx(
                build_pages(project, document, lambda fid: source_loader(storage_id, fid)),
                document,
                link_base=str(request.base_url),
                storage_id=storage_id,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(
            data,
            media_type='application/vnd.openxmlformats-officedocument.presentationml.presentation',
            headers={'Content-Disposition': 'attachment; filename="XRD-Report.pptx"'},
        )

    @router.get('/api/reports/{storage_id}/zip')
    def package(storage_id: str, revision: int):
        from ..render.package import build_package

        project, document = saved_context(storage_id, revision)
        try:
            data = build_package(project, document, lambda fid: source_loader(storage_id, fid))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(
            data,
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="XRD-Report-Package.zip"'},
        )

    @router.post('/api/reports/{storage_id}/zip')
    async def prepared_package(storage_id: str, revision: int, request: Request):
        from ..render.package import build_package

        raw = await request.body()
        if len(raw) > 512 * 1024:
            raise HTTPException(422, '뷰어 보기 설정은 512KB 이하로 보내세요.')
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError('뷰어 보기 설정 형식을 확인하세요.')
            project, document = saved_context(storage_id, revision)
            expected = payload.get('analysis_revision')
            if type(expected) is not int or expected != project.get('analysis_revision', 0):
                raise HTTPException(
                    409, '분석 검토값이 변경되었습니다. 보고서와 뷰어를 갱신한 뒤 저장하세요.'
                )
            # Rendering is blocking native work, like the synchronous GET route.
            import asyncio

            data = await asyncio.to_thread(
                build_package,
                project,
                document,
                lambda fid: source_loader(storage_id, fid),
                payload.get('viewer_state'),
                payload.get('views'),
            )
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return Response(
            data,
            media_type='application/zip',
            headers={'Content-Disposition': 'attachment; filename="XRD-Report-Package.zip"'},
        )

    return router
