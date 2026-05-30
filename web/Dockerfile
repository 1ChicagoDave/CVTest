# Automap bridge server image.
# Build context is the web/ directory:  docker build -t automap-bridge web/
FROM python:3.11-slim

# libglib2.0-0 is the one shared lib opencv-python-headless still links against.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements-server.txt .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir -r requirements-server.txt

COPY . .

EXPOSE 8000
CMD ["python", "-m", "uvicorn", "automap.server:app", "--host", "0.0.0.0", "--port", "8000"]
