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
  report/*.xlsx          # EDS raw 파일, 분석하지 않고 패키지에 원본 포함
  scale/시편명/*.tif     # TEM 코팅층 두께 분석
```

`scale` 폴더에 하위 시편 폴더 없이 이미지가 바로 들어 있으면 단일 시편
`Scale`로 처리한다. 코팅층 두께 OCR은 tesseract가 설치되어 있으면 측정
라벨의 숫자 값을 추출하고, OpenCV가 있으면 흰색 라벨 박스를 먼저 찾아
인식률을 높인다. 한 이미지에 라벨이 여러 개 있으면 JSON의
`thickness_values_nm`에 모두 보존하고 PPT 표에도 각 라벨을 별도 행으로
표시한 뒤 전체 라벨 평균을 계산한다. OCR 환경이 없거나 값이 읽히지 않으면
`검토 필요`로 남기고, 후보값 과다/편차 과다 같은 경우는 검토 플래그를 남긴다.

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
