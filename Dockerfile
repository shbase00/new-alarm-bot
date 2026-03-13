FROM python:3.11-slim

WORKDIR /app

# System deps needed by Chromium on Debian bookworm (python:3.11-slim)
RUN apt-get update && apt-get install -y \
    wget curl gnupg \
    libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 \
    libx11-6 libxext6 libxcb1 \
    libpango-1.0-0 libcairo2 libatspi2.0-0 \
    fonts-liberation \
    --no-install-recommends \
    && apt-get install -y libasound2t64 2>/dev/null || apt-get install -y libasound2 2>/dev/null || true \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers (Chromium only) + system deps
RUN python -m playwright install chromium
RUN python -m playwright install-deps chromium 2>/dev/null || true

# Copy project files
COPY . .

# Create data directory
RUN mkdir -p data

# Expose API port (Railway uses the PORT env var)
EXPOSE 8080

# Run the bot
CMD ["python", "bot.py"]
