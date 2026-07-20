-- RIST 보고서 공유 저장소/DB 전송 큐 스키마
-- 선행 조건: 기존 jobs 테이블이 같은 스키마에 존재해야 한다.
-- ZIP 본문이나 절대 경로는 DB에 저장하지 않는다.

CREATE TABLE IF NOT EXISTS report_runs (
    report_id VARCHAR(36) NOT NULL,
    source_job_id VARCHAR(36),
    request_number VARCHAR(128) NOT NULL,
    experiment_code VARCHAR(64) NOT NULL,
    equipment_code VARCHAR(64) NOT NULL,
    operator_id VARCHAR(100) NOT NULL,
    version_no INT NOT NULL DEFAULT 1,
    generation_status VARCHAR(32) NOT NULL,
    storage_key VARCHAR(64) NOT NULL DEFAULT 'RIST_REPORTS',
    package_relative_path VARCHAR(512) NOT NULL,
    package_file_name VARCHAR(255) NOT NULL,
    package_size_bytes BIGINT NOT NULL,
    package_sha256 CHAR(64) NOT NULL,
    report_options_json LONGTEXT,
    generated_at VARCHAR(64) NOT NULL,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
    PRIMARY KEY (report_id),
    UNIQUE KEY uq_report_runs_package_path (
        storage_key,
        package_relative_path
    ),
    UNIQUE KEY uq_report_runs_job_version (source_job_id, version_no),
    KEY idx_report_runs_request (request_number, experiment_code),
    CONSTRAINT fk_report_runs_job FOREIGN KEY (source_job_id)
        REFERENCES jobs(job_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS report_transfers (
    transfer_id VARCHAR(36) NOT NULL,
    report_id VARCHAR(36) NOT NULL,
    request_number VARCHAR(128) NOT NULL,
    experiment_code VARCHAR(64) NOT NULL,
    equipment_code VARCHAR(64) NOT NULL,
    operator_id VARCHAR(100) NOT NULL,
    destination VARCHAR(64) NOT NULL DEFAULT 'LIMS',
    status VARCHAR(32) NOT NULL,
    attempt_count INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 5,
    idempotency_key VARCHAR(128) NOT NULL,
    lease_owner VARCHAR(128),
    lease_until DATETIME(6),
    next_retry_at DATETIME(6),
    requested_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    started_at DATETIME(6),
    completed_at DATETIME(6),
    external_tracking_id VARCHAR(255),
    last_error_code VARCHAR(128),
    last_error_message TEXT,
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6),
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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS report_transfer_attempts (
    attempt_id BIGINT NOT NULL AUTO_INCREMENT,
    transfer_id VARCHAR(36) NOT NULL,
    attempt_no INT NOT NULL,
    worker_id VARCHAR(128),
    started_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at DATETIME(6),
    success BOOLEAN,
    response_code VARCHAR(128),
    response_message TEXT,
    error_code VARCHAR(128),
    error_message TEXT,
    transport_details_json LONGTEXT,
    PRIMARY KEY (attempt_id),
    UNIQUE KEY uq_report_transfer_attempt (transfer_id, attempt_no),
    KEY idx_report_transfer_attempts_transfer (transfer_id, started_at),
    CONSTRAINT fk_report_transfer_attempts_transfer FOREIGN KEY (transfer_id)
        REFERENCES report_transfers(transfer_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
