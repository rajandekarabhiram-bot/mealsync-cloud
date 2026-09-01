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
# 1. CORS & HEADERS
# ============================================================================
@app.after_request
def add_cors_and_cache_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS, HEAD"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Requested-With"
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response

# ============================================================================
# 2. BULLETPROOF UNIVERSAL FONT LOADER (Latin + Devanagari + Gujarati)
# ============================================================================
FONT_STORAGE = {
    "latin": "DejaVuSans-Bold.ttf",
    "devanagari": "NotoSansDevanagari-Bold.ttf",
    "gujarati": "NotoSansGujarati-Bold.ttf"
}

FONT_DOWNLOAD_URLS = {
    "DejaVuSans-Bold.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Bold.ttf",
    "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf",
    "NotoSansGujarati-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansGujarati/NotoSansGujarati-Bold.ttf"
}

def verify_and_fetch_fonts():
    for filename, url in FONT_DOWNLOAD_URLS.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 5000:
            try:
                res = requests.get(url, timeout=20)
                if res.status_code == 200 and len(res.content) > 5000:
                    with open(filename, "wb") as f:
                        f.write(res.content)
            except Exception as e:
                print(f"[FONT ENGINE] Error downloading {filename}: {e}")

verify_and_fetch_fonts()

def classify_script(char_str):
    for ch in char_str:
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            return "devanagari"
        elif 0x0A80 <= cp <= 0x0AFF:
            return "gujarati"
    return "latin"

def get_font_instance(script_key, size_1x):
    font_file = FONT_STORAGE.get(script_key, "DejaVuSans-Bold.ttf")
    target_px = int(size_1x * SCALE)
    try:
        if os.path.exists(font_file) and os.path.getsize(font_file) > 5000:
            return ImageFont.truetype(font_file, target_px)
    except Exception:
        pass
    try:
        if os.path.exists("DejaVuSans-Bold.ttf"):
            return ImageFont.truetype("DejaVuSans-Bold.ttf", target_px)
    except Exception:
        pass
    return ImageFont.load_default()

def measure_token(token, size_1x):
    script = classify_script(token)
    font = get_font_instance(script, size_1x)
    try:
        bbox = font.getbbox(token)
        return (bbox[2] - bbox[0]), font
    except Exception:
        return len(token) * int(size_1x * SCALE * 0.6), font

# ============================================================================
# 3. ADVANCED TOKENIZED MULTI-SCRIPT LINE WRAPPER & AUTO-FIT
# ============================================================================
def segment_and_wrap(text, max_w_px, size_1x):
    tokens = text.strip().split()
    if not tokens:
        return []
    
    space_w, _ = measure_token(" ", size_1x)
    lines = []
    current_line = []
    current_w = 0

    for token in tokens:
        w, _ = measure_token(token, size_1x)
        if current_line:
            if current_w + space_w + w <= max_w_px:
                current_line.append((token, w))
                current_w += space_w + w
            else:
                lines.append(current_line)
                current_line = [(token, w)]
                current_w = w
        else:
            current_line = [(token, w)]
            current_w = w

    if current_line:
        lines.append(current_line)
    return lines

def draw_multilingual_text(draw, text, x_1x, y_1x, max_w_1x, max_h_1x, max_size=17, min_size=11, max_lines=2, fill=0):
    text = str(text).strip()
    if not text:
        return

    max_w_px = max_w_1x * SCALE
    max_h_px = max_h_1x * SCALE
    best_size = min_size
    best_lines = []

    for s in range(max_size, min_size - 1, -1):
        test_lines = segment_and_wrap(text, max_w_px, s)
        line_height = int(s * SCALE * 1.28)
        if len(test_lines) <= max_lines and (len(test_lines) * line_height) <= max_h_px:
            best_size = s
            best_lines = test_lines
            break

    if not best_lines:
        best_lines = segment_and_wrap(text, max_w_px, min_size)[:max_lines]
        best_size = min_size

    line_height = int(best_size * SCALE * 1.28)
    curr_y = y_1x * SCALE
    space_w, _ = measure_token(" ", best_size)

    for line in best_lines:
        curr_x = x_1x * SCALE
        for idx, (token, token_w) in enumerate(line):
            script = classify_script(token)
            font = get_font_instance(script, best_size)
            draw.text((curr_x, curr_y), token, font=font, fill=fill)
            curr_x += token_w
            if idx < len(line) - 1:
                curr_x += space_w
        curr_y += line_height

# ============================================================================
# 4. DATABASE & DATA ACCESS
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
                ("Monday", "खमंग भाजणीचे थालीपीठ, लोणी (Thalipeeth)", "भरली वांगी, ज्वारीची भाकरी, वरण (Bharli Vangi, Bhakri)", "दाल तडका, जिरा राईस, कोशिंबीर (Dal Tadka, Jeera Rice)", "उद्याच्या उसळीसाठी मटकी/मूग भिजवणे", "डोसा/इडलीसाठी डाळ-तांदूळ भिजवून वाटणे"),
                ("Tuesday", "मऊ लुसलुशीत पोहे, चहा (Kande Pohe)", "वरण भात, गव्हाची पोळी, भेंडी भाजी (Varan Bhaat, Bhendi)", "मूग डाळ मऊ खिचडी, कढी, पापड (Moong Khichdi, Kadhi)", "ताज्या पालेभाज्या धुवून सुकवणे", "सकाळचे दूध व्यवस्थित उकळणे"),
                ("Wednesday", "मऊ इडली, सांबार, खोबरे चटणी (Idli Sambar)", "मेथीची सुकी भाजी, पोळी, वरण भात (Methi Bhaji, Poli)", "मसाला भात, काकडी कोशिंबीर (Masala Bhaat, Koshimbir)", "कोथिंबीर व हिरवी मिरची बारीक चिरणे", "घरचे ताजे दही विरजण लावणे"),
                ("Thursday", "ગરમાગરમ પૌંઆ, મસાલા ચા (Poha Chai)", "ગુજરાતી દાળ, ભાત, રોટલી, શાક (Gujarati Thali)", "ખીચડી, કઢી, પાપડ, અથાણું (Khichdi Kadhi)", "લીલા શાકભાજી સમારીને રાખવા", "ઢોકળાનું ખીરું આથો લાવવા મૂકવું"),
                ("Friday", "मेथी पराठा, ताजे दही (Methi Paratha)", "फ्लॉवर-बटाटा रस्सा भाजी, पोळी, भात (Cauliflower Curry)", "मसाला दाल खिचडी, साजूक तूप (Dal Khichdi, Ghee)", "आले-लसूण पेस्ट तयार करून ठेवणे", "चपातीचे पीठ मळून ठेवणे"),
                ("Saturday", "झणझणीत मिसळ पाव, लिंबू (Misal Pav)", "पनीर बटर मसाला, जिरा राईस, पोळी (Paneer Masala)", "घरगुती पावभाजी, बटर पाव (Pav Bhaji, Butter Pav)", "बटाटे उकडवून सोलून ठेवणे", "भाजीसाठी कांदा-टोमॅटो बारीक कापणे"),
                ("Sunday", "कुरकुरीत डोसा, सांबार, चटणी (Crispy Dosa)", "पुरणपोळी, कटाची आमटी, भजी (Puran Poli, Katachi Amti)", "दही भात, जिरा तडका, लिंबू लोणचे (Curd Rice)", "सांबार मसाला बारीक वाटून घेणे", "पोहे चाळून स्वच्छ करणे")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('forced_display_day', 'AUTO')")
        conn.execute("""
            INSERT OR IGNORE INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, 86, '430d', 4.10, -78, 'Good (2/3)', 'Online')
        """)
        conn.commit()

init_db()

def get_telemetry():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM device_telemetry WHERE id = 1").fetchone()
        if row:
            return dict(row)
        return {"battery_pct": 86, "battery_label": "430d", "voltage": 4.10, "rssi": -78, "wifi_strength": "Good (2/3)", "last_seen": "Online"}

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
        date_str = f"{target_day[:3].upper()}, {now_ist.strftime('%d %b %Y').upper()}"
    else:
        if now_ist.hour >= 21:
            target_date = now_ist + timedelta(days=1)
        else:
            target_date = now_ist
        target_day = target_date.strftime("%A")
        date_str = target_date.strftime("%a, %d %b %Y").upper()

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
# 5. REST APIS & HARDWARE SYNC
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
            rssi = int(p.get("rssi", -78))
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
# 6. E-PAPER RASTER ENGINE (Option 1 Layout: 400x300 Matrix)
# ============================================================================
@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        verify_and_fetch_fonts()
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

        f_logo = get_font_instance("latin", 14)
        f_date = get_font_instance("latin", 11)
        f_badge = get_font_instance("latin", 10.5)
        f_cuisine_strip = get_font_instance("latin", 9.5)
        f_cat = get_font_instance("latin", 10)
        f_task_hdr = get_font_instance("latin", 10)

        # --------------------------------------------------------------------
        # 1. TOP HEADER (y: 0 to 28px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 0, CANVAS_W - 1, 28 * SCALE], fill=0)

        # Left: Brand Logo
        draw.text((8 * SCALE, 7 * SCALE), "MealSync", font=f_logo, fill=255)

        # Center: Date
        d_w, _ = measure_token(date_str, 11)
        date_x = (CANVAS_W - d_w) // 2
        draw.text((date_x, 8 * SCALE), date_str, font=f_date, fill=255)

        # Right: Hardware Telemetry
        batX, batY = 368 * SCALE, 8 * SCALE
        draw.rectangle([batX, batY, batX + (22 * SCALE), batY + (12 * SCALE)], outline=255, width=SCALE)
        draw.rectangle([batX + (22 * SCALE), batY + (3 * SCALE), batX + (24 * SCALE), batY + (9 * SCALE)], fill=255)
        fill_w = max(0, min(18 * SCALE, int((batt_pct / 100.0) * 18 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + (2 * SCALE), batY + (2 * SCALE), batX + (2 * SCALE) + fill_w, batY + (10 * SCALE)], fill=255)

        b_lbl_w, _ = measure_token(batt_str, 10.5)
        bat_text_x = batX - b_lbl_w - (5 * SCALE)
        draw.text((bat_text_x, 8 * SCALE), batt_str, font=f_badge, fill=255)

        signal_bars = 3 if rssi >= -65 else (2 if rssi >= -78 else 1)
        wifiX, wifiY = bat_text_x - (16 * SCALE), 8 * SCALE
        draw.rectangle([wifiX, wifiY + (7 * SCALE), wifiX + (2 * SCALE), wifiY + (11 * SCALE)], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + (4 * SCALE), wifiY + (4 * SCALE), wifiX + (6 * SCALE), wifiY + (11 * SCALE)], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + (8 * SCALE), wifiY + (1 * SCALE), wifiX + (10 * SCALE), wifiY + (11 * SCALE)], fill=255 if signal_bars >= 3 else 0)

        # --------------------------------------------------------------------
        # 2. CUISINE SUB-HEADER STRIP (y: 28 to 44px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 28 * SCALE, CANVAS_W - 1, 44 * SCALE], fill=40)
        cuisine_full = f"CUISINE: {data['cuisine'].upper()}"
        draw.text((8 * SCALE, 30 * SCALE), cuisine_full, font=f_cuisine_strip, fill=255)

        # --------------------------------------------------------------------
        # 3. COMPACT TIMELINE RAIL (x = 16px) & MEALS (y: 46 to 222px)
        # --------------------------------------------------------------------
        rail_x = 16 * SCALE
        draw.line([(rail_x, 52 * SCALE), (rail_x, 208 * SCALE)], fill=0, width=SCALE)

        def draw_meal_slot(category, dish_text, y_start, dot_y, row_h):
            # Timeline Dot Node
            draw.ellipse([rail_x - (3 * SCALE), (dot_y - 3) * SCALE, rail_x + (3 * SCALE), (dot_y + 3) * SCALE], fill=0)

            # Solid Category Tag Pill
            cat_w, _ = measure_token(category, 10)
            draw.rectangle([28 * SCALE, y_start * SCALE, (28 * SCALE) + cat_w + (8 * SCALE), (y_start * SCALE) + (14 * SCALE)], fill=0)
            draw.text(((28 * SCALE) + (4 * SCALE), (y_start * SCALE) + (1 * SCALE)), category, font=f_cat, fill=255)

            # Multilingual Tokenized Text (No Tofu Boxes)
            draw_multilingual_text(draw, dish_text, 28, y_start + 17, 364, row_h - 19, max_size=16, min_size=11, max_lines=2, fill=0)
            
            div_y = y_start + row_h
            draw.line([(28 * SCALE, div_y * SCALE), ((PANEL_WIDTH - 8) * SCALE, div_y * SCALE)], fill=210, width=SCALE)

        draw_meal_slot("BREAKFAST", data["breakfast"], 48, 54, 54)
        draw_meal_slot("LUNCH", data["lunch"], 106, 112, 54)
        draw_meal_slot("DINNER", data["dinner"], 164, 170, 54)

        # Section Divider before Tasks
        draw.line([(0, 222 * SCALE), (CANVAS_W, 222 * SCALE)], fill=0, width=2 * SCALE)

        # --------------------------------------------------------------------
        # 4. DUAL-COLUMN TASK CARDS (y: 226 to 294px)
        # --------------------------------------------------------------------
        # Left Card: TODAY'S PREP
        draw.rectangle([6 * SCALE, 226 * SCALE, 196 * SCALE, 294 * SCALE], outline=0, width=SCALE)
        draw.rectangle([6 * SCALE, 226 * SCALE, 196 * SCALE, 241 * SCALE], fill=0)
        draw.text((10 * SCALE, 227 * SCALE), "TODAY'S PREP", font=f_task_hdr, fill=255)
        draw.rectangle([12 * SCALE, 248 * SCALE, 22 * SCALE, 258 * SCALE], outline=0, width=SCALE)
        draw_multilingual_text(draw, data["task1"], 26, 245, 166, 46, max_size=13, min_size=10, max_lines=3, fill=0)

        # Right Card: TOMORROW'S PREP
        draw.rectangle([202 * SCALE, 226 * SCALE, 394 * SCALE, 294 * SCALE], outline=0, width=SCALE)
        draw.rectangle([202 * SCALE, 226 * SCALE, 394 * SCALE, 241 * SCALE], fill=0)
        draw.text((206 * SCALE, 227 * SCALE), "TOMORROW'S PREP", font=f_task_hdr, fill=255)
        draw.rectangle([208 * SCALE, 248 * SCALE, 218 * SCALE, 258 * SCALE], outline=0, width=SCALE)
        draw_multilingual_text(draw, data["task2"], 222, 245, 168, 46, max_size=13, min_size=10, max_lines=3, fill=0)

        # Perimeter Frame
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)

        # High-order Lanczos Downscale to 400x300 & Contrast Binarization
        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=Image.LANCZOS)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 155 else 0, mode="1")

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
            <div class="status">● Active & Synchronized</div>
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
