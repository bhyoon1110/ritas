"""Persisted SSO validity and account-wide revocation without storing credentials."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from .errors import ApiException

MAX_SSO_VALID_MINUTES = 30 * 24 * 60


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def valid_minutes(connection, default):
    row = connection.execute(
        "SELECT valid_minutes FROM auth_sso_policy WHERE policy_id = 1"
    ).fetchone()
    return int(row["valid_minutes"]) if row else int(default)


def lock_policy(connection, default):
    # Authentication completion locks policy before the account generation/session,
    # keeping policy shrink/extension and grant writes in one consistent order.
    connection.execute(
        "INSERT IGNORE INTO auth_sso_policy (policy_id, valid_minutes) VALUES (1, ?)",
        (default,),
    )
    return int(connection.execute(
        "SELECT valid_minutes FROM auth_sso_policy WHERE policy_id = 1 FOR UPDATE"
    ).fetchone()["valid_minutes"])


def authenticated_at(connection, user_id, identity, generation):
    if not identity:
        return None
    row = connection.execute(
        "SELECT authenticated_at FROM auth_sso_grants "
        "WHERE user_id = ? AND provider = ? AND subject = ? AND generation = ?",
        (user_id, identity["provider"], identity["subject"], generation),
    ).fetchone()
    if row and row["authenticated_at"] == identity.get("last_authenticated_at"):
        return row["authenticated_at"]
    return None


def record_authentication(connection, user_id, provider, subject, generation, now):
    # Caller holds the policy and account-generation locks and has revalidated the
    # originating local session. Do not backfill this table from legacy timestamps.
    connection.execute("DELETE FROM auth_sso_grants WHERE user_id = ?", (user_id,))
    connection.execute(
        "INSERT INTO auth_sso_grants (user_id, provider, subject, generation, authenticated_at) "
        "VALUES (?, ?, ?, ?, ?)", (user_id, provider, subject, generation, now),
    )


def reset_generation(connection, user_id):
    row = connection.execute(
        "SELECT generation FROM auth_sso_resets WHERE user_id = ?", (user_id,)
    ).fetchone()
    return int(row["generation"]) if row else 0


def lock_generation(connection, user_id):
    connection.execute(
        "INSERT IGNORE INTO auth_sso_resets (user_id, generation) VALUES (?, 0)",
        (user_id,),
    )
    row = connection.execute(
        "SELECT generation FROM auth_sso_resets WHERE user_id = ? FOR UPDATE",
        (user_id,),
    ).fetchone()
    return int(row["generation"])


def require_generation(connection, user_id, expected):
    if lock_generation(connection, user_id) != expected:
        raise ApiException(409, "SSO_RESET_DURING_AUTH",
                           "SSO 인증이 초기화되었습니다. 인증을 다시 시작해 주세요.")


def require_active_session(connection, user_id, session_id):
    # Re-check after the upstream call. A callback from a logged-out, expired or
    # suspended session must not create account-wide proof for a later login.
    row = connection.execute(
        "SELECT s.session_id FROM auth_sessions s JOIN app_users u ON u.user_id = s.user_id "
        "WHERE s.session_id = ? AND s.user_id = ? AND s.revoked_at IS NULL "
        "AND s.expires_at > ? AND u.status = 'ACTIVE' FOR UPDATE",
        (session_id, user_id, utc_now()),
    ).fetchone()
    if not row:
        raise ApiException(401, "AUTHENTICATION_REQUIRED", "SSO 인증을 완료하려면 다시 로그인하세요.")


def change_validity(connection, minutes, default, actor_id):
    if type(minutes) is not int or not 1 <= minutes <= MAX_SSO_VALID_MINUTES:
        raise ApiException(400, "INVALID_SSO_VALIDITY", "SSO 유효기간은 1~43200분으로 입력하세요.")
    old = lock_policy(connection, default)
    now = utc_now()
    # Apply the shorter policy first so increasing it cannot revive expired proof.
    connection.execute(
        "UPDATE auth_sessions SET sso_authenticated_at = NULL "
        "WHERE sso_authenticated_at <= ?",
        (now - timedelta(minutes=min(int(old), minutes)),),
    )
    connection.execute(
        "UPDATE sso_identities SET last_authenticated_at = NULL "
        "WHERE last_authenticated_at <= ?",
        (now - timedelta(minutes=min(int(old), minutes)),),
    )
    connection.execute(
        "DELETE FROM auth_sso_grants WHERE authenticated_at <= ?",
        (now - timedelta(minutes=min(old, minutes)),),
    )
    connection.execute(
        "UPDATE auth_sso_policy SET valid_minutes = ?, updated_by = ?, updated_at = ? "
        "WHERE policy_id = 1", (minutes, actor_id, now),
    )
    return int(old)


def reset_authentication(connection, user_id, actor_id):
    if not connection.execute("SELECT user_id FROM app_users WHERE user_id = ?", (user_id,)).fetchone():
        raise ApiException(404, "USER_NOT_FOUND", "회원을 찾을 수 없습니다.")
    generation = lock_generation(connection, user_id)
    now = utc_now()
    connection.execute(
        "UPDATE auth_sso_resets SET generation = ?, reset_at = ?, reset_by = ? WHERE user_id = ?",
        (generation + 1, now, actor_id, user_id),
    )
    connection.execute(
        "UPDATE auth_sessions SET sso_authenticated_at = NULL WHERE user_id = ?", (user_id,),
    )
    connection.execute(
        "UPDATE sso_identities SET active = FALSE, last_authenticated_at = NULL, updated_at = ? "
        "WHERE user_id = ?", (now, user_id),
    )
    connection.execute("DELETE FROM auth_sso_grants WHERE user_id = ?", (user_id,))
    connection.execute("DELETE FROM auth_oidc_states WHERE user_id = ?", (user_id,))
