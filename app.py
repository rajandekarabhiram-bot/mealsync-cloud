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
# FONT PATH DEFINITIONS
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


def draw_autofit_text(draw, text, x, y, max_width, max_lines, eng_font_path, marathi_font_path, max_size=40, min_size=26, fill_color=0):
    """
    Dynamically scales down the font size until the wrapped text fits strictly 
    within the designated line count and height budget for the row.
    """
    text_str = str(text).strip()
    if not text_str:
        return

    is_eng = is_ascii(text_str)
    font_path = eng_font_path if is_eng else marathi_font_path

    selected_font = None
    selected_lines = []
    current_size = max_size

    # Loop downward to find a font size where lines <= max_lines
    while current_size >= min_size:
        try:
            test_font = ImageFont.truetype(font_path, current_size)
        except:
            test_font = ImageFont.load_default()
            selected_font = test_font
            selected_lines = [text_str]
            break

        lines = get_wrapped_lines(text_str, test_font, max_width)
        if len(lines) <= max_lines:
            selected_font = test_font
            selected_lines = lines
            break
        current_size -= 2

    # Fallback if text is extremely long
    if selected_font is None:
        try:
            selected_font = ImageFont.truetype(font_path, min_size)
        except:
            selected_font = ImageFont.load_default()
        selected_lines = get_wrapped_lines(text_str, selected_font, max_width)[:max_lines]

    line_height = int(current_size * 1.22)
    current_y = y

    for line in selected_lines[:max_lines]:
        draw.text((x, current_y), line, font=selected_font, fill=fill_color)
        current_y += line_height


def draw_section_pill(draw, text, x, y, font):
    """Draws prominent, high-contrast English header tag banners with expanded padding."""
    try:
        bbox = font.getbbox(text)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
    except:
        tw = len(text) * 18
        th = 28
    draw.rectangle([x, y, x + tw + 28, y + th + 14], fill=0)
    draw.text((x + 14, y + 6), text, font=font, fill=255)
    return y + th + 14


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

        # 3. Load English Typography (Scaled Up)
        try:
            eng_logo = ImageFont.truetype(FONT_ENGLISH_PATH, 44)   # App Logo
            eng_date = ImageFont.truetype(FONT_ENGLISH_PATH, 34)   # Date Header (Increased)
            eng_pill = ImageFont.truetype(FONT_ENGLISH_PATH, 32)   # Section Tags (Increased)
        except:
            eng_logo = eng_date = eng_pill = ImageFont.load_default()

        # Canvas Outer Frame
        draw.rectangle([0, 0, 799, 599], outline=0, width=4)

        # ---------------------------------------------------------
        # TOP APP HEADER BAR (Y: 0 to 72)
        # ---------------------------------------------------------
        draw.rectangle([0, 0, 800, 72], fill=0)
        draw.text((24, 12), "MealSync", font=eng_logo, fill=255)
        draw.text((310, 18), live_date, font=eng_date, fill=255)

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
        # LOCKED GRID SLOTS WITH AUTOMATIC TEXT AUTO-FITTING
        # ---------------------------------------------------------
        full_width = 752

        # --- SLOT 1: BREAKFAST (Slot Y: 80 to 200) ---
        draw_section_pill(draw, "BREAKFAST", 24, 82, eng_pill)
        draw_autofit_text(
            draw, str(data.get("breakfast", "")), 
            x=24, y=134, max_width=full_width, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=40, min_size=26
        )
        draw.line([(0, 200), (800, 200)], fill=0, width=2)

        # --- SLOT 2: LUNCH (Slot Y: 210 to 330) ---
        draw_section_pill(draw, "LUNCH", 24, 212, eng_pill)
        draw_autofit_text(
            draw, str(data.get("lunch", "")), 
            x=24, y=264, max_width=full_width, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=40, min_size=26
        )
        draw.line([(0, 330), (800, 330)], fill=0, width=2)

        # --- SLOT 3: DINNER (Slot Y: 340 to 460) ---
        draw_section_pill(draw, "DINNER", 24, 342, eng_pill)
        draw_autofit_text(
            draw, str(data.get("dinner", "")), 
            x=24, y=394, max_width=full_width, max_lines=2, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=40, min_size=26
        )

        # ---------------------------------------------------------
        # FOOTER: KITCHEN TASKS (Slot Y: 460 to 595)
        # ---------------------------------------------------------
        draw.line([(0, 460), (800, 460)], fill=0, width=3)

        # Kitchen Tasks Section Header (Matching English Header Tag Style)
        draw_section_pill(draw, "KITCHEN TASKS", 24, 472, eng_pill)

        col_y = 532
        t1_str = "• " + str(data.get("task1", ""))
        t2_str = "• " + str(data.get("task2", ""))

        # Two spacious columns auto-fitted
        draw_autofit_text(
            draw, t1_str, 
            x=24, y=col_y, max_width=360, max_lines=1, 
            eng_font_path=FONT_ENGLISH_PATH, marathi_font_path=FONT_MARATHI_PATH, 
            max_size=36, min_size=24
        )
        draw_autofit_text(
            draw, t2_str, 
            x=410, y=col_y, max_width=360, max_lines=1, 
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
