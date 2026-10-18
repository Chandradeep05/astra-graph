FROM python:3.11-slim

WORKDIR /app

# Prevent Python from buffering stdout/stderr and writing bytecode
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=backend \
    APP_ENV=production \
    PORT=8000

# Install system dependencies if required for git history exploration
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

# Copy application code
COPY backend /app/backend
COPY scripts /app/scripts

# Create non-root user for security
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/data/repos && \
    chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

# Start Uvicorn binding to $PORT provided by host (e.g. Render / Cloud Run)
CMD ["sh", "-c", "uvicorn app.main:app --app-dir backend --host 0.0.0.0 --port ${PORT:-8000}"]
