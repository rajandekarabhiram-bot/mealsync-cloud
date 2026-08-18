import os
import io
import requests
import traceback
from datetime import datetime
from flask import Flask, request, Response, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)

# ============================================================================
# 1. CONFIGURATION & URLS
# ============================================================================
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Mukta-Bold.ttf"
FONT_HEADER_PATH  = "ProFont.ttf"

PANEL_WIDTH = 400
PANEL_HEIGHT = 300

def ensure_fonts():
    font_urls = {
        "Mukta-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/mukta/Mukta-Bold.ttf",
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf"
    }
    for filename, url in font_urls.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 1000:
            try:
                print(f"[FONT] Downloading {filename}...")
                r = requests.get(url, timeout=10)
                if r.status_code == 200:
                    with open(filename, "wb") as f:
                        f.write(r.content)
            except Exception as e:
                print(f"[FONT ERROR] Could not fetch {filename}: {e}")

ensure_fonts()

fallback_data = {
    "breakfast": "पुरणपोळी, कटाची आमटी, भजी",
    "lunch": "वरण भात, चपाती, वांग्याची भाजी, कोशिंबीर, पापड",
    "dinner": "मसाला खिचडी, कढी, पापड",
    "task1": "दूध आणा",
    "task2": "भाजी धुवा",
    "prep": "काजू भिजवून ठेवा",
    "waste": "उघडे दूध, पालक"
}

# ============================================================================
# 2. BULLETPROOF TYPOGRAPHY & DEVANAGARI WRAPPING
# ============================================================================
def safe_font(font_path, size):
    if font_path and os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    for sys_f in [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "DejaVuSans-Bold.ttf"
    ]:
        if os.path.exists(sys_f):
            try:
                return ImageFont.truetype(sys_f, size)
            except Exception:
                pass
    return ImageFont.load_default()

def is_ascii(s):
    return all(ord(c) < 128 for c in s)

def get_text_width(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        try:
            w, _ = font.getsize(text)
            return w
        except Exception:
            return len(text) * 8

def get_wrapped_lines(text, font, max_width):
    words = str(text).strip().split()
    if not words:
        return []
    lines = []
    curr = []
    for w in words:
        test_line = " ".join(curr + [w])
        if get_text_width(font, test_line) <= max_width:
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

def draw_autofit_text(draw, text_str, x, y, max_width, max_height, max_font_size=20, min_font_size=13, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH
    selected_font = None
    selected_lines = []
    
    for size in range(max_font_size, min_font_size - 1, -1):
        test_font = safe_font(font_file, size)
        lines = get_wrapped_lines(text_str, test_font, max_width)
        line_h = int(size * 1.25)
        total_h = len(lines) * line_h
        if len(lines) <= max_lines and total_h <= max_height:
            selected_font = test_font
            selected_lines = lines
            break
            
    if not selected_font:
        selected_font = safe_font(font_file, min_font_size)
        selected_lines = get_wrapped_lines(text_str, selected_font, max_width)[:max_lines]

    line_h = int(selected_font.size * 1.25) if hasattr(selected_font, 'size') else 16
    curr_y = y
    for line in selected_lines:
        draw.text((x, curr_y), line, font=selected_font, fill=fill_color)
        curr_y += line_h

# ============================================================================
# 3. MASTER IMAGE RENDERER (Native 400x300 Tack-Sharp Engine)
# ============================================================================
@app.route('/', methods=['GET', 'HEAD'])
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        # 1. Fetch live Google Sheet Data
        data = fallback_data.copy()
        if GOOGLE_SCRIPT_URL:
            try:
                resp = requests.get(GOOGLE_SCRIPT_URL, timeout=4)
                if resp.status_code == 200:
                    sheet_json = resp.json()
                    for k in ["breakfast", "lunch", "dinner", "task1", "task2", "prep", "waste"]:
                        if k in sheet_json and sheet_json[k]:
                            data[k] = str(sheet_json[k])
            except Exception as e:
                print(f"[WARN] Sheet fetch skipped: {e}")

        # 2. Parse Query Params from ESP32
        try:
            rssi = int(request.args.get('rssi', -50))
        except Exception:
            rssi = -50

        batt_str = str(request.args.get('batt', '500d+'))
        try:
            batt_pct = int(request.args.get('pct', 95))
        except Exception:
            batt_pct = 95

        live_date = datetime.now().strftime("%a, %d %b %Y").upper()

        # 3. Direct Native 400x300 Canvas (1-bit Mode: 1=White, 0=Black)
        img = Image.new("1", (PANEL_WIDTH, PANEL_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Bold, Sharp Fonts at 1:1 Pixel Scale
        font_logo = safe_font(FONT_ENGLISH_PATH, 20)
        font_date = safe_font(FONT_ENGLISH_PATH, 13)
        font_badge = safe_font(FONT_ENGLISH_PATH, 13)
        font_section = safe_font(FONT_ENGLISH_PATH, 15)

        # --- A. Header Bar ---
        draw.rectangle([0, 0, PANEL_WIDTH - 1, 38], fill=0)
        draw.rectangle([0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1], outline=0, width=2)

        # Logo
        draw.text((10, 8), "MealSync", font=font_logo, fill=1)

        # Wi-Fi RSSI Bars
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 112, 14
        draw.rectangle([wifiX, wifiY + 8, wifiX + 2, wifiY + 12], fill=1 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 4, wifiY + 4, wifiX + 6, wifiY + 12], fill=1 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 8, wifiY, wifiX + 10, wifiY + 12], fill=1 if signal_bars >= 3 else 0)

        # Live Date
        draw.text((130, 11), live_date, font=font_date, fill=1)

        # Battery Icon & Adjacent Badge
        batX, batY = 362, 12
        draw.rectangle([batX, batY, batX + 24, batY + 14], outline=1, width=1)
        draw.rectangle([batX + 24, batY + 3, batX + 26, batY + 11], fill=1)

        if batt_str == "CHG":
            # Lightning bolt inside battery
            draw.polygon([
                (batX + 12, batY + 2), (batX + 7, batY + 7), 
                (batX + 11, batY + 7), (batX + 10, batY + 12), 
                (batX + 17, batY + 6), (batX + 13, batY + 6)
            ], fill=1)
        else:
            fill_w = max(0, min(20, int((batt_pct / 100.0) * 20)))
            if fill_w > 0:
                draw.rectangle([batX + 2, batY + 2, batX + 2 + fill_w, batY + 12], fill=1)

        # Draw battery string (CHG, 500d+) right next to battery icon
        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 5, 11), batt_str, font=font_badge, fill=1)

        # --- B. Left Sidebar & Sections ---
        sidebar_w = 118
        draw.rectangle([0, 38, sidebar_w, PANEL_HEIGHT - 1], fill=0)

        draw.text((10, 52), "BREAKFAST", font=font_section, fill=1)
        draw.text((10, 112), "LUNCH", font=font_section, fill=1)
        draw.text((10, 175), "DINNER", font=font_section, fill=1)
        draw.text((10, 245), "TASKS", font=font_section, fill=1)

        # Dividers
        for y_div in [98, 160, 228]:
            draw.line([(0, y_div), (sidebar_w, y_div)], fill=1, width=2)
            draw.line([(sidebar_w, y_div), (PANEL_WIDTH, y_div)], fill=0, width=2)

        # --- C. Main Meals Content (Crisp Devanagari / English) ---
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0)

        # --- D. Tasks Footer ---
        draw.rectangle([128, 244, 140, 256], outline=0, width=1)
        draw_autofit_text(draw, data["task1"], 146, 240, 110, 24, max_font_size=14, min_font_size=11, max_lines=1, fill_color=0)

        draw.rectangle([265, 244, 277, 256], outline=0, width=1)
        draw_autofit_text(draw, data["task2"], 283, 240, 110, 24, max_font_size=14, min_font_size=11, max_lines=1, fill_color=0)

        # 4. Stream Raw 15,000 Bytes to ESP32
        if "ESP32" in request.headers.get("User-Agent", ""):
            img_epd = ImageOps.invert(img.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        # BMP for browser verification
        buf = io.BytesIO()
        img.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        print("[CRITICAL EXCEPTION IN APP.PY]")
        traceback.print_exc()
        return f"Internal Error: {err}\n\n{traceback.format_exc()}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
