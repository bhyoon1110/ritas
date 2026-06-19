# AHN

AHN 실험/분석 코드를 넣기 위한 스캐폴딩이다. Edge worker와 연동하려면 분석
processor가 작업 폴더의 입력을 읽고 `processed` 폴더에 구조화 JSON을 생성하면
된다.

## Processor 계약

Edge worker는 보고서 생성 전에 다음 순서로 동작한다.

1. `{jobRoot}/processed/*.json`이 이미 있으면 해당 JSON을 보고서 입력으로 사용한다.
2. JSON이 없고 `RIST_PROCESSOR_COMMAND_<EXPERIMENT>`가 설정되어 있으면 그 명령을 실행한다.
3. 실행 후에도 JSON이 없으면 작업을 실패 처리한다.

실험 코드 `AHN`의 예시는 다음과 같다.

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

## 최소 산출물

```text
{jobRoot}/processed/analysis-result.json
```

예시 JSON:

```json
{
  "sample": "sample-001",
  "finding": "주요 관찰 결과",
  "metrics": {
    "score": 0.95
  }
}
```
