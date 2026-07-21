-- Existing RIST report queue migration: report/file lifecycle management
-- Safe to run repeatedly on MariaDB 10.5+.
-- Run after mariadb_report_queue.sql when report_runs already exists.

ALTER TABLE report_runs
    ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE
        COMMENT '운영 전송 대상이 아닌 시험 생성 보고서 여부. TRUE이면 기본 7일 보존 정책 적용',
    ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE
        COMMENT '수동 보존 지정 여부. TRUE이면 자동 정리 대상에서 제외',
    ADD COLUMN IF NOT EXISTS retention_until DATETIME(6)
        COMMENT '사용자가 지정한 최소 보존 기한. NULL이면 전송 상태별 기본 정책 적용',
    ADD COLUMN IF NOT EXISTS deleted_at DATETIME(6)
        COMMENT '관리 화면 또는 자동 정리에서 파일을 휴지통으로 이동한 시각',
    ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(100)
        COMMENT '삭제 또는 정리를 실행한 사용자나 배치 작업 식별자',
    ADD COLUMN IF NOT EXISTS delete_reason VARCHAR(255)
        COMMENT '휴지통 이동 사유. 감사 추적용이며 파일 본문은 저장하지 않음';

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
