FROM python:3.12-slim
WORKDIR /app
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
COPY requirements.lock .
RUN pip install --no-cache-dir -r requirements.lock && python -m playwright install --with-deps chromium
RUN useradd --create-home --uid 10001 app && mkdir -p /app/instance && chown app:app /app/instance
COPY --chown=app:app . .
USER app
EXPOSE 5000
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "120", "app:create_app()"]
