FROM python:3.12-slim

# System dependencies pentru Playwright + ffmpeg
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    ca-certificates \
    ffmpeg \
    libnss3 \
    libnspr4 \
    libdbus-1-3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libcairo2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instaleaza dependentele Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Instaleaza Playwright Chromium
RUN playwright install chromium
RUN playwright install-deps chromium

# Copiaza codul
COPY . .

# Creaza folderele necesare
RUN mkdir -p data/temp/pinterest data/temp/products data/logs data/music

# Porneste serverul
CMD ["python", "server.py"]
