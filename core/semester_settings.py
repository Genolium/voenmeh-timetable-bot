"""
Менеджер для работы с настройками семестров.
"""

from datetime import date, datetime
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy import select, update

from core.db.models import SemesterSettings


class SemesterSettingsManager:
    """Менеджер для работы с настройками семестров."""
    
    def __init__(self, session_factory: async_sessionmaker):
        self.session_factory = session_factory
    
    async def get_semester_settings(self) -> Optional[Tuple[date, date]]:
        """
        Получает текущие настройки семестров.
        
        Returns:
            Tuple[date, date] | None: (fall_semester_start, spring_semester_start) или None
        """
        async with self.session_factory() as session:
            result = await session.execute(
                select(SemesterSettings).order_by(SemesterSettings.id.desc()).limit(1)
            )
            settings = result.scalar_one_or_none()
            
            if settings:
                return settings.fall_semester_start, settings.spring_semester_start
            return None
    
    async def update_semester_settings(
        self, 
        fall_semester_start: date, 
        spring_semester_start: date, 
        updated_by: int
    ) -> bool:
        """
        Обновляет настройки семестров.
        
        Args:
            fall_semester_start: Дата начала осеннего семестра
            spring_semester_start: Дата начала весеннего семестра
            updated_by: Telegram ID администратора
            
        Returns:
            bool: True если успешно обновлено
        """
        try:
            async with self.session_factory() as session:
                # Проверяем, есть ли уже настройки
                result = await session.execute(
                    select(SemesterSettings).order_by(SemesterSettings.id.desc()).limit(1)
                )
                existing_settings = result.scalar_one_or_none()
                
                if existing_settings:
                    # Обновляем существующие настройки
                    await session.execute(
                        update(SemesterSettings)
                        .where(SemesterSettings.id == existing_settings.id)
                        .values(
                            fall_semester_start=fall_semester_start,
                            spring_semester_start=spring_semester_start,
                            updated_at=datetime.now(),
                            updated_by=updated_by
                        )
                    )
                else:
                    # Создаем новые настройки
                    new_settings = SemesterSettings(
                        fall_semester_start=fall_semester_start,
                        spring_semester_start=spring_semester_start,
                        updated_by=updated_by
                    )
                    session.add(new_settings)
                
                await session.commit()
                return True
                
        except Exception as e:
            print(f"Ошибка обновления настроек семестров: {e}")
            return False
    
    async def get_formatted_settings(self) -> str:
        """
        Получает отформатированную строку с текущими настройками.
        
        Returns:
            str: Отформатированная строка с настройками
        """
        settings = await self.get_semester_settings()
        
        if settings:
            fall_start, spring_start = settings
            return (
                f"📅 <b>Текущие настройки семестров:</b>\n\n"
                f"🍂 <b>Осенний семестр:</b> {fall_start.strftime('%d.%m.%Y')}\n"
                f"🌸 <b>Весенний семестр:</b> {spring_start.strftime('%d.%m.%Y')}\n\n"
                f"<i>Используйте кнопки ниже для изменения дат.</i>"
            )
        else:
            return (
                "⚠️ <b>Настройки семестров не установлены!</b>\n\n"
                "Используются значения по умолчанию:\n"
                "🍂 <b>Осенний семестр:</b> 1 сентября\n"
                "🌸 <b>Весенний семестр:</b> 9 февраля\n\n"
                "Нажмите кнопки ниже для настройки дат."
            )
