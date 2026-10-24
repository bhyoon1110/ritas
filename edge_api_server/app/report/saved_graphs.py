"""Bounded, ordered spectrum snapshots. Never trust browser filenames as paths."""
from copy import deepcopy
from dataclasses import replace
from io import BytesIO
import math
from pathlib import Path
import re

from .edited_analysis import edited_graph_spec, prepare_edited_report
from .builders import _clean_report_text
from .editable import ensure_editable_fields
from .model import LLM_SLOT_IDS, ReportFigure

MAX_GRAPHS = 6


def figure_filename(figure_id):
    if re.fullmatch(r"saved-graph-[1-6]", str(figure_id)):
        return str(figure_id) + ".png"
    return "current_graph.png"


def read_graphs(value):
    """Validate at the upload boundary, before enqueueing or allocating render jobs."""
    from ..preview_report import read_figure_image
    from PIL import Image

    if not isinstance(value, list) or not 1 <= len(value) <= MAX_GRAPHS:
        raise ValueError("보고서에는 저장한 그래프 1~6장을 선택하세요.")
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, dict) or not isinstance(item.get("analysis"), dict):
            raise ValueError("저장한 그래프의 분석 정보가 올바르지 않습니다.")
        figure = item.get("viewerFigure")
        if (not isinstance(figure, dict) or not isinstance(figure.get("data"), list)
                or not isinstance(figure.get("layout"), dict) or len(figure["data"]) > 10000):
            raise ValueError("저장한 그래프의 스펙트럼 정보가 올바르지 않습니다.")
        for trace in figure["data"]:
            if not isinstance(trace, dict):
                raise ValueError("저장한 그래프의 trace가 올바르지 않습니다.")
            if trace.get("meta") is not None and not isinstance(trace["meta"], dict):
                raise ValueError("저장한 그래프의 trace 메타정보가 올바르지 않습니다.")
            meta = trace.get("meta") or {}
            for key in ("rist_view_offset", "rist_raman_stack_offset", "rist_ftir_stack_offset_absorbance", "rist_ftir_stack_offset_transmittance"):
                offset = meta.get(key)
                if offset is not None and (isinstance(offset, bool) or not isinstance(offset, (int, float)) or not math.isfinite(offset)):
                    raise ValueError("저장한 그래프의 이동값을 확인하세요.")
            x, y = trace.get("x", []), trace.get("y", [])
            if not isinstance(x, list) or not isinstance(y, list) or len(x) != len(y):
                raise ValueError("저장한 그래프의 X/Y 좌표 수가 다릅니다.")
            if any(v is not None and (isinstance(v, bool) or not isinstance(v, (float, int)) or not math.isfinite(v)) for v in x + y):
                raise ValueError("저장한 그래프 좌표는 유효한 숫자여야 합니다.")
        for key in ("annotations", "shapes"):
            entries = figure["layout"].get(key) or []
            if not isinstance(entries, list) or any(not isinstance(entry, dict) for entry in entries):
                raise ValueError("저장한 그래프의 주석 또는 도형 정보가 올바르지 않습니다.")
        encoded = item.get("image")
        if not isinstance(encoded, str):
            raise ValueError("저장한 그래프 이미지가 없습니다.")
        image = read_figure_image(encoded)
        try:
            with Image.open(BytesIO(image)) as png:
                if png.format != "PNG" or png.width * png.height > 32 * 1024 * 1024:
                    raise ValueError("저장한 그래프 이미지는 32MP 이하 PNG여야 합니다.")
                png.verify()
        except Exception as exc:
            raise ValueError("저장한 그래프 PNG 이미지를 확인하세요.") from exc
        analysis = {key: val for key, val in item["analysis"].items()
                    if key not in {"reportGraphs", "viewerFigure", "figure"}}
        analysis["viewerFigure"] = figure
        analysis = prepare_edited_report(analysis)
        title = str(item.get("title") or f"그래프 {index + 1}").strip()
        if len(title) > 100:
            raise ValueError("그래프 이름은 100자 이하로 입력하세요.")
        result.append({"id": f"saved-graph-{index + 1}", "title": title,
                       "analysis": analysis, "viewerFigure": figure, "image": encoded})
    return result


def write_graph_images(graphs, report_dir, processed_dir):
    from ..preview_report import read_figure_image
    for item in graphs:
        data = read_figure_image(item["image"])
        name = figure_filename(item["id"])
        (report_dir / name).write_bytes(data)
        (processed_dir / name).write_bytes(data)
    # Old API paths/fallbacks keep pointing to the first selected graph.
    data = (report_dir / figure_filename(graphs[0]["id"])).read_bytes()
    (report_dir / "current_graph.png").write_bytes(data)
    (processed_dir / "current_graph.png").write_bytes(data)


def graph_raw_series(graphs):
    """Export displayed spectrum values minus visual stacking, not another analysis."""
    from ..preview_report import RawSeries
    result = []
    for graph in graphs:
        figure = graph["viewerFigure"]
        for trace in figure["data"]:
            meta = trace.get("meta") or {}
            if not meta.get("rist_sample_parent") or trace.get("visible") in (False, "legendonly"):
                continue
            offset = float(meta.get("rist_view_offset") or 0)
            if not math.isfinite(offset):
                raise ValueError("그래프 이동값을 확인하세요.")
            points = [(x, y - offset) for x, y in zip(trace.get("x", []), trace.get("y", []))
                      if x is not None and y is not None]
            result.append(RawSeries(f"{graph['title']} / {_clean_report_text(trace.get('name')) or '시료'}",
                                    [p[0] for p in points], [p[1] for p in points]))
    return result


def collection_document(builder, job, graphs, transfer, image_root):
    """Keep every graph's tables/evidence separate, with one cover and common prose."""
    documents, specs = [], []
    for graph in graphs:
        payload = graph["analysis"]
        items = [{"relativePath": graph["id"] + ".json", "data": payload}]
        document = builder.build(job, items)
        ensure_editable_fields(document, payload, job["experiment_code"], transfer)
        spec = builder.llm_slots(job, items)
        specs.append(edited_graph_spec(spec, payload, job["experiment_code"]) if spec else None)
        documents.append(document)
    document = documents[0]
    common_ids = {"report_transfer", "report_traceability", "sample_info", "experiment_conditions"}
    # Captions are graph-local, independent of the overall LLM caption slot.
    figures = []
    for index, (graph, part) in enumerate(zip(graphs, documents)):
        caption = deepcopy(part.section("caption"))
        caption_id = f"graph-{index + 1}-caption"
        if caption:
            caption.section_id = caption_id
            caption.heading = graph["title"] + " · 그래프 설명"
            document.sections.append(caption)
        figures.append(ReportFigure(graph["id"], graph["title"],
                                   str(Path(image_root) / figure_filename(graph["id"])), caption_id))
        for section in list(part.sections):
            if section.section_id in common_ids or section.section_id in LLM_SLOT_IDS or section.section_id.startswith("graph-"):
                continue
            section.heading = graph["title"] + " · " + section.heading
            if index:
                section.section_id = f"graph-{index + 1}-" + section.section_id
                document.sections.append(section)
    document.figures = figures
    for slot in LLM_SLOT_IDS:
        section = document.section(slot)
        if section:
            section.paragraphs = [f"[{g['title']}] {text}" for g, part in zip(graphs, documents)
                                  for text in (part.section(slot).paragraphs if part.section(slot) else [])]
    # Only genuinely identical conditions are common; different datasets get a scope.
    condition_tables = [part.section("experiment_conditions").table for part in documents]
    if any(table.rows != condition_tables[0].rows for table in condition_tables[1:]):
        condition_tables[0].rows = [[g["title"] + (" / " + row[0] if row[0] != "공통" else ""), *row[1:]]
                                    for g, table in zip(graphs, condition_tables) for row in table.rows]
    sample = document.section("sample_info")
    if sample:
        sample.bullets = [f"보고서 그래프: {g['title']}" for g in graphs]
    spec = _collection_spec(specs, graphs, document)
    return document, spec


def _collection_spec(specs, graphs, document):
    if not specs[0]:
        return None
    facts = deepcopy(specs[0].facts)
    facts.update(current_peaks=[], samples=[], experiment_conditions=[], graphs=[],
                 visible_peak_count=0, included_peak_count=0, omitted_peak_count=0, edited_peak_count=0,
                 omitted_samples=0, omitted_conditions=0,
                 graph_notes={"text_notes": [], "regions": [], "ratio_annotations": [], "ratio_measurements": [],
                              "omitted_notes": 0, "omitted_regions": 0, "omitted_ratios": 0})
    for index, (spec, graph) in enumerate(zip(specs, graphs)):
        if not spec:
            continue
        source = deepcopy(spec.facts)
        prefix = f"g{index + 1}:"
        facts["graphs"].append({"graph": graph["title"], "axis": source.get("axis"), "display_mode": source.get("display_mode")})
        for peak in source.get("current_peaks", []):
            peak["id"] = prefix + peak["id"]
            peak["sample"] = graph["title"] + " / " + peak.get("sample", "")
            facts["current_peaks"].append(peak)
        for sample in source.get("samples", []):
            sample["label"] = graph["title"] + " / " + sample.get("label", "")
            facts["samples"].append(sample)
        facts["experiment_conditions"].extend(source.get("experiment_conditions", []))
        for count in ("visible_peak_count", "included_peak_count", "omitted_peak_count", "edited_peak_count", "omitted_samples", "omitted_conditions"):
            facts[count] += source.get(count, 0)
        for key, value in source.get("graph_notes", {}).items():
            if isinstance(value, int):
                facts["graph_notes"][key] = facts["graph_notes"].get(key, 0) + value
                continue
            for entry in value:
                if isinstance(entry, dict):
                    entry["graph"] = graph["title"]
                    if "included_peak_ids" in entry:
                        entry["included_peak_ids"] = [prefix + pid for pid in entry["included_peak_ids"]]
                    if "sample" in entry:
                        entry["sample"] = graph["title"] + " / " + entry["sample"]
                    for role in ("numerator", "denominator"):
                        if isinstance(entry.get(role), dict) and "id" in entry[role]:
                            entry[role]["id"] = prefix + entry[role]["id"]
                else:
                    entry = "[" + graph["title"] + "] " + str(entry)
                facts["graph_notes"].setdefault(key, []).append(entry)
    fallback = dict(specs[0].fallback)
    # Context trimming keeps a prefix; interleave graphs so it does not drop all
    # evidence for the later saved graphs before trimming the earlier ones.
    groups = [[p for p in facts["current_peaks"] if p["id"].startswith(f"g{i + 1}:")] for i in range(len(graphs))]
    facts["current_peaks"] = [group[row] for row in range(max(map(len, groups), default=0))
                              for group in groups if row < len(group)]
    for slot in LLM_SLOT_IDS:
        section = document.section(slot)
        if section:
            fallback[slot] = "\n".join(section.paragraphs)
    fallback["email_subject"] = f"[RIST] {document.title} · 그래프 {len(graphs)}종 결과 안내"
    fallback["email_body"] = "안녕하십니까.\n분석 보고서를 안내드립니다.\n\n" + "\n\n".join(
        f"[{g['title']}]\n{spec.fallback.get('summary', '')}" for g, spec in zip(graphs, specs) if spec
    ) + "\n\n자세한 그래프와 분석 내용은 첨부 보고서를 확인해 주십시오. 해석은 후보 소견이며 추가 검토가 필요합니다."
    return replace(specs[0], facts=facts, fallback=fallback,
                   system_prompt=specs[0].system_prompt + "\n여러 저장 그래프의 최종 근거입니다. 그래프/시료 접두어로 구분하고 서로 다른 그래프의 피크나 비율을 혼합하지 마세요. 모든 그래프를 균형 있게 요약하세요.")
