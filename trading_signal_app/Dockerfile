FROM python:3.10-slim

WORKDIR /app

# Install system dependencies (required for some Python packages)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy dependencies list and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend files and static simulator client
COPY backend/ ./backend/
COPY simulator/ ./simulator/

# Expose port (Render/Railway will override or route dynamically via $PORT)
EXPOSE 8000

# Run uvicorn server in production mode
CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
