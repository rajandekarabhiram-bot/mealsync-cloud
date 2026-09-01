import os
import io
import json
import sqlite3
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file, render_template_string, jsonify
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
DB_FILE = "mealsync.db"

SYNC_VERSION = 1
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE   # 800px
CANVAS_H = PANEL_HEIGHT * SCALE  # 600px

# ============================================================================
# 1. CORS & CACHE HEADERS
# ============================================================================
@app.after_request
def add_cors_and_cache_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

# ============================================================================
# 2. INDUSTRIAL GRADE FONT ENGINE
# ============================================================================
FONT_FILES = {
    "latin_bold": "DejaVuSans-Bold.ttf",
    "latin_regular": "DejaVuSans.ttf",
    "devanagari_bold": "NotoSansDevanagari-Bold.ttf"
}

def ensure_fonts():
    font_urls = {
        "DejaVuSans-Bold.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf",
        "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"
    }
    for filename, url in font_urls.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 2000:
            try:
                r = requests.get(url, timeout=15)
                if r.status_code == 200 and len(r.content) > 2000:
                    with open(filename, "wb") as f:
                        f.write(r.content)
            except Exception:
                pass

ensure_fonts()

def load_font(name, size_1x):
    filename = FONT_FILES.get(name, "DejaVuSans-Bold.ttf")
    try:
        if os.path.exists(filename) and os.path.getsize(filename) > 2000:
            return ImageFont.truetype(filename, size_1x * SCALE)
    except Exception:
        pass
    try:
        return ImageFont.load_default()
    except Exception:
        return None

def is_devanagari(text):
    for ch in str(text):
        if 0x0900 <= ord(ch) <= 0x097F:
            return True
    return False

def get_best_font(text, size_1x):
    if is_devanagari(text):
        return load_font("devanagari_bold", size_1x)
    return load_font("latin_bold", size_1x)

def get_text_width(font, text):
    try:
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]
    except Exception:
        return len(str(text)) * (8 * SCALE)

def draw_wrapped_text(draw, text_str, x_1x, y_1x, max_w_1x, font, line_height_1x, max_lines=2, fill=0):
    words = str(text_str).strip().split()
    if not words:
        return
    max_w_px = max_w_1x * SCALE
    lines = []
    curr = []
    for w in words:
        test = " ".join(curr + [w])
        if get_text_width(font, test) <= max_w_px:
            curr.append(w)
        else:
            if curr:
                lines.append(" ".join(curr))
                curr = [w]
            else:
                lines.append(w)
                curr = []
    if curr:
        lines.append(" ".join(curr))

    curr_y = y_1x * SCALE
    for line in lines[:max_lines]:
        draw.text((x_1x * SCALE, curr_y), line, font=font, fill=fill)
        curr_y += int(line_height_1x * SCALE)

# ============================================================================
# 3. DATABASE SETUP & PERSISTENCE
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("CREATE TABLE IF NOT EXISTS app_settings (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute("CREATE TABLE IF NOT EXISTS weekly_menu (day_name TEXT PRIMARY KEY, breakfast TEXT, lunch TEXT, dinner TEXT, task1 TEXT, task2 TEXT)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS device_telemetry (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                battery_pct INTEGER,
                battery_label TEXT,
                voltage REAL,
                rssi INTEGER,
                wifi_strength TEXT,
                last_seen TEXT
            )
        """)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM weekly_menu")
        if cur.fetchone()[0] == 0:
            default_days = [
                ("Monday", "खमंग थालीपीठ व लोणी (Thalipeeth)", "वरण भात, पोळी व भेंडी भाजी (Varan Bhaat, Bhaji)", "मूग डाळ खिचडी व कढी (Moong Khichdi, Kadhi)", "उसळीसाठी मटकी भिजवणे (Soak Matki)", "इडलीचे पीठ आंबवणे (Ferment Idli Batter)"),
                ("Tuesday", "मऊ कांदे पोहे व चहा (Kande Pohe)", "भरली वांगी, भाकरी व वरण (Bharli Vangi, Bhakri)", "दाल तडका व जिरा राईस (Dal Tadka, Rice)", "पालेभाज्या धुवून ठेवणे (Wash Greens)", "सकाळचे दूध उकळणे (Boil Milk)"),
                ("Wednesday", "मऊ इडली सांबार (Idli Sambar)", "मेथी भाजी, पोळी व भात (Methi Bhaji, Poli)", "मसाला भात व कोशिंबीर (Masala Bhaat)", "कोथिंबीर बारीक चिरणे (Chop Herbs)", "दही विरजण लावणे (Set Curd)"),
                ("Thursday", "गरम रवा उपमा व चटणी (Upma)", "शेवभाजी, पोळी व भात (Shev Bhaji, Chapati)", "पिठलं भाकरी व ठेचा (Pithla Bhakri)", "हिरवे मटार सोलणे (Peel Peas)", "कांदा-लसूण वाटण करणे (Prep Paste)"),
                ("Friday", "मेथी पराठा व ताजे दही (Methi Paratha)", "फ्लॉवर रस्सा भाजी व पोळी (Cauliflower Curry)", "दाल खिचडी व साजूक तूप (Dal Khichdi, Ghee)", "आले-लसूण पेस्ट करणे (Ginger Paste)", "चपातीचे पीठ मळणे (Knead Dough)"),
                ("Saturday", "झणझणीत मिसळ पाव (Misal Pav)", "पनीर मसाला व जिरा राईस (Paneer Masala)", "घरगुती पावभाजी व बटर (Pav Bhaji)", "बटाटे उकडवून ठेवणे (Boil Potatoes)", "टोमॅटो बारीक कापणे (Chop Tomatoes)"),
                ("Sunday", "कुरकुरीत डोसा व चटणी (Crispy Dosa)", "पुरणपोळी, आमटी व भजी (Puran Poli Feast)", "दही भात व जिरा तडका (Curd Rice)", "सांबार मसाला वाटणे (Grind Spices)", "पोहे स्वच्छ करणे (Clean Poha)")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('forced_display_day', 'AUTO')")
        conn.execute("""
            INSERT OR IGNORE INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, 86, '430d', 4.10, -82, 'Good (2/3)', 'Online')
        """)
        conn.commit()

init_db()

def get_telemetry():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM device_telemetry WHERE id = 1").fetchone()
        if row:
            return dict(row)
        return {"battery_pct": 86, "battery_label": "430d", "voltage": 4.10, "rssi": -82, "wifi_strength": "Good (2/3)", "last_seen": "Online"}

def update_telemetry_db(pct, label, v, rssi, wifi_strength):
    now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
    with get_db() as conn:
        conn.execute("""
            INSERT INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                battery_pct = excluded.battery_pct,
                battery_label = excluded.battery_label,
                voltage = excluded.voltage,
                rssi = excluded.rssi,
                wifi_strength = excluded.wifi_strength,
                last_seen = excluded.last_seen
        """, (pct, label, v, rssi, wifi_strength, now_str))
        conn.commit()

def get_setting(key, default_val=""):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default_val

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_target_menu_data():
    forced_day = get_setting("forced_display_day", "AUTO")
    now_ist = datetime.now(IST)
    
    if forced_day != "AUTO":
        target_day = forced_day
        date_str = f"{target_day[:3].upper()}, {now_ist.strftime('%d %b').upper()}"
    else:
        if now_ist.hour >= 21:
            target_date = now_ist + timedelta(days=1)
        else:
            target_date = now_ist
        target_day = target_date.strftime("%A")
        date_str = target_date.strftime("%a, %d %b").upper()

    cuisine = get_setting("active_cuisine", "Maharashtrian")

    with get_db() as conn:
        row = conn.execute("SELECT * FROM weekly_menu WHERE day_name = ?", (target_day,)).fetchone()
        if row:
            data = {
                "day": row["day_name"],
                "cuisine": cuisine,
                "breakfast": (row["breakfast"] or "—").replace("+", ","),
                "lunch": (row["lunch"] or "—").replace("+", ","),
                "dinner": (row["dinner"] or "—").replace("+", ","),
                "task1": (row["task1"] or "—").replace("+", ","),
                "task2": (row["task2"] or "—").replace("+", ",")
            }
        else:
            data = {
                "day": target_day, "cuisine": cuisine,
                "breakfast": "—", "lunch": "—", "dinner": "—", "task1": "—", "task2": "—"
            }

    return date_str, data

# ============================================================================
# 4. REST APIS & TELEMETRY
# ============================================================================
@app.route('/hash', methods=['GET'])
def get_content_hash():
    global SYNC_VERSION
    date_str, data = get_target_menu_data()
    payload = f"{SYNC_VERSION}|{date_str}|{data['cuisine']}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    return jsonify({"hash": content_hash, "sync_version": SYNC_VERSION, "day": data['day']}), 200

@app.route('/api/telemetry', methods=['GET', 'POST', 'OPTIONS'])
def api_telemetry():
    if request.method == 'OPTIONS':
        return Response(status=200)

    if request.method == 'POST':
        try:
            p = request.get_json(force=True)
            pct = int(p.get("pct", 86))
            label = str(p.get("batt", "430d"))
            v = float(p.get("v", 4.10))
            rssi = int(p.get("rssi", -82))
            wifi_str = str(p.get("wifi_strength", "Good (2/3)"))
            
            update_telemetry_db(pct, label, v, rssi, wifi_str)
            return jsonify({"status": "ok"}), 200
        except Exception as e:
            return jsonify({"error": str(e)}), 400

    telem = get_telemetry()
    return jsonify({
        "status": "online",
        "battery_pct": telem["battery_pct"],
        "battery_label": telem["battery_label"],
        "voltage": telem["voltage"],
        "wifi_strength": telem["wifi_strength"],
        "rssi": telem["rssi"],
        "last_seen": telem["last_seen"]
    }), 200

@app.route('/api/menu', methods=['GET', 'POST', 'OPTIONS'])
def api_menu_handler():
    global SYNC_VERSION
    if request.method == 'OPTIONS':
        return Response(status=200)

    if request.method == 'GET':
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM weekly_menu").fetchall()
            cuisine = get_setting("active_cuisine", "Maharashtrian")
            diet = get_setting("active_diet", "VEG")
            return jsonify({
                "menu": [dict(ix) for ix in rows],
                "active_cuisine": cuisine,
                "active_diet": diet,
                "sync_version": SYNC_VERSION
            }), 200

    req = request.get_json(force=True)
    day = req.get("day_name")
    cuisine = req.get("cuisine")
    diet = req.get("diet")

    if cuisine: set_setting("active_cuisine", cuisine)
    if diet: set_setting("active_diet", diet)
    if day: set_setting("forced_display_day", day)

    with get_db() as conn:
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
            str(req.get("breakfast", "")).replace("+", ","),
            str(req.get("lunch", "")).replace("+", ","),
            str(req.get("dinner", "")).replace("+", ","),
            str(req.get("task1", "")).replace("+", ","),
            str(req.get("task2", "")).replace("+", ",")
        ))
        conn.commit()

    SYNC_VERSION += 1
    return jsonify({"status": "updated", "sync_version": SYNC_VERSION, "forced_day": day}), 200

# ============================================================================
# 5. HIGH-DENSITY SOLID STEM 1-BIT E-PAPER RENDERER
# ============================================================================
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        ensure_fonts()
        date_str, data = get_target_menu_data()
        telem = get_telemetry()

        rssi = int(request.args.get('rssi', telem["rssi"]))
        batt_pct = int(request.args.get('pct', telem["battery_pct"]))
        batt_str = str(request.args.get('batt', telem["battery_label"]))

        if 'rssi' in request.args or 'pct' in request.args:
            v = float(request.args.get('v', telem["voltage"]))
            wifi_lbl = "Excellent (3/3)" if rssi >= -65 else ("Good (2/3)" if rssi >= -78 else "Weak (1/3)")
            update_telemetry_db(batt_pct, batt_str, v, rssi, wifi_lbl)

        # 2x Master Canvas (800x600)
        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255)
        draw = ImageDraw.Draw(img_2x)

        f_logo = load_font("latin_bold", 15)
        f_cuisine = load_font("latin_bold", 10)
        f_date = load_font("latin_bold", 12)
        f_badge = load_font("latin_bold", 11)
        f_time = load_font("latin_bold", 11)
        f_cat = load_font("latin_bold", 10)
        f_dish = get_best_font(data["breakfast"] + data["lunch"] + data["dinner"], 13)
        f_task_hdr = load_font("latin_bold", 10)
        f_task_body = get_best_font(data["task1"] + data["task2"], 11)

        # --------------------------------------------------------------------
        # 1. FIXED 3-ZONE HEADER BAR (y: 0 to 36px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 0, CANVAS_W - 1, 36 * SCALE], fill=0)

        # Zone A: Left Logo
        draw.text((10 * SCALE, 10 * SCALE), "MealSync", font=f_logo, fill=255)

        # Zone B: Center Inline Cuisine & Date
        cuisine_label = f"• {data['cuisine'].upper()[:12]}"
        draw.text((88 * SCALE, 12 * SCALE), cuisine_label, font=f_cuisine, fill=200)

        date_x = 180 * SCALE
        draw.text((date_x, 10 * SCALE), date_str, font=f_date, fill=255)

        # Zone C: Right Hardware Telemetry
        # Wi-Fi Bars
        signal_bars = 3 if rssi >= -65 else (2 if rssi >= -78 else 1)
        wifiX, wifiY = 305 * SCALE, 12 * SCALE
        draw.rectangle([wifiX, wifiY + (8 * SCALE), wifiX + (2 * SCALE), wifiY + (12 * SCALE)], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + (4 * SCALE), wifiY + (5 * SCALE), wifiX + (6 * SCALE), wifiY + (12 * SCALE)], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + (8 * SCALE), wifiY + (2 * SCALE), wifiX + (10 * SCALE), wifiY + (12 * SCALE)], fill=255 if signal_bars >= 3 else 0)

        # Battery Days Label (e.g., "430d")
        b_lbl_w = get_text_width(f_badge, batt_str)
        bat_text_x = (364 * SCALE) - b_lbl_w
        draw.text((bat_text_x, 10 * SCALE), batt_str, font=f_badge, fill=255)

        # Battery Bar Icon
        batX, batY = 368 * SCALE, 11 * SCALE
        draw.rectangle([batX, batY, batX + (22 * SCALE), batY + (13 * SCALE)], outline=255, width=SCALE)
        draw.rectangle([batX + (22 * SCALE), batY + (3 * SCALE), batX + (24 * SCALE), batY + (10 * SCALE)], fill=255)
        fill_w = max(0, min(18 * SCALE, int((batt_pct / 100.0) * 18 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + (2 * SCALE), batY + (2 * SCALE), batX + (2 * SCALE) + fill_w, batY + (11 * SCALE)], fill=255)

        # --------------------------------------------------------------------
        # 2. BAUHAUS RAIL TIMELINE (y: 38 to 216px)
        # --------------------------------------------------------------------
        rail_x = 76 * SCALE
        draw.line([(rail_x, 42 * SCALE), (rail_x, 212 * SCALE)], fill=0, width=SCALE)

        def draw_cat_pill(label, x_1x, y_1x):
            tw = get_text_width(f_cat, label)
            draw.rectangle([x_1x * SCALE, y_1x * SCALE, (x_1x * SCALE) + tw + (8 * SCALE), (y_1x * SCALE) + (14 * SCALE)], fill=0)
            draw.text(((x_1x * SCALE) + (4 * SCALE), (y_1x * SCALE) + (1 * SCALE)), label, font=f_cat, fill=255)

        # --- BREAKFAST ---
        draw.text((8 * SCALE, 50 * SCALE), "08:30", font=f_time, fill=0)
        draw.text((8 * SCALE, 64 * SCALE), "AM", font=f_time, fill=0)
        draw.ellipse([rail_x - (3 * SCALE), 54 * SCALE, rail_x + (3 * SCALE), 60 * SCALE], fill=0)

        draw_cat_pill("BREAKFAST", 86, 44)
        draw_wrapped_text(draw, data["breakfast"], 86, 62, 304, f_dish, 16, max_lines=2, fill=0)
        draw.line([(86 * SCALE, 96 * SCALE), (392 * SCALE, 96 * SCALE)], fill=210, width=SCALE)

        # --- LUNCH ---
        draw.text((8 * SCALE, 106 * SCALE), "01:00", font=f_time, fill=0)
        draw.text((8 * SCALE, 120 * SCALE), "PM", font=f_time, fill=0)
        draw.ellipse([rail_x - (3 * SCALE), 110 * SCALE, rail_x + (3 * SCALE), 116 * SCALE], fill=0)

        draw_cat_pill("LUNCH", 86, 100)
        draw_wrapped_text(draw, data["lunch"], 86, 118, 304, f_dish, 16, max_lines=2, fill=0)
        draw.line([(86 * SCALE, 154 * SCALE), (392 * SCALE, 154 * SCALE)], fill=210, width=SCALE)

        # --- DINNER ---
        draw.text((8 * SCALE, 164 * SCALE), "08:30", font=f_time, fill=0)
        draw.text((8 * SCALE, 178 * SCALE), "PM", font=f_time, fill=0)
        draw.ellipse([rail_x - (3 * SCALE), 168 * SCALE, rail_x + (3 * SCALE), 174 * SCALE], fill=0)

        draw_cat_pill("DINNER", 86, 158)
        draw_wrapped_text(draw, data["dinner"], 86, 176, 304, f_dish, 16, max_lines=2, fill=0)

        # Section Divider
        draw.line([(0, 218 * SCALE), (CANVAS_W, 218 * SCALE)], fill=0, width=2 * SCALE)

        # --------------------------------------------------------------------
        # 3. DUAL-COLUMN TASK CARDS (y: 222 to 294px)
        # --------------------------------------------------------------------
        # Left Card: TODAY'S PREP
        draw.rectangle([6 * SCALE, 222 * SCALE, 196 * SCALE, 294 * SCALE], outline=0, width=SCALE)
        draw.rectangle([6 * SCALE, 222 * SCALE, 196 * SCALE, 238 * SCALE], fill=0)
        draw.text((10 * SCALE, 224 * SCALE), "TODAY'S PREP", font=f_task_hdr, fill=255)
        draw.rectangle([12 * SCALE, 246 * SCALE, 22 * SCALE, 256 * SCALE], outline=0, width=SCALE)
        draw_wrapped_text(draw, data["task1"], 26, 244, 166, f_task_body, 14, max_lines=3, fill=0)

        # Right Card: TOMORROW'S PREP
        draw.rectangle([202 * SCALE, 222 * SCALE, 394 * SCALE, 294 * SCALE], outline=0, width=SCALE)
        draw.rectangle([202 * SCALE, 222 * SCALE, 394 * SCALE, 238 * SCALE], fill=0)
        draw.text((206 * SCALE, 224 * SCALE), "TOMORROW'S PREP", font=f_task_hdr, fill=255)
        draw.rectangle([208 * SCALE, 246 * SCALE, 218 * SCALE, 256 * SCALE], outline=0, width=SCALE)
        draw_wrapped_text(draw, data["task2"], 222, 244, 168, f_task_body, 14, max_lines=3, fill=0)

        # Outer Frame
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)

        # Morphological Stem Darkening Filter to preserve thin ligatures & matras
        # Downscale via LANCZOS, then threshold with solid dense black preservation
        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=Image.LANCZOS)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 168 else 0, mode="1")

        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1':
            img_epd = ImageOps.invert(img_1bit.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        buf = io.BytesIO()
        img_1bit.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        traceback.print_exc()
        return f"Internal Error: {err}", 500

@app.route('/')
def home():
    telem = get_telemetry()
    html_page = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MealSync Cloud Service</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 30px; text-align: center; }}
            .card {{ background: #1e293b; max-width: 500px; margin: 0 auto; padding: 24px; border-radius: 16px; border: 1px solid #334155; }}
            .status {{ color: #10b981; font-weight: bold; font-size: 14px; margin-bottom: 12px; }}
            .badge {{ display: inline-block; background: #334155; padding: 6px 12px; border-radius: 8px; font-size: 13px; margin: 4px; }}
            img {{ max-width: 100%; border-radius: 12px; border: 2px solid #475569; margin-top: 16px; }}
            a {{ color: #38bdf8; text-decoration: none; font-weight: bold; }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>🍳 MealSync Cloud Engine</h2>
            <div class="status">● Active & Synchronized (Industrial HD 1-Bit Engine)</div>
            <div>
                <span class="badge">Battery: {telem['battery_label']} ({telem['battery_pct']}%)</span>
                <span class="badge">Wi-Fi: {telem['wifi_strength']}</span>
                <span class="badge">{telem['voltage']}V</span>
            </div>
            <p style="font-size: 12px; color: #94a3b8; margin-top: 12px;">Live 1-bit E-Paper Buffer Stream (SSD1683 / 400×300):</p>
            <img src="/display.bmp" alt="Live E-Paper Stream" />
            <div style="margin-top: 20px; font-size: 13px;">
                <a href="https://mealsync-hub.ai.studio/" target="_blank">Open MealSync Web Dashboard ➔</a>
            </div>
        </div>
    </body>
    </html>
    """
    return render_template_string(html_page)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
