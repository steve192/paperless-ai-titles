FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml /app/pyproject.toml
COPY src /app/src
COPY templates /app/templates
COPY static /app/static

RUN pip install --upgrade pip && \
    pip install -e .

COPY scripts /app/scripts

CMD ["uvicorn", "paperless_ai_titles.fastapi_app:app", "--host", "0.0.0.0", "--port", "8080"]
