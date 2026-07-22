-- RIST Edge 웹 회원, 프로젝트 권한, 사내 SSO 연결 스키마
--
-- 적용 예시:
--   mysql --default-character-set=utf8mb4 \
--     -h 127.0.0.1 -P 3306 -u root -p rist_edge \
--     < mariadb_auth_migration.sql
--
-- 기존 테이블과 회원 데이터는 삭제하지 않는다. 동일 스키마에 재실행해도
-- CREATE TABLE IF NOT EXISTS 정책에 따라 안전하게 종료된다.

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS app_users (
    user_id VARCHAR(36) NOT NULL COMMENT '로컬 회원 UUID',
    email VARCHAR(255) NOT NULL COMMENT '로그인 이메일. 소문자로 정규화',
    password_hash VARCHAR(512) NOT NULL COMMENT 'scrypt 비밀번호 해시. 원문 저장 금지',
    display_name VARCHAR(100) NOT NULL COMMENT '화면과 감사 로그에 표시할 이름',
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING'
        COMMENT '회원 상태: PENDING(승인 대기), ACTIVE(사용 가능), SUSPENDED(중지)',
    last_login_at DATETIME(6) COMMENT '최근 로컬 로그인 성공 시각',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '회원 가입 시각',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT '회원 정보 최종 변경 시각',
    PRIMARY KEY (user_id),
    UNIQUE KEY uq_app_users_email (email),
    KEY idx_app_users_status (status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='Edge 웹 화면에 로그인하는 로컬 회원과 관리자 승인 상태';

CREATE TABLE IF NOT EXISTS user_project_permissions (
    user_id VARCHAR(36) NOT NULL COMMENT '권한을 부여받은 app_users.user_id',
    project_code VARCHAR(32) NOT NULL COMMENT '접근 가능한 프로젝트: FTIR, RAMAN, XRD, TEM',
    granted_by VARCHAR(36) COMMENT '권한을 승인한 관리자 app_users.user_id',
    granted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '프로젝트 권한 승인 시각',
    PRIMARY KEY (user_id, project_code),
    CONSTRAINT fk_user_project_user FOREIGN KEY (user_id)
        REFERENCES app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_user_project_granter FOREIGN KEY (granted_by)
        REFERENCES app_users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='회원별 FTIR, RAMAN, XRD, TEM 화면 접근 및 보고서 생성 권한';

CREATE TABLE IF NOT EXISTS user_roles (
    user_id VARCHAR(36) NOT NULL COMMENT '역할을 부여받은 app_users.user_id',
    role_code VARCHAR(32) NOT NULL COMMENT 'ADMIN(회원/운영 관리) 또는 REPORT_SENDER(보고서 전송)',
    granted_by VARCHAR(36) COMMENT '역할을 승인한 관리자 app_users.user_id',
    granted_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '역할 승인 시각',
    PRIMARY KEY (user_id, role_code),
    CONSTRAINT fk_user_roles_user FOREIGN KEY (user_id)
        REFERENCES app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_user_roles_granter FOREIGN KEY (granted_by)
        REFERENCES app_users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='운영 관리자 및 LIMS 보고서 전송 역할';

CREATE TABLE IF NOT EXISTS sso_identities (
    identity_id VARCHAR(36) NOT NULL COMMENT 'SSO 연결 UUID',
    user_id VARCHAR(36) NOT NULL COMMENT 'SSO 계정과 연결된 로컬 회원',
    provider VARCHAR(64) NOT NULL COMMENT 'OIDC 공급자 식별자',
    subject VARCHAR(255) NOT NULL COMMENT 'OIDC sub. 공급자 내 불변 사용자 식별자',
    employee_id VARCHAR(100) COMMENT 'SSO가 제공한 사번 또는 업무 사용자 ID',
    email VARCHAR(255) COMMENT 'SSO가 제공한 이메일 스냅샷',
    display_name VARCHAR(100) COMMENT 'SSO가 제공한 표시 이름 스냅샷',
    active BOOLEAN NOT NULL DEFAULT TRUE COMMENT 'SSO 연결 사용 가능 여부',
    linked_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '로컬 회원과 SSO 계정을 처음 연결한 시각',
    last_authenticated_at DATETIME(6) COMMENT '해당 SSO 계정의 최근 인증 완료 시각',
    updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        ON UPDATE CURRENT_TIMESTAMP(6) COMMENT 'SSO 연결 정보 최종 변경 시각',
    PRIMARY KEY (identity_id),
    UNIQUE KEY uq_sso_provider_subject (provider, subject),
    UNIQUE KEY uq_sso_user_provider (user_id, provider),
    CONSTRAINT fk_sso_identity_user FOREIGN KEY (user_id)
        REFERENCES app_users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='로컬 회원과 사내 OIDC SSO 계정 연결 및 최근 인증 이력';

CREATE TABLE IF NOT EXISTS auth_sessions (
    session_id VARCHAR(36) NOT NULL COMMENT '로그인 세션 UUID',
    user_id VARCHAR(36) NOT NULL COMMENT '로그인한 app_users.user_id',
    token_hash CHAR(64) NOT NULL COMMENT '브라우저 쿠키 토큰의 SHA-256 해시. 원문 저장 금지',
    expires_at DATETIME(6) NOT NULL COMMENT '로컬 로그인 세션 만료 시각',
    sso_authenticated_at DATETIME(6) COMMENT '이 세션에서 최근 SSO 인증을 완료한 시각',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '로그인 세션 생성 시각',
    last_seen_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '세션을 마지막으로 사용한 시각',
    revoked_at DATETIME(6) COMMENT '로그아웃 또는 관리자 중지로 세션을 폐기한 시각',
    user_agent VARCHAR(512) COMMENT '보안 감사용 브라우저 User-Agent',
    remote_ip VARCHAR(64) COMMENT '세션 생성 시 클라이언트 IP',
    PRIMARY KEY (session_id),
    UNIQUE KEY uq_auth_sessions_token (token_hash),
    KEY idx_auth_sessions_user (user_id, expires_at, revoked_at),
    CONSTRAINT fk_auth_session_user FOREIGN KEY (user_id)
        REFERENCES app_users(user_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='원문 토큰을 저장하지 않는 Edge 웹 로그인 세션과 최근 SSO 재인증';

CREATE TABLE IF NOT EXISTS auth_oidc_states (
    state_hash CHAR(64) NOT NULL COMMENT 'OIDC state 원문의 SHA-256 해시',
    user_id VARCHAR(36) NOT NULL COMMENT 'SSO 연결/재인증을 시작한 로컬 회원',
    session_id VARCHAR(36) NOT NULL COMMENT 'SSO 인증 결과를 연결할 로그인 세션',
    code_verifier VARCHAR(128) NOT NULL COMMENT 'OIDC PKCE code verifier',
    return_to VARCHAR(512) NOT NULL COMMENT '인증 완료 후 같은 Edge 사이트 내 이동 경로',
    expires_at DATETIME(6) NOT NULL COMMENT '일회용 OIDC state 만료 시각',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT 'SSO 인증 시작 시각',
    PRIMARY KEY (state_hash),
    KEY idx_auth_oidc_expiry (expires_at),
    CONSTRAINT fk_auth_oidc_user FOREIGN KEY (user_id)
        REFERENCES app_users(user_id) ON DELETE CASCADE,
    CONSTRAINT fk_auth_oidc_session FOREIGN KEY (session_id)
        REFERENCES auth_sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='SSO authorization-code 및 PKCE 요청의 단기 일회용 상태';

CREATE TABLE IF NOT EXISTS auth_audit_events (
    event_id BIGINT NOT NULL AUTO_INCREMENT COMMENT '인증 감사 이벤트 자동 증가 ID',
    user_id VARCHAR(36) COMMENT '관련 회원. 로그인 실패처럼 식별 전 이벤트는 NULL 가능',
    event_type VARCHAR(64) NOT NULL
        COMMENT 'SIGNUP, LOGIN, SSO_LINK, PERMISSION_CHANGE 등 이벤트 종류',
    success BOOLEAN NOT NULL COMMENT '이벤트 성공 여부',
    details_json LONGTEXT COMMENT '민감정보를 제외한 감사 상세 JSON',
    remote_ip VARCHAR(64) COMMENT '요청 클라이언트 IP',
    created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
        COMMENT '감사 이벤트 발생 시각',
    PRIMARY KEY (event_id),
    KEY idx_auth_audit_user (user_id, created_at),
    KEY idx_auth_audit_type (event_type, created_at),
    CONSTRAINT fk_auth_audit_user FOREIGN KEY (user_id)
        REFERENCES app_users(user_id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='회원가입, 로그인, SSO 연결, 권한 변경 및 전송 인증 감사 이력';
