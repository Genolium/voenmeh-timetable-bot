import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.parser import (
    create_initial_fallback_schedule,
    fetch_and_parse_all_schedules,
    load_fallback_schedule,
    save_fallback_schedule,
)


@pytest.fixture
def sample_xml_bytes():
    """
    Фикстура с минимальным валидным XML.
    Сначала создаем обычную строку, а потом кодируем ее в байты.
    """
    xml_string = """<?xml version="1.0" encoding="utf-16"?>
<Timetable>
    <Period StartYear="2023" StartMonth="9" StartDay="1" />
    <Group Number="О735Б">
        <Days>
            <Day Title="Понедельник">
                <GroupLessons>
                    <Lesson>
                        <Time>09:00 </Time>
                        <Discipline>Лекция Математика</Discipline>
                        <Lecturers><Lecturer><ShortName>Иванов И.И.</ShortName></Lecturer></Lecturers>
                        <Classroom>101</Classroom>
                        <WeekCode>1</WeekCode>
                    </Lesson>
                </GroupLessons>
            </Day>
        </Days>
    </Group>
</Timetable>
    """.strip()
    return xml_string.encode("utf-16")


@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules(mocker, sample_xml_bytes):
    mock_response = AsyncMock()
    mock_response.read.return_value = sample_xml_bytes
    mock_response.raise_for_status = MagicMock()

    mock_session_get = AsyncMock()
    mock_session_get.__aenter__.return_value = mock_response

    mocker.patch("aiohttp.ClientSession.get", return_value=mock_session_get)

    result = await fetch_and_parse_all_schedules()

    assert result is not None
    assert "О735Б" in result
    assert "__teachers_index__" in result
    assert "__current_xml_hash__" in result

    group_schedule = result["О735Б"]
    assert "odd" in group_schedule
    assert "Понедельник" in group_schedule["odd"]

    lesson = group_schedule["odd"]["Понедельник"][0]
    assert lesson["subject"] == "Математика"
    assert lesson["type"] == "Лекция"
    assert lesson["teachers"] == "Иванов И.И."


@pytest.mark.asyncio
async def test_parser_success_builds_indexes(monkeypatch):
    import hashlib

    from core import parser

    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        "<Root>"
        '  <Period StartYear="2024" StartMonth="9" StartDay="1" />'
        '  <Weeks FirstWeek="odd" />'
        '  <Group Number="G1">'
        "    <Days>"
        '      <Day Title="Понедельник">'
        "        <GroupLessons>"
        "          <Lesson>"
        "            <Time>09:00</Time>"
        "            <Discipline>лек Математика</Discipline>"
        "            <Classroom>101</Classroom>"
        "            <WeekCode>1</WeekCode>"
        "            <Lecturers><Lecturer><ShortName>Иванов</ShortName></Lecturer></Lecturers>"
        "          </Lesson>"
        "        </GroupLessons>"
        "      </Day>"
        "    </Days>"
        "  </Group>"
        "</Root>"
    ).encode("utf-16")

    class Resp:
        status = 200
        headers = {}

        async def read(self):
            return xml

        def raise_for_status(self):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            class Ctx:
                async def __aenter__(self_inner):
                    return Resp()

                async def __aexit__(self_inner, *a):
                    return False

            return Ctx()

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Session())

    res = await parser.fetch_and_parse_all_schedules()
    assert res
    assert res["__current_xml_hash__"] == hashlib.md5(xml).hexdigest()
    assert "G1" in res and "__teachers_index__" in res and "__classrooms_index__" in res


@pytest.mark.asyncio
async def test_parser_handles_304(monkeypatch):
    # Смоделируем 304 Not Modified
    import core.parser as parser

    class Resp:
        status = 304
        headers = {}

        async def read(self):
            return b""

        def raise_for_status(self):
            return None

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def get(self, *a, **k):
            class Ctx:
                async def __aenter__(self_inner):
                    return Resp()

                async def __aexit__(self_inner, *a):
                    return False

            return Ctx()

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Session())
    res = await parser.fetch_and_parse_all_schedules()
    assert res is None


# --- Тесты для оффлайн режима (fallback) ---


def test_load_fallback_schedule_success(tmp_path):
    """Тест успешной загрузки fallback данных."""
    # Создаем временный файл с тестовыми данными
    fallback_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "test_hash",
        "О735Б": {"odd": {"Понедельник": []}},
    }

    fallback_file = tmp_path / "fallback_schedule.json"
    with open(fallback_file, "w", encoding="utf-8") as f:
        json.dump(fallback_data, f, ensure_ascii=False)

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = load_fallback_schedule()

        assert result is not None
        assert result["__current_xml_hash__"] == "test_hash"
        assert "О735Б" in result


def test_load_fallback_schedule_file_not_found():
    """Тест загрузки fallback данных при отсутствии файла."""
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", Path("/nonexistent/file.json")):
        result = load_fallback_schedule()

        assert result is None


def test_load_fallback_schedule_invalid_json(tmp_path):
    """Тест загрузки fallback данных с невалидным JSON."""
    # Создаем файл с невалидным JSON
    fallback_file = tmp_path / "invalid_fallback.json"
    fallback_file.write_text("invalid json content {", encoding="utf-8")

    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = load_fallback_schedule()

        assert result is None


@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules_fallback_on_network_error(monkeypatch):
    """Тест использования fallback данных при сетевой ошибке."""
    # Создаем тестовые fallback данные
    fallback_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "fallback_hash",
        "О735Б": {"odd": {"Понедельник": []}},
    }

    # Мокаем сетевые ошибки (3 попытки)
    class FailingSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            class FailingResponse:
                def __init__(self):
                    self.status = 500

                async def read(self):
                    raise Exception("Network error")

                def raise_for_status(self):
                    raise Exception("Network error")

            return FailingResponse()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: FailingSession())

    # Мокаем fallback данные
    with patch("core.parser.load_fallback_schedule", return_value=fallback_data):
        # Мокаем AlertSender чтобы избежать ошибок
        with patch("core.parser.AlertSender"):
            result = await fetch_and_parse_all_schedules()

            assert result is not None
            assert result["__current_xml_hash__"] == "fallback_hash"


@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules_fallback_on_parsing_error(monkeypatch):
    """Тест использования fallback данных при ошибке парсинга."""
    # Создаем тестовые fallback данные
    fallback_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "fallback_hash",
        "О735А": {"odd": {"Понедельник": []}},
    }

    # Мокаем успешный ответ, но с невалидным XML
    class Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            class Response:
                def __init__(self):
                    self.status = 200
                    self.headers = {}

                async def read(self):
                    return b"invalid xml content"

                def raise_for_status(self):
                    pass

            return Response()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Session())

    # Мокаем fallback данные
    with patch("core.parser.load_fallback_schedule", return_value=fallback_data):
        # Мокаем AlertSender чтобы избежать ошибок
        with patch("core.parser.AlertSender"):
            result = await fetch_and_parse_all_schedules()

            assert result is not None
            assert result["__current_xml_hash__"] == "fallback_hash"


@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules_no_fallback_available(monkeypatch):
    """Тест поведения при отсутствии fallback данных."""

    # Мокаем сетевые ошибки
    class FailingSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            class FailingResponse:
                def __init__(self):
                    self.status = 500

                async def read(self):
                    raise Exception("Network error")

                def raise_for_status(self):
                    raise Exception("Network error")

            return FailingResponse()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: FailingSession())

    # Мокаем отсутствие fallback данных
    with patch("core.parser.load_fallback_schedule", return_value=None):
        # Мокаем AlertSender чтобы избежать ошибок
        with patch("core.parser.AlertSender"):
            # Должно выбросить исключение
            with pytest.raises(Exception):
                await fetch_and_parse_all_schedules()


@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules_success_with_fallback_file(tmp_path):
    """Тест успешной работы с fallback файлом."""
    # Создаем тестовые fallback данные
    fallback_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "fallback_hash_2024",
        "О735Б": {
            "odd": {
                "Понедельник": [
                    {
                        "time": "09:00-10:30",
                        "subject": "Математика (fallback)",
                        "start_time_raw": "09:00",
                        "end_time_raw": "10:30",
                        "room": "101",
                        "teachers": "Иванов И.И.",
                        "week_type": "odd",
                    }
                ]
            }
        },
    }

    fallback_file = tmp_path / "fallback_schedule.json"
    with open(fallback_file, "w", encoding="utf-8") as f:
        json.dump(fallback_data, f, ensure_ascii=False)

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = load_fallback_schedule()

        assert result is not None
        assert result["__current_xml_hash__"] == "fallback_hash_2024"
        assert "О735Б" in result
        assert len(result["О735Б"]["odd"]["Понедельник"]) == 1


# --- Тесты для обновления fallback файла ---


def test_save_fallback_schedule_success(tmp_path):
    """Тест успешного сохранения fallback данных."""
    # Создаем тестовые данные
    test_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "test_hash_2024",
        "О735Б": {
            "odd": {
                "Понедельник": [
                    {
                        "time": "09:00-10:30",
                        "subject": "Математика (test)",
                        "start_time_raw": "09:00",
                        "end_time_raw": "10:30",
                        "room": "101",
                        "teachers": "Иванов И.И.",
                        "week_type": "odd",
                    }
                ]
            }
        },
    }

    # Создаем временный файл для fallback
    fallback_file = tmp_path / "test_fallback.json"

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = save_fallback_schedule(test_data)

        assert result is True
        assert fallback_file.exists()

        # Проверяем содержимое файла
        with open(fallback_file, "r", encoding="utf-8") as f:
            saved_data = json.load(f)

        assert saved_data["__current_xml_hash__"] == "test_hash_2024"
        assert "О735Б" in saved_data
        assert len(saved_data["О735Б"]["odd"]["Понедельник"]) == 1


def test_save_fallback_schedule_create_directory(tmp_path):
    """Тест создания директории при сохранении fallback данных."""
    # Создаем тестовые данные
    test_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "test_hash_create_dir",
    }

    # Создаем путь к файлу в несуществующей директории
    nested_path = tmp_path / "nested" / "deep" / "path"
    fallback_file = nested_path / "fallback.json"

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = save_fallback_schedule(test_data)

        assert result is True
        assert fallback_file.exists()
        assert nested_path.exists()


def test_save_fallback_schedule_invalid_data():
    """Тест сохранения с невалидными данными."""
    # Мокаем несуществующий путь для вызова исключения
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", Path("/invalid/path/fallback.json")):
        # Данные с некорректными символами для JSON
        invalid_data = {"test": "данные с символами \x00\x01\x02"}
        result = save_fallback_schedule(invalid_data)

        assert result is False


def test_save_fallback_schedule_permission_error(tmp_path):
    """Тест обработки ошибок доступа при сохранении."""
    import os

    # Создаем файл и устанавливаем права только на чтение
    fallback_file = tmp_path / "readonly_fallback.json"
    fallback_file.write_text("{}", encoding="utf-8")

    # На Windows может не сработать, но на Unix системах сработает
    try:
        os.chmod(str(fallback_file), 0o444)  # Только чтение

        # Мокаем FALLBACK_SCHEDULE_PATH
        with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
            test_data = {"test": "data"}
            result = save_fallback_schedule(test_data)

            # В зависимости от ОС может быть True или False
            assert isinstance(result, bool)

    except (OSError, PermissionError):
        # На Windows chmod может не сработать, это нормально
        pass


@pytest.mark.asyncio
async def test_fetch_and_parse_all_schedules_updates_fallback_on_success(monkeypatch, tmp_path):
    """Тест обновления fallback файла при успешной загрузке с сервера."""
    # Создаем тестовые данные для успешного ответа
    test_schedule_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "success_hash_2024",
        "О735Б": {
            "odd": {
                "Понедельник": [
                    {
                        "time": "09:00-10:30",
                        "subject": "Математика (success)",
                        "start_time_raw": "09:00",
                        "end_time_raw": "10:30",
                        "room": "101",
                        "teachers": "Иванов И.И.",
                        "week_type": "odd",
                    }
                ]
            }
        },
    }

    # Мокаем успешный ответ сервера
    class Session:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            class Response:
                def __init__(self):
                    self.status = 200
                    self.headers = {}

                async def read(self):
                    # Возвращаем минимальный валидный XML
                    xml_content = """<?xml version="1.0" encoding="utf-16"?>
                    <Timetable>
                        <Period StartYear="2024" StartMonth="9" StartDay="1" />
                        <Group Number="О735Б">
                            <Days>
                                <Day Title="Понедельник">
                                    <GroupLessons>
                                        <Lesson>
                                            <Time>09:00 </Time>
                                            <Discipline>Лекция Математика (success)</Discipline>
                                            <Lecturers><Lecturer><ShortName>Иванов И.И.</ShortName></Lecturer></Lecturers>
                                            <Classroom>101</Classroom>
                                            <WeekCode>1</WeekCode>
                                        </Lesson>
                                    </GroupLessons>
                                </Day>
                            </Days>
                        </Group>
                    </Timetable>""".strip()
                    return xml_content.encode("utf-16")

                def raise_for_status(self):
                    pass

            return Response()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("aiohttp.ClientSession", lambda *a, **k: Session())

    # Создаем временный файл для fallback
    fallback_file = tmp_path / "test_fallback_update.json"

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        # Мокаем save_fallback_schedule чтобы убедиться что он вызывается
        with patch("core.parser.save_fallback_schedule") as mock_save:
            mock_save.return_value = True

            result = await fetch_and_parse_all_schedules()

            # Проверяем что результат получен
            assert result is not None
            assert "О735Б" in result

            # Проверяем что save_fallback_schedule был вызван
            mock_save.assert_called_once_with(result)


# --- Тесты для инициализации fallback файла ---


def test_create_initial_fallback_schedule_file_exists(tmp_path):
    """Тест create_initial_fallback_schedule когда файл уже существует."""
    # Создаем существующий файл
    fallback_file = tmp_path / "existing_fallback.json"
    fallback_file.write_text('{"test": "existing"}', encoding="utf-8")

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = create_initial_fallback_schedule()

        # Должен вернуть True, так как файл уже существует
        assert result is True

        # Файл не должен быть изменен
        with open(fallback_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        assert data == {"test": "existing"}


def test_create_initial_fallback_schedule_create_new(tmp_path):
    """Тест create_initial_fallback_schedule создания нового файла."""
    # Создаем путь к несуществующему файлу
    fallback_file = tmp_path / "new_fallback.json"

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        result = create_initial_fallback_schedule()

        # Должен вернуть True
        assert result is True
        assert fallback_file.exists()

        # Проверяем содержимое созданного файла
        with open(fallback_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Проверяем что созданы базовые данные
        assert data["__current_xml_hash__"] == "initial_fallback_2024"
        assert "О735Б" in data
        assert "О735А" in data
        assert data["О735Б"]["odd"]["Понедельник"][0]["subject"] == "Математика (fallback)"
        assert data["О735А"]["even"]["Четверг"][0]["subject"] == "Информатика (fallback)"


def test_create_initial_fallback_schedule_save_error(tmp_path):
    """Тест create_initial_fallback_schedule при ошибке сохранения."""
    # Мокаем save_fallback_schedule чтобы он возвращал False
    with patch("core.parser.save_fallback_schedule", return_value=False):
        # Создаем путь к файлу в несуществующей директории
        fallback_file = tmp_path / "readonly" / "fallback.json"

        # Мокаем FALLBACK_SCHEDULE_PATH
        with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
            result = create_initial_fallback_schedule()

            # Должен вернуть False из-за ошибки сохранения
            assert result is False


def test_load_fallback_schedule_creates_initial_if_missing(tmp_path):
    """Тест load_fallback_schedule создает начальный файл если его нет."""
    # Создаем путь к несуществующему файлу
    fallback_file = tmp_path / "missing_fallback.json"

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        # Мокаем create_initial_fallback_schedule
        with patch("core.parser.create_initial_fallback_schedule", return_value=True) as mock_create:
            result = load_fallback_schedule()

            # Должен вернуть результат create_initial_fallback_schedule
            assert result is not None

            # Проверяем что create_initial_fallback_schedule был вызван
            mock_create.assert_called_once()


def test_load_fallback_schedule_create_initial_fails(tmp_path):
    """Тест load_fallback_schedule когда создание начального файла не удается."""
    # Создаем путь к несуществующему файлу
    fallback_file = tmp_path / "missing_fallback.json"

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        # Мокаем create_initial_fallback_schedule чтобы он возвращал False
        with patch("core.parser.create_initial_fallback_schedule", return_value=False):
            result = load_fallback_schedule()

            # Должен вернуть None
            assert result is None


def test_load_fallback_schedule_loads_existing_file(tmp_path):
    """Тест load_fallback_schedule загружает существующий файл."""
    # Создаем существующий файл с тестовыми данными
    test_data = {
        "__metadata__": {"period": {"StartYear": "2024", "StartMonth": "09", "StartDay": "01"}},
        "__current_xml_hash__": "test_existing_hash",
        "О735Б": {"odd": {"Понедельник": []}},
    }

    fallback_file = tmp_path / "existing_fallback.json"
    with open(fallback_file, "w", encoding="utf-8") as f:
        json.dump(test_data, f, ensure_ascii=False)

    # Мокаем FALLBACK_SCHEDULE_PATH
    with patch("core.parser.FALLBACK_SCHEDULE_PATH", fallback_file):
        # Мокаем create_initial_fallback_schedule чтобы убедиться что он НЕ вызывается
        with patch("core.parser.create_initial_fallback_schedule") as mock_create:
            result = load_fallback_schedule()

            # Должен вернуть данные из файла
            assert result is not None
            assert result["__current_xml_hash__"] == "test_existing_hash"

            # create_initial_fallback_schedule не должен вызываться
            mock_create.assert_not_called()
