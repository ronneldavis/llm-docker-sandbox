# syntax=docker/dockerfile:1
FROM python:3.12-slim

# ---------------------------------------------------------------------------
# System packages available inside the sandbox execution environment.
# Add/remove tools here to control what users can invoke.
# ---------------------------------------------------------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
        bash \
        coreutils \
        findutils \
        grep \
        gawk \
        sed \
        curl \
        git \
        jq \
        unzip \
        procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

# Persistent sandbox file storage (mount a volume here in production)
RUN mkdir -p /sandboxes

# Non-root user for the API process itself
RUN useradd -m -u 1000 sandboxuser \
    && chown -R sandboxuser:sandboxuser /app /sandboxes

USER sandboxuser

EXPOSE 8000

# Environment variable defaults (override in docker-compose or -e flags)
ENV NUM_WORKERS=4 \
    EXEC_TIMEOUT=30 \
    MEM_LIMIT_MB=256 \
    SANDBOX_DIR=/sandboxes

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
