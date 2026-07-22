-- Idempotent recovery/grant helper for the built-in admin login ID.
-- Run this if the admin row was activated manually but ADMIN roles were not granted.

START TRANSACTION;

UPDATE app_users
SET status = 'ACTIVE', updated_at = CURRENT_TIMESTAMP(6)
WHERE login_id = 'admin';

INSERT IGNORE INTO user_roles (user_id, role_code, granted_by)
SELECT user_id, 'ADMIN', user_id FROM app_users WHERE login_id = 'admin';

INSERT IGNORE INTO user_roles (user_id, role_code, granted_by)
SELECT user_id, 'REPORT_SENDER', user_id FROM app_users WHERE login_id = 'admin';

INSERT IGNORE INTO user_project_permissions (user_id, project_code, granted_by)
SELECT user_id, project_code, user_id
FROM app_users
CROSS JOIN (
    SELECT 'FTIR' AS project_code
    UNION ALL SELECT 'RAMAN'
    UNION ALL SELECT 'XRD'
    UNION ALL SELECT 'TEM'
) AS projects
WHERE login_id = 'admin';

COMMIT;

SELECT
    u.login_id,
    u.status,
    GROUP_CONCAT(DISTINCT r.role_code ORDER BY r.role_code) AS roles,
    GROUP_CONCAT(DISTINCT p.project_code ORDER BY p.project_code) AS projects
FROM app_users AS u
LEFT JOIN user_roles AS r ON r.user_id = u.user_id
LEFT JOIN user_project_permissions AS p ON p.user_id = u.user_id
WHERE u.login_id = 'admin'
GROUP BY u.user_id, u.login_id, u.status;
