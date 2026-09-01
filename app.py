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
# 2. BULLETPROOF NATIVE 1-BIT FONT ENGINE
# ============================================================================
FONT_FILES = {
    "latin_bold": "DejaVuSans-Bold.ttf",
    "latin_regular": "DejaVuSans.ttf",
    "devanagari_bold": "NotoSansDevanagari-Bold.ttf",
    "gujarati_bold": "NotoSansGujarati-Bold.ttf"
}

FONT_URLS = {
    "DejaVuSans-Bold.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Bold.ttf",
    "DejaVuSans.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf",
    "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf",
    "NotoSansGujarati-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansGujarati/NotoSansGujarati-Bold.ttf"
}

def verify_and_fetch_fonts():
    for filename, url in FONT_URLS.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 5000:
            try:
                res = requests.get(url, timeout=15)
                if res.status_code == 200 and len(res.content) > 5000:
                    with open(filename, "wb") as f:
                        f.write(res.content)
            except Exception as e:
                print(f"[FONT DOWNLOAD ERROR] {filename}: {e}")

verify_and_fetch_fonts()

def classify_script(char_str):
    for ch in str(char_str):
        cp = ord(ch)
        if 0x0900 <= cp <= 0x097F:
            return "devanagari_bold"
        elif 0x0A80 <= cp <= 0x0AFF:
            return "gujarati_bold"
    return "latin_bold"

def get_font_instance(script_key, size_px):
    font_file = FONT_FILES.get(script_key, "DejaVuSans-Bold.ttf")
    try:
        if os.path.exists(font_file) and os.path.getsize(font_file) > 5000:
            return ImageFont.truetype(font_file, int(size_px))
    except Exception:
        pass
    system_fallbacks = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"
    ]
    for sys_font in system_fallbacks:
        if os.path.exists(sys_font):
            try:
                return ImageFont.truetype(sys_font, int(size_px))
            except Exception:
                pass
    return ImageFont.load_default()

def measure_token(token, size_px):
    script = classify_script(token)
    font = get_font_instance(script, size_px)
    try:
        bbox = font.getbbox(token)
        return (bbox[2] - bbox[0]), font
    except Exception:
        return len(token) * int(size_px * 0.6), font

# ============================================================================
# 3. DIRECT 1-BIT MULTILINGUAL TEXT WRAPPER
# ============================================================================
def segment_and_wrap(text, max_w, size_px):
    tokens = str(text).strip().split()
    if not tokens:
        return []
    space_w, _ = measure_token(" ", size_px)
    lines = []
    current_line = []
    current_w = 0

    for token in tokens:
        w, _ = measure_token(token, size_px)
        if current_line:
            if current_w + space_w + w <= max_w:
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

def draw_native_multilingual_text(draw, text, x, y, max_w, max_h, max_size=15, min_size=11, max_lines=2, fill=0):
    text = str(text).strip()
    if not text:
        return

    best_size = min_size
    best_lines = []

    for s in range(max_size, min_size - 1, -1):
        test_lines = segment_and_wrap(text, max_w, s)
        line_height = int(s * 1.30)
        if len(test_lines) <= max_lines and (len(test_lines) * line_height) <= max_h:
            best_size = s
            best_lines = test_lines
            break

    if not best_lines:
        best_lines = segment_and_wrap(text, max_w, min_size)[:max_lines]
        best_size = min_size

    line_height = int(best_size * 1.30)
    curr_y = y
    space_w, _ = measure_token(" ", best_size)

    for line in best_lines:
        curr_x = x
        for idx, (token, token_w) in enumerate(line):
            script = classify_script(token)
            font = get_font_instance(script, best_size)
            draw.text((curr_x, curr_y), token, font=font, fill=fill)
            curr_x += token_w
            if idx < len(line) - 1:
                curr_x += space_w
        curr_y += line_height

# ============================================================================
# 4. DATABASE & STATE MANAGEMENT
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
        target_date = now_ist + timedelta(days=1) if now_ist.hour >= 21 else now_ist
        target_day = target_date.strftime("%A")
        date_str = target_date.strftime("%a, %d %b %Y").upper()

    cuisine = get_setting("active_cuisine", "Maharashtrian")
    with get_db() as conn:
        row = conn.execute("SELECT * FROM weekly_menu WHERE day_name = ?", (target_day,)).fetchone()
        if row:
            data = {
                "day": row["day_name"], "cuisine": cuisine,
                "breakfast": (row["breakfast"] or "—").replace("+", ","),
                "lunch": (row["lunch"] or "—").replace("+", ","),
                "dinner": (row["dinner"] or "—").replace("+", ","),
                "task1": (row["task1"] or "—").replace("+", ","),
                "task2": (row["task2"] or "—").replace("+", ",")
            }
        else:
            data = {"day": target_day, "cuisine": cuisine, "breakfast": "—", "lunch": "—", "dinner": "—", "task1": "—", "task2": "—"}
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
    return jsonify({"status": "online", **telem}), 200

@app.route('/api/menu', methods=['GET', 'POST', 'OPTIONS'])
def api_menu_handler():
    global SYNC_VERSION
    if request.method == 'OPTIONS':
        return Response(status=200)
    if request.method == 'GET':
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM weekly_menu").fetchall()
            return jsonify({
                "menu": [dict(ix) for ix in rows],
                "active_cuisine": get_setting("active_cuisine", "Maharashtrian"),
                "active_diet": get_setting("active_diet", "VEG"),
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
                breakfast = excluded.breakfast, lunch = excluded.lunch,
                dinner = excluded.dinner, task1 = excluded.task1, task2 = excluded.task2
        """, (day, str(req.get("breakfast", "")).replace("+", ","),
              str(req.get("lunch", "")).replace("+", ","),
              str(req.get("dinner", "")).replace("+", ","),
              str(req.get("task1", "")).replace("+", ","),
              str(req.get("task2", "")).replace("+", ",")))
        conn.commit()

    SYNC_VERSION += 1
    return jsonify({"status": "updated", "sync_version": SYNC_VERSION, "forced_day": day}), 200

# ============================================================================
# 6. ENLARGED 1-BIT MONOCHROME E-PAPER RENDERER (400x300 Matrix)
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

        # Native 1-bit Canvas: 1 = White background, 0 = Black ink
        img = Image.new("1", (PANEL_WIDTH, PANEL_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Increased Font Base Sizes
        f_logo = get_font_instance("latin_bold", 16)
        f_date = get_font_instance("latin_bold", 13)
        f_badge = get_font_instance("latin_bold", 12)
        f_cuisine_strip = get_font_instance("latin_bold", 11)
        f_cat = get_font_instance("latin_bold", 11)
        f_task_hdr = get_font_instance("latin_bold", 11.5)

        # --------------------------------------------------------------------
        # 1. ENLARGED TOP HEADER (y: 0 to 32px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 0, PANEL_WIDTH - 1, 32], fill=0)
        draw.text((8, 8), "MealSync", font=f_logo, fill=1)

        d_w, _ = measure_token(date_str, 13)
        draw.text(((PANEL_WIDTH - d_w) // 2, 9), date_str, font=f_date, fill=1)

        # Enlarged Battery Icon + Days Text + Thick Wi-Fi
        batX, batY = 364, 9
        draw.rectangle([batX, batY, batX + 26, batY + 14], outline=1, width=1)
        draw.rectangle([batX + 26, batY + 4, batX + 28, batY + 10], fill=1)
        fill_w = max(0, min(22, int((batt_pct / 100.0) * 22)))
        if fill_w > 0:
            draw.rectangle([batX + 2, batY + 2, batX + 2 + fill_w, batY + 12], fill=1)

        b_lbl_w, _ = measure_token(batt_str, 12)
        bat_text_x = batX - b_lbl_w - 6
        draw.text((bat_text_x, 8), batt_str, font=f_badge, fill=1)

        signal_bars = 3 if rssi >= -65 else (2 if rssi >= -78 else 1)
        wifiX, wifiY = bat_text_x - 20, 9
        draw.rectangle([wifiX, wifiY + 9, wifiX + 3, wifiY + 14], fill=1 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 5, wifiY + 5, wifiX + 8, wifiY + 14], fill=1 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 10, wifiY + 0, wifiX + 13, wifiY + 14], fill=1 if signal_bars >= 3 else 0)

        # --------------------------------------------------------------------
        # 2. CUISINE SUB-HEADER STRIP (y: 32 to 50px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 32, PANEL_WIDTH - 1, 50], fill=0)
        cuisine_full = f"CUISINE: {data['cuisine'].upper()}"
        draw.text((8, 35), cuisine_full, font=f_cuisine_strip, fill=1)

        # --------------------------------------------------------------------
        # 3. COMPACT TIMELINE RAIL (x = 16px) & MEAL ROWS (y: 52 to 226px)
        # --------------------------------------------------------------------
        rail_x = 16
        draw.line([(rail_x, 58), (rail_x, 214)], fill=0, width=1)

        def draw_meal_slot(category, dish_text, y_start, dot_y, row_h):
            # Timeline Dot Node
            draw.ellipse([rail_x - 3, dot_y - 3, rail_x + 3, dot_y + 3], fill=0)

            # Solid Black Inverted Category Badge (Enlarged)
            cat_w, _ = measure_token(category, 11)
            draw.rectangle([28, y_start, 28 + cat_w + 10, y_start + 16], fill=0)
            draw.text((28 + 5, y_start + 1), category, font=f_cat, fill=1)

            # Native Multilingual Text Rendering (Auto-scaling 15.5px to 11px)
            draw_native_multilingual_text(draw, dish_text, 28, y_start + 20, 364, row_h - 22, max_size=15.5, min_size=11, max_lines=2, fill=0)
            
            # Row Divider Line
            div_y = y_start + row_h
            draw.line([(28, div_y), (PANEL_WIDTH - 8, div_y)], fill=0, width=1)

        draw_meal_slot("BREAKFAST", data["breakfast"], 52, 60, 58)
        draw_meal_slot("LUNCH", data["lunch"], 110, 118, 58)
        draw_meal_slot("DINNER", data["dinner"], 168, 176, 58)

        # Section Divider before Tasks
        draw.line([(0, 226), (PANEL_WIDTH, 226)], fill=0, width=2)

        # --------------------------------------------------------------------
        # 4. DUAL-COLUMN TASK CARDS (y: 230 to 296px)
        # --------------------------------------------------------------------
        # Left Card: TODAY'S PREP
        draw.rectangle([6, 230, 196, 296], outline=0, width=1)
        draw.rectangle([6, 230, 196, 247], fill=0)
        draw.text((10, 232), "TODAY'S PREP", font=f_task_hdr, fill=1)
        draw.rectangle([12, 254, 22, 264], outline=0, width=1)
        draw_native_multilingual_text(draw, data["task1"], 26, 251, 166, 42, max_size=12.5, min_size=10, max_lines=3, fill=0)

        # Right Card: TOMORROW'S PREP
        draw.rectangle([202, 230, 394, 296], outline=0, width=1)
        draw.rectangle([202, 230, 394, 247], fill=0)
        draw.text((206, 232), "TOMORROW'S PREP", font=f_task_hdr, fill=1)
        draw.rectangle([208, 254, 218, 264], outline=0, width=1)
        draw_native_multilingual_text(draw, data["task2"], 222, 251, 168, 42, max_size=12.5, min_size=10, max_lines=3, fill=0)

        # Perimeter Frame
        draw.rectangle([0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1], outline=0, width=2)

        # Correct Byte Alignment for SSD1683 GxEPD2 driver
        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1':
            img_epd = ImageOps.invert(img.convert("L")).point(lambda p: 255 if p > 128 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        buf = io.BytesIO()
        img.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        traceback.print_exc()
        return f"Internal Error: {err}", 500

@app.route('/')
def home():
    telem = get_telemetry()
    return render_template_string("""
    <!DOCTYPE html>
    <html>
    <head>
        <title>MealSync Cloud Service</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: sans-serif; background: #0f172a; color: #f8fafc; padding: 30px; text-align: center; }
            .card { background: #1e293b; max-width: 500px; margin: 0 auto; padding: 24px; border-radius: 16px; }
            img { max-width: 100%; border-radius: 8px; margin-top: 16px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>🍳 MealSync Cloud Engine</h2>
            <p>1-Bit Native Multi-Script Rasterizer (SSD1683 / 400×300):</p>
            <img src="/display.bmp" alt="Live E-Paper Stream" />
        </div>
    </body>
    </html>
    """)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
