# --- Этап 1: Тестирование ---
FROM python:3.11-slim as builder

WORKDIR /app

# Копируем единый файл с зависимостями
COPY requirements.txt .

# Устанавливаем все зависимости из одного файла
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем весь код проекта
COPY . .

# Запускаем тесты
RUN python -m pytest


# --- Этап 2: Финальный образ ---
FROM python:3.11-slim as final

WORKDIR /app

# Копируем тот же самый requirements.txt
COPY --from=builder /app/requirements.txt .

# И устанавливаем из него все зависимости.
# Да, в финальном образе будут тестовые библиотеки, но для простоты это приемлемо.
# Альтернатива - разделять requirements, как я предлагал ранее.
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Копируем только нужный для работы код
COPY --from=builder /app/bot ./bot
COPY --from=builder /app/core ./core
COPY --from=builder /app/main.py .

CMD ["python", "main.py"]