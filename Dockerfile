FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY app ./app
ARG EMBEDDING_EXTRAS=""
RUN pip install --no-cache-dir ".${EMBEDDING_EXTRAS}" && pip check
COPY alembic ./alembic
COPY alembic.ini ./

RUN install -d -m 0700 /var/lib/rag/uploads

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
