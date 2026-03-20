FROM python:3.11-slim

# Install ffmpeg (needed for music + voice AI)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot files
COPY . .

# Create persistent data directories
RUN mkdir -p data/welcome_images logs

# Koyeb requires a PORT env var even for non-HTTP apps
ENV PORT=8080

CMD ["python", "main.py"]
