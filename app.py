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
# FONT CONFIGURATION
# English: ProFont / Monospace TTF
# Marathi: Mukta Regular/Medium (Clean, non-smudged rendering)
# ---------------------------------------------------------
FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "ProFont.ttf")
if not os.path.exists(FONT_ENGLISH_PATH):
    FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "DejaVuSansMono-Bold.ttf")

FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Regular.ttf")
if not os.path.exists(FONT_MARATHI_PATH):
    FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Bold.ttf")

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


def draw_smart_wrapped_text(draw, text, x, y, max_width, eng_font, marathi_font, fill_color=0, line_height=44):
    """
    Auto-detects language:
    - Renders English text with the English Monospace Font
    - Renders Marathi text with Mukta Font (without smudged outlines)
    """
    text_str = str(text).strip()
    if not text_str:
        return y

    # Select appropriate font based on language detection
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
        
    current_y = y
    for line in lines:
        draw.text((x, current_y), line, font=font, fill=fill_color)
        current_y += line_height
    return current_y


def draw_section_pill(draw, text, x, y, font):
    """Draws compact dark tag banners using the English ProFont."""
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except:
        tw = len(text) * 12
        th = 18
    draw.rectangle([x, y, x + tw + 18, y + th + 8], fill=0)
    draw.text((x + 9, y + 4), text, font=font, fill=255)
    return y + th + 8


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

        # 2. Canvas Setup (800x600 Supersampled)
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # 3. Load Fonts
        try:
            eng_logo = ImageFont.truetype(FONT_ENGLISH_PATH, 34)
            eng_header = ImageFont.truetype(FONT_ENGLISH_PATH, 22)
            eng_pill = ImageFont.truetype(FONT_ENGLISH_PATH, 18)
            eng_body = ImageFont.truetype(FONT_ENGLISH_PATH, 26)
        except:
            eng_logo = eng_header = eng_pill = eng_body = ImageFont.load_default()

        try:
            marathi_meal = ImageFont.truetype(FONT_MARATHI_PATH, 38)
            marathi_sub = ImageFont.truetype(FONT_MARATHI_PATH, 32)
        except:
            marathi_meal = marathi_sub = ImageFont.load_default()

        # Canvas Outer Frame
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # ---------------------------------------------------------
        # APP HEADER BAR (ProFont for Logo & Date)
        # ---------------------------------------------------------
        draw.rectangle([0, 0, 800, 64], fill=0)
        draw.text((24, 14), "MealSync", font=eng_logo, fill=255)
        draw.text((280, 20), live_date, font=eng_header, fill=255)

        # WiFi & Battery Indicators
        wifiX, wifiY = 220, 24
        draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 8, wifiY + 6, wifiX + 12, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 16, wifiY, wifiX + 20, wifiY + 20], fill=255)

        batX, batY = 730, 20
        draw.rectangle([batX, batY, batX + 46, batY + 24], outline=255, fill=0)
        draw.rectangle([batX + 46, batY + 6, batX + 50, batY + 18], fill=255)
        draw.rectangle([batX + 4, batY + 4, batX + 38, batY + 20], fill=255)

        # ---------------------------------------------------------
        # MEAL SECTIONS (Smart Language Detection & Clean Mukta Font)
        # ---------------------------------------------------------
        full_width = 752
        current_y = 76

        # BREAKFAST
        pill_end = draw_section_pill(draw, "BREAKFAST", 24, current_y, eng_pill)
        current_y = draw_smart_wrapped_text(draw, str(data.get("breakfast", "")), 24, pill_end + 6, full_width, eng_body, marathi_meal, line_height=44)
        current_y += 10
        draw.line([(0, current_y), (800, current_y)], fill=0, width=2)

        # LUNCH
        current_y += 10
        pill_end = draw_section_pill(draw, "LUNCH", 24, current_y, eng_pill)
        current_y = draw_smart_wrapped_text(draw, str(data.get("lunch", "")), 24, pill_end + 6, full_width, eng_body, marathi_meal, line_height=44)
        current_y += 10
        draw.line([(0, current_y), (800, current_y)], fill=0, width=2)

        # DINNER
        current_y += 10
        pill_end = draw_section_pill(draw, "DINNER", 24, current_y, eng_pill)
        current_y = draw_smart_wrapped_text(draw, str(data.get("dinner", "")), 24, pill_end + 6, full_width, eng_body, marathi_meal, line_height=44)

        # ---------------------------------------------------------
        # DYNAMIC FOOTER: KITCHEN TASKS
        # ---------------------------------------------------------
        footer_top = max(current_y + 16, 470)
        draw.line([(0, footer_top), (800, footer_top)], fill=0, width=2)

        # Footer Header Banner
        draw.rectangle([24, footer_top + 10, 776, footer_top + 42], fill=0)
        draw.text((36, footer_top + 14), "KITCHEN TASKS", font=eng_header, fill=255)

        # Two spacious columns for tasks
        col_y = footer_top + 52
        t1_str = "• " + str(data.get("task1", ""))
        t2_str = "• " + str(data.get("task2", ""))

        draw_smart_wrapped_text(draw, t1_str, 24, col_y, 360, eng_body, marathi_sub, line_height=36)
        draw_smart_wrapped_text(draw, t2_str, 410, col_y, 360, eng_body, marathi_sub, line_height=36)

        # ---------------------------------------------------------
        # DOWNSCALE & MONOCHROME THRESHOLDING
        # ---------------------------------------------------------
        img_downscaled = img.resize((400, 300), LANCZOS_FILTER)
        img_inverted_grayscale = ImageOps.invert(img_downscaled)
        
        # Clean threshold for sharp 1-bit text without bleeding
        threshold = 155
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
