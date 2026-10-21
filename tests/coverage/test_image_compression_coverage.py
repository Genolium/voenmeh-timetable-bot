"""Full statement coverage tests for bot/utils/image_compression.py."""

from unittest.mock import MagicMock

import pytest
from PIL import Image

from bot.utils import image_compression as ic


def _make_image(tmp_path, name, mode="RGB", size=(50, 50), color=None):
    path = tmp_path / name
    img = Image.new(mode, size, color) if color is not None else Image.new(mode, size)
    img.save(path)
    return str(path)


class TestConstants:
    def test_limits(self):
        assert ic.TELEGRAM_MAX_FILE_SIZE == 10 * 1024 * 1024
        assert ic.TELEGRAM_MAX_PHOTO_SIZE == 5 * 1024 * 1024
        assert ic.TELEGRAM_MAX_DIMENSION == 4096


class TestModeConversion:
    def test_rgba_with_mask(self, tmp_path):
        src = _make_image(tmp_path, "rgba.png", mode="RGBA", color=(255, 0, 0, 128))
        out = str(tmp_path / "rgba_out.jpg")
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=10 * 1024 * 1024)
        assert result == out

    def test_la_mode(self, tmp_path):
        src = _make_image(tmp_path, "la.png", mode="LA", color=(120, 255))
        out = str(tmp_path / "la_out.jpg")
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=10 * 1024 * 1024)
        assert result == out

    def test_palette_mode(self, tmp_path):
        src = _make_image(tmp_path, "palette.png", mode="P", color=3)
        out = str(tmp_path / "palette_out.jpg")
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=10 * 1024 * 1024)
        assert result == out

    def test_grayscale_converted_to_rgb(self, tmp_path):
        src = _make_image(tmp_path, "gray.png", mode="L", color=128)
        out = str(tmp_path / "gray_out.jpg")
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=10 * 1024 * 1024)
        assert result == out


class TestResizing:
    def test_large_dimension_resized(self, tmp_path, monkeypatch):
        src = _make_image(tmp_path, "wide.png", mode="RGB", size=(5000, 100), color=(10, 20, 30))
        out = str(tmp_path / "wide_out.jpg")
        monkeypatch.setattr(ic.os.path, "getsize", lambda p: 1024)
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=10 * 1024 * 1024)
        assert result == out
        with Image.open(out) as res:
            assert res.width <= ic.TELEGRAM_MAX_DIMENSION

    def test_quality_reduction_breaks_when_small_enough(self, tmp_path, monkeypatch):
        src = _make_image(tmp_path, "q.png", mode="RGB", size=(100, 100), color=(1, 2, 3))
        out = str(tmp_path / "q_out.jpg")
        sizes = iter([10 * 1024 * 1024, 1 * 1024 * 1024])
        monkeypatch.setattr(ic.os.path, "getsize", lambda p: next(sizes))
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=5 * 1024 * 1024)
        assert result == out

    def test_extreme_compression_resizes_until_small(self, tmp_path, monkeypatch):
        src = _make_image(tmp_path, "big.png", mode="RGB", size=(1000, 1000), color=(9, 9, 9))
        out = str(tmp_path / "big_out.jpg")
        monkeypatch.setattr(ic.os.path, "getsize", lambda p: 10 * 1024 * 1024)
        result = ic.compress_image_for_telegram(src, out, max_size_bytes=5 * 1024 * 1024)
        assert result == out
        with Image.open(out) as res:
            assert res.width <= 800
            assert res.height <= 800


class TestFallbacks:
    def test_default_output_path(self, tmp_path):
        src = _make_image(tmp_path, "default.png", mode="RGB", color=(5, 5, 5))
        result = ic.compress_image_for_telegram(src)
        assert result == src

    def test_exception_returns_input_path(self, tmp_path):
        missing = str(tmp_path / "does_not_exist.png")
        out = str(tmp_path / "out.jpg")
        assert ic.compress_image_for_telegram(missing, out) == missing


class TestGetTelegramSafeImagePath:
    def test_missing_file(self, tmp_path):
        missing = str(tmp_path / "nope.png")
        assert ic.get_telegram_safe_image_path(missing) == missing

    def test_small_file_returned_as_is(self, tmp_path):
        src = _make_image(tmp_path, "small.png", mode="RGB", size=(10, 10), color=(0, 0, 0))
        assert ic.get_telegram_safe_image_path(src) == src

    def test_large_file_compressed(self, tmp_path, monkeypatch):
        src = _make_image(tmp_path, "large.png", mode="RGB", size=(10, 10), color=(0, 0, 0))
        monkeypatch.setattr(ic.os.path, "getsize", lambda p: 10 * 1024 * 1024)
        fake_compress = MagicMock(return_value="compressed.jpg")
        monkeypatch.setattr(ic, "compress_image_for_telegram", fake_compress)
        result = ic.get_telegram_safe_image_path(src)
        assert result == "compressed.jpg"
        kwargs = fake_compress.call_args.kwargs
        assert kwargs["input_path"] == src
        assert kwargs["max_size_bytes"] == ic.TELEGRAM_MAX_PHOTO_SIZE
        assert kwargs["output_path"].endswith("large_compressed.png")
