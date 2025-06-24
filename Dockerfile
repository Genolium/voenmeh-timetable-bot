# 1. Базовый образ
# Используем slim-версию Python для уменьшения размера образа
FROM python:3.11-slim

# 2. Устанавливаем рабочую директорию внутри контейнера
WORKDIR /app

# 3. Устанавливаем переменные окружения
#    - PYTHONUNBUFFERED: гарантирует, что логи сразу выводятся в консоль Docker
#    - PYTHONPATH: говорит Python искать модули в корне /app
ENV PYTHONUNBUFFERED 1
ENV PYTHONPATH /app

# 4. Копируем файлы с зависимостями
COPY requirements.txt .

# 5. Устанавливаем зависимости
#    - --no-cache-dir: не сохраняем кэш pip, чтобы уменьшить размер образа
#    - --upgrade pip: обновляем сам pip
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# 6. Копируем весь остальной код проекта в рабочую директорию
COPY . .

# 7. Команда, которая будет выполняться при запуске контейнера
CMD ["python", "main.py"]