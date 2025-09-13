# Dockerfile - builds the app image that runs FastAPI + Temporal workers
FROM python:3.11-slim

# System deps for asyncpg + building wheels
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev curl git && \
    rm -rf /var/lib/apt/lists/*

# Create app user for safety
RUN useradd --create-home appuser
WORKDIR /home/appuser/app

# Copy only requirements first to leverage Docker cache
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy app sources
COPY src/ ./src
COPY migrations/ ./migrations
COPY examples/ ./examples

# Expose port for FastAPI
EXPOSE 8000

# Default env (overridable)
ENV DATABASE_URL="postgres://app:trellisAI@postgres:5432/appdb"
ENV TEMPORAL_ADDRESS="temporal:7233"
ENV FLAKY_MODE="ON"
ENV PYTHONUNBUFFERED=1

# Run the FastAPI + worker entrypoint (main should start workers & API)
CMD ["python", "-m", "uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]