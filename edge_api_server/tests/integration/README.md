# LIMS v5 로컬 통합시험

`run_lims_v5.py`는 실제 Python API와 Spring JAR를 동일한 임시 MariaDB에 연결하고, 로컬 HTTPS LIMS 및 SFTP 모의 서버로 전송 흐름을 검증한다. 운영 DB·LIMS 계정을 받는 옵션이 없으며 별도 Docker 컨테이너와 임시 파일만 사용한다.

준비물은 Docker, MariaDB 11.4 이미지, Java 17 JDK(`java`와 같은 디렉터리의 `keytool`), 테스트를 통과해 빌드된 Spring JAR, Python 프로젝트 의존성과 `paramiko`, `cryptography`, `uvicorn`이다.

```bash
python tests/integration/run_lims_v5.py \
  --java /absolute/path/to/jdk17/bin/java \
  --spring-jar /absolute/path/to/ritasax-web.jar
```

검증하는 흐름:

- Python SSO 클라이언트 → Spring의 Base64 변환 → Bearer 헤더를 요구하는 모의 LIMS. TLS 인증서와 호스트명을 실제 검증한다.
- Python HTTP 의뢰 필터 → Spring 프록시 → LIMS. 자산번호와 미완료 상태 필터, null 필드와 전체 건수, 기존 전체 의뢰 캐시 보존을 확인한다.
- Python HTTP 원본 업로드 및 Python 보고서 큐 등록 → 실제 MariaDB → Spring SFTP 전송. SSH 호스트 키를 실제 검증한다.
- 동일 보고서 중복 큐 등록, 문서 적재 확인, 10회 조회 후 확인 필요/600초 설정, 문서 FAILED 이후 COMPLETED 회복.
- 메일 접수 응답 유실을 재현하고 같은 requestId 재접수의 DUPLICATE를 상태 조회로 해소한다.
- LIMS 적재 완료 시점을 25시간 전으로 조정해 메일 대기 중 원본·ZIP 물리 삭제를 확인한다.
- Spring 재시작 후 메일 실패를 조회하고, 로컬 첨부가 삭제된 상태에서 메일만 재시도한 뒤 SENT로 완료한다.

대기시간을 줄이기 위해 테스트 DB의 `next_retry_at`만 앞당긴다. 실제 운영 설정인 60초/10회/600초 값과 저장된 상태는 그대로 검증한다. 외부 서버 처리 시간이나 실제 10분 경과를 측정하는 시험은 아니다.

모의 LIMS는 의뢰 내부번호 550090에 대해 실제 조회 경로에서 시험용 수신자 주소를 반환한다. 메일 접수 직후 이 주소를 변경하고 응답을 끊어도 재접수·재시도에서 최초 수신자가 유지되는지 검증한다.

성공 시 출력한 작업 디렉터리에 `result.json`과 Spring 로그가 남는다. 실패 시에도 로그를 남기고 컨테이너와 실행한 서버 프로세스는 정리한다. 로그 경로를 찾기 위해 콘솔의 `WORK_DIR`를 확인한다. 이 시험의 통과는 실제 Edge·LIMS 서버의 접속 또는 메일 수신 확인을 의미하지 않는다.
