"""Password authentication and server-validated opaque browser sessions."""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentflow.config import get_settings
from agentflow.database import get_session
from agentflow.models import LoginAttempt, User, UserSession, utcnow

SESSION_COOKIE = "agentflow_session"
CSRF_HEADER = "X-CSRF-Token"
PASSWORDS = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2, hash_len=32, salt_len=16)
DUMMY_PASSWORD_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$PEQHn/cF9Po2Jmf7CBrrZA$"
    "4yCE1Sf/UQkmqtDnsu5SmdC9jg1PBtcS7aALrHZJg/g"
)


def validate_password(password: str) -> str:
    has_number = any("0" <= character <= "9" for character in password)
    has_symbol = any(
        not character.isalnum() and not character.isspace() for character in password
    )
    if len(password) != 8 or not has_number or not has_symbol:
        raise ValueError(
            "Password must be exactly 8 characters and include a number and a symbol."
        )
    return password


def hash_password(password: str) -> str:
    validate_password(password)
    return PASSWORDS.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return PASSWORDS.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_for_session(raw_token: str) -> str:
    return hmac.new(raw_token.encode("utf-8"), b"agentflow-csrf-v1", hashlib.sha256).hexdigest()


def normalize_login(value: str) -> str:
    return value.strip().casefold()


def ensure_allowed_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin is None:
        raise HTTPException(status_code=403, detail="A valid browser origin is required.")
    if origin not in get_settings().cors_origins:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


def create_session(session: Session, user: User) -> tuple[str, str, datetime]:
    settings = get_settings()
    raw_token = secrets.token_urlsafe(32)
    csrf_token = csrf_for_session(raw_token)
    expires_at = utcnow() + timedelta(hours=settings.session_ttl_hours)
    session.add(
        UserSession(
            token_hash=digest_token(raw_token),
            csrf_hash=digest_token(csrf_token),
            user_id=user.id,
            expires_at=expires_at,
            created_at=utcnow(),
            last_seen_at=utcnow(),
        )
    )
    return raw_token, csrf_token, expires_at


def set_session_cookie(response: Response, raw_token: str, expires_at: datetime) -> None:
    settings = get_settings()
    max_age = max(0, int((expires_at - utcnow()).total_seconds()))
    response.set_cookie(
        SESSION_COOKIE,
        raw_token,
        max_age=max_age,
        expires=expires_at,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(
        SESSION_COOKIE,
        httponly=True,
        secure=get_settings().session_cookie_secure,
        samesite="lax",
        path="/",
    )


def current_session(request: Request, session: Session) -> tuple[UserSession, User] | None:
    raw_token = request.cookies.get(SESSION_COOKIE)
    if not raw_token or len(raw_token) > 128:
        return None
    row = session.scalar(
        select(UserSession).where(UserSession.token_hash == digest_token(raw_token))
    )
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        return None
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    return row, user


def require_user(request: Request, session: Session = Depends(get_session)) -> User:
    current = current_session(request, session)
    if current is None:
        from agentflow.audit import correlation_id, record_event

        record_event(
            session,
            "authorization.unauthenticated",
            actor_kind="anonymous",
            resource_type="endpoint",
            resource_id=request.url.path[:80],
            outcome="denied",
            correlation_id=correlation_id(request),
        )
        session.commit()
        raise HTTPException(status_code=401, detail="Authentication required.")
    session_row, user = current
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        ensure_allowed_origin(request)
        csrf = request.headers.get(CSRF_HEADER, "")
        if not csrf or not hmac.compare_digest(digest_token(csrf), session_row.csrf_hash):
            raise HTTPException(status_code=403, detail="CSRF validation failed.")
    session_row.last_seen_at = utcnow()
    return user


def require_roles(*roles: str):
    def dependency(
        request: Request,
        user: User = Depends(require_user),
        session: Session = Depends(get_session),
    ) -> User:
        if user.role not in roles:
            from agentflow.audit import correlation_id, record_event

            record_event(
                session,
                "authorization.denied",
                actor=user,
                resource_type="endpoint",
                resource_id=request.url.path[:80],
                outcome="denied",
                correlation_id=correlation_id(request),
            )
            session.commit()
            raise HTTPException(status_code=403, detail="Permission denied.")
        return user

    return dependency


def login_throttle_key(request: Request, identifier: str) -> str:
    source = f"{identifier}\0{request.client.host if request.client else 'unknown'}"
    return digest_token(source)


def login_is_throttled(session: Session, key_hash: str) -> bool:
    cutoff = utcnow() - timedelta(minutes=15)
    failures = session.scalar(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.key_hash == key_hash,
            LoginAttempt.succeeded.is_(False),
            LoginAttempt.created_at >= cutoff,
        )
    )
    return bool(failures and failures >= 10)


def record_login_attempt(session: Session, key_hash: str, succeeded: bool) -> None:
    session.add(LoginAttempt(key_hash=key_hash, succeeded=succeeded, created_at=utcnow()))
