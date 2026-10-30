# AHN

AHN 프로젝트는 TEM/STEM 이미지, STEM EDS Word 보고서, TEM 코팅층 두께 이미지를
하나의 입력 번들로 받아 구조화 JSON과 PowerPoint 보고서 초안을 만든다.

## 입력 폴더 규칙

폴더명은 대소문자를 구분하지 않는다.

```text
input/
  tem/시편명/*.tif       # TEM 이미지 분석
  stem/*.tif             # STEM / STEM BF 이미지 분석
  report/*.docx          # STEM EDS MAP/Line/Point Word 보고서
  reports/*.docx         # report와 동일하게 인식되는 별칭 폴더
  report/*.xlsx          # EDS raw 파일, 패키지에 원본 포함. Point 표 fallback에 활용 가능
  reports/*.xlsx         # report와 동일하게 인식되는 별칭 폴더
  scale/시편명/*.tif     # TEM 코팅층 두께 분석
```

`scale` 폴더에 하위 시편 폴더 없이 이미지가 바로 들어 있으면 단일 시편
`Scale`로 처리한다. 코팅층 두께 OCR은 RapidOCR(ONNX)로 이미지 전체에서
측정 라벨과 위치를 먼저 찾고, OpenCV와 Tesseract의 흰색 라벨 박스 인식을
독립적인 보조 경로로 사용한다. 여러 전처리 결과를 교차 확인하며, 이미지
왼쪽 아래의 현미경 배율 눈금은 위치 정보로 제외한다. 한 이미지에 라벨이
여러 개 있으면 JSON의
`thickness_values_nm`에 모두 보존하고 PPT 표에도 각 라벨을 별도 행으로
표시한 뒤 전체 라벨 평균을 계산한다. 코팅층 이미지가 10장 이상이면 이미지는
전용 이미지 장표에 먼저 배치하고, 마지막 장표에 `측정개소/두께(nm)` 표를
분리해 넣는다. OCR 환경이 없거나 값이 읽히지 않으면
`검토 필요`로 남기고, 후보값 과다/편차 과다 같은 경우는 검토 플래그를 남긴다.

Edge 서버에는 Tesseract 바이너리와 함께 `edge_api_server/requirements.txt`의
`rapidocr`, `onnxruntime`, `opencv-python`, `pytesseract`가 설치되어 있어야 한다.
RapidOCR 모델은 Python 패키지에 포함된 로컬 ONNX 파일을 사용하므로 보고서
생성 시 외부 모델 서버나 인터넷 연결이 필요하지 않다.

## Processor 계약

Edge worker는 보고서 생성 전에 다음 순서로 동작한다.

1. `{jobRoot}/processed/*.json`이 이미 있으면 해당 JSON을 보고서 입력으로 사용한다.
2. JSON이 없고 `RIST_PROCESSOR_COMMAND_<EXPERIMENT>`가 설정되어 있으면 그 명령을 실행한다.
3. 실행 후에도 JSON이 없으면 작업을 실패 처리한다.

실험 코드 `AHN`의 예시는 다음과 같다. Edge 기본 보고서 파이프라인에서는
`analysis-result.json`을 사용하고, AHN 전용 PPT 초안은 `--pptx`를 지정해 별도
생성할 수 있다.

```bash
export RIST_PROCESSOR_COMMAND_AHN='python -m ahn.processor --input "{input_dir}" --output "{processed_dir}"'
```

명령 템플릿에서 사용할 수 있는 placeholder:

```text
{job_root}
{input_dir}
{processed_dir}
{report_dir}
{experiment_code}
{job_id}
```

## 산출물

새 TEM PPT에는 FTIR/Raman과 같은 POSCO 배경·RIST 로고·제목·정보 카드 형식의
표지를 첫 장에 추가합니다. 기존 TEM/STEM/EDS/코팅층 본문은 그대로 이어집니다.
Edge 보고서 미리보기에서 표지를 클릭하여 제목, 의뢰번호, 실험코드, 실험장비,
실험자, 완료일을 수정할 수 있으며, 저장한 PPT/PDF/전송 ZIP에도 반영됩니다.
표지가 없는 과거 보고서는 그대로 열리고, 표지 추가는 새 보고서 생성부터 적용됩니다.

```text
{jobRoot}/processed/analysis-result.json
{jobRoot}/processed/manifest.json
{jobRoot}/processed/raw/*.xlsx
```

PPT까지 생성하는 로컬 테스트 예시:

```bash
python -m ahn.processor \
  --input ahn/data/TESTData \
  --output /tmp/ahn-test \
  --pptx /tmp/ahn-test/ahn-report.pptx
```

PPT 보고서는 `ahn/resources/templates/ahn_tem_template.pptx`를 템플릿으로
사용한다. 배경과 좌측 하단 RIST 로고는 XRD 원본 양식의 자산으로 통일하며,
TEM/STEM/EDS/코팅층 슬라이드는 템플릿 장표의 상단
`TEM 분석 결과` 영역, 파란 라인, 기본 서식을 복사하고 제목, 이미지, 배율,
표 데이터만 보고서 생성 시 교체한다.
STEM EDS MAP은 Word 한 파일 안의 데이터 묶음을 `Electron Image1·2·3…` 기준으로 나눈다.
각 묶음의 전자영상을 왼쪽에 크게 두고, 해당 원소맵을 오른쪽 2열 × 3행에 순서대로 배치한다.
6개를 넘으면 같은 묶음의 전자영상과 제목을 유지하여 다음 장에 이어 쓰며, 다음 묶음의
전자영상이나 원소맵이 이전 묶음에 섞이지 않는다. 제목은 `파일명_Electron Image번호`로 표시한다.
Word 본문 제목·그림 설명과 표시 크기를 읽으며, 그림에 포함된 제목은 후보 전자영상의
상단 12%만 기존 Tesseract(영문, 이미지당 최대 2초)로 확인한다. OCR이 없거나 판독에
실패해도 첫 기준 영상과 크기가 비슷한 큰 영상 사이에 작은 맵들이 반복되면 묶음을 나눈다.
이 경우 번호는 문서 순서로 붙인다. 크기 비교는 Word의 표시 크기를 우선 사용하고,
크기 정보가 없는 구형 DOCX는 원본 이미지 크기를 사용한다. 제목이나 크기 차이가 없는
모호한 문서는 임의로 분할하지 않고 기존 한 묶음 배치를 유지한다.
수정 전 생성한 PPT/ZIP은 바뀌지 않으므로 원본 DOCX로 보고서를 다시 생성해야 한다.
Line scan은 Data 블록 단위로 기준 이미지,
라인 이미지, 전체 그래프를 먼저 배치하고 이후 원소별 그래프를 2x3으로
배치한다. Point는 Word의 Spectrum 표를 `Wt%`, `At%` 순서의 별도 장표로
나누고 두 장 모두 같은 기준 이미지를 왼쪽에 크게 배치한다. 각 Spectrum
row마다 기준 이미지, 해당 row의 조성표, Spectrum 그래프를 별도 장표로
생성한다.

DOCX 추출은 일반/Strict Open XML의 DrawingML 이미지와 표, 구형 VML의
`r:id`/`o:relid` 이미지 참조를 지원한다. URI 인코딩된 내장 이미지 경로도 해석한다.
이미지가 `word/media/`가 아닌 패키지 루트의 `/media/`에 있는 장비 DOCX도
본문 관계 정보를 따라 원래 순서대로 추출한다. 같은 이름의 로고·미사용 그림과 혼동하지 않는다.
외부 이미지 링크는 서버에서 가져오지 않고, 본문에 연결되지 않은 미디어를 임의로
결과 이미지로 쓰지 않는다. 추출할 그림/표가 없으면 보고서 생성을 중단하며
미디어·본문 참조 개수와 altChunk/누락 참조 등의 진단을 오류 기록에 남긴다.
이 경우 원본 DOCX 또는 운영 관리에서 보관된 실패 파일로 원인을 확인해야 한다.

예시 JSON:

```json
{
  "experiment": "AHN-TEM",
  "tem_samples": [],
  "stem_samples": [],
  "eds_reports": [],
  "coating_samples": [],
  "summary": {}
}
```
