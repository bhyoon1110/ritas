# RIN

RIN 실험장비 프로젝트 영역이다.

현재 폴더는 신규 프로젝트 자리로 추가되었으며, 분석 로직과 실행 명령은
장비 인터페이스와 처리 방식이 확정되는 대로 이 문서에 정리한다.

## Edge 연동

Edge API 서버에서 RIN 전처리 명령을 호출하려면 환경 변수로 프로젝트별
processor command를 지정한다.

```bash
export RIST_PROCESSOR_COMMAND_RIN='python -m rin.processor --input "{input_dir}" --output "{processed_dir}"'
```

명령 템플릿에는 Edge worker가 제공하는 `{input_dir}`, `{processed_dir}`,
`{job_id}`, `{experiment_code}` 같은 값을 사용할 수 있다.
