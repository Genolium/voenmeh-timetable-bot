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
    template_file.write_text("<h1>{{ week_type }}</h1><p>{{ schedule_days[0].title }}</p>")

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
    
    mock_launcher = AsyncMock()
    mock_launcher.launch.return_value = mock_browser
    
    mock_pw_context = AsyncMock()
    mock_pw_context.__aenter__.return_value.chromium = mock_launcher
    
    mocker.patch('core.image_generator.async_playwright', return_value=mock_pw_context)
    
    return mock_page


@pytest.mark.asyncio
async def test_generate_schedule_image_success(mock_template_files, mock_playwright):
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
    output_path = str(project_root / "test.png")

    result = await generate_schedule_image(
        schedule_data=schedule_data,
        week_type="Чётная",
        group="TEST",
        output_path=output_path
    )
    
    assert result is True
    
    mock_playwright.set_viewport_size.assert_called_once_with({"width": 1280, "height": 830})
    
    html_content_call = mock_playwright.set_content.call_args
    html_content = html_content_call.args[0]
    assert "<h1>Чётная</h1>" in html_content
    assert "<p>Понедельник</p>" in html_content
    
    mock_playwright.screenshot.assert_called_once_with(path=output_path, type="png")

@pytest.mark.asyncio
async def test_generate_schedule_image_template_not_found(monkeypatch, mocker):
    """
    Тест сценария, когда шаблон не найден.
    Имитируем ошибку с помощью мока.
    """
    # Мокируем FileSystemLoader так, чтобы он падал с ошибкой TemplateNotFound
    from jinja2.exceptions import TemplateNotFound
    mock_loader = MagicMock()
    mock_loader.side_effect = TemplateNotFound("schedule_template.html")
    mocker.patch('core.image_generator.Environment', mock_loader)

    mock_print = MagicMock()
    monkeypatch.setattr('builtins.print', mock_print)

    result = await generate_schedule_image({}, "", "", "test.png")
    
    # 1. Проверяем, что функция вернула False (неудача)
    assert result is False
    
    # 2. Проверяем, что в консоль было выведено сообщение об ошибке
    mock_print.assert_called_once()
    error_message = mock_print.call_args.args[0]
    assert "Ошибка при генерации изображения" in error_message
    assert "schedule_template.html" in str(mock_print.call_args) # Проверяем, что имя файла есть в ошибке

@pytest.mark.asyncio
async def test_generate_schedule_image_playwright_fails(mock_template_files, mock_playwright, monkeypatch):
    """
    Тест сценария, когда Playwright падает на этапе скриншота.
    """
    project_root, generator_file = mock_template_files
    import core.image_generator
    core.image_generator.__file__ = str(generator_file)
    
    mock_playwright.screenshot.side_effect = Exception("Browser crashed")
    
    mock_print = MagicMock()
    monkeypatch.setattr('builtins.print', mock_print)

    result = await generate_schedule_image({}, "Нечётная", "FAIL", "fail.png")
    
    assert result is False
    
    mock_print.assert_called_once()
    error_message = mock_print.call_args.args[0]
    assert "Ошибка при генерации изображения: Browser crashed" in error_message