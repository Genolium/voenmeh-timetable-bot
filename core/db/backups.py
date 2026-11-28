from __future__ import annotations

import asyncio
import gzip
import os
import re
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from sqlalchemy.engine import make_url

from core.config import MOSCOW_TZ

DEFAULT_TIMEOUT_SECONDS = 300
DEFAULT_OUTPUT_DIR = Path(tempfile.gettempdir())
MAX_DECOMPRESSED_BYTES = 256 * 1024 * 1024  # 256 MiB gzip-bomb protection
_PREFIX_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
UNSUPPORTED_SETTINGS = {"transaction_timeout"}
UNSUPPORTED_META_PREFIXES = ("\\restrict ", "\\unrestrict ")
_SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "USER",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LC_MESSAGES",
    "TZ",
    "TMP",
    "TEMP",
    "TMPDIR",
    "SYSTEMROOT",
    "SystemRoot",
    "COMSPEC",
    "ComSpec",
    "PATHEXT",
    "WINDIR",
    "PYTHONPATH",
)
_COPY_STDIN_RE = re.compile(r"^\s*COPY\b.*\bFROM\s+stdin\s*;", re.IGNORECASE)
_DANGEROUS_PSQL_META_RE = re.compile(
    r"^\s*\\(?:!|i\b|ir\b|include\b|include_relative\b|o\b|out\b|w\b|write\b"
    r"|g\b|gx\b|s\b|e\b|ef\b|ev\b|copy\b|lo_import\b|lo_export\b)",
    re.IGNORECASE,
)


class BackupError(RuntimeError):
    """Raised when backing up or restoring the database fails."""


@dataclass(frozen=True)
class DatabaseConnectionInfo:
    host: str
    port: int
    user: str
    password: str = field(repr=False)
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
        raise BackupError(f"Command {cmd[0]} timed out after {timeout} seconds") from exc
    except BaseException:
        process.kill()
        await process.wait()
        raise

    if process.returncode != 0:
        stderr_text = stderr.decode("utf-8", errors="replace")[:1000]
        stdout_text = stdout.decode("utf-8", errors="replace")[:1000]
        raise BackupError(
            f"Command {cmd[0]} failed with exit code {process.returncode}.\n" f"stdout: {stdout_text}\nstderr: {stderr_text}"
        )


def _prepare_connection_env(info: DatabaseConnectionInfo) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS or k.startswith("PG")}
    env["PGPASSWORD"] = info.password
    env["PGCLIENTENCODING"] = "UTF8"
    return env


def _prepare_migrate_env(db_url: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k in _SAFE_ENV_KEYS}
    env["DATABASE_URL"] = db_url
    return env


def _sanitize_dump_file(sql_path: Path) -> None:
    try:
        original = sql_path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return
    except UnicodeDecodeError as exc:
        raise BackupError("Backup file must be valid UTF-8 encoded SQL.") from exc

    lines = original.splitlines(keepends=True)
    filtered: list[str] = []
    in_copy_block = False
    for line in lines:
        if in_copy_block:
            filtered.append(line)
            if line.strip() == "\\.":
                in_copy_block = False
            continue

        stripped = line.lstrip()
        if _DANGEROUS_PSQL_META_RE.match(stripped):
            raise BackupError("Backup file contains disallowed psql meta-command.")

        if any(stripped.lower().startswith(f"set {setting}") for setting in UNSUPPORTED_SETTINGS) or any(
            stripped.startswith(prefix) for prefix in UNSUPPORTED_META_PREFIXES
        ):
            continue

        filtered.append(line)
        if _COPY_STDIN_RE.match(line):
            in_copy_block = True

    if filtered != lines:
        sql_path.write_text("".join(filtered), encoding="utf-8")


def _needs_schema_reset(sql_path: Path) -> bool:
    """Возвращает True, если дамп создаёт таблицы без предварительного DROP."""
    text = sql_path.read_text(encoding="utf-8-sig", errors="replace").upper()
    return "CREATE TABLE" in text and "DROP TABLE" not in text and "DROP SCHEMA" not in text


async def create_db_backup(
    output_dir: Path | None = None,
    *,
    filename_prefix: str = "db_backup",
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> Path:
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise BackupError("DATABASE_URL environment variable is not set.")

    if not _PREFIX_RE.match(filename_prefix):
        raise BackupError("Invalid backup filename prefix.")

    conn = DatabaseConnectionInfo.from_url(db_url)
    backup_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    backup_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now(MOSCOW_TZ).strftime("%Y%m%d_%H%M%S")
    unique_suffix = uuid.uuid4().hex[:8]
    backup_path = backup_dir / f"{filename_prefix}_{timestamp}_{unique_suffix}.sql"

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

    try:
        await _run_subprocess(*cmd, env=_prepare_connection_env(conn), timeout=timeout)
    except Exception:
        backup_path.unlink(missing_ok=True)
        raise

    if not backup_path.exists():
        raise BackupError("Backup file was not created by pg_dump.")

    os.chmod(backup_path, 0o600)

    _sanitize_dump_file(backup_path)
    return backup_path


async def restore_db_backup(
    sql_file: Path,
    *,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    if not sql_file.exists():
        raise BackupError(f"Backup file {sql_file} does not exist.")

    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise BackupError("DATABASE_URL environment variable is not set.")

    conn = DatabaseConnectionInfo.from_url(db_url)
    env = _prepare_connection_env(conn)

    _sanitize_dump_file(sql_file)

    psql_base = [
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
    ]

    if _needs_schema_reset(sql_file):
        await _run_subprocess(
            *psql_base,
            "-c",
            "DROP SCHEMA public CASCADE; CREATE SCHEMA public;",
            env=env,
            timeout=timeout,
        )

    await _run_subprocess(*psql_base, "-f", str(sql_file), env=env, timeout=timeout)
    await _run_subprocess(sys.executable, "-m", "core.db.migrate", env=_prepare_migrate_env(db_url), timeout=timeout)


def compress_file_gzip(source: Path) -> Path:
    """Сжимает SQL-дамп в .gz и возвращает путь к архиву."""
    import shutil

    target = source.with_suffix(source.suffix + ".gz")
    with open(source, "rb") as src, gzip.open(target, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    os.chmod(target, 0o600)
    return target


def maybe_decompress_gzip(source: Path, *, max_bytes: int = MAX_DECOMPRESSED_BYTES) -> Path:
    if source.suffix.lower() != ".gz":
        return source

    target = source.with_suffix("")
    total = 0
    try:
        with gzip.open(source, "rb") as src, open(target, "wb") as dst:
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_bytes:
                    raise BackupError(f"Decompressed backup exceeds maximum allowed size ({max_bytes} bytes).")
                dst.write(chunk)
    except BackupError:
        target.unlink(missing_ok=True)
        raise
    except (OSError, EOFError) as exc:
        target.unlink(missing_ok=True)
        raise BackupError("Failed to decompress gzip backup archive.") from exc
    return target
