"""Validate delivered PPT click targets, not merely unused relationship records."""
import hashlib
import io
import posixpath
import unicodedata
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as E
from zipfile import ZipFile

SOURCE_ROLES = {'phase_report', 'wppf_report', 'measurement'}
DRAWING = 'http://schemas.openxmlformats.org/drawingml/2006/main'
RELATIONSHIP = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def report_sources(project, document):
    """Only included samples; refuse ambiguous IDs instead of silently dropping a file."""
    included = {entry['sample_id'] for entry in document['samples'] if entry['included']}
    records = {}
    for sample in project['samples']:
        if sample['id'] not in included:
            continue
        for record in sample['sources']:
            if record['role'] not in SOURCE_ROLES:
                continue
            previous = records.get(record['id'])
            if previous and any(previous[k] != record[k] for k in ('filename', 'sha256', 'role')):
                raise ValueError(f"보고서 원본 파일 식별자가 중복되었습니다: {record['filename']}")
            records[record['id']] = record
    return list(records.values())


def read_linked_source(record, resolve_source):
    try:
        content = resolve_source(record['id'])
    except (OSError, KeyError, ValueError) as exc:
        raise ValueError(f"보고서 링크의 원본 파일을 읽을 수 없습니다: {record['filename']}. "
                         '원본 파일을 복구하거나 다시 업로드하세요.') from exc
    if not content:
        raise ValueError(f"보고서 링크의 원본 파일이 비어 있습니다: {record['filename']}")
    if hashlib.sha256(content).hexdigest() != record['sha256']:
        raise ValueError(f"보고서 링크의 원본 파일 내용이 등록 후 변경되었습니다: {record['filename']}")
    return content


def ppt_click_targets_by_slide(content):
    """Read the actual serialized slide click actions and resolve every relationship."""
    pages = {}
    with ZipFile(io.BytesIO(content)) as archive:
        parts = [name for name in archive.namelist()
                 if name.startswith('ppt/slides/slide') and name.endswith('.xml')]
        for name in parts:
            targets = pages.setdefault(name, [])
            slide = E.fromstring(archive.read(name))
            clicks = slide.findall('.//{' + DRAWING + '}hlinkClick')
            if not clicks:
                continue
            rel_path = 'ppt/slides/_rels/' + posixpath.basename(name) + '.rels'
            if rel_path not in archive.namelist():
                raise ValueError('PPT 링크 연결 정보가 누락되었습니다. 보고서를 다시 생성하세요.')
            rels = {r.get('Id'): r for r in E.fromstring(archive.read(rel_path))}
            # Office OLE activation uses an empty r:id on the click action;
            # the actual internal file relationship belongs to p:oleObj.
            ole_actions = set()
            p = '{http://schemas.openxmlformats.org/presentationml/2006/main}'
            for frame in slide.iter(p + 'graphicFrame'):
                obj = frame.find('.//' + p + 'oleObj')
                if obj is None:
                    continue
                link = rels.get(obj.get('{' + RELATIONSHIP + '}id'))
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), link.get('Target', ''))) if link is not None else ''
                if (link is None or link.get('Type') != RELATIONSHIP + '/oleObject'
                        or link.get('TargetMode') == 'External' or target not in archive.namelist()
                        or not archive.getinfo(target).file_size):
                    raise ValueError('PPT 내부 첨부 파일이 누락되었습니다.')
                ole_actions.update(frame.findall('.//{' + DRAWING + '}hlinkClick'))
            for click in clicks:
                if (click in ole_actions and not click.get('{' + RELATIONSHIP + '}id')
                        and click.get('action') == 'ppaction://ole?verb=0'):
                    continue
                rel = rels.get(click.get('{' + RELATIONSHIP + '}id'))
                if (rel is not None and rel.get('Type') == RELATIONSHIP + '/slide'
                        and rel.get('TargetMode') != 'External'
                        and click.get('action') == 'ppaction://hlinksldjump'):
                    target = posixpath.normpath(posixpath.join(posixpath.dirname(name), rel.get('Target', '')))
                    if target not in parts:
                        raise ValueError('PPT 내부 자료 페이지가 누락되었습니다.')
                    continue
                if (rel is None or rel.get('Type') != RELATIONSHIP + '/hyperlink'
                        or rel.get('TargetMode') != 'External' or not rel.get('Target')):
                    raise ValueError('PPT 클릭 링크의 대상이 누락되었거나 올바르지 않습니다. 보고서를 다시 생성하세요.')
                targets.append(rel.get('Target'))
    return pages


def ppt_click_targets(content):
    return [target for targets in ppt_click_targets_by_slide(content).values() for target in targets]


def check_ppt_links(content, expected, expected_pages=None):
    """Every required target must have a clickable element; stale template links are refused."""
    pages = ppt_click_targets_by_slide(content)
    targets = [target for values in pages.values() for target in values]
    actual = set(targets)
    missing = set(expected) - actual
    if missing:
        labels = ', '.join(sorted({expected[target] for target in missing})[:5])
        raise ValueError(f'PPT에 필수 링크 {len(missing)}개가 누락되었습니다: {labels}. 보고서를 다시 생성하세요.')
    if actual - set(expected):
        raise ValueError('PPT에 현재 보고서의 시료·원본과 일치하지 않는 링크가 있습니다. 보고서를 다시 생성하세요.')
    if expected_pages is not None:
        for name, required in expected_pages.items():
            if set(pages.get(name, [])) != set(required):
                page_number = posixpath.basename(name)[5:-4]
                raise ValueError(f'PPT {page_number}페이지의 시료·원본 링크가 누락되었거나 다른 페이지와 뒤바뀌었습니다.')
    return targets


def check_package_links(targets, archive):
    """Resolve each PPT link to a real nonempty ZIP member, including Windows path collisions."""
    files = {}
    portable = set()
    for info in archive.infolist():
        if info.is_dir():
            continue
        key = unicodedata.normalize('NFC', info.filename).casefold()
        if key in portable:
            raise ValueError(f'보고서 패키지에 파일명이 중복되었습니다: {info.filename}')
        portable.add(key)
        files[info.filename] = info
    for target in set(targets):
        url = urlsplit(target)
        path = unquote(url.path)
        if (url.scheme or url.netloc or url.query or path.startswith('/') or '\\' in path
                or posixpath.normpath(path) != path or path.startswith('../')):
            raise ValueError('PPT 링크가 보고서 패키지 밖을 가리킵니다. 보고서를 다시 생성하세요.')
        if path not in files or not files[path].file_size:
            raise ValueError(f'PPT 링크 대상 파일이 보고서 패키지에 없거나 비어 있습니다: {path}')
