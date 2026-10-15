"""Literal named fields for analyst reports. No expression evaluation or HTML."""
from copy import deepcopy
from datetime import date
import re

from .editable import TRANSFER_FIELDS

TOKEN = re.compile(r"\{\{\s*([^{}\n]{1,100}?)\s*\}\}")


def resolve_changes(value):
    """Resolve a bounded dependency graph; retain the original in editor-state.json."""
    result = deepcopy(value)
    variables = result.get("variables") or []
    definitions = {label: result["transfer"][key] for key, label in TRANSFER_FIELDS.items()}
    completion = result.get("completionDate")
    if completion:
        try:
            if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", completion):
                raise ValueError(completion)
            date.fromisoformat(completion)
        except ValueError as exc:
            raise ValueError("완료일은 올바른 날짜(YYYY-MM-DD)로 입력하세요.") from exc
    definitions["완료일"] = completion or ""
    for section in result["sections"]:
        if section["sectionId"] == "experiment_conditions":
            for row in section.get("rows") or []:
                if len(row) == 3 and row[0] == "공통" and row[1].strip():
                    name = "공통." + row[1].strip()
                    if name in definitions:
                        raise ValueError("동일한 공통 조건 이름이 중복됩니다.")
                    definitions[name] = row[2]
    for item in variables:
        name = item["name"].strip()
        if not name or any(c in name for c in "{}\r\n") or name in definitions or name.startswith("공통."):
            raise ValueError("공통 항목의 이름은 비어 있거나 중복될 수 없습니다.")
        definitions[name] = item["value"] + (" " + item["unit"].strip() if item["value"] and item["unit"].strip() else "")
    cache = {}

    def expand(text, trail=()):
        def replacement(match):
            name = match[1].strip()
            if name not in definitions:
                raise ValueError("등록되지 않은 공통 항목이 있습니다: " + name)
            if name in trail or len(trail) >= 10:
                raise ValueError("공통 항목이 서로를 반복 참조합니다.")
            if name not in cache:
                cache[name] = expand(definitions[name], (*trail, name))
            return cache[name]
        expanded = TOKEN.sub(replacement, text)
        if len(expanded) > 4000:
            raise ValueError("공통 항목 반영 후 문단 또는 셀이 4,000자를 초과합니다.")
        return expanded

    # Validate unused definitions too, so broken cycles cannot be saved as defaults.
    for name in definitions:
        expand("{{" + name + "}}")
    result["title"] = expand(result["title"])
    if len(result["title"]) > 200:
        raise ValueError("공통 항목 반영 후 제목이 200자를 초과합니다.")
    for section in result["sections"]:
        section["heading"] = expand(section["heading"])
        if len(section["heading"]) > 200:
            raise ValueError("항목 제목은 200자 이하로 입력하세요.")
        for key in ("paragraphs", "bullets"):
            section[key] = [expand(text) for text in section[key]]
        if section["rows"] is not None:
            section["rows"] = [[expand(cell) for cell in row] for row in section["rows"]]
    result["auxiliary"] = {key: expand(text) for key, text in result["auxiliary"].items()}
    return result
