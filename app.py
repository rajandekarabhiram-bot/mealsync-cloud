import os
import requests
import traceback
from flask import Flask, send_file, request
from PIL import Image, ImageDraw, ImageFont, ImageOps
import io
from datetime import datetime

app = Flask(__name__)

# Active Google Apps Script Web App URL
GOOGLE_SCRIPT_URL = "https://script.google.com/macros/s/AKfycbzH0PUjBV480wqdp3pNpcOR8358La7La_jQxuJ9EcLbB84O_2GDJsojXK1zPWTiY4cZ/exec"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Font Configuration (Uses Yantramanav/Mukta for Marathi and Inter/DejaVu for English)
FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Yantramanav-Bold.ttf")
if not os.path.exists(FONT_MARATHI_PATH):
    FONT_MARATHI_PATH = os.path.join(BASE_DIR, "Mukta-Bold.ttf")

FONT_ENGLISH_PATH = os.path.join(BASE_DIR, "DejaVuSans-Bold.ttf")

try:
    LANCZOS_FILTER = Image.Resampling.LANCZOS
except AttributeError:
    try:
        LANCZOS_FILTER = Image.LANCZOS
    except AttributeError:
        LANCZOS_FILTER = Image.BICUBIC

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

        # 2x High-Res Canvas (800x600)
        img = Image.new("L", (800, 600), 255)
        draw = ImageDraw.Draw(img)

        # Fonts scaled up for maximum legibility
        try:
            eng_title = ImageFont.truetype(FONT_ENGLISH_PATH, 38)
            eng_header = ImageFont.truetype(FONT_ENGLISH_PATH, 26)
            eng_label = ImageFont.truetype(FONT_ENGLISH_PATH, 22)
        except:
            eng_title = eng_header = eng_label = ImageFont.load_default()

        try:
            marathi_large = ImageFont.truetype(FONT_MARATHI_PATH, 46) # Extra Large Meals
            marathi_sub = ImageFont.truetype(FONT_MARATHI_PATH, 36)   # Tasks & Prep
        except:
            marathi_large = marathi_sub = ImageFont.load_default()

        # Canvas Outer Border
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # 1. TOP HEADER BAR
        draw.rectangle([0, 0, 800, 72], fill=0)
        draw.text((24, 14), "MealSync", font=eng_title, fill=255)
        draw.text((300, 20), live_date, font=eng_header, fill=255)

        # Header Icons (Battery & WiFi)
        wifiX, wifiY = 240, 30
        draw.rectangle([wifiX, wifiY + 12, wifiX + 4, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 8, wifiY + 6, wifiX + 12, wifiY + 20], fill=255)
        draw.rectangle([wifiX + 16, wifiY, wifiX + 20, wifiY + 20], fill=255)

        batX, batY = 730, 24
        draw.rectangle([batX, batY, batX + 46, batY + 24], outline=255, fill=0)
        draw.rectangle([batX + 46, batY + 6, batX + 50, batY + 18], fill=255)
        draw.rectangle([batX + 4, batY + 4, batX + 38, batY + 20], fill=255)

        # 2. MEAL SECTION (WIDE HORIZONTAL ROWS)
        meal_y = 96
        
        # BREAKFAST ROW
        draw.rectangle([24, meal_y, 180, meal_y + 40], fill=0)
        draw.text((34, meal_y + 8), "BREAKFAST", font=eng_label, fill=255)
        draw.text((200, meal_y - 4), str(data.get("breakfast", "")), font=marathi_large, fill=0)

        # LUNCH ROW
        meal_y += 76
        draw.rectangle([24, meal_y, 180, meal_y + 40], fill=0)
        draw.text((54, meal_y + 8), "LUNCH", font=eng_label, fill=255)
        draw.text((200, meal_y - 4), str(data.get("lunch", "")), font=marathi_large, fill=0)

        # DINNER ROW
        meal_y += 76
        draw.rectangle([24, meal_y, 180, meal_y + 40], fill=0)
        draw.text((48, meal_y + 8), "DINNER", font=eng_label, fill=255)
        draw.text((200, meal_y - 4), str(data.get("dinner", "")), font=marathi_large, fill=0)

        # HORIZONTAL SECTION SEPARATING LINE
        draw.line([(0, 340), (800, 340)], fill=0, width=3)

        # 3. BOTTOM SPLIT PANEL (KITCHEN TASKS | AGENDA & PREP)
        # Left Panel: Tasks
        draw.rectangle([24, 356, 380, 396], fill=0)
        draw.text((36, 362), "KITCHEN TASKS", font=eng_header, fill=255)
        draw.text((24, 412), "• " + str(data.get("task1", "")), font=marathi_sub, fill=0)
        draw.text((24, 468), "• " + str(data.get("task2", "")), font=marathi_sub, fill=0)

        # Vertical Divider Line for Bottom Split
        draw.line([(400, 340), (400, 590)], fill=0, width=3)

        # Right Panel: Agenda & Prep Alert
        draw.rectangle([420, 356, 776, 396], fill=0)
        draw.text((432, 362), "AGENDA & PREP", font=eng_header, fill=255)

        agenda_txt = f"{data.get('agenda1_time', '')} {data.get('agenda1_desc', '')}"
        draw.text((420, 412), agenda_txt, font=marathi_sub, fill=0)

        prep_txt = f"Alert: {data.get('prep', '')}"
        draw.text((420, 468), prep_txt, font=marathi_sub, fill=0)

        # 4. DOWNSCALE & SHARPEN THRESHOLD
        img_downscaled = img.resize((400, 300), LANCZOS_FILTER)
        img_inverted_grayscale = ImageOps.invert(img_downscaled)
        
        # Binary thresholding for pure 1-bit monochrome output
        threshold = 145
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
