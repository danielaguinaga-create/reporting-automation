# Imagen del Cloud Run Service que recibe el push de Pub/Sub (Fase 3).
# No usado por `run`/`run-batch` en local -- eso corre directo con el venv.
FROM python:3.13-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 \
    reporting_automation.main_entrypoint:app
