from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import make_url

from core.config import MOSCOW_TZ

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_OUTPUT_DIR = Path("/tmp")
UNSUPPORTED_SETTINGS = {"transaction_timeout"}


class BackupError(RuntimeError):
    """Raised when backing up or restoring the database fails."""


@dataclass(frozen=True)
class DatabaseConnectionInfo:
    host: str
    port: int
    user: str
    password: str
    database: str

    @classmethod
    def from_url(cls, url: str) -> "DatabaseConnectionInfo":
        parsed = make_url(url)
        if not parsed.username or not parsed.password or not parsed.host or not parsed.database:
            raise BackupError("DATABASE_URL must include username, password, host and database name.")
        return cls(
            host=parsed.host or "localhost",
            port=parsed.port or 5432,
            user=parsed.username,
            password=str(parsed.password or ""),
            database=parsed.database or "",
        )


async def _run_subprocess(
    *cmd: str,
    env: Optional[dict[str, str]] = None,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """
    Run a subprocess command asynchronously and raise BackupError on failure.
    """

    process = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise BackupError(f"Command {' '.join(cmd)} timed out after {timeout} seconds") from exc

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace")
        stdout_text = stdout.decode("utf-8", errors="replace")
        raise BackupError(
            f"Command {' '.join(cmd)} failed with exit code {process.returncode}.\n"
            f"stdout: {stdout_text}\nstderr: {stderr_text}"
        )


def _prepare_connection_env(info: DatabaseConnectionInfo) -> dict[str, str]:
    env = dict(os.environ)
    env["PGPASSWORD"] = info.password
    return env


def _sanitize_dump_file(sql_path: Path) -> None:
    """
    Remove SET statements that reference settings unsupported by the target server.
    """

    try:
        original = sql_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return

    lines = original.splitlines(keepends=True)
    filtered = [
        line
        for line in lines
        if not any(line.strip().lower().startswith(f"set {setting}".lower()) for setting in UNSUPPORTED_SETTINGS)
    ]

    if filtered != lines:
        sql_path.write_text("".join(filtered), encoding="utf-8")


async def create_db_backup(
    output_dir: Path | None = None,
    *,
    filename_prefix: str = "db_backup",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    """
    Create a PostgreSQL database backup using pg_dump and return the resulting file path.
    """

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise BackupError("DATABASE_URL environment variable is not set.")

    conn = DatabaseConnectionInfo.from_url(db_url)
    backup_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(MOSCOW_TZ).strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"{filename_prefix}_{timestamp}.sql"

    cmd = [
        "pg_dump",
        "-h",
        conn.host,
        "-p",
        str(conn.port),
        "-U",
        conn.user,
        "-d",
        conn.database,
        "--clean",
        "--if-exists",
        "--no-owner",
        "--no-privileges",
        "-f",
        str(backup_path),
    ]

    await _run_subprocess(*cmd, env=_prepare_connection_env(conn), timeout=timeout)

    if not backup_path.exists():
        raise BackupError("Backup file was not created by pg_dump.")

    _sanitize_dump_file(backup_path)
    return backup_path


async def restore_db_backup(
    sql_file: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    """
    Restore a PostgreSQL database from the provided SQL dump file using psql.
    """

    if not sql_file.exists():
        raise BackupError(f"Backup file {sql_file} does not exist.")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise BackupError("DATABASE_URL environment variable is not set.")

    conn = DatabaseConnectionInfo.from_url(db_url)
    env = _prepare_connection_env(conn)

    cmd = [
        "psql",
        "-h",
        conn.host,
        "-p",
        str(conn.port),
        "-U",
        conn.user,
        "-d",
        conn.database,
        "-v",
        "ON_ERROR_STOP=1",
        "-f",
        str(sql_file),
    ]

    _sanitize_dump_file(sql_file)
    await _run_subprocess(*cmd, env=env, timeout=timeout)

