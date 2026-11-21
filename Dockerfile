# Build the UI, then serve it and the API from one small Python image.
FROM node:22-alpine AS ui
WORKDIR /ui
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.13-slim
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock backend/
RUN cd backend && uv sync --frozen --no-dev --group llm --group s3 --group adls --group gcs --group kafka --group lake --group search --group durable --group tracing --group ml
COPY backend/ backend/
COPY --from=ui /ui/dist frontend/dist
# Everything the app writes on disk is under /app/data. It belongs to the user Kubernetes runs the pods as, so the
# image starts as that user (docker run --user 10001:10001) as well as root, with nothing else to set.
RUN mkdir -p data/samples data/lake data/evidence data/quarantine data/warehouse && chown -R 10001:10001 data
ENV UV_NO_SYNC=1 UV_CACHE_DIR=/tmp/uv HOME=/tmp
WORKDIR /app/backend
EXPOSE 8000
CMD ["uv", "run", "--no-dev", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
