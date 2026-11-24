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


def change_validity(connection, minutes, default, actor_id):
    if type(minutes) is not int or not 1 <= minutes <= MAX_SSO_VALID_MINUTES:
        raise ApiException(400, "INVALID_SSO_VALIDITY", "SSO 유효기간은 1~43200분으로 입력하세요.")
    connection.execute(
        "INSERT IGNORE INTO auth_sso_policy (policy_id, valid_minutes) VALUES (1, ?)",
        (default,),
    )
    old = connection.execute(
        "SELECT valid_minutes FROM auth_sso_policy WHERE policy_id = 1 FOR UPDATE"
    ).fetchone()["valid_minutes"]
    now = utc_now()
    # Increasing policy must not resurrect sessions that have already expired.
    connection.execute(
        "UPDATE auth_sessions SET sso_authenticated_at = NULL "
        "WHERE sso_authenticated_at <= ?",
        (now - timedelta(minutes=min(int(old), minutes)),),
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
    connection.execute("DELETE FROM auth_oidc_states WHERE user_id = ?", (user_id,))
