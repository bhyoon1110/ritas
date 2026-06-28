# RIN

RIN은 Raman 장비 프로젝트 영역이다. Edge API 서버에는 FT-IR(`/ftir`)과
분리된 Raman 미리보기 화면(`/raman`)을 제공한다.

## Raman 웹 미리보기

Edge API 서버 실행 후 브라우저에서 다음 주소로 접속한다.

```text
http://127.0.0.1:8000/raman
```

개발용으로 별도 포트에서 띄울 때는 다음처럼 실행할 수 있다.

```bash
cd edge_api_server
.venvtest/bin/python -m uvicorn app.raman_web:create_raman_preview_app \
  --factory --host 127.0.0.1 --port 8012
```

현재 지원하는 raw 입력은 다음과 같다.

- `.txt`, `.csv`, `.tsv`: 주석(`#`)과 헤더를 건너뛰고 첫 숫자 2열을
  Raman shift / intensity로 사용
- `.xlsx`, `.xlsm`: `openpyxl` 설치 환경에서 첫 sheet의 첫 숫자 2열을 사용

분석 흐름은 다음과 같다.

```text
raw 파일 업로드
  -> 숫자 2열 추출
  -> 0~4000 cm⁻¹ 공통 grid 보간
  -> Savitzky-Golay smoothing
  -> ALS baseline 보정
  -> Min-Max 정규화
  -> 민감도 기반 피크 검출
  -> Plotly 그래프 표시
```

다른 Raman raw 포맷이 추가되면 `rin/raman/preprocess.py`의
`load_raman_raw()` 또는 내부 reader 함수를 확장하면 된다. 그래프와 피크
편집 기능은 `rist_common.plotting` 공통 모듈을 사용한다.

## Edge 연동

Edge API 서버에서 RIN 전처리 명령을 호출하려면 환경 변수로 프로젝트별
processor command를 지정한다.

```bash
export RIST_PROCESSOR_COMMAND_RIN='python -m rin.processor --input "{input_dir}" --output "{processed_dir}"'
```

명령 템플릿에는 Edge worker가 제공하는 `{input_dir}`, `{processed_dir}`,
`{job_id}`, `{experiment_code}` 같은 값을 사용할 수 있다.
