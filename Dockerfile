# Use a slim, official Python base image
FROM python:3.11-slim

# Prevent Python from writing pyc files and buffering stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000

# Establish working directory
WORKDIR /app

# Create a non-privileged user to run the app
RUN groupadd -g 1000 appuser && \
    useradd -u 1000 -g appuser -m -s /bin/bash appuser

# Copy dependency configuration and readme first to leverage build cache
COPY pyproject.toml README.md ./

# Install production dependencies via project metadata
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .

# Copy application source code and assets
COPY src/ ./src/

# Change ownership to the non-root user
RUN chown -R appuser:appuser /app

# Switch to the non-privileged user
USER appuser

# Expose port conceptually
EXPOSE 8000

# Start FastAPI application using uvicorn, reading dynamically bound $PORT
CMD ["sh", "-c", "uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT}"]
