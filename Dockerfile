FROM python:3.10-slim

# Install text-shaping libraries AND the compiler tools needed to build Pillow
RUN apt-get update && apt-get install -y \
    libraqm-dev \
    libharfbuzz-dev \
    libfribidi-dev \
    libfreetype6-dev \
    libjpeg-dev \
    zlib1g-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy files
COPY requirements.txt .
COPY . .

# 1. Install standard web packages
RUN pip install --no-cache-dir -r requirements.txt

# 2. FORCE Pillow to compile from source so it binds to the libraqm engine
RUN pip install --no-cache-dir --no-binary pillow pillow

CMD gunicorn app:app --bind 0.0.0.0:$PORT
