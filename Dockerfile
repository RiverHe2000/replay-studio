# syntax=docker/dockerfile:1
FROM node:22-bookworm-slim AS frontend
WORKDIR /web
RUN npm install --global pnpm@11.19.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.12-slim-bookworm AS runtime
ADD https://www.postgresql.org/media/keys/ACCC4CF8.asc /usr/share/keyrings/postgresql.asc
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REPLAY_DATA_DIR=/data HF_HOME=/models/huggingface \
    REPLAY_MODEL_DEVICE=cpu REPLAY_ASR_MODEL=base
RUN printf 'deb [signed-by=/usr/share/keyrings/postgresql.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main\n' > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg libgomp1 libgl1 libglib2.0-0 postgresql-client-16 fonts-dejavu-core espeak-ng \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -g 10001 replay && useradd -m -u 10001 -g replay replay \
    && mkdir -p /data /models /app && chown -R replay:replay /data /models /app
WORKDIR /app
COPY pyproject.toml ./
COPY requirements-cpu.lock ./
COPY src/ ./src/
RUN pip install --no-cache-dir --require-hashes --extra-index-url https://download.pytorch.org/whl/cpu -r requirements-cpu.lock \
    && pip install --no-cache-dir --no-deps .
COPY scripts/ ./scripts/
COPY --from=frontend /web/dist ./frontend/dist
USER replay
EXPOSE 8080
CMD ["python", "-m", "replay_studio.cli", "serve", "--host", "0.0.0.0", "--port", "8080"]

# Optional NVIDIA image. Host needs the NVIDIA container toolkit. CUDA execution
# must be verified on that host; the CPU profile is always available.
FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu24.04 AS cuda
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REPLAY_DATA_DIR=/data HF_HOME=/models/huggingface \
    REPLAY_MODEL_DEVICE=cuda REPLAY_ASR_MODEL=base PATH=/opt/venv/bin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.12 python3.12-venv ffmpeg libgomp1 libgl1 libglib2.0-0t64 postgresql-client fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && python3.12 -m venv /opt/venv \
    && groupadd -g 10001 replay && useradd -m -u 10001 -g replay replay \
    && mkdir -p /data /models /app && chown -R replay:replay /data /models /app
WORKDIR /app
COPY pyproject.toml ./
COPY requirements-cuda.lock ./
COPY src/ ./src/
RUN pip install --no-cache-dir --require-hashes --extra-index-url https://download.pytorch.org/whl/cu128 -r requirements-cuda.lock \
    && pip install --no-cache-dir --no-deps .
COPY scripts/ ./scripts/
COPY --from=frontend /web/dist ./frontend/dist
USER replay
CMD ["python", "-m", "replay_studio.cli", "worker", "--queue", "gpu"]
