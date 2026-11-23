"""Генерирует превью-витрину всех тем расписания (bot/media/themes_showcase.jpg)."""

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

FEATURED_THEME_ID = "vice"

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
    featured_srcs: dict[str, str] = {}
    featured_label = "Vice VI"

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
        data_uri = f"data:image/png;base64,{b64}"

        if meta.id == FEATURED_THEME_ID:
            featured_label = label
            featured_srcs["odd"] = data_uri
            even_png = renders_dir / f"{meta.id}_even.png"
            ok_even = await generate_schedule_image(
                schedule_data=SAMPLE_SCHEDULE,
                week_type="Чётная неделя",
                group="О735Б",
                output_path=str(even_png),
                user_theme=meta.id,
                lang="ru",
                week_key="even",
            )
            if not ok_even:
                raise RuntimeError(f"Failed to render even theme {meta.id}")
            b64_even = base64.b64encode(even_png.read_bytes()).decode("ascii")
            featured_srcs["even"] = f"data:image/png;base64,{b64_even}"
        else:
            tiles.append((label, data_uri))

    cards_html = "\n".join(
        f"""
        <div class="tile">
          <div class="thumb"><img src="{src}" alt="{label}"></div>
          <div class="caption"><span class="idx">{idx:02d}</span><span class="name">{label}</span></div>
        </div>
        """
        for idx, (label, src) in enumerate(tiles, start=1)
    )

    featured_html = ""
    if "odd" in featured_srcs and "even" in featured_srcs:
        featured_html = f"""
  <section class="featured">
    <div class="featured-head">
      <div class="featured-title-wrap">
        <span class="badge-new">NEW</span>
        <span class="featured-title">{featured_label}</span>
        <span class="featured-desc">закат Майами и неоновые сумерки в стиле GTA VI</span>
      </div>
      <span class="featured-idx">17</span>
    </div>
    <div class="featured-grid">
      <div class="featured-card">
        <div class="featured-thumb"><img src="{featured_srcs['odd']}" alt="{featured_label} Sunset"></div>
        <div class="featured-sub">Нечётная неделя · Leonida Sunset</div>
      </div>
      <div class="featured-card">
        <div class="featured-thumb"><img src="{featured_srcs['even']}" alt="{featured_label} Twilight"></div>
        <div class="featured-sub">Чётная неделя · Vice Twilight</div>
      </div>
    </div>
  </section>"""

    total_count = len(THEMES)
    html = f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    width: 2048px;
    background: radial-gradient(circle at 18% 0%, #24193d 0%, #10121e 48%, #090b10 100%);
    color: #f4f6fb;
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Inter', sans-serif;
    padding: 46px 52px 52px;
  }}
  header {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 30px;
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
  .featured {{
    background: linear-gradient(135deg, rgba(114, 92, 206, 0.20) 0%, rgba(218, 70, 144, 0.16) 52%, rgba(255, 148, 88, 0.16) 100%);
    border: 1.5px solid rgba(255, 142, 196, 0.36);
    border-radius: 26px;
    padding: 20px 22px 22px;
    margin-bottom: 30px;
    box-shadow: 0 24px 48px rgba(0, 0, 0, 0.45);
  }}
  .featured-head {{
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 16px;
    padding: 0 4px;
  }}
  .featured-title-wrap {{
    display: flex;
    align-items: center;
    gap: 14px;
  }}
  .badge-new {{
    background: linear-gradient(135deg, #6552c9 0%, #d9458e 54%, #ff8e54 100%);
    color: #ffffff;
    font-size: 15px;
    font-weight: 800;
    letter-spacing: 0.08em;
    padding: 5px 12px;
    border-radius: 999px;
  }}
  .featured-title {{
    font-size: 28px;
    font-weight: 700;
    color: #ffffff;
    letter-spacing: -0.01em;
  }}
  .featured-desc {{
    font-size: 20px;
    color: #d6c5e8;
    font-weight: 400;
  }}
  .featured-idx {{
    font-family: ui-monospace, 'SFMono-Regular', Menlo, monospace;
    font-size: 18px;
    font-weight: 700;
    color: #ff9ec9;
  }}
  .featured-grid {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: 22px;
  }}
  .featured-card {{
    background: rgba(14, 10, 26, 0.48);
    border: 1px solid rgba(255, 255, 255, 0.12);
    border-radius: 18px;
    padding: 10px 10px 12px;
  }}
  .featured-thumb {{
    border-radius: 12px;
    overflow: hidden;
    aspect-ratio: 11 / 8;
    background: #141824;
  }}
  .featured-thumb img {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    display: block;
  }}
  .featured-sub {{
    margin-top: 10px;
    padding: 0 6px;
    font-size: 18px;
    font-weight: 600;
    color: #f3e4f9;
  }}
  .grid {{
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: 26px;
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
    <div class="sub">{total_count} стилей · чётная и нечётная недели</div>
  </header>
  {featured_html}
  <main class="grid">
    {cards_html}
  </main>
</body>
</html>"""

    tmp_png = renders_dir / "themes_showcase_raw.png"
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": 2048, "height": 2480})
        await page.set_content(html, wait_until="load")
        await page.screenshot(path=str(tmp_png), full_page=True)
        await browser.close()

    output_jpg.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(tmp_png) as img:
        img.convert("RGB").save(output_jpg, "JPEG", quality=86, optimize=True, progressive=True)
    print(f"Saved showcase to {output_jpg} ({output_jpg.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    renders = Path("/Users/ilyan/.gemini/antigravity/brain/9bac0716-3459-41f4-922a-3583d496f692/scratch/renders_v2")
    asyncio.run(build_showcase(renders, ROOT / "bot/media/themes_showcase.jpg"))
