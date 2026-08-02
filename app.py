import os
import requests
import traceback
from flask import Flask, send_file, request
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
from datetime import datetime

app = Flask(__name__)

# Your active Google Apps Script Web App URL
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------
# FONT CONFIGURATION SEPARATION
# ---------------------------------------------------------
# 1. Marathi Font (Mukta for Devanagari Content)
FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Bold.ttf")

# 2. English UI Font (Crisp Sans-Serif / DejaVu / Standard TTF)
# If DejaVuSans-Bold.ttf is in your repo, it will be loaded; otherwise, fallback is handled safely.
FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")

# Safe Filter Selection for Pillow Version Compatibility
try:
    LANCZOS_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    try:
        LANCZOS_FILTER = Image.LANCZOS
    except AttributeError:
        LANCZOS_FILTER = Image.BICUBIC


def draw_wrapped_marathi_text(draw, text, x, y, max_width, font, fill_color=0, line_height=38):
    """Handles line wrapping and drawing specifically for Marathi (Devanagari) content."""
    words = str(text).split(" ")
    lines = []
    current_line = ""
    
    for word in words:
        test_line = current_line + " " + word if current_line else word
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except:
            w = len(test_line) * 14  
            
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


def draw_english_highlight_label(draw, text, x, y, font):
    """Draws solid black highlight box tightly around English section sub-headers (BREAKFAST, LUNCH, DINNER)."""
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except:
        tw = len(text) * 12
        th = 20
    draw.rectangle([x, y, x + tw + 12, y + th + 8], fill=0)
    draw.text((x + 6, y + 4), text, font=font, fill=255)
    return y + th + 8


@app.route("/", methods=["GET", "HEAD"])
def render_dashboard():
    if request.method == "HEAD":
        return "OK", 200

    try:
        # Fetch Google Sheet Data
        try:
            response = requests.get(GOOGLE_SCRIPT_URL, timeout=20)
            data = response.json()
        except Exception:
            data = {
                "breakfast": "पुरणपोळी", "lunch": "भाजी पोळी", "dinner": "खिचडी",
                "task1": "दूध आणा", "task2": "भाजी धुवा",
                "agenda1_time": "10:00 AM", "agenda1_desc": "Grocery Run", 
                "agenda2_time": "04:30 PM", "agenda2_desc": "Tea with Mom",
                "prep": "भिजवून ठेवा"
            }

        live_date = data.get("date", datetime.now().strftime("%a, %d %b %Y").upper())

        # 2x Supersampling Canvas (800x600 -> downscaled to 400x300 for e-paper)
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # ---------------------------------------------------------
        # LOAD DEDICATED FONTS
        # ---------------------------------------------------------
        # English UI Fonts
        try:
            eng_title = ImageFont.truetype(FONT_ENGLISH_PATH, 36)
            eng_header = ImageFont.truetype(FONT_ENGLISH_PATH, 26)
            eng_time = ImageFont.truetype(FONT_ENGLISH_PATH, 28)
            eng_label = ImageFont.truetype(FONT_ENGLISH_PATH, 18)
        except:
            eng_title = ImageFont.truetype(FONT_MARATHI_PATH, 36)
            eng_header = ImageFont.truetype(FONT_MARATHI_PATH, 26)
            eng_time = ImageFont.truetype(FONT_MARATHI_PATH, 28)
            eng_label = ImageFont.truetype(FONT_MARATHI_PATH, 18)

        # Marathi Dynamic Content Font
        try:
            marathi_content = ImageFont.truetype(FONT_MARATHI_PATH, 34)
        except:
            marathi_content = ImageFont.load_default()

        # Canvas Border
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # HEADER BAR (ENGLISH)
        draw.rectangle([0, 0, 800, 72], fill=0)
        draw.text((24, 14), "MealSync", font=eng_title, fill=255)
        draw.text((280, 22), live_date, font=eng_header, fill=255)

        # BATTERY / WIFI ICONS
        wifiX, wifiY = 220, 30
        draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 8, wifiY + 6, wifiX + 12, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 16, wifiY, wifiX + 20, wifiY + 20], fill=255)

        batX, batY = 740, 24
        draw.rectangle([batX, batY, batX + 48, batY + 24], outline=255, fill=0)
        draw.rectangle([batX + 48, batY + 6, batX + 52, batY + 18], fill=255)
        draw.rectangle([batX + 4, batY + 4, batX + 40, batY + 20], fill=255)

        # ---------------------------------------------------------
        # LEFT COLUMN: TODAY'S MENU & TASKS
        # ---------------------------------------------------------
        leftX = 24
        leftWidth = 456
        leftMaxWidth = 430

        # TODAY'S MENU HEADER (ENGLISH)
        menuHeaderY = 88
        draw.rectangle([leftX, menuHeaderY, leftX + leftWidth, menuHeaderY + 40], fill=0)
        draw.text((leftX + 12, menuHeaderY + 6), "TODAY'S MENU", font=eng_header, fill=255)

        current_y = menuHeaderY + 48
        
        # BREAKFAST (English Label -> Marathi Content)
        box_end_y = draw_english_highlight_label(draw, "BREAKFAST", leftX, current_y, eng_label)
        current_y = draw_wrapped_marathi_text(draw, str(data.get("breakfast", "")), leftX, box_end_y + 6, leftMaxWidth, marathi_content, fill_color=0, line_height=38)

        current_y += 12
        # LUNCH (English Label -> Marathi Content)
        box_end_y = draw_english_highlight_label(draw, "LUNCH", leftX, current_y, eng_label)
        current_y = draw_wrapped_marathi_text(draw, str(data.get("lunch", "")), leftX, box_end_y + 6, leftMaxWidth, marathi_content, fill_color=0, line_height=38)

        current_y += 12
        # DINNER (English Label -> Marathi Content)
        box_end_y = draw_english_highlight_label(draw, "DINNER", leftX, current_y, eng_label)
        current_y = draw_wrapped_marathi_text(draw, str(data.get("dinner", "")), leftX, box_end_y + 6, leftMaxWidth, marathi_content, fill_color=0, line_height=38)

        # KITCHEN TASKS HEADER (ENGLISH)
        taskHeaderY = max(current_y + 16, 390)
        draw.rectangle([leftX, taskHeaderY, leftX + leftWidth, taskHeaderY + 40], fill=0)
        draw.text((leftX + 12, taskHeaderY + 6), "KITCHEN TASKS", font=eng_header, fill=255)

        # TASKS (Marathi Content)
        t1_y = taskHeaderY + 52
        draw.rectangle([leftX, t1_y + 4, leftX + 24, t1_y + 28], outline=0, width=2)
        draw.text((leftX + 40, t1_y), str(data.get("task1", "")), font=marathi_content, fill=0)

        t2_y = t1_y + 44
        draw.rectangle([leftX, t2_y + 4, leftX + 24, t2_y + 28], outline=0, width=2)
        draw.text((leftX + 40, t2_y), str(data.get("task2", "")), font=marathi_content, fill=0)

        # CENTER VERTICAL DIVIDER
        dividerX = 496
        draw.line([(dividerX, 72), (dividerX, 590)], fill=0, width=2)

        # ---------------------------------------------------------
        # RIGHT COLUMN: AGENDA & PREP ALERT
        # ---------------------------------------------------------
        rightX = 520
        rightWidth = 256
        rightMaxWidth = 236

        # AGENDA HEADER (ENGLISH)
        agendaHeaderY = 88
        draw.rectangle([rightX, agendaHeaderY, rightX + rightWidth, agendaHeaderY + 40], fill=0)
        draw.text((rightX + 12, agendaHeaderY + 6), "AGENDA", font=eng_header, fill=255)

        agenda_y = agendaHeaderY + 48
        # English Time -> Marathi Description
        draw.text((rightX, agenda_y), str(data.get("agenda1_time", "")), font=eng_time, fill=0)
        agenda_y = draw_wrapped_marathi_text(draw, str(data.get("agenda1_desc", "")), rightX, agenda_y + 32, rightMaxWidth, marathi_content, fill_color=0, line_height=34)

        agenda_y += 8
        draw.text((rightX, agenda_y), str(data.get("agenda2_time", "")), font=eng_time, fill=0)
        agenda_y = draw_wrapped_marathi_text(draw, str(data.get("agenda2_desc", "")), rightX, agenda_y + 32, rightMaxWidth, marathi_content, fill_color=0, line_height=34)

        # PREP ALERT HEADER (ENGLISH)
        prepHeaderY = max(agenda_y + 24, taskHeaderY)
        draw.rectangle([rightX, prepHeaderY, rightX + rightWidth, prepHeaderY + 40], fill=0)
        draw.text((rightX + 12, prepHeaderY + 6), "PREP ALERT", font=eng_header, fill=255)

        # PREP ALERT CONTENT (MARATHI)
        draw_wrapped_marathi_text(draw, str(data.get("prep", "")), rightX, prepHeaderY + 50, rightMaxWidth, marathi_content, fill_color=0, line_height=38)

        # ---------------------------------------------------------
        # DOWNSCALE (800x600 -> 400x300) & THRESHOLD FOR E-PAPER
        # ---------------------------------------------------------
        img_downscaled = img.resize((400, 300), LANCZOS_FILTER)
        img_inverted_grayscale = ImageOps.invert(img_downscaled)
        
        threshold = 160
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
