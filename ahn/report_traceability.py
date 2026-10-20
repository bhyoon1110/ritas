"""Shared, factual report-cover vocabulary. Never infer measurement/review facts."""
import unicodedata
import math

FIELDS = (
    ("client", "의뢰기관"), ("requester", "의뢰자"), ("receivedDate", "접수일"),
    ("purpose", "분석 목적"), ("analysisPeriod", "분석 기간"), ("equipmentName", "장비명 / 모델"),
    ("measuredDate", "측정일 / 기간"), ("preparation", "시료 전처리"),
    ("environment", "측정 환경"), ("analyst", "분석 담당자"), ("reviewer", "검토자"),
)
LABELS = dict(FIELDS, requestNumber="의뢰번호", equipmentCode="장비 코드",
    operatorId="실험자 / 전송 ID", completionDate="완료일", sampleNames="시료",
    sourceSummary="원자료", conditions="핵심 측정 조건", recordId="작업 / 프로젝트 ID",
    revision="보고서 이력", experimentCode="실험 코드")


def factual(value):
    text = str(value or "").strip()
    return "" if text in {"-", "미기재", "(미기재)", "WEB-PREVIEW", "SSO-PENDING"} else text


def brief(values, limit=130):
    """Explicit summary only; canonical full values stay in the report/source record."""
    items = list(dict.fromkeys(factual(v) for v in values if factual(v)))
    result = ""
    for index, item in enumerate(items):
        candidate = (result + " · " if result else "") + item
        if len(candidate) > limit:
            if not result:
                result = item[:limit] + "…"
                index += 1
            return result + (f" 외 {len(items)-index}건" if index < len(items) else " (상세 참조)")
        result = candidate
    return result


def cover_shapes(experiment, title, values, width=12192000, height=6858000):
    """Six native text panels; stable names also identify preview edit targets.

    Source and condition lists are summaries, not substitutions for the full body.
    No processing timestamp is relabelled as a measurement date or review approval.
    """
    specs = []
    def add(name, x, y, w, h, text, size=1100, color="243B53", bold=False, fill=None, line=None):
        specs.append(dict(shape_id=len(specs)+2, name=name, x=x, y=y, cx=w, cy=h,
            text=[text], font_size=size, color=color, bold=bold, fill=fill, line=line,
            inset=0, line_spacing_pct=108000, space_after_pts=0))
    add("Experiment banner title",254000,160020,8500000,400050,experiment+" 분석 결과",2000,"172B4D",True)
    add("Report eyebrow",600000,780000,10500000,240000,"분석 이력 요약  |  의뢰 → 시료 → 측정 → 분석·검토 → 완료",1150,"137B8D",True)
    add("Report title",600000,1080000,10900000,610000,title,2700,"172B4D",True)
    groups = (
        ("01  의뢰 정보", ("requestNumber", "client", "requester", "receivedDate", "purpose")),
        ("02  시료 · 원자료", ("sampleNames", "sourceSummary", "experimentCode")),
        ("03  측정 이력", ("equipmentName", "equipmentCode", "operatorId", "measuredDate")),
        ("04  실험 조건 · 환경", ("conditions", "preparation", "environment")),
        ("05  분석 · 검토", ("analyst", "reviewer", "analysisPeriod", "completionDate")),
        ("06  기록 연결", ("recordId", "revision")),
    )
    for index, (heading, keys) in enumerate(groups):
        x, y, w, h = 600000 + index % 2 * 5600000, 1840000 + index // 2 * 1450000, 5390000, 1320000
        add(f"Trace panel {index}",x,y,w,h,"",fill="F5F9FB",line="D9E5EA")
        add(f"Trace heading {index}",x+150000,y+90000,w-300000,230000,heading,1300,"137B8D",True)
        texts = [LABELS[key]+": "+(factual(values.get(key)) or "미기재") for key in keys]
        weights = [max(1,math.ceil(sum(1.1 if unicodedata.east_asian_width(c) in "WF" else .65
            for c in text)/((w-300000)/(127*1100)))) for text in texts]
        top = y+350000
        for row, key in enumerate(keys):
            base = 120000
            extra = sum(weight-1 for weight in weights)
            row_h = base + ((920000-base*len(keys))*(weights[row]-1)//extra if extra else
                             (920000-base*len(keys))//len(keys))
            add("Trace " + key,x+150000,top,w-300000,row_h,texts[row],1100)
            top += row_h
    add("Trace notice",600000,6200000,10900000,260000,
        "미기재 항목은 확인 후 입력하세요. 검토자 기재는 전자서명을 의미하지 않습니다. 상세 조건·원자료는 본문과 작업 기록을 참조하세요.",850,"627D98")
    scale = min(width/12192000,height/6858000)
    for spec in specs:
        for k in ("x", "cx"): spec[k] = round(spec[k]*width/12192000)
        for k in ("y", "cy"): spec[k] = round(spec[k]*height/6858000)
        spec["font_size"] = round(spec["font_size"]*scale)
        # Conservative native-text fit, also used by TEM's direct-edit revisions.
        spec["font_size"] = fitted_size(spec["text"][0],spec["cx"],spec["cy"],spec["font_size"])
    return specs


def fitted_size(text, width, height, size):
    def units(line):
        return sum(1.1 if unicodedata.east_asian_width(c) in "WF" else .65 for c in line)
    while size > 650:
        chars = max(1,width/(127*size))
        lines = sum(max(1,math.ceil(units(line)/chars)) for line in text.split("\n"))
        if lines*size*127*1.15 <= height:
            break
        size -= 25
    return size
