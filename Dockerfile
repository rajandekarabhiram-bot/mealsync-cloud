FROM python:3.10-slim

# Install system-level text-shaping libraries AND C-compilers
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

COPY requirements.txt .

# Install standard requirements
RUN pip install --no-cache-dir -r requirements.txt

# FORCE Pillow to install and compile from source without a strict version number
RUN pip install --no-cache-dir --no-binary pillow pillow

COPY . .

CMD gunicorn app:app --bind 0.0.0.0:$PORT
