"""Editable report state, independent of immutable measurements and PDF facts."""

import hashlib
from copy import deepcopy
from datetime import date

from ..core.errors import RevisionConflict
from ..core.figure import phase_key
from ..core.notes import build_candidates, validate_settings, verify_note_text
from ..core.phase_class import DEFAULT_TRACE_PERCENT, check_percent, evaluate_trace, phase_ref
from ..core.review import BASE_FINGERPRINT


def new_document_with_defaults(project: dict) -> dict:
    """Start unclassified; only the analyst may apply a phase classification rule."""
    # Importing a report is not permission to make a scientific classification.
    # The optional trace rule must be explicitly applied by the analyst.
    return new_document(project)


def new_document(project: dict) -> dict:
    """Default editable report for a project: every sample included, rule-based note
    candidates selected, the first four samples on the summary page."""
    entries = []
    for i, sample in enumerate(project['samples']):
        roles = {r['role'] for r in sample['sources']}
        notes = [
            dict(**candidate, selected=True, edited=False, selection_edited=False, origin='rules')
            for candidate in build_candidates(sample)
        ]
        entries.append(
            dict(
                sample_id=sample['id'],
                display_name=sample['name'],
                included=True,
                summary_included=i < 4,
                include_phase_image='phase_image' in roles,
                include_quantity_image='quantity_image' in roles,
                phase_caption='원본 상매칭 결과',
                quantity_caption='원본 정량 결과',
                notes=notes,
                retained_notes=[],
                note_order_edited=False,
                manual_note='',
                reviewed=False,
                analysis_fingerprint=sample.get('analysis_fingerprint', BASE_FINGERPRINT),
                analysis_review_required=False,
                generation={
                    'status': 'rules',
                    'model_used': None,
                    'message': '모델 연결 전의 근거 기반 후보입니다.',
                },
            )
        )
    return dict(
        schema_version=1,
        project_id=project['id'],
        revision=0,
        analysis_revision=project.get('analysis_revision', 0),
        metadata={
            'title': 'XRD 분석 결과',
            'client': '',
            'requester': '',
            'received_date': '',
            'analysis_period': '',
            'report_number': '',
            'experiment_code': 'XRD',
            'equipment_code': '',
            'operator_id': '',
            'author': '',
            'date': date.today().isoformat(),
        },
        summary='',
        samples=entries,
        llm={'provider': 'none', 'base_url': 'http://127.0.0.1:11434', 'model': ''},
        figure=default_figure(True),
        quantity_table=default_quantity_table(True),
        phase_classes=default_phase_classes(),
    )


def string(value, maxlen, label):
    """Return `value` if it is a str of at most `maxlen` characters, else raise ValueError."""
    if not isinstance(value, str) or len(value) > maxlen:
        raise ValueError(f'{label}: 문자열 길이/형식을 확인하세요 (최대 {maxlen}자).')
    return value


FIGURE_ORDER_MODES = ('report', 'similarity')


def default_quantity_table(include: bool) -> dict:
    """Quantity summary table settings; new reports include it, old saves open with it off."""
    return dict(include=include)


def validate_quantity_table(raw) -> dict:
    """Clean quantity table settings: only a boolean `include`."""
    if not isinstance(raw, dict) or type(raw.get('include')) is not bool:
        raise ValueError('정량 요약 표 설정(포함 여부)을 확인하세요.')
    return dict(include=raw['include'])


def default_figure(include: bool) -> dict:
    """Comparison-figure settings; `include` is True for new reports, False for old saves."""
    return dict(
        include=include,
        order_mode='report',
        spacing=None,
        x_range=None,
        normalize=False,
        show_markers=True,
        show_reference=True,
        caption='',
        groups=[],
    )


def _number(value, label):
    if type(value) not in (int, float) or value != value or abs(value) == float('inf'):
        raise ValueError(f'{label}: 숫자를 입력하세요.')
    return value


def validate_figure(raw, entries: list[dict]) -> dict:
    """Clean figure settings. Groups keep only samples that are in the report (others are
    dropped silently), reject unknown or repeated sample IDs, and empty groups disappear."""
    if not isinstance(raw, dict):
        raise ValueError('그래프 설정 형식을 확인하세요.')
    clean = default_figure(False)
    for key in ('include', 'normalize', 'show_markers', 'show_reference'):
        value = raw.get(key, clean[key])
        if type(value) is not bool:
            raise ValueError(f'그래프 {key}: boolean 상태가 필요합니다.')
        clean[key] = value
    # Normalization was removed from the app; an older report that had it on opens raw.
    clean['normalize'] = False
    mode = raw.get('order_mode', 'report')
    if mode not in FIGURE_ORDER_MODES:
        raise ValueError('그래프 정렬 방식을 확인하세요.')
    clean['order_mode'] = mode
    if raw.get('spacing') is not None:
        clean['spacing'] = _number(raw['spacing'], 'Offset 간격')
        if clean['spacing'] < 0:
            raise ValueError('Offset 간격은 0 이상이어야 합니다.')
    if raw.get('x_range') is not None:
        r = raw['x_range']
        if not isinstance(r, list) or len(r) != 2:
            raise ValueError('가로축 범위는 최소·최대 두 값입니다.')
        lo, hi = _number(r[0], '가로축 최소'), _number(r[1], '가로축 최대')
        if lo >= hi:
            raise ValueError('가로축 최소는 최대보다 작아야 합니다.')
        clean['x_range'] = [lo, hi]
    clean['caption'] = string(raw.get('caption', ''), 300, '그래프 캡션')
    groups = raw.get('groups', [])
    if not isinstance(groups, list) or len(groups) > 100:
        raise ValueError('그래프 그룹 형식을 확인하세요.')
    known = {e['sample_id'] for e in entries}
    included = {e['sample_id'] for e in entries if e['included']}
    seen: set = set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('samples'), list):
            raise ValueError('그래프 그룹 형식을 확인하세요.')
        title = string(group.get('title', ''), 120, '그룹 이름').strip()
        if not title:
            raise ValueError('그룹 이름을 입력하세요.')
        members = group['samples']
        if len(set(members)) != len(members) or not set(members) <= known:
            raise ValueError('그룹에 중복되었거나 알 수 없는 시료가 있습니다.')
        if seen & set(members):
            raise ValueError('한 시료는 하나의 그룹에만 속할 수 있습니다.')
        seen |= set(members)
        kept = [i for i in members if i in included]
        if kept:
            clean['groups'].append({'title': title, 'samples': kept})
    return clean


PHASE_CLASSES = ('major', 'trace', 'presumed')
LABEL_ORIGINS = ('manual', 'rule')


def default_phase_classes() -> dict:
    """The author's phase classification: nothing classified, no criteria, no rule applied."""
    return dict(criteria_text='', rule=None, labels=[], retained=[])


def _class_label(raw, known_ids) -> dict:
    if not isinstance(raw, dict) or raw.get('sample_id') not in known_ids:
        raise ValueError('상 분류의 시료를 확인하세요.')
    phase = raw.get('phase')
    if not isinstance(phase, dict) or set(phase) != {'name', 'card', 'db'}:
        raise ValueError('상 분류의 상 정보(이름, Card 번호, 데이터베이스)를 확인하세요.')
    ref = {key: string(phase[key], 300, '상 정보') for key in ('name', 'card', 'db')}
    if not ref['name'].strip():
        raise ValueError('상 분류의 상 이름이 비어 있습니다.')
    if raw.get('class') not in PHASE_CLASSES or raw.get('origin') not in LABEL_ORIGINS:
        raise ValueError('상 분류 값(주요상, 미량상, 추정상)과 출처를 확인하세요.')
    label = {
        'sample_id': raw['sample_id'],
        'phase': ref,
        'class': raw['class'],
        'origin': raw['origin'],
    }
    if raw['origin'] == 'rule':
        # The percent the rule was applied with; each label states its own criterion.
        label['percent'] = check_percent(raw.get('percent'))
    return label


def validate_phase_classes(raw, project: dict) -> dict:
    """Clean phase classification. A class whose phase is no longer in the sample's analysis is
    moved to `retained` instead of being rejected or dropped."""
    if not isinstance(raw, dict):
        raise ValueError('상 분류 설정 형식을 확인하세요.')
    clean = default_phase_classes()
    clean['criteria_text'] = string(raw.get('criteria_text', ''), 1500, '분류 기준 문구')
    rule = raw.get('rule')
    if rule is not None:
        if not isinstance(rule, dict):
            raise ValueError('분류 규칙 형식을 확인하세요.')
        clean['rule'] = {'trace_max_percent': check_percent(rule.get('trace_max_percent'))}
    labels, retained = raw.get('labels', []), raw.get('retained', [])
    if not isinstance(labels, list) or not isinstance(retained, list):
        raise ValueError('상 분류 목록 형식을 확인하세요.')
    if len(labels) > 20000 or len(retained) > 20000:
        raise ValueError('상 분류 항목이 너무 많습니다.')
    current = {s['id']: {phase_key(p) for p in s['phases']} for s in project['samples']}
    seen = set()
    for item in labels:
        label = _class_label(item, current)
        key = (label['sample_id'], tuple(label['phase'].values()))
        if key in seen:
            raise ValueError('같은 시료의 같은 상에 분류가 둘 이상입니다.')
        seen.add(key)
        if tuple(label['phase'].values()) in current[label['sample_id']]:
            clean['labels'].append(label)
        else:
            clean['retained'].append(label)
    kept = [_class_label(item, current) for item in retained]
    clean['retained'] = [x for x in kept if x not in clean['retained']] + clean['retained']
    return clean


def apply_trace_verdicts(document: dict, evaluations: list, percent) -> dict:
    """A copy of the document with trace verdicts applied. `evaluations` is a list of
    {sample_id, phases: [{phase: {name, card, db}, status}]} for the samples that were
    evaluated. Phases with status `trace` get the trace class (origin rule); direct choices are
    never overwritten; a class an earlier run of the rule set is removed when the phase no
    longer qualifies; samples that were not evaluated keep what they have."""
    limit = check_percent(percent)
    result = deepcopy(document)
    classes = result.setdefault('phase_classes', default_phase_classes())
    for evaluation in evaluations:
        sample_id = evaluation['sample_id']
        for item in evaluation['phases']:
            ref = item['phase']
            existing = next(
                (x for x in classes['labels'] if x['sample_id'] == sample_id and x['phase'] == ref),
                None,
            )
            if existing and existing['origin'] == 'manual':
                continue
            if item['status'] == 'trace':
                if existing:
                    existing.update({'class': 'trace', 'origin': 'rule', 'percent': limit})
                else:
                    classes['labels'].append(
                        {
                            'sample_id': sample_id,
                            'phase': ref,
                            'class': 'trace',
                            'origin': 'rule',
                            'percent': limit,
                        }
                    )
            elif existing:
                classes['labels'].remove(existing)
    classes['rule'] = {'trace_max_percent': limit}
    return result


def apply_trace_rule(project: dict, document: dict, percent) -> dict:
    """Evaluate the trace rule for every sample included in the report and apply the verdicts
    (see apply_trace_verdicts). No class is ever set for a phase the rule cannot decide."""
    limit = check_percent(percent)
    samples = {s['id']: s for s in project['samples']}
    evaluations = []
    for entry in document['samples']:
        if not entry['included']:
            continue
        sample = samples[entry['sample_id']]
        verdicts = evaluate_trace(sample, limit)
        evaluations.append(
            dict(
                sample_id=sample['id'],
                phases=[
                    dict(phase=phase_ref(p), status=verdicts[phase_key(p)]['status'])
                    for p in sample['phases']
                ],
            )
        )
    return apply_trace_verdicts(document, evaluations, limit)


def project_phase_names(project: dict) -> list[str]:
    """Every phase name of the project: a model sentence may not mention one its evidence lacks."""
    names = {p['name'] for s in project['samples'] for p in s.get('phases', [])}
    names |= {q['phase'] for s in project['samples'] for q in s.get('quantities', [])}
    return sorted(names)


def validate_document(project: dict, document: dict) -> dict:
    """Rebuild a clean document from the project's canonical candidates and the user's
    edits; foreign samples, unknown candidates and out-of-range fields are rejected."""
    if (
        not isinstance(document, dict)
        or document.get('project_id') != project['id']
        or document.get('schema_version') != 1
    ):
        raise ValueError('보고서와 원본 프로젝트 버전이 맞지 않습니다.')
    result = new_document(project)
    revision = document.get('revision', 0)
    if type(revision) is not int or revision < 0:
        raise ValueError('잘못된 문서 revision입니다.')
    result['revision'] = revision
    if document.get('analysis_revision', 0) != project.get('analysis_revision', 0):
        raise ValueError('분석 검토값이 변경되었습니다. 보고서를 다시 열어 확인하세요.')
    metadata = document.get('metadata', {})
    for key, limit in [
        ('title', 120),
        ('client', 120),
        ('requester', 80),
        ('received_date', 10),
        ('analysis_period', 60),
        ('report_number', 80),
        ('experiment_code', 100),
        ('equipment_code', 100),
        ('operator_id', 100),
        ('author', 80),
        ('date', 10),
    ]:
        result['metadata'][key] = string(metadata.get(key, ''), limit, key)
    if not result['metadata']['title'].strip():
        raise ValueError('보고서 제목을 입력하세요.')
    for key in ('date', 'received_date'):
        if result['metadata'][key]:
            date.fromisoformat(result['metadata'][key])
    result['summary'] = string(document.get('summary', ''), 1500, '전체 요약')
    result['llm'] = validate_settings(document.get('llm', {}))
    canonical = {s['id']: s for s in project['samples']}
    phase_names = project_phase_names(project)
    defaults = {s['sample_id']: s for s in result['samples']}
    entries = document.get('samples')
    if not isinstance(entries, list) or len(entries) != len(canonical):
        raise ValueError('모든 등록 시료의 포함/제외 상태가 필요합니다.')
    ids = [entry.get('sample_id') for entry in entries]
    if len(set(ids)) != len(ids) or set(ids) != set(canonical):
        raise ValueError('중복 또는 다른 프로젝트의 시료입니다.')
    result['samples'] = []
    for entry in entries:
        sid = entry['sample_id']
        clean = deepcopy(defaults[sid])
        clean['display_name'] = string(entry.get('display_name', ''), 200, '표시 시료명')
        if not clean['display_name'].strip():
            raise ValueError('표시 시료명을 입력하세요.')
        for key in (
            'included',
            'summary_included',
            'include_phase_image',
            'include_quantity_image',
            'reviewed',
        ):
            if type(entry.get(key)) is not bool:
                raise ValueError(f'{key}: boolean 상태가 필요합니다.')
            clean[key] = entry[key]
        for key, limit in [
            ('phase_caption', 300),
            ('quantity_caption', 300),
            ('manual_note', 10000),
        ]:
            clean[key] = string(entry.get(key, ''), limit, key)
        clean['note_order_edited'] = bool(entry.get('note_order_edited', False))
        if entry.get('analysis_fingerprint', BASE_FINGERPRINT) != clean['analysis_fingerprint']:
            raise ValueError('시료 분석 검토값이 변경되었습니다. 보고서를 다시 열어 확인하세요.')
        clean['analysis_review_required'] = (
            bool(entry.get('analysis_review_required', False)) and not clean['reviewed']
        )
        allowed = {n['id']: n for n in build_candidates(canonical[sid])}
        notes = entry.get('notes', [])
        if not isinstance(notes, list) or len(notes) > len(allowed):
            raise ValueError('잘못된 특이사항 후보 목록입니다.')
        seen = set()
        clean['notes'] = []
        for note in notes:
            cid = note.get('id')
            if cid not in allowed or cid in seen:
                raise ValueError('다른 시료 또는 알 수 없는 특이사항 후보입니다.')
            seen.add(cid)
            candidate = deepcopy(allowed[cid])
            text = string(note.get('text', ''), 10000, '특이사항')
            if type(note.get('selected')) is not bool:
                raise ValueError('특이사항 포함 상태가 필요합니다.')
            # A model-written sentence stays the model's only while it still passes the evidence
            # check; anything else that differs from the template is the author's own text.
            model = note.get('origin') == 'llm' and not note.get('edited')
            written = (
                model
                and text != candidate['text']
                and verify_note_text(candidate, text, phase_names) is None
            )
            if model and not written:
                # A model sentence that no longer passes is never printed as the author's.
                text = candidate['text']
            edited = not model and (bool(note.get('edited')) or text != candidate['text'])
            candidate.update(
                text=text,
                selected=note['selected'],
                edited=edited,
                selection_edited=bool(note.get('selection_edited', False)),
                origin='user'
                if edited
                else ('llm' if written else ('rules' if model else note.get('origin', 'rules'))),
            )
            if candidate['origin'] not in ('user', 'rules', 'llm'):
                candidate['origin'] = 'rules'
            clean['notes'].append(candidate)
        retained = entry.get('retained_notes', [])
        if not isinstance(retained, list) or len(retained) > 100:
            raise ValueError('보존된 이전 문장을 확인하세요.')
        source_names = {r['filename'] for r in canonical[sid]['sources']}
        retained_ids = set()
        clean['retained_notes'] = []
        for note in retained:
            nid = string(note.get('id', ''), 80, '보존 문장 ID')
            if not nid or nid in retained_ids or type(note.get('selected')) is not bool:
                raise ValueError('보존 문장의 ID·포함 상태를 확인하세요.')
            retained_ids.add(nid)
            evidence = []
            for e in note.get('evidence', []):
                if e.get('filename') not in source_names:
                    raise ValueError('보존 문장의 출처가 다른 시료입니다.')
                evidence.append({'filename': e['filename'], 'page': e.get('page')})
            clean['retained_notes'].append(
                {
                    'id': nid,
                    'title': string(note.get('title', '이전 사용자 문장'), 200, '보존 문장 제목'),
                    'candidate_id': string(note.get('candidate_id', nid), 80, '이전 후보 ID'),
                    'text': string(note.get('text', ''), 10000, '보존 문장'),
                    'selected': note['selected'],
                    'evidence': evidence,
                }
            )
        generation = entry.get('generation', {})
        clean['generation'] = {
            'status': generation.get('status')
            if generation.get('status') in ('rules', 'llm', 'fallback')
            else 'rules',
            'model_used': string(generation.get('model_used') or '', 200, '모델명') or None,
            'message': string(generation.get('message') or '', 500, '생성 상태'),
        }
        result['samples'].append(clean)
    result['figure'] = (
        validate_figure(document['figure'], result['samples'])
        if 'figure' in document
        else default_figure(False)
    )
    result['quantity_table'] = (
        validate_quantity_table(document['quantity_table'])
        if 'quantity_table' in document
        else default_quantity_table(False)
    )
    result['phase_classes'] = (
        validate_phase_classes(document['phase_classes'], project)
        if 'phase_classes' in document
        else default_phase_classes()
    )
    included = [s for s in result['samples'] if s['included']]
    if not included:
        raise ValueError('보고서에 포함할 시료를 하나 이상 선택하세요.')
    if sum(s['summary_included'] for s in included) > 4:
        raise ValueError('첫 장 요약 시료는 최대 4개입니다. 나머지는 상세에 모두 포함됩니다.')
    return result


def sync_analysis(project, document):
    """Return a reviewable draft; never write or discard an author's prose."""
    if document.get('analysis_revision', 0) == project.get('analysis_revision', 0):
        return deepcopy(document), False
    result = deepcopy(document)
    result['analysis_revision'] = project.get('analysis_revision', 0)
    canonical = {s['id']: s for s in project['samples']}
    for entry in result['samples']:
        sample = canonical[entry['sample_id']]
        fingerprint = sample.get('analysis_fingerprint', BASE_FINGERPRINT)
        if entry.get('analysis_fingerprint', BASE_FINGERPRINT) == fingerprint:
            continue
        old = {n['kind']: n for n in entry['notes']}
        candidates = build_candidates(sample)
        by_kind = {n['kind']: n for n in candidates}
        order = [n['kind'] for n in entry['notes'] if n['kind'] in by_kind] + [
            n['kind'] for n in candidates if n['kind'] not in old
        ]
        notes = []
        for kind in order:
            note = deepcopy(by_kind[kind])
            previous = old.get(kind)
            note.update(
                selected=previous['selected']
                if previous and previous.get('selection_edited')
                else True,
                edited=bool(previous and previous.get('edited')),
                selection_edited=bool(previous and previous.get('selection_edited')),
                origin='user' if previous and previous.get('edited') else 'rules',
            )
            if previous and previous.get('edited'):
                note['text'] = previous['text']
            notes.append(note)
        entry.setdefault('retained_notes', [])
        for kind, previous in old.items():
            if kind not in by_kind and previous.get('edited'):
                retained = {
                    key: deepcopy(previous[key])
                    for key in ('id', 'title', 'text', 'selected', 'evidence')
                }
                signature = f"{previous['id']}:{result['analysis_revision']}:{len(entry['retained_notes'])}:{previous['text']}"
                retained.update(
                    candidate_id=previous['id'],
                    id='retained-' + hashlib.sha256(signature.encode()).hexdigest()[:24],
                )
                entry['retained_notes'].append(retained)
        entry.update(
            notes=notes,
            reviewed=False,
            analysis_review_required=True,
            analysis_fingerprint=fingerprint,
            generation={
                'status': 'rules',
                'model_used': None,
                'message': '분석 검토값이 변경되어 근거 후보를 갱신했습니다. 보존된 사용자 문장과 원본 이미지를 다시 검토하세요.',
            },
        )
    return result, True


def merge_notes(entry: dict, result: dict):
    """Merge regenerated note candidates into a sample entry, keeping user-edited text,
    explicit selections and explicit order."""
    previous = {note['id']: note for note in entry['notes']}
    candidates = {c['id']: c for c in result['candidates']}
    order = list(dict.fromkeys(result['selected_ids'] + list(candidates)))
    if entry.get('note_order_edited'):
        order = [cid for cid in previous if cid in candidates] + [
            cid for cid in order if cid not in previous
        ]
    merged = []
    for cid in order:
        candidate = candidates[cid]
        old = previous.get(cid)
        if old and old.get('edited'):
            merged.append(old)
        else:
            note = deepcopy(candidate)
            note['text'] = result.get('texts', {}).get(cid, candidate['text'])
            selected = (
                old['selected']
                if old and old.get('selection_edited')
                else cid in result['selected_ids']
            )
            note.update(
                selected=selected,
                selection_edited=bool(old and old.get('selection_edited')),
                edited=False,
                origin='llm' if result['status'] == 'llm' else 'rules',
            )
            merged.append(note)
    entry['notes'] = merged
    entry['generation'] = {key: result.get(key) for key in ('status', 'message', 'model_used')}


def validate_for_save(project: dict, document: dict) -> dict:
    """Reject documents written against another analysis revision, then validate them."""
    if document.get('analysis_revision', 0) != project.get('analysis_revision', 0):
        raise RevisionConflict('분석 검토 버전이 변경되었습니다. 보고서를 다시 열어 확인하세요.')
    return validate_document(project, document)


def carry_review_flags(current: dict | None, clean: dict) -> None:
    """Keep 'review required' on samples whose analysis changed since the saved revision."""
    if not current:
        return
    prior = {s['sample_id']: s for s in current['samples']}
    for entry in clean['samples']:
        old = prior[entry['sample_id']]
        required = old.get('analysis_fingerprint', BASE_FINGERPRINT) != entry[
            'analysis_fingerprint'
        ] or old.get('analysis_review_required', False)
        if required:
            entry['analysis_review_required'] = not entry['reviewed']
