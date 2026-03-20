FROM python:3.11-slim

# Install ffmpeg (needed for music + voice AI)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install dependencies first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy all bot files
COPY . .

# Create data directories
RUN mkdir -p data/welcome_images logs

CMD ["python", "main.py"]
