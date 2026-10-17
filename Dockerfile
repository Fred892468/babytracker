FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY static ./static
RUN useradd -r -u 10001 app && mkdir /data && chown app /data
USER app
ENV PORT=8000
EXPOSE 8000
# 1 worker: il rate limit dei login e' in memoria; SQLite gestisce bene il carico familiare
CMD exec gunicorn -b 0.0.0.0:${PORT} -w 1 --threads 4 app:app
