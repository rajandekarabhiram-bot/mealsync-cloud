FROM python:3.10-slim

# 1. Install text-shaping libraries and compilers
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

# 2. Copy your repository files into the container
COPY . .

# 3. Upgrade pip to the latest version to prevent bugs
RUN pip install --upgrade pip

# 4. FOOLPROOF OVERRIDE: Forcibly delete any mention of Pillow from requirements.txt so Render can't crash on it
RUN sed -i '/[Pp]illow/d' requirements.txt || true
RUN sed -i '/PIL/d' requirements.txt || true

# 5. Install the clean requirements (Flask, Requests, Gunicorn)
RUN pip install --no-cache-dir -r requirements.txt

# 6. Force-compile Pillow from source so it permanently binds to Devanagari text-shaping
RUN pip install --no-cache-dir --no-binary pillow pillow

CMD gunicorn app:app --bind 0.0.0.0:$PORT
