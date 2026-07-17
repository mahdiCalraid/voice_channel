FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend source files
COPY app/ ./app/
COPY frontend/ ./frontend/

# Expose port 6891
EXPOSE 6891

# Run the backend server
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "6891", "--reload"]
