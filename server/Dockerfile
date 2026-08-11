FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
# Default to a path that exists on every plan. The blueprint overrides this,
# but a disk-less host has no /data, and SQLite failing to open its file
# takes the whole app down at import time.
ENV DB_PATH=/tmp/mindora.db
EXPOSE 8000
CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
