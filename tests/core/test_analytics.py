import io
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from core.analytics import FEATURE_NAMES, generate_dynamics_chart, get_top_features, track_feature_click


class TestAnalytics:
    """Тесты для модуля core.analytics."""

    @pytest.mark.asyncio
    async def test_track_feature_click(self):
        """Тест отслеживания клика по фиче в Redis."""
        from unittest.mock import MagicMock
        mock_redis = AsyncMock()
        mock_pipeline = MagicMock()
        mock_redis.pipeline.return_value = mock_pipeline
        mock_pipeline.execute = AsyncMock()

        await track_feature_click(mock_redis, "inline_teacher")

        mock_pipeline.hincrby.assert_called()
        mock_pipeline.expire.assert_called()
        mock_pipeline.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_top_features_with_data(self):
        """Тест получения топа фич из Redis."""
        mock_redis = AsyncMock()
        mock_redis.hgetall.side_effect = [
            {"inline_teacher": b"10", "day_nav": b"25"},
            {"image_export": b"5"},
        ] + [{}] * 10

        top = await get_top_features(mock_redis, days=3, limit=3)
        assert len(top) == 3
        # day_nav should have 25
        assert top[0][0] == "day_nav"
        assert top[0][2] == 25
        assert "Переход по дням" in top[0][1]

    @pytest.mark.asyncio
    async def test_get_top_features_empty(self):
        """Тест получения топа фич при отсутствии данных."""
        mock_redis = AsyncMock()
        mock_redis.hgetall.return_value = {}

        top = await get_top_features(mock_redis, days=7)
        assert top == []

    def test_generate_dynamics_chart(self):
        """Тест генерации графика динамики в формате PNG."""
        now = datetime.now()
        dates = [now - timedelta(days=i) for i in reversed(range(7))]
        new_users = [10, 15, 8, 20, 25, 12, 18]
        active_users = [100, 120, 110, 130, 145, 125, 150]
        notifications = [80, 85, 82, 90, 95, 88, 92]

        buf = generate_dynamics_chart(
            dates=dates,
            new_users=new_users,
            active_users=active_users,
            notifications_sent=notifications,
            period_title="7 дней",
        )

        assert isinstance(buf, io.BytesIO)
        content = buf.getvalue()
        assert len(content) > 1000  # График не пустой
        assert content.startswith(b"\x89PNG\r\n\x1a\n")  # Валидный PNG header
