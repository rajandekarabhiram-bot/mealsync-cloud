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
# LOCKED FONT PIPELINE
# ---------------------------------------------------------
FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "ProFont.ttf")
if not os.path.exists(FONT_ENGLISH_PATH):
    FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "DejaVuSansMono-Bold.ttf")

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


def is_ascii(text):
    """Detects if a string consists purely of English / ASCII characters."""
    return bool(re.match(r'^[\x00-\x7F]+$', str(text).strip()))


def draw_smart_wrapped_text(draw, text, x, y, max_width, eng_font, marathi_font, fill_color=0, line_height=44, max_lines=2):
    """Auto-detects language and renders uniform text within a fixed slot."""
    text_str = str(text).strip()
    if not text_str:
        return y

    font = eng_font if is_ascii(text_str) else marathi_font

    words = text_str.split(" ")
    lines = []
    current_line = ""
    
    for word in words:
        test_line = current_line + " " + word if current_line else word
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except:
            w = len(test_line) * 16  
            
        if w <= max_width:
            current_line = test_line
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
        
    # Cap output to 2 lines per slot to prevent layout overlap
    lines_to_draw = lines[:max_lines]
    
    current_y = y
    for line in lines_to_draw:
        draw.text((x, current_y), line, font=font, fill=fill_color)
        current_y += line_height
    return current_y


def draw_section_pill(draw, text, x, y, font):
    """Draws a padded dark header pill tag for section headers."""
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except:
        tw = len(text) * 16
        th = 24
    draw.rectangle([x, y, x + tw + 24, y + th + 12], fill=0)
    draw.text((x + 12, y + 6), text, font=font, fill=255)
    return y + th + 12


@app.route("/", methods=["GET", "HEAD"])
def render_dashboard():
    if request.method == "HEAD":
        return "OK", 200

    try:
        # 1. Fetch Google Sheet Data
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

        # 2. Canvas Setup (800x600 Supersampled for e-paper)
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # 3. Load Fonts with Uniform Scaling
        try:
            eng_logo = ImageFont.truetype(FONT_ENGLISH_PATH, 42)    # Title Logo
            eng_header = ImageFont.truetype(FONT_ENGLISH_PATH, 28)  # Date / Footer Title
            eng_pill = ImageFont.truetype(FONT_ENGLISH_PATH, 26)    # Section Header Pills
            eng_body = ImageFont.truetype(FONT_ENGLISH_PATH, 34)    # English Menu Items
        except:
            eng_logo = eng_header = eng_pill = eng_body = ImageFont.load_default()

        try:
            marathi_meal = ImageFont.truetype(FONT_MARATHI_PATH, 40) # Uniform Devanagari Meals
            marathi_sub = ImageFont.truetype(FONT_MARATHI_PATH, 34)  # Uniform Devanagari Tasks
        except:
            marathi_meal = marathi_sub = ImageFont.load_default()

        # Canvas Outer Frame
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # ---------------------------------------------------------
        # TOP APP HEADER BAR (Y: 0 to 70)
        # ---------------------------------------------------------
        draw.rectangle([0, 0, 800, 70], fill=0)
        draw.text((24, 12), "MealSync", font=eng_logo, fill=255)
        draw.text((310, 20), live_date, font=eng_header, fill=255)

        # WiFi & Battery Status Indicators
        wifiX, wifiY = 250, 26
        draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 8, wifiY + 6, wifiX + 12, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 16, wifiY, wifiX + 20, wifiY + 20], fill=255)

        batX, batY = 730, 22
        draw.rectangle([batX, batY, batX + 46, batY + 24], outline=255, fill=0)
        draw.rectangle([batX + 46, batY + 6, batX + 50, batY + 18], fill=255)
        draw.rectangle([batX + 4, batY + 4, batX + 38, batY + 20], fill=255)

        # ---------------------------------------------------------
        # FIXED 2-ROW VERTICAL GRID SLOTS (752px Width)
        # ---------------------------------------------------------
        full_width = 752

        # --- SLOT 1: BREAKFAST ---
        draw_section_pill(draw, "BREAKFAST", 24, 82, eng_pill)
        draw_smart_wrapped_text(draw, str(data.get("breakfast", "")), 24, 126, full_width, eng_body, marathi_meal, line_height=44, max_lines=2)
        # Segregation Line with balanced top & bottom margin
        draw.line([(0, 200), (800, 200)], fill=0, width=2)

        # --- SLOT 2: LUNCH ---
        draw_section_pill(draw, "LUNCH", 24, 212, eng_pill)
        draw_smart_wrapped_text(draw, str(data.get("lunch", "")), 24, 256, full_width, eng_body, marathi_meal, line_height=44, max_lines=2)
        # Segregation Line
        draw.line([(0, 330), (800, 330)], fill=0, width=2)

        # --- SLOT 3: DINNER ---
        draw_section_pill(draw, "DINNER", 24, 342, eng_pill)
        draw_smart_wrapped_text(draw, str(data.get("dinner", "")), 24, 386, full_width, eng_body, marathi_meal, line_height=44, max_lines=2)

        # ---------------------------------------------------------
        # FOOTER: KITCHEN TASKS (Fixed Slot: Y=460 to 595)
        # ---------------------------------------------------------
        draw.line([(0, 460), (800, 460)], fill=0, width=3)

        # Footer Header Banner
        draw.rectangle([24, 472, 776, 512], fill=0)
        draw.text((36, 477), "KITCHEN TASKS", font=eng_header, fill=255)

        # Two spacious columns for tasks
        col_y = 526
        t1_str = "• " + str(data.get("task1", ""))
        t2_str = "• " + str(data.get("task2", ""))

        draw_smart_wrapped_text(draw, t1_str, 24, col_y, 360, eng_body, marathi_sub, line_height=40, max_lines=1)
        draw_smart_wrapped_text(draw, t2_str, 410, col_y, 360, eng_body, marathi_sub, line_height=40, max_lines=1)

        # ---------------------------------------------------------
        # DOWNSCALE & MONOCHROME THRESHOLDING
        # ---------------------------------------------------------
        img_downscaled = img.resize((400, 300), LANCZOS_FILTER)
        img_inverted_grayscale = ImageOps.invert(img_downscaled)
        
        # Crisp monochrome threshold
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
