# Edge 웹 회원·권한 및 사내 SSO 연동 명세

## 1. 목적

FT-IR, Raman, XRD, TEM 웹 화면은 로컬 회원으로 로그인한 뒤 사용한다. 사내
SSO 연동이 준비되지 않은 기간에도 가입과 관리자 승인을 거쳐 보고서를 생성할
수 있지만, LIMS 전송은 사내 SSO 본인 확인이 완료된 사용자에게만 허용한다.

## 2. 권한 흐름

1. 사용자가 `/signup`에서 로그인 ID, 이름, 비밀번호로 가입한다. 이메일은 연락용
   선택 정보이며 로그인과 SSO 식별에 사용하지 않는다.
2. 관리자가 `/admin/users`에서 회원을 `ACTIVE`로 변경하고 FTIR, RAMAN, XRD,
   TEM 중 필요한 프로젝트를 승인한다.
3. 승인된 사용자는 `/login`으로 로그인하여 허용된 프로젝트의 파일 업로드,
   분석, 보고서 생성을 수행한다.
4. 보고서 전송 담당자에게는 관리자가 `REPORT_SENDER` 역할을 추가한다.
5. 사용자는 `/account`에서 사내 SSO 계정을 연결한다.
6. 전송 버튼을 누를 때 프로젝트 권한, `REPORT_SENDER`, SSO 연결, 최근 SSO
   인증 시각을 다시 검사한다.
7. 인증이 오래되었으면 SSO 재인증 후 전송한다. 전송 작업의 실험자는 화면의
   임의 입력값이 아니라 SSO의 사번, `sub` 순서로 결정한다.

회원 상태는 `PENDING`, `ACTIVE`, `SUSPENDED`로 관리한다. `ADMIN` 역할은 회원과
권한 관리 및 운영 관리 화면 접근을 허용한다. 관리자는 모든 프로젝트 화면에
접근할 수 있지만, 보고서 전송에는 관리자도 `REPORT_SENDER`와 SSO 인증이
필요하다.

## 3. 최초 관리자

운영 배포 전 `RIST_AUTH_BOOTSTRAP_ADMIN_IDS`에 최초 관리자 로그인 ID(권장값
`admin`)를 지정한다. 이 값이 있으면 해당 ID로 가입한 회원만 자동으로 활성화되고
`ADMIN`, `REPORT_SENDER`, 전체 프로젝트 권한을 받는다. 값이 비어 있는 초기
설치에서는 첫 가입자 한 명이 최초 관리자가 된다.

최초 관리자 생성 후에는 환경 변수에서 ID를 제거해도 기존 권한은 유지된다.

## 4. 화면 및 API

| 주소 | 용도 |
|---|---|
| `/signup` | 로컬 회원 가입 |
| `/login` | 로컬 로그인 |
| `/account` | 회원정보·비밀번호 변경, 로그아웃, 승인 프로젝트 및 SSO 상태 확인 |
| `/admin/users` | 회원 상태, 프로젝트, 역할 관리 |
| `/auth/sso/start` | 설정된 방식의 SSO 재인증 시작. POSCO 모드는 계정 화면으로 이동 |
| `/api/v1/auth/sso/verify` | POSCO SSO ID/PW 서버측 검증 및 현재 세션 재인증 |
| `/auth/sso/callback` | 선택적 OIDC Authorization Code + PKCE 콜백 |
| `/api/v1/auth/me` | 현재 로그인 사용자와 권한 조회 |

운영 관리 화면은 `사용 기록 | 오류 기록 | 보고서/파일 관리 | 회원 관리` 네 탭으로
구성한다. 회원 관리에서는 가입 승인 상태, FTIR/RAMAN/XRD/TEM 접근권한,
`ADMIN`·`REPORT_SENDER` 역할, SSO 연결·활성 여부, 사번과 최근 SSO 인증 시각을
한 화면에서 조회하고 변경한다.

`/account`에서는 이름과 선택 연락 이메일을 수정하고 비밀번호를 변경하거나
로그아웃할 수 있다. 비밀번호를 변경하면 탈취된 세션이 남지 않도록 기존 로그인
세션을 모두 종료하고 새 비밀번호로 다시 로그인하게 한다. 사용 기록, 오류 기록,
보고서/파일 관리 목록은 서버 측 페이징으로 조회하며 기본 25건, 선택 가능한 크기는
25·50·100건이다.

실험 PC의 C# 클라이언트가 사용하는 `/api/v1/jobs`, `/api/v1/jobs/{jobId}/...`,
`/api/v1/requests`는 브라우저 회원 쿠키 인증과 분리한다. 기존
`X-Request-Id`와 클라이언트 식별 헤더 정책을 그대로 사용한다.

C# 보고서 worker는 생성된 ZIP을 `report_runs`에 등록하되 전송 큐는 만들지
않는다. `GET /api/v1/jobs/{jobId}`가 반환하는 `/reports/{reportId}` 검토 화면은
브라우저 로그인이 필요하다. 사용자가 ZIP을 확인한 뒤 전송 버튼을 누를 때
프로젝트 권한, `REPORT_SENDER`, 연결된 SSO 계정과 최근 SSO 인증을 검사하고,
통과한 경우에만 `report_transfers(PENDING)`를 생성한다. C# 프로그램은 SSO
ID/PW를 취급하거나 전송 API를 직접 호출하지 않는다.

## 5. DB 테이블

| 테이블 | 역할 |
|---|---|
| `app_users` | 로그인 ID, 선택 연락 이메일, 로컬 회원과 승인 상태 |
| `user_project_permissions` | 회원별 FTIR, RAMAN, XRD, TEM 권한 |
| `user_roles` | `ADMIN`, `REPORT_SENDER` 역할 |
| `sso_identities` | 로컬 회원과 사내 SSO 사번 또는 OIDC 계정 연결 |
| `auth_sessions` | 해시된 로그인 토큰과 최근 SSO 인증 시각 |
| `auth_oidc_states` | PKCE 인증 중 일회성 state와 verifier |
| `auth_audit_events` | 가입, 로그인, 권한 변경, SSO 연결 감사 기록 |

스키마 적용:

```bash
cd ~/ritas/edge_api_server/deploy
mysql --default-character-set=utf8mb4 \
  -h 127.0.0.1 -P 3306 \
  -u root -p rist_edge \
  < mariadb_auth_migration.sql
```

이 스크립트는 기존 회원 데이터를 삭제하지 않는다. 이메일 로그인 버전에서 다시
실행하면 기존 이메일 전체를 `login_id`로 이관하므로 종전 이메일 문자열을 로그인
ID 칸에 입력해 계속 로그인할 수 있다. 관리자는 이후 원하는 신규 ID의 계정을
만들고 권한을 이전할 수 있다.

관리자 행의 상태만 DB에서 수동으로 `ACTIVE`로 바꾼 경우에는 `ADMIN` 역할이 없어
회원 관리 화면에 접근할 수 없다. 다음 보정 스크립트는 로그인 ID `admin`에 관리자,
보고서 전송, 전체 프로젝트 권한을 중복 없이 부여한다.

```bash
cd ~/ritas/edge_api_server/deploy
mysql --default-character-set=utf8mb4 \
  -h 127.0.0.1 -P 3306 \
  -u root -p rist_edge \
  < mariadb_grant_admin.sql
```

적용 후에는 로그아웃했다가 다시 로그인한다.

## 6. POSCO SSO 연동 방식

이 프로젝트의 기본 SSO 방식은 브라우저 리다이렉트가 아니라 POSCO가 제공한
ID/PW 정합성 확인 API다. 사용자가 `/account`에서 SSO ID와 비밀번호를 입력하면
브라우저는 Edge 서버에만 전달하고, Edge 서버가 다음 값을
`application/x-www-form-urlencoded` HTTPS POST로 SSO 서버에 전송한다.

- `username`: 사용자의 SSO ID
- `password`: 사용자의 SSO 비밀번호
- `sid`: SSO 담당자가 이 시스템에 발급한 SID

응답 본문을 공백 제거 후 `T` 또는 `F`로만 판정한다. `T`이면 로컬 회원과 사번을
연결하고 현재 로그인 세션의 최근 SSO 인증 시각을 갱신한다. `F`는 매뉴얼상 ID/PW
불일치와 미등록 출발지 IP를 구분하지 않으므로 사용자 화면에도 두 가능성을 함께
안내한다. 비밀번호는 DB, 세션, 감사 로그, 사용 기록, 오류 아카이브에 저장하지
않는다.

Java 예제에 포함된 trust-all `X509TrustManager`와 무조건 `true`인
`HostnameVerifier`는 적용하지 않는다. 서버 인증서와 호스트 이름을 정상 검증하며,
사내 CA가 OS 신뢰 저장소에 없을 때만 `RIST_SSO_CA_BUNDLE`로 CA PEM을 추가한다.
리다이렉트는 따라가지 않으며 URL은 HTTPS만 허용한다.

브라우저에서 Edge까지의 구간도 반드시 HTTPS여야 한다. Edge 공개 주소가 HTTP이면
계정 화면에서 SSO 비밀번호 입력창을 표시하지 않고, 검증 API도 요청 본문을 처리하기
전에 `426 SSO_HTTPS_REQUIRED`로 차단한다. TLS를 Edge에서 직접 종료하거나 신뢰된
리버스 프록시에서 종료한 뒤 `RIST_EDGE_PUBLIC_BASE_URL=https://...`와
`RIST_AUTH_COOKIE_SECURE=true`를 함께 적용한다.

## 7. 환경 설정

서버별 값은 `/home/rist/ritas/edge.env`에서 관리한다.

```dotenv
RIST_AUTH_ENABLED=true
RIST_AUTH_SESSION_HOURS=12
RIST_AUTH_RECENT_SSO_MINUTES=30
RIST_AUTH_COOKIE_SECURE=true
RIST_AUTH_BOOTSTRAP_ADMIN_IDS=admin

RIST_SSO_MODE=posco
RIST_SSO_PROVIDER_NAME=POSCO SSO
# 개발계
RIST_SSO_VALIDATION_URL=https://uswpsso.posco.net/idms/U61/jsp/userValidSSOM.jsp
# 운영 전환 시 다음 URL로 명시적으로 변경
# RIST_SSO_VALIDATION_URL=https://swpsso.posco.net/idms/U61/jsp/userValidSSOM.jsp
RIST_SSO_SID=issued-system-sid
RIST_SSO_CA_BUNDLE=
RIST_SSO_CONNECT_TIMEOUT_SECONDS=2
RIST_SSO_READ_TIMEOUT_SECONDS=4
```

`RIST_SSO_SID`는 실제 발급값으로 바꾸되 Git 저장소에는 커밋하지 않는다. 설정을
바꾼 뒤 서비스를 재시작한다. 기존 OIDC 연동을 사용하는 별도 환경만
`RIST_SSO_MODE=oidc`와 `RIST_SSO_ISSUER_URL`, `RIST_SSO_CLIENT_ID`,
`RIST_SSO_CLIENT_SECRET`, `RIST_SSO_SCOPES`를 설정한다. 이 경우 SSO 담당자에게
등록할 Redirect URI는 다음과 같다.

```text
${RIST_EDGE_PUBLIC_BASE_URL}/auth/sso/callback
```

SSO를 사용하지 않는 로컬 기능 개발에서만 `RIST_AUTH_COOKIE_SECURE=false`를 쓸 수
있다. POSCO SSO 활성화 전에는 Edge HTTPS를 적용하고 반드시 `true`로 변경한다.
환경 설정을 바꾼 뒤 서비스를 재시작한다.

```bash
sudo systemctl restart rist-edge-api.service
sudo systemctl status rist-edge-api.service
```

## 8. SID·네트워크 사전 작업

SSO 담당자에게 SID를 신청할 때 회사, 모든 Edge 서버 IP, 서비스 도메인, 시스템명,
서비스 설명, 운영 담당자, 담당 PM, 정보조회대상을 제출한다. NAT를 거치면 서버의
사설 IP가 아니라 SSO 서버에서 관측되는 NAT 출발지 IP도 반드시 등록해야 한다.
등록되지 않은 IP에서 호출하면 올바른 ID/PW도 `F`가 반환될 수 있다.

매뉴얼의 DNS/hosts 값은 다음과 같다. 실제 운영망 구분과 최신 값은 적용 전에 SSO
담당자에게 다시 확인한다.

| 구분 | 패밀리망 IP | 포스코망 IP | SSO 호스트 |
|---|---:|---:|---|
| 개발·테스트 | `10.132.18.34` | `172.31.143.34` | `uswpsso.posco.net` |
| 운영 | `10.132.18.41` | `172.31.143.41` | `swpsso.posco.net` |

Edge 서버에서 선택한 호스트가 올바르게 해석되고 TCP 443에 연결되는지 확인한다.
`telnet` 대신 인증서와 SNI까지 확인할 수 있는 `openssl s_client` 또는 `curl`을
권장한다. 실제 ID/PW와 SID를 셸 명령줄 인수나 셸 기록에 남기지 않는다.

## 9. 권장 적용·검증 순서

1. 인증 DB 마이그레이션을 실행한다.
2. 최초 관리자 로그인 ID와 쿠키 설정을 `edge.env`에 추가한다.
3. 서비스를 재시작하고 최초 관리자로 가입한다.
4. 일반 회원 가입, 승인, 프로젝트별 보고서 생성을 검증한다.
5. SSO 담당자에게 개발계 SID와 Edge/NAT 출발지 IP를 등록한다.
6. 개발계 DNS, TCP 443, TLS 인증서 체인을 확인하고 URL·SID·필요 시 CA를 설정한다.
7. 테스트 계정으로 `T`, 잘못된 비밀번호로 `F`, 미등록 IP 환경에서 `F`를 각각 확인한다.
8. 인증 직후 보고서 전송, 최근 SSO 인증 만료 후 차단 및 재인증을 검증한다.
9. 운영 SID와 운영 출발지 IP를 별도로 등록한 뒤 운영 URL로 전환한다.

## 10. 로그인·회원가입의 구형 브라우저 호환성

로그인·가입 화면은 `fetch`, `async/await`, `FormData.get`, `Object.fromEntries`
의존성을 제거하고 기본 HTML 양식으로 동작한다. `GET /login`, `GET /signup`에서
양식을 받고, 같은 주소에 `application/x-www-form-urlencoded` POST로 제출한다.
비밀번호는 URL/쿼리 문자열에 넣지 않으며 오류 화면에도 재표시하지 않는다.
기존 `/api/v1/auth/login`, `/api/v1/auth/signup` JSON API는 그대로 유지한다.

- 입력 검증은 서버에서 동일한 Pydantic 모델로 수행한다. HTML5의 `required`,
  `minlength`를 지원하지 않는 브라우저도 비밀번호 길이 등의 정책을 우회할 수 없다.
- 가입 성공은 로그인 화면으로 303 리다이렉트한다. 새로고침으로 가입 요청이
  반복되지 않으며, 기존 관리자 지정·일반 회원 승인 대기 정책을 유지한다.
- 로그인 성공은 기존 인증 서비스가 발급한 HttpOnly 세션 쿠키를 설정하고 안전한
  내부 `returnTo` 경로로 303 리다이렉트한다. 쿠키에는 구형 브라우저를 위한
  `Expires`도 함께 설정하며 Secure/SameSite 정책을 낮추지 않는다.
- 인증 양식과 응답은 `Cache-Control: no-store`로 캐시하지 않는다. 오류가 나면
  ID·이름·이메일만 HTML 이스케이프하여 유지하고 비밀번호는 다시 입력하게 한다.
- HTML 폼에는 쿠키와 대조하는 CSRF 토큰을 넣는다. POST의 `Origin`이 없으면
  구형 IE가 보내는 `Referer`로 동일 출처를 확인한다. 둘 다 없거나 출처가 다르면
  거부한다. 따라서 쿠키를 허용하고 보안 프록시가 동일 출처 Referer를 제거하지
  않도록 해야 한다. 별도의 비밀키/DB 마이그레이션은 필요하지 않다.
- `RIST_AUTH_COOKIE_SECURE=true`인 배포에서 HTTP 주소로 접속하면 HTTPS 접속
  안내를 표시하고 폼 제출을 거부한다. 리버스 프록시는 Host를 보존하고 신뢰된
  프록시에서 전달한 HTTPS scheme이 앱까지 반영되어야 한다.

### 적용 범위와 점검

이 절의 무스크립트 POST 보완은 로그인·회원가입에 해당한다. 별도로 추가한
Chrome 49 호환 모드는 `/account`, 실험·그래프·VOC·운영·보고서 검토 화면에도
변환된 JavaScript와 브라우저 API 폴백을 제공한다. 전체 시스템의 XP/IE 지원이나
구형 OS의 보안 지원을 보장하는 것은 아니다. 실제 XP Chrome 49에서의 파일 규모,
TLS/인증서와 SSO 연결은 별도로 확인한다.
계정 화면의 스크립트가 실패하면 기본 POST가 안전 안내 화면으로 이동하며,
비밀번호를 URL에 노출하거나 회원정보·비밀번호 변경·SSO 인증을 실행하지 않는다.

1. 서버에 수정 버전을 배포하고 API 서비스를 재시작한다.
2. 브라우저에서 로그인·가입 화면을 새로 연다. JavaScript를 꺼도 가입 신청,
   잘못된 입력 안내, 승인된 회원 로그인이 되는지 확인한다.
3. 화면은 열리지만 실패하면 화면 오류와 브라우저 버전을 확인한다. 로그인 후
   다시 로그인 화면으로 돌아오면 쿠키 차단, Secure 쿠키와 HTTP 주소 혼용,
   리버스 프록시의 Host/scheme 전달을 점검한다.
4. 화면 자체가 열리지 않으면 DNS/네트워크/인증서/TLS 문제부터 확인한다.
   브라우저나 OS가 서버의 보안 통신을 지원하지 못하는 경우 TLS 또는 인증서
   검증을 낮추지 말고 지원되는 OS·브라우저를 사용한다.

Windows 7용 Edge는 Microsoft의 지원 이력상 109가 마지막 버전이다.
이는 프로젝트 전체의 호환성 보장을 의미하지 않으며 Windows 7이라는 이유만으로
이번 장애를 JavaScript 미지원으로 단정해서는 안 된다.
참고: [Microsoft Edge 지원 운영체제](https://learn.microsoft.com/en-us/deployedge/microsoft-edge-supported-operating-systems).

## 11. Chrome 중심 지원 및 출시 검증

> Chrome 49 호환 모드 추가: XP 장비에는 Chrome 49.0.2623.75를 기준으로 호환 자산을
> 제공한다. Chromium 49~109와 EdgeHTML 요청에만 ES5/브라우저 API/CSS 폴백을 적용하며
> 최신 브라우저는 기존 코드로 실행한다. [빌드·배포·현장 검증](../edge_api_server/browser_compat/README.md)
> 절차를 따른다. 아래 기존 최신 브라우저 권장 정책과 HTTPS/SSO 조건은 유지한다.

최적화·출시 검증의 주 대상은 보안 지원 중인 OS의 최신 안정 버전 Chrome이다.
Windows 업무 PC는 Windows 11을 권장하며, 최신 Edge에서도 핵심 경로를 확인한다.
이는 브라우저별로 서로 다른 인증·권한 정책을 적용한다는 의미가 아니다.
특정 옛 Chrome 버전에 고정하지 않고 배포 검증 시 OS·브라우저·버전·확인 일자를
기록한다. Windows 7의 Chrome 109가 최신 지원 브라우저라는 뜻으로 안내하지 않는다.
참고: [Chrome 시스템 요구사항](https://support.google.com/chrome/a/answer/7100626?hl=ko).

모바일 반응형·터치 기능은 유지한다. Android Chrome 및 iOS Safari/Chrome의
실기기에서 Y축 이동 후 페이지 스크롤 복구, 파일 선택·다운로드를 확인한다.
모바일의 폴더 선택은 장치 제약이 있을 수 있으므로 기존 파일/ZIP 입력도 유지한다.

### 구현 범위

- 로그인·가입은 정적인 Chrome 중심 지원 안내와 기존 무스크립트 POST를 유지한다.
- 첫 화면, FTIR/Raman/XRD/TEM, 계정·회원 관리, 운영·보고서 관리·C# 보고서 검토
  화면에 공통 브라우저 안내를 넣는다.
- 안내 스크립트는 ES5로 작성한다. IE/구형 Edge, 알려진 구형 Windows,
  Chromium 109 이하 또는 필수 API 누락을 감지하면 이유를 표시한다. 호환 모드가
  적용된 경우에는 지원 제외 경고 대신 호환 모드 안내를 표시한다.
  Supermium/최신 엔진 + 구형 Windows는 최신 UI를 유지하고 OS 보안 안내만 표시한다.
  Supermium 브랜드(UA 또는 Client Hints)가 없으면 Chrome과 강제로 구분하지 않는다.
  `?browser=supermium`은 이 브라우저의 저사양 UI 정책 선택이며 localStorage에 저장한다.
  `?browser=auto`로 해제하고, 저장소 사용이 차단되면 현재 URL에서만 적용한다.
  이 선택은 서버 인증·권한·SSO·Chrome 49 자산 분기에는 영향을 주지 않는다.
  110 이상을 정식 최소 지원 버전으로 선언하는 것은 아니며, UA를 보안 판단에
  사용하지 않는다. 사용자 에이전트 위장·OS 식별 한계 때문에 경고 부재가
  호환성이나 보안 지원의 보증은 아니다.
- 안내는 원래 문서 흐름에 표시하여 그래프 위를 덮지 않는다. 정상 환경에서는
  감추고, 출력물·독립 다운로드 HTML·이미지/PDF에 안내를 추가하지 않는다.
- 차단·자동 이동·브라우저 설치·외부 통신은 하지 않는다. C# 전송 API, 기존
  권한·SSO 요구사항과 TLS/인증서 검증은 변경하지 않는다.

### 배포 전 확인 항목

자동 테스트 통과와 다음 실제 브라우저/운영망 확인은 구분해서 기록한다.

- [ ] 최신 Chrome: 가입 → 관리자 승인 → 로그인 → 권한별 화면 접근.
- [ ] 최신 Chrome: FTIR/Raman 업로드, 피크·범례·Crop·Y축 이동, 이미지 저장.
- [ ] 최신 Chrome: XRD/TEM 파일·폴더·ZIP 업로드, 진행률·오류·대용량 재시도.
- [ ] 최신 Chrome: HTML/PPTX/PDF/ZIP 다운로드와 원본 결과 확인.
- [ ] 최신 Edge: 위 핵심 경로에 대한 호환 확인.
- [ ] 모바일 실기기: 터치 스크롤 복구, 파일 선택·다운로드, 화면 넘침 확인.
- [ ] 사내 운영망: HTTPS/쿠키, POSCO SSO 인증·만료·재인증, 보고서 전송 큐 확인.
- [ ] C# 개발자: 장비별 OS·.NET 런타임·전송 경로·TLS 확인 및 최신 PC에서 reviewUrl 열기.

SSO 실계정과 운영망의 SID/IP 등록 확인은 담당자와 함께 수행한다. 자동 테스트나
Chrome 안내 추가만으로 이 항목들을 완료 처리하지 않는다.
