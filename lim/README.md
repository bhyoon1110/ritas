# LIM

XRD raw 데이터와 ICDD Card PDF를 기반으로 보고서형 Plotly HTML을 생성하는
도구이다. 기본 출력은 `(AX) XRD Report 양식 포맷.pptx`의 구조를 따른다.

## XRD 실행

기존 스크립트 실행 방식:

```bash
cd lim
python xrd_plot.py "data/raw.txt" "data/ICDD Card" --origin -o result.html
```

패키지 진입점 실행 방식:

```bash
cd ..
python -m lim.xrd.cli "lim/data/raw.txt" "lim/data/ICDD Card" --origin -o result.html
```

기본 HTML은 다음 구역으로 구성된다.

- `[샘플명] Report`
- 그래프 영역: raw XRD 패턴과 ICDD Card 피크 overlay
- 그래프/상매칭 보조 이미지: 입력 bundle의 이미지 파일 표시
- 특이사항 / 자동 해석 초안: raw 피크, ICDD 후보상, 첨부 표/이미지 정보를
  기준으로 작성한 자동 해석 초안
- 피크 정보: 입력 Excel/CSV 표와 PDF 카드에서 추출한 피크 표
- 결정상(Phase) 정보: PDF/DB 카드 메타데이터와 Norm. I. 상위 피크

지원하는 보조 입력 파일은 다음과 같다.

- 표 파일: `.xlsx`, `.csv`, `.tsv`
- 이미지 파일: `.png`, `.jpg`, `.jpeg`, `.webp`, `.gif`

`--data-dir` 또는 raw 파일 주변 폴더에 있는 보조 파일은 자동으로 보고서에
포함된다. 직접 지정하려면 `--excel`, `--image`를 반복해서 사용할 수 있다.

```bash
python -m lim.xrd.cli "lim/data/raw.txt" "lim/data/ICDD Card" \
  --excel "lim/data/peaks.xlsx" \
  --image "lim/data/상매칭 이미지.png" \
  -o xrd-report.html
```

기존처럼 그래프와 하단 피크 표만 확인하려면 `--plot-only`를 사용한다.

```bash
python -m lim.xrd.cli "lim/data/raw.txt" "lim/data/ICDD Card" --plot-only -o plot.html
```

## XRD 웹 미리보기

Edge API 서버에는 `/xrd` 미리보기 화면이 있다. 브라우저에서 raw TXT,
ICDD Card PDF, Excel/CSV/TSV, 이미지 파일을 한 번에 업로드하면 서버가
확장자로 자동 분류하고 같은 XRD 렌더러로 보고서형 HTML을 생성해 화면에서
바로 확인할 수 있다. Chrome 계열 브라우저에서는 하나의 `XRD 번들 추가`
영역에 파일과 폴더를 함께 드래그하거나 bundle 폴더를 선택할 수 있다.
Edge API 통합 서버에서 실행하면 이 자동 해석 초안은 설정된 로컬 LLM을
우선 사용하고, LLM 호출 실패 시 규칙 기반 초안으로 대체된다.

```text
http://127.0.0.1:8010/xrd
```

로컬에서 XRD 화면만 띄울 때는 다음 명령을 사용할 수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.xrd_web:create_xrd_preview_app --factory --host 127.0.0.1 --port 8010
```

FT-IR, Raman, XRD 화면을 같은 `8000`번 포트에서 함께 띄우려면 통합 preview
앱을 사용한다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.preview_web:create_preview_app --factory --host 127.0.0.1 --port 8000
```

## Edge processor 연동

Edge worker가 자동 processor를 실행하게 하려면 `RIST_PROCESSOR_COMMAND_XRD`에
명령을 등록한다. 명령에는 `{job_root}`, `{input_dir}`, `{processed_dir}`,
`{report_dir}`, `{experiment_code}`, `{job_id}` placeholder를 사용할 수 있다.

```bash
export RIST_PROCESSOR_COMMAND_XRD='python -m lim.xrd.cli "{input_dir}/raw.txt" "{input_dir}/ICDD Card" -o "{report_dir}/xrd-report.html"'
```

현재 XRD CLI는 `report_dir`에 바로 전달 가능한 보고서형 HTML을 생성한다. 이후
Spring Boot 전송 ZIP에 포함하려면 Edge processor 명령의 `-o` 경로를
`{report_dir}` 아래로 지정한다.
