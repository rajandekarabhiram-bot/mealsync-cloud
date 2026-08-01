FROM debian:bullseye-slim

# 1. Install System Python and Linux's pre-compiled Pillow (which has Raqm built-in natively)
RUN apt-get update && apt-get install -y \
    python3 \
    python3-pip \
    python3-pil \
    libraqm0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

# 2. Install standard web packages (Notice Pillow is NOT installed here)
RUN pip3 install --no-cache-dir -r requirements.txt

COPY . .

# 3. Start the server
CMD python3 -m gunicorn app:app --bind 0.0.0.0:$PORT
