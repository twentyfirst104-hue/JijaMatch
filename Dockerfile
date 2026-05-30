# Dockerfile — на случай, если предпочитаете контейнерный деплой
# (Amvera поддерживает и Docker-образы, и нативный python-toolchain через amvera.yml).
FROM python:3.11-slim

WORKDIR /app

# Сначала зависимости (кешируется лучше)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Затем код
COPY . .

# Каталог для постоянных данных (БД и бэкапы).
# На Amvera смонтируйте сюда persistent storage и задайте
# DB_PATH=/data/bot.db, BACKUP_DIR=/data/backups в переменных окружения.
RUN mkdir -p /data

CMD ["python", "bot.py"]
