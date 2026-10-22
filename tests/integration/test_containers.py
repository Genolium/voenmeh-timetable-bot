"""Real infrastructure smoke tests (PostgreSQL, Redis, RabbitMQ).

They are opt-in locally: RUN_INTEGRATION=1 pytest -m integration.
CI enables them because Docker is available on ubuntu-latest.
"""

import os
import socket
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import redis
from testcontainers.community.postgres import PostgresContainer
from testcontainers.community.rabbitmq import RabbitMqContainer
from testcontainers.community.redis import RedisContainer

from core.manager import TimetableManager


def _ensure_docker_host() -> None:
    """На Docker Desktop сокет лежит в ~/.docker/run, а не в /var/run."""
    if os.getenv("DOCKER_HOST"):
        return
    desktop_socket = Path.home() / ".docker" / "run" / "docker.sock"
    if desktop_socket.exists():
        os.environ["DOCKER_HOST"] = f"unix://{desktop_socket}"


_ensure_docker_host()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="set RUN_INTEGRATION=1"),
]


def test_postgres_migrations_apply_to_head():
    with PostgresContainer("postgres:15-alpine") as postgres:
        database_url = postgres.get_connection_url()
        env = {**os.environ, "DATABASE_URL": database_url}
        result = subprocess.run(
            [".venv/bin/alembic", "upgrade", "head"],
            cwd=os.getcwd(),
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr

        import psycopg2

        with psycopg2.connect(database_url.replace("postgresql+psycopg2://", "postgresql://")) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT version_num FROM alembic_version")
                assert cur.fetchone()[0] == "20261021_reminder_20"


@pytest.mark.asyncio
async def test_redis_schedule_cache_roundtrip_json():
    with RedisContainer("redis:7-alpine") as redis_container:
        client = redis.asyncio.Redis(
            host=redis_container.get_container_host_ip(),
            port=int(redis_container.get_exposed_port(6379)),
            decode_responses=False,
        )
        data = {
            "__metadata__": {},
            "__teachers_index__": {},
            "__classrooms_index__": {},
            "G1": {"odd": {}, "even": {}},
        }
        manager = TimetableManager(data, client)
        await manager.save_to_cache()
        raw = await client.get("timetable:schedule_cache")
        assert manager._decompress_data(raw)["G1"] == {"odd": {}, "even": {}}
        await client.aclose()


def test_rabbitmq_is_reachable():
    with RabbitMqContainer("rabbitmq:3-management-alpine") as rabbit:
        with socket.create_connection((rabbit.get_container_host_ip(), int(rabbit.get_exposed_port(5672))), timeout=10):
            pass
