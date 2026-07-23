-- RIST 보고서 재생성 신호 저장 테이블 migration
-- 선행 조건: report_runs 테이블이 같은 스키마에 존재해야 한다.
-- 실행 예:
--   mysql --default-character-set=utf8mb4 -h <host> -P <port> \
--     -u <user> -p <database> < mariadb_report_regeneration_migration.sql

CREATE TABLE IF NOT EXISTS report_regeneration_requests (
    signal_id VARCHAR(36) NOT NULL
        COMMENT '재생성 신호 UUID. report_regeneration_requests 기본키',
    report_id VARCHAR(36) NOT NULL
        COMMENT '재생성 대상 report_runs.report_id',
    source_job_id VARCHAR(36)
        COMMENT '대상 보고서의 원본 jobs.job_id. 작업 삭제 시 NULL',
    requested_at VARCHAR(64)
        COMMENT '호출자가 전달한 재생성 요청 시각 ISO-8601 문자열',
    requested_by VARCHAR(100)
        COMMENT '재생성을 요청한 사용자 또는 시스템 식별자',
    reason VARCHAR(1000)
        COMMENT '재생성을 요청한 업무 사유',
    prompt TEXT NOT NULL
        COMMENT '보고서 재생성 시 적용할 사용자 지시문. 고정 분석 정책을 대체하지 않음',
    status VARCHAR(32) NOT NULL DEFAULT 'RECEIVED'
        COMMENT '신호 처리 상태. 현재는 RECEIVED만 사용',
    idempotency_key VARCHAR(128) NOT NULL
        COMMENT '동일 재생성 신호 중복 접수를 방지하는 요청 키',
    received_at VARCHAR(64) NOT NULL
        COMMENT 'Edge가 신호를 접수한 시각 ISO-8601 문자열',
    processed_at VARCHAR(64)
        COMMENT '향후 재생성 worker가 신호 처리를 완료한 시각',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 생성 시각',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6)
        COMMENT 'DB 레코드 최종 변경 시각',
    PRIMARY KEY (signal_id),
    UNIQUE KEY uq_report_regeneration_idempotency (
        report_id,
        idempotency_key
    ),
    KEY idx_report_regeneration_status (status, created_at),
    CONSTRAINT fk_report_regeneration_report FOREIGN KEY (report_id)
        REFERENCES report_runs(report_id) ON DELETE CASCADE,
    CONSTRAINT fk_report_regeneration_job FOREIGN KEY (source_job_id)
        REFERENCES jobs(job_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Spring Boot가 전달한 보고서 재생성 프롬프트와 신호 처리 상태';

