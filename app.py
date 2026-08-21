import os
import io
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Yantramanav-Bold.ttf"

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

fallback_data = {
    "breakfast": "पुरणपोळी, कटाची आमटी, भजी",
    "lunch": "वरण भात, पोळी, वांग्याची भाजी, कोशिंबीर, पापड",
    "dinner": "मसाला खिचडी, कढी, पापड",
    "task1": "दूध आणा",
    "task2": "भाजी धुवा"
}

def get_target_date_and_data():
    now_ist = datetime.now(IST)
    # Switch to tomorrow's menu starting at 09:00 PM (21:00 IST)
    if now_ist.hour >= 21:
        target_date = now_ist + timedelta(days=1)
    else:
        target_date = now_ist

    data = fallback_data.copy()
    if GOOGLE_SCRIPT_URL:
        try:
            resp = requests.get(GOOGLE_SCRIPT_URL, timeout=5)
            if resp.status_code == 200:
                sheet_json = resp.json()
                for k in ["breakfast", "lunch", "dinner", "task1", "task2"]:
                    if k in sheet_json:
                        val = str(sheet_json[k]).strip()
                        data[k] = val if val else "—"
        except Exception as e:
            print(f"[WARN] Sheet fetch error: {e}")

    date_str = target_date.strftime("%a, %d %b %Y").upper()
    return date_str, data

@app.route('/hash', methods=['GET'])
def get_content_hash():
    date_str, data = get_target_date_and_data()
    payload = f"{date_str}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    return {"hash": content_hash}, 200

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

@app.route('/', methods=['GET', 'HEAD'])
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        date_str, data = get_target_date_and_data()

        try:
            rssi = int(request.args.get('rssi', -50))
        except Exception:
            rssi = -50

        batt_str = str(request.args.get('batt', '500d+'))
        try:
            batt_pct = int(request.args.get('pct', 100))
        except Exception:
            batt_pct = 100

        if batt_pct <= 20 and batt_str != "CHG":
            live_date_text = f"{date_str} • CHG REQ"
            date_font_size = 12
        else:
            live_date_text = date_str
            date_font_size = 13

        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255)
        draw = ImageDraw.Draw(img_2x)

        font_logo = safe_font(FONT_ENGLISH_PATH, 18)
        font_date = safe_font(FONT_ENGLISH_PATH, date_font_size)
        font_badge = safe_font(FONT_ENGLISH_PATH, 13)
        font_section = safe_font(FONT_ENGLISH_PATH, 15)

        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255)

        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 96 * SCALE, 13 * SCALE
        draw.rectangle([wifiX + 4, wifiY + 20, wifiX + 8,  wifiY + 28], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 14, wifiY + 12, wifiX + 18, wifiY + 28], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 24, wifiY + 4,  wifiX + 28, wifiY + 28], fill=255 if signal_bars >= 3 else 0)

        date_w = get_text_width(font_date, live_date_text)
        date_center_x = (CANVAS_W - date_w) // 2
        date_y = (12 if date_font_size == 12 else 11) * SCALE
        draw.text((date_center_x, date_y), live_date_text, font=font_date, fill=255)

        batX, batY = 362 * SCALE, 12 * SCALE
        draw.rectangle([batX, batY, batX + 24 * SCALE, batY + 14 * SCALE], outline=255, width=SCALE)
        draw.rectangle([batX + 24 * SCALE, batY + 3 * SCALE, batX + 26 * SCALE, batY + 11 * SCALE], fill=255)

        fill_w = max(0, min(20 * SCALE, int((batt_pct / 100.0) * 20 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + 2 * SCALE, batY + 2 * SCALE, batX + 2 * SCALE + fill_w, batY + 12 * SCALE], fill=255)

        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 10, 11 * SCALE), batt_str, font=font_badge, fill=255)

        sidebar_w = 118 * SCALE
        draw.rectangle([0, 38 * SCALE, sidebar_w, CANVAS_H - 1], fill=0)
        draw.text((10 * SCALE, 52 * SCALE), "BREAKFAST", font=font_section, fill=255)
        draw.text((10 * SCALE, 112 * SCALE), "LUNCH", font=font_section, fill=255)
        draw.text((10 * SCALE, 175 * SCALE), "DINNER", font=font_section, fill=255)
        draw.text((10 * SCALE, 245 * SCALE), "TASKS", font=font_section, fill=255)

        for y_div in [98, 160, 228]:
            draw.line([(0, y_div * SCALE), (sidebar_w, y_div * SCALE)], fill=255, width=2 * SCALE)
            draw.line([(sidebar_w, y_div * SCALE), (CANVAS_W, y_div * SCALE)], fill=0, width=2 * SCALE)

        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)

        draw.rectangle([128 * SCALE, 243 * SCALE, 142 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        draw.rectangle([264 * SCALE, 243 * SCALE, 278 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=Image.Resampling.LANCZOS)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 155 else 0, mode="1")

        if "ESP32" in request.headers.get("User-Agent", ""):
            img_epd = ImageOps.invert(img_1bit.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        buf = io.BytesIO()
        img_1bit.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        print("[CRITICAL EXCEPTION IN APP.PY]")
        traceback.print_exc()
        return f"Internal Error: {err}\n\n{traceback.format_exc()}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
