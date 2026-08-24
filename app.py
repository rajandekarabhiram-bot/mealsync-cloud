import os
import io
import csv
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file, render_template_string, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

# ============================================================================
# 1. GOOGLE SHEETS CSV EXPORT URL CONFIGURATION
# ============================================================================
# If you have your published Google Sheet CSV URL in Render Environment variables:
SHEET_CSV_URL = os.environ.get(
    "SHEET_CSV_URL",
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vT5KxJzQo_YOUR_SHEET_ID/pub?output=csv"
)

# 400x300 Canvas Dimensions for N1B4V02 (2x supersampling for clean anti-aliased text)
PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Yantramanav-Bold.ttf"

DEVICE_LOGS = []

# ============================================================================
# 2. AUTO FONT DOWNLOADER
# ============================================================================
def ensure_fonts():
    font_urls = {
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf",
        "Yantramanav-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/yantramanav/Yantramanav-Bold.ttf"
    }
    for filename, url in font_urls.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
            try:
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    with open(filename, "wb") as f:
                        f.write(r.content)
            except Exception:
                pass

ensure_fonts()

# ============================================================================
# 3. GOOGLE SHEETS FETCHING & 9:00 PM ROLLOVER ENGINE
# ============================================================================
def fetch_menu_from_google_sheet():
    now_ist = datetime.now(IST)
    # 9:00 PM IST Rollover to tomorrow's date & schedule
    if now_ist.hour >= 21:
        target_date = now_ist + timedelta(days=1)
    else:
        target_date = now_ist

    target_day = target_date.strftime("%A")
    date_str = target_date.strftime("%a, %d %b %Y").upper()

    default_data = {
        "day": target_day,
        "breakfast": "—",
        "lunch": "—",
        "dinner": "—",
        "task1": "—",
        "task2": "—"
    }

    try:
        res = requests.get(SHEET_CSV_URL, timeout=8)
        if res.status_code == 200:
            lines = res.text.splitlines()
            reader = csv.reader(lines)
            rows = list(reader)
            
            # Expected Google Sheet column headers: Day, Breakfast, Lunch, Dinner, Task1, Task2
            for row in rows[1:]:
                if len(row) >= 6 and row[0].strip().lower() == target_day.lower():
                    return date_str, {
                        "day": target_day,
                        "breakfast": row[1].strip().replace("+", ",") or "—",
                        "lunch": row[2].strip().replace("+", ",") or "—",
                        "dinner": row[3].strip().replace("+", ",") or "—",
                        "task1": row[4].strip().replace("+", ",") or "—",
                        "task2": row[5].strip().replace("+", ",") or "—"
                    }
    except Exception as e:
        print(f"[ERROR] Fetching Google Sheet: {e}")

    return date_str, default_data

# ============================================================================
# 4. CONTENT HASH & TELEMETRY LOGS
# ============================================================================
@app.route('/hash', methods=['GET'])
def get_content_hash():
    date_str, data = fetch_menu_from_google_sheet()
    payload = f"{date_str}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    return jsonify({"hash": content_hash}), 200

@app.route('/log', methods=['POST'])
def receive_device_log():
    try:
        log_entry = request.get_json(force=True)
        now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
        log_entry['timestamp'] = now_str
        DEVICE_LOGS.insert(0, log_entry)
        if len(DEVICE_LOGS) > 300:
            DEVICE_LOGS.pop()
        return jsonify({"status": "logged"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/logs', methods=['GET'])
def view_logs():
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MealSync Telemetry Logs (Google Sheets V1.0)</title>
        <meta http-equiv="refresh" content="20">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; }
            table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; margin-top: 16px; }
            th, td { padding: 12px 16px; text-align: left; font-size: 13px; border-bottom: 1px solid #334155; }
            th { background: #334155; color: #cbd5e1; text-transform: uppercase; font-size: 11px; }
            .badge { padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; background: #0284c7; color: white; }
            .wifi-tag { color: #38bdf8; font-weight: 600; }
        </style>
    </head>
    <body>
        <h2>📊 MealSync Google Sheets Telemetry Logs</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (IST)</th>
                    <th>Action</th>
                    <th>Battery</th>
                    <th>Voltage</th>
                    <th>Wi-Fi Strength</th>
                    <th>Hash</th>
                    <th>Next Sleep Slot</th>
                </tr>
            </thead>
            <tbody>
                {% for log in logs %}
                <tr>
                    <td>{{ log.timestamp }}</td>
                    <td><span class="badge">{{ log.event }}</span></td>
                    <td><b>{{ log.batt }}</b> ({{ log.pct }}%)</td>
                    <td>{{ log.v }} V</td>
                    <td><span class="wifi-tag">{{ log.wifi_strength }}</span></td>
                    <td><code>{{ log.hash }}</code></td>
                    <td>{{ log.sleep_sec }}s</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """
    return render_template_string(html_template, logs=DEVICE_LOGS)

# ============================================================================
# 5. E-PAPER BITMAP RENDERER (400x300 Otsu 1-Bit Stream for N1B4V02)
# ============================================================================
def safe_font(font_path, size_1x):
    try:
        return ImageFont.truetype(font_path, size_1x * SCALE)
    except Exception:
        return ImageFont.load_default()

def is_ascii(s):
    return all(ord(c) < 128 for c in s)

def get_text_width(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * 16

def get_wrapped_lines(text, font, max_width_2x):
    words = str(text).strip().split()
    if not words:
        return []
    lines, curr = [], []
    for w in words:
        test_line = " ".join(curr + [w])
        if get_text_width(font, test_line) <= max_width_2x:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
                curr = [w]
            else:
                lines.append(w)
                curr = []
    if curr:
        lines.append(" ".join(curr))
    return lines

def draw_autofit_text(draw, text_str, x_1x, y_1x, max_w_1x, max_h_1x, max_size=18, min_size=13, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH
    selected_font = None
    selected_lines = []
    line_mult = 1.35 if is_ascii(text_str) else 1.40
    
    max_w_2x = max_w_1x * SCALE
    max_h_2x = max_h_1x * SCALE

    for size in range(max_size, min_size - 1, -1):
        test_font = safe_font(font_file, size)
        lines = get_wrapped_lines(text_str, test_font, max_w_2x)
        line_h = int((size * SCALE) * line_mult)
        total_h = len(lines) * line_h
        if len(lines) <= max_lines and total_h <= max_h_2x:
            selected_font = test_font
            selected_lines = lines
            break
            
    if not selected_font:
        selected_font = safe_font(font_file, min_size)
        selected_lines = get_wrapped_lines(text_str, selected_font, max_w_2x)[:max_lines]

    line_h = int((selected_font.size) * line_mult) if hasattr(selected_font, 'size') else 36
    curr_y = y_1x * SCALE
    for line in selected_lines:
        draw.text((x_1x * SCALE, curr_y), line, font=selected_font, fill=fill_color)
        curr_y += line_h

@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        date_str, data = fetch_menu_from_google_sheet()

        rssi = int(request.args.get('rssi', -50))
        batt_str = str(request.args.get('batt', '500d+'))
        batt_pct = int(request.args.get('pct', 100))

        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255)
        draw = ImageDraw.Draw(img_2x)

        font_logo = safe_font(FONT_ENGLISH_PATH, 18)
        font_date = safe_font(FONT_ENGLISH_PATH, 13)
        font_badge = safe_font(FONT_ENGLISH_PATH, 13)
        font_section = safe_font(FONT_ENGLISH_PATH, 15)

        # Header Bar
        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255)

        # Wi-Fi Signal Bars
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 96 * SCALE, 13 * SCALE
        draw.rectangle([wifiX + 4, wifiY + 20, wifiX + 8,  wifiY + 28], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 14, wifiY + 12, wifiX + 18, wifiY + 28], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 24, wifiY + 4,  wifiX + 28, wifiY + 28], fill=255 if signal_bars >= 3 else 0)

        # Center Date
        date_w = get_text_width(font_date, date_str)
        date_center_x = (CANVAS_W - date_w) // 2
        draw.text((date_center_x, 11 * SCALE), date_str, font=font_date, fill=255)

        # Battery Icon & Runtime Badge
        batX, batY = 362 * SCALE, 12 * SCALE
        draw.rectangle([batX, batY, batX + 24 * SCALE, batY + 14 * SCALE], outline=255, width=SCALE)
        draw.rectangle([batX + 24 * SCALE, batY + 3 * SCALE, batX + 26 * SCALE, batY + 11 * SCALE], fill=255)

        fill_w = max(0, min(20 * SCALE, int((batt_pct / 100.0) * 20 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + 2 * SCALE, batY + 2 * SCALE, batX + 2 * SCALE + fill_w, batY + 12 * SCALE], fill=255)

        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 10, 11 * SCALE), batt_str, font=font_badge, fill=255)

        # Left Sidebar Sections
        sidebar_w = 118 * SCALE
        draw.rectangle([0, 38 * SCALE, sidebar_w, CANVAS_H - 1], fill=0)
        draw.text((10 * SCALE, 52 * SCALE), "BREAKFAST", font=font_section, fill=255)
        draw.text((10 * SCALE, 112 * SCALE), "LUNCH", font=font_section, fill=255)
        draw.text((10 * SCALE, 175 * SCALE), "DINNER", font=font_section, fill=255)
        draw.text((10 * SCALE, 245 * SCALE), "TASKS", font=font_section, fill=255)

        # Divider Grid Lines
        for y_div in [98, 160, 228]:
            draw.line([(0, y_div * SCALE), (sidebar_w, y_div * SCALE)], fill=255, width=2 * SCALE)
            draw.line([(sidebar_w, y_div * SCALE), (CANVAS_W, y_div * SCALE)], fill=0, width=2 * SCALE)

        # Autofit Meals
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)

        # Task Checkboxes
        draw.rectangle([128 * SCALE, 243 * SCALE, 142 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        draw.rectangle([264 * SCALE, 243 * SCALE, 278 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        # Lanczos Supersampled Anti-Aliasing Downscale
        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=Image.Resampling.LANCZOS)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 155 else 0, mode="1")

        # Explicit Raw 1-bit Stream for ESP32 Firmware
        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1':
            img_epd = ImageOps.invert(img_1bit.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        # Standard BMP container for Web Browser / Preview
        buf = io.BytesIO()
        img_1bit.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        traceback.print_exc()
        return f"Internal Error: {err}", 500

# ============================================================================
# 6. ROOT ROUTE - LIVE WEB PREVIEW
# ============================================================================
@app.route('/')
def live_preview_home():
    html_page = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1.0" />
        <title>MealSync • Google Sheets Live E-Paper Stream</title>
        <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-900 text-slate-100 min-h-screen flex flex-col items-center justify-center p-4 space-y-4">
        <div class="max-w-md w-full bg-slate-800 border border-slate-700 rounded-3xl p-5 shadow-2xl text-center space-y-4">
            <div>
                <h1 class="text-xl font-extrabold text-white">🍳 MealSync Live Screen Preview</h1>
                <p class="text-xs text-slate-400 mt-1">Live 400×300 monochrome stream synced with Google Sheets</p>
            </div>
            
            <div class="bg-slate-950 p-2 rounded-2xl border border-slate-700 overflow-hidden">
                <img id="screen-img" src="/display.bmp" alt="MealSync Display" class="w-full h-auto rounded-xl shadow-inner border border-slate-800" />
            </div>

            <div class="flex gap-2">
                <button onclick="refreshImage()" class="flex-1 bg-teal-600 hover:bg-teal-500 text-white font-bold text-xs py-3 rounded-xl transition-all shadow-md shadow-teal-600/20">
                    🔄 Refresh Screen
                </button>
                <a href="/logs" class="flex-1 bg-slate-700 hover:bg-slate-600 text-slate-200 font-bold text-xs py-3 rounded-xl text-center transition-all">
                    📊 Hardware Logs
                </a>
            </div>
        </div>

        <script>
            function refreshImage() {
                const img = document.getElementById('screen-img');
                img.src = '/display.bmp?t=' + new Date().getTime();
            }
        </script>
    </body>
    </html>
    """
    return render_template_string(html_page)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
