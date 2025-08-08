import pytest
from core.user_data import UserDataManager
from core.db import Base, User
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy import text

TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

@pytest.fixture
async def user_data_manager():
    engine = create_async_engine(TEST_DB_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    manager = UserDataManager(TEST_DB_URL)
    # Привязываем менеджер к уже созданному in-memory движку,
    # чтобы использовать одну и ту же БД в рамках теста
    manager.engine = engine
    manager.async_session_maker.configure(bind=engine)
    try:
        yield manager
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()

@pytest.mark.asyncio
async def test_register_user_and_get_group(user_data_manager):
    await user_data_manager.register_user(123, "testuser")
    group = await user_data_manager.get_user_group(123)
    assert group is None

    await user_data_manager.set_user_group(123, "О735Б")
    group = await user_data_manager.get_user_group(123)
    assert group == "О735Б"