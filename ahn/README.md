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
사용한다. TEM/STEM/EDS/코팅층 슬라이드는 템플릿 장표의 배경, 상단
`TEM 분석 결과` 영역, 파란 라인, 기본 서식을 복사하고 제목, 이미지, 배율,
표 데이터만 보고서 생성 시 교체한다.
STEM EDS 보고서는 Word 1페이지의 기준 이미지를 왼쪽에 크게 배치하고,
해당 Word 파일의 EDS 이미지가 모두 배치될 때까지 후속 장표에서도 같은
기준 이미지를 왼쪽에 유지한다. MAP 후속 이미지는 기준 이미지 오른쪽의
3x2 영역에 순차 배치한다. Line scan은 Data 블록 단위로 기준 이미지,
라인 이미지, 전체 그래프를 먼저 배치하고 이후 원소별 그래프를 2x3으로
배치한다. Point는 Word의 Spectrum 표를 `Wt%`, `At%` 순서의 별도 장표로
나누고 두 장 모두 같은 기준 이미지를 왼쪽에 크게 배치한다. 각 Spectrum
row마다 기준 이미지, 해당 row의 조성표, Spectrum 그래프를 별도 장표로
생성한다.

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
