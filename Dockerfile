FROM python:3.13-slim

WORKDIR /app

# Сначала копируем только requirements.txt, чтобы кэшировать установку зависимостей
COPY requirements.txt .

# Устанавливаем системные зависимости для Pillow/Playwright (chromium) и PostgreSQL
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libxcb1 \
    libxkbcommon0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    libatspi2.0-0 \
    libexpat1 \
    libfreetype6 \
    fonts-liberation \
    # PostgreSQL development пакеты для psycopg2
    libpq-dev \
    postgresql-client \
    gcc \
    g++ \
    python3-dev \
    # Дополнительные пакеты для сборки Python модулей
    libffi-dev \
    libssl-dev \
    make \
    file \
    cmake \
    build-essential \
  && rm -rf /var/lib/apt/lists/*

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    # Playwright 1.18 CLI: install-deps; newer: --with-deps. Пытаемся оба способа
    (python -m playwright install-deps chromium || true) && \
    (python -m playwright install --with-deps chromium || python -m playwright install chromium)

# Теперь копируем ВСЕ остальные файлы проекта 
COPY . .

# Команда для запуска приложения
CMD ["python", "main.py"]