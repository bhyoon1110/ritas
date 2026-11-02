-- Edge MariaDB only; no Oracle/LIMS schema change.
-- Apply while the Spring report transfer scheduler is disabled.
-- Safe to repeat on MariaDB 10.5+. Existing completed registrations are NOT
-- backfilled as durable document completions: verify them with LIMS first.
ALTER TABLE report_transfers
    ADD COLUMN IF NOT EXISTS failure_count INT NOT NULL DEFAULT 0
        COMMENT '연속 오류 횟수. 정상 폴링 제외, 수동 재시도 시 초기화',
    ADD COLUMN IF NOT EXISTS delivery_state_json LONGTEXT
        COMMENT '문서와 메일 단계 체크포인트. 자격증명과 메일 본문 저장 금지',
    ADD COLUMN IF NOT EXISTS document_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS mail_status VARCHAR(32),
    ADD COLUMN IF NOT EXISTS document_completed_at DATETIME(6)
        COMMENT '해당 regenSeq의 fileNo와 영구 적재 완료를 확인한 시각';

-- attempt_count remains monotonic, including polls, to preserve the unique
-- (transfer_id, attempt_no) audit history. Never reset it for a manual retry.
