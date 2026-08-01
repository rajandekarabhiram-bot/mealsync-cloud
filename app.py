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
# THIS IS THE MAGIC LINE YOU WERE MISSING:
# It tells the server to load the dashboard on the main page
# =========================================================
@app.route("/")
def render_dashboard():
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

    current_y = menuHeader
