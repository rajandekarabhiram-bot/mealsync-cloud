import os
import io
import traceback
import requests
from datetime import datetime
from flask import Flask, request, Response, send_file
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)

# ============================================================================
# 1. CONFIGURATION
# ============================================================================
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

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
# 2. SAFE FONT LOADER (Never crashes if TTF files are missing)
# ============================================================================
def get_font(path, size):
    if path and os.path.exists(path):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            pass
    # Linux system fallbacks on Render
    for sys_font in ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "DejaVuSans-Bold.ttf"]:
        if os.path.exists(sys_font):
            try:
                return ImageFont.truetype(sys_font, size)
            except Exception:
                pass
    return ImageFont.load_default()

# ============================================================================
# 3. MASTER ENDPOINT: / & /display.bmp
# ============================================================================
@app.route('/', methods=['GET', 'HEAD'])
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def display_endpoint():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        # 1. Fetch Google Sheet Data safely
        data = fallback_data.copy()
        if GOOGLE_SCRIPT_URL:
            try:
                r = requests.get(GOOGLE_SCRIPT_URL, timeout=4)
                if r.status_code == 200:
                    j = r.json()
                    for k in data.keys():
                        if k in j and j[k]:
                            data[k] = str(j[k])
            except Exception as e:
                print(f"[WARN] Sheet fetch error: {e}")

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

        # 3. Create 400x300 Direct 1-Bit Monochrome Canvas
        img = Image.new("1", (PANEL_WIDTH, PANEL_HEIGHT), 1)  # 1 = White
        draw = ImageDraw.Draw(img)

        # Load safe fonts
        f_title = get_font("ProFont.ttf", 16)
        f_date = get_font("ProFont.ttf", 13)
        f_badge = get_font("ProFont.ttf", 11)
        f_sec = get_font("ProFont.ttf", 13)
        f_body = get_font("Rubik-Bold.ttf", 14)
        f_task = get_font("Rubik-Bold.ttf", 12)

        # 4. Draw Header Bar (Black rectangle at top)
        draw.rectangle([0, 0, PANEL_WIDTH, 34], fill=0)
        draw.text((10, 8), "MealSync", font=f_title, fill=1)
        draw.text((105, 10), live_date, font=f_date, fill=1)

        # Wi-Fi RSSI Bars
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wx, wy = 90, 14
        draw.rectangle([wx, wy + 6, wx + 2, wy + 10], fill=1 if signal_bars >= 1 else 0)
        draw.rectangle([wx + 4, wy + 3, wx + 6, wy + 10], fill=1 if signal_bars >= 2 else 0)
        draw.rectangle([wx + 8, wy, wx + 10, wy + 10], fill=1 if signal_bars >= 3 else 0)

        # Battery Label & Icon
        draw.text((310, 10), batt_str, font=f_badge, fill=1)
        bx, by = 358, 10
        draw.rectangle([bx, by, bx + 28, by + 13], outline=1, width=1)
        draw.rectangle([bx + 28, by + 3, bx + 30, by + 10], fill=1)

        if batt_str == "CHG":
            draw.line([(bx + 14, by + 2), (bx + 8, by + 7), (bx + 16, by + 7), (bx + 12, by + 12)], fill=1, width=1)
        else:
            fill_w = max(0, min(24, int((batt_pct / 100.0) * 24)))
            if fill_w > 0:
                draw.rectangle([bx + 2, by + 2, bx + 2 + fill_w, by + 11], fill=1)

        # 5. Sidebar (Left black column)
        draw.rectangle([0, 34, 115, PANEL_HEIGHT], fill=0)
        draw.text((10, 48), "BREAKFAST", font=f_sec, fill=1)
        draw.text((10, 108), "LUNCH", font=f_sec, fill=1)
        draw.text((10, 168), "DINNER", font=f_sec, fill=1)
        draw.text((10, 238), "TASKS", font=f_sec, fill=1)

        # Dividers
        for y_div in [98, 158, 228]:
            draw.line([(0, y_div), (115, y_div)], fill=1, width=2)
            draw.line([(115, y_div), (PANEL_WIDTH, y_div)], fill=0, width=2)

        # 6. Meals Content
        draw.text((125, 48), data["breakfast"][:30], font=f_body, fill=0)
        draw.text((125, 108), data["lunch"][:30], font=f_body, fill=0)
        draw.text((125, 168), data["dinner"][:30], font=f_body, fill=0)

        # Tasks Footer
        draw.rectangle([125, 242, 137, 254], outline=0, width=1)
        draw.text((144, 240), data["task1"][:16], font=f_task, fill=0)

        draw.rectangle([260, 242, 272, 254], outline=0, width=1)
        draw.text((279, 240), data["task2"][:16], font=f_task, fill=0)

        # 7. Deliver Raw 15,000-byte stream if requested by ESP32
        user_agent = request.headers.get("User-Agent", "")
        if "ESP32" in user_agent:
            byte_arr = bytearray(PANEL_WIDTH * PANEL_HEIGHT // 8)
            pixels = img.load()
            idx = 0
            for y in range(PANEL_HEIGHT):
                for x in range(0, PANEL_WIDTH, 8):
                    byte_val = 0
                    for b in range(8):
                        if (x + b) < PANEL_WIDTH:
                            # 1-bit EPD convention: Black pixel = 0, White pixel = 1
                            if pixels[x + b, y] != 0:
                                byte_val |= (1 << (7 - b))
                    byte_arr[idx] = byte_val
                    idx += 1
            return Response(bytes(byte_arr), mimetype='application/octet-stream')

        # Deliver standard BMP for browser testing
        buf = io.BytesIO()
        img.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        print("[CRITICAL EXCEPTION IN APP.PY]")
        traceback.print_exc()
        return f"Internal Server Error: {err}\n\n{traceback.format_exc()}", 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
