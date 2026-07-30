# Imagen del Cloud Run Service que recibe el push de Pub/Sub (Fase 3).
# No usado por `run`/`run-batch` en local -- eso corre directo con el venv.
FROM python:3.13-slim

WORKDIR /app

# Deps de sistema de WeasyPrint (renderizado de PDF/HTML via plantillas Jinja2).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY src ./src
COPY config ./config

RUN pip install --no-cache-dir .

ENV PORT=8080
EXPOSE 8080

CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 \
    reporting_automation.main_entrypoint:app
