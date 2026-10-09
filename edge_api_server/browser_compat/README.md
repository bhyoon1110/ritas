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
- HTML 캐시는 User-Agent별로 분리한다. 개인별 VOC 설정은 공유 JS에 넣지 않는다.
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
