"""One page geometry for preview, report HTML and PDF. All dimensions in mm."""

import hashlib
import io
from urllib.parse import quote

from PIL import Image

from ..core.figure import build_plan, panel_slice
from ..core.quantity_table import build_quantity_table
from ..core.report import validate_document
from ..render import theme
from ..render.figure import render_panel_png
from ..render.fonts import wrap_lines
from ..render.items import text_item
from ..render.sample_page import (
    place_sample,
    quantity_label,
    quantity_status,
    remark_items,
    remarks,
)
from ..render.theme import BOTTOM, LEFT, TOP, WIDTH

# Comparison figure geometry (mm): one curve, one reference-peak row (shrinks to the minimum when
# a page is short of room), and the axis area.
CURVE_MM, REF_MM, REF_MIN_MM, AXIS_MM = 11, 5, 3.5, 16
CURVE_MAX_MM = 26  # a figure with few curves is stretched to this height per curve
FIGURE_FILENAME = 'sample-comparison.png'
COVER_SCALES = (1, 0.9, 0.8, 0.7, 0.6)  # the first page shrinks its sample blocks to fit


def archive_path(record):
    folder = 'Measured Data' if record['role'] == 'measurement' else 'Report'
    return f"{folder}/{record['id'][:8]}-{record['filename']}"


def source_links(sample):
    labels = {
        'phase_report': '상매칭 Report',
        'wppf_report': '정량(WPPF) Report',
        'measurement': 'Measured Data (TXT)',
    }
    return [
        dict(
            label=labels[r['role']],
            source_id=r['id'],
            filename=r['filename'],
            href=quote(archive_path(r), safe='/'),
        )
        for r in sample['sources']
        if r['role'] in labels
    ]


def build_pages(project, document, resolve_source):
    document = validate_document(project, document)
    sources = {s['id']: s for s in project['samples']}
    entries = [s for s in document['samples'] if s['included']]
    pages = []

    def new_page(kind, title, sample_id=None, band=True):
        if not band:
            page = dict(
                kind=kind,
                title=title,
                sample_id=sample_id,
                items=[],
                number=len(pages) + 1,
                header_lines=[],
                header_bottom=0,
                content_top=TOP,
            )
            pages.append(page)
            return page
        title_item = text_item(
            title,
            LEFT,
            theme.HEADER_PAD,
            WIDTH,
            theme.HEADER_FONT,
            True,
            theme.WHITE,
            theme.HEADER_LEADING,
        )
        title_item['role'] = 'header'
        header_bottom = max(
            theme.HEADER_MIN_H,
            theme.HEADER_PAD * 2 + len(title_item['lines']) * theme.HEADER_LEADING,
        )
        band = dict(
            type='rect', x=0, y=0, w=theme.PAGE_W, h=header_bottom, fill=theme.NAVY, role='header'
        )
        page = dict(
            kind=kind,
            title=title,
            sample_id=sample_id,
            items=[band, title_item],
            number=len(pages) + 1,
            header_lines=title_item['lines'],
            header_bottom=header_bottom,
            content_top=max(TOP, header_bottom + theme.HEADER_GAP),
        )
        pages.append(page)
        return page

    def add_cover(page):
        """Title and the request details; a line without a value is left out, never filled."""
        meta = document['metadata']
        person = ' '.join(
            part
            for part in (f"({meta['client']})" if meta['client'] else '', meta['requester'])
            if part
        )
        lines = [
            f'작성자: {meta["author"]}',
            f'완료일: {meta["date"]}',
            f'접수일: {meta["received_date"]}' if meta['received_date'] else '',
            f'분석 기간: {meta["analysis_period"]}' if meta['analysis_period'] else '',
            f'의뢰번호: {meta["report_number"]}' if meta['report_number'] else '',
            f'실험코드: {meta.get("experiment_code", "")}  |  장비코드: {meta.get("equipment_code", "")}',
            f'실험자 ID: {meta.get("operator_id", "")}',
            f'{person} 님' if meta['requester'] else person,
        ]
        # The largest title and line sizes that fit the page (long titles and fields shrink).
        for title_y in (theme.COVER_TITLE_Y, 40, TOP):
            for title_size, line_size in ((32, 14), (28, 14), (24, 13), (20, 12), (16, 11), (14, 10), (12, 9)):
                title = text_item(meta['title'], LEFT, title_y, WIDTH, title_size, True, theme.NAVY)
                y = title['y'] + title['h'] + theme.COVER_GAP_Y
                items = [title]
                for line in filter(None, lines):
                    item = text_item(line, LEFT, y, WIDTH, line_size, color=theme.TEXT)
                    items.append(item)
                    y += item['h'] + 3
                if y - 3 <= BOTTOM:
                    page['items'] += items
                    return
        raise ValueError('표지 내용이 페이지를 넘습니다. 제목이나 표지 입력 항목을 줄이세요.')

    add_cover(new_page('cover', '표지', band=False))
    summary_page = new_page('summary', '분석 결과 요약')
    y = summary_page['content_top']
    selected = [s for s in entries if s['summary_included']]
    counts = text_item(
        f'총 {len(entries)}개 시료  |  첫 장 요약 {len(selected)}개 시료  |  전체 시료의 상세는 다음 페이지에 수록',
        LEFT,
        y,
        WIDTH,
        9,
        color=theme.SOFT,
    )
    summary_page['items'].append(counts)
    y += counts['h'] + 9
    if document['summary'].strip():
        item = text_item(document['summary'], LEFT, y, WIDTH, 11)
        summary_page['items'].append(item)
        y += item['h'] + 8
    column_w = (WIDTH - theme.COVER_GAP) / 2

    def summary_block(entry, x, top, s, with_remarks):
        sample = sources[entry['sample_id']]
        phase_names = [p['name'] for p in sample['phases']]
        displayed = phase_names[:4]
        phases = (
            '; '.join(displayed)
            if displayed
            else (
                '상 Report 등록 · 추출/제외 상태 확인 필요'
                if sample.get('reports', {}).get('phase_report')
                else '상 목록 미제공'
            )
        )
        if len(phase_names) > 4:
            phases += f'; 외 {len(phase_names)-4}개 상 (상세 Report 참조)'
        quantities = sample.get('quantities', [])
        if quantities:
            quant = '; '.join(f"{q['phase']} {q['raw']} {q['unit']}" for q in quantities[:4])
        else:
            quant = quantity_status(sample)
        if len(quantities) > 4:
            quant += f'; 외 {len(quantities)-4}개 값 (원본 Report 참조)'
        heading = text_item(entry['display_name'], x, top, column_w, 12 * s, True, theme.NAVY)
        body = text_item(
            '상매칭 Report: ' + phases + chr(10) + quantity_label(sample) + ': ' + quant,
            x,
            top + heading['h'] + 2 * s,
            column_w,
            9.5 * s,
        )
        items, bottom = [heading, body], body['y'] + body['h']
        rows = remarks(entry)
        if rows and with_remarks:
            block, bottom = remark_items(rows, x, bottom + 3 * s, column_w, s, 10 * s)
            items += block
        elif rows:
            moved = text_item(
                '특이사항: 시료별 특이사항 페이지에 수록',
                x,
                bottom + 2 * s,
                column_w,
                8.5 * s,
                color=theme.MUTED,
            )
            items.append(moved)
            bottom = moved['y'] + moved['h']
        return items, bottom

    def summary_rows(s, with_remarks):
        """Items of the two-column sample blocks at scale `s`, or None when they overflow."""
        items, top, bottom_of_blocks = [], y, y
        for start in range(0, len(selected), 2):
            row = [
                summary_block(
                    entry, LEFT + col * (column_w + theme.COVER_GAP), top, s, with_remarks
                )
                for col, entry in enumerate(selected[start : start + 2])
            ]
            if any(bottom > BOTTOM - 4 for _, bottom in row):
                return None
            for block, _ in row:
                items += block
            bottom_of_blocks = max(bottom for _, bottom in row)
            top = bottom_of_blocks + 8 * s
        # The gap below the last row is only spacing: it never makes the page overflow.
        return items, bottom_of_blocks

    # Two columns: samples fill a row left then right; a row is as tall as its taller block.
    # When the remarks cannot fit the first page even at the smallest size, they move to the
    # remark pages (and the block says so) instead of refusing the report.
    attempts = [(s, True) for s in COVER_SCALES] + [(s, False) for s in COVER_SCALES]
    placed, remarks_on_cover = None, True
    for scale, with_remarks in attempts:
        placed = summary_rows(scale, with_remarks)
        if placed is not None:
            remarks_on_cover = with_remarks
            break
    if placed is None:
        raise ValueError(
            '첫 장 요약이 페이지를 넘습니다. 요약 문장이나 특이사항을 줄이거나 첫 장 포함 시료를 줄이세요.'
        )
    summary_page['items'] += placed[0]
    y = placed[1]
    if y > BOTTOM:
        raise ValueError('첫 장 내용이 페이지를 넘습니다. 제목/요약을 줄여주세요.')
    y = max(y, BOTTOM - 16)
    if y + 10 <= BOTTOM:
        item = text_item(
            '등록된 분석 자료와 작성자의 검토·입력값을 정리했습니다. 원본 이미지는 수정하지 않았습니다. 시료마다 한 장에 상 동정·정량 결과와 작성자 분류를 표시하고, 원본 Report와 측정 TXT는 링크로 제공합니다.',
            LEFT,
            y,
            WIDTH,
            9,
            color=theme.MUTED,
        )
        summary_page['items'].append(item)

    def place_text(page, y, text, size, color, title, kind='figure'):
        """Wrapped text from `y` on `page`, continuing on new figure pages instead of running
        past the bottom edge."""
        lines = wrap_lines(text, WIDTH, size)
        leading = size * 1.55 / 2.83464567
        while lines:
            available = int((BOTTOM - y) / leading)
            if available < 1:
                page = new_page(kind, title + ' (계속)')
                y = page['content_top']
                continue
            part, lines = lines[:available], lines[available:]
            page['items'].append(
                dict(
                    type='text',
                    text=chr(10).join(part),
                    lines=part,
                    x=LEFT,
                    y=y,
                    w=WIDTH,
                    h=len(part) * leading,
                    size=size,
                    bold=False,
                    color=color,
                    leading=leading,
                )
            )
            y += len(part) * leading + 2
        return page, y

    def add_figure_pages():
        figure = document['figure']
        if not figure['include']:
            return
        for panel in build_plan(project, document)['panels']:
            base_title = '시료 비교 그래프' + (f" · {panel['title']}" if panel['title'] else '')
            excluded_text = '; '.join(f"{e['name']} ({e['reason']})" for e in panel['excluded'])
            if not panel['curves']:
                page = new_page('figure', base_title)
                place_text(
                    page,
                    page['content_top'],
                    '그림을 만들 수 없습니다. ' + excluded_text,
                    10,
                    theme.TEXT,
                    base_title,
                )
                continue
            rows = len(panel['phases']) if panel['show_reference'] else 0
            total = len(panel['curves'])
            omitted = False  # reference rows left out because they cannot fit on one page

            def tail_items(is_first):
                lines = []
                if is_first and figure['caption'].strip():
                    lines.append((figure['caption'], theme.CAPTION))
                if is_first and omitted:
                    lines.append(
                        (
                            f"기준 피크 줄이 많아(상 {len(panel['phases'])}개) 이 그림에서는 기준 피크 줄을 "
                            '생략했습니다. 그룹을 나누면 표시됩니다.',
                            theme.WARN,
                        )
                    )
                if panel['phases'] and (panel['show_reference'] or panel['show_markers']):
                    lines.append((panel['merge_note'], theme.NOTE))
                return [text_item(t, LEFT, 0, WIDTH, 8.3, color=c) for t, c in lines]

            def title_top(title):
                lines = wrap_lines(title, WIDTH, theme.HEADER_FONT, True)
                band = max(
                    theme.HEADER_MIN_H,
                    theme.HEADER_PAD * 2 + len(lines) * theme.HEADER_LEADING,
                )
                return max(TOP, band + theme.HEADER_GAP)

            def plan_chunks(rows):
                """Pages of the figure: how many curves each takes at the minimum curve height.
                None when not even one curve fits with the reference rows."""
                chunks, start = [], 0
                while start < total:
                    first = start == 0
                    title = base_title if first else base_title + ' (계속)'
                    top = title_top(title)
                    tail = tail_items(first)
                    tail_h = sum(t['h'] + 2 for t in tail)
                    chosen = None
                    for row_mm in (REF_MM, REF_MIN_MM) if rows else (REF_MM,):
                        for n in range(total - start, 0, -1):
                            used = AXIS_MM + n * CURVE_MM + rows * row_mm + tail_h + 4
                            if top + used <= BOTTOM:
                                chosen = (n, row_mm)
                                break
                        if chosen:
                            break
                    if not chosen:
                        return None
                    chunks.append(
                        dict(
                            start=start,
                            n=chosen[0],
                            row_mm=chosen[1],
                            title=title,
                            top=top,
                            tail_h=tail_h,
                        )
                    )
                    start += chosen[0]
                return chunks

            chunks = plan_chunks(rows)
            if chunks is None:
                # Too many phase rows for one page: draw the curves and markers without the
                # reference rows (and say so) instead of refusing to export the report.
                panel = dict(panel, show_reference=False)
                rows, omitted = 0, True
                chunks = plan_chunks(0)
            # One curve height for every page of this comparison (so pages stay comparable),
            # using spare height up to CURVE_MAX_MM but keeping room for the exclusion line.
            exclusion_h = 0
            if panel['excluded']:
                exclusion_h = min(
                    40, text_item('제외: ' + excluded_text, LEFT, 0, WIDTH, 8.3)['h'] + 2
                )
            curve_mm = CURVE_MAX_MM
            for index, c in enumerate(chunks):
                reserve = exclusion_h if index == len(chunks) - 1 else 0
                spare = BOTTOM - c['top'] - 4 - c['tail_h'] - reserve - AXIS_MM - rows * c['row_mm']
                curve_mm = min(curve_mm, spare / c['n'])
            curve_mm = max(curve_mm, CURVE_MM)
            for index, c in enumerate(chunks):
                page = new_page('figure', c['title'])
                top = page['content_top']
                height = AXIS_MM + c['n'] * curve_mm + rows * c['row_mm']
                data = render_panel_png(
                    panel_slice(panel, c['start'], c['start'] + c['n']), WIDTH, height, c['row_mm']
                )
                page['items'].append(
                    dict(
                        type='image',
                        data=data,
                        mime='image/png',
                        x=LEFT,
                        y=top,
                        w=WIDTH,
                        h=height,
                        source_id=None,
                        filename=FIGURE_FILENAME,
                        original_size=[round(WIDTH / 25.4 * 300), round(height / 25.4 * 300)],
                    )
                )
                y = top + height + 2
                for item in tail_items(c['start'] == 0):
                    item['y'] = y
                    page['items'].append(item)
                    y += item['h'] + 2
                if index == len(chunks) - 1 and panel['excluded']:
                    place_text(page, y, '제외: ' + excluded_text, 8.3, theme.WARN, base_title)

    def add_quantity_table_pages():
        if not document['quantity_table']['include']:
            return
        table = build_quantity_table(project, document)
        title = '정량 요약 표'
        if not table['columns']:
            page = new_page('table', title)
            place_text(
                page, page['content_top'], table['empty_message'], 10, theme.TEXT, title, 'table'
            )
            return
        size = theme.QT_FONT
        per_table = int((WIDTH - theme.QT_FIRST_COL_W) // theme.QT_COL_MIN_W)
        count = len(table['columns'])
        chunks = [range(i, min(i + per_table, count)) for i in range(0, count, per_table)]
        page, y = None, 0
        kinds_color = {
            'value': theme.TEXT,
            'missing': theme.MUTED,
            'none': theme.MUTED,
            'blank': theme.MUTED,
            'check': theme.WARN,
        }

        def header(page, y, cols, col_w):
            items = [text_item('시료', LEFT, y, theme.QT_FIRST_COL_W - 2, size, True, theme.NAVY)]
            for j, c in enumerate(cols):
                column = table['columns'][c]
                label = column['name'] + (
                    chr(10) + column['class_note'] if column['class_note'] else ''
                )
                x = LEFT + theme.QT_FIRST_COL_W + j * col_w
                items.append(text_item(label, x, y, col_w - 2, size, True, theme.NAVY))
            page['items'].extend(items)
            return y + max(i['h'] for i in items) + theme.QT_HEADER_GAP

        def row_items(row, y, cols, col_w):
            items = [
                text_item(row['name'], LEFT, y, theme.QT_FIRST_COL_W - 2, size, False, theme.TEXT)
            ]
            for j, c in enumerate(cols):
                cell = row['cells'][c]
                if j == 0 and row['cells'][0]['kind'] == 'none':
                    # The reason is repeated at the start of every table of a split table.
                    cell = row['cells'][0]
                x = LEFT + theme.QT_FIRST_COL_W + j * col_w
                color = kinds_color[cell['kind']]
                items.append(text_item(cell['text'] or ' ', x, y, col_w - 2, size, False, color))
            return items, max(i['h'] for i in items) + theme.QT_ROW_GAP

        for number, cols in enumerate(chunks):
            col_w = min(theme.QT_COL_MAX_W, (WIDTH - theme.QT_FIRST_COL_W) / len(cols))
            page = new_page('table', title if number == 0 else title + ' (계속)')
            y = page['content_top']
            if len(chunks) > 1:
                note = text_item(
                    f'표 {number + 1}/{len(chunks)} · 상 {cols[0] + 1}–{cols[-1] + 1}번째 열',
                    LEFT,
                    y,
                    WIDTH,
                    size,
                    False,
                    theme.MUTED,
                )
                page['items'].append(note)
                y += note['h'] + theme.QT_HEADER_GAP
            y = header(page, y, cols, col_w)
            for row in table['rows']:
                items, height = row_items(row, y, cols, col_w)
                if y + height > BOTTOM:
                    page = new_page('table', title + ' (계속)')
                    y = header(page, page['content_top'], cols, col_w)
                    items, height = row_items(row, y, cols, col_w)
                page['items'].extend(items)
                y += height
        y += 2
        for note in table['footnotes']:
            page, y = place_text(page, y, note, 8.3, theme.NOTE, title, 'table')

    def add_remark_pages():
        """'시료별 특이사항' pages right after the first page: the remarks of every included
        sample that are not on the first page, under the sample name, continuing as needed."""
        title = '시료별 특이사항'
        page = y = None
        for entry in entries:
            if entry['summary_included'] and remarks_on_cover:
                continue
            rows = remarks(entry)
            if not rows:
                continue
            if page is None:
                page = new_page('remarks', title)
                y = page['content_top']
            heading = text_item(entry['display_name'], LEFT, y, WIDTH, 12, True, theme.NAVY)
            # Keep the sample name with at least its first line.
            if y + heading['h'] + 2 + text_item(rows[0][0], LEFT, 0, WIDTH, 10)['leading'] > BOTTOM:
                page = new_page('remarks', title + ' (계속)')
                y = heading['y'] = page['content_top']
            page['items'].append(heading)
            y += heading['h'] + 2
            for text, evidence in rows:
                page, y = place_text(page, y, text, 10, theme.TEXT, title, 'remarks')
                if evidence:
                    page, y = place_text(page, y - 1, evidence, 8.3, theme.NOTE, title, 'remarks')
                y += 2
            y += 5

    add_remark_pages()
    add_figure_pages()
    add_quantity_table_pages()
    classes = document['phase_classes']
    for entry in entries:
        sid = entry['sample_id']
        sample = sources[sid]
        images = {}
        for role, flag, caption_key in [
            ('phase_image', 'include_phase_image', 'phase_caption'),
            ('quantity_image', 'include_quantity_image', 'quantity_caption'),
        ]:
            if not entry[flag]:
                continue
            record = next((r for r in sample['sources'] if r['role'] == role), None)
            if not record:
                continue
            data = resolve_source(record['id'])
            if hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError(
                    f"{record['filename']}: 등록 후 원본 내용이 변경되었습니다. 자료를 다시 확인하세요."
                )
            try:
                with Image.open(io.BytesIO(data)) as image:
                    size = image.size
                    fmt = image.format
                    if fmt not in ('PNG', 'JPEG'):
                        raise ValueError('PNG/JPG 원본 이미지만 지원합니다.')
            except Exception as exc:
                raise ValueError(f"{record['filename']}: 원본 이미지를 읽지 못했습니다.") from exc
            images[role] = dict(
                data=data,
                mime='image/png' if fmt == 'PNG' else 'image/jpeg',
                size=size,
                record=record,
                caption=entry[caption_key] or record['filename'],
            )
        page = new_page('sample', entry['display_name'], sid)
        labels = [x for x in classes['labels'] if x['sample_id'] == sid]
        place_sample(page, entry, sample, labels, classes, images, source_links(sample))
    # Order of the deck: cover, the Multi Profile figure, the summary, remarks, tables, samples.
    order = {'cover': 0, 'figure': 1, 'summary': 2, 'remarks': 3, 'table': 4, 'sample': 5}
    pages.sort(key=lambda page: order[page['kind']])
    for number, page in enumerate(pages, 1):
        page['number'] = number
    footer_lines = wrap_lines(
        document['metadata'].get('report_number') or 'XRD 분석 결과',
        theme.FOOTER_TEXT_W,
        theme.FOOTER_FONT,
    )
    footer_y = theme.PAGE_H - theme.FOOTER_BOTTOM - len(footer_lines) * theme.FOOTER_LEADING
    for page in pages:
        page['total'] = len(pages)
        page['footer_lines'] = footer_lines
        for text, x, w, align in (
            ('\n'.join(footer_lines), LEFT, theme.FOOTER_TEXT_W, 'left'),
            (
                f"{page['number']} / {len(pages)}",
                theme.PAGE_W - LEFT - theme.FOOTER_COUNT_W,
                theme.FOOTER_COUNT_W,
                'right',
            ),
        ):
            item = text_item(
                text,
                x,
                footer_y,
                w,
                theme.FOOTER_FONT,
                False,
                theme.FOOTER,
                theme.FOOTER_LEADING,
                align,
            )
            item['role'] = 'footer'
            page['items'].append(item)
    return pages
