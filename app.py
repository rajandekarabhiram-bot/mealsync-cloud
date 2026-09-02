import os
import io
import json
import sqlite3
import hashlib
import requests
import traceback
from datetime import datetime, timezone, timedelta
from flask import Flask, request, Response, send_file, render_template_string, jsonify
from PIL import Image, ImageDraw, ImageFont

app = Flask(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
DB_FILE = "mealsync.db"

PANEL_WIDTH = 400
PANEL_HEIGHT = 300

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
# 2. LOCKED FONT ASSET ENGINE
# ============================================================================
FONT_DOWNLOAD_URLS = {
    # System Chrome & Headers
    "Inter-ExtraBold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/inter/Inter-ExtraBold.ttf",
    # Content & Regional Fonts
    "JetBrainsMono-Bold.ttf": "https://raw.githubusercontent.com/JetBrains/JetBrainsMono/master/fonts/ttf/JetBrainsMono-Bold.ttf",
    "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf"
}

def verify_and_fetch_fonts():
    for filename, url in FONT_DOWNLOAD_URLS.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 5000:
            try:
                res = requests.get(url, timeout=15)
                if res.status_code == 200 and len(res.content) > 5000:
                    with open(filename, "wb") as f:
                        f.write(res.content)
            except Exception as e:
                print(f"[FONT ENGINE] Failed downloading {filename}: {e}")

verify_and_fetch_fonts()

def classify_script(char_str):
    for ch in str(char_str):
        if 0x0900 <= ord(ch) <= 0x097F:
            return "devanagari"
    return "latin"

def get_content_font(script_type, size_px):
    font_file = "NotoSansDevanagari-Bold.ttf" if script_type == "devanagari" else "JetBrainsMono-Bold.ttf"
    try:
        if os.path.exists(font_file) and os.path.getsize(font_file) > 5000:
            return ImageFont.truetype(font_file, int(round(size_px)))
    except Exception:
        pass
    return ImageFont.load_default()

def get_chrome_font(size_px):
    try:
        if os.path.exists("Inter-ExtraBold.ttf") and os.path.getsize("Inter-ExtraBold.ttf") > 5000:
            return ImageFont.truetype("Inter-ExtraBold.ttf", int(round(size_px)))
    except Exception:
        pass
    return ImageFont.load_default()

def measure_token(token, size_px):
    script = classify_script(token)
    font = get_content_font(script, size_px)
    try:
        bbox = font.getbbox(str(token))
        return (bbox[2] - bbox[0]), font
    except Exception:
        return len(str(token)) * int(size_px * 0.6), font

# ============================================================================
# 3. TEXT WRAPPING & UNIFORM SIZING ENGINE
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

def determine_uniform_font_size(text_items, max_w, max_h, target_size=18, min_size=13, max_allowed_lines=2):
    t_size = int(round(target_size))
    m_size = int(round(min_size))

    for s in range(t_size, m_size - 1, -1):
        line_height = int(s * 1.24)
        fits_all = True
        for item in text_items:
            lines = segment_and_wrap(item, max_w, s)
            if len(lines) > max_allowed_lines or (len(lines) * line_height) > max_h:
                fits_all = False
                break
        if fits_all:
            return s
    return m_size

def draw_uniform_text(draw, text, x, y, max_w, size_px, max_lines=2, fill=0):
    size_int = int(round(size_px))
    lines = segment_and_wrap(text, max_w, size_int)[:max_lines]
    line_height = int(size_int * 1.24)
    curr_y = y
    space_w, _ = measure_token(" ", size_int)

    for line in lines:
        curr_x = x
        for idx, (token, token_w) in enumerate(line):
            script = classify_script(token)
            font = get_content_font(script, size_int)
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
                ("Monday", "खमंग भाजणीचे थालीपीठ, लोणी (Thalipeeth)", "भरली वांगी, ज्वारीची भाकरी, वरण (Bharli Vangi)", "दाल तडका, जिरा राईस, कोशिंबीर (Dal Tadka)", "उद्याच्या उसळीसाठी मटकी भिजवून मोड आणणे", "डोसा/इडलीसाठी डाळ-तांदूळ भिजवून वाटून ठेवणे"),
                ("Tuesday", "मऊ लुसलुशीत पोहे, चहा (Kande Pohe)", "वरण भात, गव्हाची पोळी, भेंडी भाजी (Bhendi Bhaji)", "मूग डाळ मऊ खिचडी, कढी, पापड (Moong Khichdi)", "ताज्या पालेभाज्या स्वच्छ धुवून सुकवून ठेवणे", "सकाळचे ताजे दूध व्यवस्थित उकळवून साय काढणे"),
                ("Wednesday", "मऊ इडली, सांबार, खोबरे चटणी (Idli Sambar)", "मेथीची सुकी भाजी, पोळी, वरण भात (Methi Bhaji)", "मसाला भात, काकडी कोशिंबीर (Masala Bhaat)", "कोथिंबीर व हिरवी मिरची बारीक चिरून ठेवणे", "घरचे ताजे दही व्यवस्थित विरजण लावणे"),
                ("Thursday", "ગરમાગરમ પૌંઆ, મસાલા ચા (Poha Chai)", "ગુજરાતી દાળ, ભાત, રોટલી, શાક (Gujarati Thali)", "ખીચડી, કઢી, પાપડ, અથાણું (Khichdi Kadhi)", "લીલા શાકભાજી સમારીને ફ્રિજમાં રાખવા", "ઢોકળાનું ખીરું આથો લાવવા માટે મૂકવું"),
                ("Friday", "मेथी पराठा, ताजे दही (Methi Paratha)", "फ्लॉवर-बटाटा रस्सा भाजी, पोळी, भात (Cauliflower Curry)", "मसाला दाल खिचडी, साजूक तूप (Dal Khichdi)", "आले-लसूण पेस्ट ताज्या घटकांपासून तयार करणे", "चपातीचे पीठ मळून ओल्या कपड्यात ठेवणे"),
                ("Saturday", "झणझणीत मिसळ पाव, लिंबू (Misal Pav)", "पनीर बटर मसाला, जिरा राईस, पोळी (Paneer Masala)", "घरगुती पावभाजी, बटर पाव (Pav Bhaji)", "बटाटे उकडवून साल काढून मॅश करून ठेवणे", "भाजीसाठी कांदा आणि टोमॅटो बारीक कापणे"),
                ("Sunday", "कुरकुरीत डोसा, सांबार, चटणी (Crispy Dosa)", "पुरणपोळी, कटाची आमटी, भजी (Puran Poli)", "दही भात, जिरा तडका, लिंबू लोणचे (Curd Rice)", "सांबार मसाला खलबत्त्यात बारीक वाटून घेणे", "जाडे पोहे व्यवस्थित चाळून स्वच्छ करणे")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('sync_version', '1')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('forced_display_day', 'AUTO')")
        conn.execute("""
            INSERT OR IGNORE INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, 86, '430d', 4.10, -78, 'Good (2/3)', 'Online')
        """)
        conn.commit()

init_db()

def get_setting(key, default_val=""):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default_val

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, str(value)))
        conn.commit()

def increment_sync_version():
    cur = int(get_setting("sync_version", "1")) + 1
    set_setting("sync_version", cur)
    return cur

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
@app.route('/hash', methods=['GET', 'HEAD'])
def get_content_hash():
    sync_ver = get_setting("sync_version", "1")
    date_str, data = get_target_menu_data()
    payload = f"{sync_ver}|{date_str}|{data['cuisine']}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    
    response = jsonify({"hash": content_hash, "sync_version": int(sync_ver), "day": data['day']})
    response.headers["ETag"] = content_hash
    return response, 200

@app.route('/api/telemetry', methods=['GET', 'POST', 'OPTIONS'])
def api_telemetry():
    if request.method == 'OPTIONS':
        return Response(status=200)
    telem = get_telemetry()
    return jsonify({"status": "online", **telem}), 200

@app.route('/api/menu', methods=['GET', 'POST', 'OPTIONS'])
def api_menu_handler():
    if request.method == 'OPTIONS':
        return Response(status=200)
    if request.method == 'GET':
        with get_db() as conn:
            rows = conn.execute("SELECT * FROM weekly_menu").fetchall()
            return jsonify({
                "menu": [dict(ix) for ix in rows],
                "active_cuisine": get_setting("active_cuisine", "Maharashtrian"),
                "active_diet": get_setting("active_diet", "VEG"),
                "sync_version": int(get_setting("sync_version", "1"))
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

    new_ver = increment_sync_version()
    return jsonify({"status": "updated", "sync_version": new_ver, "forced_day": day}), 200

# ============================================================================
# 6. UNIFORM 1-BIT MONOCHROME E-PAPER RENDERER
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

        # Mode "1": Native 1-bit monochrome (0 = Black Ink, 1 = White Background)
        img = Image.new("1", (PANEL_WIDTH, PANEL_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Inter ExtraBold for System Chrome (Increased MealSync logo font size)
        f_logo = get_chrome_font(18)
        f_date = get_chrome_font(11)
        f_badge = get_chrome_font(10)
        f_cuisine_strip = get_chrome_font(9.5)
        f_cat = get_chrome_font(9.5)
        f_task_hdr = get_chrome_font(10)

        # --------------------------------------------------------------------
        # 1. TOP SYSTEM BAR (y: 0 to 28px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 0, PANEL_WIDTH - 1, 28], fill=0)
        draw.text((8, 4), "MealSync", font=f_logo, fill=1)

        d_bbox = f_date.getbbox(date_str)
        d_w = d_bbox[2] - d_bbox[0]
        draw.text(((PANEL_WIDTH - d_w) // 2, 8), date_str, font=f_date, fill=1)

        # Battery Box
        batX, batY = 368, 8
        draw.rectangle([batX, batY, batX + 22, batY + 12], outline=1, width=1)
        draw.rectangle([batX + 22, batY + 3, batX + 24, batY + 9], fill=1)
        fill_w = max(0, min(18, int((batt_pct / 100.0) * 18)))
        if fill_w > 0:
            draw.rectangle([batX + 2, batY + 2, batX + 2 + fill_w, batY + 10], fill=1)

        b_bbox = f_badge.getbbox(batt_str)
        b_lbl_w = b_bbox[2] - b_bbox[0]
        bat_text_x = batX - b_lbl_w - 5
        draw.text((bat_text_x, 8), batt_str, font=f_badge, fill=1)

        # Wi-Fi 3-Bar Indicator
        signal_bars = 3 if rssi >= -65 else (2 if rssi >= -78 else 1)
        wifiX, wifiY = bat_text_x - 16, 8
        draw.rectangle([wifiX, wifiY + 7, wifiX + 2, wifiY + 11], fill=1 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 4, wifiY + 4, wifiX + 6, wifiY + 11], fill=1 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 8, wifiY + 1, wifiX + 10, wifiY + 11], fill=1 if signal_bars >= 3 else 0)

        # --------------------------------------------------------------------
        # 2. CUISINE SUB-HEADER STRIP (y: 28 to 44px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 28, PANEL_WIDTH - 1, 44], fill=0)
        cuisine_full = f"CUISINE: {data['cuisine'].upper()}"
        draw.text((8, 30), cuisine_full, font=f_cuisine_strip, fill=1)

        # --------------------------------------------------------------------
        # 3. UNIFORM MEALS SECTION (y: 46 to 222px)
        # --------------------------------------------------------------------
        rail_x = 16
        draw.line([(rail_x, 52), (rail_x, 208)], fill=0, width=1)

        uniform_meal_font_size = determine_uniform_font_size(
            [data["breakfast"], data["lunch"], data["dinner"]],
            max_w=364,
            max_h=37,
            target_size=18,
            min_size=14,
            max_allowed_lines=2
        )

        def draw_meal_slot(category, dish_text, y_start, dot_y, row_h):
            draw.ellipse([rail_x - 3, dot_y - 3, rail_x + 3, dot_y + 3], fill=0)

            c_bbox = f_cat.getbbox(category)
            cat_w = c_bbox[2] - c_bbox[0]
            draw.rectangle([28, y_start, 28 + cat_w + 8, y_start + 14], fill=0)
            draw.text((28 + 4, y_start + 1), category, font=f_cat, fill=1)

            draw_uniform_text(draw, dish_text, 28, y_start + 17, 364, uniform_meal_font_size, max_lines=2, fill=0)
            
            div_y = y_start + row_h
            draw.line([(28, div_y), (PANEL_WIDTH - 8, div_y)], fill=0, width=1)

        draw_meal_slot("BREAKFAST", data["breakfast"], 48, 54, 54)
        draw_meal_slot("LUNCH", data["lunch"], 106, 112, 54)
        draw_meal_slot("DINNER", data["dinner"], 164, 170, 54)

        draw.line([(0, 222), (PANEL_WIDTH, 222)], fill=0, width=2)

        # --------------------------------------------------------------------
        # 4. UNIFORM PREP CARDS (y: 226 to 294px)
        # --------------------------------------------------------------------
        uniform_task_font_size = determine_uniform_font_size(
            [data["task1"], data["task2"]],
            max_w=166,
            max_h=45,
            target_size=14,
            min_size=11,
            max_allowed_lines=3
        )

        # Left Card: TODAY'S PREP
        draw.rectangle([6, 226, 196, 294], outline=0, width=1)
        draw.rectangle([6, 226, 196, 241], fill=0)
        draw.text((10, 227), "TODAY'S PREP", font=f_task_hdr, fill=1)
        draw.rectangle([12, 248, 22, 258], outline=0, width=1)
        draw_uniform_text(draw, data["task1"], 26, 245, 166, uniform_task_font_size, max_lines=3, fill=0)

        # Right Card: TOMORROW'S PREP
        draw.rectangle([202, 226, 394, 294], outline=0, width=1)
        draw.rectangle([202, 226, 394, 241], fill=0)
        draw.text((206, 227), "TOMORROW'S PREP", font=f_task_hdr, fill=1)
        draw.rectangle([208, 248, 218, 258], outline=0, width=1)
        draw_uniform_text(draw, data["task2"], 222, 245, 168, uniform_task_font_size, max_lines=3, fill=0)

        # Outer perimeter frame
        draw.rectangle([0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1], outline=0, width=2)

        # Active-High packed byte format (1 = Black Ink for SSD1683)
        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1':
            img_epd = img.point(lambda p: 0 if p else 1, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        # Browser preview
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
        <title>MealSync Production Stream</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 30px; text-align: center; }
            .card { background: #1e293b; max-width: 500px; margin: 0 auto; padding: 24px; border-radius: 16px; border: 1px solid #334155; }
            img { max-width: 100%; border-radius: 8px; margin-top: 16px; border: 1px solid #475569; }
            .badge { display: inline-block; background: #334155; padding: 6px 12px; border-radius: 8px; font-size: 13px; margin: 4px; }
        </style>
    </head>
    <body>
        <div class="card">
            <h2>🍳 MealSync Production Display</h2>
            <div>
                <span class="badge">Battery: {{ telem['battery_label'] }} ({{ telem['battery_pct'] }}%)</span>
                <span class="badge">Wi-Fi: {{ telem['wifi_strength'] }}</span>
                <span class="badge">{{ telem['voltage'] }}V</span>
            </div>
            <img src="/display.bmp" alt="Live E-Paper Stream" />
        </div>
    </body>
    </html>
    """, telem=telem)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
