from datetime import date

import pytest

from core.manager import TimetableManager


def _build_manager() -> TimetableManager:
    # Канонические имена преподавателей в индексе
    teachers_index = {
        "Землянская Е.Р.": [
            {
                "day": "Четверг",
                "week_code": "0",  # каждая неделя
                "time": "18:30-20:00",
                "start_time_raw": "18:30",
                "subject": "ИНФ.ТЕХН. И ПРОГР.",
                "groups": ["О742Б"],
                "room": "218*",
            }
        ],
        "Ялыч Е.С.": [
            {
                "day": "Четверг",
                "week_code": "0",
                "time": "10:50-12:20",
                "start_time_raw": "10:50",
                "subject": "КОНСТРУКЦИИ,ВЗРЫВ",
                "groups": ["Е321С"],
                "room": "СК-14",
            }
        ],
    }

    all_schedules_data = {
        "__teachers_index__": teachers_index,
        "__classrooms_index__": {},
        "__current_xml_hash__": "test",
        # хотя бы одна группа в данных, чтобы структура была реалистичной
        "О742Б": {},
    }

    # Redis клиент менеджеру здесь не нужен — передаём None
    return TimetableManager(all_schedules_data=all_schedules_data, redis_client=None)  # type: ignore[arg-type]


def test_resolve_canonical_teacher_exact():
    manager = _build_manager()
    assert manager.resolve_canonical_teacher("Землянская Е.Р.") == "Землянская Е.Р."


def test_resolve_canonical_teacher_normalization():
    manager = _build_manager()
    # Разные точки/пробелы/регистр
    raw = "ЗЕМЛЯНСКАЯ  Е. Р."
    assert manager.resolve_canonical_teacher(raw) == "Землянская Е.Р."


def test_resolve_canonical_teacher_fuzzy():
    manager = _build_manager()
    # Опечатка и пропуск символов
    raw = "Землянска ЕР"
    assert manager.resolve_canonical_teacher(raw) == "Землянская Е.Р."


@pytest.mark.asyncio
async def test_get_teacher_schedule_normalizes_name():
    manager = _build_manager()
    # Дата четверга, чтобы совпала с зашитым выше днём
    thursday = date(2025, 8, 28)
    # Вводим вариант имени в верхнем регистре и с точками/пробелами
    info = await manager.get_teacher_schedule("ЗЕМЛЯНСКАЯ Е. Р.", thursday)
    assert info.get("teacher") == "Землянская Е.Р."
    assert info.get("day_name") == "Четверг"
    assert info.get("lessons"), "Ожидались занятия в выдаче"


@pytest.mark.asyncio
async def test_get_teacher_schedule_unknown_returns_error():
    manager = _build_manager()
    info = await manager.get_teacher_schedule("Несуществующий Преподаватель", date(2025, 8, 28))
    assert "error" in info
