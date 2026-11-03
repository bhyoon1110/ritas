# LIMS 연계 선행 구현 — 2026-11-02

> 이 문서는 11월 2일 당시 기록이다. 현재 v4 구현·설정·남은 지원 항목은 [LIMS_INTEGRATION_V4.md](LIMS_INTEGRATION_V4.md)를 따른다.

기준: 11월 2일 수신 API 명세와 기존 API1/API2 협의. 실제 LIMS 호출·메일 발송·운영 DB 변경은 하지 않았다. 문서/메일 연계와 SSO 중계는 기본 비활성화다. 기존 운영 env를 이 예시 파일로 덮어쓰지 않는다.

## 구현된 흐름

1. Python의 기존 SSO 확인 화면 → Spring `POST /api/lims/auth/verify` → LIMS `POST {base}/auth/verify`. JSON은 `userId`, `password`. HTTP 200의 `result=SUCCESS/FAIL`을 판정하며 비밀번호와 응답 본문을 로그·DB에 기록하지 않는다. 기존 POSCO/OIDC 모드는 유지한다.
2. 보고서 전송 큐 → SFTP → 기존 문서 등록 API → 문서 적재 상태 조회 → 등록한 `regenSeq`·파일명·크기의 `fileNo` 확인 → 메일 접수 → 발송 결과 조회. 문서 등록 또는 메일 접수만으로 전송 완료 처리하지 않는다. 메일 `SENT`에서만 큐를 완료한다.
3. 메일 요청은 `to/cc/bcc/subject/htmlBody/attachFileNos/requestId`. Python의 CID 보관 형식을 data URI HTML로 변환한다. 주소는 다운로드 ZIP에 넣지 않는다. 기존 CC/BCC의 `@rist.re.kr` 제한은 유지한다.
4. 단계 상태와 ID를 Edge MariaDB에 저장한다. 재시작 후 문서를 다시 업로드하지 않고 후속 조회를 재개한다. 메일 POST 직전 상태를 저장하고 타임아웃 시 동일 `requestId`를 재사용한다. `DUPLICATE`는 별도 GET으로 실제 상태를 확인한다.
5. 정상 폴링은 실패 횟수에서 제외한다. `attempt_count`는 이력 번호로 계속 증가하고 `failure_count`만 연속 오류를 센다. 수동 재시도는 이력 번호를 초기화하지 않는다. 기본 폴링은 60초이며 10회 경과만으로 실패 처리하지 않는다.
6. 영구 적재 확인 시각 이후 기존 보존 설정에 따라 로컬 파일을 정리한다. 메일 상태 확인에 파일은 필요하지 않다. 적재 미확인 자료와 과거 등록만 완료된 자료는 자동 삭제하지 않는다. 1,000건 이후 자료도 정리 대상으로 검사한다.
7. API1 전체 동기화는 누락·반복 페이지·중복 업무키·변경된 `totalCount`를 검출하면 기존 목록을 유지한다. 외부 조회 필터는 추정해 전송하지 않는다.

## 아직 연결하지 않은 부분

- **To 수신자 조회 규격**: 현재 Python은 기존 정책대로 브라우저가 지정한 To를 버리고 LIMS에서 해결하도록 되어 있다. API1에 이메일 필드도 별도 수신자 API도 없어 실제 발송은 `LIMS_RECIPIENT_UNRESOLVED`로 차단한다. 임의 주소나 사용자 ID로 이메일을 생성하지 않는다. LIMS의 주소 필드/조회 API가 정해지면 신뢰된 수신자 해석기를 추가해야 한다.
- SSO와 문서 상태 조회 API의 배포 여부, 최종 HTTPS 호스트·포트·prefix, CA, 시험 계정, X-Authorization 실제 값/접두어/적용 범위, SFTP 계정·호스트키·방화벽 확인이 필요하다.
- 장비·실험자·기간·상태 필터와 코드/ID 연결 규격이 필요하다. 현재 전체 조회와 기존 로컬 조회만 유지한다.
- `retryMail` 호출 어댑터는 준비했으나 **자동 재발송에는 연결하지 않았다**. POST 요청/응답과 재시도 한도·타임아웃 시 중복 동작을 확정해야 한다. LIMS `FAILED`인 건을 수동 재큐잉해도 현재 상태만 확인하며 재발송하지 않는다.
- LIMS 첨부/전체 메일 용량 한도는 확인 후 반영한다. Python의 기존 이미지 제한은 유지하며 Outlook data URI 표시 호환성은 별도 확인 대상이다.
- 보고서 재생성의 운영 활성화는 이번 작업 범위 밖이다.

## Edge DB 적용 순서

공유 DB는 **Edge MariaDB**다. Oracle/LIMS 스키마 변경 SQL이 아니다.

1. Spring 전송 큐를 `RITASAX_REPORT_TRANSFER_ENABLED=false`로 유지한다.
2. Python 저장소 `edge_api_server/deploy/mariadb_lims_delivery_migration.sql`을 Edge DB에 적용한다. 신규 설치 스키마와 Python 초기화 경로에도 같은 컬럼을 반영했다. Spring 저장소에는 `src/main/resources/schema/migration-lims-delivery-20261102.sql`로 같은 SQL을 제공한다. 둘 중 한 번만 적용하면 된다(반복 실행 가능).
3. Python과 Spring 코드를 함께 반영한다. Spring은 큐 기동 시 새 컬럼 존재를 검사한다.
4. 실제 연계와 수신자 규격을 확인한 뒤 `RITASAX_LIMS_MAIL_WORKFLOW_ENABLED=true`, `RITASAX_REPORT_TRANSFER_ENABLED=true`로 활성화한다. 이전 `COMPLETED` 행을 임의로 새 메일 큐에 재등록하지 않는다.

추가 컬럼: `failure_count`, `delivery_state_json`, `document_status`, `mail_status`, `document_completed_at`. 상태 JSON에는 ID·단계만 저장하며 인증정보·메일 본문·수신자 주소를 넣지 않는다. 기존 보존 일수 기본값은 바꾸지 않았으므로, 실제 용량 정책에 맞게 관리 화면/환경설정에서 조정해야 한다. 휴지통 보존 기간까지 지나야 물리적으로 삭제된다.

## 설정 예시

Spring의 `RITASAX_LIMS_API_BASE_URL`에는 최종 확인한 HTTPS base와 `/RIST/api/ax`를 넣는다. 경로 설정의 기본값은 `/requests`, `/documents`, `/mails`, SSO `/auth/verify`이므로 prefix를 중복하지 않는다. 기존 HTTP/1004 예시는 확정된 주소가 아니므로 배포 예시에서 제거했다. 서비스용 키는 기본 `X-Authorization` 헤더로 보내며, 받은 값에 `Bearer `가 필요한지 확인한 후 값을 그대로 설정한다. 자동 JWT 갱신은 구현하지 않았다.

Python:

```dotenv
RIST_SSO_MODE=lims
RIST_SSO_READ_TIMEOUT_SECONDS=40
RIST_SSO_LIMS_URL=https://<Spring-host>/api/lims/auth/verify
RIST_SSO_LIMS_INTERNAL_KEY=<32자 이상 내부 공유 키>
RIST_SSO_LIMS_CA_BUNDLE=<필요한 경우 CA PEM 경로>
RIST_SSO_LIMS_ALLOW_LOOPBACK_HTTP=false
```

Spring:

```dotenv
RITASAX_LIMS_SSO_ENABLED=true
RITASAX_LIMS_SSO_INTERNAL_KEY=<Python과 동일한 내부 공유 키>
RITASAX_LIMS_SSO_VERIFY_PATH=/auth/verify
RITASAX_LIMS_SSO_SEND_API_KEY=false
RITASAX_LIMS_API_AUTH_HEADER=X-Authorization
RITASAX_LIMS_MAIL_WORKFLOW_ENABLED=false
RITASAX_REPORT_TRANSFER_ENABLED=false
RITASAX_LIMS_POLL_INTERVAL_SECONDS=60
```

SSO 내부 공유 키는 외부 LIMS용 X-Authorization 값과 다른 비밀값이다. 동일 서버에서만 Python URL을 `http://127.0.0.1:8888/api/lims/auth/verify`와 `RIST_SSO_LIMS_ALLOW_LOOPBACK_HTTP=true`로 사용할 수 있다. 원격 HTTP는 거절한다. 브라우저 SSO의 기존 HTTPS 요구는 별도 유지한다. Spring→LIMS 인증은 HTTPS만 허용하며 JVM truststore로 인증서를 검증한다. Python→Spring CA 설정은 JVM truststore를 대신하지 않는다. 인증 API의 서비스 토큰 요구가 확인되면 `RITASAX_LIMS_SSO_SEND_API_KEY`를 맞춘다.

SFTP 파일은 보고서 생성일을 기준으로 `YYYYMM/YYYYMMDD_의뢰번호_시험코드_보고서ID.zip`에 저장한다. 재시도 날짜가 바뀌어도 같은 경로를 사용하고 보고서 ID로 이름 충돌을 막는다. SFTP 루트가 서버 `AX_FILES`에 매핑된다는 기존 협의가 실제 설정에도 적용되어야 한다.

## 검증 범위

새 코드의 자동 테스트는 모의 LIMS·SFTP 응답, API 계약, 실패/중복/타임아웃, 큐 상태, 정리 정책을 검증한다. 실제 LIMS 메일 발송, 실 서버 SSO, Oracle 변경, Windows XP/7/10/11 실기 검증은 수행하지 않았다. 테스트 결과는 작업 완료 보고에 기재한다.

검증 결과: Python 관련 테스트 276개 통과, 실제 MariaDB 연결이 필요한 13개 건너뜀. 브라우저 호환 빌드(65개 스크립트·16개 화면)와 JavaScript 테스트 48개 통과. Spring 전체 113개 테스트 통과. Spring의 기존 파일 검증 테스트는 macOS 임시 경로 별칭 대신 실제 경로를 사용하도록 수정했으며 운영 경로 검증은 유지했다.
