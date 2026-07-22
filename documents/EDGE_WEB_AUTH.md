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
| `/account` | 승인 프로젝트 및 SSO 연결 상태 확인 |
| `/admin/users` | 회원 상태, 프로젝트, 역할 관리 |
| `/auth/sso/start` | OIDC Authorization Code + PKCE 인증 시작 |
| `/auth/sso/callback` | OIDC 콜백 |
| `/api/v1/auth/me` | 현재 로그인 사용자와 권한 조회 |

실험 PC의 C# 클라이언트가 사용하는 `/api/v1/jobs`, `/api/v1/jobs/{jobId}/...`,
`/api/v1/requests`는 브라우저 회원 쿠키 인증과 분리한다. 기존
`X-Request-Id`와 클라이언트 식별 헤더 정책을 그대로 사용한다.

## 5. DB 테이블

| 테이블 | 역할 |
|---|---|
| `app_users` | 로그인 ID, 선택 연락 이메일, 로컬 회원과 승인 상태 |
| `user_project_permissions` | 회원별 FTIR, RAMAN, XRD, TEM 권한 |
| `user_roles` | `ADMIN`, `REPORT_SENDER` 역할 |
| `sso_identities` | 로컬 회원과 사내 OIDC 계정 연결 |
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

## 6. 환경 설정

서버별 값은 `/home/rist/ritas/edge.env`에서 관리한다.

```dotenv
RIST_AUTH_ENABLED=true
RIST_AUTH_SESSION_HOURS=12
RIST_AUTH_RECENT_SSO_MINUTES=30
RIST_AUTH_COOKIE_SECURE=false
RIST_AUTH_BOOTSTRAP_ADMIN_IDS=admin

RIST_SSO_PROVIDER_NAME=RIST SSO
RIST_SSO_ISSUER_URL=https://sso.example.com
RIST_SSO_CLIENT_ID=issued-client-id
RIST_SSO_CLIENT_SECRET=issued-client-secret
RIST_SSO_SCOPES=openid profile email
```

SSO 담당자에게 등록할 Redirect URI는 다음과 같다.

```text
${RIST_EDGE_PUBLIC_BASE_URL}/auth/sso/callback
```

현재 HTTP로 서비스하는 동안에는 `RIST_AUTH_COOKIE_SECURE=false`가 필요하다.
HTTPS 적용 후에는 반드시 `true`로 변경한다. 환경 설정을 바꾼 뒤 서비스를
재시작한다.

```bash
sudo systemctl restart rist-edge-api.service
sudo systemctl status rist-edge-api.service
```

## 7. 권장 적용 순서

1. 인증 DB 마이그레이션을 실행한다.
2. 최초 관리자 로그인 ID와 쿠키 설정을 `edge.env`에 추가한다.
3. 서비스를 재시작하고 최초 관리자로 가입한다.
4. 일반 회원 가입, 승인, 프로젝트별 보고서 생성을 검증한다.
5. 사내 OIDC 클라이언트 발급 후 issuer, client, redirect URI를 설정한다.
6. `REPORT_SENDER` 부여 전후와 최근 SSO 인증 만료 전후의 전송 차단을 검증한다.
