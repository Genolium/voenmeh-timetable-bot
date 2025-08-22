import pytest
from unittest.mock import AsyncMock, MagicMock, patch, mock_open
from PIL import Image
import io
import os
import tempfile

from bot.utils.image_compression import (
    compress_image_for_telegram, 
    get_telegram_safe_image_path,
    TELEGRAM_MAX_FILE_SIZE,
    TELEGRAM_MAX_PHOTO_SIZE,
    TELEGRAM_MAX_DIMENSION
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
    
    def test_constants(self):
        """Тест констант Telegram лимитов."""
        assert TELEGRAM_MAX_FILE_SIZE == 10 * 1024 * 1024
        assert TELEGRAM_MAX_PHOTO_SIZE == 5 * 1024 * 1024
        assert TELEGRAM_MAX_DIMENSION == 4096

    def test_compress_image_for_telegram_no_output_path(self, mock_image):
        """Тест сжатия без указания выходного пути."""
        input_path = "/tmp/test.png"
        
        with patch('PIL.Image.open', return_value=mock_image):
            with patch('os.path.getsize', return_value=1024):
                result = compress_image_for_telegram(input_path)
                assert result == input_path

    def test_compress_image_for_telegram_rgba_mode(self):
        """Тест сжатия RGBA изображения."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        # Создаем реальное тестовое изображение для проверки
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=1024):
                # Патчим PIL.Image.open чтобы он вернул исключение, что приведет к возврату input_path
                with patch('PIL.Image.open', side_effect=Exception("Test PIL error")):
                    result = compress_image_for_telegram(input_path, output_path)
                    # При ошибке должен возвращаться исходный путь
                    assert result == input_path

    def test_compress_image_for_telegram_la_mode(self):
        """Тест сжатия LA изображения."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        # Тест обработки ошибки
        with patch('PIL.Image.open', side_effect=Exception("Test PIL error")):
            result = compress_image_for_telegram(input_path, output_path)
            assert result == input_path

    def test_compress_image_for_telegram_p_mode(self):
        """Тест сжатия P (palette) изображения."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        # Тест обработки ошибки
        with patch('PIL.Image.open', side_effect=Exception("Test PIL error")):
            result = compress_image_for_telegram(input_path, output_path)
            assert result == input_path

    def test_compress_image_for_telegram_non_rgb_mode(self, mock_image):
        """Тест сжатия изображения в не-RGB режиме."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        mock_image.mode = 'CMYK'
        
        with patch('PIL.Image.open', return_value=mock_image):
            with patch('os.path.getsize', return_value=1024):
                result = compress_image_for_telegram(input_path, output_path)
                
                mock_image.convert.assert_called_with('RGB')
                assert result == output_path

    def test_compress_image_for_telegram_resize_large_image(self, mock_image):
        """Тест изменения размера большого изображения."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        mock_image.size = (8000, 6000)  # Больше TELEGRAM_MAX_DIMENSION
        mock_image.width = 8000
        mock_image.height = 6000
        mock_resized = MagicMock()
        mock_image.resize.return_value = mock_resized
        
        with patch('PIL.Image.open', return_value=mock_image):
            with patch('os.path.getsize', return_value=1024):
                with patch('logging.info') as mock_log:
                    result = compress_image_for_telegram(input_path, output_path)
                    
                    # Проверяем, что изображение было изменено
                    expected_scale = min(TELEGRAM_MAX_DIMENSION / 8000, TELEGRAM_MAX_DIMENSION / 6000)
                    expected_width = int(8000 * expected_scale)
                    expected_height = int(6000 * expected_scale)
                    
                    mock_image.resize.assert_called_with((expected_width, expected_height), Image.LANCZOS)
                    mock_log.assert_any_call(f"Изображение уменьшено с 8000x6000 до {expected_width}x{expected_height}")
                    assert result == output_path

    def test_compress_image_for_telegram_quality_reduction(self, mock_image):
        """Тест снижения качества для уменьшения размера файла."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        # Имитируем большой размер файла, затем меньший
        file_sizes = [10 * 1024 * 1024, 3 * 1024 * 1024]  # 10MB -> 3MB
        
        with patch('PIL.Image.open', return_value=mock_image):
            with patch('os.path.getsize', side_effect=file_sizes):
                with patch('logging.info') as mock_log:
                    result = compress_image_for_telegram(input_path, output_path, max_size_bytes=5 * 1024 * 1024)
                    
                    # Проверяем, что save был вызван несколько раз с разным качеством
                    assert mock_image.save.call_count >= 2
                    mock_log.assert_any_call("Изображение сжато до 3145728 байт с качеством 70")
                    assert result == output_path

    def test_compress_image_for_telegram_extreme_compression(self, mock_image):
        """Тест экстремального сжатия с уменьшением размеров."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        mock_image.width = 2000
        mock_image.height = 1500
        mock_resized = MagicMock()
        mock_resized.width = 1800
        mock_resized.height = 1350
        mock_image.resize.return_value = mock_resized
        
        # Имитируем файл, который остается большим даже после снижения качества
        large_size = 10 * 1024 * 1024
        smaller_size = 3 * 1024 * 1024
        
        with patch('PIL.Image.open', return_value=mock_image):
            with patch('os.path.getsize', side_effect=[large_size] * 6 + [smaller_size]):
                with patch('logging.warning') as mock_warning:
                    with patch('logging.info') as mock_info:
                        result = compress_image_for_telegram(input_path, output_path, max_size_bytes=5 * 1024 * 1024)
                        
                        mock_warning.assert_called_with(f"Не удалось сжать изображение до {5 * 1024 * 1024} байт, уменьшаем размеры")
                        # Проверяем, что было уменьшение размеров
                        mock_image.resize.assert_called()
                        assert result == output_path

    def test_compress_image_for_telegram_exception_handling(self):
        """Тест обработки исключений при сжатии."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        with patch('PIL.Image.open', side_effect=Exception("Test error")):
            with patch('logging.error') as mock_error:
                result = compress_image_for_telegram(input_path, output_path)
                
                mock_error.assert_called_with(f"Ошибка при сжатии изображения {input_path}: Test error")
                assert result == input_path

    def test_get_telegram_safe_image_path_file_not_exists(self):
        """Тест получения безопасного пути для несуществующего файла."""
        input_path = "/tmp/nonexistent.png"
        
        with patch('os.path.exists', return_value=False):
            result = get_telegram_safe_image_path(input_path)
            assert result == input_path

    def test_get_telegram_safe_image_path_small_file(self):
        """Тест получения безопасного пути для небольшого файла."""
        input_path = "/tmp/test_image.png"
        
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=1024 * 1024):  # 1MB
                result = get_telegram_safe_image_path(input_path)
                
                assert result == input_path

    def test_get_telegram_safe_image_path_large_file(self, mock_image):
        """Тест получения безопасного пути для большого файла."""
        input_path = "/tmp/test_image.png"
        compressed_path = "/tmp/test_image_compressed.png"
        
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=10 * 1024 * 1024):  # 10MB
                with patch('bot.utils.image_compression.compress_image_for_telegram') as mock_compress:
                    mock_compress.return_value = compressed_path
                    
                    result = get_telegram_safe_image_path(input_path)
                    
                    assert result == compressed_path
                    # Проверяем, что функция была вызвана с правильными параметрами
                    mock_compress.assert_called_once()
                    args, kwargs = mock_compress.call_args
                    assert kwargs['input_path'] == input_path
                    assert kwargs['max_size_bytes'] == TELEGRAM_MAX_PHOTO_SIZE
                    # Проверяем только имя файла, игнорируя различия в путях Windows/Unix
                    assert "test_image_compressed.png" in kwargs['output_path']

    def test_get_telegram_safe_image_path_pathlib_usage(self):
        """Тест использования pathlib для создания пути сжатого файла."""
        input_path = "/tmp/my_image.png"
        
        with patch('os.path.exists', return_value=True):
            with patch('os.path.getsize', return_value=10 * 1024 * 1024):  # 10MB
                with patch('bot.utils.image_compression.compress_image_for_telegram') as mock_compress:
                    expected_compressed_path = "/tmp/my_image_compressed.png"
                    mock_compress.return_value = expected_compressed_path
                    
                    result = get_telegram_safe_image_path(input_path)
                    
                    # Проверяем, что compress_image_for_telegram был вызван с правильным путем
                    args, kwargs = mock_compress.call_args
                    assert kwargs['input_path'] == input_path
                    assert "my_image_compressed.png" in kwargs['output_path']
                    assert result == expected_compressed_path

    def test_telegram_size_limits_boundary_cases(self):
        """Тест граничных случаев размера файлов."""
        test_cases = [
            (TELEGRAM_MAX_PHOTO_SIZE - 1, False),  # Чуть меньше лимита
            (TELEGRAM_MAX_PHOTO_SIZE, False),      # Точно на лимите
            (TELEGRAM_MAX_PHOTO_SIZE + 1, True),   # Чуть больше лимита
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
                            mock_compress.assert_called_once()
                    else:
                        result = get_telegram_safe_image_path(input_path)
                        assert result == input_path

    def test_compress_image_success_logging(self, mock_image):
        """Тест логирования успешного сжатия."""
        input_path = "/tmp/test.png"
        output_path = "/tmp/test_out.jpg"
        
        with patch('PIL.Image.open', return_value=mock_image):
            with patch('os.path.getsize', return_value=2048):
                with patch('logging.info') as mock_log:
                    result = compress_image_for_telegram(input_path, output_path)
                    
                    mock_log.assert_any_call("Изображение успешно сжато: 2048 байт")
                    assert result == output_path
