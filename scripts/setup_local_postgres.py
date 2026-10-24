"""Create a private, project-local PostgreSQL instance for AgentFlow development."""

from __future__ import annotations

import getpass
import secrets
import subprocess
from pathlib import Path
from urllib.parse import quote

import psycopg
from psycopg import sql
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".local" / "postgres"
DATA = LOCAL / "data"
SOCKET = LOCAL / "socket"
LOG = LOCAL / "postgres.log"
ENV_FILE = ROOT / ".env"
DATABASE_NAME = "agentflow"
DATABASE_ROLE = "agentflow"
PORT = 5432


def _postgres_bin(name: str) -> str:
    bindir = subprocess.run(
        ["pg_config", "--bindir"], check=True, capture_output=True, text=True
    ).stdout.strip()
    binary = str(Path(bindir) / name)
    if not Path(binary).is_file():
        raise RuntimeError(f"PostgreSQL executable not found: {binary}")
    return binary


def _env_values() -> tuple[list[str], dict[str, str]]:
    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    values: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition("=")
        if separator and name and not name.startswith("#"):
            values[name] = value
    return lines, values


def _save_env(lines: list[str], updates: dict[str, str]) -> None:
    remaining = set(updates)
    output: list[str] = []
    for line in lines:
        name, separator, _ = line.partition("=")
        if separator and name in updates:
            if name in remaining:
                output.append(f"{name}={updates[name]}")
                remaining.remove(name)
        else:
            output.append(line)
    output.extend(f"{name}={updates[name]}" for name in sorted(remaining))
    ENV_FILE.write_text("\n".join(output).rstrip() + "\n")
    ENV_FILE.chmod(0o600)


def _start_postgres() -> None:
    local_root = LOCAL.parent
    local_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    local_root.chmod(0o700)
    LOCAL.mkdir(parents=True, exist_ok=True, mode=0o700)
    LOCAL.chmod(0o700)
    SOCKET.mkdir(parents=True, exist_ok=True, mode=0o700)
    SOCKET.chmod(0o700)
    if not (DATA / "PG_VERSION").exists():
        DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
        subprocess.run(
            [
                _postgres_bin("initdb"),
                "-D",
                str(DATA),
                "--auth-local=peer",
                "--auth-host=scram-sha-256",
                "--username",
                getpass.getuser(),
            ],
            check=True,
        )
    pg_ctl = _postgres_bin("pg_ctl")
    status = subprocess.run([pg_ctl, "-D", str(DATA), "status"], capture_output=True)
    if status.returncode != 0:
        subprocess.run(
            [
                pg_ctl,
                "-D",
                str(DATA),
                "-l",
                str(LOG),
                "-o",
                f"-h 127.0.0.1 -p {PORT} -k {SOCKET}",
                "start",
                "-w",
            ],
            check=True,
        )


def _database_url(lines: list[str], values: dict[str, str]) -> tuple[str, str]:
    configured = values.get("DATABASE_URL", "")
    if configured and "replace-with-a-random-password" not in configured:
        parsed = make_url(configured)
        if parsed.drivername != "postgresql+psycopg" or parsed.username != DATABASE_ROLE:
            raise RuntimeError("DATABASE_URL must use the AgentFlow PostgreSQL role.")
        password = parsed.password or ""
        if not password:
            raise RuntimeError("DATABASE_URL must include the AgentFlow role password.")
    else:
        password = secrets.token_urlsafe(36)
    url = (
        f"postgresql+psycopg://{DATABASE_ROLE}:{quote(password, safe='')}"
        f"@127.0.0.1:{PORT}/{DATABASE_NAME}"
    )
    _save_env(lines, {"DATABASE_URL": url, "FREELLMAPI_MODEL": "auto"})
    return url, password


def _ensure_role_and_database(password: str) -> None:
    admin = getpass.getuser()
    with psycopg.connect(
        dbname="postgres", user=admin, host=str(SOCKET), port=PORT, autocommit=True
    ) as connection:
        exists = connection.execute(
            "SELECT 1 FROM pg_roles WHERE rolname = %s", (DATABASE_ROLE,)
        ).fetchone()
        statement = sql.SQL(
            "ALTER ROLE {} LOGIN PASSWORD {}" if exists else "CREATE ROLE {} LOGIN PASSWORD {}"
        )
        connection.execute(
            statement.format(sql.Identifier(DATABASE_ROLE), sql.Literal(password))
        )
        database_exists = connection.execute(
            "SELECT 1 FROM pg_database WHERE datname = %s", (DATABASE_NAME,)
        ).fetchone()
        if not database_exists:
            connection.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(DATABASE_NAME), sql.Identifier(DATABASE_ROLE)
                )
            )


def main() -> None:
    lines, values = _env_values()
    _start_postgres()
    url, password = _database_url(lines, values)
    _ensure_role_and_database(password)
    psycopg_url = make_url(url).set(drivername="postgresql").render_as_string(
        hide_password=False
    )
    with psycopg.connect(psycopg_url, connect_timeout=5) as connection:
        connection.execute("SELECT 1")
    print("Project-local PostgreSQL is running on 127.0.0.1:5432.")
    print("AgentFlow DATABASE_URL is saved in the private root .env file.")


if __name__ == "__main__":
    main()
