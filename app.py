import os
import io
import traceback
import requests
from datetime import datetime
from flask import Flask, request, Response, send_file
from PIL import Image, ImageDraw, ImageFont

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

fallback_data = {
    "breakfast": "Puran Poli & Katachi Amti",
    "lunch": "Varan Bhaat, Chapati & Bhaji",
    "dinner": "Masala Khichdi & Kadhi",
    "task1": "Buy Milk",
    "task2": "Wash Veggies",
    "prep": "Soak Cashews",
    "waste": "Opened Milk, Spinach"
}

# ============================================================================
# 2. BULLETPROOF FONT & TEXT HELPERS
# ============================================================================
def safe_load_font(font_path, size):
    if font_path and os.path.exists(font_path):
        try:
            return ImageFont.truetype(font_path, size)
        except Exception:
            pass
    try:
        # Standard cross-platform Linux/Render fallback
        return ImageFont.truetype("DejaVuSans-Bold.ttf", size)
    except Exception:
        return ImageFont.load_default()

def get_text_width(font, text_str):
    try:
        bbox = font.getbbox(text_str)
        return bbox[2] - bbox[0]
    except Exception:
        try:
            w, _ = font.getsize(text_str)
            return w
        except Exception:
            return len(text_str) * 8

def draw_wrapped_text(draw, text_str, x, y, max_width, font, line_height, max_lines=2, fill=0):
    words = str(text_str).strip().split()
    if not words:
        return
    lines = []
    current_line = []
    
    for word in words:
        test_line = " ".join(current_line + [word])
        if get_text_width(font, test_line) <= max_width:
            current_line.append(word)
        else:
            if current_line:
                lines.append(" ".join(current_line))
                current_line = [word]
            else:
                lines.append(word)
                current_line = []
    if current_line:
        lines.append(" ".join(current_line))
        
    for i, line in enumerate(lines[:max_lines]):
        draw.text((x, y + (i * line_height)), line, font=font, fill=fill)

# ============================================================================
# 3. MASTER ENDPOINT: / & /display.bmp
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
                resp = requests.get(GOOGLE_SCRIPT_URL, timeout=5)
                if resp.status_code == 200:
                    sheet_json = resp.json()
                    for k in ["breakfast", "lunch", "dinner", "task1", "task2", "prep", "waste"]:
                        if k in sheet_json and sheet_json[k]:
                            data[k] = str(sheet_json[k])
            except Exception as e:
                print(f"[WARN] Google Sheet skipped, using fallback: {e}")

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

        # 3. Create 800x600 Supersampled Canvas
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # Fonts
        font_logo = safe_load_font(FONT_HEADER_PATH, 46)
        font_date = safe_load_font(FONT_HEADER_PATH, 32)
        font_section = safe_load_font(FONT_HEADER_PATH, 30)
        font_badge = safe_load_font(FONT_HEADER_PATH, 24)
        font_dish = safe_load_font(FONT_ENGLISH_PATH, 36)
        font_task = safe_load_font(FONT_ENGLISH_PATH, 26)

        # 4. Header Bar
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)
        draw.rectangle([0, 0, 800, 76], fill=0)
        draw.text((20, 14), "MealSync", font=font_logo, fill=255)
        draw.text((230, 20), live_date, font=font_date, fill=255)

        # Dynamic Wi-Fi RSSI Bars
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 190, 26
        draw.rectangle([wifiX, wifiY + 14, wifiX + 4, wifiY + 22], fill=255 if signal_bars >= 1 else 50)
        draw.rectangle([wifiX + 7, wifiY + 7, wifiX + 11, wifiY + 22], fill=255 if signal_bars >= 2 else 50)
        draw.rectangle([wifiX + 14, wifiY, wifiX + 18, wifiY + 22], fill=255 if signal_bars >= 3 else 50)

        # Dynamic Battery Icon & Badge
        draw.text((625, 24), batt_str, font=font_badge, fill=255)
        batX, batY = 724, 24
        draw.rectangle([batX, batY, batX + 44, batY + 24], outline=255, width=3)
        draw.rectangle([batX + 44, batY + 6, batX + 49, batY + 18], fill=255)

        if batt_str == "CHG":
            draw.polygon([(batX + 22, batY + 3), (batX + 13, batY + 13), (batX + 21, batY + 13), 
                          (batX + 18, batY + 21), (batX + 31, batY + 10), (batX + 23, batY + 10)], fill=255)
        else:
            fill_w = max(0, min(36, int((batt_pct / 100.0) * 36)))
            if fill_w > 0:
                draw.rectangle([batX + 4, batY + 4, batX + 4 + fill_w, batY + 20], fill=255)

        # 5. Sidebar & Divider Lines
        draw.rectangle([0, 72, 230, 600], fill=0)
        draw.text((24, 105), "BREAKFAST", font=font_section, fill=255)
        draw.text((24, 225), "LUNCH", font=font_section, fill=255)
        draw.text((24, 350), "DINNER", font=font_section, fill=255)
        draw.text((24, 490), "TASKS", font=font_section, fill=255)

        for y_div in [195, 315, 450]:
            draw.line([(0, y_div), (230, y_div)], fill=255, width=3)
            draw.line([(230, y_div), (800, y_div)], fill=0, width=3)

        # 6. Meals Content
        draw_wrapped_text(draw, data["breakfast"], 250, 90, 520, font_dish, line_height=42, max_lines=2, fill=0)
        draw_wrapped_text(draw, data["lunch"], 250, 210, 520, font_dish, line_height=42, max_lines=2, fill=0)
        draw_wrapped_text(draw, data["dinner"], 250, 335, 520, font_dish, line_height=42, max_lines=2, fill=0)

        # 7. Tasks Footer
        draw.rectangle([250, 485, 270, 505], outline=0, width=2)
        draw_wrapped_text(draw, data["task1"], 285, 482, 230, font_task, line_height=28, max_lines=1, fill=0)

        draw.rectangle([530, 485, 550, 505], outline=0, width=2)
        draw_wrapped_text(draw, data["task2"], 565, 482, 210, font_task, line_height=28, max_lines=1, fill=0)

        # 8. Downscale to 400x300 E-Paper Resolution
        img_downscaled = img.resize((PANEL_WIDTH, PANEL_HEIGHT), Image.Resampling.LANCZOS)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 140 else 0, mode="1")

        # 9. Deliver Raw 15,000-byte Octet Stream if requested by ESP32
        user_agent = request.headers.get("User-Agent", "")
        if "ESP32" in user_agent:
            byte_arr = bytearray(PANEL_WIDTH * PANEL_HEIGHT // 8)
            pixels = img_1bit.load()
            idx = 0
            for y in range(PANEL_HEIGHT):
                for x in range(0, PANEL_WIDTH, 8):
                    byte_val = 0
                    for b in range(8):
                        if (x + b) < PANEL_WIDTH:
                            # Monochrome e-Paper bit convention (Black=0, White=1)
                            if pixels[x + b, y] != 0:
                                byte_val |= (1 << (7 - b))
                    byte_arr[idx] = byte_val
                    idx += 1
            return Response(bytes(byte_arr), mimetype='application/octet-stream')

        # Deliver standard BMP for browser inspection
        buf = io.BytesIO()
        img_1bit.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        print("[CRITICAL ERROR IN APP.PY]")
        traceback.print_exc()
        return f"Render Error: {err}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
