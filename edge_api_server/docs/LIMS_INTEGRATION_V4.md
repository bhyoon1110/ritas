# LIMS v4 연계 — 2026-11-04

> 11월 6일 확정된 수신자 조회, 장비·미완료 시험 검색 및 문서 등록 규격은 [v5 가이드](LIMS_INTEGRATION_V5.md)를 따른다.

기준: 강주영 책임의 11월 4일 회신과 `RITAS_AX_API_명세_v4.xlsx` 8개 시트. 이 문서는 11월 2일 선행 구현 문서의 미확정 사항을 갱신한다. 실제 LIMS 호출이나 운영 파일 삭제를 수행하는 문서가 아니다.

## 구현과 운영 경계

- Python은 사용자 인증과 의뢰정보 실시간 조회를 내부 Spring에 요청한다. 외부 `X-Authorization` 토큰은 Spring에만 둔다. 인증 ID/비밀번호는 Spring→LIMS에서 각각 UTF-8 Base64로 한 번 인코딩한다.
- Python의 보고서 ZIP은 공유 저장소에 두고 Edge MariaDB 큐에 상대 경로·크기·SHA-256을 등록한다. Spring은 SFTP 전송, 문서 등록, 적재 확인, 메일 접수, 메일 상태 조회를 수행한다.
- 파일 적재 `COMPLETED`에서 등록 회차·파일명·크기가 일치하는 `fileNo`를 확인해야 로컬 파일을 정리할 수 있다. API2 등록 성공만으로 삭제하지 않는다. LIMS 첨부 보관 기간은 변경하지 않는다.
- `SENT`는 LIMS의 메일 서버 전달 완료다. 수신자의 수신 확인을 뜻하지 않는다. Edge 큐 등록과 LIMS 메일 큐 접수는 별도 상태로 표시한다.
- 보고서 재생성 운영 기능은 활성화하지 않는다. C#과 Oracle 직접 연결은 이번 흐름에 포함되지 않는다.

## 의뢰정보 조회

`GET /api/v1/requests`는 기존 기본값인 `source=cache`를 유지한다. `source=lims`는 Spring `GET /api/lims/requests`를 통해 실시간 조회한다. 전체 동기화 캐시를 부분 조회 결과로 교체하지 않는다.

공통 조건: `reqNumber`, `testMtdCode`, `fromDate`, `toDate`, `page`, `pageSize`. 실시간 조건에는 `testChargerNo`, `testChargerId`가 추가된다. 모든 조건은 AND다. 날짜는 포함 범위, 페이지는 1부터, 페이지 크기는 1~1000이다. 응답 `totalCount`는 전체 조건 건수다. 실시간 결과의 null 필드를 허용한다.

기존 캐시의 `experimentType`, `includeCompleted` 의미는 유지한다. 실시간에서는 임의 상태코드나 장비 매핑을 추정하지 않는다. 시험방법코드로 조회하며, 담당자 ID 조건은 캐시에 필요한 ID가 없으므로 실시간 조회에서만 지원한다. FTIR/Raman/TEM/XRD 의뢰 선택 화면에서 조회 출처와 조건을 선택한다.

## 재시도와 장기 대기

문서/메일은 기본 60초 간격으로 조회한다. 각 단계 10회 이후에는 `needsReview`와 식별자를 표시하고 600초 간격으로 전환한다. 문서 `FAILED`도 LIMS에서 회복될 수 있어 같은 문서 번호를 재조회한다. 새 문서를 자동 생성하지 않는다.

실패 메일의 수동 재시도는 기존 `POST /api/v1/report-management/{reportId}/retry`를 사용한다. 인증 사용 환경에서 관리자·보고서 전송 권한·최근 SSO 인증을 모두 확인한다. DB 행 잠금으로 동일 클릭이 중복 의도로 등록되지 않는다. 문서·첨부를 다시 보내지 않고 기존 requestId의 무본문 `/retry`를 호출한다.

Spring은 재시도 의도를 전송 전에 영구 기록한다. 응답이 유실되면 GET으로만 확인하며 같은 의도를 반복 POST하지 않는다. `PENDING`·`SENT` 상태의 시도 횟수를 재설정하지 않는다. 판단이 불확실하면 확인 필요로 남겨 명시적인 후속 조치를 요구한다. 수신 주소·제목·본문을 변경할 때는 기존 retry로 수정할 수 없으며 새 보고서 전송 요청으로 접수해야 한다.

상태 JSON에는 단계·조회 횟수·식별자·오류와 최초 메일 POST의 불변 스냅샷을 보관한다. 공개 상태 API는 허용된 상태 필드만 반환하며 원본 JSON·메일 내용·주소·토큰을 노출하지 않는다. 비밀번호와 외부 토큰은 상태 DB에 저장하지 않는다.

## 24시간 파일 보관

사용자가 선택한 기준은 LIMS 적재 확인 시각부터 24시간 보관 후 삭제다. `RIST_REPORT_COMPLETED_RETENTION_HOURS=24`, `RIST_REPORT_TRASH_RETENTION_HOURS=0`을 기본 적용한다. 기존 DB의 90일/휴지통 7일 값은 시간 설정이 있는 동안 적용되지 않는다. 시간 값을 명시적으로 비우면 기존 일수 정책으로 돌아간다. 관리 화면은 적용 중인 시간과 환경설정 우선 여부를 표시한다.

자동 정리 주기에 만료 파일을 휴지통으로 이동하고 같은 주기에 물리 삭제한다. 정확히 24시간 시각에 실행되는 타이머는 아니다. 보고서 보존 지정, 직접 지정한 보관 기한, 아직 적재가 확인되지 않은 파일, 다른 보고서가 사용하는 파일은 보호한다. 메일 대기 중에도 적재가 확인된 파일은 정리할 수 있고 이후 메일 조회·재시도는 fileNo만 사용한다.

공통 업로드 원본과 ZIP은 보고서 아티팩트로 관리한다. 전문 분석 작업공간의 전송 원본·생성 사본은 서버가 기록한 소유 정보로 연결하며, 생성 이후 변경되거나 사용 중인 작업은 정리를 보류한다. 브라우저가 보낸 경로로 삭제 대상을 추가하지 않는다. 기존 등록 이력이 없는 과거 작업공간이나 독립적인 편집 초안을 무조건 검색·삭제하지 않는다.

## 첨부 용량

`RIST_REPORT_MAIL_MAX_ATTACHMENT_BYTES`, `RIST_REPORT_MAIL_MAX_TOTAL_ATTACHMENT_BYTES`로 단일/합계 원본 첨부 크기를 제한한다. Spring에도 같은 의미의 `RITASAX_LIMS_MAIL_MAX_ATTACHMENT_BYTES`, `RITASAX_LIMS_MAIL_MAX_TOTAL_ATTACHMENT_BYTES`를 설정한다. 현재 한 메일의 첨부는 한 ZIP이며, 설정 한도 초과는 큐 등록 또는 SFTP 전송 전에 차단한다.

0은 LIMS 제한이 미확정이라는 의미다. 입력 업로드 한도나 Spring의 일반 ZIP 제한은 LIMS 메일 한도와 다르다. Base64/MIME 전송 크기 기준은 위존 회신 후 별도로 반영해야 한다.

## 설정과 배포

Python `edge.env.example`의 v4 항목을 기존 환경설정에 선택적으로 반영한다. 내부 공유 키는 32자 이상으로 Python과 Spring을 일치시킨다. 동일 서버는 명시적으로 허용한 `http://127.0.0.1:8888`을 사용할 수 있고, 원격 연결은 HTTPS를 사용한다. 인증 모드는 `RIST_SSO_MODE=lims`로 전환한다. 실시간 조회는 `RIST_LIMS_REQUESTS_ENABLED=true`와 Spring의 `RITASAX_LIMS_REQUEST_PROXY_ENABLED=true`가 모두 필요하다.

Spring 외부 base는 `https://tsritas.rist.res.kr/RIST/api/ax`다. `X-Authorization` 헤더를 사용하며 실제 토큰은 별도 전달 후 설정한다. 상세 env는 Spring 저장소의 v4 문서를 따른다. Python과 Spring을 함께 배포하고, 기존 11월 2일 Edge MariaDB 마이그레이션이 적용되어 있는지 확인한다. v4 상태 확장은 기존 JSON 컬럼을 사용하며 추가 Oracle 컬럼은 요구하지 않는다.

실서버 점검 전에는 전송 큐를 비활성으로 유지한다. 회신받은 토큰·테스트 계정으로 인증/조회부터 확인하고, SFTP 복구 후 파일→문서→메일 전체 시험을 진행한다.

## 남아 있는 외부 정보

실제 연계 토큰, SFTP 복구 및 계정·호스트키, 의뢰조회 개발 반영 여부, 수신자 이메일 조회 규격, 첨부/전체 메일 용량 기준, 장비·상태 코드 기준, 기존 문서 등록 API2 규격 유지 확인이 필요하다. Python은 신뢰할 수 있는 To 주소 연계가 없으면 임의로 주소를 만들지 않는다. 통합시험의 합성 수신자는 모의 환경에만 주입하며 운영 수신자 연계가 완성됐다는 뜻이 아니다.

실제 MariaDB·Python·Spring과 모의 HTTPS LIMS/SFTP를 사용하는 재현 가능한 시험은 `tests/integration/README.md`를 따른다. Windows XP/7/10/11의 실기 시험과 실제 LIMS 발송은 자동 시험과 별도로 수행해야 한다.
