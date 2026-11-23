"""Генерирует превью-витрину всех 16 тем расписания (bot/media/themes_showcase.jpg)."""

from __future__ import annotations

import asyncio
import base64
import re
import sys
from pathlib import Path

from PIL import Image
from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.i18n import i18n  # noqa: E402
from core.image_generator import generate_schedule_image  # noqa: E402
from core.themes import THEMES  # noqa: E402

SAMPLE_SCHEDULE = {
    "Понедельник": [
        {
            "subject": "ФИЗИКА (лаб)",
            "time": "9:00 - 10:30",
            "start_time_raw": "09:00",
            "end_time_raw": "10:30",
            "room": "322",
            "teacher": "Иванов И.И.",
        },
        {
            "subject": "СИСТЕМНОЕ ПРОГРАММНОЕ ОБЕСПЕЧЕНИЕ",
            "time": "10:50 - 12:20",
            "start_time_raw": "10:50",
            "end_time_raw": "12:20",
            "room": "325*",
            "teacher": "Сидорова А.В.",
        },
        {
            "subject": "ОСНОВЫ СИСТЕМНОГО АНАЛИЗА (пр)",
            "time": "12:40 - 14:10",
            "start_time_raw": "12:40",
            "end_time_raw": "14:10",
            "room": "259",
            "teacher": "Кузнецов Д.С.",
        },
        {
            "subject": "ТЕОРЕТИЧЕСКАЯ ЭЛЕКТРОТЕХНИКА",
            "time": "14:55 - 16:25",
            "start_time_raw": "14:55",
            "end_time_raw": "16:25",
            "room": "429",
            "teacher": "",
        },
    ],
    "Вторник": [
        {
            "subject": "МАТЕМАТИКА (лек)",
            "time": "9:00 - 10:30",
            "start_time_raw": "09:00",
            "end_time_raw": "10:30",
            "room": "101",
            "teacher": "Смирнов В.Г.",
        },
    ],
    "Среда": [
        {
            "subject": "ХИМИЯ (лаб)",
            "time": "10:50 - 12:20",
            "start_time_raw": "10:50",
            "end_time_raw": "12:20",
            "room": "201",
            "teacher": "Орлова Е.Н.",
        },
    ],
    "Четверг": [],
    "Пятница": [
        {
            "subject": "АЛГОРИТМЫ И СТРУКТУРЫ ДАННЫХ (лаб)",
            "time": "12:40 - 14:10",
            "start_time_raw": "12:40",
            "end_time_raw": "14:10",
            "room": "301",
            "teacher": "Волков А.А.",
        },
    ],
    "Суббота": [],
}


def _strip_leading_emoji(title: str) -> str:
    return re.sub(r"^[^\wА-Яа-яЁё]+", "", title).strip()


async def build_showcase(renders_dir: Path, output_jpg: Path) -> None:
    renders_dir.mkdir(parents=True, exist_ok=True)
    tiles: list[tuple[str, str]] = []

    for meta in THEMES:
        out_png = renders_dir / f"{meta.id}_odd.png"
        ok = await generate_schedule_image(
            schedule_data=SAMPLE_SCHEDULE,
            week_type="Нечётная неделя",
            group="О735Б",
            output_path=str(out_png),
            user_theme=meta.id,
            lang="ru",
            week_key="odd",
        )
        if not ok:
            raise RuntimeError(f"Failed to render theme {meta.id}")
        raw_title = i18n.get(meta.name_key, lang="ru")
        label = _strip_leading_emoji(raw_title)
        b64 = base64.b64encode(out_png.read_bytes()).decode("ascii")
        tiles.append((label, f"data:image/png;base64,{b64}"))

    cards_html = "\n".join(
        f"""
        <div class="tile">
          <div class="thumb"><img src="{src}" alt="{label}"></div>
          <div class="caption"><span class="idx">{idx:02d}</span><span class="name">{label}</span></div>
        </div>
        """
        for idx, (label, src) in enumerate(tiles, start=1)
    )

    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 2048px;
    background: radial-gradient(circle at 18% 0%, #1d2336 0%, #0e1018 55%, #090b10 100%);
    color: #f4f6fb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', sans-serif;
    padding: 48px 52px 52px;
  }}
  header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 34px;
  }}
  h1 {{
    font-size: 46px;
    font-weight: 700;
    letter-spacing: -0.02em;
    color: #ffffff;
  }}
  .sub {{
    font-size: 22px;
    color: #9aa4bc;
    font-weight: 500;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 28px;
  }}
  .tile {{
    background: rgba(255, 255, 255, 0.04);
    border: 1px solid rgba(255, 255, 255, 0.09);
    border-radius: 20px;
    padding: 12px 12px 14px;
    box-shadow: 0 18px 34px rgba(0, 0, 0, 0.42);
  }}
  .thumb {{
    border-radius: 12px;
    overflow: hidden;
    aspect-ratio: 5 / 4;
    background: #141824;
  }}
  .thumb img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }}
  .caption {{
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 12px;
    padding: 0 6px;
  }}
  .idx {{
    font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
    font-size: 16px;
    font-weight: 600;
    color: #7c8aa8;
  }}
  .name {{
    font-size: 21px;
    font-weight: 600;
    color: #f1f5f9;
    letter-spacing: -0.01em;
  }}
</style>
</head>
<body>
  <header>
    <h1>Темы расписания</h1>
    <div class="sub">16 стилей · чётная и нечётная недели</div>
  </header>
  <main class="grid">
    {cards_html}
  </main>
</body>
</html>"""

    tmp_png = renders_dir / "themes_showcase_raw.png"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 2048, "height": 1760})
        await page.set_content(html, wait_until="load")
        await page.screenshot(path=str(tmp_png), full_page=True)
        await browser.close()

    output_jpg.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(tmp_png) as img:
        img.convert("RGB").save(output_jpg, "JPEG", quality=88, optimize=True, progressive=True)
    print(f"Saved showcase to {output_jpg} ({output_jpg.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    renders = Path("/Users/ilyan/.gemini/antigravity/brain/9bac0716-3459-41f4-922a-3583d496f692/scratch/renders_v2")
    asyncio.run(build_showcase(renders, ROOT / "bot/media/themes_showcase.jpg"))
