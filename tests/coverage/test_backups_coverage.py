"""Coverage tests for ``core.db.backups``.

No real ``pg_dump``/``psql`` process is spawned.
"""

import asyncio
import gzip
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.db import backups
from core.db.backups import (
    BackupError,
    DatabaseConnectionInfo,
    _run_subprocess,
    _sanitize_dump_file,
    create_db_backup,
    maybe_decompress_gzip,
    restore_db_backup,
)

DB_URL = "postgresql://user:pass@localhost:5432/test_db"


class FakeProcess:
    def __init__(self, returncode=0, stdout=b"", stderr=b"", communicate_side_effect=None):
        self.returncode = returncode
        self.kill = MagicMock()
        if communicate_side_effect is not None:
            self.communicate = AsyncMock(side_effect=communicate_side_effect)
        else:
            self.communicate = AsyncMock(return_value=(stdout, stderr))


# ---------------------------------------------------------------------------
# DatabaseConnectionInfo
# ---------------------------------------------------------------------------


def test_from_url_valid():
    info = DatabaseConnectionInfo.from_url(DB_URL)
    assert info.host == "localhost"
    assert info.port == 5432
    assert info.user == "user"
    assert info.password == "pass"
    assert info.database == "test_db"


def test_from_url_defaults_port():
    info = DatabaseConnectionInfo.from_url("postgresql://user:pass@localhost/test_db")
    assert info.port == 5432


def test_from_url_invalid_raises():
    with pytest.raises(BackupError):
        DatabaseConnectionInfo.from_url("sqlite:///tmp/test.db")


# ---------------------------------------------------------------------------
# _run_subprocess
# ---------------------------------------------------------------------------


async def test_run_subprocess_success():
    process = FakeProcess(returncode=0, stdout=b"ok", stderr=b"")
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        await _run_subprocess("echo", "hi")


async def test_run_subprocess_failure():
    process = FakeProcess(returncode=1, stdout=b"out", stderr=b"err")
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(BackupError):
            await _run_subprocess("false")


async def test_run_subprocess_timeout():
    process = FakeProcess(communicate_side_effect=[asyncio.TimeoutError(), (b"", b"")])
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        with pytest.raises(BackupError):
            await _run_subprocess("sleep", timeout=1)
    process.kill.assert_called_once()


# ---------------------------------------------------------------------------
# _sanitize_dump_file
# ---------------------------------------------------------------------------


def test_sanitize_dump_file_missing(tmp_path):
    _sanitize_dump_file(tmp_path / "missing.sql")  # returns silently


def test_sanitize_dump_file_filters_unsupported(tmp_path):
    sql = tmp_path / "dump.sql"
    sql.write_text("SET transaction_timeout = 0;\nSELECT 1;\n", encoding="utf-8")
    _sanitize_dump_file(sql)
    content = sql.read_text(encoding="utf-8")
    assert "transaction_timeout" not in content
    assert "SELECT 1;" in content


# ---------------------------------------------------------------------------
# create_db_backup
# ---------------------------------------------------------------------------


async def test_create_db_backup_no_url(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(BackupError):
        await create_db_backup(output_dir=tmp_path)


async def test_create_db_backup_success(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", DB_URL)

    async def fake_run(*cmd, **kwargs):
        args = list(cmd)
        Path(args[args.index("-f") + 1]).write_text("SET transaction_timeout = 0;\nSELECT 1;\n", encoding="utf-8")

    monkeypatch.setattr(backups, "_run_subprocess", fake_run)
    path = await create_db_backup(output_dir=tmp_path)
    assert path.exists()
    assert "transaction_timeout" not in path.read_text(encoding="utf-8")


async def test_create_db_backup_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    monkeypatch.setattr(backups, "_run_subprocess", AsyncMock())
    with pytest.raises(BackupError):
        await create_db_backup(output_dir=tmp_path)


# ---------------------------------------------------------------------------
# restore_db_backup
# ---------------------------------------------------------------------------


async def test_restore_db_backup_missing_file(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    with pytest.raises(BackupError):
        await restore_db_backup(tmp_path / "missing.sql")


async def test_restore_db_backup_no_url(monkeypatch, tmp_path):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    sql = tmp_path / "restore.sql"
    sql.write_text("SELECT 1;", encoding="utf-8")
    with pytest.raises(BackupError):
        await restore_db_backup(sql)


async def test_restore_db_backup_success(monkeypatch, tmp_path):
    monkeypatch.setenv("DATABASE_URL", DB_URL)
    sql = tmp_path / "restore.sql"
    sql.write_text("SET transaction_timeout = 0;\nSELECT 1;\n", encoding="utf-8")
    captured = {}

    async def fake_run(*cmd, **kwargs):
        captured["cmd"] = cmd

    monkeypatch.setattr(backups, "_run_subprocess", fake_run)
    await restore_db_backup(sql)
    assert str(sql) in captured["cmd"]
    assert "transaction_timeout" not in sql.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# maybe_decompress_gzip
# ---------------------------------------------------------------------------


def test_maybe_decompress_gzip_plain_file(tmp_path):
    plain = tmp_path / "backup.sql"
    plain.write_text("SELECT 1;", encoding="utf-8")
    assert maybe_decompress_gzip(plain) == plain


def test_maybe_decompress_gzip_compressed(tmp_path):
    gz_path = tmp_path / "backup.sql.gz"
    with gzip.open(gz_path, "wb") as handle:
        handle.write(b"SELECT 1;")
    result = maybe_decompress_gzip(gz_path)
    assert result == tmp_path / "backup.sql"
    assert result.read_bytes() == b"SELECT 1;"
