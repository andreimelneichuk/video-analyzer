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

# sing-box: optional VLESS client for YouTube downloads (see src/vpn_bootstrap.py)
ARG SING_BOX_VERSION=1.11.4
ARG TARGETARCH=amd64
RUN curl -fsSL "https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/sing-box-${SING_BOX_VERSION}-linux-${TARGETARCH}.tar.gz" \
    | tar -xz -C /tmp && mv /tmp/sing-box-*/sing-box /usr/local/bin/sing-box

# --- Stage 2: Final lightweight runtime container ---
FROM python:3.13-slim

WORKDIR /app

# Install ffmpeg (required for audio/video stream extraction) and ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Copy source code and documentation
COPY . /app

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /usr/local/bin/sing-box /usr/local/bin/sing-box
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

# Ensure data and temp directories exist
RUN mkdir -p /app/data /app/temp

EXPOSE 8080

# Starts the VLESS proxy when VLESS_URL is set, then execs uvicorn
CMD ["python", "-m", "src.vpn_bootstrap"]
