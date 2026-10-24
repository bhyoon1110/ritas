"""Stable, author-only slide inclusion and added text pages; never mutate evidence."""
from collections import defaultdict
import hashlib
import json
import textwrap


def identify(payloads):
    counts = defaultdict(int)
    for payload in payloads:
        meta = payload.get("editing", {})
        # Titles/text are editable; preceding tables may grow. Neither can be
        # used as an absolute page number or a content hash for page identity.
        key = json.dumps([meta.get("kind"), meta.get("sectionId"), meta.get("figureId")])
        number = counts[key]
        counts[key] += 1
        payload["pageId"] = "generated-" + hashlib.sha256(key.encode()).hexdigest()[:20] + "-" + str(number)
    return payloads


def text_layout(body):
    """Bound a single added page without truncating text, including blank lines."""
    # 319pt body height; each paragraph has 125% line spacing + 5pt after.
    # Conservative CJK widths also keep unbroken strings inside the text box.
    for font, width, maximum in [(2000, 39, 10), (1800, 44, 11), (1600, 49, 12), (1400, 56, 13)]:
        lines = [part for line in body.split("\n") for part in (textwrap.wrap(line, width=width, break_on_hyphens=False) or [""])]
        if len(lines) <= maximum:
            return lines, font
    raise ValueError("추가 페이지의 내용이 한 장에 들어가지 않습니다. 내용을 줄이거나 페이지를 나누어 추가하세요.")


def compose(document, base):
    from . import renderers
    slides = list(identify(base))
    edits = document.page_edits or {}
    hidden = set(edits.get("hidden", []))
    for item in edits.get("added", []):
        payload = {"pageId": item["id"]}
        if item["id"] not in hidden:
            lines, font = text_layout(item["body"])
            payload.update(xml=renderers._pptx_added_slide(document, item["title"], lines, font),
                           rels=renderers._pptx_slide_rels(), media=[],
                           editing={"kind": "added", "pageId": item["id"]})
        anchor = next((i for i, page in enumerate(slides) if page["pageId"] == item["after"]), len(slides) - 1)
        slides.insert(anchor + 1, payload)
    order = edits.get("order") or []
    if order:
        by_id = {slide["pageId"]: slide for slide in slides}
        arranged = [by_id[page_id] for page_id in order if page_id in by_id]
        placed = {slide["pageId"] for slide in arranged}
        # New pagination follows its natural predecessor without resetting the
        # order of known pages. Missing/hidden stable IDs can later reappear.
        predecessor = None
        for slide in slides:
            if slide["pageId"] not in placed:
                index = next((i + 1 for i, p in enumerate(arranged) if p["pageId"] == predecessor), 0)
                arranged.insert(index, slide)
                placed.add(slide["pageId"])
            predecessor = slide["pageId"]
        slides = arranged
    # Keep hidden anchor pages until after insertion so additions retain their
    # position even when their predecessor is subsequently removed.
    slides = [slide for slide in slides if slide["pageId"] not in hidden]
    if not 1 <= len(slides) <= 200:
        raise ValueError("보고서는 최소 1페이지, 최대 200페이지를 유지해야 합니다.")
    return slides


def validate(document, previous):
    from . import renderers
    edits = document.page_edits or {}
    base = renderers._pptx_base_slide_payloads(document)
    current = {p["pageId"] for p in identify(base)}
    old = {p["pageId"] for p in identify(renderers._pptx_base_slide_payloads(previous))}
    allowed = current | old
    old_added = {p["id"]: p for p in (previous.page_edits or {}).get("added", [])}
    added_ids = set()
    for item in edits.get("added", []):
        retained_anchor = old_added.get(item["id"], {}).get("after") == item["after"]
        if item["id"] in added_ids or (item["after"] not in allowed and not retained_anchor):
            raise ValueError("추가 페이지의 위치 또는 식별자가 올바르지 않습니다. 미리보기를 다시 불러오세요.")
        added_ids.add(item["id"])
        allowed.add(item["id"])
        if item["id"] not in edits.get("hidden", []):
            text_layout(item["body"])
    hidden = edits.get("hidden", [])
    allowed.update((previous.page_edits or {}).get("hidden", []))
    if len(set(hidden)) != len(hidden) or set(hidden) - allowed:
        raise ValueError("삭제할 페이지가 현재 보고서에 없습니다. 미리보기를 다시 불러오세요.")
    order = edits.get("order") or []
    allowed.update((previous.page_edits or {}).get("order", []))
    if len(set(order)) != len(order) or set(order) - allowed:
        raise ValueError("페이지 순서에 중복되거나 알 수 없는 페이지가 있습니다. 미리보기를 다시 불러오세요.")
    compose(document, base)
