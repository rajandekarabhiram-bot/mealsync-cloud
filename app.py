import os
import io
import json
import sqlite3
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file, render_template_string, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageOps

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30)) #
DB_FILE = "mealsync.db" #

SYNC_VERSION = 1 #
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "") #

PANEL_WIDTH = 400 #
PANEL_HEIGHT = 300 #
SCALE = 2 #
CANVAS_W = PANEL_WIDTH * SCALE #
CANVAS_H = PANEL_HEIGHT * SCALE #

FONT_ENGLISH_PATH = "Rubik-Bold.ttf" #
FONT_MARATHI_PATH = "Yantramanav-Bold.ttf" #

DEVICE_LOGS = [] #

def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=15) #
    conn.row_factory = sqlite3.Row #
    return conn #

def init_db():
    with get_db() as conn: #
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """) #
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_menu (
                day_name TEXT PRIMARY KEY,
                breakfast TEXT,
                lunch TEXT,
                dinner TEXT,
                task1 TEXT,
                task2 TEXT
            )
        """) #
        cur = conn.cursor() #
        cur.execute("SELECT COUNT(*) FROM weekly_menu") #
        if cur.fetchone()[0] == 0: #
            default_days = [
                ("Monday", "पोहे, चहा", "वरण भात, पोळी, भेंडी भाजी", "खिचडी, कढी, पापड", "दूध आणणे", "उद्यासाठी मटकी भिजवणे"),
                ("Tuesday", "उपमा, खोबरे चटणी", "पोळी, मटकी उसळ, भात", "थालीपीठ, लोणी", "किराणा आणणे", "पीठ आंबवणे"),
                ("Wednesday", "इडली, चटणी, सांबार", "वरण भात, पोळी, वांगी भाजी", "मसाला भात, कोशिंबीर", "भाजी धुणे", "दही लावणे"),
                ("Thursday", "शिरा, गरम दूध", "पोळी, शेवभाजी, भात", "मुगाची मऊ खिचडी", "कोथिंबीर कापणे", "दूध आणणे"),
                ("Friday", "मेथी पराठा, दही", "वरण भात, फ्लॉवर भाजी, पोळी", "दाल खिचडी, कढी", "मेथी निवडून ठेवणे", "पीठ मळणे"),
                ("Saturday", "मिसळ पाव, लिंबू", "पोळी, पनीर भाजी, जीरा राईस", "पावभाजी, कांदा", "मटार सोलणे", "बटाटे उकडणे"),
                ("Sunday", "डोसा, सांबार, चटणी", "पुरणपोळी, कटाची आमटी, भजी", "दही भात, लोणचे", "सांबार मसाला", "उद्यासाठी पोहे चाळणे")
            ] #
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days) #
        
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')") #
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')") #
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('forced_display_day', 'AUTO')")
        conn.commit() #

init_db() #

def ensure_fonts():
    font_urls = {
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf",
        "Yantramanav-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/yantramanav/Yantramanav-Bold.ttf"
    } #
    for filename, url in font_urls.items(): #
        if not os.path.exists(filename) or os.path.getsize(filename) < 2000: #
            try:
                r = requests.get(url, timeout=12) #
                if r.status_code == 200 and len(r.content) > 2000: #
                    with open(filename, "wb") as f: #
                        f.write(r.content) #
            except Exception:
                pass #

ensure_fonts() #

def get_setting(key, default_val=""):
    with get_db() as conn: #
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone() #
        return row["value"] if row else default_val #

def set_setting(key, value):
    with get_db() as conn: #
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value)) #
        conn.commit() #

def get_target_menu_data():
    forced_day = get_setting("forced_display_day", "AUTO")
    now_ist = datetime.now(IST) #
    
    if forced_day != "AUTO":
        target_day = forced_day
        date_str = f"{target_day.upper()} (LIVE SYNC)"
    else:
        if now_ist.hour >= 21: #
            target_date = now_ist + timedelta(days=1) #
        else:
            target_date = now_ist #
        target_day = target_date.strftime("%A") #
        date_str = target_date.strftime("%a, %d %b %Y").upper() #

    cuisine = get_setting("active_cuisine", "Maharashtrian") #

    with get_db() as conn: #
        row = conn.execute("SELECT * FROM weekly_menu WHERE day_name = ?", (target_day,)).fetchone() #
        if row: #
            data = {
                "day": row["day_name"], #
                "cuisine": cuisine, #
                "breakfast": (row["breakfast"] or "—").replace("+", ","), #
                "lunch": (row["lunch"] or "—").replace("+", ","), #
                "dinner": (row["dinner"] or "—").replace("+", ","), #
                "task1": (row["task1"] or "—").replace("+", ","), #
                "task2": (row["task2"] or "—").replace("+", ",") #
            }
        else:
            data = {
                "day": target_day, "cuisine": cuisine, #
                "breakfast": "—", "lunch": "—", "dinner": "—", "task1": "—", "task2": "—" #
            }

    return date_str, data #

@app.route('/hash', methods=['GET'])
def get_content_hash():
    global SYNC_VERSION #
    date_str, data = get_target_menu_data() #
    payload = f"{SYNC_VERSION}|{date_str}|{data['cuisine']}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}" #
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10] #
    
    resp = jsonify({"hash": content_hash, "sync_version": SYNC_VERSION, "day": data['day']}) #
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate" #
    return resp, 200 #

@app.route('/api/menu', methods=['GET'])
def api_get_menu():
    with get_db() as conn: #
        rows = conn.execute("SELECT * FROM weekly_menu").fetchall() #
        cuisine = get_setting("active_cuisine", "Maharashtrian") #
        diet = get_setting("active_diet", "VEG") #
        resp = jsonify({
            "menu": [dict(ix) for ix in rows], #
            "active_cuisine": cuisine, #
            "active_diet": diet, #
            "sync_version": SYNC_VERSION #
        }) #
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate" #
        return resp, 200 #

@app.route('/api/menu', methods=['POST'])
def api_update_day_menu():
    global SYNC_VERSION #
    req = request.get_json(force=True) #
    day = req.get("day_name") #
    cuisine = req.get("cuisine") #
    diet = req.get("diet") #

    if cuisine: set_setting("active_cuisine", cuisine) #
    if diet: set_setting("active_diet", diet) #
    
    # ⚡ Force the display to render this exact day
    set_setting("forced_display_day", day)

    with get_db() as conn: #
        conn.execute("""
            INSERT INTO weekly_menu (day_name, breakfast, lunch, dinner, task1, task2)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(day_name) DO UPDATE SET
                breakfast = excluded.breakfast,
                lunch = excluded.lunch,
                dinner = excluded.dinner,
                task1 = excluded.task1,
                task2 = excluded.task2
        """, (
            day,
            str(req.get("breakfast", "")).replace("+", ","), #
            str(req.get("lunch", "")).replace("+", ","), #
            str(req.get("dinner", "")).replace("+", ","), #
            str(req.get("task1", "")).replace("+", ","), #
            str(req.get("task2", "")).replace("+", ",") #
        )) #
        conn.commit() #

    SYNC_VERSION += 1 #
    print(f"[SYNC TRIGGERED] Day: {day} updated. SYNC_VERSION: {SYNC_VERSION}") #
    
    resp = jsonify({"status": "updated", "sync_version": SYNC_VERSION, "forced_day": day}) #
    resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate" #
    return resp, 200 #

def safe_font(font_path, size_1x):
    try:
        if os.path.exists(font_path) and os.path.getsize(font_path) > 2000: #
            return ImageFont.truetype(font_path, size_1x * SCALE) #
    except Exception:
        pass #
    return ImageFont.load_default() #

def is_ascii(s):
    return all(ord(c) < 128 for c in s) #

def get_text_width(font, text):
    try:
        bbox = font.getbbox(text) #
        return bbox[2] - bbox[0] #
    except Exception:
        return len(text) * 16 #

def get_wrapped_lines(text, font, max_width_2x):
    words = str(text).strip().split() #
    if not words: #
        return [] #
    lines, curr = [], [] #
    for w in words: #
        test_line = " ".join(curr + [w]) #
        if get_text_width(font, test_line) <= max_width_2x: #
            curr.append(w) #
        else:
            if curr: #
                lines.append(" ".join(curr)) #
                curr = [w] #
            else:
                lines.append(w) #
                curr = [] #
    if curr: #
        lines.append(" ".join(curr)) #
    return lines #

def draw_autofit_text(draw, text_str, x_1x, y_1x, max_w_1x, max_h_1x, max_size=18, min_size=13, max_lines=2, fill_color=0):
    text_str = str(text_str).strip() #
    if not text_str: #
        return #
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH #
    selected_font = None #
    selected_lines = [] #
    line_mult = 1.35 if is_ascii(text_str) else 1.40 #
    
    max_w_2x = max_w_1x * SCALE #
    max_h_2x = max_h_1x * SCALE #

    for size in range(max_size, min_size - 1, -1): #
        test_font = safe_font(font_file, size) #
        lines = get_wrapped_lines(text_str, test_font, max_w_2x) #
        line_h = int((size * SCALE) * line_mult) #
        total_h = len(lines) * line_h #
        if len(lines) <= max_lines and total_h <= max_h_2x: #
            selected_font = test_font #
            selected_lines = lines #
            break #
            
    if not selected_font: #
        selected_font = safe_font(font_file, min_size) #
        selected_lines = get_wrapped_lines(text_str, selected_font, max_w_2x)[:max_lines] #

    line_h = int((selected_font.size) * line_mult) if hasattr(selected_font, 'size') else 36 #
    curr_y = y_1x * SCALE #
    for line in selected_lines: #
        draw.text((x_1x * SCALE, curr_y), line, font=selected_font, fill=fill_color) #
        curr_y += line_h #

@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD': #
        return "OK", 200 #

    try:
        ensure_fonts() #
        date_str, data = get_target_menu_data() #

        rssi = int(request.args.get('rssi', -50)) #
        batt_str = str(request.args.get('batt', '500d+')) #
        batt_pct = int(request.args.get('pct', 100)) #

        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255) #
        draw = ImageDraw.Draw(img_2x) #

        font_logo = safe_font(FONT_ENGLISH_PATH, 18) #
        font_date = safe_font(FONT_ENGLISH_PATH, 13) #
        font_badge = safe_font(FONT_ENGLISH_PATH, 13) #
        font_section = safe_font(FONT_ENGLISH_PATH, 15) #

        # Header
        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0) #
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE) #
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255) #

        # Wi-Fi Signal
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1) #
        wifiX, wifiY = 96 * SCALE, 13 * SCALE #
        draw.rectangle([wifiX + 4, wifiY + 20, wifiX + 8,  wifiY + 28], fill=255 if signal_bars >= 1 else 0) #
        draw.rectangle([wifiX + 14, wifiY + 12, wifiX + 18, wifiY + 28], fill=255 if signal_bars >= 2 else 0) #
        draw.rectangle([wifiX + 24, wifiY + 4,  wifiX + 28, wifiY + 28], fill=255 if signal_bars >= 3 else 0) #

        # Date
        date_w = get_text_width(font_date, date_str) #
        date_center_x = (CANVAS_W - date_w) // 2 #
        draw.text((date_center_x, 11 * SCALE), date_str, font=font_date, fill=255) #

        # Battery
        batX, batY = 362 * SCALE, 12 * SCALE #
        draw.rectangle([batX, batY, batX + 24 * SCALE, batY + 14 * SCALE], outline=255, width=SCALE) #
        draw.rectangle([batX + 24 * SCALE, batY + 3 * SCALE, batX + 26 * SCALE, batY + 11 * SCALE], fill=255) #
        fill_w = max(0, min(20 * SCALE, int((batt_pct / 100.0) * 20 * SCALE))) #
        if fill_w > 0: #
            draw.rectangle([batX + 2 * SCALE, batY + 2 * SCALE, batX + 2 * SCALE + fill_w, batY + 12 * SCALE], fill=255) #
        badge_w = get_text_width(font_badge, batt_str) #
        draw.text((batX - badge_w - 10, 11 * SCALE), batt_str, font=font_badge, fill=255) #

        # Sidebar
        sidebar_w = 118 * SCALE #
        draw.rectangle([0, 38 * SCALE, sidebar_w, CANVAS_H - 1], fill=0) #
        draw.text((10 * SCALE, 52 * SCALE), "BREAKFAST", font=font_section, fill=255) #
        draw.text((10 * SCALE, 112 * SCALE), "LUNCH", font=font_section, fill=255) #
        draw.text((10 * SCALE, 175 * SCALE), "DINNER", font=font_section, fill=255) #
        draw.text((10 * SCALE, 245 * SCALE), "TASKS", font=font_section, fill=255) #

        for y_div in [98, 160, 228]: #
            draw.line([(0, y_div * SCALE), (sidebar_w, y_div * SCALE)], fill=255, width=2 * SCALE) #
            draw.line([(sidebar_w, y_div * SCALE), (CANVAS_W, y_div * SCALE)], fill=0, width=2 * SCALE) #

        # Meal & Tasks Text
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0) #
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0) #
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0) #

        # Checkboxes
        draw.rectangle([128 * SCALE, 243 * SCALE, 142 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE) #
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_size=17, min_size=14, max_lines=1, fill_color=0) #

        draw.rectangle([264 * SCALE, 243 * SCALE, 278 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE) #
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_size=17, min_size=14, max_lines=1, fill_color=0) #

        resample_mode = Image.LANCZOS if hasattr(Image, 'LANCZOS') else getattr(Image, 'ANTIALIAS', 1) #
        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=resample_mode) #
        img_1bit = img_downscaled.point(lambda p: 255 if p > 160 else 0, mode="1") #

        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1': #
            img_epd = ImageOps.invert(img_1bit.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1") #
            resp = Response(img_epd.tobytes(), mimetype='application/octet-stream') #
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate" #
            return resp #

        buf = io.BytesIO() #
        img_1bit.save(buf, format='BMP') #
        buf.seek(0) #
        resp = send_file(buf, mimetype='image/bmp') #
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate" #
        return resp #

    except Exception as err:
        traceback.print_exc() #
        return f"Internal Error: {err}", 500 #

# Re-include web UI router
@app.route('/')
def app_home():
    # Retains full PWA template
    return render_template_string(HTML_TEMPLATE_DATA)
