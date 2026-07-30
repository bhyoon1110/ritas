from __future__ import annotations

import json
from queue import Empty, Full, LifoQueue
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

import pymysql
from pymysql.cursors import DictCursor

from rist_common import get_logger

logger = get_logger(__name__)


TERMINAL_STATUSES = {"COMPLETED", "FAILED", "UPLOAD_EXPIRED"}
JOB_COLUMNS = {
    "job_id",
    "request_number",
    "experiment_code",
    "equipment_code",
    "operator_id",
    "source_host_name",
    "declared_ip_address",
    "observed_remote_ip",
    "client_version",
    "expected_file_count",
    "expected_total_size_bytes",
    "status",
    "progress",
    "created_at",
    "upload_expires_at",
    "verified_at",
    "report_requested_at",
    "processing_started_at",
    "completed_at",
    "root_relative_path",
    "report_options_json",
    "error_json",
}
FILE_COLUMNS = {
    "file_id",
    "job_id",
    "relative_path",
    "size_bytes",
    "sha256",
    "last_modified_at",
    "uploaded_at",
}


@dataclass(frozen=True)
class DatabaseConfig:
    """MariaDB 접속 정보."""

    host: str
    name: str = "rist_edge"
    port: int = 3306
    user: str = "rist"
    password: str = ""
    pool_size: int = 8
    pool_timeout_seconds: float = 10.0


_SCHEMA: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS jobs (
        job_id VARCHAR(36) NOT NULL,
        request_number VARCHAR(128) NOT NULL,
        experiment_code VARCHAR(64) NOT NULL,
        equipment_code VARCHAR(64) NOT NULL,
        operator_id VARCHAR(64) NOT NULL,
        source_host_name VARCHAR(255) NOT NULL,
        declared_ip_address VARCHAR(64),
        observed_remote_ip VARCHAR(64),
        client_version VARCHAR(64),
        expected_file_count INT NOT NULL,
        expected_total_size_bytes BIGINT NOT NULL,
        status VARCHAR(32) NOT NULL,
        progress INT NOT NULL DEFAULT 0,
        created_at VARCHAR(64) NOT NULL,
        upload_expires_at VARCHAR(64) NOT NULL,
        verified_at VARCHAR(64),
        report_requested_at VARCHAR(64),
        processing_started_at VARCHAR(64),
        completed_at VARCHAR(64),
        root_relative_path VARCHAR(512) NOT NULL,
        report_options_json LONGTEXT,
        error_json LONGTEXT,
        PRIMARY KEY (job_id),
        KEY idx_jobs_business_pk (
            request_number,
            experiment_code,
            equipment_code,
            operator_id
        )
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS files (
        file_id VARCHAR(36) NOT NULL,
        job_id VARCHAR(36) NOT NULL,
        relative_path VARCHAR(512) NOT NULL,
        size_bytes BIGINT NOT NULL,
        sha256 VARCHAR(64) NOT NULL,
        last_modified_at VARCHAR(64),
        uploaded_at VARCHAR(64) NOT NULL,
        PRIMARY KEY (file_id),
        UNIQUE KEY uq_files_job_path (job_id, relative_path),
        CONSTRAINT fk_files_job FOREIGN KEY (job_id) REFERENCES jobs(job_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS idempotency_records (
        endpoint VARCHAR(128) NOT NULL,
        idempotency_key VARCHAR(128) NOT NULL,
        request_hash VARCHAR(128) NOT NULL,
        response_status INT NOT NULL,
        response_json LONGTEXT NOT NULL,
        created_at VARCHAR(64) NOT NULL,
        PRIMARY KEY (endpoint, idempotency_key)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
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
            COMMENT '운영 전송 대상이 아닌 테스트 보고서 여부',
        pinned BOOLEAN NOT NULL DEFAULT FALSE
            COMMENT 'TRUE이면 자동 정리 대상에서 제외',
        retention_until DATETIME(6)
            COMMENT '사용자가 지정한 최소 보존 기한. NULL이면 상태별 기본 정책 적용',
        deleted_at DATETIME(6)
            COMMENT '관리 화면에서 파일을 휴지통으로 이동한 시각',
        deleted_by VARCHAR(100)
            COMMENT '삭제 또는 정리를 실행한 작업자 식별자',
        delete_reason VARCHAR(255)
            COMMENT '삭제 또는 자동 정리 사유',
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
      COMMENT='Edge가 생성하고 검증한 보고서 ZIP의 버전, 공유 저장소 위치 및 무결성 정보'
    """,
    """
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
      COMMENT='Spring Boot가 전달한 보고서 재생성 프롬프트와 신호 처리 상태'
    """,
    """
    ALTER TABLE report_runs
        ADD COLUMN IF NOT EXISTS is_test BOOLEAN NOT NULL DEFAULT FALSE
            COMMENT '운영 전송 대상이 아닌 테스트 보고서 여부',
        ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE
            COMMENT 'TRUE이면 자동 정리 대상에서 제외',
        ADD COLUMN IF NOT EXISTS retention_until DATETIME(6)
            COMMENT '사용자가 지정한 최소 보존 기한',
        ADD COLUMN IF NOT EXISTS deleted_at DATETIME(6)
            COMMENT '관리 화면에서 파일을 휴지통으로 이동한 시각',
        ADD COLUMN IF NOT EXISTS deleted_by VARCHAR(100)
            COMMENT '삭제 또는 정리를 실행한 작업자 식별자',
        ADD COLUMN IF NOT EXISTS delete_reason VARCHAR(255)
            COMMENT '삭제 또는 자동 정리 사유'
    """,
    """
    CREATE TABLE IF NOT EXISTS report_artifacts (
        artifact_id VARCHAR(36) NOT NULL
            COMMENT '보고서 산출물 UUID',
        report_id VARCHAR(36) NOT NULL
            COMMENT 'report_runs.report_id 참조',
        source_job_id VARCHAR(36)
            COMMENT 'RAW 파일의 원본 Edge 작업 ID',
        artifact_type VARCHAR(32) NOT NULL
            COMMENT 'RAW, PPTX, PDF, HTML, XLSX, ZIP, IMAGE 중 하나',
        storage_key VARCHAR(64) NOT NULL
            COMMENT '공유 저장소 루트 별칭',
        relative_path VARCHAR(512) NOT NULL
            COMMENT '공유 저장소 루트 기준 파일 상대 경로',
        file_name VARCHAR(255) NOT NULL
            COMMENT '다운로드 파일명',
        size_bytes BIGINT NOT NULL
            COMMENT '파일 크기(bytes)',
        sha256 CHAR(64)
            COMMENT '파일 SHA-256. 미확인 상태는 NULL',
        retention_until DATETIME(6)
            COMMENT '산출물별 최소 보존 기한',
        trash_relative_path VARCHAR(512)
            COMMENT '휴지통 이동 후 공유 저장소 기준 상대 경로',
        deleted_at DATETIME(6)
            COMMENT '휴지통으로 이동한 시각',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (artifact_id),
        UNIQUE KEY uq_report_artifacts_path (
            report_id,
            storage_key,
            relative_path
        ),
        KEY idx_report_artifacts_report (report_id, artifact_type, deleted_at),
        CONSTRAINT fk_report_artifacts_report FOREIGN KEY (report_id)
            REFERENCES report_runs(report_id) ON DELETE CASCADE,
        CONSTRAINT fk_report_artifacts_job FOREIGN KEY (source_job_id)
            REFERENCES jobs(job_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='보고서 ZIP, 개별 산출물 및 RAW 파일의 위치, 무결성, 보존 및 삭제 상태'
    """,
    """
    CREATE TABLE IF NOT EXISTS report_retention_policies (
        policy_key VARCHAR(32) NOT NULL
            COMMENT '보존 정책 식별자. UNSENT_TEST, FAILED_OR_CANCELLED, COMPLETED, TRASH',
        retention_days INT NOT NULL
            COMMENT '기준 시각부터 자동 정리 또는 물리 삭제까지 보존할 일수',
        auto_cleanup_enabled BOOLEAN NOT NULL DEFAULT TRUE
            COMMENT 'TRUE이면 자동 정리 배치가 이 정책을 적용',
        description VARCHAR(255) NOT NULL
            COMMENT '운영 화면에 표시할 정책 설명',
        updated_by VARCHAR(100)
            COMMENT '마지막으로 정책을 변경한 사용자 식별자',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            COMMENT '정책 최초 등록 시각',
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6)
            COMMENT '정책 최종 변경 시각',
        PRIMARY KEY (policy_key),
        CONSTRAINT chk_report_retention_days CHECK (retention_days BETWEEN 1 AND 3650)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='운영 화면에서 즉시 변경하는 보고서 및 휴지통 자동 정리 정책'
    """,
    """
    INSERT IGNORE INTO report_retention_policies (
        policy_key, retention_days, auto_cleanup_enabled, description, updated_by
    ) VALUES
        ('UNSENT_TEST', 7, TRUE, '전송 큐에 등록되지 않은 테스트 보고서 보존 기간', 'schema-default'),
        ('FAILED_OR_CANCELLED', 30, TRUE, '실패 또는 취소된 전송 보고서 보존 기간', 'schema-default'),
        ('COMPLETED', 90, TRUE, 'LIMS 전송 완료 시점 이후 보고서 보존 기간', 'schema-default'),
        ('TRASH', 7, TRUE, '휴지통 이동 후 실제 파일을 물리 삭제하기까지의 기간', 'schema-default')
    """,
    """
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
      COMMENT='Spring Boot가 선점하여 LIMS로 전달하는 보고서 전송 큐와 현재 상태'
    """,
    """
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
      COMMENT='각 보고서 전송 시도의 성공, 실패, 응답 및 진단 감사 이력'
    """,
    """
    CREATE TABLE IF NOT EXISTS app_users (
        user_id VARCHAR(36) NOT NULL COMMENT '로컬 회원 UUID',
        login_id VARCHAR(255) NOT NULL COMMENT '로컬 로그인 ID. 소문자로 정규화',
        email VARCHAR(255) COMMENT '선택 연락 이메일. 로그인 식별자로 사용하지 않음',
        password_hash VARCHAR(512) NOT NULL COMMENT 'scrypt 비밀번호 해시. 원문 저장 금지',
        display_name VARCHAR(100) NOT NULL COMMENT '화면과 감사 로그에 표시할 이름',
        status VARCHAR(32) NOT NULL DEFAULT 'PENDING'
            COMMENT '회원 상태: PENDING, ACTIVE, SUSPENDED',
        last_login_at DATETIME(6) COMMENT '최근 로컬 로그인 성공 시각',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (user_id),
        UNIQUE KEY uq_app_users_login_id (login_id),
        UNIQUE KEY uq_app_users_email (email),
        KEY idx_app_users_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='Edge 웹 화면에 로그인하는 로컬 회원과 관리자 승인 상태'
    """,
    """
    CREATE TABLE IF NOT EXISTS user_project_permissions (
        user_id VARCHAR(36) NOT NULL COMMENT 'app_users.user_id 참조',
        project_code VARCHAR(32) NOT NULL COMMENT 'FTIR, RAMAN, XRD, TEM 중 하나',
        granted_by VARCHAR(36) COMMENT '권한을 승인한 관리자 user_id',
        granted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (user_id, project_code),
        CONSTRAINT fk_user_project_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE,
        CONSTRAINT fk_user_project_granter FOREIGN KEY (granted_by)
            REFERENCES app_users(user_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='회원별 FTIR, RAMAN, XRD, TEM 웹 화면 접근 및 보고서 생성 권한'
    """,
    """
    CREATE TABLE IF NOT EXISTS user_roles (
        user_id VARCHAR(36) NOT NULL COMMENT 'app_users.user_id 참조',
        role_code VARCHAR(32) NOT NULL COMMENT 'ADMIN 또는 REPORT_SENDER',
        granted_by VARCHAR(36) COMMENT '역할을 승인한 관리자 user_id',
        granted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (user_id, role_code),
        CONSTRAINT fk_user_roles_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE,
        CONSTRAINT fk_user_roles_granter FOREIGN KEY (granted_by)
            REFERENCES app_users(user_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='운영 관리자 및 LIMS 보고서 전송 역할'
    """,
    """
    CREATE TABLE IF NOT EXISTS sso_identities (
        identity_id VARCHAR(36) NOT NULL COMMENT 'SSO 연결 UUID',
        user_id VARCHAR(36) NOT NULL COMMENT '연결된 로컬 회원',
        provider VARCHAR(64) NOT NULL COMMENT 'OIDC 공급자 식별자',
        subject VARCHAR(255) NOT NULL COMMENT 'OIDC sub. 공급자 내 불변 사용자 ID',
        employee_id VARCHAR(100) COMMENT 'SSO가 제공한 사번 또는 업무 사용자 ID',
        email VARCHAR(255) COMMENT 'SSO가 제공한 이메일 스냅샷',
        display_name VARCHAR(100) COMMENT 'SSO가 제공한 표시 이름 스냅샷',
        active BOOLEAN NOT NULL DEFAULT TRUE COMMENT 'SSO 연결 사용 가능 여부',
        linked_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        last_authenticated_at DATETIME(6) COMMENT 'SSO 로그인 완료 시각',
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
            ON UPDATE CURRENT_TIMESTAMP(6),
        PRIMARY KEY (identity_id),
        UNIQUE KEY uq_sso_provider_subject (provider, subject),
        UNIQUE KEY uq_sso_user_provider (user_id, provider),
        CONSTRAINT fk_sso_identity_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='로컬 회원과 사내 OIDC SSO 계정 연결 및 최근 인증 시각'
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_sessions (
        session_id VARCHAR(36) NOT NULL COMMENT '로그인 세션 UUID',
        user_id VARCHAR(36) NOT NULL COMMENT '로그인 회원',
        token_hash CHAR(64) NOT NULL COMMENT '브라우저 쿠키 토큰의 SHA-256 해시',
        expires_at DATETIME(6) NOT NULL COMMENT '세션 만료 시각',
        sso_authenticated_at DATETIME(6) COMMENT '이 세션에서 최근 SSO 인증 완료 시각',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        revoked_at DATETIME(6) COMMENT '로그아웃 또는 관리자 강제 만료 시각',
        user_agent VARCHAR(512) COMMENT '보안 감사용 브라우저 User-Agent',
        remote_ip VARCHAR(64) COMMENT '세션 생성 시 클라이언트 IP',
        PRIMARY KEY (session_id),
        UNIQUE KEY uq_auth_sessions_token (token_hash),
        KEY idx_auth_sessions_user (user_id, expires_at, revoked_at),
        CONSTRAINT fk_auth_session_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='원문 토큰을 저장하지 않는 Edge 웹 로그인 세션'
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_oidc_states (
        state_hash CHAR(64) NOT NULL COMMENT 'OIDC state 원문의 SHA-256 해시',
        user_id VARCHAR(36) NOT NULL COMMENT 'SSO 연결을 시작한 로컬 회원',
        session_id VARCHAR(36) NOT NULL COMMENT 'SSO 인증을 완료할 로그인 세션',
        code_verifier VARCHAR(128) NOT NULL COMMENT 'PKCE code verifier',
        return_to VARCHAR(512) NOT NULL COMMENT '인증 완료 후 동일 사이트 이동 경로',
        expires_at DATETIME(6) NOT NULL COMMENT '일회용 state 만료 시각',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (state_hash),
        KEY idx_auth_oidc_expiry (expires_at),
        CONSTRAINT fk_auth_oidc_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE,
        CONSTRAINT fk_auth_oidc_session FOREIGN KEY (session_id)
            REFERENCES auth_sessions(session_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='SSO authorization-code 및 PKCE 요청의 단기 일회용 상태'
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_audit_events (
        event_id BIGINT NOT NULL AUTO_INCREMENT COMMENT '인증 감사 이벤트 ID',
        user_id VARCHAR(36) COMMENT '관련 회원. 로그인 실패 등은 NULL 가능',
        event_type VARCHAR(64) NOT NULL COMMENT 'SIGNUP, LOGIN, SSO_LINK, PERMISSION_CHANGE 등',
        success BOOLEAN NOT NULL COMMENT '동작 성공 여부',
        details_json LONGTEXT COMMENT '민감정보를 제외한 감사 상세 JSON',
        remote_ip VARCHAR(64) COMMENT '요청 클라이언트 IP',
        created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (event_id),
        KEY idx_auth_audit_user (user_id, created_at),
        KEY idx_auth_audit_type (event_type, created_at),
        CONSTRAINT fk_auth_audit_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE SET NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='회원가입, 로그인, SSO 연결, 권한 변경 및 전송 인증 감사 이력'
    """,
    """
    CREATE OR REPLACE VIEW request_summary AS
    SELECT request_number,
           COUNT(*) AS job_count,
           SUM(status = 'COMPLETED') AS completed_job_count,
           SUM(status = 'FAILED') AS failed_job_count,
           GROUP_CONCAT(DISTINCT status ORDER BY status) AS statuses,
           GROUP_CONCAT(DISTINCT experiment_code ORDER BY experiment_code) AS experiments,
           GROUP_CONCAT(DISTINCT equipment_code ORDER BY equipment_code) AS equipment_codes,
           MIN(created_at) AS created_at,
           MAX(COALESCE(
               completed_at,
               processing_started_at,
               report_requested_at,
               verified_at,
               created_at
           )) AS updated_at
    FROM jobs
    GROUP BY request_number
    """,
)


class _Cursor:
    """PyMySQL 커서를 dict 결과 인터페이스로 감싼다."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    @property
    def rowcount(self) -> int:
        return self._cursor.rowcount

    def fetchone(self) -> dict[str, Any] | None:
        row = self._cursor.fetchone()
        return dict(row) if row is not None else None

    def fetchall(self) -> list[dict[str, Any]]:
        return [dict(row) for row in self._cursor.fetchall()]


class _Connection:
    """PyMySQL 연결을 execute/commit 인터페이스로 감싼다."""

    def __init__(self, raw: Any, release: Callable[[Any], None]) -> None:
        self._raw = raw
        self._release = release
        self._closed = False

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:
        cursor = self._raw.cursor()
        cursor.execute(sql.replace("?", "%s"), params)
        return _Cursor(cursor)

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._release(self._raw)

    def __enter__(self) -> "_Connection":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if exc_type is None:
                self._raw.commit()
            else:
                self._raw.rollback()
        finally:
            self.close()


class Database:
    def __init__(self, config: DatabaseConfig) -> None:
        if not config.host:
            raise ValueError("MariaDB 접속 호스트(RIST_DB_HOST)가 필요합니다.")
        if config.pool_size < 1:
            raise ValueError("MariaDB 커넥션 풀 크기는 1 이상이어야 합니다.")
        if config.pool_timeout_seconds <= 0:
            raise ValueError("MariaDB 커넥션 풀 대기 시간은 0보다 커야 합니다.")
        self.config = config
        self._pool: LifoQueue[Any] = LifoQueue(maxsize=config.pool_size)
        self._pool_lock = threading.Lock()
        self._pool_created = 0
        self._pool_closed = False
        logger.info(
            "MariaDB에 연결합니다 (host=%s, port=%s, db=%s, user=%s)",
            config.host,
            config.port,
            config.name,
            config.user,
        )
        self._ensure_database()
        self._initialize()
        logger.info("MariaDB 스키마 초기화 완료 (db=%s)", config.name)

    @classmethod
    def from_settings(cls, settings: Any) -> "Database":
        return cls(
            DatabaseConfig(
                host=settings.db_host,
                port=settings.db_port,
                name=settings.db_name,
                user=settings.db_user,
                password=settings.db_password,
                pool_size=settings.db_pool_size,
                pool_timeout_seconds=settings.db_pool_timeout_seconds,
            )
        )

    def _ensure_database(self) -> None:
        """접속 정보만으로 동작하도록 대상 DB가 없으면 생성한다."""
        connection = pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            charset="utf8mb4",
            autocommit=True,
        )
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{self.config.name}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
        finally:
            connection.close()

    def _connect(self) -> _Connection:
        return _Connection(
            self._acquire_raw_connection(), self._release_raw_connection
        )

    def _create_raw_connection(self) -> Any:
        return pymysql.connect(
            host=self.config.host,
            port=self.config.port,
            user=self.config.user,
            password=self.config.password,
            database=self.config.name,
            charset="utf8mb4",
            cursorclass=DictCursor,
            autocommit=False,
        )

    def _acquire_raw_connection(self) -> Any:
        while True:
            with self._pool_lock:
                if self._pool_closed:
                    raise RuntimeError("닫힌 MariaDB 커넥션 풀을 사용할 수 없습니다.")
            try:
                connection = self._pool.get_nowait()
            except Empty:
                with self._pool_lock:
                    can_create = (
                        not self._pool_closed
                        and self._pool_created < self.config.pool_size
                    )
                    if can_create:
                        self._pool_created += 1
                if can_create:
                    try:
                        return self._create_raw_connection()
                    except Exception:
                        with self._pool_lock:
                            self._pool_created -= 1
                        raise
                try:
                    connection = self._pool.get(
                        timeout=self.config.pool_timeout_seconds
                    )
                except Empty as exc:
                    raise TimeoutError(
                        "MariaDB 커넥션 풀 대기 시간이 초과되었습니다."
                    ) from exc

            try:
                connection.ping(reconnect=True)
            except Exception:
                self._discard_raw_connection(connection)
                continue
            return connection

    def _release_raw_connection(self, connection: Any) -> None:
        try:
            connection.rollback()
        except Exception:
            self._discard_raw_connection(connection)
            return
        with self._pool_lock:
            pool_closed = self._pool_closed
        if pool_closed:
            self._discard_raw_connection(connection)
            return
        try:
            self._pool.put_nowait(connection)
        except Full:
            self._discard_raw_connection(connection)

    def _discard_raw_connection(self, connection: Any) -> None:
        try:
            connection.close()
        finally:
            with self._pool_lock:
                self._pool_created -= 1

    def close(self) -> None:
        """유휴 연결을 닫는다. API/worker 종료 훅에서 한 번 호출한다."""
        with self._pool_lock:
            if self._pool_closed:
                return
            self._pool_closed = True
        while True:
            try:
                self._discard_raw_connection(self._pool.get_nowait())
            except Empty:
                return

    @contextmanager
    def transaction(self) -> Iterator[_Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.transaction() as connection:
            for statement in _SCHEMA:
                connection.execute(statement)

    def fetch_job(self, job_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        return row

    def fetch_jobs_by_status(
        self, status: str, limit: int = 100
    ) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT * FROM jobs
                WHERE status = ?
                ORDER BY report_requested_at, created_at
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        return rows

    def claim_queued_job(self, job_id: str, started_at: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE jobs
                SET status = 'PROCESSING',
                    progress = 50,
                    processing_started_at = ?,
                    error_json = NULL
                WHERE job_id = ? AND status = 'QUEUED'
                """,
                (started_at, job_id),
            )
            claimed = cursor.rowcount == 1
        return claimed

    def fetch_active_job(
        self,
        request_number: str,
        experiment_code: str,
        equipment_code: str,
        operator_id: str,
    ) -> dict[str, Any] | None:
        placeholders = ",".join("?" for _ in TERMINAL_STATUSES)
        params = (
            request_number,
            experiment_code,
            equipment_code,
            operator_id,
            *sorted(TERMINAL_STATUSES),
        )
        query = f"""
            SELECT * FROM jobs
            WHERE request_number = ?
              AND experiment_code = ?
              AND equipment_code = ?
              AND operator_id = ?
              AND status NOT IN ({placeholders})
            ORDER BY created_at DESC
            LIMIT 1
        """
        with self._connect() as connection:
            row = connection.execute(query, params).fetchone()
        return row

    def insert_job(self, job: dict[str, Any]) -> None:
        _validate_columns("jobs", job, JOB_COLUMNS)
        columns = ", ".join(job)
        placeholders = ", ".join("?" for _ in job)
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
                tuple(job.values()),
            )

    def update_job(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        _validate_columns("jobs", values, JOB_COLUMNS)
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE jobs SET {assignments} WHERE job_id = ?",
                (*values.values(), job_id),
            )

    def fetch_files(self, job_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM files WHERE job_id = ? ORDER BY relative_path",
                (job_id,),
            ).fetchall()
        return rows

    def delete_file(self, job_id: str, relative_path: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM files WHERE job_id = ? AND relative_path = ?",
                (job_id, relative_path),
            )
        return cursor.rowcount == 1

    def update_file(self, job_id: str, relative_path: str, **values: Any) -> None:
        _validate_columns(
            "files",
            values,
            FILE_COLUMNS - {"file_id", "job_id", "relative_path"},
        )
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                "UPDATE files SET "
                f"{assignments} WHERE job_id = ? AND relative_path = ?",
                (*values.values(), job_id, relative_path),
            )

    def fetch_request_summaries(
        self,
        limit: int,
        offset: int,
        experiment_keywords: tuple[str, ...] = (),
        include_completed: bool = False,
    ) -> list[dict[str, Any]]:
        filters: list[str] = []
        params: list[Any] = []
        if not include_completed:
            filters.append(
                """
                LOWER(COALESCE(req_state_name, '')) NOT LIKE ?
                """
            )
            params.append("%완료%")
        if experiment_keywords:
            keyword_filters: list[str] = []
            for keyword in experiment_keywords:
                like = f"%{keyword.lower()}%"
                keyword_filters.append(
                    """
                    (
                        LOWER(COALESCE(test_mtd_code, '')) LIKE ?
                        OR LOWER(COALESCE(test_mtd_name, '')) LIKE ?
                    )
                    """
                )
                params.extend([like, like])
            filters.append("(" + " OR ".join(keyword_filters) + ")")
        where_sql = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT
                    req_result_no,
                    req_number,
                    req_date,
                    req_state,
                    req_state_name,
                    req_type_no,
                    req_type_code,
                    req_type_name,
                    project_code,
                    cust_req_name,
                    customer_no,
                    customer_name,
                    req_user_no,
                    req_user_name,
                    smp_result_no,
                    smp_result_name,
                    smp_result_state,
                    test_mtd_result_no,
                    test_mtd_no,
                    test_mtd_code,
                    test_mtd_name,
                    test_state,
                    test_charger_name,
                    output_order,
                    synced_at
                FROM lims_req_ax_search
                {where_sql}
                ORDER BY
                    req_date DESC,
                    req_number DESC,
                    COALESCE(output_order, 2147483647),
                    smp_result_no,
                    test_mtd_result_no
                LIMIT ? OFFSET ?
                """,
                (*params, limit, offset),
            ).fetchall()

    def fetch_file(self, job_id: str, relative_path: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM files WHERE job_id = ? AND relative_path = ?",
                (job_id, relative_path),
            ).fetchone()
        return row

    def insert_file(self, file_record: dict[str, Any]) -> None:
        _validate_columns("files", file_record, FILE_COLUMNS)
        columns = ", ".join(file_record)
        placeholders = ", ".join("?" for _ in file_record)
        with self.transaction() as connection:
            connection.execute(
                f"INSERT INTO files ({columns}) VALUES ({placeholders})",
                tuple(file_record.values()),
            )

    def fetch_idempotency(
        self, endpoint: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM idempotency_records
                WHERE endpoint = ? AND idempotency_key = ?
                """,
                (endpoint, idempotency_key),
            ).fetchone()
        if not row:
            return None
        row["response"] = json.loads(row.pop("response_json"))
        return row

    def insert_idempotency(
        self,
        endpoint: str,
        idempotency_key: str,
        request_hash: str,
        response_status: int,
        response: dict[str, Any],
        created_at: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO idempotency_records (
                    endpoint,
                    idempotency_key,
                    request_hash,
                    response_status,
                    response_json,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    endpoint,
                    idempotency_key,
                    request_hash,
                    response_status,
                    json.dumps(response, ensure_ascii=False, sort_keys=True),
                    created_at,
                ),
            )

    def enqueue_report_transfer(
        self,
        *,
        report_id: str,
        transfer_id: str,
        source_job_id: str | None,
        request_number: str,
        experiment_code: str,
        equipment_code: str,
        operator_id: str,
        storage_key: str,
        package_relative_path: str,
        package_file_name: str,
        package_size_bytes: int,
        package_sha256: str,
        report_options_json: str | None,
        generated_at: str,
        max_attempts: int,
        destination: str = "LIMS",
    ) -> dict[str, Any]:
        """보고서 메타데이터와 LIMS 전송 큐를 같은 트랜잭션으로 등록한다."""
        idempotency_key = f"{report_id}:{destination}"
        with self.transaction() as connection:
            existing_report = connection.execute(
                "SELECT version_no FROM report_runs WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if existing_report is not None:
                version_no = int(existing_report["version_no"])
            elif source_job_id:
                version_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version
                    FROM report_runs WHERE source_job_id = ?
                    """,
                    (source_job_id,),
                ).fetchone()
                version_no = int(version_row["next_version"])
            else:
                version_no = 1
            connection.execute(
                """
                INSERT INTO report_runs (
                    report_id,
                    source_job_id,
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id,
                    version_no,
                    generation_status,
                    storage_key,
                    package_relative_path,
                    package_file_name,
                    package_size_bytes,
                    package_sha256,
                    report_options_json,
                    generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    request_number = VALUES(request_number),
                    experiment_code = VALUES(experiment_code),
                    equipment_code = VALUES(equipment_code),
                    operator_id = VALUES(operator_id),
                    generation_status = 'READY',
                    storage_key = VALUES(storage_key),
                    package_relative_path = VALUES(package_relative_path),
                    package_file_name = VALUES(package_file_name),
                    package_size_bytes = VALUES(package_size_bytes),
                    package_sha256 = VALUES(package_sha256),
                    report_options_json = VALUES(report_options_json),
                    generated_at = VALUES(generated_at),
                    updated_at = CURRENT_TIMESTAMP(6)
                """,
                (
                    report_id,
                    source_job_id,
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id,
                    version_no,
                    storage_key,
                    package_relative_path,
                    package_file_name,
                    package_size_bytes,
                    package_sha256,
                    report_options_json,
                    generated_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO report_transfers (
                    transfer_id,
                    report_id,
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id,
                    destination,
                    status,
                    attempt_count,
                    max_attempts,
                    idempotency_key
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'PENDING', 0, ?, ?)
                ON DUPLICATE KEY UPDATE
                    request_number = VALUES(request_number),
                    experiment_code = VALUES(experiment_code),
                    equipment_code = VALUES(equipment_code),
                    operator_id = VALUES(operator_id),
                    max_attempts = VALUES(max_attempts),
                    updated_at = CURRENT_TIMESTAMP(6)
                """,
                (
                    transfer_id,
                    report_id,
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id,
                    destination,
                    max_attempts,
                    idempotency_key,
                ),
            )
            transfer = connection.execute(
                """
                SELECT * FROM report_transfers
                WHERE report_id = ? AND destination = ?
                """,
                (report_id, destination),
            ).fetchone()
        if transfer is None:  # pragma: no cover - DB 무결성 방어
            raise RuntimeError("등록한 보고서 전송 큐를 조회할 수 없습니다.")
        return transfer

    def register_report_run(
        self,
        *,
        report_id: str,
        source_job_id: str | None,
        request_number: str,
        experiment_code: str,
        equipment_code: str,
        operator_id: str,
        storage_key: str,
        package_relative_path: str,
        package_file_name: str,
        package_size_bytes: int,
        package_sha256: str,
        report_options_json: str | None,
        generated_at: str,
        is_test: bool = False,
    ) -> dict[str, Any]:
        """전송 여부와 무관하게 생성 완료된 보고서를 등록한다."""
        with self.transaction() as connection:
            existing_report = connection.execute(
                "SELECT version_no FROM report_runs WHERE report_id = ?",
                (report_id,),
            ).fetchone()
            if existing_report is not None:
                version_no = int(existing_report["version_no"])
            elif source_job_id:
                version_row = connection.execute(
                    """
                    SELECT COALESCE(MAX(version_no), 0) + 1 AS next_version
                    FROM report_runs WHERE source_job_id = ?
                    """,
                    (source_job_id,),
                ).fetchone()
                version_no = int(version_row["next_version"])
            else:
                version_no = 1
            connection.execute(
                """
                INSERT INTO report_runs (
                    report_id,
                    source_job_id,
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id,
                    version_no,
                    generation_status,
                    storage_key,
                    package_relative_path,
                    package_file_name,
                    package_size_bytes,
                    package_sha256,
                    report_options_json,
                    is_test,
                    generated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'READY', ?, ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    request_number = VALUES(request_number),
                    experiment_code = VALUES(experiment_code),
                    equipment_code = VALUES(equipment_code),
                    operator_id = VALUES(operator_id),
                    generation_status = 'READY',
                    storage_key = VALUES(storage_key),
                    package_relative_path = VALUES(package_relative_path),
                    package_file_name = VALUES(package_file_name),
                    package_size_bytes = VALUES(package_size_bytes),
                    package_sha256 = VALUES(package_sha256),
                    report_options_json = VALUES(report_options_json),
                    is_test = VALUES(is_test),
                    deleted_at = NULL,
                    deleted_by = NULL,
                    delete_reason = NULL,
                    generated_at = VALUES(generated_at),
                    updated_at = CURRENT_TIMESTAMP(6)
                """,
                (
                    report_id,
                    source_job_id,
                    request_number,
                    experiment_code,
                    equipment_code,
                    operator_id,
                    version_no,
                    storage_key,
                    package_relative_path,
                    package_file_name,
                    package_size_bytes,
                    package_sha256,
                    report_options_json,
                    bool(is_test),
                    generated_at,
                ),
            )
            row = connection.execute(
                "SELECT * FROM report_runs WHERE report_id = ?",
                (report_id,),
            ).fetchone()
        if row is None:  # pragma: no cover - DB 무결성 방어
            raise RuntimeError("등록한 보고서 생성 기록을 조회할 수 없습니다.")
        return row

    def upsert_report_artifact(
        self,
        *,
        artifact_id: str,
        report_id: str,
        source_job_id: str | None,
        artifact_type: str,
        storage_key: str,
        relative_path: str,
        file_name: str,
        size_bytes: int,
        sha256: str | None,
        retention_until: Any = None,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO report_artifacts (
                    artifact_id,
                    report_id,
                    source_job_id,
                    artifact_type,
                    storage_key,
                    relative_path,
                    file_name,
                    size_bytes,
                    sha256,
                    retention_until
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON DUPLICATE KEY UPDATE
                    source_job_id = VALUES(source_job_id),
                    artifact_type = VALUES(artifact_type),
                    file_name = VALUES(file_name),
                    size_bytes = VALUES(size_bytes),
                    sha256 = VALUES(sha256),
                    retention_until = VALUES(retention_until),
                    trash_relative_path = NULL,
                    deleted_at = NULL,
                    updated_at = CURRENT_TIMESTAMP(6)
                """,
                (
                    artifact_id,
                    report_id,
                    source_job_id,
                    artifact_type,
                    storage_key,
                    relative_path,
                    file_name,
                    int(size_bytes),
                    sha256,
                    retention_until,
                ),
            )

    def list_report_artifacts(self, report_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM report_artifacts
                WHERE report_id = ?
                ORDER BY deleted_at IS NOT NULL, artifact_type, file_name
                """,
                (report_id,),
            ).fetchall()

    def fetch_report_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM report_artifacts WHERE artifact_id = ?",
                (artifact_id,),
            ).fetchone()

    def count_active_artifact_references(
        self,
        relative_path: str,
        *,
        excluding_report_id: str,
    ) -> int:
        """Count other live reports that still reference the same stored file."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT COUNT(DISTINCT report_id) AS reference_count
                FROM report_artifacts
                WHERE relative_path = ?
                  AND report_id <> ?
                  AND deleted_at IS NULL
                """,
                (relative_path, excluding_report_id),
            ).fetchone()
        return int((row or {}).get("reference_count") or 0)

    def list_report_management(
        self,
        *,
        query: str = "",
        experiment_code: str = "",
        transfer_status: str = "",
        date_from: str = "",
        date_to: str = "",
        sort_by: str = "createdAt",
        sort_dir: str = "desc",
        include_deleted: bool = False,
        limit: int = 300,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if not include_deleted:
            clauses.append("r.deleted_at IS NULL")
        if query.strip():
            like = f"%{query.strip()}%"
            clauses.append(
                "(r.report_id LIKE ? OR r.request_number LIKE ? "
                "OR r.equipment_code LIKE ? OR r.operator_id LIKE ?)"
            )
            params.extend([like, like, like, like])
        experiment_codes = [
            value.strip() for value in experiment_code.split(",") if value.strip()
        ]
        if experiment_codes:
            clauses.append(
                f"r.experiment_code IN ({','.join('?' for _ in experiment_codes)})"
            )
            params.extend(experiment_codes)
        transfer_statuses = [
            value.strip().upper()
            for value in transfer_status.split(",")
            if value.strip()
        ]
        if transfer_statuses:
            queued_statuses = [
                value for value in transfer_statuses if value != "NOT_QUEUED"
            ]
            status_parts: list[str] = []
            if "NOT_QUEUED" in transfer_statuses:
                status_parts.append("t.transfer_id IS NULL")
            if queued_statuses:
                status_parts.append(
                    f"t.status IN ({','.join('?' for _ in queued_statuses)})"
                )
                params.extend(queued_statuses)
            clauses.append("(" + " OR ".join(status_parts) + ")")
        if date_from.strip():
            clauses.append("DATE(r.created_at) >= ?")
            params.append(date_from.strip())
        if date_to.strip():
            clauses.append("DATE(r.created_at) <= ?")
            params.append(date_to.strip())
        sort_columns = {
            "createdAt": "r.created_at",
            "requestNumber": "r.request_number",
            "experimentCode": "r.experiment_code",
            "equipmentCode": "r.equipment_code",
            "operatorId": "r.operator_id",
            "status": "r.status",
            "transferStatus": "COALESCE(t.status, 'NOT_QUEUED')",
            "fileSize": "artifact_size_bytes",
            "retentionUntil": "r.retention_until",
            "lastError": "t.last_error_message",
        }
        order_column = sort_columns.get(sort_by, sort_columns["createdAt"])
        order_direction = "ASC" if sort_dir.casefold() == "asc" else "DESC"
        params.extend(
            [max(1, min(int(limit), 1000)), max(0, int(offset))]
        )
        with self._connect() as connection:
            return connection.execute(
                f"""
                SELECT
                    r.*,
                    t.transfer_id,
                    t.status AS transfer_status,
                    t.attempt_count,
                    t.max_attempts,
                    t.completed_at AS transfer_completed_at,
                    t.last_error_code,
                    t.last_error_message,
                    t.external_tracking_id,
                    COALESCE(a.artifact_count, 0) AS artifact_count,
                    COALESCE(a.artifact_size_bytes, 0) AS artifact_size_bytes,
                    COALESCE(a.active_artifact_count, 0) AS active_artifact_count
                FROM report_runs r
                LEFT JOIN report_transfers t
                    ON t.report_id = r.report_id AND t.destination = 'LIMS'
                LEFT JOIN (
                    SELECT
                        report_id,
                        COUNT(*) AS artifact_count,
                        COALESCE(SUM(CASE WHEN deleted_at IS NULL
                            THEN size_bytes ELSE 0 END), 0) AS artifact_size_bytes,
                        COALESCE(SUM(CASE WHEN deleted_at IS NULL
                            THEN 1 ELSE 0 END), 0) AS active_artifact_count
                    FROM report_artifacts
                    GROUP BY report_id
                ) a ON a.report_id = r.report_id
                WHERE {' AND '.join(clauses)}
                ORDER BY {order_column} {order_direction}, r.report_id DESC
                LIMIT ? OFFSET ?
                """,
                tuple(params),
            ).fetchall()

    def count_report_management(
        self,
        *,
        query: str = "",
        experiment_code: str = "",
        transfer_status: str = "",
        date_from: str = "",
        date_to: str = "",
        include_deleted: bool = False,
    ) -> int:
        clauses = ["1 = 1"]
        params: list[Any] = []
        if not include_deleted:
            clauses.append("r.deleted_at IS NULL")
        if query.strip():
            like = f"%{query.strip()}%"
            clauses.append(
                "(r.report_id LIKE ? OR r.request_number LIKE ? "
                "OR r.equipment_code LIKE ? OR r.operator_id LIKE ?)"
            )
            params.extend([like, like, like, like])
        experiment_codes = [
            value.strip() for value in experiment_code.split(",") if value.strip()
        ]
        if experiment_codes:
            clauses.append(
                f"r.experiment_code IN ({','.join('?' for _ in experiment_codes)})"
            )
            params.extend(experiment_codes)
        transfer_statuses = [
            value.strip().upper()
            for value in transfer_status.split(",")
            if value.strip()
        ]
        if transfer_statuses:
            queued_statuses = [
                value for value in transfer_statuses if value != "NOT_QUEUED"
            ]
            status_parts: list[str] = []
            if "NOT_QUEUED" in transfer_statuses:
                status_parts.append("t.transfer_id IS NULL")
            if queued_statuses:
                status_parts.append(
                    f"t.status IN ({','.join('?' for _ in queued_statuses)})"
                )
                params.extend(queued_statuses)
            clauses.append("(" + " OR ".join(status_parts) + ")")
        if date_from.strip():
            clauses.append("DATE(r.created_at) >= ?")
            params.append(date_from.strip())
        if date_to.strip():
            clauses.append("DATE(r.created_at) <= ?")
            params.append(date_to.strip())
        with self._connect() as connection:
            row = connection.execute(
                f"""
                SELECT COUNT(*) AS report_count
                FROM report_runs r
                LEFT JOIN report_transfers t
                    ON t.report_id = r.report_id AND t.destination = 'LIMS'
                WHERE {' AND '.join(clauses)}
                """,
                tuple(params),
            ).fetchone()
        return int((row or {}).get("report_count") or 0)

    def fetch_report_management(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    r.*,
                    t.transfer_id,
                    t.status AS transfer_status,
                    t.attempt_count,
                    t.max_attempts,
                    t.requested_at,
                    t.started_at AS transfer_started_at,
                    t.completed_at AS transfer_completed_at,
                    t.last_error_code,
                    t.last_error_message,
                    t.external_tracking_id
                FROM report_runs r
                LEFT JOIN report_transfers t
                    ON t.report_id = r.report_id AND t.destination = 'LIMS'
                WHERE r.report_id = ?
                """,
                (report_id,),
            ).fetchone()
            if row is None:
                return None
            row["artifacts"] = connection.execute(
                """
                SELECT * FROM report_artifacts
                WHERE report_id = ?
                ORDER BY deleted_at IS NOT NULL, artifact_type, file_name
                """,
                (report_id,),
            ).fetchall()
            transfer_id = row.get("transfer_id")
            row["attempts"] = (
                connection.execute(
                    """
                    SELECT * FROM report_transfer_attempts
                    WHERE transfer_id = ? ORDER BY attempt_no DESC
                    """,
                    (transfer_id,),
                ).fetchall()
                if transfer_id
                else []
            )
            return row

    def update_report_lifecycle(
        self,
        report_id: str,
        *,
        is_test: bool | None = None,
        pinned: bool | None = None,
        retention_until: Any = None,
        update_retention: bool = False,
    ) -> None:
        assignments: list[str] = []
        values: list[Any] = []
        if is_test is not None:
            assignments.append("is_test = ?")
            values.append(bool(is_test))
        if pinned is not None:
            assignments.append("pinned = ?")
            values.append(bool(pinned))
        if update_retention:
            assignments.append("retention_until = ?")
            values.append(retention_until)
        if not assignments:
            return
        values.append(report_id)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE report_runs SET {', '.join(assignments)} WHERE report_id = ?",
                tuple(values),
            )

    def ensure_report_retention_policies(
        self,
        defaults: dict[str, dict[str, Any]],
    ) -> None:
        """환경 기본값으로 누락된 정책만 채우고 운영자가 저장한 값은 보존한다."""
        with self.transaction() as connection:
            for policy_key, policy in defaults.items():
                connection.execute(
                    """
                    INSERT IGNORE INTO report_retention_policies (
                        policy_key,
                        retention_days,
                        auto_cleanup_enabled,
                        description,
                        updated_by
                    ) VALUES (?, ?, ?, ?, 'environment-default')
                    """,
                    (
                        policy_key,
                        int(policy["retention_days"]),
                        bool(policy.get("auto_cleanup_enabled", True)),
                        str(policy["description"]),
                    ),
                )

    def list_report_retention_policies(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM report_retention_policies
                ORDER BY FIELD(
                    policy_key,
                    'UNSENT_TEST',
                    'FAILED_OR_CANCELLED',
                    'COMPLETED',
                    'TRASH'
                )
                """
            ).fetchall()

    def update_report_retention_policies(
        self,
        policies: dict[str, dict[str, Any]],
        *,
        actor: str,
    ) -> None:
        with self.transaction() as connection:
            for policy_key, policy in policies.items():
                connection.execute(
                    """
                    UPDATE report_retention_policies
                    SET retention_days = ?,
                        auto_cleanup_enabled = ?,
                        updated_by = ?,
                        updated_at = CURRENT_TIMESTAMP(6)
                    WHERE policy_key = ?
                    """,
                    (
                        int(policy["retention_days"]),
                        bool(policy["auto_cleanup_enabled"]),
                        actor,
                        policy_key,
                    ),
                )

    def retry_report_transfer(self, report_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE report_transfers
                SET status = 'PENDING',
                    next_retry_at = NULL,
                    lease_owner = NULL,
                    lease_until = NULL,
                    completed_at = NULL,
                    last_error_code = NULL,
                    last_error_message = NULL,
                    updated_at = CURRENT_TIMESTAMP(6)
                WHERE report_id = ? AND status IN ('FAILED', 'CANCELLED')
                """,
                (report_id,),
            )
            return bool(cursor.rowcount)

    def cancel_report_transfer(self, report_id: str) -> bool:
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE report_transfers
                SET status = 'CANCELLED',
                    completed_at = CURRENT_TIMESTAMP(6),
                    lease_owner = NULL,
                    lease_until = NULL,
                    next_retry_at = NULL,
                    updated_at = CURRENT_TIMESTAMP(6)
                WHERE report_id = ? AND status IN ('PENDING', 'RETRY_WAIT')
                """,
                (report_id,),
            )
            return bool(cursor.rowcount)

    def mark_report_trashed(
        self,
        report_id: str,
        *,
        deleted_by: str,
        reason: str,
        artifacts: list[tuple[str, str]],
    ) -> None:
        with self.transaction() as connection:
            for artifact_id, trash_relative_path in artifacts:
                connection.execute(
                    """
                    UPDATE report_artifacts
                    SET trash_relative_path = ?,
                        deleted_at = CURRENT_TIMESTAMP(6),
                        updated_at = CURRENT_TIMESTAMP(6)
                    WHERE artifact_id = ? AND report_id = ?
                    """,
                    (trash_relative_path, artifact_id, report_id),
                )
            connection.execute(
                """
                UPDATE report_runs
                SET deleted_at = CURRENT_TIMESTAMP(6),
                    deleted_by = ?,
                    delete_reason = ?,
                    generation_status = 'TRASHED',
                    updated_at = CURRENT_TIMESTAMP(6)
                WHERE report_id = ?
                """,
                (deleted_by, reason, report_id),
            )

    def fetch_report_run(self, report_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM report_runs WHERE report_id = ?",
                (report_id,),
            ).fetchone()

    def insert_report_regeneration_request(
        self,
        *,
        signal_id: str,
        report_id: str,
        source_job_id: str | None,
        requested_at: str | None,
        requested_by: str | None,
        reason: str | None,
        prompt: str,
        idempotency_key: str,
        received_at: str,
    ) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO report_regeneration_requests (
                    signal_id,
                    report_id,
                    source_job_id,
                    requested_at,
                    requested_by,
                    reason,
                    prompt,
                    status,
                    idempotency_key,
                    received_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'RECEIVED', ?, ?)
                """,
                (
                    signal_id,
                    report_id,
                    source_job_id,
                    requested_at,
                    requested_by,
                    reason,
                    prompt,
                    idempotency_key,
                    received_at,
                ),
            )

    def fetch_report_regeneration_request(
        self,
        signal_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM report_regeneration_requests
                WHERE signal_id = ?
                """,
                (signal_id,),
            ).fetchone()

    def fetch_report_run_by_source_job(
        self,
        source_job_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM report_runs
                WHERE source_job_id = ?
                ORDER BY version_no DESC, created_at DESC
                LIMIT 1
                """,
                (source_job_id,),
            ).fetchone()

    def fetch_report_transfer(self, transfer_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                "SELECT * FROM report_transfers WHERE transfer_id = ?",
                (transfer_id,),
            ).fetchone()

    def fetch_report_transfer_for_report(
        self,
        report_id: str,
        destination: str = "LIMS",
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            return connection.execute(
                """
                SELECT * FROM report_transfers
                WHERE report_id = ? AND destination = ?
                """,
                (report_id, destination),
            ).fetchone()

    def delete_idempotency(self, endpoint: str, idempotency_key: str) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                DELETE FROM idempotency_records
                WHERE endpoint = ? AND idempotency_key = ?
                """,
                (endpoint, idempotency_key),
            )


def _validate_columns(
    table: str,
    values: dict[str, Any],
    allowed: set[str],
) -> None:
    invalid = sorted(set(values) - allowed)
    if invalid:
        raise ValueError(
            f"{table}에 허용되지 않은 컬럼입니다: {', '.join(invalid)}"
        )
