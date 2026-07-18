FROM python:3.11-slim

WORKDIR /app

# Install system dependencies if any
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    nodejs \
    npm \
    && rm -rf /var/lib/apt/lists/*

RUN npm install -g @openai/codex

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend and frontend source files
COPY app/ ./app/
COPY frontend/ ./frontend/
COPY workers/ ./workers/
COPY docker/start.sh /app/docker/start.sh
COPY *.md ./

RUN chmod +x /app/docker/start.sh

# Expose port 6891
EXPOSE 6891

# Run the backend server
CMD ["/app/docker/start.sh"]
