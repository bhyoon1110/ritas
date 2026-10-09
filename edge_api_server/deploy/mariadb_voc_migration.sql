-- VOC schema. Run in the Edge database after the authentication schema (app_users).
-- Additive and safe to rerun. Never drops or modifies experiment/report data.
-- All application timestamps are stored as UTC.
CREATE TABLE IF NOT EXISTS voc_requests (
    voc_id VARCHAR(36) NOT NULL,
    project VARCHAR(16) NOT NULL,
    author_user_id VARCHAR(36) NOT NULL,
    author_login_id VARCHAR(255) NOT NULL,
    author_display_name VARCHAR(100) NOT NULL,
    request_key VARCHAR(36) NOT NULL,
    title VARCHAR(160) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    resolution_note TEXT NOT NULL,
    resolved_by VARCHAR(36),
    resolved_by_name VARCHAR(100),
    resolved_at DATETIME(6),
    confirmed_at DATETIME(6),
    version INT NOT NULL DEFAULT 1,
    created_at DATETIME(6) NOT NULL,
    updated_at DATETIME(6) NOT NULL,
    PRIMARY KEY (voc_id),
    UNIQUE KEY uq_voc_author_request (author_user_id, request_key),
    KEY idx_voc_author_updated (author_user_id, updated_at),
    KEY idx_voc_project_status (project, status, updated_at),
    CONSTRAINT fk_voc_author FOREIGN KEY (author_user_id)
        REFERENCES app_users(user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='실험별 VOC. 해당 실험 권한 회원과 관리자 조회, 시각은 UTC';

CREATE TABLE IF NOT EXISTS voc_events (
    event_id BIGINT NOT NULL AUTO_INCREMENT,
    voc_id VARCHAR(36) NOT NULL,
    actor_user_id VARCHAR(36) NOT NULL,
    actor_login_id VARCHAR(255) NOT NULL,
    actor_display_name VARCHAR(100) NOT NULL,
    action VARCHAR(32) NOT NULL,
    previous_status VARCHAR(32),
    status VARCHAR(32) NOT NULL,
    note TEXT NOT NULL,
    created_at DATETIME(6) NOT NULL,
    PRIMARY KEY (event_id),
    KEY idx_voc_events_request (voc_id, event_id),
    CONSTRAINT fk_voc_events_request FOREIGN KEY (voc_id)
        REFERENCES voc_requests(voc_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='VOC 등록, 관리자 조치, 작성자 확인의 변경 이력. 시각은 UTC';
