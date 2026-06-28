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

## Raman 피크 라이브러리

`/raman` 화면은 FT-IR 화면처럼 피크 assignment 라이브러리를 관리한다.

- 기본 라이브러리 ID: `general-raman`
- 기본 라이브러리 원본: `rin/raman/resources/func_groups.csv`
- PPTX 기반 번들 라이브러리:
  - `carbon-graphite-raman`: Carbon D/G/2D band
  - `lithium-compound-raman`: LiOH, Li2S, LPSCl, Li2CO3, Li2SO4
  - `lmr-layered-oxide-raman`: LMR Eg/A1g mode
- Edge 서버 저장 폴더: `edge_api_server/data/raman_assignment_libraries`
- 지원 포맷: JSON, CSV

라이브러리 항목은 `centerWavenumber`, `tolerance`, `name`, `color`, `note`
필드를 사용한다. 선택된 라이브러리의 `centerWavenumber ± tolerance` 범위에
검출 피크가 들어오면 그래프 피크 이름과 색상이 해당 라이브러리 기준으로
표시된다.

PPTX 기반 번들 라이브러리는 `rin/data/RAMAN 데이터 정리.pptx`에 숫자로
명시된 피크 범위만 반영한다. LMR peak deconvolution처럼 문서에 피크 위치가
명시되지 않은 항목은 별도 알고리즘/양식 확정 후 추가한다.

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
