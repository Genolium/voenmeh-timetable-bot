FROM python:3.11-slim

WORKDIR /app

# Сначала копируем только requirements.txt, чтобы кэшировать установку зависимостей
COPY requirements.txt .

# Устанавливаем системные зависимости для Pillow
RUN apt-get update && apt-get install -y libfreetype6 --no-install-recommends && rm -rf /var/lib/apt/lists/*
RUN pip install --no-cache-dir playwright==1.46.0 && playwright install --with-deps chromium

# Устанавливаем зависимости Python
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Теперь копируем ВСЕ остальные файлы проекта (включая папку assets)
COPY . .

# Команда для запуска приложения
CMD ["python", "main.py"]