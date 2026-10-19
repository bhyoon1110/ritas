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

- `.txt`, `.csv`, `.tsv`: 주석(`#`)과 헤더를 읽고 숫자 열을
  Raman shift / intensity로 사용. 반복된 shift/intensity 쌍 또는 공통 shift와
  여러 intensity 열을 시료별로 구분하며 시료명·측정 조건을 보존한다.
- 텍스트 인코딩: UTF-8(선택적 BOM), CP949 및 BOM이 있는 UTF-16/UTF-32(LE/BE).
  CRLF·LF·CR 및 혼합 줄바꿈을 지원한다. UTF-16/UTF-32가 손상되거나 잘린 경우에는
  다른 인코딩으로 오인하지 않고 오류를 표시한다.
- `.xlsx`, `.xlsm`: `openpyxl` 설치 환경에서 첫 sheet의 첫 숫자 2열을 사용

텍스트 인코딩·줄바꿈 처리는 그래프 미리보기와 보고서의 RAW 데이터 추출에
동일하게 적용된다. 원본 파일을 별도로 변환할 필요는 없다.

분석 흐름은 다음과 같다.

```text
raw 파일 업로드
  -> 숫자 2열 추출
  -> 원본 X·Y 측정점 그대로 Plotly 표시 (정규화/보정 없음)

별도의 피크 검출용 복사본
  -> 0~4000 cm⁻¹ 공통 grid 보간
  -> Savitzky-Golay smoothing
  -> ALS baseline 보정
  -> Min-Max 정규화
  -> 민감도 기반 피크 검출
  -> 후보를 가장 가까운 원본 측정점에 표시 (FWHM은 분석 추정)
```

여러 시료의 기본 화면도 원본값 중첩이다. 필요할 때만 `스택`/`Y 이동`으로 수직 이동한다.
원본의 측정 간격·노이즈·기저선·강도를 유지하며, 측정 범위 밖으로 선을 연장하지 않는다.
기존 저장된 작업/보고서는 재분석해야 원본 표시 방식이 적용된다.

## Raman 피크 라이브러리

`/raman` 화면은 FT-IR 화면처럼 피크 assignment 라이브러리를 관리한다.

- 기본 라이브러리 ID: `general-raman`
- 기본 라이브러리 원본: `rin/raman/resources/func_groups.csv`
- PPTX/참고 marker 기반 번들 라이브러리:
  - `carbon-graphite-raman`: Carbon D/G/2D band와 disordered carbon 보조 밴드
  - `lithium-compound-raman`: LiOH, Li2S, LPSCl, Li2CO3, Li2SO4
  - `lmr-layered-oxide-raman`: LMR Eg/A1g mode와 M-phase/spinel marker
- Edge 서버 저장 폴더: `edge_api_server/data/raman_assignment_libraries`
- 지원 포맷: JSON, CSV

측정 raw 파일은 상단 **Raw 파일 선택** 또는 드래그 앤 드롭으로 올린다.
**피크 라이브러리 가져오기**에서 TXT/TSV/Excel을 선택해도 raw 분석으로
연결되며, 이 파일을 assignment 라이브러리로 저장하지는 않는다.
CSV raw는 **Raw 파일 선택**을, CSV assignment는 **피크 라이브러리 가져오기**를 사용한다.

라이브러리 항목은 `centerWavenumber`, `tolerance`, `name`, `color`, `note`
필드를 사용한다. 선택된 라이브러리의 `centerWavenumber ± tolerance` 범위에
검출 피크가 들어오면 그래프 피크 이름과 색상이 해당 라이브러리 기준으로
표시된다.

PPTX 기반 항목은 `rin/data/RAMAN 데이터 정리.pptx`에 숫자로 명시된 피크
범위를 우선 반영한다. PPTX 밖의 참고 marker는 note에 `Reference marker
outside PPTX`로 표시한다. LMR peak deconvolution처럼 문서에 피크 위치가
명시되지 않은 정량 항목은 별도 알고리즘/양식 확정 후 추가한다.

다른 Raman raw 포맷이 추가되면 `rin/raman/preprocess.py`의
`load_raman_raw()` 또는 내부 reader 함수를 확장하면 된다. 그래프와 피크
편집 기능은 `rist_common.plotting` 공통 모듈을 사용한다.

## 그래프 조작과 비율 라벨

- 그래프 내부 마우스 휠은 편집 모드에서도 **Y축만** 확대·축소한다.
  영역 드래그 확대는 X·Y축 모두에 적용된다.
- **그래프 도구 → 축 범위 및 눈금 설정**의 **X축 고정 / Y축 고정**을
  체크하고 적용하면 해당 축의 마우스 확대·이동·Crop을 제한한다.
  고정한 축도 From/To 숫자를 직접 입력하여 범위를 변경할 수 있다.
- **비율**에서 같은 샘플의 분자·분모 피크를 순서대로 선택한다.
  두 피크를 연결하는 점선 없이 계산 라벨만 표시한다. 계산에는 스택 이동량을
  제외한 원본 피크 강도를 사용하며, 분모 0은 허용하지 않는다.
- **비율 관리**에서 샘플별 항목을 선택해 분자·분모 및 라벨 제목을 수정하거나
  **이 비율 삭제**로 선택한 항목만 삭제한다. 다른 샘플의 라벨은 유지된다.
  비율 라벨 더블클릭으로도 편집창을 열 수 있다.
- 범례(샘플명·피크명)는 더블클릭하여 이름을 직접 수정한다. 샘플 범례의 단일 클릭은
  샘플 표시/숨김, 더블클릭은 이름 편집으로 구분된다.
- 비율 추가·수정·삭제는 실행취소/다시 실행을 지원한다. 비율과 축 고정 상태는
  브라우저 작업 저장/복원에 포함된다. 새 raw 분석은 새 그래프이므로 기존 비율을
  이월하지 않으며, 참조하던 피크를 삭제하면 해당 비율도 제거된다.

Raman 상호작용 회귀 테스트: `edge_api_server/tests/raman_interactions.test.cjs`.

### ID/IG 구간 일괄 계산

- 그래프 도구의 **ID/IG 일괄 계산** → A(D/분자) 구간 드래그 → B(G/분모) 구간 드래그 → 시료별 검토 → **선택 결과 적용** 순서로 사용한다. 구간은 숫자로도 입력할 수 있다.
- 표시 중인 Raw 시료 각각에서 두 구간의 원본 강도 최고점을 찾는다. 피크 민감도가 0이어도 동작하며, 스택/Y 이동·정규화·평활화·베이스라인 보정은 계산에 포함하지 않는다. 면적비가 아닌 최고 강도비다.
- 결과표에 D/G 위치(cm⁻¹), 실제 강도, ID/IG를 표시한다. 구간 내 데이터가 없거나 2점 미만, 분모가 0 또는 절댓값 1e-12 미만이면 적용할 수 없다. 구간은 서로 겹칠 수 없다.
- 경계 최고점·동률 최고점·음의 강도는 검토 경고를 표시한다. 동률이면 가장 낮은 X를 선택한다. 시료별 체크를 해제해 제외할 수 있다. 행 호버/클릭으로 ① 파란 원·② 주황 사각형을 확인하며, 창을 접으면 그래프를 가리지 않고 확인할 수 있다.
- 기존 검출/수동 피크 목록은 변경하지 않는다. 같은 시료에 재적용하면 해당 ID/IG 일괄 결과만 갱신하고 수동 비율은 보존한다. 비율 관리에서 제목 수정·개별 삭제가 가능하며 일괄 적용도 한 번에 실행취소/다시실행할 수 있다.
- 피크 민감도 변경/피크 삭제와 무관하게 구간 측정 결과를 유지한다. Raw 시료 삭제 시 해당 결과도 제거된다. 새 Raw 분석에는 이전 결과를 이월하지 않는다.
- 보고서 비율표와 LLM 근거에 구간, 위치, 원본 강도, 비율 및 검토 경고가 반영된다. 작업 템플릿은 기존 정책대로 측정 결과를 포함하지 않는다.

구간 단위/호환성 테스트: `edge_api_server/browser_compat/tests/raman_ratio.test.cjs`.
실제 Plotly 브라우저 검사: `edge_api_server/tests/raman_ratio.browser.cjs` (Windows/Supermium 실기 검증과 별도).
이 테스트와 ES5 번들 검증은 실제 XP/Supermium 바이너리 실기 검증과는 별개다.

## Edge 연동

Edge API 서버에서 RIN 전처리 명령을 호출하려면 환경 변수로 프로젝트별
processor command를 지정한다.

```bash
export RIST_PROCESSOR_COMMAND_RIN='python -m rin.processor --input "{input_dir}" --output "{processed_dir}"'
```

명령 템플릿에는 Edge worker가 제공하는 `{input_dir}`, `{processed_dir}`,
`{job_id}`, `{experiment_code}` 같은 값을 사용할 수 있다.
