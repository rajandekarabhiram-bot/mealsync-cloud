import os
import requests
from flask import Flask, send_file, request
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
from datetime import datetime

app = Flask(__name__)

# Make sure this is your active Google Apps Script Web App URL!
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbxroRao7ruKprKxpK3VIeP2uHbysBPp2IEDs9MhIzG9JdbPVXSatA746tBwXFhZdVay/exec"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONT_REGULAR = os.path.join(BASE_DIR, "NotoSansDevanagari-Regular.ttf")
FONT_BOLD = os.path.join(BASE_DIR, "NotoSansDevanagari-Bold.ttf")

def draw_wrapped_text(draw, text, x, y, max_width, font, fill_color, line_height=18):
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

# =========================================================
# THE BOUNCER: We add methods=['GET', 'HEAD'] to allow pinging.
# =========================================================
@app.route("/", methods=["GET", "HEAD"])
def render_dashboard():
    # 1. If it's UptimeRobot just checking in, say "I'm alive" instantly!
    if request.method == "HEAD":
        return "OK", 200

    # 2. Otherwise, do the normal heavy lifting for the ESP32 or Browser
    try:
        response = requests.get(GOOGLE_SCRIPT_URL, timeout=10)
        data = response.json()
    except Exception as e:
        # Fallback data if Google Sheets fails to connect
        data = {
            "breakfast": "पुरणपोळी", "lunch": "भाजी पोळी", "dinner": "खिचडी",
            "task1": "दूध आणा", "task1_done": True, "task2": "भाजी धुवा", "task2_done": False,
            "agenda1_time": "10:00 AM", "agenda1_desc": "Grocery Run", "agenda2_time": "04:30 PM", "agenda2_desc": "Tea with Mom",
            "prep": "भिजवून ठेवा"
        }

    live_date = data.get("date", datetime.now().strftime("%a, %d %b %Y").upper())

    img = Image.new("L", (400, 300), 255)
    draw = ImageDraw.Draw(img)

    try:
        font_title = ImageFont.truetype(FONT_BOLD, 18)
        font_header = ImageFont.truetype(FONT_BOLD, 14)
        font_time = ImageFont.truetype(FONT_BOLD, 13)
        font_label = ImageFont.truetype(FONT_BOLD, 10)
        font_marathi = ImageFont.truetype(FONT_REGULAR, 15)
        font_footer = ImageFont.truetype(FONT_REGULAR, 12)
    except:
        font_title = font_header = font_time = font_label = font_marathi = font_footer = ImageFont.load_default()

    draw.rectangle([0, 0, 399, 299], outline=0, width=2)

    # HEADER
    draw.rectangle([0, 0, 400, 36], fill=0)
    draw.text((12, 6), "MealSync", font=font_title, fill=255)
    draw.text((140, 10), live_date, font=font_header, fill=255)

    # BATTERY / WIFI ICONS
    wifiX, wifiY = 110, 15
    draw.rectangle([wifiX, wifiY + 6, wifiX + 2, wifiY + 10], fill=255)
    draw.rectangle([wifiX + 4, wifiY + 3, wifiX + 6, wifiY + 10], fill=255)
    draw.rectangle([wifiX + 8, wifiY, wifiX + 10, wifiY + 10], fill=255)

    batX, batY = 370, 12
    draw.rectangle([batX, batY, batX + 24, batY + 12], outline=255, fill=0)
    draw.rectangle([batX + 24, batY + 3, batX + 26, batY + 9], fill=255)
    draw.rectangle([batX + 2, batY + 2, batX + 20, batY + 10], fill=255)

    # LEFT COLUMN
    leftX = 12
    leftWidth = 228
    leftMaxWidth = 215

    menuHeaderY = 44
    draw.rectangle([leftX, menuHeaderY, leftX + leftWidth, menuHeaderY + 20], fill=0)
    draw.text((leftX + 6, menuHeaderY + 3), "TODAY'S MENU", font=font_header, fill=255)

    current_y = menuHeaderY + 24
    draw.text((leftX, current_y), "BREAKFAST", font=font_label, fill=0)
    current_y = draw_wrapped_text(draw, str(data.get("breakfast", "")), leftX, current_y + 14, leftMaxWidth, font_marathi, 0, line_height=17)

    current_y += 6
    draw.text((leftX, current_y), "LUNCH", font=font_label, fill=0)
    current_y = draw_wrapped_text(draw, str(data.get("lunch", "")), leftX, current_y + 14, leftMaxWidth, font_marathi, 0, line_height=17)

    current_y += 6
    draw.text((leftX, current_y), "DINNER", font=font_label, fill=0)
    current_y = draw_wrapped_text(draw, str(data.get("dinner", "")), leftX, current_y + 14, leftMaxWidth, font_marathi, 0, line_height=17)

    taskHeaderY = max(current_y + 10, 190)
    draw.rectangle([leftX, taskHeaderY, leftX + leftWidth, taskHeaderY + 20], fill=0)
    draw.text((leftX + 6, taskHeaderY + 3), "KITCHEN TASKS", font=font_header, fill=255)

    task1_done = data.get("task1_done", False)
    t1_y = taskHeaderY + 26
    draw.rectangle([leftX, t1_y + 2, leftX + 12, t1_y + 14], outline=0, width=1)
    if task1_done:
        draw.rectangle([leftX + 3, t1_y + 5, leftX + 9, t1_y + 11], fill=0)
    draw.text((leftX + 20, t1_y), str(data.get("task1", "")), font=font_marathi, fill=0)

    task2_done = data.get("task2_done", False)
    t2_y = t1_y + 22
    draw.rectangle([leftX, t2_y + 2, leftX + 12, t2_y + 14], outline=0, width=1)
    if task2_done:
        draw.rectangle([leftX + 3, t2_y + 5, leftX + 9, t2_y + 11], fill=0)
    draw.text((leftX + 20, t2_y), str(data.get("task2", "")), font=font_marathi, fill=0)

    dividerX = 248
    draw.line([(dividerX, 36), (dividerX, 270)], fill=0, width=1)

    # RIGHT COLUMN
    rightX = 260
    rightWidth = 128
    rightMaxWidth = 118

    agendaHeaderY = 44
    draw.rectangle([rightX, agendaHeaderY, rightX + rightWidth, agendaHeaderY + 20], fill=0)
    draw.text((rightX + 6, agendaHeaderY + 3), "AGENDA", font=font_header, fill=255)

    agenda_y = agendaHeaderY + 24
    draw.text((rightX, agenda_y), str(data.get("agenda1_time", "")), font=font_time, fill=0)
    agenda_y = draw_wrapped_text(draw, str(data.get("agenda1_desc", "")), rightX, agenda_y + 15, rightMaxWidth, font_marathi, 0, line_height=16)

    agenda_y += 6
    draw.text((rightX, agenda_y), str(data.get("agenda2_time", "")), font=font_time, fill=0)
    agenda_y = draw_wrapped_text(draw, str(data.get("agenda2_desc", "")), rightX, agenda_y + 15, rightMaxWidth, font_marathi, 0, line_height=16)

    prepHeaderY = max(agenda_y + 15, taskHeaderY)
    draw.rectangle([rightX, prepHeaderY, rightX + rightWidth, prepHeaderY + 20], fill=0)
    draw.text((rightX + 6, prepHeaderY + 3), "PREP ALERT", font=font_header, fill=255)

    draw_wrapped_text(draw, str(data.get("prep", "")), rightX, prepHeaderY + 25, rightMaxWidth, font_marathi, 0, line_height=17)

    # FOOTER
    draw.line([(0, 270), (400, 270)], fill=0, width=1)
    draw.text((12, 275), '"Patience in cooking is the finest seasoning."', font=font_footer, fill=0)

    img_1bit = img.convert("1", dither=Image.NONE)
    img_inverted = ImageOps.invert(img_1bit)

    user_agent = request.headers.get('User-Agent', '')
    
    # Send PNG for Browser preview, raw stream for ESP32
    if "ESP32HTTPClient" not in user_agent:
        buf = io.BytesIO()
        img_inverted.save(buf, format="PNG")
        buf.seek(0)
        return send_file(buf, mimetype="image/png")
    else:
        raw_pixels = img_inverted.tobytes()
        return send_file(io.BytesIO(raw_pixels), mimetype="application/octet-stream")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
