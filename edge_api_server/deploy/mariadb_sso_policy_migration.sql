-- Additive only: no existing identities, permissions or sessions are changed.
CREATE TABLE IF NOT EXISTS auth_sso_policy (
        policy_id TINYINT NOT NULL,
        valid_minutes INT NOT NULL COMMENT 'SSO 인증 유효기간(분), 관리자 설정',
        updated_by VARCHAR(36),
        updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
        PRIMARY KEY (policy_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='기존 인증에도 적용되는 전역 SSO 유효기간';

CREATE TABLE IF NOT EXISTS auth_sso_resets (
        user_id VARCHAR(36) NOT NULL,
        generation BIGINT NOT NULL DEFAULT 0,
        reset_at DATETIME(6),
        reset_by VARCHAR(36),
        PRIMARY KEY (user_id),
        CONSTRAINT fk_sso_reset_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='회원별 SSO 초기화 세대, 진행 중 인증의 재활성화 방지';

-- Empty on upgrade: every account must complete SSO once before cross-session reuse.
CREATE TABLE IF NOT EXISTS auth_sso_grants (
        user_id VARCHAR(36) NOT NULL,
        provider VARCHAR(64) NOT NULL,
        subject VARCHAR(255) NOT NULL,
        generation BIGINT NOT NULL,
        authenticated_at DATETIME(6) NOT NULL,
        PRIMARY KEY (user_id),
        CONSTRAINT fk_sso_grant_user FOREIGN KEY (user_id)
            REFERENCES app_users(user_id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
      COMMENT='새 SSO 인증으로만 발급하는 회원별 전송 인증, 기존 세션에서 복원 금지';
