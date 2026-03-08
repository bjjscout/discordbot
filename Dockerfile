# Vidmaker3 Discord Bot Dockerfile
#
# This Dockerfile is optimized for the Discord bot with video processing capabilities.
# It includes:
# - Python 3.11 base
# - FFmpeg for video processing
# - All required Python dependencies
#
# Build: docker build -t vidmaker3-bot .

FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONFAULTHANDLER=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libgl1 \
    libglib2.0-0 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy lightweight requirements first for better caching
COPY requirements-bot.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements-bot.txt

# Copy application code
COPY . .

# Create temp directory
RUN mkdir -p /app/temp

# Run the bot
CMD ["python", "discord_bot_new.py"]
