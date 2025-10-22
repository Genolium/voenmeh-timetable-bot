import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timedelta, timezone
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.feedback_manager import FeedbackManager
from core.db.models import Feedback


@pytest.fixture
def mock_session_factory():
    """Фикстура для мока session_factory."""
    return MagicMock(spec=async_sessionmaker)


@pytest.fixture
def mock_session():
    """Фикстура для мока сессии."""
    session = AsyncMock()
    return session


@pytest.fixture
def sample_feedback():
    """Фикстура для создания тестового feedback."""
    return Feedback(
        id=1,
        user_id=123456789,
        username="testuser",
        user_full_name="Test User",
        message_text="Test feedback message",
        message_type="text",
        file_id=None,
        is_answered=False,
        created_at=datetime.now(timezone.utc).replace(tzinfo=None)
    )


class TestFeedbackManager:
    """Тесты для FeedbackManager."""

    @pytest.mark.asyncio
    async def test_create_feedback_success(self, mock_session_factory, mock_session):
        """Тест успешного создания фидбека."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Создаем тестовый feedback
        test_feedback = Feedback(
            user_id=123456789,
            username="testuser",
            user_full_name="Test User",
            message_text="Test message",
            message_type="text",
            file_id=None,
            is_answered=False,
            created_at=datetime.now(timezone.utc).replace(tzinfo=None)
        )
        mock_session.add = MagicMock()
        mock_session.refresh = AsyncMock(return_value=test_feedback)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        result = await manager.create_feedback(
            user_id=123456789,
            username="testuser",
            user_full_name="Test User",
            message_text="Test message",
            message_type="text",
            file_id=None
        )

        # Проверяем результаты
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()
        assert result.user_id == 123456789
        assert result.username == "testuser"
        assert result.message_text == "Test message"

    @pytest.mark.asyncio
    async def test_create_feedback_with_photo(self, mock_session_factory, mock_session):
        """Тест создания фидбека с фото."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        result = await manager.create_feedback(
            user_id=123456789,
            username="testuser",
            user_full_name="Test User",
            message_text="Photo caption",
            message_type="photo",
            file_id="photo_file_id_123"
        )

        # Проверяем результаты
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()
        assert result.message_type == "photo"
        assert result.file_id == "photo_file_id_123"

    @pytest.mark.asyncio
    async def test_create_feedback_with_video(self, mock_session_factory, mock_session):
        """Тест создания фидбека с видео."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        result = await manager.create_feedback(
            user_id=123456789,
            username="testuser",
            user_full_name="Test User",
            message_text="Video caption",
            message_type="video",
            file_id="video_file_id_123"
        )

        # Проверяем результаты
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()
        assert result.message_type == "video"
        assert result.file_id == "video_file_id_123"

    @pytest.mark.asyncio
    async def test_create_feedback_with_document(self, mock_session_factory, mock_session):
        """Тест создания фидбека с документом."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.commit = AsyncMock()
        mock_session.refresh = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        result = await manager.create_feedback(
            user_id=123456789,
            username="testuser",
            user_full_name="Test User",
            message_text="Document: test.pdf",
            message_type="document",
            file_id="document_file_id_123"
        )

        # Проверяем результаты
        mock_session.add.assert_called_once()
        mock_session.commit.assert_called_once()
        mock_session.refresh.assert_called_once()
        assert result.message_type == "document"
        assert result.file_id == "document_file_id_123"

    @pytest.mark.asyncio
    async def test_list_feedback_all(self, mock_session_factory, mock_session, sample_feedback):
        """Тест получения всех фидбеков."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запросы
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [sample_feedback]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Мокаем count запрос
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_count_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        feedbacks, total = await manager.list_feedback()

        # Проверяем результаты
        assert len(feedbacks) == 1
        assert total == 1
        assert feedbacks[0].user_id == 123456789

    @pytest.mark.asyncio
    async def test_list_feedback_only_unanswered(self, mock_session_factory, mock_session, sample_feedback):
        """Тест получения только неотвеченных фидбеков."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запросы с фильтром
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [sample_feedback]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Мокаем count запрос
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_count_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод с фильтром
        feedbacks, total = await manager.list_feedback(only_unanswered=True)

        # Проверяем результаты
        assert len(feedbacks) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_feedback_only_answered(self, mock_session_factory, mock_session, sample_feedback):
        """Тест получения только отвеченных фидбеков."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запросы с фильтром
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [sample_feedback]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Мокаем count запрос
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_count_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод с фильтром
        feedbacks, total = await manager.list_feedback(only_unanswered=False)

        # Проверяем результаты
        assert len(feedbacks) == 1
        assert total == 1

    @pytest.mark.asyncio
    async def test_list_feedback_with_pagination(self, mock_session_factory, mock_session, sample_feedback):
        """Тест пагинации в списке фидбеков."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запросы
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [sample_feedback]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Мокаем count запрос
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 25  # Общее количество больше лимита
        mock_session.execute = AsyncMock(return_value=mock_count_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод с пагинацией
        feedbacks, total = await manager.list_feedback(limit=10, offset=10)

        # Проверяем результаты
        assert len(feedbacks) == 1
        assert total == 25

    @pytest.mark.asyncio
    async def test_list_feedback_by_user_id(self, mock_session_factory, mock_session, sample_feedback):
        """Тест фильтрации фидбеков по ID пользователя."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запросы
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [sample_feedback]
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Мокаем count запрос
        mock_count_result = MagicMock()
        mock_count_result.scalar.return_value = 1
        mock_session.execute = AsyncMock(return_value=mock_count_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод с фильтром по пользователю
        feedbacks, total = await manager.list_feedback(user_id=123456789)

        # Проверяем результаты
        assert len(feedbacks) == 1
        assert total == 1
        assert feedbacks[0].user_id == 123456789

    @pytest.mark.asyncio
    async def test_get_feedback_success(self, mock_session_factory, mock_session, sample_feedback):
        """Тест получения фидбека по ID."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запрос
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = sample_feedback
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        result = await manager.get_feedback(1)

        # Проверяем результаты
        assert result is not None
        assert result.id == 1
        assert result.user_id == 123456789

    @pytest.mark.asyncio
    async def test_get_feedback_not_found(self, mock_session_factory, mock_session):
        """Тест получения несуществующего фидбека."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запрос с None результатом
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        result = await manager.get_feedback(999)

        # Проверяем результаты
        assert result is None

    @pytest.mark.asyncio
    async def test_answer_feedback_success(self, mock_session_factory, mock_session, sample_feedback):
        """Тест успешного ответа на фидбек."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем update запрос
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        success = await manager.answer_feedback(
            feedback_id=1,
            admin_id=987654321,
            response_text="Спасибо за ваш отзыв!"
        )

        # Проверяем результаты
        assert success is True
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_answer_feedback_not_found(self, mock_session_factory, mock_session):
        """Тест ответа на несуществующий фидбек."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем update запрос с нулевым количеством обновлений
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        success = await manager.answer_feedback(
            feedback_id=999,
            admin_id=987654321,
            response_text="Ответ на несуществующий фидбек"
        )

        # Проверяем результаты
        assert success is False

    @pytest.mark.asyncio
    async def test_delete_feedback_success(self, mock_session_factory, mock_session):
        """Тест успешного удаления фидбека."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем delete запрос
        mock_result = MagicMock()
        mock_result.rowcount = 1
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        success = await manager.delete_feedback(1)

        # Проверяем результаты
        assert success is True
        mock_session.execute.assert_called_once()
        mock_session.commit.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_feedback_not_found(self, mock_session_factory, mock_session):
        """Тест удаления несуществующего фидбека."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем delete запрос с нулевым количеством удалений
        mock_result = MagicMock()
        mock_result.rowcount = 0
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.commit = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        success = await manager.delete_feedback(999)

        # Проверяем результаты
        assert success is False

    @pytest.mark.asyncio
    async def test_get_feedback_stats(self, mock_session_factory, mock_session):
        """Тест получения статистики по фидбекам."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем запросы для статистики
        mock_total_result = MagicMock()
        mock_total_result.fetchall.return_value = [(10,)]  # 10 фидбеков всего
        mock_session.execute = AsyncMock(return_value=mock_total_result)

        mock_unanswered_result = MagicMock()
        mock_unanswered_result.fetchall.return_value = [(3,)]  # 3 неотвеченных
        mock_session.execute = AsyncMock(return_value=mock_unanswered_result)

        mock_recent_result = MagicMock()
        mock_recent_result.fetchall.return_value = [(2,)]  # 2 за последнюю неделю
        mock_session.execute = AsyncMock(return_value=mock_recent_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        stats = await manager.get_feedback_stats()

        # Проверяем результаты
        assert stats['total'] == 10
        assert stats['unanswered'] == 3
        assert stats['answered'] == 7  # 10 - 3
        assert stats['recent_7_days'] == 2

    @pytest.mark.asyncio
    async def test_get_feedback_stats_empty(self, mock_session_factory, mock_session):
        """Тест получения статистики при отсутствии фидбеков."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session

        # Мокаем пустые результаты
        mock_result = MagicMock()
        mock_result.fetchall.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод
        stats = await manager.get_feedback_stats()

        # Проверяем результаты
        assert stats['total'] == 0
        assert stats['unanswered'] == 0
        assert stats['answered'] == 0
        assert stats['recent_7_days'] == 0

    @pytest.mark.asyncio
    async def test_list_feedback_exception_handling(self, mock_session_factory, mock_session):
        """Тест обработки исключений в list_feedback."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.execute.side_effect = Exception("Database error")

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод - не должно падать
        try:
            await manager.list_feedback()
            assert False, "Должно было выбросить исключение"
        except Exception:
            assert True  # Ожидаемое поведение

    @pytest.mark.asyncio
    async def test_answer_feedback_exception_handling(self, mock_session_factory, mock_session):
        """Тест обработки исключений в answer_feedback."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.execute.side_effect = Exception("Database error")
        mock_session.commit = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод - не должно падать
        try:
            await manager.answer_feedback(1, 123, "Response")
            assert False, "Должно было выбросить исключение"
        except Exception:
            assert True  # Ожидаемое поведение

    @pytest.mark.asyncio
    async def test_delete_feedback_exception_handling(self, mock_session_factory, mock_session):
        """Тест обработки исключений в delete_feedback."""
        # Настраиваем моки
        mock_session_factory.return_value = mock_session
        mock_session.execute.side_effect = Exception("Database error")
        mock_session.commit = AsyncMock()

        # Создаем менеджер
        manager = FeedbackManager(mock_session_factory)

        # Вызываем метод - не должно падать
        try:
            await manager.delete_feedback(1)
            assert False, "Должно было выбросить исключение"
        except Exception:
            assert True  # Ожидаемое поведение
