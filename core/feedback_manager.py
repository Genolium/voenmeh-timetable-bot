import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import selectinload

from core.db.models import Feedback
from core.metrics import FEATURE_POPULARITY, USER_ACTIONS


class FeedbackManager:
    """
    Менеджер для управления фидбеком от пользователей.
    Работает через переданный async_sessionmaker.
    """

    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory

    # --- Фидбек ---
    async def create_feedback(
        self,
        user_id: int,
        username: str | None,
        user_full_name: str | None,
        message_text: str | None = None,
        message_type: str = "text",
        file_id: str | None = None,
    ) -> Feedback:
        """
        Создает новый фидбек от пользователя.
        """
        logger = logging.getLogger(__name__)

        async with self.session_factory() as session:
            feedback = Feedback(
                user_id=user_id,
                username=username,
                user_full_name=user_full_name,
                message_text=message_text,
                message_type=message_type,
                file_id=file_id,
                is_answered=False,
                created_at=datetime.utcnow(),
            )
            session.add(feedback)
            await session.commit()
            await session.refresh(feedback)

            # Логируем создание фидбека
            logger.info(
                "Feedback created",
                extra={
                    "user_id": str(user_id),
                    "username": username,
                    "message_type": message_type,
                    "feedback_id": feedback.id,
                    "action": "create_feedback",
                    "feature": "feedback",
                },
            )

            # Обновляем метрики (тип пользователя будет определен в middleware)
            FEATURE_POPULARITY.labels(
                feature_name="feedback",
                user_type="unknown",
                day_of_week=datetime.now().strftime("%A").lower(),
            ).inc()
            USER_ACTIONS.labels(action="feedback_sent", user_type="unknown", source="user_interface").inc()

            return feedback

    async def list_feedback(
        self, *, only_unanswered: Optional[bool] = None, limit: int = 20, offset: int = 0, user_id: Optional[int] = None
    ) -> Tuple[List[Feedback], int]:
        """
        Получает список фидбеков с пагинацией и фильтрами.
        """
        async with self.session_factory() as session:
            base = select(Feedback).order_by(Feedback.created_at.desc())

            # Фильтр по ответу
            if only_unanswered is True:
                base = base.where(Feedback.is_answered == False)
            elif only_unanswered is False:
                base = base.where(Feedback.is_answered == True)

            # Фильтр по пользователю
            if user_id:
                base = base.where(Feedback.user_id == user_id)

            # Подсчет общего количества
            count_query = select(Feedback.id)
            if only_unanswered is True:
                count_query = count_query.where(Feedback.is_answered == False)
            elif only_unanswered is False:
                count_query = count_query.where(Feedback.is_answered == True)
            if user_id:
                count_query = count_query.where(Feedback.user_id == user_id)

            total_result = await session.execute(count_query)
            total = total_result.scalar() or 0

            # Пагинация
            base = base.limit(limit).offset(offset)

            result = await session.execute(base)
            feedbacks = list(result.scalars().all())

            return feedbacks, total

    async def get_feedback(self, feedback_id: int) -> Optional[Feedback]:
        """
        Получает фидбек по ID.
        """
        async with self.session_factory() as session:
            result = await session.execute(select(Feedback).where(Feedback.id == feedback_id))
            return result.scalar_one_or_none()

    async def answer_feedback(self, feedback_id: int, admin_id: int, response_text: str) -> bool:
        """
        Отвечает на фидбек от имени администратора.
        """
        logger = logging.getLogger(__name__)

        async with self.session_factory() as session:
            result = await session.execute(
                update(Feedback)
                .where(Feedback.id == feedback_id)
                .values(
                    is_answered=True,
                    admin_response=response_text,
                    admin_id=admin_id,
                    answered_at=datetime.utcnow(),
                )
            )
            await session.commit()

            success = result.rowcount > 0

            if success:
                # Логируем ответ на фидбек
                logger.info(
                    "Feedback answered",
                    extra={
                        "feedback_id": feedback_id,
                        "admin_id": str(admin_id),
                        "response_length": len(response_text),
                        "action": "answer_feedback",
                        "feature": "admin_feedback",
                    },
                )

                # Обновляем метрики
                FEATURE_POPULARITY.labels(
                    feature_name="admin_feedback",
                    user_type="admin",
                    day_of_week=datetime.now().strftime("%A").lower(),
                ).inc()
                USER_ACTIONS.labels(action="feedback_answered", user_type="admin", source="admin_panel").inc()

            return success

    async def delete_feedback(self, feedback_id: int) -> bool:
        """
        Удаляет фидбек.
        """
        async with self.session_factory() as session:
            result = await session.execute(delete(Feedback).where(Feedback.id == feedback_id))
            await session.commit()
            return result.rowcount > 0

    async def get_feedback_stats(self) -> dict:
        """
        Получает статистику по фидбекам.
        """
        async with self.session_factory() as session:
            # Общее количество фидбеков
            total_result = await session.execute(select(Feedback))
            total = len(total_result.fetchall())

            # Неотвеченные фидбеки
            unanswered_result = await session.execute(select(Feedback).where(Feedback.is_answered == False))
            unanswered = len(unanswered_result.fetchall())

            # Фидбеки за последние 7 дней
            week_ago = datetime.utcnow() - timedelta(days=7)
            recent_result = await session.execute(select(Feedback).where(Feedback.created_at >= week_ago))
            recent = len(recent_result.fetchall())

            return {
                "total": total,
                "unanswered": unanswered,
                "answered": total - unanswered,
                "recent_7_days": recent,
            }
