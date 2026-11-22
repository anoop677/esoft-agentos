FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv/agentos

COPY pyproject.toml ./
COPY app ./app

RUN pip install --upgrade pip && pip install .

RUN useradd --create-home --uid 10001 agentos && chown -R agentos:agentos /srv/agentos
USER agentos

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
