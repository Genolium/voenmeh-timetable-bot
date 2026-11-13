#!/usr/bin/env python3
"""Оптимизация фонов расписания.

Для каждого `assets/*_background.png` выбирается формат, который даёт меньший файл
при ширине не больше 2869 px (родное разрешение большинства фонов):

* фотографии и градиенты лучше сжимаются в JPEG (q88);
* line-art (blueprint, matrix, paper) — в PNG.

Результат детерминирован: повторный запуск на уже оптимизированных файлах ничего
не меняет. Исходники не сохраняются — они остаются в истории git.

Запуск: python3 scripts/optimize_backgrounds.py [--dry-run]
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

from PIL import Image

MAX_WIDTH = 2869
JPEG_QUALITY = 88


def best_encoding(path: Path) -> tuple[str, bytes]:
    image = Image.open(path).convert("RGB")
    if image.width > MAX_WIDTH:
        height = int(image.height * MAX_WIDTH / image.width)
        image = image.resize((MAX_WIDTH, height), Image.Resampling.LANCZOS)

    png = io.BytesIO()
    image.save(png, "PNG", optimize=True)
    jpeg = io.BytesIO()
    image.save(jpeg, "JPEG", quality=JPEG_QUALITY, optimize=True)

    if len(png.getvalue()) <= len(jpeg.getvalue()):
        return ".png", png.getvalue()
    return ".jpg", jpeg.getvalue()


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    assets = Path(__file__).resolve().parents[1] / "assets"
    before = after = 0
    changed = 0

    for source in sorted(assets.glob("*_background.png")):
        suffix, data = best_encoding(source)
        target = source.with_suffix(suffix)
        before += source.stat().st_size
        after += len(data)

        if suffix == ".png" and len(data) >= source.stat().st_size:
            print(f"  skip {source.name}: пережатие не уменьшает файл")
            continue

        changed += 1
        print(f"  {source.name} -> {target.name} ({source.stat().st_size // 1024}K -> {len(data) // 1024}K)")
        if dry_run:
            continue
        target.write_bytes(data)
        if target != source:
            source.unlink()

    print(f"\nИтого: {before // 1024 // 1024} MB -> {after // 1024} KB, изменено файлов: {changed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
