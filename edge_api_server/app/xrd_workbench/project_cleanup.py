"""Owner-scoped, snapshot-confirmed permanent cleanup of archived working projects.

Completed export artifacts and report-transfer storage/DB are deliberately outside scope.
Only synthetic projects should be used when testing this module.
"""
import json
import os
import shutil
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from .storage import _is_upload_id, _write_json_atomic


class ProjectCleanup:
    def __init__(self, state):
        self.state = state
        self.home = state.home

    def path(self, *parts):
        """All parts are server-generated directory names or validated opaque IDs."""
        current = self.home
        if current.is_symlink():
            raise ValueError('회원 저장소 경로를 확인하세요.')
        for part in parts:
            if not part or part in ('.', '..') or '/' in part or '\\' in part:
                raise ValueError('잘못된 정리 대상 경로입니다.')
            current = current / part
            if current.is_symlink():
                raise ValueError('연결된 경로는 영구 삭제할 수 없습니다.')
        if not current.resolve().is_relative_to(self.home.resolve()):
            raise ValueError('회원 저장소 밖의 경로는 정리할 수 없습니다.')
        return current

    def plan_path(self, plan_id):
        if not isinstance(plan_id, str) or not _is_upload_id(plan_id):
            raise ValueError('잘못된 삭제 확인 번호입니다.')
        return self.path('purge-plans', plan_id + '.json')

    def jobs(self, exclude=None):
        found = {}
        root = self.path('jobs')
        for directory in root.iterdir() if root.exists() else []:
            if not _is_upload_id(directory.name):
                continue
            path = self.path('jobs', directory.name, 'job.json')
            if not path.is_file():
                continue
            value = json.loads(path.read_text())
            if directory.name != exclude and value.get('status') in ('queued', 'running'):
                try:
                    os.kill(value['pid'], 0)
                except ProcessLookupError:
                    pass
                else:
                    raise ValueError('진행 중인 XRD 업로드·보고서 작업이 있습니다. 완료 후 휴지통을 비우세요.')
            found[directory.name] = value
        return found

    def validate(self, plan):
        for item in plan['items']:
            sid = item['storage_id']
            if not _is_upload_id(sid):
                raise ValueError('잘못된 프로젝트 식별 정보입니다.')
            source = self.path('uploads', sid, 'project.json')
            metadata = self.path('uploads', sid, 'management.json')
            if not source.is_file() or not metadata.is_file():
                raise ValueError('삭제 대상이 변경되었습니다. 목록에서 다시 확인하세요.')
            value = json.loads(metadata.read_text())
            if not value.get('archived_at') or value.get('revision') != item['expected_revision']:
                raise ValueError('확인 후 복원/변경된 프로젝트가 있습니다. 새 삭제 계획을 확인하세요.')

    def targets(self, plan, jobs):
        paths, ids = [], {p['storage_id'] for p in plan['items']}
        for sid in sorted(ids):
            paths += [self.path(folder, sid) for folder in ('uploads', 'analysis', 'reports')]
            paths.append(self.path('drafts', sid + '.json'))
        # Completed import sessions contain a duplicate of the same raw upload.
        sessions = self.path('sessions')
        for directory in sessions.iterdir() if sessions.exists() else []:
            if not _is_upload_id(directory.name):
                continue
            path = self.path('sessions', directory.name, 'session.json')
            if not path.is_file():
                continue
            session = json.loads(path.read_text())
            job = jobs.get(session.get('job_id'), {})
            if job.get('kind') == 'import' and job.get('status') == 'completed' and job.get('storage_id') in ids:
                paths.append(self.path('sessions', directory.name))
        return [p for p in paths if p.exists()]

    def size(self, paths):
        size = 0
        for path in paths:
            entries = [path] if path.is_file() else path.rglob('*')
            for item in entries:
                if item.is_symlink():
                    raise ValueError('프로젝트 안의 연결 파일은 정리할 수 없습니다. 관리자에게 확인하세요.')
                if item.is_file():
                    size += item.stat().st_size
        return size

    def prepare(self, items=None):
        with self.state.storage.lock:
            jobs = self.jobs()
            if items is None:
                items = [dict(storage_id=p['storage_id'], expected_revision=p['managed_revision'])
                         for p in self.state.storage.files.list(archived=True)]
            if not isinstance(items, list) or not 1 <= len(items) <= 10000:
                raise ValueError('삭제할 휴지통 프로젝트를 1~10,000개 확인하세요.')
            if any(not isinstance(p, dict) or not isinstance(p.get('storage_id'), str)
                   or type(p.get('expected_revision')) is not int for p in items):
                raise ValueError('삭제 대상 식별 정보와 버전이 필요합니다.')
            if len({p['storage_id'] for p in items}) != len(items):
                raise ValueError('삭제 대상이 중복되었습니다.')
            plan = dict(plan_id=uuid.uuid4().hex, created_at=time.time(), phase='planned',
                        items=[dict(storage_id=p['storage_id'], expected_revision=p['expected_revision']) for p in items])
            self.validate(plan)
            plan['bytes'] = self.size(self.targets(plan, jobs))
            _write_json_atomic(self.plan_path(plan['plan_id']), plan)
            return dict(plan_id=plan['plan_id'], count=len(items), total_bytes=plan['bytes'], expires_in=900)

    def execute(self, plan_id, job_id):
        with self.state.storage.lock:
            plan_file = self.plan_path(plan_id)
            plan = json.loads(plan_file.read_text())
            staging = self.path('purges', plan_id, 'payload')
            if plan['phase'] == 'planned':
                jobs = self.jobs(exclude=job_id)
                self.validate(plan)
                targets = self.targets(plan, jobs)
                size = self.size(targets)
                staging.mkdir(parents=True, exist_ok=False)
                moves = [(source, staging / str(i)) for i, source in enumerate(targets)]
                plan.update(phase='staging', paths=[str(p.relative_to(self.home)) for p in targets], bytes=size)
                try:
                    _write_json_atomic(plan_file, plan)
                    for source, destination in moves:
                        source.rename(destination)
                    plan['phase'] = 'committed'
                    _write_json_atomic(plan_file, plan)
                except Exception:
                    # No bytes deleted yet: roll back any completed moves on ordinary errors.
                    for source, destination in reversed(moves):
                        if destination.exists():
                            destination.rename(source)
                    staging.rmdir()
                    plan['phase'] = 'planned'
                    _write_json_atomic(plan_file, plan)
                    raise
            if plan['phase'] == 'staging':
                raise ValueError('이전 정리가 중단되었습니다. 관리자에게 삭제 확인 번호를 전달하세요. 임시 보관 원본은 남아 있습니다.')
            if plan['phase'] == 'committed':
                # This exact plan's staged working files only; never jobs/export ZIPs or the owner root.
                if staging.exists():
                    shutil.rmtree(staging)
                plan['phase'] = 'completed'
                _write_json_atomic(plan_file, plan)
            return dict(deleted_count=len(plan['items']), reclaimed_bytes=plan['bytes'], plan_id=plan_id)


def build_cleanup_router(get_state, start_job, read_job):
    router = APIRouter()

    @router.post('/api/project-purges/plan')
    def plan(payload: dict):
        try:
            if payload.get('all_trash') is True:
                if payload.get('items') is not None:
                    raise ValueError('전체 또는 선택 삭제 중 하나만 지정하세요.')
                items = None
            else:
                items = payload.get('items', [])
                if not isinstance(items, list) or len(items) > 50:
                    raise ValueError('선택 삭제는 한 페이지의 최대 50개입니다.')
            return JSONResponse(ProjectCleanup(get_state()).prepare(items), headers={'Cache-Control': 'private, no-store'})
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '삭제 대상과 작업 상태를 확인하지 못했습니다. 관리자에게 확인하세요.') from exc

    @router.post('/api/project-purges', status_code=202)
    def purge(payload: dict):
        if payload.get('confirmation') != '영구 삭제' or type(payload.get('expected_count')) is not int:
            raise HTTPException(422, '삭제 건수를 확인하고 “영구 삭제”를 입력하세요.')
        state = get_state()
        cleanup = ProjectCleanup(state)
        try:
            with state.storage.lock:
                path = cleanup.plan_path(payload.get('plan_id'))
                value = json.loads(path.read_text())
                if payload['expected_count'] != len(value['items']):
                    raise ValueError('확인한 삭제 건수와 다릅니다.')
                if value.get('job_id'):
                    old = read_job(state, value['job_id'])
                    if old['status'] != 'failed':
                        return old
                if value['phase'] == 'planned':
                    if time.time() - value['created_at'] > 900:
                        raise ValueError('삭제 확인이 만료되었습니다. 대상을 다시 확인하세요.')
                    cleanup.jobs()
                    cleanup.validate(value)
                elif value['phase'] != 'committed':
                    raise ValueError('관리자가 중단된 정리 상태를 확인해야 합니다.')
                job = start_job(state, 'project-purge', lambda directory: cleanup.execute(value['plan_id'], directory.name))
                value['job_id'] = job['job_id']
                _write_json_atomic(path, value)
                return job
        except (ValueError, OSError, KeyError, TypeError) as exc:
            raise HTTPException(409, str(exc) if isinstance(exc, ValueError) else '본인의 삭제 계획을 확인하지 못했습니다.') from exc
    return router
