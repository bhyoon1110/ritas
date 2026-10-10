# 브라우저별 UI 호환 빌드

목표는 XP의 Chrome **49.0.2623.75**에서 기존 FTIR·TEM 업무 흐름을 유지하면서 최신
브라우저의 코드는 변경하지 않는 것이다. Chromium 49~109와 EdgeHTML 요청을 호환
프로파일로 분류한다. 그 외 브라우저는 기존 화면을 사용한다. IE 지원 구현은 아니다.
UA는 자산 선택에만 사용하며 권한 판단에는 사용하지 않는다.

## 구조와 배포

- 서버의 HTML 화면에만 호환 코드를 적용한다. JSON API, 업로드/다운로드 스트림,
  PDF/PPTX/ZIP과 독립 HTML 보고서를 변환하거나 프록시하지 않는다.
- Babel로 인라인 프로그램과 Plotly를 ES5로 빌드한다. core-js, XHR 기반 fetch,
  AbortController, FormData, Blob 읽기, DOM, 마우스 Pointer Events 폴백을 로컬 번들로 제공한다.
- Chrome 49의 fetch 기본 쿠키 누락과 취소 미지원을 보완한다. SameSite에 의존해 CSRF
  방어를 완화하지 않는다. 쿠키, 동일 출처, 권한, SSO·HTTPS 요구조건은 그대로다.
- Grid/gap/min()/inset 일부를 Flexbox/여백/폭 제한으로 대체한다. 최신 화면에는 적용하지 않는다.
- 해시가 포함된 JS/CSS와 manifest, 라이선스를 `../app/static/chrome49/`에 함께 커밋한다.
  서버에는 Node/Babel/npm/CDN이 필요 없다. Python 의존성과 DB 스키마 변경도 없다.
- HTML 캐시는 User-Agent 및 Sec-CH-UA별로 분리한다. 개인별 VOC 설정은 공유 JS에 넣지 않는다.
- UI 소스가 변했는데 재빌드하지 않은 경우 Chrome 49에 고장 난 JS를 보내지 않고
  읽을 수 있는 503 배포 안내를 표시한다. 최신 브라우저는 이 검사를 적용하지 않는다.

## 개발 환경에서 재빌드

RIST Python 의존성이 설치된 환경과 Node.js가 필요하다. 이 디렉터리에서:

```bash
npm ci --ignore-scripts
RIST_BUILD_PYTHON=/absolute/path/to/python npm run build
npm test
```

`collect.py`는 DB 접속 없이 현재 HTML 빌더에서 JS/CSS를 추출한다. 테스트용 입력만
사용하고 사용자 정보나 비밀을 자산에 기록하지 않는다. 생성된 JS는 ES5 파서로 검사한다.
생성 파일은 직접 수정하지 않는다. UI 소스·빌드 도구·lockfile·생성 자산을 함께 커밋한다.
서버에는 Git pull/오프라인 bundle 적용 후 API 재시작으로 반영된다.

## 검증 범위와 한계

### 실제 Windows XP VM 실기 검증 (2026-10-10)

코드 기준 `8bb3b68`에서 Windows XP Professional SP3 **32비트**를 실제로 부팅하고,
Chrome **49.0.2623.75**와 Supermium **150 R2 / 150.0.7871.255(32비트)** 바이너리로
시험했다. UA만 바꾼 모의 시험과 다르다. 환경은 Apple Silicon Mac의 QEMU 11.1.1/TCG,
논리 CPU 1개·RAM 2GB·1024×768이며, 별도 FastAPI 서버와 임시 MariaDB를 사용했다.
분석 입력은 합성 데이터다.

| 실제 XP에서 확인한 항목 | Chrome 49 | Supermium 150 |
| --- | --- | --- |
| 가입·로그인·한국어 화면 표시 | 통과 | 통과 |
| FTIR DPT 2개 분석, 민감도 0에서 피크 숨김·민감도 복원 | 통과 | 통과 |
| 실제 마우스 Y 드래그의 축 범위 유지, Crop 진입 시 Y 이동 해제 | 통과 | 통과 |
| PNG 저장 후 XP 기본 뷰어로 피크 정보·좌측 상단 샘플 범례 확인 | 통과 | 통과 |
| FTIR 보고서 ZIP 생성·실제 다운로드 | 통과 | 통과 |
| 약 30MiB TEM ZIP을 2MiB씩 15개 조각으로 전송 | 통과 | 통과 |
| TEM 3장·STEM 2장 반영, PPTX 포함 ZIP 다운로드 | 통과 | 통과 |
| 계정 이름 저장, VOC 등록→관리자 조치→작성자 확인 | 통과 | 통과 |
| Raman/XRD 초기 화면 전환(분석 시험은 아님) | 통과 | 통과 |
| 자산 선택 | Chrome 49 호환 자산 | 네이티브 최신 자산, 호환 번들 없음 |

두 브라우저에서 XP 디스크에 저장한 FTIR/TEM ZIP **4개 모두**를 서버가 생성한 원본과
XP의 `fc /b`로 비교하여 바이트 단위 일치를 확인했다. 원본 ZIP의 전체 멤버 CRC도
통과했다. FTIR PNG는 두 브라우저 모두 실제 저장 파일을 XP 기본 이미지 뷰어에서 열었다.
Chrome 49는 시험 도구 보정 후 그래프와 TEM/VOC 단계를 나누어 재실행한 결과다.
실험 화면에서 수집된 미처리 JavaScript 예외는 0개였다. 최종 전체 회귀는 Python
**612 passed**(기존 deprecation warning 16개), JavaScript **22 passed**였다.

실제 Supermium은 기본 UA를 Windows 10/Win64/Chrome 150으로 표시했다. 그 상태에서도
최신 자산과 자원 절약 정책(`lowResource=true`, 2MiB/200개)이 함께 선택됐다.
`?browser=supermium` 선택, 다른 실험 페이지로 설정 유지, `?browser=auto` 해제도
실제 브라우저에서 통과했으며, 수동 식별 때문에 2MiB 정책이 달라지지 않았다.
Supermium의 `chrome://version`에서 실제 OS는 Windows XP(Build 2600)로 확인했다.
시험 실행 배치에는 추가하지 않았지만 실제 명령줄에는 `--no-sandbox`가 표시됐다.
따라서 브라우저 샌드박스가 활성화된 보안 시험으로 간주하지 않으며, 이 기능 시험이
구형 OS나 브라우저의 보안 보호를 보장하는 것은 아니다.

**안정성 미해결 사항:** Chrome 49 기능 시험 완료 후 저장 PNG를 브라우저 파일 URL로
열려던 단계에서 XP VM이 한 차례 재시작됐다. XP Save Dump 이벤트 1001에는
bugcheck `0x1000000A`가 기록됐다. 원인은 확정하지 않았으며, 웹앱 오류가 아니라거나
해결됐다고 단정하지 않는다. 재부팅 후 같은 PNG는 XP 기본 뷰어에서 정상 표시됐고,
이후 Supermium 기능 시험은 완료됐다. 따라서 기능 통과를 XP 장시간 안정성 보장으로
해석하지 않는다.

외부 인터넷은 차단하고 테스트 서버만 연결했다. 게스트 내부 루프백 디버깅을 사용했고
방화벽·인증서 검증을 끄거나 외부 디버그 포트를 개방하지 않았다. 이번 실기는 **HTTP**의
격리 서버 시험으로, 운영 HTTPS/인증서·POSCO SSO·LIMS·실제 LLM·현장 원본 데이터·
수GB 업로드는 검증하지 않았다. XP 커널 재시작과 실제 장비의 메모리/드라이버 조건은
현장 확인 항목으로 남는다. 아래 공통 검증 절은 앞서 수행한 모의 시험을 별도로 기록한다.

VM 디스크, 브라우저, 합성 입력, 화면·JSON 결과, `final-regression.xml`과 시험 DB의
`test-db-snapshot.sql`은 저장소 밖의
`/Volumes/DATA/VMs/rist-xp-test.eVtP54/`에 보관했다. OS/브라우저 바이너리나 VM 디스크는
Git에 넣지 않는다. 같은 Mac에서 재실행할 때 테스트 DB의 기동 완료를 기다린 뒤
별도 터미널에서 서버와 VM을 실행한다:

```bash
docker start rist-xp-test-db
sh /Volumes/DATA/VMs/rist-xp-test.eVtP54/run-server.sh
# 다른 터미널
sh /Volumes/DATA/VMs/rist-xp-test.eVtP54/run-xp.sh
open vnc://127.0.0.1:5931
```

VM의 접속 주소는 `http://10.0.2.100:43310`이다. DB는 tmpfs이므로 컨테이너를 재시작하면
합성 회원/VOC가 초기화되며, 격리 서버의 bootstrap ID로 다시 가입해야 한다. 운영 계정과
데이터를 사용하지 않는다. 이 환경은 해당 Mac의 QEMU/Docker 및 보존된 Python 런타임에
의존한다. XP는 설치 직후 정품 인증 유예 상태로 시험했으며 인증 우회는 하지 않았다.

### Supermium 및 저사양 PC

Supermium 150은 네이티브 최신 화면을 사용한다. 구형 폴리필로 fetch/Pointer Events/Grid를
교체하지 않는다. 명시적인 Supermium UA/Client Hints 브랜드는 식별하되, 기본 설정에서
Chrome/Windows 10으로 표시될 수 있으므로 UA만으로 모든 Supermium을 식별할 수는 없다.
([공식 Client Hints 구현](https://github.com/win32ss/supermium/blob/main/client_hints.patch),
[실제 헤더 보고](https://github.com/win32ss/supermium/issues/1692))

브라우저 엔진 호환성과 자원 정책은 분리되어 있다. Supermium 여부, 구형 Windows 여부,
수동 식별 설정은 저사양 정책을 강제하지 않는다. 식별자가 숨겨져도 브라우저가 제공한
메모리(`deviceMemory`) 4GB 초과·논리 CPU(`hardwareConcurrency`) 2개 초과가 모두
확인되면 4MiB 순차 전송/목록 500개, 그 외에는 2MiB/200개를 사용한다.
어느 한 값이라도 미제공·유효하지 않으면 보수적으로 자원 절약 모드를 적용한다.
이는 대략적인 자원 힌트이며 실시간 여유 메모리/CPU 부하 측정이 아니다.
Chrome 49처럼 메모리 API가 없거나 비보안 HTTP 접속에서 정보가 제한되는 경우에도
기능을 막지 않고 작은 전송 조각을 사용한다. 명시적 브라우저 식별 모드는
`/ftir?browser=supermium` 또는 `/tem?browser=supermium`으로 선택하고 `?browser=auto`로
해제한다. localStorage를 차단한 PC에서는 URL 옵션이 있는 화면에만 유지된다.
이 설정은 Chrome 49의 호환 번들을 우회하거나 인증/SSO를 완화하지 않는다.

TEM 브라우저 검증 실행(이 디렉터리의 의존성을 설치한 뒤 `edge_api_server/`에서):

```bash
NODE_PATH=./browser_compat/node_modules RIST_TEST_PYTHON=/absolute/path/to/python node --test tests/tem_upload.test.cjs
python -m pytest tests/test_tem_resources.py tests/test_browser_support.py tests/test_browser_compat.py
```

32비트 XP의 실제 메모리 한계, TLS, GPU/렌더러는 현대 Chrome의 UA·자원 정보·CPU 제한
시험으로 재현되지 않는다. Supermium 150 R2는 배포 당시 pre-release이므로 현장 장비에서
파일 규모별 검증 후 적용한다. [공식 릴리스](https://github.com/win32ss/supermium/releases/tag/v150-r2)

### 공통 검증

2026-10-10 자원 정책 분리 검증: Python 612개와 JavaScript 22개 테스트를 통과했다.
격리 Chrome에서 Supermium 명시 UA/Client Hints/수동 설정, Chrome으로 표시되는 경우,
최신 Chrome, Chrome 49 UA와 고사양·저사양·자원 정보 누락을 조합한 9개 시나리오를
확인했다. 같은 약 30MiB TEM ZIP을 고사양 모의에서는 4MiB씩 8조각, 저사양 모의에서는
2MiB씩 15조각으로 전송하여 모두 보고서 생성·다운로드와 ZIP CRC를 확인했다.
목록 200/500개 제한, 수동 식별 설정의 페이지 간 유지·해제, 최신/호환 자산 선택,
FTIR 민감도 및 Crop/Y 이동 배타 동작도 확인했다. 실제 XP/Supermium 실기 시험은 아니다.

2026-10-10 추가 검증: Supermium 명시 UA와 Chrome으로 표시되는 경우의 수동 설정,
최신 Chrome, Chrome 49 기능 제한 모의 환경에서 가입·로그인, FTIR 분석·피크 민감도·
Y 이동/Crop 배타·PNG/ZIP 저장, TEM ZIP 업로드·PPTX 패키지 다운로드, 계정 변경,
VOC 작성·조치·확인을 실행했다. Supermium 모의는 CPU 6배 지연 상태로 수행했고,
약 96MiB ZIP(합성 STEM TIFF 8개)을 49개 조각으로 전송해 모두 보고서에 반영했다.
ZIP CRC도 확인했다. 이것은 macOS의 별도 Chrome 프로필 시험이며 실제 XP/Supermium
바이너리, 운영 SSO/LIMS/LLM을 실행했다는 의미가 아니다.

메모리 회귀는 32MiB ZIP 멤버 검증의 Python 추적 할당 피크 24MiB 미만,
8MiB 조각 수신 5MiB 미만을 확인한다. 이는 **해당 함수의 Python 할당** 기준으로,
프로세스 전체 RSS·이미지 라이브러리의 네이티브 할당·실제 XP 브라우저 메모리와 다르다.
대용량 CRC의 256KiB 읽기/이벤트 루프 양보, 3,000개 파일 목록의 DOM 제한, 조각 재시도,
ZIP/이미지/디스크/대기열 제한, 완료 요청 경합 및 검증 실패 차단을 자동 회귀로 포함했다.

자동 검증: 템플릿/해시 동기화, UA 분기, ES5 구문, 미지원 API 폴백, multipart와
로그인 쿠키, 요청 취소, Pointer Events, 인증/업로드/보고서 회귀 테스트.
대체 브라우저 검증: 최신 Chrome에 구형 UA를 적용하고 실제로 없는 API를 제거하여
FTIR 분석·민감도·Y 드래그·Crop·이미지/보고서 저장, TEM 업로드/보고서 저장 등을 확인한다.
이 시험은 XP의 실제 렌더러, 32비트 메모리, TLS/인증서를 재현하지 않는다.

2026-10-09 검증: 격리 MariaDB를 포함한 Python 519개, 호환 런타임 7개와 VOC
JavaScript 7개 테스트 통과. 별도 Chrome 프로필에서 일반 모드와 미지원 API를 제거한
호환 모드 각각 가입·로그인, FTIR 민감도/Y 이동/Crop/PNG·보고서 저장, 약 30MiB TEM
ZIP의 8개 조각 업로드·보고서 저장, 계정 수정, VOC 등록·조치 완료·작성자 확인을 검증했다.
특히 Y 이동 중 Plotly의 마우스 확대가 중복 실행되지 않고 축 범위가 유지되는지 확인했다.
외부 LLM·실제 POSCO SSO·LIMS에 연결하지 않은 합성 데이터 검증이다.

현장 확인은 **실제 XP + Chrome 49.0.2623.75**에서 별도로 해야 한다:

1. HTTPS/인증서 연결, 가입·승인·로그인·로그아웃.
2. 실제 FTIR DPT의 분석, 피크/범례 편집, Crop/Y 이동, 이미지/보고서 다운로드.
3. 실제 TEM 파일/폴더/ZIP과 큰 파일의 청크 업로드, 진행률, 실패 후 재시도.
4. VOC 등록·조치·작성자 확인, POSCO SSO 및 보고서 전송 승인.

XP/Chrome 49는 보안 지원이 종료된 환경이다. 실험망을 격리하고 인터넷 노출을 피한다.
화면 호환성을 위해 TLS, 인증서 검증, SSO 정책을 끄지 않는다. 아주 큰 파일은 XP의
메모리/브라우저 한계가 있으므로 현장 파일 규모로 확인하고 필요하면 C# 전송을 사용한다.
