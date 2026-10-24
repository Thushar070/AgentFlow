"""Local, interactive account bootstrap and emergency password recovery."""

import argparse
import getpass
import sys

from sqlalchemy import func, select, text

from agentflow.auth import hash_password, normalize_login
from agentflow.database import SessionLocal
from agentflow.models import AuditEvent, User, UserSession, utcnow


def _prompt_password() -> str:
    password = getpass.getpass(
        "Password (exactly 8 characters, including a number and symbol): "
    )
    confirm = getpass.getpass("Confirm password: ")
    if password != confirm:
        raise ValueError("Passwords do not match.")
    return password


def bootstrap_admin() -> int:
    with SessionLocal() as session:
        dialect = session.get_bind().dialect.name
        if dialect == "sqlite":
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        elif dialect == "postgresql":
            session.execute(text("SELECT pg_advisory_xact_lock(731945120014)"))
        else:
            raise RuntimeError(f"Administrator bootstrap is unsupported for {dialect}.")
        initialized = session.scalar(
            select(func.count(User.id)).where(User.role == "admin", User.is_active.is_(True))
        )
        if initialized:
            print("Administrator bootstrap is already closed.", file=sys.stderr)
            return 1
        username = input("Administrator username: ").strip()
        email = input("Administrator email: ").strip()
        password = _prompt_password()
        password_hash = hash_password(password)
        user = User(
            username=username,
            username_normalized=normalize_login(username),
            email=email,
            email_normalized=normalize_login(email),
            password_hash=password_hash,
            role="admin",
            is_active=True,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        session.add(user)
        try:
            session.flush()
            session.commit()
        except Exception:
            session.rollback()
            print(
                "Could not create administrator; username or email may already exist.",
                file=sys.stderr,
            )
            return 1
        print("Administrator created. Bootstrap is now closed.")
    return 0


def reset_admin_password(username: str) -> int:
    normalized = normalize_login(username)
    password_hash = hash_password(_prompt_password())
    with SessionLocal() as session:
        # PostgreSQL starts a transaction with the SELECT and locks the target
        # row so concurrent password resets cannot overwrite one another.
        user = session.scalar(
            select(User)
            .where(User.username_normalized == normalized, User.role == "admin")
            .with_for_update()
        )
        if user is None:
            print("Administrator not found.", file=sys.stderr)
            return 1
        user.password_hash = password_hash
        user.updated_at = utcnow()
        session.query(UserSession).filter(UserSession.user_id == user.id).update(
            {"revoked_at": utcnow()}, synchronize_session=False
        )
        session.add(
            AuditEvent(
                event_type="admin.password_reset_local",
                actor_user_id=None,
                actor_kind="local_operator",
                resource_type="user",
                resource_id=str(user.id),
                outcome="success",
                correlation_id="local-cli",
                details={},
                created_at=utcnow(),
            )
        )
        session.commit()
        print("Administrator password reset; existing sessions were revoked.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap-admin", help="Create the first administrator once")
    reset = commands.add_parser("reset-admin-password", help="Reset an admin password locally")
    reset.add_argument("username")
    args = parser.parse_args()
    try:
        if args.command == "bootstrap-admin":
            return bootstrap_admin()
        return reset_admin_password(args.username)
    except (EOFError, KeyboardInterrupt, ValueError) as exc:
        print(str(exc) or "Setup cancelled.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
