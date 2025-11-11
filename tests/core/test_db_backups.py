from pathlib import Path

import pytest

from core.db import backups
from core.db.backups import BackupError, create_db_backup, maybe_decompress_gzip, restore_db_backup


@pytest.mark.asyncio
async def test_create_db_backup_success(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")

    async def fake_run(*cmd, **kwargs):
        args = list(cmd)
        target_path = Path(args[args.index("-f") + 1])
        target_path.write_text("SET transaction_timeout = 0;\nSELECT 1;\n", encoding="utf-8")

    monkeypatch.setattr(backups, "_run_subprocess", fake_run)

    backup_path = await create_db_backup(output_dir=tmp_path, filename_prefix="test_backup")

    assert backup_path.exists()
    assert backup_path.parent == tmp_path
    assert backup_path.suffix == ".sql"
    assert "transaction_timeout" not in backup_path.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_create_db_backup_failure(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")

    async def fake_run(*cmd, **kwargs):
        raise BackupError("command failed")

    monkeypatch.setattr(backups, "_run_subprocess", fake_run)

    with pytest.raises(BackupError):
        await create_db_backup(output_dir=tmp_path, filename_prefix="test_backup")


@pytest.mark.asyncio
async def test_restore_db_backup_success(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")
    sql_file = tmp_path / "restore.sql"
    sql_file.write_text("SET transaction_timeout = 0;\nSELECT 1;\n", encoding="utf-8")

    captured = {}

    async def fake_run(*cmd, **kwargs):
        captured["cmd"] = cmd

    monkeypatch.setattr(backups, "_run_subprocess", fake_run)

    await restore_db_backup(sql_file)

    assert str(sql_file) in captured["cmd"]
    assert "transaction_timeout" not in sql_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_restore_db_backup_missing_file(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost:5432/test_db")
    missing = tmp_path / "missing.sql"

    with pytest.raises(BackupError):
        await restore_db_backup(missing)


def test_maybe_decompress_gzip(tmp_path: Path):
    original = tmp_path / "backup.sql"
    original.write_text("SELECT 1;", encoding="utf-8")
    gz_path = tmp_path / "backup.sql.gz"
    import gzip

    with gzip.open(gz_path, "wb") as f:
        f.write(original.read_bytes())

    result = maybe_decompress_gzip(gz_path)
    assert result.exists()
    assert result.suffix == ".sql"

