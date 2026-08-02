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

# Font Paths
FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Bold.ttf")
FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")

# Safe Filter Selection for Pillow Compatibility
try:
    LANCZOS_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    try:
        LANCZOS_FILTER = Image.LANCZOS
    except AttributeError:
        LANCZOS_FILTER = Image.BICUBIC


def draw_wrapped_marathi_text(draw, text, x, y, max_width, font, fill_color=0, line_height=48):
    """Draws Marathi content with generous line spacing to protect Devanagari matras."""
    words = str(text).split(" ")
    lines = []
    current_line = ""
    
    for word in words:
        test_line = current_line + " " + word if current_line else word
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except:
            w = len(test_line) * 18  
            
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
    """Draws a well-padded black box around sub-headers to prevent text clipping."""
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except:
        tw = len(text) * 14
        th = 22
    # Padding tuned for 800x600 canvas
    draw.rectangle([x, y, x + tw + 20, y + th + 16], fill=0)
    draw.text((x + 10, y + 6), text, font=font, fill=255)
    return y + th + 16


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

        # 2x Canvas (800x600 -> downscaled to 400x300 for crispness)
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # UNIFORM FONT SCALING (800x600 Base)
        try:
            eng_title = ImageFont.truetype(FONT_ENGLISH_PATH, 38)
            eng_header = ImageFont.truetype(FONT_ENGLISH_PATH, 28)
            eng_time = ImageFont.truetype(FONT_ENGLISH_PATH, 30)
            eng_label = ImageFont.truetype(FONT_ENGLISH_PATH, 22) # Increased for clear visibility
        except:
            eng_title = ImageFont.truetype(FONT_MARATHI_PATH, 38)
            eng_header = ImageFont.truetype(FONT_MARATHI_PATH, 28)
            eng_time = ImageFont.truetype(FONT_MARATHI_PATH, 30)
            eng_label = ImageFont.truetype(FONT_MARATHI_PATH, 22)

        # Uniform Large Devanagari Font (42px)
        try:
            marathi_content = ImageFont.truetype(FONT_MARATHI_PATH, 42)
        except:
            marathi_content = ImageFont.load_default()

        # Canvas Outer Frame
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # HEADER BAR
        draw.rectangle([0, 0, 800, 76], fill=0)
        draw.text((24, 16), "MealSync", font=eng_title, fill=255)
        draw.text((280, 24), live_date, font=eng_header, fill=255)

        # BATTERY / WIFI ICONS
        wifiX, wifiY = 220, 32
        draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 8, wifiY + 6, wifiX + 12, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 16, wifiY, wifiX + 20, wifiY + 20], fill=255)

        batX, batY = 740, 26
        draw.rectangle([batX, batY, batX + 48, batY + 24], outline=255, fill=0)
        draw.rectangle([batX + 48, batY + 6, batX + 52, batY + 18], fill=255)
        draw.rectangle([batX + 4, batY + 4, batX + 40, batY + 20], fill=255)

        # LEFT COLUMN
        leftX = 24
        leftWidth = 456
        leftMaxWidth = 430

        # TODAY'S MENU HEADER
        menuHeaderY = 92
        draw.rectangle([leftX, menuHeaderY, leftX + leftWidth, menuHeaderY + 42], fill=0)
        draw.text((leftX + 14, menuHeaderY + 6), "TODAY'S MENU", font=eng_header, fill=255)

        current_y = menuHeaderY + 54
        
        # BREAKFAST
        box_end_y = draw_english_highlight_label(draw, "BREAKFAST", leftX, current_y, eng_label)
        current_y = draw_wrapped_marathi_text(draw, str(data.get("breakfast", "")), leftX, box_end_y + 8, leftMaxWidth, marathi_content, fill_color=0, line_height=48)

        current_y += 12
        # LUNCH
        box_end_y = draw_english_highlight_label(draw, "LUNCH", leftX, current_y, eng_label)
        current_y = draw_wrapped_marathi_text(draw, str(data.get("lunch", "")), leftX, box_end_y + 8, leftMaxWidth, marathi_content, fill_color=0, line_height=48)

        current_y += 12
        # DINNER
        box_end_y = draw_english_highlight_label(draw, "DINNER", leftX, current_y, eng_label)
        current_y = draw_wrapped_marathi_text(draw, str(data.get("dinner", "")), leftX, box_end_y + 8, leftMaxWidth, marathi_content, fill_color=0, line_height=48)

        # KITCHEN TASKS HEADER
        taskHeaderY = max(current_y + 16, 396)
        draw.rectangle([leftX, taskHeaderY, leftX + leftWidth, taskHeaderY + 42], fill=0)
        draw.text((leftX + 14, taskHeaderY + 6), "KITCHEN TASKS", font=eng_header, fill=255)

        # TASKS (Bullet alignment)
        t1_y = taskHeaderY + 52
        draw.text((leftX, t1_y), "• " + str(data.get("task1", "")), font=marathi_content, fill=0)

        t2_y = t1_y + 48
        draw.text((leftX, t2_y), "• " + str(data.get("task2", "")), font=marathi_content, fill=0)

        # CENTER VERTICAL DIVIDER
        dividerX = 496
        draw.line([(dividerX, 76), (dividerX, 590)], fill=0, width=2)

        # RIGHT COLUMN
        rightX = 520
        rightWidth = 256
        rightMaxWidth = 236

        # AGENDA HEADER
        agendaHeaderY = 92
        draw.rectangle([rightX, agendaHeaderY, rightX + rightWidth, agendaHeaderY + 42], fill=0)
        draw.text((rightX + 14, agendaHeaderY + 6), "AGENDA", font=eng_header, fill=255)

        agenda_y = agendaHeaderY + 54
        draw.text((rightX, agenda_y), str(data.get("agenda1_time", "")), font=eng_time, fill=0)
        agenda_y = draw_wrapped_marathi_text(draw, str(data.get("agenda1_desc", "")), rightX, agenda_y + 34, rightMaxWidth, marathi_content, fill_color=0, line_height=42)

        agenda_y += 10
        draw.text((rightX, agenda_y), str(data.get("agenda2_time", "")), font=eng_time, fill=0)
        agenda_y = draw_wrapped_marathi_text(draw, str(data.get("agenda2_desc", "")), rightX, agenda_y + 34, rightMaxWidth, marathi_content, fill_color=0, line_height=42)

        # PREP ALERT HEADER
        prepHeaderY = max(agenda_y + 24, taskHeaderY)
        draw.rectangle([rightX, prepHeaderY, rightX + rightWidth, prepHeaderY + 42], fill=0)
        draw.text((rightX + 14, prepHeaderY + 6), "PREP ALERT", font=eng_header, fill=255)

        # PREP ALERT CONTENT
        draw_wrapped_marathi_text(draw, str(data.get("prep", "")), rightX, prepHeaderY + 54, rightMaxWidth, marathi_content, fill_color=0, line_height=48)

        # DOWNSCALE & SHARP MONOCHROME THRESHOLD
        img_downscaled = img.resize((400, 300), LANCZOS_FILTER)
        img_inverted_grayscale = ImageOps.invert(img_downscaled)
        
        # Tighter threshold value for crisp monoline stroke edges
        threshold = 140
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
