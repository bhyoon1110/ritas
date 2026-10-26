"""Editable delivery-mail drafts, separate from report slides and actual sending."""
import hashlib
import json
import re

from .phase_class import phase_ref
from ...report_mail import normalize_delivery

CLASSES = {'major': '주요상', 'trace': '미량상', 'presumed': '추정상'}


def empty_email():
    """Return an independent blank draft for new and legacy reports."""
    return dict(subject='', body='', status='empty', source_fingerprint='', model_used='', message='')


def validate_email(value):
    """Validate bounded plain-text mail without interpreting it as HTML or headers."""
    if not isinstance(value, dict):
        raise ValueError('이메일 제목·본문 형식을 확인하세요.')
    result = empty_email()
    for key, limit in (('subject', 180), ('body', 4000), ('model_used', 200), ('message', 500), ('source_fingerprint', 64)):
        text = value.get(key, '')
        if not isinstance(text, str) or len(text) > limit or '\x00' in text:
            raise ValueError(f'이메일 {key}: 최대 {limit}자의 문자열이 필요합니다.')
        result[key] = text
    if any(c in result['subject'] for c in '\r\n'):
        raise ValueError('이메일 제목은 한 줄로 입력하세요.')
    if result['source_fingerprint'] and not re.fullmatch('[0-9a-f]{64}', result['source_fingerprint']):
        raise ValueError('이메일 분석 근거 식별자가 올바르지 않습니다.')
    status = value.get('status', 'empty')
    if status not in ('empty', 'manual', 'rules', 'llm', 'fallback'):
        raise ValueError('이메일 생성 상태가 올바르지 않습니다.')
    result['status'] = status
    if 'delivery' in value:
        result['delivery'] = normalize_delivery(value['delivery'])
    return result


def email_evidence(project, document):
    """Use reviewed values and current author edits, never browser-supplied RAW facts."""
    samples = {s['id']: s for s in project['samples']}
    labels = document['phase_classes']['labels']
    facts = dict(experiment='XRD', analysis_revision=project.get('analysis_revision', 0),
                 report_context={k: document['metadata'].get(k, '') for k in
                                 ('title', 'report_number', 'client', 'requester', 'analysis_period', 'author')},
                 summary=document['summary'], samples=[])
    for entry in document['samples']:
        if not entry['included']:
            continue
        sample = samples[entry['sample_id']]
        phases = []
        for phase in sample.get('phases', []):
            label = next((v['class'] for v in labels if v['sample_id'] == sample['id'] and v['phase'] == phase_ref(phase)), None)
            phases.append(dict(name=phase['name'], formula=phase.get('formula') or '',
                               classification=CLASSES.get(label, '미분류')))
        notes = [n['text'] for n in entry['notes'] if n['selected']]
        # Retained old prose is explicitly marked: it is not current scientific evidence.
        retained = [n['text'] for n in entry.get('retained_notes', []) if n['selected']]
        facts['samples'].append(dict(name=entry['display_name'], phases=phases,
            quantities=[{k: q.get(k, '') for k in ('phase', 'raw', 'unit')} for q in sample.get('quantities', [])],
            selected_notes=notes, manual_note=entry['manual_note'], retained_previous_notes=retained,
            review_required=entry.get('analysis_review_required', False),
            warnings=sample.get('warnings', [])))
    return facts


def evidence_fingerprint(facts):
    """Identify the reviewed evidence independently of the editable email itself."""
    return hashlib.sha256(json.dumps(facts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def bounded_evidence(facts):
    """Bound model context by whole evidence records; expose every omission."""
    result = {k: v for k, v in facts.items() if k != 'samples'}
    result['sample_count'] = len(facts['samples'])
    result['omitted_samples'] = max(0, len(facts['samples']) - 12)
    result['samples'] = []
    for sample in facts['samples'][:12]:
        item = dict(sample)
        for key, count in (('phases', 20), ('quantities', 20), ('selected_notes', 4),
                           ('retained_previous_notes', 2), ('warnings', 4)):
            item[key] = sample[key][:count]
            item['omitted_' + key] = max(0, len(sample[key]) - count)
        # Omit oversized prose instead of cutting a scientific number in half.
        for key in ('selected_notes', 'retained_previous_notes', 'warnings'):
            kept = [v for v in item[key] if len(v) <= 1500]
            item['omitted_' + key] += len(item[key]) - len(kept)
            item[key] = kept
        if len(item['manual_note']) > 1500:
            item['manual_note'] = ''
            item['omitted_manual_note'] = True
        result['samples'].append(item)
    return result


def rule_email(facts):
    """Build a deterministic fallback that preserves supplied phase values verbatim."""
    context = facts['report_context']
    samples = facts['samples']
    name = samples[0]['name'] if samples else '시료'
    if len(samples) > 1:
        name += f' 외 {len(samples) - 1}종'
    subject = ' '.join(f'[RIST] {name} XRD 분석 결과 안내'.split())
    if len(subject) > 180:
        subject = '[RIST] XRD 분석 결과 안내'
    lines = ['안녕하십니까.', '', f'{name}의 XRD 분석 보고서를 안내드립니다.']
    if context['report_number']:
        lines.append('의뢰번호: ' + context['report_number'])
    if facts['summary'].strip():
        lines += ['', '분석 결과 요약', facts['summary'].strip()]
    lines += ['', '주요 분석 결과']
    omitted = False
    for sample in samples:
        details = []
        for label in ('주요상', '미량상', '추정상', '미분류'):
            names = [p['name'] for p in sample['phases'] if p['classification'] == label]
            if names:
                details.append(label + ': ' + ', '.join(names))
        if sample['quantities']:
            details.append('WPPF 정량: ' + '; '.join(f"{q['phase']} {q['raw']} {q['unit']}" for q in sample['quantities']))
        else:
            details.append('WPPF 정량값 미제공')
        line = '- ' + sample['name'] + ': ' + ' / '.join(details)
        if len('\n'.join(lines)) + len(line) > 3000:
            omitted = True
            break
        lines.append(line)
        for note in sample['selected_notes'] + ([sample['manual_note']] if sample['manual_note'].strip() else []):
            if len('\n'.join(lines)) + len(note) < 3200:
                lines.append('  검토 문장: ' + note)
            else:
                omitted = True
    if omitted:
        lines += ['', '메일에는 일부 결과만 요약했습니다. 전체 시료와 상세 내용은 보고서에서 확인해 주십시오.']
    if any(s['review_required'] or s['retained_previous_notes'] for s in samples):
        lines += ['', '분석값 변경 또는 이전 문장이 있어 최종 대조 검토가 필요합니다.']
    if any(s['warnings'] for s in samples):
        lines += ['', '입력 자료의 확인 사항이 있습니다. 분석 자료 검토 화면의 경고를 함께 확인해 주십시오.']
    lines += ['', '상 동정 결과와 추정상은 구분하여 검토해 주십시오. 정량값의 단위·괄호 표기는 원문을 유지했습니다.',
              '상세 그래프와 분석 내용은 보고서를 확인해 주시고, 추가 확인이 필요한 사항은 문의해 주십시오.', '', '감사합니다.']
    return dict(empty_email(), subject=subject, body='\n'.join(lines), status='rules',
                source_fingerprint=evidence_fingerprint(facts), message='분석 근거로 작성한 기본 문안입니다. 발송 전 검토하세요.')


def email_markdown(document):
    """Serialize the saved mail for delivery, omitting untouched blank drafts."""
    email = validate_email(document.get('email', empty_email()))
    subject, body = email['subject'].strip(), email['body'].strip()
    lines = ['# ' + subject, ''] if subject else []
    if body:
        lines.append(body)
    return '\n'.join(lines).rstrip() + '\n' if lines else ''
