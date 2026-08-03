import os
import requests
import traceback
import re
from flask import Flask, send_file, request
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
from datetime import datetime

app = Flask(__name__)

# Active Google Apps Script Web App URL
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------
# LOCKED FONT PATH DEFINITIONS
# ---------------------------------------------------------
# 1. Header & UI Label Font: Strictly ProFont
FONT_HEADER_PATH = os.path.join(BASE_DIR, "ProFont.ttf")

# 2. English Menu Content Font: Strictly Rubik-Bold
FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "Rubik-Bold.ttf")
if not os.path.exists(FONT_ENGLISH_PATH):
    FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")

# 3. Marathi Menu Content Font: Mukta-Bold
FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Bold.ttf")
if not os.path.exists(FONT_MARATHI_PATH):
    FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Regular.ttf")

# Safe Filter Selection for Pillow Version Compatibility
try:
    LANCZOS_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    try:
        LANCZOS_FILTER = Image.LANCZOS
    except AttributeError:
        LANCZOS_FILTER = Image.BICUBIC


def has_devanagari(text):
    """Detects if a string contains any Devanagari (Marathi/Hindi) script characters."""
    return bool(re.search(r'[\u0900-\u097F]', str(text)))


def get_wrapped_lines(text, font, max_width):
    """Calculates wrapped lines for a given text, font, and maximum pixel width."""
    words = str(text).strip().split(" ")
    lines = []
    current_line = ""
    for word in words:
        test_line = current_line + " " + word if current_line else word
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except:
            w = len(test_line) * (font.size * 0.5)
            
        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return lines


def draw_autofit_text(draw, text, x, y, max_width, max_lines, eng_font_path, marathi_font_path, max_size=46, min_size=28, fill_color=0):
    """
    Dynamically scales font size to fit text within assigned width and line budget.
    Uses Mukta-Bold for Devanagari text and Rubik-Bold for English text.
    """
    text_str = str(text).strip()
    if not text_str:
        return

    is_marathi = has_devanagari(text_str)
    font_path = marathi_font_path if is_marathi else eng_font_path

    selected_font = None
    selected_lines = []
    current_size = max_size

    while current_size >= min_size:
        try:
            if font_path and os.path.exists(font_path):
                test_font = ImageFont.truetype(font_path, current_size)
            else:
                test_font = ImageFont.load_default()
        except:
            test_font = ImageFont.load_default()

        lines = get_wrapped_lines(text_str, test_font, max_width)
        if len(lines) <= max_lines:
            selected_font = test_font
            selected_lines = lines
            break
        current_size -= 2

    if selected_font is None:
        try:
            if font_path and os.path.exists(font_path):
                selected_font = ImageFont.truetype(font_path, min_size)
            else:
                selected_font = ImageFont.load_default()
        except:
            selected_font = ImageFont.load_default()
        selected_lines = get_wrapped_lines(text_str, selected_font, max_width)[:max_lines]

    line_height = int(current_size * 1.20)
    current_y = y

    for line in selected_lines[:max_lines]:
        draw.text((x, current_y), line, font=selected_font, fill=fill_color)
        current_y += line_height


@app.route("/", methods=["GET", "HEAD"])
def render_dashboard():
    if request.method == "HEAD":
        return "OK", 200

    try:
        # 1. Fetch Google Sheet Data via Google Apps Script
        try:
            response = requests.get(GOOGLE_SCRIPT_URL, timeout=20)
            data = response.json()
        except Exception:
            data = {
                "breakfast": "पुरणपोळी, कटाची आमटी, भजी", 
                "lunch": "वरण भात, चपाती, वांग्याची भाजी, कोशिंबीर, पापड", 
                "dinner": "मसाला खिचडी, कढी, पापड",
                "task1": "दूध आणा", 
                "task2": "भाजी धुवा"
            }

        live_date = data.get("date", datetime.now().strftime("%a, %d %b %Y").upper())

        # 2. Canvas Setup (800x600 Supersampled)
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # 3. Load Header Typography (ProFont.ttf)
        try:
            eng_logo = ImageFont.truetype(FONT_HEADER_PATH, 54) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
            eng_date = ImageFont.truetype(FONT_HEADER_PATH, 38) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
            eng_section = ImageFont.truetype(FONT_HEADER_PATH, 32) if os.path.exists(FONT_HEADER_PATH) else ImageFont.load_default()
        except:
            eng_logo = eng_date = eng_section = ImageFont.load_default()

        # Canvas Outer Frame
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # ---------------------------------------------------------
        # APP HEADER BAR (Y: 0 to 76) — ProFont
        # ---------------------------------------------------------
        draw.rectangle([0, 0, 800, 76], fill=0)
        draw.text((24, 8), "MealSync", font=eng_logo, fill=255)
        draw.text((320, 16), live_date, font=eng_date, fill=255)

        # ---------------------------------------------------------
        # DYNAMIC WIFI SIGNAL INDICATOR (Reads RSSI Query Parameter)
        # ---------------------------------------------------------
        try:
            rssi = int(request.args.get('rssi', -50))
        except (ValueError, TypeError):
            rssi = -50

        # Calculate active bars based on RSSI strength (1 to 3 bars)
        if rssi >= -67:
            signal_bars = 3  # Strong signal
        elif rssi >= -80:
            signal_bars = 2  # Medium signal
        else:
            signal_bars = 1  # Weak signal

        wifiX, wifiY = 250, 26
        # Bar 1 (Shortest - Weak Signal)
        draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255 if signal_bars >= 1 else 40)
        # Bar 2 (Medium Signal)
        draw.rectangle([wifiX + 8, wifiY + 6, wifiX + 12, wifiY + 20], fill=255 if signal_bars >= 2 else 40)
        # Bar 3 (Tallest - Strong Signal)
        draw.rectangle([wifiX + 16, wifiY, wifiX + 20, wifiY + 20], fill=255 if signal_bars >= 3 else 40)

        # Battery Status Indicator Icon
        batX, batY = 730, 22
        draw.rectangle([batX, batY, batX + 46, batY + 24], outline=255, fill=0)
        draw.rectangle([batX + 46, batY + 6, batX + 50, batY + 18], fill=255)
        draw.rectangle([batX + 4, batY + 4, batX + 38, batY + 20], fill=255)

        # ---------------------------------------------------------
        # UNIFIED BLACK SIDEBAR COLUMN (X: 0 to 230, Y: 76 to 600)
        # ---------------------------------------------------------
        sidebar_w = 230
        draw.rectangle([0, 76, sidebar_w, 600], fill=0)

        # Section Labels inside Black Sidebar (White ProFont Text)
        draw.text((24, 110), "BREAKFAST", font=eng_section, fill=255)
        draw.text((24, 230), "LUNCH", font=eng_section, fill=255)
        draw.text((24, 350), "DINNER", font=eng_section, fill=255)
        draw.text((24, 480), "TASKS", font=eng_section, fill=255)

        # ---------------------------------------------------------
        # HORIZONTAL ROW DIVIDERS Across Display (3px thickness)
        # ---------------------------------------------------------
        divider_w = 3

        # Row 1 Divider (Breakfast / Lunch)
        draw.line([(0, 195), (sidebar_w, 195)], fill=255, width=divider_w)
        draw.line([(sidebar_w, 195), (800, 195)], fill=0, width=divider_w)

        # Row 2 Divider (Lunch / Dinner)
        draw.line([(0, 315), (sidebar_w, 315)], fill=255, width=divider_w)
        draw.line([(sidebar_w, 315), (800, 315)], fill=0, width=divider_w)

        # Row 3 Divider (Dinner / Kitchen Tasks)
        draw.line([(0, 450), (sidebar_w, 450)], fill=255, width=divider_w)
        draw.line([(sidebar_w, 450), (800, 450)], fill=0, width=divider_w)

        # ---------------------------------------------------------
        # RIGHT MAIN CONTENT AREA (X: 246 to 776, Width = 530)
        # Max Font Size = 46px
        # ---------------------------------------------------------
        right_x = sidebar_w + 16  # 246
        content_w = 800 - right_x - 24  # 530

        # --- SLOT 1: BREAKFAST (Y: 90 to 185) ---
        draw_autofit_text(
            draw, str(data.get("breakfast", "")), 
            x=right_x, y=90, max_width=content_w, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=46, min_size=28
        )

        # --- SLOT 2: LUNCH (Y: 210 to 305) ---
        draw_autofit_text(
            draw, str(data.get("lunch", "")), 
            x=right_x, y=210, max_width=content_w, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=46, min_size=28
        )

        # --- SLOT 3: DINNER (Y: 330 to 435) ---
        draw_autofit_text(
            draw, str(data.get("dinner", "")), 
            x=right_x, y=330, max_width=content_w, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=46, min_size=28
        )

        # --- SLOT 4: KITCHEN TASKS ---
        col1_x = right_x
        col2_x = right_x + 270
        task_col_w = 250

        t1_str = "• " + str(data.get("task1", ""))
        t2_str = "• " + str(data.get("task2", ""))

        draw_autofit_text(
            draw, t1_str, 
            x=col1_x, y=480, max_width=task_col_w, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=36, min_size=24
        )
        draw_autofit_text(
            draw, t2_str, 
            x=col2_x, y=480, max_width=task_col_w, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=36, min_size=24
        )

        # ---------------------------------------------------------
        # DOWNSCALE & MONOCHROME THRESHOLDING
        # ---------------------------------------------------------
        img_downscaled = img.resize((400, 300), LANCZOS_FILTER)
        img_inverted_grayscale = ImageOps.invert(img_downscaled)
        
        threshold = 150
        img_thresholded = img_inverted_grayscale.point(lambda p: 255 if p > threshold else 0)
        final_img = img_thresholded.convert("1", dither=Image.NONE)

        user_agent = request.headers.get('User-Agent', '')
        
        if "ESP32HTTPClient" not in user_agent:
            buf = io.BytesIO()
            final_img.save(buf, format="PNG")
            buf.seek(0)
            return send_file(buf, mimetype="image/png")
        else:
            raw_pixels = final_img.tobytes()
            return send_file(io.BytesIO(raw_pixels), mimetype="application/octet-stream")

    except Exception as err:
        print("CRITICAL SERVER ERROR:")
        traceback.print_exc()
        return f"Server Error: {str(err)}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
