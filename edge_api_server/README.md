# RIST Edge API Server

`EXPERIMENT_PC_EDGE_API.md`를 구현한 실험 PC - Edge 서버 간 FastAPI
프로젝트이다.

## 제공 기능

- 복합 PK 기반 작업 등록 및 UUID `jobId` 발급
- timestamp와 PK 기반 작업 폴더 생성
- multipart 파일 업로드
- 검증 전 업로드 파일 교체·삭제·목록 조회
- 파일 크기 및 SHA-256 검증
- 전체 bundle 검증
- 의뢰 번호별 장비 작업 집계 조회
- 보고서 생성 요청을 디스크 큐에 적재
- OpenAI 호환 로컬 LLM(`/v1/chat/completions`) 호출 worker
- 로컬 LLM 모델 조회 및 설정 모델 검증(`/v1/models`)
- LLM 입력 크기 제한과 vLLM 오류 변환
- 전처리 JSON 및 선택적 분석 이미지 입력
- C# raw bundle을 기존 XRD HTML/TEM PPTX 생성기에 직접 연결하는 내장 adapter
- 작업 상태 조회
- MariaDB 기반 작업, 파일, 멱등 요청 저장
- 업로드 유효기간 만료 처리
- `manifest.json` 생성 및 갱신
- DPT 다중 업로드·드래그 앤 드롭 FT-IR 웹 분석
- 업로드 바이트 기반 전처리·피크 분석과 Plotly Figure JSON 응답
- TEM/STEM/EDS/코팅층 raw 폴더 업로드와 PowerPoint 보고서 생성
- 실험별 VOC 게시판, 관리자 조치 및 작성자 확인 이력

보고서 생성 API는 요청을 작업 폴더의 `queue` 영역에 기록한다. 별도 worker는
C# XRD/TEM raw bundle이면 웹 화면과 같은 XRD HTML 또는 TEM PPTX 생성기를 직접
사용하고, 구조화 JSON이 있는 다른 작업은 공통 규칙 기반 보고서와 로컬 LLM
자유서술 보강 경로를 사용한다. 결과를 `report-package.zip`으로 패키징하며,
`includeRawFiles=true`이면 원본 bundle도 함께 넣는다. 생성된 ZIP은 공유 저장소와
`report_runs`에 먼저 등록되고, 사용자가 웹에서 검토하고 SSO 전송을 승인한 뒤에만
`report_transfers(PENDING)`가 생긴다. 공유 저장소 및 DB 전송 큐 계약은 루트의
`EDGE_SPRING_BOOT_API.md`를 따른다.

### FTIR/Raman PPT 기본 양식

FTIR/Raman **PPT 보고서 배경**은 `ahn/resources/templates/ahn_tem_template.pptx`의
포스코 배경을 공통 렌더러에서 재사용한다. 템플릿 원본을 배포에 포함해야 하며,
샘플 이미지·TEM 제목·고정 페이지 번호는 가져오지 않는다. 기존 16:9 보고서의 내용과
PPT에서 변환하는 PDF/페이지 미리보기를 유지한다. 별도 설정·DB 변경은 없으며
새 보고서 생성 또는 보고서 수정 저장 시 적용된다. 아래의 작업 설정 JSON과는 별개이다.

### FTIR/Raman 작업 템플릿

`/ftir`와 `/raman`의 **템플릿 저장 / 템플릿 불러오기**는 분석 데이터와 분리된
버전 1 JSON 설정 파일(`rist-spectrum-work-template`)을 사용한다. 공통 구현은
`app/work_template.js`이며 호환 빌드에서 Chrome 49용 ES5 프로그램도 생성한다.
새 API·DB 테이블·서버 저장 경로는 필요하지 않다.

표시 서식·크기·스택·민감도·라이브러리 ID·공통 실험조건을 재사용하며, 축 범위와 제목은
저장 시 선택한다. RAW/시료명/수동 피크·도형·비율/의뢰정보/개별 조건·AI 문안은 포함하지 않는다.
Raman 세부조건 원문은 RAW별 헤더/시료정보를 포함하므로 템플릿에서 제외한다.
새 RAW의 기존 분석 API로 피크를 재계산하고 표시용 스택은 새 측정값 범위로 재설정한다.
같은 실험 종류만 허용하고 파일 크기(256KB), 버전, 허용된 설정/값, 라이브러리 존재를 확인한다.
분석 실패 시 이전 편집을 복원하며, 성공하면 이전 보고서 다운로드/전송 연결을 해제하므로
새 보고서를 생성해야 한다. 선택한 라이브러리는 현재 서버 내용으로 사용하므로 다른 서버로
옮길 때는 동일 ID/내용의 라이브러리도 준비한다. 상세 사용법은 루트 README를 참고한다.

### 웹 의뢰번호 조회 및 직접 입력

- `/ftir`, `/raman`, `/xrd`, `/tem`의 **보고서 전송 정보**에서 `의뢰 조회`로 항목을
  선택하거나 `의뢰번호`와 `실험코드`를 직접 입력할 수 있다. 조회 실패·빈 목록도 입력을 막지 않는다.
- 직접 입력 시 실험코드는 `FTIR`/`TEM` 같은 화면 이름이 아니라 **LIMS에 등록된 실제 코드**를
  사용한다. 입력값의 앞뒤 공백은 제거하며 전송 필수값 검사는 유지한다. 수동 입력만으로
  해당 의뢰번호·실험코드가 LIMS에 존재한다고 검증되지는 않는다.
- 조회한 의뢰번호나 실험코드를 수정하면 선택 연결을 해제하여 이전 시료·결과번호가 섞이지
  않게 한다. 의뢰번호만 바꾸면 이전에 자동 입력한 실험코드도 비우므로 새 코드 확인 후 입력한다.
  의뢰 목록을 다시 조회하거나 빈 항목을 선택하는 것만으로 수동 입력값을 지우지 않는다.
- 전송에는 완료된 보고서, 의뢰번호·실험코드·실험장비·실험자 정보가 필요하다. 운영 환경의
  프로젝트 접근권한, `REPORT_SENDER`, 연결된 SSO 및 최근 인증 요건은 변경하지 않는다.
  실제 전송 작업자는 서버가 SSO 사용자 정보로 확정한다. DB 테이블 변경은 없다.

### Raw 그래프 표시와 피크 분석 분리

FTIR/Raman 웹 그래프는 유효한 원본 X·Y 측정점 전체를 X 오름차순(동일 X는 입력 순서)
으로 유지하여 직선으로 연결한다. 표시선에 보간·평활화·베이스라인 보정·정규화·점 생략을
적용하지 않는다. 피크 검출에는 종전 분석용 grid와 전처리를 별도로 사용하고, 검출 후보를
가장 가까운 실제 측정점에 표시한다. 같은 측정점으로 겹친 후보는 합치며 FWHM은 분석 추정값이다.
표준 피크 분석 범위는 FTIR 400~4000, Raman 0~4000 cm⁻¹이지만 그래프에는 그 밖의
측정점도 표시한다. 분석 API의 `settings.displayMode=raw`, `displayPreprocessing=false`,
`analysisPreprocessing`으로 구분할 수 있다.

여러 시료도 기본 스택은 꺼져 있다. 스택/Y 이동을 명시적으로 사용하면 표시 위치만 이동한다.
FTIR 투과도 전환은 사용자가 선택한 흡광도→투과도 변환이며, Origin 스타일은 서식만 바꾼다.
Raman 비율은 새 분석 결과의 원본 피크 강도(스택 오프셋 제외)를 기준으로 계산한다.
기존 작업 복원·이미지·HTML·보고서는 소급 변경되지 않으므로 원본 표시를 쓰려면 재분석한다.
XRD 측정선은 원래 Raw 값이며 ICDD 비교용 막대만 측정 최대 강도에 맞춰 배율 조정한다.
TEM은 첨부 이미지/Word 그래프 배치와 별도 OCR·표 처리로, 이 스펙트럼 전처리 경로를 쓰지 않는다.
CLI의 명시적인 전처리 비교/라이브러리 매칭용 정규화 그래프는 계속 분석용으로 제공된다.

### FTIR/Raman Raw 순서 변경

스택 OFF에서는 ‘범례·스타일’ 창에서 Raw 이름 왼쪽의 `↕` 손잡이를 위아래로 드래그하면 순서를 바꿀 수 있다.
파란 삽입선으로 이동 위치를 확인하며, 손잡이에 초점을 둔 뒤 키보드 `↑`/`↓`로도 이동한다.
그래프 안에 표시된 범례에서도 **Raw 이름 자체를 드래그**해 순서를 바꿀 수 있다.
범례 상단의 ‘범례 이동’ 바는 창 전체 위치 이동용이다. 항목의 단일 클릭은 표시·숨김,
더블클릭은 이름 수정으로 유지되며, 드래그는 이 클릭 동작을 실행하지 않는다.
스택 ON에서는 두 범례의 Raw 순서 드래그를 막고 ‘Y 이동’으로만 순서를 변경한다.
시료와 연결된 피크·라벨이 함께 이동하며 범례가 그래프의 위→아래 순서를 자동으로 따른다.
스택이 꺼져 있으면 범례 순서만 변경하며 원본 X·Y 측정값과 피크/비율 연결은 유지한다.
마지막 순서는 스택 전환·간격 조절(0 포함)·시료 숨김/표시 및 그래프 저장/복원에 유지된다.
범례 순서 변경과 Y 이동은 실행취소/다시실행할 수 있다.
드래그 도중 포커스 이탈·취소 또는 창 밖에 놓으면 순서는 바뀌지 않는다.
창에서 아직 저장하지 않은 이름·선 스타일 입력은 순서 변경 시 그대로 유지한다.
‘전체 저장’은 입력 검증을 통과하면 변경을 적용하고 창을 닫는다. 글자 크기·선 굵기 등
입력 오류가 있으면 창과 입력값을 유지하며, 바깥 클릭이나 Esc만으로는 닫히지 않는다.
범례·스타일을 열면 그래프 도구창은 잠시 접고, 닫기/전체 저장 후 이전 도구창으로 돌아간다.
편집 중 도구 버튼을 누르면 입력값을 유지한 채 범례·스타일 창으로 초점을 옮긴다.
넓은 화면의 상시 도구 모음도 편집 중에는 숨기며, 복귀 시 그래프 편집 모드를 자동으로 켜지 않는다.

### FTIR/Raman 새 작업 초기화

모든 작업 단계의 상단 `초기화`는 확인 후 현재 RAW·피크·주석 등 그래프 편집 상태,
브라우저의 저장 그래프 목록과 현재 보고서 연결을 비우고 `① 데이터 업로드`로 돌아간다.
RAW 업로드 패널은 첫 단계에서 기본으로 보이며, `파일 선택` 또는 드래그로 시작한다.
운영체제 파일 선택창은 사용자 클릭/키보드 조작으로만 연다(페이지 로딩 시 자동 팝업하지 않음).
새로고침해도 이전 그래프가 복원되지 않도록 현재 작업 키와 해당 사용자의 저장 목록을
같은 IndexedDB 트랜잭션에서 초기화한다. 저장 실패 시 삭제를 취소하고 기존 작업을 유지한다.
원본 파일·등록한 피크 라이브러리·다른 사용자의 저장 목록·서버 생성/전송 보고서는 삭제하지 않는다.
단순히 `이전 단계`로 돌아가는 것은 초기화가 아니며, 저장 그래프를 유지한 채 RAW를 교체할 수 있다.
업로드/분석/보고서 저장 중에는 초기화를 잠가 처리 중인 결과가 다시 들어오는 것을 방지한다.

### Raman 비율 피크 선택

그래프 도구의 ‘비율’을 누르면 고정 선택창이 열린다. Raw 시료를 먼저 고르거나
첫 피크를 선택하면 해당 시료를 강조하고 다른 시료를 일시적으로 흐리게 표시한다.
분자와 분모는 같은 시료 안에서만 선택되며, 겹친 피크는 시료명·피크명·위치가
표시된 후보 목록에서 확정한다. 데스크톱 호버는 확인용이며 클릭 전에는 선택하지 않는다.

- 분자: 파란 테두리 원 ‘① 분자’, 분모: 주황 테두리 사각형 ‘② 분모’.
- 선택창에 시료명, 피크명, 위치(cm⁻¹), 스택/Y 이동을 제외한 원래 강도를 표시한다.
- ‘분자 다시 선택’, ‘분모 다시 선택’, ‘분자↔분모 교환’은 현재 비율을 수정한다.
  다른 비율은 ‘새 비율’로 추가한다. 분모가 0이면 선택·교환을 차단한다.
- 완료한 비율 라벨에 마우스를 올려 두 피크를 확인하고, 더블클릭하여 개별 수정·삭제한다.
- 작은 화면에서는 선택창의 ‘접기’로 그래프 공간을 확보한다. 비율 모드 진입 시
  겹칠 수 있는 그래프 도구창은 닫으며, 터치에서는 호버 없이 탭·후보 목록으로 선택한다.
- 표식·흐리게 표시는 화면 전용으로, 원본 데이터·이미지 내보내기·보고서·작업 템플릿에
  저장하지 않는다. 확정한 비율만 기존 실행취소/보고서 경로에 반영한다.

### FTIR/Raman 그래프 폭·비율

그래프 도구의 `축 범위 및 눈금 설정` 안 `그래프 크기`에서 최대 폭(320~2400px)과
높이(240~2400px)를 입력하거나 자동/4:3/16:9/1:1/3:2/5:4/16:10/2:1/21:9/3:4/2:3/9:16
비율을 선택하고 `크기 적용`을 누른다. 높이 입력은 직접 입력 비율로 전환한다. 폭의 빈칸은 화면 너비이며,
`자동 크기`는 원래 반응형 크기로 복원한다. 비율은 데이터 영역만이 아닌 축·범례를
포함한 전체 캔버스 기준이다. 데이터, Crop/축 범위, 축 고정, 피크 설정은 바꾸지 않는다.
설정은 작업 저장·복원, 실행취소/다시실행, 재분석에 반영되며 새 작업 초기화 시 자동으로 돌아간다.
좁은 화면에서는 가독성을 위해 화면 높이 최소 420px를 확보하되, 이미지 다운로드와
보고서용 PNG 캡처에는 선택 비율을 적용한다. 출력 배율(기본 2배)은 그대로 유지한다.
오른쪽 아래 `↘ 크기`는 드래그/방향키로 조절하고 Esc·포커스 이탈·포인터 취소 시 원복한다.
드래그 미리보기는 테두리만 갱신하며 놓을 때 한 번 렌더링/실행취소 기록한다. 선택적 비율 고정을 지원한다.
`ristPlotSize`의 기존 width/ratio와 호환되며 직접 입력은 ratio=custom과 height를 추가한다.
새 PPT는 PNG를 비율대로 contain/중앙 배치하며 PDF와 발행 HTML에도 크기를 반영한다.
저장 그래프마다 서로 다른 비율을 유지한다. 기존 발행본은 불변이며 다시 생성해야 적용된다.
서버 재시작 후 페이지를 새로고침하면 적용되며 DB 테이블 변경은 없다.

### 공통 색상 팔레트

FTIR/Raman 범례·피크 그룹·도형·라이브러리와 XRD 범례·상 그룹의 색상 버튼을 누르면
동일한 웹 팔레트를 연다. 80색 기본표, 최근 사용 10색, `#RGB`/`#RRGGBB` HEX 입력,
색조·채도·명도 조절을 지원한다. 최근 색은 해당 브라우저의 로컬 저장소에 보관하며,
저장소를 차단한 환경에서도 팔레트 사용은 가능하다. 외부 CDN/네트워크 연결은 필요 없다.
선택 전 미리보기는 원본 색을 바꾸지 않고, 선택 시 기존 색상 입력의 `input`/`change`
이벤트를 한 번씩 전달한다. 기존 범례/라이브러리 저장 및 그래프 실행취소 정책은 유지한다.
Esc·닫기·외부 클릭으로 취소할 수 있고 인쇄/그래프 이미지에는 팔레트가 포함되지 않는다.
기존에 내려받은 HTML은 변경되지 않으므로 새 팔레트를 쓰려면 HTML을 다시 생성한다.

## 설치 및 실행

Python 3.11 이상이 필요하다.

웹 화면은 보안 지원 중인 OS의 최신 Chrome을 기본 대상으로 하고, 최신 Edge를
호환 확인 대상으로 유지한다. XP의 Chrome 49.0.2623.75를 위한 호환 모드를 제공하며,
Chromium 49~109/EdgeHTML 요청에는 미리 빌드한 호환 코드·스타일을 전달한다. IE는 제외한다.
기존 모바일 동작과 JavaScript 없는 로그인·가입 폼은 유지한다. 지원 정책과
출시 전 확인 항목은 [루트 README](../README.md#웹-브라우저-지원-정책) 및
[웹 인증 명세](../documents/EDGE_WEB_AUTH.md#11-chrome-중심-지원-및-출시-검증)를 참고한다.
폐쇄망에서는 `app/static/chrome49/`까지 함께 배포하고 API를 재시작한다.
운영 서버에 Node/npm 설치나 DB 변경은 필요 없다. 빌드 및 실제 장비 검증은
[호환 모드 안내](browser_compat/README.md)를 따른다.

Edge 서버에는 프로젝트 루트의 다음 폴더를 같은 부모 경로 아래에
배포해야 한다.

```text
RIST/
  common/
  config/
  sune/
  rin/
  lim/
  ahn/
  edge_api_server/
```

```bash
cd edge_api_server
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 운영 기본 DB는 MariaDB이다. 접속 정보를 먼저 지정한다.
export RIST_DB_HOST=127.0.0.1
export RIST_DB_USER=rist
export RIST_DB_PASSWORD=********

export RIST_ENV=development
python -m app.run
```

작업 파일 큐가 로컬 디스크를 사용하므로 Uvicorn worker는 1개로 실행한다.
여러 서버 인스턴스로 수평 확장하려면 작업 큐를 공유 스토리지/서비스로
전환해야 한다.

환경 전환:

```bash
# 개발 환경: http://bhyoon.me:8000
export RIST_ENV=development
python -m app.run

# 운영 환경: http://192.168.0.10:8000
export RIST_ENV=production
python -m app.run
```

공통 프로파일은 각각 다음 파일에 있다.

```text
../config/environments/development.env
../config/environments/production.env
```

API 문서:

- 개발 Swagger UI: `http://bhyoon.me:8000/docs`
- 개발 OpenAPI JSON: `http://bhyoon.me:8000/openapi.json`
- 개발 상태 확인: `http://bhyoon.me:8000/health`
- 로컬 LLM 상태 확인: `http://bhyoon.me:8000/health/llm`
- FT-IR 웹 분석: `http://bhyoon.me:8000/ftir`
- XRD 웹 미리보기: `http://bhyoon.me:8000/xrd`
- TEM/STEM 보고서 생성: `http://bhyoon.me:8000/tem`

XRD/TEM 실험 PC 연동용 빌드 가능한 .NET 8 참조 클라이언트는
`edge_api_server/examples/csharp-edge-client/`에 있다. 이 예제는 의뢰 조회,
파일별 SHA-256 업로드, 서버 목록 재검증, 보고서 생성·상태 폴링과 브라우저
`reviewUrl` 전달까지 구현한다. SSO 인증정보와 전송 승인은 C# 프로그램에서
취급하지 않는다.

## 실험별 VOC 게시판

네 실험 화면의 VOC 버튼은 `/voc?project=FTIR|RAMAN|XRD|TEM`을 별도 탭으로 연다.
`/voc`는 접근 가능한 실험 전체를 보여 준다. 관리자 메뉴에서도 같은 게시판에 접근한다.
목록은 실험·상태·검색어(제목/내용/작성자 이름/로그인 ID) 필터와 페이지네이션을 지원한다.

일반 회원은 **현재 승인된 동일 실험의 모든 VOC**를 조회한다. 권한이 없는 실험의
내용·건수·처리 이력은 노출하지 않으며 권한 회수는 다음 요청부터 적용한다.
등록자 ID/이름과 실험 페이지 경로는 서버가 세션/프로젝트 코드에서 결정한다.
작성자의 이름과 로그인 ID, VOC 내용, 관리자 조치와 이력은 같은 실험 회원에게 공개되므로
비밀번호나 민감한 원본 데이터는 작성하지 않는다. 관리자는 전체 실험을 조회한다.

상태는 `OPEN`(접수), `IN_PROGRESS`(조치 중), `RESOLVED`(조치 완료·확인 대기),
`CONFIRMED`(작성자 확인 완료)이다. 관리자가 앞의 세 상태를 변경하고 조치 내용을 남긴다.
`RESOLVED`에는 공백이 아닌 조치 내용이 필수다. 작성자 본인만 조치 결과를 확인해
`CONFIRMED`로 변경할 수 있다. 관리자도 타인의 확인을 대신할 수 없고, 확인 이후에는
기록이 확정되어 변경할 수 없다. 확인 전에는 관리자가 조치 중으로 되돌릴 수 있다.
삭제/첨부/댓글/메일 알림은 제공하지 않는다. SSO 여부나 보고서 전송 권한과는 무관하다.

| 메서드·경로 | 역할 |
|---|---|
| `GET /api/v1/voc` | 권한 범위 내 목록/상태별 건수 (`project`, `status`, `q`, `page`, `pageSize`) |
| `POST /api/v1/voc` | `{project, title, content}`로 등록; `Idempotency-Key` UUID 필수 |
| `GET /api/v1/voc/{vocId}` | 본문·조치 결과·처리 이력 조회 |
| `POST /api/v1/voc/{vocId}/status` | 관리자: `{expectedVersion, status, note}` |
| `POST /api/v1/voc/{vocId}/confirm` | 작성자: `{expectedVersion}`으로 조치 확인 |

모든 API는 로그인 세션이 필요하고 변경 요청에는 `X-Requested-With: RIST-VOC`가 필요하다.
인증을 비활성화한 개발 환경에서도 VOC를 익명으로 공개하지 않는다.
등록 제목은 1–160자, 본문/조치 내용은 최대 10,000자, 목록은 기본 20건/최대 100건이다.
같은 회원의 동일 요청 키 재전송은 글을 중복 생성하지 않는다. 다른 내용으로 같은 키를
재사용하면 409를 반환한다. 상태 변경은 행 잠금과 버전 검사를 사용하며 오래된 화면의
수정/확인에는 409를 반환하므로 최신 조치 내용을 다시 읽고 제출해야 한다.
이미 성공한 확인의 재전송은 이력을 추가하지 않는다. 잘못된 입력은 400, 미로그인은 401,
변경 권한 부족은 403, 접근 불가 글 ID는 404다. 응답은 `Cache-Control: no-store`다.

DB는 `voc_requests`와 `voc_events`를 사용하며 UTC 시각을 저장한다(화면은 한국 시간).
앱 시작 시 누락된 테이블을 생성하며 기존 보고서/분석 데이터는 변경하지 않는다.
운영 DB 사전 반영이 필요하면 인증 스키마 적용 후
[`deploy/mariadb_voc_migration.sql`](deploy/mariadb_voc_migration.sql)을 실행한다.
실행 방법은 [배포 문서](deploy/README.md#voc-게시판-db-추가)에 있다.

회귀 검증은 `tests/test_voc.py`(실제 격리 MariaDB 사용)와
`tests/voc_board.test.cjs`(DOM/이벤트 실행 및 생성 JavaScript 문법 검사)로 나눈다.
저장소 루트에서 Python 테스트용 가상환경과 `RIST_TEST_DB_*`를 지정해 실행한다.
DOM 테스트의 npm 패키지는 프로젝트가 아닌 임시 폴더에 설치할 수 있다.

```bash
PYTHONPATH=edge_api_server:common:sune:rin:. python -m pytest edge_api_server/tests/test_voc.py
VOC_JS_DIR=$(mktemp -d)
npm install --prefix "$VOC_JS_DIR" --ignore-scripts --no-audit --no-fund jsdom@26 acorn@8
NODE_PATH="$VOC_JS_DIR/node_modules" PYTHONPATH=edge_api_server:common:sune:rin:. \
  node --test edge_api_server/tests/voc_board.test.cjs
```

Node 테스트는 기본 `python3`을 사용한다. 다른 가상환경은 `RIST_TEST_PYTHON`에
Python 실행 파일 경로를 지정한다. DOM 테스트는 화면 렌더링/모바일 실기 검증을 대체하지 않는다.

## FT-IR 웹 분석

`/ftir`은 빈 Plotly 그래프에서 시작하며 DPT 파일 선택과 드래그 앤 드롭을
지원한다. 파일을 추가하면 별도 저장 파일을 만들지 않고 업로드 바이트를
메모리에서 전처리·피크 분석한 뒤 그래프를 갱신한다.

- 한 번에 최대 10개 DPT 파일
- 파일당 최대 20MB
- 한 요청의 파일 총합 최대 50MB
- 파수·강도 2열 DPT의 쉼표/탭/공백 구분 자동 인식(미리보기·보고서 공통)
- 400~4000 cm⁻¹ 범위에서 유효한 숫자 데이터가 10행 이상 필요
- 여러 파일을 샘플별 trace와 피크로 한 그래프에 표시
- 재료·분석 목적별 피크 assignment 라이브러리 다중 선택
- 화면 편집창에서 라이브러리 생성·수정 및 JSON/CSV 파일 가져오기
- 민감도, 범례, 피크 추가·삭제·그룹, 도형 편집 기능 지원
- 파일 제거 시 남은 샘플을 자동 재분석

피크 assignment 라이브러리는 기본적으로 다음 폴더에서 관리한다.

```text
edge_api_server/data/ftir_assignment_libraries/
```

폴더가 처음 생성될 때 기존 일반 작용기표 `general-ftir.csv`와
`sune/data/RIST_FTIR_Library/`의 590개 기준 스펙트럼에서 자동 추출한
카테고리별 marker 피크 JSON들이 함께 복사된다. 예전 설치처럼
`.initialized` marker가 이미 있는 환경도 새 bundled 기본 라이브러리는 한 번
추가된다. 이후 사용자가 삭제한 bundled 라이브러리는 재시작 때 다시 만들지
않는다.

기본으로 제공되는 marker 피크 라이브러리는 배터리 전해질/바인더, 철강
방청제/윤활제/코팅수지, 범용·엔지니어링·바이오 플라스틱, 엘라스토머,
세라믹/무기, 천연섬유 묶음으로 나뉜다. 이 라이브러리들은 각 기준
스펙트럼에서 대표 피크를 최대 5개씩 추출해 `Material marker @ 1234 cm-1`
형태의 후보 이름을 제공한다. 일반 작용기표와 목적이 다르므로 화면에서
필요한 묶음만 선택해 사용한다.

이후에는 폴더에 JSON/CSV 파일을 직접 배치하거나 `/ftir` 화면에서 파일을
가져올 수 있다. `새 라이브러리`를 누르면 빈 편집창에서 직접 만들 수 있고,
라이브러리 이름을 누르면 중심 파수, tolerance, 피크 이름, 색상과 비고를
수정할 수 있다. 체크된 라이브러리만 그래프 assignment에 사용하며 선택
상태는 `적용`/`미적용`으로 표시한다. 서버 파일의 실수 삭제를 막기 위해
기본 설정에서는 라이브러리 전체 삭제를 제공하지 않는다.
`RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED=true`로 설정하고 Edge API
서비스를 재시작하면 웹 화면과 API에서 서버 라이브러리 파일 삭제를 허용한다.
파일명 stem이 API에서 사용하는 라이브러리 ID가 되므로
`melamine.json`, `phenolic-resin.csv`처럼 영문자·숫자·하이픈으로 작성한다.

JSON 형식:

```json
{
  "name": "Melamine",
  "description": "멜라민 분석용 피크 assignment",
  "assignments": [
    {
      "centerWavenumber": 3460,
      "tolerance": 35,
      "name": "N-H stretch",
      "color": "#db2777",
      "note": "멜라민 N-H 후보"
    }
  ]
}
```

CSV 형식:

```csv
center_wn,tolerance,name,color,note
3460,35,N-H stretch,#db2777,멜라민 N-H 후보
```

여러 라이브러리를 선택하면 각 라이브러리 안에서 해당 파수와 일치하는 가장
구체적인 assignment를 하나씩 고른다. 서로 다른 이름이 나오면 그래프에
`후보 A / 후보 B`로 함께 표시하고 hover에 라이브러리 출처를 표시한다.

기본 marker 피크 JSON을 다시 만들 때는 다음 명령을 사용한다.

```bash
cd edge_api_server
.venv/bin/python ../sune/scripts/library/build_assignment_libraries.py
```

웹 화면이 사용하는 API:

```text
POST /api/v1/ftir/analyze
Content-Type: multipart/form-data

files: DPT 파일(복수)
sensitivity: 0~100, 기본값 25
assignment_library_ids: 선택한 라이브러리 ID(복수)

GET    /api/v1/ftir/assignment-libraries
POST   /api/v1/ftir/assignment-libraries
POST   /api/v1/ftir/assignment-libraries/create
GET    /api/v1/ftir/assignment-libraries/{libraryId}
PUT    /api/v1/ftir/assignment-libraries/{libraryId}
DELETE /api/v1/ftir/assignment-libraries/{libraryId}
```

`DELETE`는 `RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED=true`일 때만 성공한다.
기본값에서는 `403 ASSIGNMENT_LIBRARY_DELETE_DISABLED`를 반환한다.

운영 Edge 앱과 같은 라우터를 DB 없이 화면만 개발할 때는 다음 명령을 사용할
수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.ftir_web:create_ftir_preview_app --factory --host 127.0.0.1 --port 8010
```

## XRD 웹 미리보기

`/xrd`는 LIM XRD 보고서 양식을 브라우저에서 바로 확인하기 위한 미리보기
화면이다. raw TXT, ICDD Card PDF, 선택 Excel/CSV/TSV, 선택 이미지 파일을
한 번에 업로드하면 서버가 확장자로 자동 분류하고 기존 `lim.xrd_plot`
렌더러를 사용해 HTML 보고서를 생성해 화면 안에 표시한다. Chrome 계열
브라우저에서는 하나의 `XRD 번들 추가` 영역에 파일과 폴더를 함께 드래그하거나
bundle 폴더를 선택할 수 있다.

- raw TXT: XRD 측정 패턴 그래프
- ICDD Card PDF: 2θ 피크 overlay와 결정상 후보 정보
- Excel/CSV/TSV: 보고서의 `피크 정보` 영역에 제공 표로 표시
- 이미지: 보고서의 `그래프/상매칭 보조 이미지` 영역에 표시

작업 화면의 분석결과 문안은 편집 가능하다. **보고서 다운로드**는 현재 편집 문안을
보존한 복사본에서 `xrd-analysis-result`와 내부 편집 요소를 읽기 전용으로 만든다.
`make_xrd_html_portable`도 같은 처리를 수행하므로 웹 ZIP·C# 작업/LIMS 전송 패키지의
HTML은 읽기 전용이다. 그래프 조작·오프라인 Plotly·PDF Export 기능은 유지한다.
기존 발행 파일은 변경하지 않으며 외부 HTML 소스 편집을 방지하는 보안 기능은 아니다.

웹 화면이 사용하는 API:

```text
GET  /xrd
POST /api/v1/xrd/analyze
GET  /api/v1/xrd/example
```

`POST /api/v1/xrd/analyze`는 multipart `files` 필드 하나에 raw/PDF/표/이미지
파일을 함께 담아 보낸다. 서버는 `.txt/.dat/.xy/.asc`, `.pdf`,
`.xlsx/.csv/.tsv`, `.png/.jpg/.jpeg/.webp/.gif`를 자동으로 구분한다.

DB 없이 XRD 화면만 개발할 때는 다음 명령을 사용할 수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.xrd_web:create_xrd_preview_app --factory --host 127.0.0.1 --port 8010
```

## TEM/STEM 웹 보고서

`/tem`은 AHN 프로젝트의 TEM/STEM/EDS/코팅층 raw bundle을 브라우저에서
폴더째 업로드하고, 기존 `ahn.processor`와 PowerPoint 템플릿 렌더러를 사용해
PPTX 보고서를 생성한다. Chrome 계열 브라우저에서는 `tem`, `stem`, `report`/`reports`,
`scale` 폴더가 들어 있는 상위 폴더를 드래그하거나 폴더 선택으로 올릴 수 있다.
상위 폴더명이 함께 올라와도 서버가 실제 입력 루트를 자동으로 찾는다.

- `tem`: TEM 이미지 분석 슬라이드
- `stem`: STEM/BF-STEM 이미지 분석 슬라이드
- `report`/`reports`: EDS Word 보고서와 raw spreadsheet
- EDS `.docx`는 폴더 구조 없이 여러 파일을 개별 첨부해도 처리하며, 선택 폴더의
  하위 폴더도 검색한다. 같은 이름의 문서라도 경로별 이미지 캐시를 분리한다.
  이미지·표를 읽을 수 없는 문서는 조용히 제외하지 않고 파일명을 포함한 오류로 안내한다.
- `scale`: 코팅층 두께 OCR 및 표 슬라이드

웹 화면이 사용하는 API:

```text
GET  /tem
GET  /api/v1/tem/upload-worker.js
POST /api/v1/tem/upload-sessions
GET  /api/v1/tem/upload-sessions/{uploadId}
POST /api/v1/tem/upload-sessions/{uploadId}/chunks
POST /api/v1/tem/upload-sessions/{uploadId}/complete
POST /api/v1/tem/analyze
GET  /api/v1/tem/example
GET  /api/v1/tem/report/jobs/{jobId}
GET  /api/v1/tem/report/jobs/{jobId}/download/pptx
GET  /api/v1/tem/report/jobs/{jobId}/download/package
GET  /api/v1/tem/report/jobs/{jobId}/download/analysis-json
```

브라우저는 업로드 세션을 만들고 파일을 조각 단위로 전송한 뒤 `complete`를 호출한다.
새 화면은 세션 생성 시 `{"files":[{"path":"tem/A.tif","size":1234,"lastModified":123}]}`
형태로 전체 파일 목록을 전달한다. 기존 body 없는 세션 생성 API도 유지하되 페이지 복원은
파일 목록을 등록한 세션만 지원한다. 조회 API는 원래 경로·크기·수정일, 받은 바이트 수 및
조각별 `offset/size/crc32`를 반환하며, 이미 완료 요청이 접수되었으면 기존 `job`을 반환한다.
인증 사용 시 작성자(현 TEM 권한 필요) 또는 관리자만 조회·전송·완료할 수 있다.
아직 전송을 시작하지 않은 파일까지 전부 받아야 보고서를 생성하고, 동일 조각 재전송은
기존 파일이나 뒤쪽 조각을 덮어쓰지 않는다. 세션의 조각 기록은 최대 25,000개다.

페이지는 사용자별 업로드/작업 ID를 localStorage에 저장한다(원본 바이트·인증 토큰 저장 안 함).
화면 숨김/오프라인 중에는 다음 조각과 폴링을 대기하고 복귀하면 재개한다. 새로고침·탭 종료로
파일 핸들이 사라졌으면 같은 전체 원본을 재선택한 뒤 **업로드 이어받기**를 누른다.
경로·크기·수정일 및 이미 받은 모든 조각의 CRC32가 일치해야 이어받으며, 완성된 파일은 재전송하지 않는다.
업로드 종료 후 `jobId`를 받으면 서버 생성 작업은 브라우저 없이도 계속되고, 복귀 시 진행 상태를
복원한다. 이미 완성된 보고서는 자동 재이동을 반복하지 않고 **진행 상태 확인** 버튼으로 연다.
완성본은 기존 미리보기에서 검토 후 전송한다. 이 변경은 LIMS 자동 전송을 추가하지 않는다.

지원되는 HTTPS 환경은 업로드 중 Wake Lock을 요청하고 숨김/완료 시 해제한다. 거부되거나
미지원인 HTTP·구형 브라우저에서도 이어받기는 동작한다. 모바일 OS가 페이지를 정지/종료할 수 있어
잠금 상태의 지속 업로드는 보장하지 않는다. 브라우저 저장소 삭제·차단·다른 브라우저에서는 ID를
복원할 수 없으며, 동일 서버 프로세스에서 마지막 전송 후 2시간 이내의 업로드만 복원한다.
미완료 업로드의 **서버 재시작/worker 변경 시 이어받기는 불가**하다.
이 버전 이후 생성된 완료 보고서는 아래 작업 이력 정책으로 영구 저장하여 재시작 후 다시 열 수 있다.
업데이트·재시작 전에 작업을 마치고 API worker 1개 설정을 유지한다.

### TEM 작업 이력·보관 정책

- TEM 작업 이력과 XRD 프로젝트 관리의 공통 스타일은
  `app/xrd_workbench/web/shared/project_manager.css`에서 관리한다. TEM은 HTML에 포함하고,
  XRD 세 화면은 같은 파일을 읽는다. 변경 후 `browser_compat` 자산을 재빌드해야 한다.
  예제 불러오기/다운로드 UI와 XRD 자동 예제 로딩은 제거했다(개발용 예제 API·테스트 자료는 유지).
- `/tem` 상단 **작업 이력**: 작성자 본인/관리자 조회, 작업명·의뢰번호·ID 검색, 20건 페이지 처리,
  이름 변경, 보존/해제, 보고서 새 창 열기, 휴지통, 복원, 개별 또는 현재 페이지 휴지통 비우기.
- 저장 위치: `RIST_STORAGE_ROOT/tem-work-history/work-<uuid>/`. 원본·결과·편집 모델과
  원자적으로 저장하는 `job.json`으로 구성하며 DB 테이블 신설은 필요 없다.
  기존 `/tmp` 작업이나 다른 실험 데이터는 이관·삭제하지 않는다. 신규 작업부터 적용된다.
- 완료/실패 시점부터 **30일** 후 휴지통 이동, 이동 시점부터 **7일** 뒤 영구 삭제.
  단순 조회/상태 폴링으로 기간을 연장하지 않는다. 복원하면 보관 기간을 새로 시작한다.
  **보존** 작업은 자동 정리 제외(수동 삭제 전 보존 해제 필요). 휴지통 이동만으로 용량이 줄지 않는다.
- 단일 API 프로세스에서 10분마다, 이력/작업 요청 시에도 정리한다. 진행 중 작업과 수정본 생성,
  전송·파일 다운로드 중인 원본은 제외한다. 방금 접근한 보고서의 영구 삭제는 1분 유예한다.
- TEM 작업 디렉터리 전체(미완료 업로드 포함) **50GiB**를 기준으로 신규 업로드·다음 조각을 거절한다.
  측정은 최대 30초 캐시하며 생성 중 산출물/동시 조각 때문에 실제 용량이 한도를 일시 초과할 수 있다.
  이는 파일시스템의 강제 quota가 아니다. 디스크 여유 공간 검사도 유지하며 90% 이상 시 UI에 경고한다.
  필요 시 관리자에게 한도 조정을 요청하거나 불필요한 작업을 휴지통으로 이동 후 영구 삭제한다.
- 완료 보고서는 재시작 후 다시 열기/수정/전송 가능. 처리 중 재시작된 작업은 실패(중단)로 표시하여
  새 업로드가 필요함을 알린다. 자동 분석/자동 LIMS 전송은 하지 않는다. 여러 API worker 운영은 지원하지 않는다.
- 삭제 범위는 서버가 생성한 해당 TEM 작업 폴더뿐이다. LIMS 공유 저장소 ZIP/DB 큐는 삭제하지 않는다.
  공유 보고서는 기존 `/report-management` 보존 정책(진행 중 전송 보호)을 사용한다.
  XRD 프로젝트는 기존 프로젝트 관리 휴지통의 확인 후 영구 삭제를 유지한다.

`.env`에 선택적으로 설정하고 서비스를 재시작한다(기본값을 유지하면 추가 설정 불필요):

```dotenv
RIST_TEM_HISTORY_DAYS=30
RIST_TEM_HISTORY_TRASH_DAYS=7
RIST_TEM_HISTORY_LIMIT_GB=50
```

저장 볼륨을 백업할 때 `tem-work-history`도 포함한다. 보존 기간 단축 시 기존 신규형 이력에도
적용되므로, 필요한 작업을 먼저 보존 지정하고 백업한다. 실행 계정에 `RIST_STORAGE_ROOT` 쓰기 권한이 필요하다.

호환 코드 선택은 브라우저 엔진을, TEM 전송 예산은 `navigator.deviceMemory`,
`navigator.hardwareConcurrency`, 모바일·데이터 절약 힌트와 실제 처리 시간을 사용한다.
Supermium/Chrome 또는 XP라는 이유만으로 저사양으로 분류하지 않는다.

| 환경 | 최초 조각 / 상한 | 다음 조각 미리 검증 |
| --- | --- | --- |
| 데스크톱 RAM ≥8GB, 논리 CPU ≥4 | 4 / 8MiB | Worker 정상 시 1개 |
| 데스크톱 RAM ≥4GB, 논리 CPU ≥4 | 2 / 4MiB | Worker 정상 시 1개 |
| 저사양·사양 정보 미제공·모바일 | 2 / 2MiB | 하지 않음 |
| RAM ≤1GB·데이터 절약·2G 힌트 | 1 / 1MiB | 하지 않음 |

서버 `maxChunkBytes`를 넘지 않는다. 충분한 크기의 조각이 연속 2회 2.5초 이내,
약 1MiB/s 이상으로 성공하면 크기를 두 배로 늘린다. 응답이 8초를 넘거나 재시도가 생기면
절반으로 줄인다(기본 하한 1MiB). 재시도 중인 조각의 바이트/offset은 바꾸지 않으며,
이미 준비한 최대 1개 조각 뒤부터 작은 크기를 적용한다. 확대 전 안정화 대기와 숨김/오프라인
표본 제외를 적용한다. 읽기/검증이 2회 연속 2초/MiB보다 느리면 미리 준비를 끄고 상한을 2MiB로 낮춘다.

Worker는 ES5 소스로 같은 서버에서 제공하며 CDN·새 Python 패키지가 필요하지 않다.
하나의 Worker가 256KiB씩 읽어 CRC32를 계산한다. 미지원/권한 차단/오류/30초 응답 초과 시
기존 FileReader/Blob 기반 순차 검증으로 복귀한다. 완료/오류 시 Worker·타이머·리스너를 정리한다.
전송 요청은 항상 하나씩 보내므로 기존 세션 잠금·재전송·이어받기를 유지한다.
파일 전체를 JS 메모리에 적재하거나 파일 수만큼 Worker/전송을 병렬 실행하지 않는다.
목록의 200/500개 렌더링 제한과 순차 폴더 수집, 서버 작업 동시성은 기존 정책을 유지한다.
이 정책은 실제 여유 RAM을 측정하는 것은 아니며 브라우저/OS 내부 버퍼 크기까지 보장하지 않는다.

`업로드 성능 측정`에서 평균 MiB/s·남은 시간·현재 조각 크기·재시도 횟수와 단계 시간을 확인한다.
평균 속도는 이번 실행에서 서버가 확인한 바이트 / 활성 시간이며, 이전 업로드에서 이어받은
바이트와 화면 숨김·오프라인 대기는 제외한다. 숨김/연결 전환에 걸친 조각은 진행률에는 포함하되
속도 표본에서는 제외하여 활성 시간 대비 속도가 부풀려지지 않게 한다. 파일 검증과 요청은 겹칠 수 있고 응답 대기에는
서버 처리 등이 포함되므로 시간들을 합산하면 안 된다. 조각 응답의 `chunkProcessingMs`는
서버가 multipart 본문을 받은 뒤 검증·저장을 처리한 시간이며 수신/프록시 버퍼링은 제외한다.
응답 대기는 브라우저 upload 완료 이벤트부터 응답까지의 관측값(미지원 시 측정 불가)이다.

브라우저와 서버는 각 조각의 CRC32를 대조하고, 서버는 모든 조각과 실제 저장
크기가 일치하는지 다시 확인한다. 이어서 이미지 디코딩과
ZIP/DOCX/XLSX/XLSM/XLSB 내부 구조 및 CRC를 검사한다. 암호화 ZIP이나 암호·DRM으로
보호된 Office 파일, 확장자와 실제 형식이 다른 파일은 문제 파일명을 포함한 오류로
차단한다. 웹 화면은 `complete?defer_validation=true`로 먼저 `jobId`를 받고, 검증과
보고서 생성 전체를 제한된 서버 대기열에서 수행한다. 검증 실패는 job의 `failed`/`error`로
표시하고 다운로드를 활성화하지 않는다. 완료 요청 재전송은 같은 `jobId`를 반환한다.
기존 `complete`(옵션 생략)는 종전처럼 검증 후 job을 발급하므로 연동 계약을 유지한다.
대기열이 가득 차면 `503 TEM_REPORT_QUEUE_FULL`이며, 저장된 업로드 세션을 유지하므로
같은 세션의 `complete`를 나중에 재시도할 수 있다(서버 재시작을 넘는 영구 복구는 아님).

`POST /api/v1/tem/analyze`는 같은 검증을 사용하는 단일 multipart 호환 API다.
이 API도 `?defer_validation=true`를 선택할 수 있으며, 생략 시 기존 응답을 유지한다.
서버는 `.tif/.tiff/.png/.jpg/.jpeg/.bmp/.webp`, `.docx`,
`.xlsx/.xls/.xlsm/.xlsb/.csv/.tsv`, `.zip`을 지원한다. 브라우저는 발급된 `jobId`로
`GET /api/v1/tem/report/jobs/{jobId}`를 폴링해 `completed` 상태가 되면 PPTX,
보고서 ZIP, 분석 JSON 다운로드 링크를 표시한다.

자원 보호는 TEM 웹 API 프로세스별로 적용한다. C# 공통 작업 API/별도 보고서 worker의
자원 정책까지 변경하지 않는다. 저사양 Edge에서는 API worker 수를 1로 유지한다.

- 파일당 250MiB, 원본과 압축 해제 항목을 합한 묶음 1200MiB, 기본 5,000개 파일/압축 항목.
- 조각은 최대 8MiB. 서버는 1MiB 버퍼로 임시 디스크에 저장·CRC 검증 후 `.part`에 반영하여
  잘못된 체크섬/재전송이 기존 바이트를 덮지 않게 한다. 세션의 동시 쓰기를 차단한다.
- ZIP은 메타데이터 한도를 해제 전에 검사하고 각 멤버를 디스크에 순차 검증한다.
  여러 ZIP 및 확장자가 잘못된 ZIP도 합산한다. Office 내부 확장 크기는 기본 256MiB,
  이미지는 기본 4,000만 픽셀(Word 포함) 한도다. 원본을 몰래 축소하지 않고 분할을 안내한다.
- 검증 전 묶음 예산의 2배와 여유 공간 256MiB를 확보할 수 있는지 확인하고, 조각 저장과
  ZIP 해제 전에도 확인한다. 다른 프로세스의 사용량 증가나 OS OOM 종료까지 보장하지는 않는다.
- 기본 동시 처리 1개 + 대기 4개. 동기 API 검증도 같은 처리 슬롯을 공유한다.
  서버 메모리/디스크와 현장 보고서 크기를 측정한 뒤 한도를 변경한다.

DB 없이 TEM 화면만 개발할 때는 다음 명령을 사용할 수 있다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.ahn_web:create_tem_preview_app --factory --host 127.0.0.1 --port 8010
```

FT-IR, Raman, XRD, TEM 화면을 DB 없이 같은 포트에서 함께 확인하려면 통합 preview
앱을 사용한다.

```bash
cd edge_api_server
.venv/bin/python -m uvicorn \
  app.preview_web:create_preview_app --factory --host 127.0.0.1 --port 8000
```

## 환경 변수

| 변수 | 기본값 | 설명 |
|---|---|---|
| `RIST_ENV` | `development` | `development` 또는 `production` |
| `RIST_CONFIG_DIR` | `config/environments` | 공통 프로파일 파일 경로 |
| `RIST_EDGE_PUBLIC_BASE_URL` | 프로파일 값 | Edge 공개 Base URL 재정의 |
| `RIST_EDGE_BIND_HOST` | 프로파일 값 | Uvicorn bind 주소 재정의 |
| `RIST_EDGE_API_PORT` | 프로파일 값 | Uvicorn 포트 재정의 |
| `RIST_STORAGE_ROOT` | `edge_api_server/data/jobs` | 작업 파일 저장 루트 |
| `RIST_USAGE_LOG_ROOT` | `<RIST_STORAGE_ROOT>/usage` | 운영 관리 사용 기록 저장 루트 |
| `RIST_USAGE_LOG_RETENTION_DAYS` | `90` | 사용 기록 자동 보관 기간(일) |
| `RIST_ERROR_ARCHIVE_ROOT` | `<RIST_STORAGE_ROOT>/errors` | 통합 오류 로그와 실패 파일 보관 루트 |
| `RIST_ERROR_RETENTION_DAYS` | `30` | 오류 기록 자동 보관 기간(일) |
| `RIST_ERROR_CAPTURE_FILES` | `true` | 오류 당시 업로드/입력 파일 보관 여부 |
| `RIST_ERROR_MAX_FILE_BYTES` | `536870912` | 오류 보관 개별 파일 최대 크기 |
| `RIST_ERROR_MAX_TOTAL_BYTES` | `2147483648` | 오류 한 건당 보관 파일 총크기 상한 |
| `RIST_FTIR_ASSIGNMENT_LIBRARY_DIR` | `edge_api_server/data/ftir_assignment_libraries` | FT-IR 피크 assignment 라이브러리 폴더 |
| `RIST_FTIR_ASSIGNMENT_LIBRARY_DELETE_ENABLED` | `false` | `true`일 때 FT-IR 라이브러리 파일 삭제 API/UI 활성화. 서비스 재시작 후 반영 |
| `RIST_DB_HOST` | `127.0.0.1` | MariaDB 호스트 |
| `RIST_DB_PORT` | `3306` | MariaDB 포트 |
| `RIST_DB_NAME` | `rist_edge` | MariaDB 데이터베이스명(없으면 자동 생성) |
| `RIST_DB_USER` | `rist` | MariaDB 사용자 |
| `RIST_DB_PASSWORD` | 빈 값 | MariaDB 비밀번호 |
| `RIST_DB_POOL_SIZE` | `8` | API/worker 프로세스당 MariaDB 최대 연결 수 |
| `RIST_DB_POOL_TIMEOUT_SECONDS` | `10` | 풀 연결 대기 제한 시간(초) |
| `RIST_PDF_FONT_PATH` | 없음 | 외부 전달용 PDF에 임베드할 한글 TrueType 폰트 경로 |
| `RIST_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `RIST_LOG_FORMAT` | `text` | `text` 또는 `json`(구조화 로그) |
| `RIST_LOG_FILE` | 없음 | 지정 시 회전 파일 핸들러 추가 |
| `RIST_LOG_DIR` | 없음 | 디렉터리만 지정. `<DIR>/rist.log`로 기록 |
| `RIST_LOG_MAX_BYTES` | `10485760` | 회전 파일 한 개의 최대 크기 |
| `RIST_LOG_BACKUP_COUNT` | `5` | 보관할 회전 파일 개수 |
| `RIST_UPLOAD_EXPIRY_HOURS` | `24` | 업로드 유효시간 |
| `RIST_MAX_UPLOAD_BYTES` | `2147483648` | 개별 파일 최대 크기 |
| `RIST_SUPPORTED_EXPERIMENT_CODES` | 빈 값 | 쉼표 구분 허용 실험코드. 빈 값이면 제한 없음 |
| `RIST_ANALYSIS_TYPE_MAP` | 빈 값 | LIMS 시험코드/장비코드를 내장 분석기와 연결. 예: `A23141=XRD,B54123=TEM` |
| `RIST_LLM_BASE_URL` | `http://127.0.0.1:8001` | OpenAI 호환 로컬 LLM 주소 |
| `RIST_LLM_MODEL` | `gemma4-e4b` | `/v1/chat/completions` 요청의 model 값 |
| `RIST_LLM_TIMEOUT_SECONDS` | `180` | LLM 요청 제한 시간 |
| `RIST_LLM_TEMPERATURE` | `0.1` | 보고서 작성 temperature |
| `RIST_LLM_MAX_TOKENS` | `1200` | LLM 최대 출력 토큰 수 |
| `RIST_LLM_CONTEXT_WINDOW` | `8192` | 모델 컨텍스트 길이 |
| `RIST_LLM_CONTEXT_MARGIN` | `256` | 컨텍스트 계산 안전 여유 토큰 |
| `RIST_LLM_VALIDATE_MODEL` | `true` | 실행 전 `/v1/models`에서 모델 확인 |
| `RIST_LLM_INCLUDE_IMAGES` | `true` | 처리 결과 이미지의 vision 입력 사용 |
| `RIST_LLM_MAX_IMAGES` | `3` | 한 요청에 포함할 최대 이미지 수 |
| `RIST_LLM_MAX_IMAGE_BYTES` | `2097152` | 이미지 한 장의 최대 바이트 수 |
| `RIST_LLM_MAX_INPUT_CHARS` | `200000` | 구조화 분석 JSON 최대 문자 수 |
| `RIST_PROCESSOR_TIMEOUT_SECONDS` | `600` | 자동 processor 실행 제한 시간 |
| `RIST_PROCESSOR_COMMAND_<EXPERIMENT>` | 없음 | 분석 JSON이 없을 때 실행할 processor 명령 템플릿 |
| `RIST_TEM_REPORT_WORKERS` | `1` | TEM/STEM 웹 보고서 동시 생성 작업 수. PPT/OCR 메모리 사용량 때문에 기본은 순차 처리 |
| `RIST_TEM_REPORT_QUEUE_SIZE` | `4` | 실행 중 작업 외 TEM 웹 보고서 최대 대기 수(프로세스별) |
| `RIST_TEM_MAX_FILES` | `5000` | TEM 서버의 입력/압축 항목 수 한도. 웹 선택 UI의 상한도 5,000개 |
| `RIST_TEM_MAX_IMAGE_PIXELS` | `40000000` | TEM/Word 이미지당 픽셀 수 상한 |
| `RIST_TEM_MAX_OFFICE_EXPANDED_BYTES` | `268435456` | Office 파일 내부 해제 크기 상한 |
| `RIST_TEM_DISK_RESERVE_BYTES` | `268435456` | TEM 임시 저장소에 남길 최소 디스크 여유 공간 |
| `RIST_TEM_OCR_WORKERS` | `2` | TEM 코팅층 두께 OCR 병렬 처리 수. CPU 여유가 있으면 최대 4까지 권장 |
| `RIST_WORKER_POLL_SECONDS` | `2` | worker 큐 조회 간격 |
| `RIST_REPORT_STORAGE_KEY` | `RIST_REPORTS` | DB 상대 경로를 해석할 공유 저장소 논리 키 |
| `RIST_REPORT_TRANSFER_MAX_ATTEMPTS` | `5` | LIMS 전송 큐 최대 시도 횟수 |
| `RIST_REPORT_TEST_RETENTION_DAYS` | `7` | 미전송 테스트 보고서 DB 정책 최초 생성 기본값 |
| `RIST_REPORT_FAILED_RETENTION_DAYS` | `30` | 실패·취소 보고서 DB 정책 최초 생성 기본값 |
| `RIST_REPORT_COMPLETED_RETENTION_DAYS` | `90` | LIMS 완료 보고서 DB 정책 최초 생성 기본값 |
| `RIST_REPORT_TRASH_RETENTION_DAYS` | `7` | 휴지통 DB 정책 최초 생성 기본값 |
| `RIST_AUTH_ENABLED` | `true` | 웹 회원 로그인과 프로젝트별 접근 제어 사용 여부 |
| `RIST_AUTH_SESSION_HOURS` | `12` | 로컬 로그인 세션 유지 시간 |
| `RIST_AUTH_RECENT_SSO_MINUTES` | `30` | 보고서 전송에 인정할 최근 SSO 인증 시간 |
| `RIST_AUTH_COOKIE_SECURE` | production `true` | HTTPS에서만 세션 쿠키를 전송. HTTP/HTTPS 혼용 시 명시적으로 `false` |
| `RIST_AUTH_BOOTSTRAP_ADMIN_IDS` | 빈 값 | 쉼표로 구분한 최초 관리자 로그인 ID. 빈 초기 설치는 첫 가입자가 관리자 |
| `RIST_SSO_MODE` | `posco` | `posco` 서버측 ID/PW 검증 또는 선택적 `oidc` 방식 |
| `RIST_SSO_ALLOW_HTTP` | `false` | `true`이면 PC → Edge의 POSCO SSO 요청에 HTTP도 허용. Edge → SSO의 HTTPS·인증서 검증에는 영향 없음 |
| `RIST_SSO_PROVIDER_NAME` | `RIST SSO` | 화면과 DB에 표시할 SSO 공급자 이름 |
| `RIST_SSO_VALIDATION_URL` | 없음 | POSCO SSO HTTPS 검증 URL. 개발계와 운영계를 명시적으로 구분 |
| `RIST_SSO_SID` | 없음 | SSO 담당자가 발급한 시스템 SID. 저장소에 커밋하지 않음 |
| `RIST_SSO_CA_BUNDLE` | 없음 | 기본 신뢰 저장소로 SSO 인증서를 검증하지 못할 때 실제 인증서 체인에 맞춰 지정할 PEM CA bundle 경로 |
| `RIST_SSO_CONNECT_TIMEOUT_SECONDS` | `2` | POSCO SSO TCP/TLS 연결 제한시간 |
| `RIST_SSO_READ_TIMEOUT_SECONDS` | `4` | POSCO SSO 응답 제한시간 |
| `RIST_SSO_ISSUER_URL` | 없음 | 선택적 OIDC 모드의 issuer URL |
| `RIST_SSO_CLIENT_ID` | 없음 | Edge 웹용 OIDC client ID |
| `RIST_SSO_CLIENT_SECRET` | 없음 | Edge 웹용 OIDC client secret |
| `RIST_SSO_SCOPES` | `openid profile email` | OIDC 요청 scope |

`config/environments/*.env`는 git으로 공유하는 Edge scheme/host/port/bind 기본값만
담는다. 서버마다 달라지는 런타임 값은 `/home/rist/ritas/edge.env` 한 곳에서
관리한다.

- LLM 주소/모델은 `RIST_LLM_*`를 사용한다.
- 최종 ZIP은 `RIST_STORAGE_ROOT` 공유 저장소에 두고 DB에는
  `RIST_REPORT_STORAGE_KEY`와 상대 경로만 등록한다. Spring Boot는 큐를 조회해
  같은 공유 저장소에서 ZIP을 직접 읽는다.
- Edge 공개 주소는 프로파일의 `EDGE_SERVER_SCHEME/HOST/PORT`에서 조합하고,
  필요하면 `RIST_EDGE_PUBLIC_BASE_URL`로 재정의한다.

## 웹 회원과 사내 SSO

`RIST_AUTH_ENABLED=true`이면 가입한 회원은 관리자 승인과 프로젝트 권한을 받은
뒤 FTIR, Raman, XRD, TEM 화면에서 보고서를 생성할 수 있다. 보고서 생성에는
사내 SSO가 필요하지 않다. LIMS 전송에는 `REPORT_SENDER` 역할, 연결된 사내 SSO,
최근 SSO 재인증이 모두 필요하며 전송 작업자 값은 SSO 사용자 정보로 기록한다.
POSCO SSO는 기본적으로 브라우저 → Edge도 HTTPS를 요구한다. 인증서 발급 전
HTTP를 사용하려면 Nginx 80포트를 앞단에 두고 Python은 서버 내부에서만 접근하도록
설정한다. 수정된 코드를 배포한 뒤 Edge에서 다음을 실행한다.

```bash
cd /home/rist/ritas
sudo bash edge_api_server/deploy/setup_https.sh apply --mode http --host 10.10.1.31
```

이 명령은 서버 인증서 없이 Nginx를 설정하고 API를 재시작하며, `edge.env`의
아래 값과 내부 API 포트 `8000`, 프록시 신뢰 주소 `127.0.0.1`을 함께 적용한다.
실험실 PC의 주소는 **`http://10.10.1.31`(80)** 이며 외부에서 8000으로 접속하지 않는다.

```dotenv
RIST_AUTH_ENABLED=true
RIST_SSO_ALLOW_HTTP=true
RIST_AUTH_COOKIE_SECURE=false
RIST_EDGE_PUBLIC_BASE_URL=http://10.10.1.31
RIST_EDGE_BIND_HOST=127.0.0.1
```

이 설정은 SSO 입력 화면과 인증 API에서 HTTP/HTTPS를 모두 허용한다.
HTTP 사용 중에는 비밀번호와 세션이 해당 구간에서 암호화되지 않는다.
로그인 세션, 요청 헤더 검사, SSO 성공 여부, 보고서 전송 권한·최근 인증 검사는
그대로 적용된다. `RIST_AUTH_COOKIE_SECURE=true`이면 HTTP 로그인은 계속 차단되므로
혼용 시 두 설정을 함께 적용해야 한다. 예제 `edge.env.example`은 혼용 설정을 사용한다.

Edge → SSO는 항상 HTTPS POST로 `username`, `password`, `sid`를 전송한다.
POSCO 연동에는 `RIST_SSO_MODE=posco`를 별도로 설정한다. Nginx 적용 스크립트는
기존 SSO 모드·URL·SID·CA 설정을 변경하지 않는다.
`RIST_SSO_VALIDATION_URL`은 운영계
`https://swpsso.posco.net/idms/U61/jsp/userValidSSOM.jsp` 또는 개발계
`https://uswpsso.posco.net/idms/U61/jsp/userValidSSOM.jsp`를 사용하고,
`RIST_SSO_SID`에는 해당 환경에서 발급받은 SID를 설정한다. 기본 CA로 검증되면
`RIST_SSO_CA_BUNDLE`은 비워 둔다. SID·출발지 IP 등록과 DNS/TCP 443 통신 허용도 필요하다.

인증서 발급 후에는 아래 명령으로 Nginx 80·443을 함께 사용한다. 파일명은 실제
발급받은 서버 인증서 체인과 루트 CA 경로로 바꾼다. 기존 `/etc/rist/tls/edge.key`와
일치하는 인증서가 필요하다.

```bash
sudo bash edge_api_server/deploy/setup_https.sh apply --mode both \
  --host 10.10.1.31 --cert /media/usb/edge-chain.pem --ca-cert /media/usb/root-ca.crt
```

PC는 `http://10.10.1.31` 또는 `https://10.10.1.31`을 사용하고, 두 경로 모두
Nginx가 `127.0.0.1:8000`으로 전달한다. 자동 HTTPS 리다이렉트는 하지 않는다.
HTTPS 전용 전환 시 `--mode https`로 다시 적용하면 80 리스너를 제거하고
`RIST_SSO_ALLOW_HTTP=false`, `RIST_AUTH_COOKIE_SECURE=true`로 변경한다.
`--mode`를 생략하면 기존과 같이 `https` 모드다. [배포 절차](deploy/README.md)를 참고한다.

관리자는 운영 관리의 `회원 관리` 탭(`/admin/users`)에서 가입 승인 상태,
프로젝트별 접근권한, `ADMIN`·`REPORT_SENDER` 역할과 SSO 연결·최근 인증 상태를
확인하고 변경한다. 관리자 상태만 수동으로 활성화해 역할이 누락된 경우에는
`deploy/mariadb_grant_admin.sql`을 실행한 뒤 다시 로그인한다.

```text
/signup       로컬 회원 가입
/login        로그인
/account      회원정보·비밀번호 변경, 로그아웃, 프로젝트 및 SSO 상태 확인
/admin/users  회원 승인과 프로젝트·전송 역할 관리
```

DB 적용, POSCO SID·서버 IP 등록, DNS/CA 및 최초 관리자 생성·배포 순서는
[`documents/EDGE_WEB_AUTH.md`](../documents/EDGE_WEB_AUTH.md)를 참조한다. 실험 PC의
C# 작업 API는 브라우저 회원 쿠키 인증과 분리되어 기존 인터페이스를 유지한다.
C# 상태 응답의 `reviewUrl`을 브라우저로 열면 생성된 ZIP을 확인할 수 있으며,
LIMS 전송은 그 화면에서 `REPORT_SENDER`와 최근 SSO 인증을 검사한 뒤 승인한다.

## 운영 관리

FT-IR, Raman, XRD, TEM 및 공통 Edge API의 사용 기록, 오류 기록, 보고서와 파일
보존 상태를 한 화면에서 탭으로 나누어 확인한다.

```text
http://<Edge 서버>:8000/operations
```

보고서와 저장소 관리 탭은 다음 주소로 직접 열 수도 있다.

```text
http://<Edge 서버>:8000/report-management
```

- **사용 기록**: 화면 진입, 의뢰 조회, 업로드 완료, 보고서 생성·다운로드·전송 등
  실제 사용자 동작을 프로젝트, 처리 결과, 날짜, 의뢰번호, 작업 ID로 검색한다.
- 사용 기록, 오류 기록, 보고서/파일 관리 목록은 서버 측 페이징을 사용한다. 기본은
  페이지당 25건이며 화면에서 25·50·100건으로 변경하고 이전·다음 페이지를 이동한다.
- 기록 유형을 `화면 조회`, `정보 조회`, `파일 전송`, `보고서 생성 요청`,
  `보고서 완료`, `보고서 실패`, `다운로드`, `보고서 전송`으로 구분한다.
  `보고서 완료`는 요청을 접수한 시점이 아니라 PPTX/PDF/HTML/ZIP 산출물이
  백그라운드에서 실제 생성된 시점에만 기록된다.
- 공통 작업 API로 파일을 보내는 C#/.NET 실험 PC도 사용 기록 대상이다. 작업의
  프로젝트·의뢰번호·장비·실험자와 클라이언트명/버전/PC 이름, 업로드 파일의
  상대 경로·크기·SHA-256을 함께 조회할 수 있다. `X-Client-Type`,
  `X-Client-Name`, `X-Client-Version` 헤더를 보내면 프로그램을 명확히 구분한다.
- 목록의 `클라이언트 / 접속 위치`에는 클라이언트 종류·프로그램명, 실험 PC 이름,
  접속 IP가 함께 표시된다. 프록시를 사용하는 경우 상세 화면에서
  `X-Forwarded-For` 전달 경로와 서버가 직접 확인한 연결 IP도 구분해 조회한다.
- 업로드 청크와 보고서 상태 반복 조회처럼 운영 이력으로 의미가 낮은 내부 요청은
  사용 기록에서 제외한다.
- HTTP 요청 오류와 비동기 보고서 생성 오류를 프로젝트별로 모아 표시한다.
- 오류 코드, 메시지, 작업 ID, 요청 경로, 스택 트레이스를 기록한다.
- 오류 상세의 `클립보드로 복사`로 표시된 스택 트레이스 전체를 줄바꿈 그대로 복사한다.
  HTTPS Clipboard API가 없는 HTTP·구형 브라우저에서는 선택 영역 복사를 사용하며,
  브라우저가 둘 다 차단하면 텍스트를 선택하고 수동 복사 방법을 안내한다.
- 실패 시점까지 업로드된 파일을 별도 오류 폴더에 복사하며, 개별 파일 또는
  사건 전체 ZIP으로 내려받을 수 있다.
- 목록에서 미해결/해결 상태를 변경하거나 보관 기록을 삭제할 수 있다.
- 오류 상세의 `고객 코멘트`에서 재현 상황과 추가 설명을 기록한다. 고객에게는
  `/error-feedback/{errorEventId}` 주소를 전달하면 오류 로그나 보관 파일을
  노출하지 않고 해당 오류에 코멘트만 남길 수 있다.
- API 오류 응답에는 추적용 `X-Error-Event-Id`와 고객 입력 주소인
  `X-Error-Comment-Url` 헤더가 포함된다. JSON 본문과 비동기 보고서 상태에도
  동일한 `errorEventId`, `errorFeedbackUrl`이 포함되므로 브라우저와 C# 클라이언트
  모두 실패 직후 고객 코멘트 화면을 열 수 있다.
- **보고서/파일 관리**에서는 의뢰번호, 프로젝트, 장비, 실험자, 보고서 생성·전송
  상태, 최근 오류, 보존 기한과 LIMS 완료 시각을 함께 조회한다.
- `report_artifacts`에 등록된 RAW/ZIP/PPTX/PDF/HTML/XLSX/IMAGE의 실제 존재 여부,
  크기, SHA-256, 공유 저장소 상대 경로를 확인하고 개별 파일을 내려받을 수 있다.
- 저장소 전체·프로젝트별 사용량, DB에만 있는 파일, DB 등록 없이 남은
  `web-reports` 파일을 표시한다.
- 실패·취소 전송 재시도, 대기 전송 취소, 테스트 표시, 보존 고정, 보존 기한 직접
  지정, 정리 대상 미리보기와 선택 일괄 정리를 지원한다.
- `보존 정책 설정`에서 미전송 테스트, 실패·취소, LIMS 완료, 휴지통 파일의
  보존일과 자동 정리 여부를 변경할 수 있다. 변경값은 DB에 저장되어 API와
  worker에 즉시 적용되므로 서비스 재시작이 필요하지 않다.
- 활성 전송(`PENDING`, `PROCESSING`, `RETRY_WAIT`) 삭제 금지와 보존 고정 보고서의
  자동 정리 제외 규칙은 안전 정책으로 고정되어 운영 화면에서 해제할 수 없다.

파일 정리는 즉시 삭제하지 않는다. 활성 전송(`PENDING`, `PROCESSING`,
`RETRY_WAIT`)과 보존 고정 보고서는 정리할 수 없으며, 선택 항목 중 하나라도
차단되면 일괄 정리 전체가 중단된다. 정리 대상 파일은 먼저
`<RIST_STORAGE_ROOT>/.report-trash`로 이동하고 DB에 작업자·사유·휴지통 경로를
기록한다. 다른 보고서가 같은 파일을 참조하면 해당 물리 파일은 유지한다.

기본 자동 보존 정책은 미전송 테스트 7일, 실패·취소 30일, LIMS 완료 90일이며
휴지통 파일은 7일 뒤 실제 삭제한다. 위 환경 변수는 DB에 정책 행이 없을 때만
사용하는 최초 생성 기본값이다. 이후 변경은 운영 관리 화면에서 수행한다.

사용 기록의 기본 보관 위치는 `<RIST_STORAGE_ROOT>/usage`, 기간은 90일이다.
오류 기록은 `<RIST_STORAGE_ROOT>/errors`에 30일간 보관한다. 대용량 raw 파일
때문에 디스크가 과도하게 사용되지 않도록 개별 파일 512 MiB, 오류 한 건 전체
2 GiB 상한을 적용한다. 기존 `/errors` 주소는 오류 기록 탭으로 바로 진입하는
호환 주소로 유지한다. 사용·오류 기록 관련 환경 설정을 변경한 경우에는 API와
worker 서비스를 함께 재시작한다.

## 데이터베이스

운영 기본 백엔드는 **MariaDB**이다. 서버 실행 전에 MariaDB가 동작 중이어야
하며, 접속 정보를 환경 변수로 지정한다.

```bash
export RIST_DB_HOST=127.0.0.1     # 엣지 서버 로컬 MariaDB
export RIST_DB_PORT=3306
export RIST_DB_NAME=rist_edge
export RIST_DB_USER=rist
export RIST_DB_PASSWORD=********
```

- 지정한 데이터베이스(`RIST_DB_NAME`)가 없으면 서버 시작 시 `utf8mb4`로
  자동 생성하고 필요한 테이블과 `request_summary` View를 만든다. 따라서 DB
  사용자에게 `CREATE`, `CREATE VIEW` 권한이 있어야 한다.
- 드라이버는 순수 파이썬 `PyMySQL`을 사용하므로 시스템 라이브러리 설치가
  필요 없다.
- 연결은 프로세스별로 재사용한다. 동시 요청 수와 MariaDB `max_connections`를
  고려해 `RIST_DB_POOL_SIZE`를 조정한다.
- 작업 파일 큐는 로컬 디스크를 사용한다. 여러 서버로 수평 확장하려면 공유
  스토리지가 필요하다.

## PDF 한글 폰트

PDF는 ReportLab으로 생성하며, 한글이 네모로 깨지지 않도록 한글 글리프가 있는
TTF/OTF/TTC 폰트만 임베드한다. `RIST_PDF_FONT_PATH`를 비우면 macOS 기본 한글
폰트, Ubuntu `fonts-nanum`/`fonts-noto-cjk` 계열, `/opt/rist/fonts` 순서로
자동 탐색한다. 자동 탐색에 실패하면 깨진 PDF를 만들지 않고 오류를 낸다.
Ubuntu의 `fonts-noto-cjk`는 `.ttc` 컬렉션이 ReportLab에서 등록되지 않는
환경이 있으므로 운영 서버는 `fonts-nanum`과 명시 경로 사용을 권장한다.

```bash
export RIST_PDF_FONT_PATH=/usr/share/fonts/truetype/nanum/NanumGothic.ttf
```

Ubuntu 서버에서 자동 탐색 또는 명시 경로를 사용하려면 한글 폰트를 설치한다.

```bash
sudo apt install -y fonts-nanum
```

PPTX 기반 PDF는 별도로 LibreOffice/soffice를 사용한다. 이 변환에도
`RIST_PDF_FONT_PATH`가 가리키는 폰트의 디렉터리를 전달한다. macOS의 headless
변환기는 설치된 시스템/사용자 폰트를 찾도록 변환별 임시 fontconfig와 캐시를
사용하며, 변환 후 정리한다. 운영자가 지정한 `FONTCONFIG_FILE`, `FONTCONFIG_PATH`,
`SAL_FONTPATH`는 보존한다. 이 처리는 폰트를 설치하지 않으므로 Linux 운영 서버에는
위와 같이 실제 한글 폰트가 설치되어 있어야 한다.

## 테스트

```bash
pip install -r requirements-dev.txt
pytest
```

테스트는 MariaDB(또는 MySQL) 인스턴스가 필요하다. 접속 정보는 다음 환경
변수로 지정하며, 각 테스트는 격리된 임시 데이터베이스를 생성·삭제한다.
접속할 수 없으면 해당 테스트는 건너뛴다.

```bash
export RIST_TEST_DB_HOST=127.0.0.1
export RIST_TEST_DB_PORT=3306
export RIST_TEST_DB_USER=root
export RIST_TEST_DB_PASSWORD=********
```

Docker가 있는 개발 장비에서는 임시 MariaDB를 자동으로 띄워 통합 테스트를
실행할 수 있다.

```bash
PYTHON_BIN=.venv/bin/python scripts/run_mariadb_tests.sh
```

## FTIR/Raman 발행 보고서 미리보기

- `자유 편집 켜기`로 기존 글자/이미지/도형의 위치·크기와 새 글자/이미지/사각형/타원/선을 편집한다.
  연필 아이콘으로 켜며 삽입·실행 취소·개체 관리 도구는 아이콘 버튼으로 표시한다.
  마우스를 올리면 기능명을 확인할 수 있고, 키보드/보조기술용 이름도 제공한다. 외부 아이콘 폰트는 사용하지 않는다.
  드래그는 이동, 오른쪽 아래 핸들은 크기 조절(이미지는 비율 유지), 숫자는 페이지 대비 백분율이다.
  글자 크기·굵기·색·정렬, 채우기·테두리 색, 선 굵기·실선/점선/일점쇄선을 설정할 수 있다.
  개체 목록으로 겹친 항목을 선택하고 앞/뒤 배치, 삭제, 실행 취소/다시 실행(현재 편집 세션 30회),
  현재 페이지 개체 편집 초기화를 지원한다. 드래그 중에는 테두리만 표시하고 놓으면 자동 저장한다.
  자유 편집의 문구 수정은 **해당 페이지의 표시값**이며 분석 데이터/전송 식별자/공통 정보는 변경하지 않는다.
  의뢰정보는 기존 공통 입력을 사용하고 그래프의 분석 변경은 분석 화면에서 수행한다.
  긴 문구/표로 자동 페이지 분할이 달라지면 배치를 다시 확인한다. 배경·RIST 로고는 고정한다.
  `canvasEdits`를 서버에서 검증한 뒤 PPT의 네이티브 개체에 적용하므로 PowerPoint에서도 편집할 수 있다.
  동일한 PPT에서 PDF·페이지 미리보기를 생성하고 최종 전송 ZIP에 반영한다. 원본/이전 발행본은 보존한다.
  `POST .../editor-images`는 기존 작성자 권한·동일 출처 검증을 사용하며 PNG/JPEG/WebP 2MiB,
  1,200만 화소 제한, 최대 1920px 정규화와 메타데이터 제거를 적용한다. 작업별 이미지 보관은 32MiB,
  개체 변경은 페이지당 100개/보고서당 500개로 제한한다. 외부 URL·클라이언트 파일 경로를 받지 않는다.
  이미지 파일은 별도 업로드하고 자동 저장에는 해시 ID만 포함한다. 미사용 업로드는 최종 ZIP에서 제외한다.
  이 기능은 기본 개체 편집이며 애니메이션·그룹 편집 등 PowerPoint 전체 기능을 제공하는 것은 아니다.
- 작성자용 PPT 미리보기의 `페이지 추가`는 현재 페이지 뒤에 포스코 배경·RIST 로고가 있는
  제목/본문 페이지를 추가한다. 왼쪽 입력칸 또는 PPT의 편집 영역에서 내용을 수정하면 자동 반영된다.
  `현재 페이지 삭제`는 확인 후 수정본에서 제외하며 `최근 삭제 취소`로 역순 복원한다.
  PPT 왼쪽의 페이지 사이드바에서 썸네일을 선택하거나 끌어 순서를 바꾼다.
  위/아래 아이콘과 썸네일의 Alt+↑/↓도 지원하며, 방향키만 누르면 선택 페이지를 이동한다.
  목록 버튼으로 접고 펼칠 수 있고 좁은 화면에서는 기본적으로 접힌 서랍 형태로 제공한다.
  썸네일은 표시 영역 주변의 240px 이미지만 한 번에 하나씩 요청한다.
  순서는 안정적인 페이지 ID의 `pageEdits.order`로 저장하고 재접속·PPT/PDF·전송본에도 유지한다.
  삭제한 페이지를 복원하면 저장된 순서로 돌아오며, 새로 늘어난 자동 분할 페이지는 원래 앞 페이지 뒤에 배치한다.
  저장·구조 갱신 중에는 추가/삭제/순서 변경을 잠가 이전 페이지 목록을 잘못 적용하지 않도록 한다.
  원본 분석값·Raw·이미지와 기존 발행본은 삭제하지 않는다. 최소 1장, 전체 200장,
  추가 페이지 50장(삭제 취소용 포함)까지 지원한다. 제목 100자/본문 2,000자 제한과 별개로
  한 장에 들어가지 않는 본문은 저장 전에 분할 안내를 표시하며, 문구를 잘라 버리지 않는다.
  페이지 구성은 `pageEdits`로 저장하며 저장 완료 후 PPT, 두 PDF, 읽기 전용 PPT 미리보기,
  다운로드·전송 대기 패키지에 같은 구성을 반영한다. 별도 분석 요약·인터랙티브 그래프는 보존한다.
  순번 대신 생성 페이지의 종류/항목/그림 식별자를 사용해 앞쪽 항목의 페이지 증가로
  다른 종류의 페이지가 잘못 삭제되지 않도록 한다. 표의 자동 분할 내용 자체는 문구 길이에 따라 바뀔 수 있다.
  이 기능은 FTIR/Raman에 적용하며 TEM/XRD의 기존 페이지 편집 경로는 변경하지 않는다.
- 실험 화면의 `보고서 미리보기`는 작성자용 `/api/v1/{ftir|raman}/report/jobs/{jobId}/edit`를 연다.
  전송정보 4개 항목, 실험조건/환경의 표준 항목은 미입력이어도 빈칸으로 포함하며,
  문단·표·AI 초안·메일 문안을 수정할 수 있다. 조건 및 표는 PPT에서 페이지를 나누어 끝까지 보존한다.
  `GET .../editor-data`는 서버 소유 보고서 JSON에서 편집 데이터만 반환한다.
  `POST .../revisions`는 `X-Requested-With: RIST-Report-Editor`, 동일 출처 및 작성자/관리자 권한을 확인한다.
  요청 2MiB/입력 총량 제한, 동시 수정본 생성 1개 제한을 두며 브라우저가 보낸 파일 경로는 받지 않는다.
  저장은 새 job/report ID로 PPT/PDF/HTML ZIP을 생성하고 기존 원본·전송 대기 파일은 보존한다.
  그래프·raw Excel은 그대로 복사하고 LLM/분석은 재실행하지 않는다. 실패해도 원본과 입력 내용은 유지한다.
  수정 표시가 있는 동안 편집 화면의 다운로드/전송을 차단하며, 저장 완료 시 열려 있는 분석 화면에도
  수정본 ID·전송정보를 알린다. 전송 시 유효한 SSO 실험자를 포함한 정보가 보고서와 다르면 저장을 요구한다.
  편집은 임시 작업이 유지되는 동안 가능하다(기본 TTL 1시간, 새 작업 생성 시 정리). 서버 재시작/만료 시 재생성한다.
  `/reports/{reportId}` 및 ZIP의 수신자용 HTML은 계속 읽기 전용이며 편집 API/코드를 포함하지 않는다.
- 분석 화면에서 피크/라벨/박스/메모를 편집한 뒤 보고서를 생성하면 `분석 결과` 탭을 먼저 연다.
  `RIST_LLM_BASE_URL`/`RIST_LLM_MODEL`의 로컬 LLM에 현재 표시된 피크명·좌표·강도·수동 추가 여부,
  박스 좌표와 포함 피크, 메모 및 Raman 비율을 전달한다. 스택 오프셋은 실제 강도와 구분한다.
  삭제·숨김 피크와 편집 전 판정은 입력에서 제외하며 원본에서 피크를 다시 검출하지 않는다.
- 문안은 요약/주요 관찰/해석/검토 사항으로 표시하며 PPT/PDF와 동일한 보고서 문서를 사용한다.
  PPT 요약 카드에서 축약된 AI 문안은 `전문` 상세 페이지에 줄/페이지를 나누어 보존한다.
  성공/일부 생성/규칙 기반 대체를 구분하며 LLM 실패로 보고서 전체 생성을 막지 않는다.
  사용자 메모는 명령이 아닌 참고 데이터로 처리하고 확정 동정으로 간주하지 않도록 지시한다.
  AI 해석은 검토용 초안이며 생성 내용의 정확성은 분석자가 확인해야 한다.
- 입력은 수동 추가·이름 수정·그룹·관심 영역·비율 선택 피크를 우선으로 시료별 순환 선택한다.
  최대 48개 피크, 메모/영역/비율 각 8개를 포함하고 생략 수를 전달한다.
  Raman 비율은 보고서 표와 같은 계산 경로로 시료·분자/분모의 현재 이름/위치/이동 전 강도·비율을 전달한다.
  삭제/숨김 피크를 참조하는 비율은 제외하며, 분모가 0에 가까우면 계산 불가로 전달한다.
  작은 모델 컨텍스트에 맞춰 이미지·피크 수와 메모/비율 등의 입력을 줄이되 생략 수를 명시한다.
  피크가 없어도 남아 있는 메모는 전달하고 피크 기반 해석은 할 수 없다고 지시한다.
  클라이언트는 스택 계산 완료를 기다린 뒤 이미지 캡처 전에 그래프·분석 정보를 같은 시점에 고정한다.
  생성 당시 스냅샷만 사용하므로 그래프 편집 후에는 분석 화면에서 보고서를 재생성한다.
  기존 미리보기를 열 때 그래프가 바뀌었으면 재생성 안내와 이전 발행본 열기 확인을 표시한다.
  재생성은 기존 보고서의 수동 수정 문안을 자동으로 병합하지 않으며, 수정본 저장은 계속 LLM을 재호출하지 않는다.
  수신자용 미리보기에는 해석 재생성/편집 기능이나 LLM 접속 정보가 포함되지 않는다.
- 보고서 생성이 끝나면 실험 화면의 `보고서 미리보기` 또는 `/reports/{reportId}`의
  `PPT · 그래프 미리보기`를 연다. 기존 로그인/프로젝트 권한 검사와 SSO 전송 요건은 유지한다.
- `/api/v1/{ftir|raman}/report/jobs/{jobId}/preview`는 현재 작업의 ZIP을 읽고,
  `/api/v1/reports/{reportId}/preview`는 DB에 등록된 공유 저장소 ZIP을 읽는다.
- ZIP의 `report.html`은 실제 `report-from-pptx.pdf`에서 렌더링한 PPT 페이지와
  독립된 열람용 Plotly 그래프를 포함한다. `report-summary.html`은 기존 정적 본문이다.
  Plotly와 페이지 이미지를 HTML에 포함하므로 오프라인에서도 사용할 수 있다.
- 그래프는 발행 좌표/피크/주석을 스냅샷으로 보존한다. 중첩/스택은 Y 표시 위치만 바꾸며
  `발행본으로 복원`하면 원본 표시로 돌아간다. 분석 편집 코드와 저장/전송 API는 없으며
  서버 미리보기는 별도 CSP sandbox로 격리한다. 다운로드 파일 자체는 DRM 대상이 아니다.
- 클라이언트는 기존 `figure_json` 메타데이터 외에 `viewer_figure` JSON 파일 파트를 보낸다.
  텍스트 파트 제한과 구분하여 50MiB 이하로 제한한다. 이전 클라이언트처럼 좌표가 없는
  요청은 정적 그래프 이미지와 재생성 안내를 표시하며 좌표를 재분석해서 만들지 않는다.
- 기존 LibreOffice/soffice PPT→PDF 변환과 `pdfplumber`의 PDFium 렌더러를 사용한다.
  페이지별 1280px JPEG를 생성하고 200페이지/이미지 합계 40MiB/HTML 80MiB로 제한한다.
  브라우저는 현재 페이지 이미지만 디코딩하고 그래프는 탭을 열 때 초기화한다.
- DB 스키마 변경은 없다. 기존 DB 전송 큐의 ZIP 안에 미리보기도 포함된다.
  최신 코드·호환 자산 배포와 서버 재시작 후 브라우저를 새로고침하고 새 보고서를 생성한다.
  기존 ZIP은 자동 변환하지 않으며, 기존 보고서 미리보기 요청에는 재생성 안내를 반환한다.

## 로컬 LLM 및 보고서 worker

로컬 LLM은 다음 주소에서 OpenAI 호환 API를 제공해야 한다.

```text
http://127.0.0.1:8001/v1/models
http://127.0.0.1:8001/v1/chat/completions
```

연결과 모델 설정은 다음 API로 확인한다.

```bash
curl http://127.0.0.1:8000/health/llm
```

보고서 요청을 받으면 다음 파일이 생성된다.

```text
{jobRoot}/queue/report-request.json
```

C# raw-only XRD/TEM 작업은 별도 processor 명령 없이 내장 adapter로 처리한다.

- XRD: `input/`의 raw 패턴과 ICDD PDF를 분류해 `lim.xrd_plot` 기반 오프라인
  HTML 보고서를 생성한다.
- TEM: `input/`의 `tem`, `stem`, `report`/`reports`, `scale` 구조를 검사해
  `ahn.processor`와 템플릿 기반 PPTX를 생성한다.
- LIMS 시험코드가 분석기 이름과 다르면 `RIST_ANALYSIS_TYPE_MAP`으로 연결한다.

FT-IR/Raman 또는 별도 사용자 processor를 사용하는 공통 경로는 LLM 실행 전에
구조화 분석 결과 JSON을 다음 위치에 하나 이상 생성해야 한다.

```text
{jobRoot}/processed/analysis-result.json
```

`processed` 폴더에 JSON이 없고 실험 코드별 processor 명령이 설정되어 있으면
worker가 보고서 생성 전에 해당 명령을 실행한다. 환경 변수 이름은 실험 코드를
대문자로 바꾸고 영숫자가 아닌 문자를 `_`로 치환한 값이다. 예를 들어 `FT-IR`은
`RIST_PROCESSOR_COMMAND_FT_IR`, `XRD`는 `RIST_PROCESSOR_COMMAND_XRD`를 사용한다.

명령 템플릿에는 다음 placeholder를 사용할 수 있다.

```text
{job_root}
{input_dir}
{processed_dir}
{report_dir}
{experiment_code}
{job_id}
```

이미지 입력을 사용할 경우 `processed` 폴더에 `png`, `jpg`, `jpeg`, `webp`
파일을 둔다. 최대 3개, 파일당 2 MiB까지 data URL로 전달하며 이 값은 환경
변수로 변경할 수 있다.

worker 실행:

```bash
source .venv/bin/activate
export RIST_ENV=development
python -m app.report_worker
```

대기 작업 한 건만 처리하고 종료:

```bash
python -m app.report_worker --once
```

worker가 생성하는 파일:

```text
{jobRoot}/logs/llm-request.json
{jobRoot}/logs/llm-response.json
{jobRoot}/logs/processor-<experiment>.json
{jobRoot}/report/report.json
{jobRoot}/report/report.md
{jobRoot}/report/report.pptx, report.pdf 또는 report.html
```

LLM에는 원본 bundle을 보내지 않고 `processed` 폴더의 JSON과 허용된 분석
이미지만 전달한다. 요청 로그에는 이미지의 base64 본문을 기록하지 않는다.
보고서는 먼저 규칙 기반 작성기가 판정, 수치, 표를 결정론적으로 채운 뒤,
LLM이 `summary`, `narrative`, `caption` 자유서술 슬롯만 보조 작성한다.
LLM 호출이 실패해도 규칙 기반 기본 문안으로 `report.json`, `report.md`,
요청 포맷의 PPTX/PDF를 완성한다. 내장 XRD/TEM 경로를 포함해 작업이
`COMPLETED`, 진행률 100%가 되면 보고서는 `READY_FOR_REVIEW`이며 아직 LIMS 전송
큐에는 등록되지 않은 상태다.

FT-IR 작업은 라이브러리 매칭 결과와 룰 기반 판정을 구분해 고정 섹션을 만들고,
단정적 해석을 피하는 전용 프롬프트로 자유서술 슬롯만 보강한다.
