FROM python:3.12-slim AS builder

WORKDIR /app

# Install system deps needed for building Python packages
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.12-slim

# Install curl for Docker healthcheck + create non-root user
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser && useradd -r -g appuser -m appuser

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY src ./src

# Copy CrewAI skills (copied into build context by build.sh)
# These provide domain expertise to crew agents
COPY --chown=appuser:appuser skills /app/skills

RUN chown -R appuser:appuser /app
USER appuser

# Environment variable for skills path (optional, for documentation)
ENV CREWAI_SKILLS_PATH=/app/skills

EXPOSE 8081

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8081"]
