
# --- Этап 1: Сборка зависимостей и копирование исходников (builder) ---
FROM python:3.11-slim as builder

# Устанавливаем рабочую директорию
WORKDIR /app


COPY requirements.txt .
COPY alembic.ini .
COPY migrations/ ./migrations/
COPY core/ ./core/
COPY bot/ ./bot/
COPY main.py .

# Устанавливаем зависимости
# Используем --no-cache-dir для уменьшения размера образа и ускорения билда
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# --- Этап 2: Финальный образ (final) ---
FROM python:3.11-slim as final

# Устанавливаем рабочую директорию
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages/ /usr/local/lib/python3.11/site-packages/

COPY --from=builder /app/bot ./bot
COPY --from=builder /app/core ./core
COPY --from=builder /app/main.py .
COPY --from=builder /app/alembic.ini . 
COPY --from=builder /app/migrations ./migrations 

COPY --from=builder /app/bot/media ./bot/media 
COPY --from=builder /app/bot/screenshots ./bot/screenshots

CMD ["python", "main.py"]