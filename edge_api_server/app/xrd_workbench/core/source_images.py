"""Explicit report image selection; never guess which sample owns an orphan."""
import hashlib
import io
from pathlib import PureWindowsPath

from PIL import Image


def phase_image_choices(project, sample):
    """This sample's artwork plus preserved images not owned by another sample."""
    others = {r['id'] for s in project['samples'] if s['id'] != sample['id']
              for r in s.get('sources', [])}
    records = {r['id']: r for r in sample.get('sources', []) if r['role'] == 'phase_image'}
    for record in project.get('sources', []):
        if (record['id'] not in others and record['role'] in {'phase_image', 'ignore'}
                and PureWindowsPath(record['filename']).suffix.lower() in {'.png', '.jpg', '.jpeg'}):
            records.setdefault(record['id'], record)
    return list(records.values())


def phase_image_record(project, sample, entry):
    """Resolve the explicit report choice or the immutable upload association."""
    selected = entry.get('phase_image_source_id', '')
    if selected:
        record = next((r for r in phase_image_choices(project, sample) if r['id'] == selected), None)
        if record is None:
            raise ValueError(f"{sample['name']}: 선택한 상매칭 이미지가 없거나 다른 시료에 연결되어 있습니다.")
        return record
    records = [r for r in sample.get('sources', []) if r['role'] == 'phase_image']
    if len(records) > 1:
        raise ValueError(f"{sample['name']}: 상매칭 이미지가 중복되어 있습니다. 사용할 원본을 선택하세요.")
    return records[0] if records else None


def read_image(record, resolve_source):
    """Missing, changed or invalid registered evidence must not trigger RAW fallback."""
    try:
        data = resolve_source(record['id'])
    except (OSError, KeyError, ValueError) as exc:
        raise ValueError(f"{record['filename']}: 원본 이미지를 읽지 못했습니다. 파일 보존 상태를 확인하세요.") from exc
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError(f"{record['filename']}: 등록 후 원본 이미지가 변경되었습니다.")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.format not in ('PNG', 'JPEG'):
                raise ValueError('PNG/JPG 원본 이미지만 지원합니다.')
            size, fmt = image.size, image.format
            image.verify()
    except Exception as exc:
        raise ValueError(f"{record['filename']}: 원본 이미지가 손상되었거나 PNG/JPG 형식이 아닙니다.") from exc
    return data, size, fmt
