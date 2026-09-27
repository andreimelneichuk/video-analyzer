# --- Stage 1: Dependency builder with uv ---
FROM python:3.13-slim AS builder

WORKDIR /app

# Install curl to fetch uv
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates && rm -rf /var/lib/apt/lists/*
ADD https://astral.sh/uv/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"

# Copy dependency definition
COPY pyproject.toml .

# Create virtual environment and install dependencies
RUN uv venv /app/.venv && uv pip install --no-cache -r pyproject.toml

# --- Stage 2: Final lightweight runtime container ---
FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg (required for audio/video stream extraction) and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Copy source code and documentation
COPY . /app

# Ensure data and temp directories exist
RUN mkdir -p /app/data /app/temp

EXPOSE 8080

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
