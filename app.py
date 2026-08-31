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
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

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
# 2. GLOBAL & REGIONAL FONT ENGINE
# ============================================================================
FONT_MAP = {
    "english": "Rubik-Bold.ttf",
    "devanagari": "NotoSansDevanagari-Bold.ttf",
    "gurmukhi": "NotoSansGurmukhi-Bold.ttf",
    "gujarati": "NotoSansGujarati-Bold.ttf",
    "bengali": "NotoSansBengali-Bold.ttf",
    "odia": "NotoSansOriya-Bold.ttf",
    "tamil": "NotoSansTamil-Bold.ttf",
    "telugu": "NotoSansTelugu-Bold.ttf",
    "kannada": "NotoSansKannada-Bold.ttf",
    "malayalam": "NotoSansMalayalam-Bold.ttf",
    "arabic": "NotoSansArabic-Bold.ttf",
    "hebrew": "NotoSansHebrew-Bold.ttf",
    "thai": "NotoSansThai-Bold.ttf",
    "chinese": "NotoSansSC-Bold.ttf",
    "japanese": "NotoSansJP-Bold.ttf",
    "korean": "NotoSansKR-Bold.ttf",
}

def ensure_fonts():
    font_urls = {
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf",
        "NotoSansDevanagari-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansDevanagari/NotoSansDevanagari-Bold.ttf",
        "NotoSansGurmukhi-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansGurmukhi/NotoSansGurmukhi-Bold.ttf",
        "NotoSansGujarati-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansGujarati/NotoSansGujarati-Bold.ttf",
        "NotoSansBengali-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansBengali/NotoSansBengali-Bold.ttf",
        "NotoSansOriya-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansOriya/NotoSansOriya-Bold.ttf",
        "NotoSansTamil-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansTamil/NotoSansTamil-Bold.ttf",
        "NotoSansTelugu-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansTelugu/NotoSansTelugu-Bold.ttf",
        "NotoSansKannada-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansKannada/NotoSansKannada-Bold.ttf",
        "NotoSansMalayalam-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansMalayalam/NotoSansMalayalam-Bold.ttf",
        "NotoSansArabic-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansArabic/NotoSansArabic-Bold.ttf",
        "NotoSansHebrew-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansHebrew/NotoSansHebrew-Bold.ttf",
        "NotoSansThai-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-fonts/main/hinted/ttf/NotoSansThai/NotoSansThai-Bold.ttf",
        "NotoSansSC-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/SimplifiedChinese/NotoSansSC-Bold.otf",
        "NotoSansJP-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/Japanese/NotoSansJP-Bold.otf",
        "NotoSansKR-Bold.ttf": "https://raw.githubusercontent.com/googlefonts/noto-cjk/main/Sans/OTF/Korean/NotoSansKR-Bold.otf"
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

def get_font_for_text(text):
    for char in text:
        cp = ord(char)
        if 0x0900 <= cp <= 0x097F: return FONT_MAP["devanagari"]
        if 0x0980 <= cp <= 0x09FF: return FONT_MAP["bengali"]
        if 0x0A00 <= cp <= 0x0A7F: return FONT_MAP["gurmukhi"]
        if 0x0A80 <= cp <= 0x0AFF: return FONT_MAP["gujarati"]
        if 0x0B00 <= cp <= 0x0B7F: return FONT_MAP["odia"]
        if 0x0B80 <= cp <= 0x0BFF: return FONT_MAP["tamil"]
        if 0x0C00 <= cp <= 0x0C7F: return FONT_MAP["telugu"]
        if 0x0C80 <= cp <= 0x0CFF: return FONT_MAP["kannada"]
        if 0x0D00 <= cp <= 0x0D7F: return FONT_MAP["malayalam"]
        if 0x0600 <= cp <= 0x06FF: return FONT_MAP["arabic"]
        if 0x0590 <= cp <= 0x05FF: return FONT_MAP["hebrew"]
        if 0x0E00 <= cp <= 0x0E7F: return FONT_MAP["thai"]
        if (0x4E00 <= cp <= 0x9FFF) or (0x3400 <= cp <= 0x4DBF): return FONT_MAP["chinese"]
        if (0x3040 <= cp <= 0x309F) or (0x30A0 <= cp <= 0x30FF): return FONT_MAP["japanese"]
        if (0xAC00 <= cp <= 0xD7AF) or (0x1100 <= cp <= 0x11FF): return FONT_MAP["korean"]
    return FONT_MAP["english"]

# ============================================================================
# 3. DATABASE SETUP & PERSISTENCE
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_menu (
                day_name TEXT PRIMARY KEY,
                breakfast TEXT,
                lunch TEXT,
                dinner TEXT,
                task1 TEXT,
                task2 TEXT
            )
        """)
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
                ("Monday", "पोहे, चहा", "वरण भात, पोळी, भेंडी भाजी", "खिचडी, कढी, पापड", "दूध आणणे", "मटकी भिजवणे"),
                ("Tuesday", "उपमा, खोबरे चटणी", "पोळी, उसळ, भात", "थालीपीठ, लोणी", "किराणा आणणे", "पीठ आंबवणे"),
                ("Wednesday", "इडली, चटणी, सांबार", "वरण भात, पोळी, वांगी भाजी", "मसाला भात, कोशिंबीर", "भाजी धुणे", "दही लावणे"),
                ("Thursday", "शिरा, गरम दूध", "पोळी, शेवभाजी, भात", "मुगाची मऊ खिचडी", "कोथिंबीर कापणे", "दूध आणणे"),
                ("Friday", "मेथी पराठा, दही", "वरण भात, फ्लॉवर भाजी, पोळी", "दाल खिचडी, कढी", "मेथी निवडून ठेवणे", "पीठ मळणे"),
                ("Saturday", "मिसळ पाव, लिंबू", "पोळी, पनीर भाजी, जीरा राईस", "पावभाजी, कांदा", "मटार सोलणे", "बटाटे उकडणे"),
                ("Sunday", "डोसा, सांबार, चटणी", "पुरणपोळी, कटाची आमटी, भजी", "दही भात, लोणचे", "सांबार मसाला", "पोहे चाळणे")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('forced_display_day', 'AUTO')")
        conn.execute("""
            INSERT OR IGNORE INTO device_telemetry (id, battery_pct, battery_label, voltage, rssi, wifi_strength, last_seen)
            VALUES (1, 100, '500d+', 4.20, -55, 'Excellent (3/3)', 'Online')
        """)
        conn.commit()

init_db()

def get_telemetry():
    with get_db() as conn:
        row = conn.execute("SELECT * FROM device_telemetry WHERE id = 1").fetchone()
        if row:
            return dict(row)
        return {"battery_pct": 100, "battery_label": "500d+", "voltage": 4.20, "rssi": -55, "wifi_strength": "Excellent (3/3)", "last_seen": "Online"}

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
        date_str = f"{target_day.upper()} (LIVE)"
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
            label = str(p.get("batt", "500d+"))
            v = float(p.get("v", 4.20))
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

@app.route('/api/ai-suggest', methods=['POST', 'OPTIONS'])
def api_ai_suggest():
    if request.method == 'OPTIONS':
        return Response(status=200)

    req = request.get_json(force=True)
    target_day = req.get("day_name", "Monday")
    cuisine = req.get("cuisine", "Maharashtrian")
    diet = req.get("diet", "VEG")
    user_prompt = req.get("prompt", f"Healthy authentic {diet} {cuisine} menu with advance prep.")
    api_key = req.get("gemini_key") or GEMINI_API_KEY

    if not api_key:
        return jsonify({"error": "No Gemini API key provided."}), 400

    system_instruction = f"""
    You are the MealSync AI Sous-Chef.
    Generate a culinary plan matching Cuisine: {cuisine} and Diet: {diet}.
    Rules:
    1. Diet: If VEG, strictly pure vegetarian. If NON_VEG, include authentic meat/fish.
    2. Format: Use comma separators (", ") between items. Keep concise (<35 chars per line).
    3. Script: Native script for the region.
    4. task1: Today's fresh prep.
    5. task2: Overnight/advance prep for tomorrow.
    """

    prompt_text = f"Day: {target_day}\nCuisine: {cuisine}\nDiet: {diet}\nNotes: {user_prompt}\nReturn strict JSON schema with keys: breakfast, lunch, dinner, task1, task2."

    try:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"
        payload = {
            "systemInstruction": {"parts": [{"text": system_instruction}]},
            "contents": [{"parts": [{"text": prompt_text}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "temperature": 0.3
            }
        }
        res = requests.post(url, json=payload, timeout=12)
        if res.status_code == 200:
            result_json = res.json()
            plan_str = result_json["candidates"][0]["content"]["parts"][0]["text"]
            return jsonify(json.loads(plan_str)), 200
        else:
            return jsonify({"error": res.text}), res.status_code
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ============================================================================
# 5. E-PAPER BITMAP RENDERER (400x300 Matrix)
# ============================================================================
def safe_font(font_path, size_1x):
    try:
        if os.path.exists(font_path) and os.path.getsize(font_path) > 2000:
            return ImageFont.truetype(font_path, size_1x * SCALE)
    except Exception:
        pass
    return ImageFont.load_default()

def get_text_width(font, text):
    try:
        bbox = font.getbbox(text)
        return bbox[2] - bbox[0]
    except Exception:
        return len(text) * 16

def get_wrapped_lines(text, font, max_width_2x):
    words = str(text).strip().split()
    if not words:
        return []
    lines, curr = [], []
    for w in words:
        test_line = " ".join(curr + [w])
        if get_text_width(font, test_line) <= max_width_2x:
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
    return lines

def draw_autofit_text(draw, text_str, x_1x, y_1x, max_w_1x, max_h_1x, max_size=18, min_size=10, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
        
    font_file = get_font_for_text(text_str)
    selected_font = None
    selected_lines = []
    line_mult = 1.30

    max_w_2x = max_w_1x * SCALE
    max_h_2x = max_h_1x * SCALE

    for size in range(max_size, min_size - 1, -1):
        test_font = safe_font(font_file, size)
        lines = get_wrapped_lines(text_str, test_font, max_w_2x)
        line_h = int((size * SCALE) * line_mult)
        total_h = len(lines) * line_h
        if len(lines) <= max_lines and total_h <= max_h_2x:
            selected_font = test_font
            selected_lines = lines
            break
            
    if not selected_font:
        selected_font = safe_font(font_file, min_size)
        selected_lines = get_wrapped_lines(text_str, selected_font, max_w_2x)[:max_lines]

    line_h = int((selected_font.size) * line_mult) if hasattr(selected_font, 'size') else 28
    curr_y = y_1x * SCALE
    for line in selected_lines:
        draw.text((x_1x * SCALE, curr_y), line, font=selected_font, fill=fill_color)
        curr_y += line_h

@app.route('/display.bmp', methods=['GET', 'HEAD'])
def render_display():
    if request.method == 'HEAD':
        return "OK", 200

    try:
        ensure_fonts()
        date_str, data = get_target_menu_data()
        telem = get_telemetry()

        # Telemetry ingestion from hardware request
        rssi = int(request.args.get('rssi', telem["rssi"]))
        batt_pct = int(request.args.get('pct', telem["battery_pct"]))
        batt_str = str(request.args.get('batt', telem["battery_label"]))

        if 'rssi' in request.args or 'pct' in request.args:
            v = float(request.args.get('v', telem["voltage"]))
            wifi_lbl = "Excellent (3/3)" if rssi >= -65 else ("Good (2/3)" if rssi >= -78 else "Weak (1/3)")
            update_telemetry_db(batt_pct, batt_str, v, rssi, wifi_lbl)

        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255)
        draw = ImageDraw.Draw(img_2x)

        font_logo = safe_font(FONT_MAP["english"], 18)
        font_date = safe_font(FONT_MAP["english"], 13)
        font_badge = safe_font(FONT_MAP["english"], 13)
        font_section = safe_font(FONT_MAP["english"], 15)

        # Header Bar
        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255)

        # Wi-Fi Bars
        signal_bars = 3 if rssi >= -65 else (2 if rssi >= -78 else 1)
        wifiX, wifiY = 96 * SCALE, 13 * SCALE
        draw.rectangle([wifiX + 4 * SCALE,  wifiY + 8 * SCALE, wifiX + 7 * SCALE,  wifiY + 12 * SCALE], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 9 * SCALE,  wifiY + 5 * SCALE, wifiX + 12 * SCALE, wifiY + 12 * SCALE], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 14 * SCALE, wifiY + 2 * SCALE, wifiX + 17 * SCALE, wifiY + 12 * SCALE], fill=255 if signal_bars >= 3 else 0)

        # Date
        date_w = get_text_width(font_date, date_str)
        date_center_x = (CANVAS_W - date_w) // 2
        draw.text((date_center_x, 11 * SCALE), date_str, font=font_date, fill=255)

        # Battery Bar & Badge
        batX, batY = 360 * SCALE, 12 * SCALE
        draw.rectangle([batX, batY, batX + 26 * SCALE, batY + 14 * SCALE], outline=255, width=SCALE)
        draw.rectangle([batX + 26 * SCALE, batY + 3 * SCALE, batX + 28 * SCALE, batY + 11 * SCALE], fill=255)

        fill_w = max(0, min(22 * SCALE, int((batt_pct / 100.0) * 22 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + 2 * SCALE, batY + 2 * SCALE, batX + 2 * SCALE + fill_w, batY + 12 * SCALE], fill=255)

        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 8 * SCALE, 11 * SCALE), batt_str, font=font_badge, fill=255)

        # Sidebar
        sidebar_w = 118 * SCALE
        draw.rectangle([0, 38 * SCALE, sidebar_w, CANVAS_H - 1], fill=0)
        draw.text((10 * SCALE, 52 * SCALE), "BREAKFAST", font=font_section, fill=255)
        draw.text((10 * SCALE, 112 * SCALE), "LUNCH", font=font_section, fill=255)
        draw.text((10 * SCALE, 175 * SCALE), "DINNER", font=font_section, fill=255)
        draw.text((10 * SCALE, 245 * SCALE), "TASKS", font=font_section, fill=255)

        for y_div in [98, 160, 228]:
            draw.line([(0, y_div * SCALE), (sidebar_w, y_div * SCALE)], fill=255, width=2 * SCALE)
            draw.line([(sidebar_w, y_div * SCALE), (CANVAS_W, y_div * SCALE)], fill=0, width=2 * SCALE)

        # Meals
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=12, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=12, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=12, max_lines=2, fill_color=0)

        # Checkboxes & Tasks
        draw.rectangle([126 * SCALE, 238 * SCALE, 138 * SCALE, 250 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task1"], 142, 234, 116, 56, max_size=13, min_size=10, max_lines=2, fill_color=0)

        draw.rectangle([264 * SCALE, 238 * SCALE, 276 * SCALE, 250 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task2"], 280, 234, 114, 56, max_size=13, min_size=10, max_lines=2, fill_color=0)

        # Downscale & 1-bit monochrome dithering
        resample_mode = Image.LANCZOS if hasattr(Image, 'LANCZOS') else getattr(Image, 'ANTIALIAS', 1)
        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=resample_mode)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 160 else 0, mode="1")

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

# ============================================================================
# 6. ROOT ROUTE
# ============================================================================
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
            <p style="font-size: 12px; color: #94a3b8; margin-top: 12px;">Live 1-bit E-Paper Buffer Stream (N1B4V02 / SSD1683):</p>
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
