# Local teaching demo only. Never expose Flask's built-in server to the public Internet.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock /app/requirements.lock
RUN pip install --no-cache-dir -r requirements.lock gunicorn==23.0.0
RUN groupadd --system ksm && useradd --system --gid ksm --home-dir /app --shell /usr/sbin/nologin ksm
COPY app.py /app/app.py
COPY ops/lz11/backup_rotate.py /app/ops/lz11/backup_rotate.py
COPY templates/ /app/templates/
COPY static/ /app/static/
RUN mkdir -p /app/instance && chown -R ksm:ksm /app/instance
USER ksm
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "2", "--threads", "16", "--timeout", "60", "app:create_app()"]
