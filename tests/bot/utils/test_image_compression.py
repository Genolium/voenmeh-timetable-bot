import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from PIL import Image
import io
import os

from bot.utils.image_compression import (
    compress_image_for_telegram, get_telegram_safe_image_path
)

@pytest.fixture
def mock_image():
    """Создает мок изображения для тестирования."""
    image = MagicMock(spec=Image.Image)
    # Устанавливаем размер как кортеж
    image.size = (2048, 1536)
    image.width = 2048
    image.height = 1536
    image.format = 'PNG'
    image.mode = 'RGB'
    image.save = MagicMock()
    image.resize = MagicMock(return_value=image)
    image.convert = MagicMock(return_value=image)
    image.split = MagicMock(return_value=[MagicMock(), MagicMock(), MagicMock(), MagicMock()])
    # Делаем изображение контекстным менеджером
    image.__enter__ = MagicMock(return_value=image)
    image.__exit__ = MagicMock(return_value=None)
    return image

@pytest.fixture
def sample_image_data():
    """Создает тестовые данные изображения."""
    # Создаем простое тестовое изображение
    img = Image.new('RGB', (100, 100), color='red')
    img_bytes = io.BytesIO()
    img.save(img_bytes, format='PNG')
    return img_bytes.getvalue()

class TestImageCompression:
    
    def test_compress_image_for_telegram_file_not_exists(self):
        """Тест сжатия несуществующего файла."""
        input_path = "/tmp/nonexistent.png"
        output_path = "/tmp/test_output.png"
        
        with patch('os.path.exists', return_value=False):
            result = compress_image_for_telegram(input_path, output_path)
            
            assert result == input_path  # Возвращает исходный путь при ошибке

    def test_get_telegram_safe_image_path_success(self):
        """Тест получения безопасного пути для Telegram."""
        input_path = "/tmp/test_image.png"
        
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=1024 * 1024):  # 1MB
                result = get_telegram_safe_image_path(input_path)
                
                assert result == input_path

    def test_get_telegram_safe_image_path_large_file(self, mock_image):
        """Тест получения безопасного пути для большого файла."""
        input_path = "/tmp/test_image.png"
        compressed_path = "/tmp/test_image_compressed.png"
        
        with patch('os.path.exists', side_effect=[True, True]):
            with patch('os.path.getsize', return_value=10 * 1024 * 1024):  # 10MB
                with patch('bot.utils.image_compression.compress_image_for_telegram') as mock_compress:
                    mock_compress.return_value = compressed_path
                    
                    result = get_telegram_safe_image_path(input_path)
                    
                    assert result == compressed_path
                    mock_compress.assert_called_once()

    def test_get_telegram_safe_image_path_compression_failed(self, mock_image):
        """Тест получения безопасного пути при неудачном сжатии."""
        input_path = "/tmp/test_image.png"
        
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=10 * 1024 * 1024):  # 10MB
                with patch('bot.utils.image_compression.compress_image_for_telegram') as mock_compress:
                    mock_compress.return_value = input_path  # Возвращает исходный путь при ошибке
                    
                    result = get_telegram_safe_image_path(input_path)
                    
                    assert result == input_path

    def test_telegram_size_limits(self):
        """Тест лимитов размера для Telegram."""
        # Тестируем разные размеры файлов
        test_cases = [
            (1024 * 1024, False),      # 1MB - не требует сжатия
            (5 * 1024 * 1024, False),  # 5MB - не требует сжатия
            (10 * 1024 * 1024, True),  # 10MB - требует сжатия
            (20 * 1024 * 1024, True),  # 20MB - требует сжатия
        ]
        
        for file_size, should_compress in test_cases:
            with patch('os.path.exists', return_value=True):
                with patch('os.path.getsize', return_value=file_size):
                    input_path = "/tmp/test.png"
                    
                    if should_compress:
                        with patch('bot.utils.image_compression.compress_image_for_telegram') as mock_compress:
                            mock_compress.return_value = "/tmp/test_compressed.png"
                            result = get_telegram_safe_image_path(input_path)
                            assert result != input_path
                    else:
                        result = get_telegram_safe_image_path(input_path)
                        assert result == input_path

    def test_error_handling_integration(self):
        """Тест интеграционной обработки ошибок."""
        input_path = "/tmp/test_input.png"
        output_path = "/tmp/test_output.png"
        
        # Тестируем различные сценарии ошибок
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=10 * 1024 * 1024):
                with patch('PIL.Image.open', side_effect=Exception("Open error")):
                    result = compress_image_for_telegram(input_path, output_path)
                    assert result == input_path
                
                with patch('PIL.Image.open', return_value=MagicMock()):
                    with patch('PIL.Image.Image.save', side_effect=Exception("Save error")):
                        result = compress_image_for_telegram(input_path, output_path)
                        assert result == input_path
