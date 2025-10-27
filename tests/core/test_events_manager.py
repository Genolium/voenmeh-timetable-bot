from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from core.db.models import Base, Event
from core.events_manager import EventsManager


@pytest.fixture
async def async_session():
    """Создание тестовой базы данных в памяти"""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        echo=False,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    yield session_factory

    await engine.dispose()


@pytest.fixture
async def events_manager(async_session):
    """EventsManager с тестовой БД"""
    return EventsManager(async_session)


@pytest.fixture
async def sample_event(async_session):
    """Создание тестового события"""
    async with async_session() as session:
        event = Event(
            title="Тестовое событие",
            description="Описание тестового события",
            start_at=datetime(2025, 12, 31, 23, 59, 0),
            location="Тестовая локация",
            link="https://example.com",
            is_published=True,
        )
        session.add(event)
        await session.commit()
        await session.refresh(event)
        return event


class TestEventsManager:
    """Тесты для EventsManager"""

    # --- Тесты категорий ---

    async def test_init(self, async_session):
        """Тест инициализации EventsManager"""
        manager = EventsManager(async_session)
        assert manager.session_factory == async_session

    # --- Тесты событий ---

    async def test_list_events_empty(self, events_manager):
        """Тест получения пустого списка событий"""
        events, count = await events_manager.list_events()
        assert events == []
        assert count == 0

    async def test_create_event_basic(self, events_manager):
        """Тест создания простого события"""
        event_id = await events_manager.create_event(title="Тестовое событие")
        assert isinstance(event_id, int)
        assert event_id > 0

        events, count = await events_manager.list_events()
        assert count == 1
        assert events[0].title == "Тестовое событие"

    async def test_create_event_with_all_params(self, events_manager):
        """Тест создания события со всеми параметрами"""
        start_time = datetime(2025, 12, 31, 23, 59, 0)
        end_time = datetime(2026, 1, 1, 0, 30, 0)

        event_id = await events_manager.create_event(
            title="Полное событие",
            description="Полное описание",
            start_at=start_time,
            end_at=end_time,
            location="Тестовая локация",
            link="https://example.com",
            image_file_id="test_file_id",
            is_published=False,
        )

        event = await events_manager.get_event(event_id)
        assert event.title == "Полное событие"
        assert event.description == "Полное описание"
        assert event.start_at == start_time
        assert event.end_at == end_time
        assert event.location == "Тестовая локация"
        assert event.link == "https://example.com"
        assert event.image_file_id == "test_file_id"
        assert event.is_published is False

    async def test_create_event_strips_fields(self, events_manager):
        """Тест обрезки пробелов в полях события"""
        event_id = await events_manager.create_event(
            title="  Событие с пробелами  ",
            location="  Локация с пробелами  ",
            link="  https://example.com  ",
        )

        event = await events_manager.get_event(event_id)
        assert event.title == "Событие с пробелами"
        assert event.location == "Локация с пробелами"
        assert event.link == "https://example.com"

    async def test_create_event_validation_empty_title(self, events_manager):
        """Тест валидации пустого заголовка"""
        with pytest.raises(ValueError, match="Заголовок не может быть пустым"):
            await events_manager.create_event(title="")

        with pytest.raises(ValueError, match="Заголовок не может быть пустым"):
            await events_manager.create_event(title="   ")

    async def test_create_event_validation_long_title(self, events_manager):
        """Тест валидации длинного заголовка"""
        long_title = "a" * 256
        with pytest.raises(ValueError, match="Заголовок слишком длинный"):
            await events_manager.create_event(title=long_title)

    async def test_create_event_validation_long_location(self, events_manager):
        """Тест валидации длинной локации"""
        long_location = "a" * 256
        with pytest.raises(ValueError, match="Локация слишком длинная"):
            await events_manager.create_event(title="Событие", location=long_location)

    async def test_create_event_validation_long_link(self, events_manager):
        """Тест валидации длинной ссылки"""
        long_link = "https://example.com/" + "a" * 500
        with pytest.raises(ValueError, match="Ссылка слишком длинная"):
            await events_manager.create_event(title="Событие", link=long_link)

    async def test_create_event_validation_long_file_id(self, events_manager):
        """Тест валидации длинного file_id"""
        long_file_id = "a" * 513
        with pytest.raises(ValueError, match="File ID изображения слишком длинный"):
            await events_manager.create_event(title="Событие", image_file_id=long_file_id)

    async def test_get_event_existing(self, events_manager, sample_event):
        """Тест получения существующего события"""
        event = await events_manager.get_event(sample_event.id)
        assert event is not None
        assert event.id == sample_event.id
        assert event.title == sample_event.title

    async def test_get_event_nonexistent(self, events_manager):
        """Тест получения несуществующего события"""
        event = await events_manager.get_event(99999)
        assert event is None

    async def test_list_events_published_filter(self, events_manager):
        """Тест фильтрации опубликованных событий"""
        await events_manager.create_event(title="Опубликованное", is_published=True)
        await events_manager.create_event(title="Неопубликованное", is_published=False)

        published_events, published_count = await events_manager.list_events(only_published=True)
        unpublished_events, unpublished_count = await events_manager.list_events(only_published=False)
        all_events, all_count = await events_manager.list_events(only_published=None)

        assert published_count == 1
        assert unpublished_count == 1
        assert all_count == 2
        assert published_events[0].title == "Опубликованное"
        assert unpublished_events[0].title == "Неопубликованное"

    async def test_list_events_pagination(self, events_manager):
        """Тест пагинации событий"""
        # Создаем несколько событий
        for i in range(5):
            await events_manager.create_event(title=f"Событие {i}")

        # Тестируем пагинацию
        page1_events, total_count = await events_manager.list_events(limit=2, offset=0)
        page2_events, _ = await events_manager.list_events(limit=2, offset=2)

        assert total_count == 5
        assert len(page1_events) == 2
        assert len(page2_events) == 2
        assert page1_events[0].id != page2_events[0].id

    async def test_list_events_time_filter_today(self, events_manager):
        """Тест фильтрации событий по сегодняшнему дню"""
        now = datetime(2025, 6, 15, 12, 0, 0)
        today_morning = datetime(2025, 6, 15, 9, 0, 0)
        yesterday = datetime(2025, 6, 14, 12, 0, 0)
        tomorrow = datetime(2025, 6, 16, 12, 0, 0)

        await events_manager.create_event(title="Сегодня утром", start_at=today_morning)
        await events_manager.create_event(title="Вчера", start_at=yesterday)
        await events_manager.create_event(title="Завтра", start_at=tomorrow)

        today_events, count = await events_manager.list_events(time_filter="today", now=now)

        assert count == 1
        assert today_events[0].title == "Сегодня утром"

    async def test_list_events_time_filter_this_week(self, events_manager):
        """Тест фильтрации событий по текущей неделе"""
        # Среда, 11 июня 2025
        now = datetime(2025, 6, 11, 12, 0, 0)  # Wednesday
        monday_this_week = datetime(2025, 6, 9, 12, 0, 0)  # Monday
        friday_this_week = datetime(2025, 6, 13, 12, 0, 0)  # Friday
        last_week = datetime(2025, 6, 1, 12, 0, 0)  # Previous Sunday
        next_week = datetime(2025, 6, 16, 12, 0, 0)  # Next Monday

        await events_manager.create_event(title="Понедельник этой недели", start_at=monday_this_week)
        await events_manager.create_event(title="Пятница этой недели", start_at=friday_this_week)
        await events_manager.create_event(title="Прошлая неделя", start_at=last_week)
        await events_manager.create_event(title="Следующая неделя", start_at=next_week)

        this_week_events, count = await events_manager.list_events(time_filter="this_week", now=now)

        assert count == 2
        titles = [event.title for event in this_week_events]
        assert "Понедельник этой недели" in titles
        assert "Пятница этой недели" in titles

    async def test_list_events_from_now_only(self, events_manager):
        """Тест фильтрации событий от текущего момента"""
        now = datetime(2025, 6, 15, 12, 0, 0)
        past_event = datetime(2025, 6, 14, 12, 0, 0)
        future_event = datetime(2025, 6, 16, 12, 0, 0)

        await events_manager.create_event(title="Прошедшее событие", start_at=past_event)
        await events_manager.create_event(title="Будущее событие", start_at=future_event)
        await events_manager.create_event(title="Событие без даты")  # start_at=None

        future_events, count = await events_manager.list_events(from_now_only=True, now=now)

        assert count == 2  # Будущее событие + событие без даты
        titles = [event.title for event in future_events]
        assert "Будущее событие" in titles
        assert "Событие без даты" in titles

    async def test_update_event_basic(self, events_manager, sample_event):
        """Тест обновления события"""
        result = await events_manager.update_event(sample_event.id, title="Новый заголовок")
        assert result is True

        updated_event = await events_manager.get_event(sample_event.id)
        assert updated_event.title == "Новый заголовок"

    async def test_update_event_no_values(self, events_manager, sample_event):
        """Тест обновления события без параметров"""
        result = await events_manager.update_event(sample_event.id)
        assert result is True

    async def test_delete_event(self, events_manager, sample_event):
        """Тест удаления события"""
        result = await events_manager.delete_event(sample_event.id)
        assert result is True

        deleted_event = await events_manager.get_event(sample_event.id)
        assert deleted_event is None
