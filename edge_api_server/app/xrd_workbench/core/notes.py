"""Evidence-bound note templates. No new phase identification or interpretation."""

import hashlib
import json
import re
from decimal import Decimal
from urllib.parse import urlsplit

VERSION = 'evidence-notes-1'


def build_candidates(sample: dict) -> list[dict]:
    """Evidence-bound note candidates for one sample. Every number in the text comes from
    the source data; no phase identification or interpretation is added."""
    candidates = []
    reports = sample.get('reports', {})

    def add(kind, title, text, evidence, mandatory=False):
        signature = json.dumps(
            [sample['id'], kind, text, evidence, VERSION], ensure_ascii=False, sort_keys=True
        )
        cid = hashlib.sha256(signature.encode()).hexdigest()[:24]
        candidates.append(
            dict(id=cid, kind=kind, title=title, text=text, evidence=evidence, mandatory=mandatory)
        )

    phases = sample.get('phases', [])
    if phases:
        names = '、'.join(p['name'] for p in phases)
        evidence = [
            dict(
                filename=p.get('source', ''), page=p['page'], field='Phase name', raw=p['name_raw']
            )
            for p in phases
        ]
        add(
            'reported_phases',
            '보고된 상 목록',
            f'상매칭 Report에는 {names}의 {len(phases)}개 상이 기재되어 있습니다.',
            evidence,
            True,
        )
    quantities = sample.get('quantities', [])
    if quantities:
        filename = reports.get('wppf_report', {}).get('filename', '')
        evidence = [
            dict(
                filename=q.get('source_filename') or filename,
                page=q['page'],
                field='Quantity' if q.get('origin') == 'manual' else 'Weight fraction',
                raw=q['raw'],
            )
            for q in quantities
        ]
        text = '; '.join(f"{q['phase']} {q['raw']} {q['unit']}" for q in quantities)
        manual = any(q.get('origin') == 'manual' for q in quantities)
        prefix = (
            '등록된 출처에서 직접 확인·입력한 정량값은'
            if manual
            else '정량(WPPF) Report의 추출·검토값은'
            if sample.get('analysis_review', {}).get('changes')
            else '정량(WPPF) Report에 기재된 값은'
        )
        add(
            'reported_quantities',
            '정량 결과 (직접 입력)' if manual else '보고된 정량 결과',
            f'{prefix} {text}입니다. 괄호 표기는 입력 토큰 그대로 유지했습니다.'
            if manual
            else f'{prefix} {text}입니다. 괄호 표기는 원문 그대로 유지했습니다.',
            evidence,
            True,
        )
        if all(q.get('unit') == 'wt%' and q.get('value') is not None for q in quantities):
            total = sum(Decimal(str(q['raw']).split('(')[0]) for q in quantities)
            if abs(total - Decimal('100')) >= Decimal('.01'):
                add(
                    'weight_sum',
                    '정량 값 합계',
                    f'보고된 중량 분율 중심값의 합계는 {total:.2f} wt%입니다. 원문 값을 100 wt%로 재조정하지 않았습니다.',
                    evidence,
                )
    elif reports.get('wppf_report'):
        filename = reports['wppf_report']['filename']
        add(
            'quantity_extraction_review',
            '정량 표 추출 확인',
            '정량(WPPF) Report는 등록되어 있으나 수치형 정량 결과를 추출하지 못했습니다. 원본 Report에서 결과를 확인하세요.',
            [
                dict(
                    filename=filename,
                    page=None,
                    field='extraction_status',
                    raw='no_structured_quantities',
                )
            ],
            True,
        )
    elif any(r['role'] == 'quantity_image' for r in sample.get('sources', [])):
        filename = next(r['filename'] for r in sample['sources'] if r['role'] == 'quantity_image')
        add(
            'quantity_extraction_review',
            '정량 수치 확인',
            '정량 이미지는 등록되어 있으나 수치형 정량 값은 입력 자료에서 확인하지 못했습니다. 원본 이미지를 확인하세요.',
            [
                dict(
                    filename=filename,
                    page=None,
                    field='registered_quantity_image',
                    raw='no_structured_quantities',
                )
            ],
            True,
        )
    elif reports.get('phase_report'):
        filename = reports['phase_report']['filename']
        add(
            'no_quantity',
            '정량 자료 미제공',
            '현재 등록 자료에 정량(WPPF) 결과가 포함되어 있지 않습니다. 상매칭 결과만으로 함량을 채우지 않았습니다.',
            [
                dict(
                    filename=filename,
                    page=None,
                    field='registered_sources',
                    raw=[r['filename'] for r in sample.get('sources', [])],
                )
            ],
        )
    for role, report in reports.items():
        labels = [label for label in report.get('dataset_labels', []) if label != sample['name']]
        if labels:
            add(
                'dataset_label_' + role,
                '시료명 표기 확인',
                f"등록 시료명은 {sample['name']}이고 {report['filename']}의 내부 시료명 표기는 {', '.join(labels)}입니다. 파일 연결을 확인할 수 있도록 두 표기를 보존했습니다.",
                [dict(filename=report['filename'], page=1, field='dataset_label', raw=labels)],
            )
    changes = sample.get('analysis_review', {}).get('changes', [])
    if changes:
        descriptions = []
        evidence = []
        for change in changes[:10]:
            location = f"{change['filename']} p.{change['page']} 추출 표 {change['table']+1}"
            if change.get('excluded_table'):
                descriptions.append(location + '를 분석 활용에서 제외했습니다.')
            elif 'excluded_rows' in change:
                descriptions.append(
                    location
                    + '의 행 '
                    + ', '.join(str(n + 1) for n in change['excluded_rows'])
                    + '을 분석 활용에서 제외했습니다.'
                )
            else:
                descriptions.append(
                    f"{location} 행 {change['row']+1}·열 {change['col']+1}: 추출값 {change.get('original')!s}, 검토값 {change.get('value')!s} (사유: {change['reason']})."
                )
            evidence.append(
                {
                    'filename': change['filename'],
                    'page': change['page'],
                    'field': 'analysis_review',
                    'raw': change,
                }
            )
        if len(changes) > 10:
            descriptions.append(
                f'총 {len(changes)}건의 검토 변경이 있습니다. 전체 기록은 분석 담당자가 서버의 분석 자료 검토 화면에서 확인할 수 있습니다.'
            )
        add(
            'analysis_changes',
            '분석 검토 변경',
            ' '.join(descriptions) + ' 원본 PDF·이미지는 수정하지 않았습니다.',
            evidence,
            True,
        )
    return candidates


# Words that would add an interpretation the source does not state.
INTERPRETATION_WORDS = (
    '순도',
    '합격',
    '불합격',
    '적합',
    '부적합',
    '불순물',
    '오염',
    '추정',
    '판단',
    '의심',
    '가능성',
)
NUMBER = re.compile(r'\d+(?:\.\d+)?(?:\(\d+\))?')


NEGATIONS = ('않', '없', '못')  # a sentence must keep the template's negations


def fact_tokens(text: str, phase_names) -> list[tuple[str, str]]:
    """Phase names (whole words, longest first) and number tokens of `text` in reading order."""
    taken = [False] * len(text)
    found = []
    for name in sorted({n for n in phase_names if n}, key=len, reverse=True):
        pattern = r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9])'
        for match in re.finditer(pattern, text):
            if not any(taken[match.start() : match.end()]):
                taken[match.start() : match.end()] = [True] * (match.end() - match.start())
                found.append((match.start(), 'phase', name))
    masked = ''.join(' ' if t else c for t, c in zip(taken, text, strict=True))
    found += [(m.start(), 'number', m.group()) for m in NUMBER.finditer(masked)]
    return [(kind, value) for _, kind, value in sorted(found)]


def verify_note_text(candidate: dict, text, phase_names) -> str | None:
    """Why a model-written sentence cannot replace `candidate`'s template, or None when it can.
    The sentence must state the template's phase names and number tokens (an uncertainty in
    parentheses stays attached) in the same order with nothing added or dropped, use no Latin word
    the template lacks (no new phase), keep its negations, and add no interpretation word."""
    if not isinstance(text, str) or not text.strip():
        return '빈 문장'
    if len(text) > 1500:
        return '문장이 너무 깁니다'
    template = candidate['text']
    if fact_tokens(text, phase_names) != fact_tokens(template, phase_names):
        return '상 이름·수치가 원문과 다르거나 순서가 바뀌었습니다'
    extra = set(re.findall(r'[A-Za-z]+', text)) - set(re.findall(r'[A-Za-z]+', template))
    if extra:
        return f'근거에 없는 표기 {", ".join(sorted(extra))}'
    for marker in NEGATIONS:
        if text.count(marker) != template.count(marker):
            return '부정 표현이 원문과 다릅니다'
    for word in INTERPRETATION_WORDS:
        if word in text and word not in template:
            return f'근거 없는 해석 표현 {word}'
    return None


def validate_writing(sample_id: str, candidates: list[dict], raw: str, phase_names) -> dict:
    """Check a model reply {sample_id, notes:[{id, text}]} for this sample. Unknown, duplicate
    or foreign entries reject the whole reply; a sentence that fails verify_note_text keeps its
    template. Mandatory candidates the model left out are appended with their template."""
    if len(raw) > 40000:
        raise ValueError('모델 응답이 허용 크기를 넘었습니다.')
    value = json.loads(raw)
    if not isinstance(value, dict) or set(value) != {'sample_id', 'notes'}:
        raise ValueError('모델은 sample_id와 notes만 응답해야 합니다.')
    if value['sample_id'] != sample_id:
        raise ValueError('다른 시료의 응답을 사용할 수 없습니다.')
    notes = value['notes']
    by_id = {c['id']: c for c in candidates}
    if not isinstance(notes, list) or any(
        not isinstance(n, dict) or set(n) != {'id', 'text'} or n['id'] not in by_id for n in notes
    ):
        raise ValueError('모델이 알 수 없는 후보 또는 추가 필드를 반환했습니다.')
    ids = [n['id'] for n in notes]
    if len(set(ids)) != len(ids):
        raise ValueError('모델이 같은 후보를 두 번 반환했습니다.')
    texts, replaced = {}, 0
    for note in notes:
        if verify_note_text(by_id[note['id']], note['text'], phase_names) is None:
            texts[note['id']] = note['text'].strip()
        else:
            replaced += 1
    # A weak model cannot silently omit required reported results.
    ids += [c['id'] for c in candidates if c.get('mandatory') and c['id'] not in ids]
    return dict(ids=ids, texts=texts, replaced=replaced)


def validate_settings(settings: dict) -> dict:
    """Normalise local LLM settings; only loopback HTTP(S) endpoints without credentials pass."""
    provider = settings.get('provider', 'none')
    if provider not in ('none', 'ollama', 'lmstudio'):
        raise ValueError('지원하지 않는 로컬 모델 연결 방식입니다.')
    default = 'http://127.0.0.1:11434' if provider == 'ollama' else 'http://127.0.0.1:1234/v1'
    base = str(settings.get('base_url') or default).rstrip('/')
    parsed = urlsplit(base)
    if (
        parsed.scheme not in ('http', 'https')
        or parsed.hostname not in ('127.0.0.1', 'localhost', '::1')
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError('모델 주소는 인증정보 없는 localhost/loopback HTTP 주소여야 합니다.')
    model = str(settings.get('model') or '').strip()
    if len(model) > 200:
        raise ValueError('모델명이 너무 깁니다.')
    return dict(provider=provider, base_url=base, model=model)
