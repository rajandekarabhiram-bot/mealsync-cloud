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
# 2. CRISP FONT ENGINE FOR 1-BIT E-PAPER
# ============================================================================
FONT_FILES = {
    "latin_bold": "DejaVuSans-Bold.ttf",
    "latin_regular": "DejaVuSans.ttf",
    "devanagari": "NotoSansDevanagari-Bold.ttf",
    "regional_fallback": "NotoSans-Bold.ttf"
}

def ensure_fonts():
    font_urls = {
        "DejaVuSans-Bold.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans-Bold.ttf",
        "DejaVuSans.ttf": "https://raw.githubusercontent.com/dejavu-fonts/dejavu-fonts/master/ttf/DejaVuSans.ttf",
        "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf",
        "NotoSans-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSans/NotoSans-Bold.ttf"
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

def load_font(font_key, size):
    filename = FONT_FILES.get(font_key, "DejaVuSans-Bold.ttf")
    try:
        if os.path.exists(filename) and os.path.getsize(filename) > 2000:
            return ImageFont.truetype(filename, size)
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

def get_best_font(text, size, bold=True):
    if is_devanagari(text):
        return load_font("devanagari", size) or load_font("latin_bold", size)
    return load_font("latin_bold" if bold else "latin_regular", size)

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
                ("Monday", "Pohe, Masala Chai, Koshimbir", "Varan Bhaat, Poli, Bhendi Bhaji, Mattha", "Moong Khichdi, Kadhi, Roasted Papad, Pickle", "Clean coriander & wash green peas", "Soak matki & chana dal"),
                ("Tuesday", "Upma, Coconut Chutney", "Poli, Usal, Steamed Rice", "Thalipeeth, Fresh White Butter", "Buy groceries from market", "Ferment idli batter"),
                ("Wednesday", "Idli, Medu Vada, Sambar", "Varan Bhaat, Baingan Bhaji, Poli", "Masala Bhaat, Cucumber Koshimbir", "Chop fresh vegetables", "Set fresh curd"),
                ("Thursday", "Sheera, Hot Masala Milk", "Poli, Shev Bhaji, Steamed Rice", "Moong Dal Khichdi, Ghee", "Chop and pluck coriander", "Boil milk properly"),
                ("Friday", "Methi Paratha, Dahi", "Varan Bhaat, Cauliflower Bhaji, Poli", "Dal Khichdi, Gujarati Kadhi", "Clean and dry fenugreek", "Knead chapati dough"),
                ("Saturday", "Misal Pav, Lemon, Farsan", "Poli, Paneer Bhaji, Jeera Rice", "Pav Bhaji, Chopped Onions", "Peel green peas", "Boil and mash potatoes"),
                ("Sunday", "Crispy Dosa, Sambar, Chutney", "Puran Poli, Katachi Amti, Bhaji", "Curd Rice, Tadka, Lemon Pickle", "Grind sambar spice powder", "Clean poha grains")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('forced_display_day', 'AUTO')")
        conn.execute("""
            INSERT OR IGNORE INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, 88, '420d', 4.10, -55, 'Excellent (3/3)', 'Online')
        """)
        conn.commit()

init_db()

def get_telemetry():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM device_telemetry WHERE id = 1").fetchone()
        if row:
            return dict(row)
        return {"battery_pct": 88, "battery_label": "420d", "voltage": 4.10, "rssi": -55, "wifi_strength": "Excellent (3/3)", "last_seen": "Online"}

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
            pct = int(p.get("pct", 100))
            label = str(p.get("batt", "420d"))
            v = float(p.get("v", 4.10))
            rssi = int(p.get("rssi", -55))
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
# 5. PIXEL-PERFECT 1-BIT BITMAP RENDERER (400x300 Matrix)
# ============================================================================
def get_text_width(font, text):
    try:
        bbox = font.getbbox(str(text))
        return bbox[2] - bbox[0]
    except Exception:
        return len(str(text)) * 8

def draw_wrapped_text(draw, text_str, x, y, max_w, font, line_height, max_lines=2, fill=0):
    words = str(text_str).strip().split()
    if not words:
        return
    lines = []
    curr = []
    for w in words:
        test = " ".join(curr + [w])
        if get_text_width(font, test) <= max_w:
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

    curr_y = y
    for line in lines[:max_lines]:
        draw.text((x, curr_y), line, font=font, fill=fill)
        curr_y += line_height

@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        ensure_fonts()
        date_str, data = get_target_menu_data()
        telem = get_telemetry()

        # Telemetry extraction
        rssi = int(request.args.get('rssi', telem["rssi"]))
        batt_pct = int(request.args.get('pct', telem["battery_pct"]))
        batt_str = str(request.args.get('batt', telem["battery_label"]))

        if 'rssi' in request.args or 'pct' in request.args:
            v = float(request.args.get('v', telem["voltage"]))
            wifi_lbl = "Excellent (3/3)" if rssi >= -65 else ("Good (2/3)" if rssi >= -78 else "Weak (1/3)")
            update_telemetry_db(batt_pct, batt_str, v, rssi, wifi_lbl)

        # 1-Bit Native Direct Canvas (400x300)
        img = Image.new("1", (PANEL_WIDTH, PANEL_HEIGHT), 1)
        draw = ImageDraw.Draw(img)

        # Crisp High-Legibility Typography
        f_logo = load_font("latin_bold", 15)
        f_cuisine = load_font("latin_bold", 10)
        f_date = load_font("latin_bold", 12)
        f_badge = load_font("latin_bold", 11)
        f_time = load_font("latin_bold", 11)
        f_cat = load_font("latin_bold", 10)
        f_dish = get_best_font(data["breakfast"] + data["lunch"] + data["dinner"], 12, bold=True)
        f_task_hdr = load_font("latin_bold", 10)
        f_task_body = get_best_font(data["task1"] + data["task2"], 11, bold=True)

        # --------------------------------------------------------------------
        # 1. NON-OVERLAPPING CONTRAST HEADER BAR (y: 0 to 34px)
        # --------------------------------------------------------------------
        draw.rectangle([0, 0, PANEL_WIDTH - 1, 34], fill=0)

        # [Zone A: x=8 to 140] Brand + Cuisine Pill
        draw.text((8, 9), "MealSync", font=f_logo, fill=1)
        
        cuisine_clean = data["cuisine"].upper()[:12]
        c_w = get_text_width(f_cuisine, cuisine_clean)
        # Inverted white pill for Cuisine
        draw.rectangle([82, 8, 86 + c_w, 24], fill=1)
        draw.text((84, 10), cuisine_clean, font=f_cuisine, fill=0)

        # [Zone B: x=145 to 275] Centered Today's Date
        d_w = get_text_width(f_date, date_str)
        date_x = (PANEL_WIDTH - d_w) // 2
        draw.text((date_x, 10), date_str, font=f_date, fill=1)

        # [Zone C: x=280 to 394] Wi-Fi Ladder + Battery Days + Battery Icon
        # 3-Bar Wi-Fi Indicator
        signal_bars = 3 if rssi >= -65 else (2 if rssi >= -78 else 1)
        wifiX, wifiY = 295, 11
        draw.rectangle([wifiX, wifiY + 8, wifiX + 2, wifiY + 12], fill=1 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 4, wifiY + 5, wifiX + 6, wifiY + 12], fill=1 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 8, wifiY + 2, wifiX + 10, wifiY + 12], fill=1 if signal_bars >= 3 else 0)

        # Exact Battery Days Label (e.g., "420d")
        b_lbl_w = get_text_width(f_badge, batt_str)
        bat_text_x = 362 - b_lbl_w
        draw.text((bat_text_x, 10), batt_str, font=f_badge, fill=1)

        # Battery Bar Icon
        batX, batY = 368, 10
        draw.rectangle([batX, batY, batX + 22, batY + 13], outline=1, width=1)
        draw.rectangle([batX + 22, batY + 3, batX + 24, batY + 10], fill=1)
        fill_w = max(0, min(18, int((batt_pct / 100.0) * 18)))
        if fill_w > 0:
            draw.rectangle([batX + 2, batY + 2, batX + 2 + fill_w, batY + 11], fill=1)

        # --------------------------------------------------------------------
        # 2. BAUHAUS RAIL TIMELINE (y: 35 to 218px)
        # --------------------------------------------------------------------
        rail_x = 76
        draw.line([(rail_x, 42), (rail_x, 212)], fill=0, width=2)

        # --- SLOT 1: BREAKFAST ---
        draw.text((8, 48), "08:30", font=f_time, fill=0)
        draw.text((8, 62), "AM", font=f_time, fill=0)
        draw.ellipse([rail_x - 3, 54, rail_x + 3, 60], fill=0)
        
        # High-Contrast Category Box
        draw.rectangle([86, 42, 168, 56], fill=0)
        draw.text((90, 44), "BREAKFAST", font=f_cat, fill=1)
        draw_wrapped_text(draw, data["breakfast"], 86, 60, 304, f_dish, 14, max_lines=2, fill=0)
        draw.line([(86, 95), (392, 95)], fill=0, width=1)

        # --- SLOT 2: LUNCH ---
        draw.text((8, 104), "01:00", font=f_time, fill=0)
        draw.text((8, 118), "PM", font=f_time, fill=0)
        draw.ellipse([rail_x - 3, 110, rail_x + 3, 116], fill=0)
        
        draw.rectangle([86, 98, 138, 112], fill=0)
        draw.text((90, 100), "LUNCH", font=f_cat, fill=1)
        draw_wrapped_text(draw, data["lunch"], 86, 116, 304, f_dish, 14, max_lines=2, fill=0)
        draw.line([(86, 153), (392, 153)], fill=0, width=1)

        # --- SLOT 3: DINNER ---
        draw.text((8, 162), "08:30", font=f_time, fill=0)
        draw.text((8, 176), "PM", font=f_time, fill=0)
        draw.ellipse([rail_x - 3, 168, rail_x + 3, 174], fill=0)
        
        draw.rectangle([86, 156, 142, 170], fill=0)
        draw.text((90, 158), "DINNER", font=f_cat, fill=1)
        draw_wrapped_text(draw, data["dinner"], 86, 174, 304, f_dish, 14, max_lines=2, fill=0)

        # Divider line above Tasks
        draw.line([(0, 218), (PANEL_WIDTH, 218)], fill=0, width=2)

        # --------------------------------------------------------------------
        # 3. DUAL-COLUMN TASK CARDS (y: 222 to 294px)
        # --------------------------------------------------------------------
        # Card 1: TODAY'S PREP (Left)
        draw.rectangle([6, 222, 196, 294], outline=0, width=1)
        draw.rectangle([6, 222, 196, 238], fill=0)
        draw.text((10, 224), "TODAY'S PREP", font=f_task_hdr, fill=1)
        
        draw.rectangle([12, 246, 22, 256], outline=0, width=1)
        draw_wrapped_text(draw, data["task1"], 28, 244, 162, f_task_body, 13, max_lines=3, fill=0)

        # Card 2: TOMORROW'S PREP (Right)
        draw.rectangle([202, 222, 394, 294], outline=0, width=1)
        draw.rectangle([202, 222, 394, 238], fill=0)
        draw.text((206, 224), "TOMORROW'S PREP", font=f_task_hdr, fill=1)
        
        draw.rectangle([208, 246, 218, 256], outline=0, width=1)
        draw_wrapped_text(draw, data["task2"], 224, 244, 162, f_task_body, 13, max_lines=3, fill=0)

        # Outer Frame
        draw.rectangle([0, 0, PANEL_WIDTH - 1, PANEL_HEIGHT - 1], outline=0, width=2)

        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1':
            img_epd = ImageOps.invert(img.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
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
            <div class="status">● Active & Synchronized (Bauhaus Rail Layout)</div>
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
