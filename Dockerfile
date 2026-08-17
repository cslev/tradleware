# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory in container
WORKDIR /app

# Set environment variables
ENV PYTHONPATH=/app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker layer caching
COPY src/requirement.txt ./requirements.txt

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY src/ ./src/


# Create non-root user for security
RUN adduser --disabled-password --gecos '' --uid 1000 tradleware

# Create logs directory and set proper permissions
RUN mkdir -p src/logs && \
    chown -R tradleware:tradleware /app && \
    chmod -R 755 /app/src/logs

# Switch to non-root user
USER tradleware

# Documents that port 8080 is used
EXPOSE 8080 

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/ || exit 1

# Command to run the application
# --no-proxy-headers: uvicorn must not rewrite the client address from
# X-Forwarded-For (it trusts 127.0.0.1 by default). Tradleware resolves the
# client IP itself via TRUSTED_PROXIES, which requires request.client.host to be
# the real TCP peer.
CMD ["python", "-m", "uvicorn", "src.ui.app:app", "--host", "0.0.0.0", "--port", "8080", "--no-access-log", "--no-proxy-headers"]