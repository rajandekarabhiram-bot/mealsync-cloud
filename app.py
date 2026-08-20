import os
import io
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)

# Indian Standard Time (UTC +5:30)
IST = timezone(timedelta(hours=5, minutes=30))

# Replace with your actual Google Script Web App Executable URL
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Mukta-Bold.ttf"

PANEL_WIDTH = 400
PANEL_HEIGHT = 300

# In-memory version tracker for Solution B micro-heartbeats
current_menu_version = 1

def ensure_fonts():
    font_urls = {
        "Mukta-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/mukta/Mukta-Bold.ttf",
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf"
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

def safe_font(font_path, size):
    try:
        return ImageFont.truetype(font_path, size)
    except Exception:
        return ImageFont.load_default()

def is_ascii(s):
    return all(ord(c) < 128 for c in s)

def get_text_width(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * 8

def get_wrapped_lines(text, font, max_width):
    words = str(text).strip().split()
    if not words:
        return []
    lines, curr = [], []
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

def draw_autofit_text(draw, text_str, x, y, max_width, max_height, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH
    selected_font = None
    selected_lines = []
    line_mult = 1.35 if is_ascii(text_str) else 1.40
    
    for size in range(max_font_size, min_font_size - 1, -1):
        test_font = safe_font(font_file, size)
        lines = get_wrapped_lines(text_str, test_font, max_width)
        line_h = int(size * line_mult)
        total_h = len(lines) * line_h
        if len(lines) <= max_lines and total_h <= max_height:
            selected_font = test_font
            selected_lines = lines
            break
            
    if not selected_font:
        selected_font = safe_font(font_file, min_font_size)
        selected_lines = get_wrapped_lines(text_str, selected_font, max_width)[:max_lines]

    line_h = int(selected_font.size * line_mult) if hasattr(selected_font, 'size') else 18
    curr_y = y
    for line in selected_lines:
        draw.text((x, curr_y), line, font=selected_font, fill=fill_color)
        curr_y += line_h

# ============================================================================
# VERSION CONTROL ENDPOINTS (Solution B Micro-Heartbeat)
# ============================================================================
@app.route('/sheet-updated', methods=['POST'])
def handle_sheet_webhook():
    global current_menu_version
    current_menu_version += 1
    print(f"[SHEET WEBHOOK] Google Sheet modified! Incrementing Version to: {current_menu_version}")
    return {"status": "ok", "version": current_menu_version}, 200

@app.route('/version', methods=['GET'])
def get_version():
    return {"v": current_menu_version}, 200

# ============================================================================
# MASTER DISPLAY RENDERING ENDPOINT
# ============================================================================
@app.route('/', methods=['GET', 'HEAD'])
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        data = fallback_data.copy()
        
        # 1. Fetch live Google Sheet Data
        if GOOGLE_SCRIPT_URL:
            try:
                resp = requests.get(GOOGLE_SCRIPT_URL, timeout=4)
                if resp.status_code == 200:
                    sheet_json = resp.json()
                    for k in ["breakfast", "lunch", "dinner", "task1", "task2"]:
                        if k in sheet_json:
                            val = str(sheet_json[k]).strip()
                            data[k] = val if val else "—"
            except Exception as e:
                print(f"[WARN] Sheet fetch error: {e}")

        # 2. Query Params
        try:
            rssi = int(request.args.get('rssi', -50))
        except Exception:
            rssi = -50

        batt_str = str(request.args.get('batt', '500d+'))
        try:
            batt_pct = int(request.args.get('pct', 95))
        except Exception:
            batt_pct = 95

        # 3. Timezone-Aware IST Date & Switchover Logic
        now_ist = datetime.now(IST)
        if now_ist.hour > 20 or (now_ist.hour == 20 and now_ist.minute >= 30):
            display_date = now_ist + timedelta(days=1)
        else:
            display_date = now_ist

        base_date = display_date.strftime("%a, %d %b %Y").upper()

        if batt_pct <= 20 and batt_str != "CHG":
            live_date_text = f"{base_date} • CHG REQ"
            date_font_size = 12
        else:
            live_date_text = base_date
            date_font_size = 13

        img = Image.new("L", (PANEL_WIDTH, PANEL_HEIGHT), 255)
        draw = ImageDraw.Draw(img)

        font_logo = safe_font(FONT_ENGLISH_PATH, 18)
        font_date = safe_font(FONT_ENGLISH_PATH, date_font_size)
        font_badge = safe_font(FONT_ENGLISH_PATH, 13)
        font_section = safe_font(FONT_ENGLISH_PATH, 15)

        # Header Bar
        draw.rectangle([0, 0, PANEL_WIDTH - 1, 38], fill=0)
        draw.rectangle([0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1], outline=0, width=2)
        draw.text((10, 9), "MealSync", font=font_logo, fill=255)

        # Wi-Fi RSSI Bars (Byte-aligned X = 96, Y = 13)
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 96, 13
        draw.rectangle([wifiX + 2, wifiY + 10, wifiX + 4, wifiY + 14], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 7, wifiY + 6,  wifiX + 9, wifiY + 14], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 12, wifiY + 2, wifiX + 14, wifiY + 14], fill=255 if signal_bars >= 3 else 0)

        # Centered Date
        date_w = get_text_width(font_date, live_date_text)
        date_center_x = (PANEL_WIDTH - date_w) // 2
        date_y = 12 if date_font_size == 12 else 11
        draw.text((date_center_x, date_y), live_date_text, font=font_date, fill=255)

        # Battery Icon & Adjacent Badge
        batX, batY = 362, 12
        draw.rectangle([batX, batY, batX + 24, batY + 14], outline=255, width=1)
        draw.rectangle([batX + 24, batY + 3, batX + 26, batY + 11], fill=255)

        if batt_str == "CHG":
            draw.polygon([
                (batX + 12, batY + 2), (batX + 7, batY + 7), 
                (batX + 11, batY + 7), (batX + 10, batY + 12), 
                (batX + 17, batY + 6), (batX + 13, batY + 6)
            ], fill=255)
        else:
            fill_w = max(0, min(20, int((batt_pct / 100.0) * 20)))
            if fill_w > 0:
                draw.rectangle([batX + 2, batY + 2, batX + 2 + fill_w, batY + 12], fill=255)

        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 5, 11), batt_str, font=font_badge, fill=255)

        # Sidebar & Sections
        sidebar_w = 118
        draw.rectangle([0, 38, sidebar_w, PANEL_HEIGHT - 1], fill=0)
        draw.text((10, 52), "BREAKFAST", font=font_section, fill=255)
        draw.text((10, 112), "LUNCH", font=font_section, fill=255)
        draw.text((10, 175), "DINNER", font=font_section, fill=255)
        draw.text((10, 245), "TASKS", font=font_section, fill=255)

        for y_div in [98, 160, 228]:
            draw.line([(0, y_div), (sidebar_w, y_div)], fill=255, width=2)
            draw.line([(sidebar_w, y_div), (PANEL_WIDTH, y_div)], fill=0, width=2)

        # Meal Texts
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_font_size=18, min_font_size=13, max_lines=2, fill_color=0)

        # Tasks Checkboxes
        draw.rectangle([128, 243, 142, 257], outline=0, width=2)
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_font_size=17, min_font_size=14, max_lines=1, fill_color=0)

        draw.rectangle([264, 243, 278, 257], outline=0, width=2)
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_font_size=17, min_font_size=14, max_lines=1, fill_color=0)

        img_1bit = img.point(lambda p: 255 if p > 160 else 0, mode="1")

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
