-- RIST 보고서 공유 저장소/DB 전송 큐 스키마
-- 선행 조건: 기존 jobs 테이블이 같은 스키마에 존재해야 한다.
-- ZIP 본문이나 절대 경로는 DB에 저장하지 않는다.
-- 컬럼 설명 확인: SHOW FULL COLUMNS FROM <table_name>;
-- 테이블 설명 확인: SHOW TABLE STATUS LIKE '<table_name>';
-- 주의: 이미 생성된 테이블은 CREATE TABLE IF NOT EXISTS 재실행만으로
-- COMMENT가 갱신되지 않는다. 기존 테이블은 별도 ALTER TABLE migration이 필요하다.
-- 기존 큐 데이터까지 삭제하고 이 정의로 새로 생성하려면
-- mariadb_report_queue_recreate.sql을 사용한다.

CREATE TABLE IF NOT EXISTS report_runs (
    report_id VARCHAR(36) NOT NULL
        COMMENT '보고서 생성 건 UUID. report_runs 기본키',
    source_job_id VARCHAR(36)
        COMMENT '보고서를 생성한 Edge 작업 ID. jobs.job_id 참조, 작업 삭제 시 NULL',
    request_number VARCHAR(128) NOT NULL
        COMMENT 'LIMS 의뢰번호. 보고서와 전송 건을 업무적으로 조회하는 기준',
    experiment_code VARCHAR(64) NOT NULL
        COMMENT '실험 코드. 예: FT-IR, RAMAN, XRD, TEM',
    equipment_code VARCHAR(64) NOT NULL
        COMMENT '보고서를 생성한 실험 장비 코드',
    operator_id VARCHAR(100) NOT NULL
        COMMENT '보고서 생성 또는 전송을 요청한 실험자/사용자 식별자',
    version_no INT NOT NULL DEFAULT 1
        COMMENT '동일 Edge 작업에서 재생성된 보고서 버전. 1부터 증가',
    generation_status VARCHAR(32) NOT NULL
        COMMENT '보고서 생성 상태. READY는 ZIP 생성과 무결성 검증이 완료되어 전송 가능한 상태',
    storage_key VARCHAR(64) NOT NULL DEFAULT 'RIST_REPORTS'
        COMMENT '공유 저장소 루트 별칭. 절대 경로 대신 Spring Boot 설정의 root key와 매핑',
    package_relative_path VARCHAR(512) NOT NULL
        COMMENT 'storage_key 루트 기준 보고서 ZIP 상대 경로. 절대 경로 및 상위 경로 이동 금지',
    package_file_name VARCHAR(255) NOT NULL
        COMMENT '사용자 다운로드 및 LIMS 전송에 사용할 보고서 ZIP 파일명',
    package_size_bytes BIGINT NOT NULL
        COMMENT '생성 완료 시점의 보고서 ZIP 크기(bytes). 전송 전 무결성 검증에 사용',
    package_sha256 CHAR(64) NOT NULL
        COMMENT '보고서 ZIP SHA-256 소문자 16진수 해시. 공유 저장소 파일 검증에 사용',
    report_options_json LONGTEXT
        COMMENT '생성 당시 보고서 형식, 포함 파일 등 옵션 JSON. 없으면 NULL',
    is_test BOOLEAN NOT NULL DEFAULT FALSE
        COMMENT '운영 전송 대상이 아닌 시험 생성 보고서 여부. TRUE이면 기본 7일 보존 정책 적용',
    pinned BOOLEAN NOT NULL DEFAULT FALSE
        COMMENT '수동 보존 지정 여부. TRUE이면 자동 정리 대상에서 제외',
    retention_until DATETIME(6)
        COMMENT '사용자가 지정한 최소 보존 기한. NULL이면 전송 상태별 기본 정책 적용',
    deleted_at DATETIME(6)
        COMMENT '관리 화면 또는 자동 정리에서 파일을 휴지통으로 이동한 시각',
    deleted_by VARCHAR(100)
        COMMENT '삭제 또는 정리를 실행한 사용자나 배치 작업 식별자',
    delete_reason VARCHAR(255)
        COMMENT '휴지통 이동 사유. 감사 추적용이며 파일 본문은 저장하지 않음',
    generated_at VARCHAR(64) NOT NULL
        COMMENT 'Edge가 기록한 보고서 생성 시각 ISO-8601 문자열. 시간대 포함',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최초 등록 시각',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최종 변경 시각',
    PRIMARY KEY (report_id),
    UNIQUE KEY uq_report_runs_package_path (
        storage_key,
        package_relative_path
    ),
    UNIQUE KEY uq_report_runs_job_version (source_job_id, version_no),
    KEY idx_report_runs_request (request_number, experiment_code),
    CONSTRAINT fk_report_runs_job FOREIGN KEY (source_job_id)
        REFERENCES jobs(job_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Edge가 생성하고 검증한 보고서 ZIP의 버전, 공유 저장소 위치 및 무결성 정보';

CREATE TABLE IF NOT EXISTS report_artifacts (
    artifact_id VARCHAR(36) NOT NULL
        COMMENT '보고서 산출물 또는 원본 파일 UUID. report_artifacts 기본키',
    report_id VARCHAR(36) NOT NULL
        COMMENT '소속 보고서 ID. report_runs.report_id 참조',
    source_job_id VARCHAR(36)
        COMMENT '원본 업로드 파일이 속한 Edge 작업 ID. jobs.job_id 참조, 작업 삭제 시 NULL',
    artifact_type VARCHAR(32) NOT NULL
        COMMENT '파일 종류: RAW, ZIP, PPTX, PDF, HTML, XLSX, IMAGE, JSON, MARKDOWN, OTHER',
    storage_key VARCHAR(64) NOT NULL DEFAULT 'RIST_REPORTS'
        COMMENT '공유 저장소 루트 별칭. 절대 경로 대신 서버 설정과 매핑',
    relative_path VARCHAR(512) NOT NULL
        COMMENT 'storage_key 기준 파일 상대 경로. 절대 경로와 상위 경로 이동 금지',
    file_name VARCHAR(255) NOT NULL
        COMMENT '다운로드 시 사용할 파일명',
    size_bytes BIGINT NOT NULL
        COMMENT '등록 시점 파일 크기(bytes). 실제 파일과의 불일치 검사에 사용',
    sha256 CHAR(64)
        COMMENT '등록 시점 SHA-256 소문자 16진수 해시. 무결성 검증이 불가능하면 NULL',
    retention_until DATETIME(6)
        COMMENT '파일별 최소 보존 기한. NULL이면 report_runs 보존 정책을 따름',
    trash_relative_path VARCHAR(512)
        COMMENT '휴지통 이동 후 storage_key 기준 상대 경로. 이동 전에는 NULL',
    deleted_at DATETIME(6)
        COMMENT '파일을 휴지통 영역으로 이동한 시각. NULL이면 활성 파일',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최초 등록 시각',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최종 변경 시각',
    PRIMARY KEY (artifact_id),
    UNIQUE KEY uq_report_artifacts_path (report_id, storage_key, relative_path),
    KEY idx_report_artifacts_report (report_id, artifact_type, deleted_at),
    CONSTRAINT fk_report_artifacts_report FOREIGN KEY (report_id)
        REFERENCES report_runs(report_id) ON DELETE CASCADE,
    CONSTRAINT fk_report_artifacts_job FOREIGN KEY (source_job_id)
        REFERENCES jobs(job_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='보고서 ZIP, 개별 산출물, 원본 RAW 파일의 위치, 무결성, 보존 및 휴지통 상태';

CREATE TABLE IF NOT EXISTS report_transfers (
    transfer_id VARCHAR(36) NOT NULL
        COMMENT '보고서 전송 큐 UUID. report_transfers 기본키',
    report_id VARCHAR(36) NOT NULL
        COMMENT '전송할 보고서 ID. report_runs.report_id 참조',
    request_number VARCHAR(128) NOT NULL
        COMMENT 'LIMS 의뢰번호 스냅샷. 큐 조회와 장애 대응 시 사용',
    experiment_code VARCHAR(64) NOT NULL
        COMMENT '실험 코드 스냅샷. 예: FT-IR, RAMAN, XRD, TEM',
    equipment_code VARCHAR(64) NOT NULL
        COMMENT '실험 장비 코드 스냅샷',
    operator_id VARCHAR(100) NOT NULL
        COMMENT '전송 요청 실험자/사용자 식별자 스냅샷',
    destination VARCHAR(64) NOT NULL DEFAULT 'LIMS'
        COMMENT '전송 대상 시스템 코드. 기본값 LIMS',
    status VARCHAR(32) NOT NULL
        COMMENT '큐 상태: PENDING, PROCESSING, RETRY_WAIT, COMPLETED, FAILED, CANCELLED',
    attempt_count INT NOT NULL DEFAULT 0
        COMMENT 'worker가 선점하여 시작한 누적 전송 시도 횟수',
    max_attempts INT NOT NULL DEFAULT 5
        COMMENT '자동 재시도를 포함한 최대 전송 시도 횟수',
    idempotency_key VARCHAR(128) NOT NULL
        COMMENT '중복 LIMS 전송 방지 키. 전체 큐에서 고유',
    lease_owner VARCHAR(128)
        COMMENT '현재 작업을 선점한 Spring Boot worker 식별자. 미선점 상태는 NULL',
    lease_until DATETIME(6)
        COMMENT '현재 worker 선점 만료 시각. 만료 후 다른 worker가 복구 가능',
    next_retry_at DATETIME(6)
        COMMENT 'RETRY_WAIT 상태에서 다음 선점이 허용되는 시각',
    requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT 'Edge가 전송을 요청하여 큐에 등록한 시각',
    started_at DATETIME(6)
        COMMENT '최초 전송 처리가 시작된 시각',
    completed_at DATETIME(6)
        COMMENT 'COMPLETED, FAILED 또는 CANCELLED 최종 종료 시각',
    external_tracking_id VARCHAR(255)
        COMMENT 'LIMS가 반환한 접수번호, 문서번호 또는 외부 추적 ID',
    last_error_code VARCHAR(128)
        COMMENT '가장 최근 전송 실패의 표준 오류 코드',
    last_error_message TEXT
        COMMENT '가장 최근 전송 실패 요약. 민감정보와 파일 본문 저장 금지',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최초 등록 시각',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최종 변경 시각',
    PRIMARY KEY (transfer_id),
    UNIQUE KEY uq_report_transfers_report_destination (report_id, destination),
    UNIQUE KEY uq_report_transfers_idempotency (idempotency_key),
    KEY idx_report_transfers_scheduler (
        status,
        next_retry_at,
        lease_until,
        requested_at
    ),
    CONSTRAINT fk_report_transfers_report FOREIGN KEY (report_id)
        REFERENCES report_runs(report_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Spring Boot가 선점하여 LIMS로 전달하는 보고서 전송 큐와 현재 상태';

CREATE TABLE IF NOT EXISTS report_transfer_attempts (
    attempt_id BIGINT NOT NULL AUTO_INCREMENT
        COMMENT '전송 시도 이력 자동 증가 기본키',
    transfer_id VARCHAR(36) NOT NULL
        COMMENT '대상 전송 큐 ID. report_transfers.transfer_id 참조',
    attempt_no INT NOT NULL
        COMMENT '해당 transfer_id 내 1부터 증가하는 전송 시도 순번',
    worker_id VARCHAR(128)
        COMMENT '실제 전송을 수행한 Spring Boot worker 식별자',
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '해당 전송 시도 시작 시각',
    finished_at DATETIME(6)
        COMMENT '해당 전송 시도 종료 시각. 처리 중이면 NULL',
    success BOOLEAN
        COMMENT '성공 여부. 처리 중 NULL, 성공 TRUE, 실패 FALSE',
    response_code VARCHAR(128)
        COMMENT 'LIMS 또는 전송 어댑터가 반환한 응답 코드',
    response_message TEXT
        COMMENT 'LIMS 또는 전송 어댑터의 응답 요약. 민감정보 저장 금지',
    error_code VARCHAR(128)
        COMMENT '실패 시 표준 오류 코드. 성공 또는 처리 중이면 NULL',
    error_message TEXT
        COMMENT '실패 원인 요약. 자격증명, 원문 파일 등 민감정보 저장 금지',
    transport_details_json LONGTEXT
        COMMENT '전송 시간, 대상 식별자 등 진단용 JSON. ZIP 본문과 자격증명 저장 금지',
    PRIMARY KEY (attempt_id),
    UNIQUE KEY uq_report_transfer_attempt (transfer_id, attempt_no),
    KEY idx_report_transfer_attempts_transfer (transfer_id, started_at),
    CONSTRAINT fk_report_transfer_attempts_transfer FOREIGN KEY (transfer_id)
        REFERENCES report_transfers(transfer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='각 보고서 전송 시도의 성공, 실패, 응답 및 진단 감사 이력';
