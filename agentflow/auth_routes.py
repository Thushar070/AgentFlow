"""Local account, session, and administrator account-management routes."""

import hmac
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agentflow.audit import correlation_id, record_event
from agentflow.auth import (
    DUMMY_PASSWORD_HASH,
    SESSION_COOKIE,
    clear_session_cookie,
    create_session,
    csrf_for_session,
    current_session,
    ensure_allowed_origin,
    hash_password,
    login_is_throttled,
    login_throttle_key,
    normalize_login,
    record_login_attempt,
    require_roles,
    require_user,
    set_session_cookie,
    verify_password,
)
from agentflow.database import get_session
from agentflow.models import User, UserSession, utcnow

router = APIRouter(prefix="/api/auth", tags=["authentication"])
admin_router = APIRouter(prefix="/api/admin", tags=["administration"])

Username = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=80)]
Email = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=3,
        max_length=254,
        pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$",
    ),
]
Password = Annotated[str, StringConstraints(min_length=12, max_length=1024)]


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username_or_email: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=3, max_length=254)
    ]
    password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]


class AccountCreateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: Username
    email: Email
    password: Password
    role: Literal["operator", "approver", "admin"] = "operator"


class RoleUpdateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["operator", "approver", "admin"]
    is_active: bool = True


class PasswordChangeInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    new_password: Password


def user_response(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "role": user.role,
        "is_active": user.is_active,
        "created_at": user.created_at,
    }


@router.post("/login")
def login(
    payload: LoginInput,
    request: Request,
    response: Response,
    session: Session = Depends(get_session),
):
    ensure_allowed_origin(request)
    identifier = normalize_login(payload.username_or_email)
    key_hash = login_throttle_key(request, identifier)
    user = session.scalar(
        select(User).where(
            (User.username_normalized == identifier) | (User.email_normalized == identifier)
        )
    )
    if login_is_throttled(session, key_hash):
        record_login_attempt(session, key_hash, False)
        record_event(
            session,
            "auth.login_failed",
            resource_type="session",
            outcome="rate_limited",
            correlation_id=correlation_id(request),
        )
        session.commit()
        raise HTTPException(status_code=429, detail="Too many sign-in attempts. Try again later.")
    password_hash = user.password_hash if user else DUMMY_PASSWORD_HASH
    password_valid = verify_password(password_hash, payload.password)
    if user is None or not user.is_active or not password_valid:
        record_login_attempt(session, key_hash, False)
        record_event(
            session,
            "auth.login_failed",
            actor=user,
            resource_type="session",
            outcome="denied",
            correlation_id=correlation_id(request),
        )
        session.commit()
        raise HTTPException(status_code=401, detail="Invalid username/email or password.")

    raw_token, csrf_token, expires_at = create_session(session, user)
    record_login_attempt(session, key_hash, True)
    record_event(
        session,
        "auth.login_succeeded",
        actor=user,
        resource_type="session",
        outcome="success",
        correlation_id=correlation_id(request),
    )
    session.commit()
    set_session_cookie(response, raw_token, expires_at)
    return {"user": user_response(user), "csrf_token": csrf_token}


@router.get("/me")
def who_am_i(
    request: Request, user: User = Depends(require_user), session: Session = Depends(get_session)
):
    current = current_session(request, session)
    if current is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    session_row, _ = current
    raw_token = request.cookies.get(SESSION_COOKIE, "")
    csrf_token = csrf_for_session(raw_token)
    from agentflow.auth import digest_token

    if not hmac.compare_digest(digest_token(csrf_token), session_row.csrf_hash):
        raise HTTPException(status_code=401, detail="Authentication required.")
    session.commit()
    return {"user": user_response(user), "csrf_token": csrf_token}


@router.post("/logout")
def logout(
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    current = current_session(request, session)
    if current:
        session_row, _ = current
        session_row.revoked_at = utcnow()
    record_event(
        session,
        "auth.logout",
        actor=user,
        resource_type="session",
        outcome="success",
        correlation_id=correlation_id(request),
    )
    session.commit()
    clear_session_cookie(response)
    return {"status": "signed_out"}


@router.post("/password")
def change_password(
    payload: PasswordChangeInput,
    request: Request,
    response: Response,
    user: User = Depends(require_user),
    session: Session = Depends(get_session),
):
    current = current_session(request, session)
    if current is None or not verify_password(user.password_hash, payload.current_password):
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    user.password_hash = hash_password(payload.new_password)
    user.updated_at = utcnow()
    session.execute(
        UserSession.__table__.update()
        .where(UserSession.user_id == user.id)
        .values(revoked_at=utcnow())
    )
    record_event(
        session,
        "auth.password_changed",
        actor=user,
        resource_type="user",
        resource_id=user.id,
        outcome="success",
        correlation_id=correlation_id(request),
    )
    session.commit()
    clear_session_cookie(response)
    return {"status": "password_changed_sign_in_again"}


@admin_router.get("/users")
def list_users(
    request: Request,
    _: User = Depends(require_roles("admin")),
    session: Session = Depends(get_session),
):
    return [user_response(user) for user in session.scalars(select(User).order_by(User.id)).all()]


@admin_router.post("/users", status_code=201)
def create_user(
    payload: AccountCreateInput,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    session: Session = Depends(get_session),
):
    username = payload.username.strip()
    email = str(payload.email).strip()
    user = User(
        username=username,
        username_normalized=normalize_login(username),
        email=email,
        email_normalized=normalize_login(email),
        password_hash=hash_password(payload.password),
        role=payload.role,
        is_active=True,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    session.add(user)
    try:
        session.flush()
        record_event(
            session,
            "admin.user_created",
            actor=actor,
            resource_type="user",
            resource_id=user.id,
            outcome="success",
            correlation_id=correlation_id(request),
            details={"role": user.role},
        )
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail="Username or email already exists.") from exc
    return user_response(user)


@admin_router.put("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    payload: RoleUpdateInput,
    request: Request,
    actor: User = Depends(require_roles("admin")),
    session: Session = Depends(get_session),
):
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found.")
    if user.role == "admin" and (payload.role != "admin" or not payload.is_active):
        active_admins = session.scalar(
            select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
        )
        if active_admins <= 1:
            raise HTTPException(
                status_code=409, detail="The last active administrator cannot be removed."
            )
    old_role = user.role
    user.role = payload.role
    user.is_active = payload.is_active
    user.updated_at = utcnow()
    if not user.is_active:
        session.execute(
            UserSession.__table__.update()
            .where(UserSession.user_id == user.id)
            .values(revoked_at=utcnow())
        )
    record_event(
        session,
        "admin.user_role_changed",
        actor=actor,
        resource_type="user",
        resource_id=user.id,
        outcome="success",
        correlation_id=correlation_id(request),
        details={"previous_role": old_role, "role": user.role, "is_active": user.is_active},
    )
    session.commit()
    return user_response(user)
