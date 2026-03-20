FROM python:3.11-slim

# Install system dependencies including libsodium for PyNaCl (voice support)
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
    libsodium-dev \
    libsodium23 \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p data/welcome_images logs

ENV PORT=8080

CMD ["python", "main.py"]
