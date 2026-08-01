FROM python:3.10-slim

# Install system-level text shaping libraries for Devanagari script
RUN apt-get update && apt-get install -y \
    libraqm0 \
    libharfbuzz0b \
    libfribidi0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD gunicorn app:app --bind 0.0.0.0:$PORT
