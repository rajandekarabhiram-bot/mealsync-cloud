import os
import requests
import traceback
from flask import Flask, send_file, request
from PIL import Image, ImageDraw, ImageFont
import io
from datetime import datetime

app = Flask(__name__)

# Your active Google Apps Script Web App URL
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REGULAR = os.path.join(BASE_DIR, "Teko-Regular.ttf")
FONT_BOLD = os.path.join(BASE_DIR, "Teko-Bold.ttf")

def draw_wrapped_text(draw, text, x, y, max_width, font, fill_color=0, line_height=20):
    words = str(text).split(" ")
    lines = []
    current_line = ""
    
    for word in words:
        test_line = current_line + " " + word if current_line else word
        try:
            bbox = font.getbbox(test_line)
            w = bbox[2] - bbox[0]
        except:
            w = len(test_line) * 7  
            
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

def draw_tight_label(draw, text, x, y, font):
    """Draws a solid black highlight box tightly fitted around the label word."""
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except:
        tw = len(text) * 6
        th = 10
    draw.rectangle([x, y, x + tw + 6, y + th + 4], fill=0)
    draw.text((x + 3, y + 2), text, font=font, fill=1) # 1 = White in 1-bit mode
    return y + th + 4

@app.route("/", methods=["GET", "HEAD"])
def render_dashboard():
    if request.method == "HEAD":
        return "OK", 200

    try:
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

        # --- PURE 1-BIT CANVAS (1 = White, 0 = Black) ---
        img = Image.new("1", (400, 300), 1)
        draw = ImageDraw.Draw(img)

        try:
            # Use Bold font weights for Devanagari to give strokes strong pixel presence
            font_title = ImageFont.truetype(FONT_BOLD, 18)
            font_header = ImageFont.truetype(FONT_BOLD, 14)
            font_time = ImageFont.truetype(FONT_BOLD, 14)
            font_label = ImageFont.truetype(FONT_BOLD, 10)
            font_marathi = ImageFont.truetype(FONT_BOLD, 16) # Bold Devanagari prevents thin/broken strokes
        except:
            font_title = font_header = font_time = font_label = font_marathi = ImageFont.load_default()

        # Outer Frame
        draw.rectangle([0, 0, 399, 299], outline=0, width=2)

        # HEADER
        draw.rectangle([0, 0, 400, 36], fill=0)
        draw.text((12, 6), "MealSync", font=font_title, fill=1)
        draw.text((140, 10), live_date, font=font_header, fill=1)

        # BATTERY / WIFI ICONS
        wifiX, wifiY = 110, 15
        draw.rectangle([wifiX, wifiY + 6, wifiX + 2, wifiY + 10], fill=1)
        draw.rectangle([wifiX + 4, wifiY + 3, wifiX + 6, wifiY + 10], fill=1)
        draw.rectangle([wifiX + 8, wifiY, wifiX + 10, wifiY + 10], fill=1)

        batX, batY = 370, 12
        draw.rectangle([batX, batY, batX + 24, batY + 12], outline=1, fill=0)
        draw.rectangle([batX + 24, batY + 3, batX + 26, batY + 9], fill=1)
        draw.rectangle([batX + 2, batY + 2, batX + 20, batY + 10], fill=1)

        # LEFT COLUMN
        leftX = 12
        leftWidth = 228
        leftMaxWidth = 215

        menuHeaderY = 44
        draw.rectangle([leftX, menuHeaderY, leftX + leftWidth, menuHeaderY + 20], fill=0)
        draw.text((leftX + 6, menuHeaderY + 3), "TODAY'S MENU", font=font_header, fill=1)

        current_y = menuHeaderY + 24
        
        # BREAKFAST
        box_end_y = draw_tight_label(draw, "BREAKFAST", leftX, current_y, font_label)
        current_y = draw_wrapped_text(draw, str(data.get("breakfast", "")), leftX, box_end_y + 3, leftMaxWidth, font_marathi, fill_color=0, line_height=19)

        current_y += 6
        # LUNCH
        box_end_y = draw_tight_label(draw, "LUNCH", leftX, current_y, font_label)
        current_y = draw_wrapped_text(draw, str(data.get("lunch", "")), leftX, box_end_y + 3, leftMaxWidth, font_marathi, fill_color=0, line_height=19)

        current_y += 6
        # DINNER
        box_end_y = draw_tight_label(draw, "DINNER", leftX, current_y, font_label)
        current_y = draw_wrapped_text(draw, str(data.get("dinner", "")), leftX, box_end_y + 3, leftMaxWidth, font_marathi, fill_color=0, line_height=19)

        taskHeaderY = max(current_y + 8, 195)
        draw.rectangle([leftX, taskHeaderY, leftX + leftWidth, taskHeaderY + 20], fill=0)
        draw.text((leftX + 6, taskHeaderY + 3), "KITCHEN TASKS", font=font_header, fill=1)

        t1_y = taskHeaderY + 26
        draw.rectangle([leftX, t1_y + 2, leftX + 12, t1_y + 14], outline=0, width=1)
        draw.text((leftX + 20, t1_y), str(data.get("task1", "")), font=font_marathi, fill=0)

        t2_y = t1_y + 22
        draw.rectangle([leftX, t2_y + 2, leftX + 12, t2_y + 14], outline=0, width=1)
        draw.text((leftX + 20, t2_y), str(data.get("task2", "")), font=font_marathi, fill=0)

        dividerX = 248
        draw.line([(dividerX, 36), (dividerX, 295)], fill=0, width=1)

        # RIGHT COLUMN
        rightX = 260
        rightWidth = 128
        rightMaxWidth = 118

        agendaHeaderY = 44
        draw.rectangle([rightX, agendaHeaderY, rightX + rightWidth, agendaHeaderY + 20], fill=0)
        draw.text((rightX + 6, agendaHeaderY + 3), "AGENDA", font=font_header, fill=1)

        agenda_y = agendaHeaderY + 24
        draw.text((rightX, agenda_y), str(data.get("agenda1_time", "")), font=font_time, fill=0)
        agenda_y = draw_wrapped_text(draw, str(data.get("agenda1_desc", "")), rightX, agenda_y + 16, rightMaxWidth, font_marathi, fill_color=0, line_height=17)

        agenda_y += 4
        draw.text((rightX, agenda_y), str(data.get("agenda2_time", "")), font=font_time, fill=0)
        agenda_y = draw_wrapped_text(draw, str(data.get("agenda2_desc", "")), rightX, agenda_y + 16, rightMaxWidth, font_marathi, fill_color=0, line_height=17)

        prepHeaderY = max(agenda_y + 12, taskHeaderY)
        draw.rectangle([rightX, prepHeaderY, rightX + rightWidth, prepHeaderY + 20], fill=0)
        draw.text((rightX + 6, prepHeaderY + 3), "PREP ALERT", font=font_header, fill=1)

        draw_wrapped_text(draw, str(data.get("prep", "")), rightX, prepHeaderY + 25, rightMaxWidth, font_marathi, fill_color=0, line_height=19)

        # Output Stream
        user_agent = request.headers.get('User-Agent', '')
        
        if "ESP32HTTPClient" not in user_agent:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            buf.seek(0)
            return send_file(buf, mimetype="image/png")
        else:
            # In 1-bit mode, invert black/white bits for the e-paper buffer if necessary
            img_inverted = img.point(lambda p: 0 if p == 1 else 1)
            raw_pixels = img_inverted.tobytes()
            return send_file(io.BytesIO(raw_pixels), mimetype="application/octet-stream")

    except Exception as err:
        print("CRITICAL SERVER ERROR:")
        traceback.print_exc()
        return f"Server Error: {str(err)}", 500

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
