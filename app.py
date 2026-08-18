import os
import io
import requests
from datetime import datetime
from flask import Flask, request, Response, send_file
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)

# ============================================================================
# 1. CONFIGURATION
# ============================================================================
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Mukta-Bold.ttf"
FONT_HEADER_PATH  = "ProFont.ttf"

PANEL_WIDTH = 400
PANEL_HEIGHT = 300

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
# 2. TEXT AUTO-FITTING HELPER
# ============================================================================
def is_ascii(s):
    return all(ord(c) < 128 for c in s)

def get_wrapped_lines(text, font, max_width):
    words = text.split()
    if not words:
        return []
    lines, current_line = [], []
    for word in words:
        test_line = " ".join(current_line + [word])
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except:
            w, _ = font.getsize(test_line)
        if w <= max_width:
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
    return lines

def draw_autofit_text(draw, text_str, x, y, max_width, max_height, max_font_size=42, min_font_size=24, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH
    selected_font = None
    selected_lines = []
    for size in range(max_font_size, min_font_size - 1, -2):
        try:
            test_font = ImageFont.truetype(font_file, size) if os.path.exists(font_file) else ImageFont.load_default()
        except:
            test_font = ImageFont.load_default()
        lines = get_wrapped_lines(text_str, test_font, max_width)
        line_h = int(size * 1.20)
        total_h = len(lines) * line_h
        if len(lines) <= max_lines and total_h <= max_height:
            selected_font = test_font
            selected_lines = lines
            break
    if not selected_font:
        try:
            selected_font = ImageFont.truetype(font_file, min_font_size) if os.path.exists(font_file) else ImageFont.load_default()
        except:
            selected_font = ImageFont.load_default()
        selected_lines = get_wrapped_lines(text_str, selected_font, max_width)[:max_lines]

    line_h = int(selected_font.size * 1.20) if hasattr(selected_font, 'size') else 20
    curr_y = y
    for line in selected_lines:
        draw.text((x, curr_y), line, font=selected_font, fill=fill_color)
        curr_y += line_h

# ============================================================================
# 3. DASHBOARD IMAGE RENDERER (`/display.bmp` & `/`)
# ============================================================================
@app.route('/', methods=['GET', 'HEAD'])
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_dashboard():
    if request.method == 'HEAD':
        return "OK", 200

    data = fallback_data.copy()
    try:
        if GOOGLE_SCRIPT_URL:
            resp = requests.get(GOOGLE_SCRIPT_URL, timeout=12)
            if resp.status_code == 200:
                sheet_json = resp.json()
                for k in ["breakfast", "lunch", "dinner", "task1", "task2", "prep", "waste"]:
                    if k in sheet_json and sheet_json[k]:
                        data[k] = sheet_json[k]
    except Exception as e:
        print(f"[WARN] Using fallback data: {e}")

    live_date = datetime.now().strftime("%a, %d %b %Y").upper()

    # 800x600 Supersampled Canvas
    img = Image.new("L", (800, 600), 255)
    draw = ImageDraw.Draw(img)

    try:
        eng_logo = ImageFont.truetype(FONT_HEADER_PATH, 50) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
        eng_date = ImageFont.truetype(FONT_HEADER_PATH, 34) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
        eng_section = ImageFont.truetype(FONT_HEADER_PATH, 32) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
        font_badge = ImageFont.truetype(FONT_HEADER_PATH, 24) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
    except:
        eng_logo = eng_date = eng_section = font_badge = ImageFont.load_default()

    # Borders & Header Bar
    draw.rectangle([0, 0, 799, 599], outline=0, width=4)
    draw.rectangle([0, 0, 800, 76], fill=0)
    draw.text((20, 10), "MealSync", font=eng_logo, fill=255)
    draw.text((250, 18), live_date, font=eng_date, fill=255)

    # Dynamic Wi-Fi RSSI Signal Bars
    try:
        rssi = int(request.args.get('rssi', -50))
    except (ValueError, TypeError):
        rssi = -50
    signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
    wifiX, wifiY = 205, 26
    draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255 if signal_bars >= 1 else 40)
    draw.rectangle([wifiX + 7, wifiY + 6, wifiX + 11, wifiY + 20], fill=255 if signal_bars >= 2 else 40)
    draw.rectangle([wifiX + 14, wifiY, wifiX + 18, wifiY + 20], fill=255 if signal_bars >= 3 else 40)

    # Dynamic Battery Icon & Remaining Label
    batt_str = request.args.get('batt', '500d+')
    try:
        batt_pct = int(request.args.get('pct', 95))
    except (ValueError, TypeError):
        batt_pct = 95

    draw.text((630, 24), batt_str, font=font_badge, fill=255)

    batX, batY = 724, 24
    draw.rectangle([batX, batY, batX + 44, batY + 24], outline=255, width=3)
    draw.rectangle([batX + 44, batY + 6, batX + 49, batY + 18], fill=255)

    if batt_str == "CHG":
        draw.polygon([(batX + 22, batY + 3), (batX + 13, batY + 13), (batX + 21, batY + 13), (batX + 18, batY + 21), (batX + 31, batY + 10), (batX + 23, batY + 10)], fill=255)
    else:
        fill_w = int((batt_pct / 100.0) * 36)
        if fill_w > 0:
            draw.rectangle([batX + 4, batY + 4, batX + 4 + fill_w, batY + 20], fill=255)

    # Sidebar & Sections
    draw.rectangle([0, 72, 230, 600], fill=0)
    draw.text((24, 105), "BREAKFAST", font=eng_section, fill=255)
    draw.text((24, 225), "LUNCH", font=eng_section, fill=255)
    draw.text((24, 350), "DINNER", font=eng_section, fill=255)
    draw.text((24, 490), "TASKS", font=eng_section, fill=255)

    for y_div in [195, 315, 450]:
        draw.line([(0, y_div), (230, y_div)], fill=255, width=3)
        draw.line([(230, y_div), (800, y_div)], fill=0, width=3)

    draw_autofit_text(draw, data["breakfast"], 250, 85, 520, 95, max_font_size=42, min_font_size=28, max_lines=2, fill_color=0)
    draw_autofit_text(draw, data["lunch"], 250, 205, 520, 95, max_font_size=42, min_font_size=28, max_lines=2, fill_color=0)
    draw_autofit_text(draw, data["dinner"], 250, 330, 520, 95, max_font_size=42, min_font_size=28, max_lines=2, fill_color=0)

    draw.rectangle([250, 485, 270, 505], outline=0, width=2)
    draw_autofit_text(draw, data["task1"], 285, 478, 230, 48, max_font_size=32, min_font_size=22, max_lines=1, fill_color=0)

    draw.rectangle([530, 485, 550, 505], outline=0, width=2)
    draw_autofit_text(draw, data["task2"], 565, 478, 210, 48, max_font_size=32, min_font_size=22, max_lines=1, fill_color=0)

    # Downscale and Format Stream
    img_downscaled = img.resize((PANEL_WIDTH, PANEL_HEIGHT), Image.Resampling.LANCZOS)
    img_1bit = img_downscaled.point(lambda p: 255 if p > 140 else 0, mode="1")

    if "ESP32" in request.headers.get("User-Agent", ""):
        img_epd = ImageOps.invert(img_downscaled.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
        return Response(img_epd.tobytes(), mimetype='application/octet-stream')

    buf = io.BytesIO()
    img_1bit.save(buf, format='BMP')
    buf.seek(0)
    return send_file(buf, mimetype='image/bmp')

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
