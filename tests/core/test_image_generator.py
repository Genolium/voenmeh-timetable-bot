import pytest
from unittest.mock import AsyncMock, MagicMock, call
from pathlib import Path
from core.image_generator import generate_schedule_image

# Фикстура, которая создает временную структуру папок и файлов для теста
@pytest.fixture
def mock_template_files(tmp_path):
    # tmp_path - это специальная фикстура pytest для создания временных папок
    project_root = tmp_path
    
    # Создаем структуру, как в реальном проекте
    core_dir = project_root / "core"
    core_dir.mkdir()
    (core_dir / "__init__.py").touch()
    
    templates_dir = project_root / "templates"
    templates_dir.mkdir()
    
    # Создаем фейковый HTML-шаблон
    template_file = templates_dir / "schedule_template.html"
    template_file.write_text("<h1>{{ week_type }}</h1><p>{{ schedule_days[0].name }}</p>")

    # Создаем фейковый файл генератора, чтобы Path(__file__) работал предсказуемо
    generator_file = core_dir / "image_generator.py"
    generator_file.touch()

    return project_root, generator_file

# Мокируем async_playwright, чтобы не вызывать реальный браузер
@pytest.fixture
def mock_playwright(mocker):
    mock_browser = AsyncMock()
    mock_page = AsyncMock()
    mock_browser.new_page.return_value = mock_page
    
    # Мокируем логику измерения высоты контента
    mock_content_element = AsyncMock()
    mock_content_element.bounding_box.return_value = {
        'x': 0, 'y': 0, 'width': 2800, 'height': 1500
    }
    mock_page.query_selector.return_value = mock_content_element
    
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = mock_browser
    
    mock_pw_context = AsyncMock()
    mock_pw_context.__aenter__.return_value.chromium = mock_launcher
    
    mocker.patch('core.image_generator.async_playwright', return_value=mock_pw_context)
    
    return mock_page


@pytest.mark.asyncio
async def test_generate_schedule_image_success(mock_template_files, mock_playwright, tmp_path):
    """
    Тест успешного сценария: шаблон найден, HTML сгенерирован, скриншот сделан.
    """
    project_root, generator_file = mock_template_files
    
    # Подменяем путь к нашему скрипту, чтобы он искал шаблоны в tmp_path
    import core.image_generator
    core.image_generator.__file__ = str(generator_file)
    
    schedule_data = {
        "ПОНЕДЕЛЬНИК": [
            {'start_time_raw': '09:00', 'subject': 'Матан', 'type': 'лек', 'room': '101', 'time': '9-10'}
        ]
    }
    output_path = str(tmp_path / "test.png")

    result = await generate_schedule_image(
        schedule_data=schedule_data,
        week_type="Чётная",
        group="TEST",
        output_path=output_path
    )
    
    assert result is True
    
    # Проверяем что viewport устанавливался хотя бы один раз
    assert mock_playwright.set_viewport_size.call_count >= 1
    # Первый вызов - начальный viewport (новые размеры)
    mock_playwright.set_viewport_size.assert_any_call({"width": 2800, "height": 4000})
    
    html_content_call = mock_playwright.set_content.call_args
    html_content = html_content_call.args[0]
    assert "<h1>Чётная</h1>" in html_content
    assert "<p>Понедельник</p>" in html_content
    
    mock_playwright.screenshot.assert_called_once_with(path=output_path, type="png")

@pytest.mark.asyncio
async def test_generate_schedule_image_template_not_found(monkeypatch, mocker, tmp_path):
    """
    Тест сценария, когда шаблон не найден.
    Имитируем ошибку с помощью мока.
    """
    # Сбрасываем глобальные кэши перед тестом
    import core.image_generator
    core.image_generator._template_cache = None
    core.image_generator._bg_images_cache = {}
    core.image_generator._browser_instance = None
    core.image_generator._browser_lock = None
    
    # Мокируем FileSystemLoader так, чтобы он падал с ошибкой TemplateNotFound
    from jinja2.exceptions import TemplateNotFound
    mock_loader = MagicMock()
    mock_loader.side_effect = TemplateNotFound("schedule_template.html")
    mocker.patch('core.image_generator.Environment', mock_loader)

    mock_print = MagicMock()
    monkeypatch.setattr('builtins.print', mock_print)

    result = await generate_schedule_image({}, "", "", str(tmp_path / "test.png"))
    
    # Теперь при ошибках функция возвращает True (fallback), а не False
    assert result is True
    
    # Проверяем, что в консоль было выведено сообщение об ошибке
    mock_print.assert_called()
    # Ищем вызов с сообщением об ошибке
    error_calls = [call for call in mock_print.call_args_list if "Ошибка при генерации изображения" in str(call)]
    assert len(error_calls) > 0

@pytest.mark.asyncio
async def test_generate_schedule_image_playwright_fails(mock_template_files, mock_playwright, monkeypatch, tmp_path):
    """
    Тест сценария, когда Playwright падает на этапе скриншота.
    """
    project_root, generator_file = mock_template_files
    import core.image_generator
    core.image_generator.__file__ = str(generator_file)
    
    # Сбрасываем глобальные кэши перед тестом
    core.image_generator._template_cache = None
    core.image_generator._bg_images_cache = {}
    core.image_generator._browser_instance = None
    core.image_generator._browser_lock = None
    
    mock_playwright.screenshot.side_effect = Exception("Browser crashed")
    
    mock_print = MagicMock()
    monkeypatch.setattr('builtins.print', mock_print)

    result = await generate_schedule_image({}, "Нечётная", "FAIL", str(tmp_path / "fail.png"))
    
    # Теперь при ошибках функция возвращает True (fallback), а не False
    assert result is True
    
    mock_print.assert_called()
    # Ищем вызов с сообщением об ошибке
    error_calls = [call for call in mock_print.call_args_list if "Ошибка при генерации изображения" in str(call)]
    assert len(error_calls) > 0

@pytest.mark.asyncio
async def test_generate_schedule_image_fallback(monkeypatch, tmp_path):
    # Подменим модуль, чтобы форсировать PIL-фолбэк
    import core.image_generator as ig
    ig.async_playwright = None
    
    # Сбрасываем глобальные кэши перед тестом
    ig._template_cache = None
    ig._bg_images_cache = {}
    ig._browser_instance = None
    ig._browser_lock = None

    schedule_data = {
        "ПОНЕДЕЛЬНИК": [
            {"start_time_raw": "09:00", "subject": "Т", "type": "лек", "room": "101", "time": "09:00-10:30"}
        ]
    }
    out = tmp_path / "out.png"
    ok = await ig.generate_schedule_image(schedule_data, "Чётная", "G1", str(out))
    assert ok is True
    assert out.exists() and out.stat().st_size > 0