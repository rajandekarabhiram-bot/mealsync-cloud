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

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Fixed 400x300 Layout Dimensions (2x Supersampling)
PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Yantramanav-Bold.ttf"

DEVICE_LOGS = []

# ============================================================================
# 1. DATABASE SETUP (Persisting Cuisine, Language, and Menus)
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE)
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
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM weekly_menu")
        if cur.fetchone()[0] == 0:
            default_days = [
                ("Monday", "पोहे, चहा", "वरण भात, पोळी, भेंडी भाजी", "खिचडी, कढी, पापड", "दूध आणणे", "उद्यासाठी मटकी भिजवणे"),
                ("Tuesday", "उपमा, खोबरे चटणी", "पोळी, मटकी उसळ, भात", "थालीपीठ, लोणी", "किराणा आणणे", "पीठ आंबवणे"),
                ("Wednesday", "इडली, चटणी, सांबार", "वरण भात, पोळी, वांगी भाजी", "मसाला भात, कोशिंबीर", "भाजी धुणे", "दही लावणे"),
                ("Thursday", "शिरा, गरम दूध", "पोळी, शेवभाजी, भात", "मुगाची मऊ खिचडी", "कोथिंबीर कापणे", "दूध आणणे"),
                ("Friday", "मेथी पराठा, दही", "वरण भात, फ्लॉवर भाजी, पोळी", "दाल खिचडी, कढी", "मेथी निवडून ठेवणे", "पीठ मळणे"),
                ("Saturday", "मिसळ पाव, लिंबू", "पोळी, पनीर भाजी, जीरा राईस", "पावभाजी, कांदा", "मटार सोलणे", "बटाटे उकडणे"),
                ("Sunday", "डोसा, सांबार, चटणी", "पुरणपोळी, कटाची आमटी, भजी", "दही भात, लोणचे", "सांबार मसाला", "उद्यासाठी पोहे चाळणे")
            ]
            conn.executemany("INSERT INTO weekly_menu VALUES (?, ?, ?, ?, ?, ?)", default_days)
        
        # Set default settings
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_cuisine', 'Maharashtrian')")
        conn.execute("INSERT OR IGNORE INTO app_settings (key, value) VALUES ('active_diet', 'VEG')")
        conn.commit()

init_db()

# ============================================================================
# 2. AUTO FONT DOWNLOADER
# ============================================================================
def ensure_fonts():
    font_urls = {
        "Rubik-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/rubik/Rubik-Bold.ttf",
        "Yantramanav-Bold.ttf": "https://raw.githubusercontent.com/google/fonts/main/ofl/yantramanav/Yantramanav-Bold.ttf"
    }
    for filename, url in font_urls.items():
        if not os.path.exists(filename) or os.path.getsize(filename) < 2000:
            try:
                r = requests.get(url, timeout=12)
                if r.status_code == 200 and len(r.content) > 2000:
                    with open(filename, "wb") as f:
                        f.write(r.content)
            except Exception:
                pass

ensure_fonts()

# ============================================================================
# 3. HELPER FUNCTIONS & CONTENT HASH
# ============================================================================
def get_setting(key, default_val=""):
    with get_db() as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default_val

def set_setting(key, value):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()

def get_target_menu_data():
    now_ist = datetime.now(IST)
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

@app.route('/hash', methods=['GET'])
def get_content_hash():
    date_str, data = get_target_menu_data()
    payload = f"{date_str}|{data['cuisine']}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    return jsonify({"hash": content_hash}), 200

# ============================================================================
# 4. REST APIS & AI PRO ENGINE
# ============================================================================
@app.route('/api/menu', methods=['GET'])
def api_get_menu():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM weekly_menu").fetchall()
        cuisine = get_setting("active_cuisine", "Maharashtrian")
        diet = get_setting("active_diet", "VEG")
        return jsonify({
            "menu": [dict(ix) for ix in rows],
            "active_cuisine": cuisine,
            "active_diet": diet
        }), 200

@app.route('/api/menu', methods=['POST'])
def api_update_day_menu():
    req = request.get_json(force=True)
    day = req.get("day_name")
    cuisine = req.get("cuisine")
    diet = req.get("diet")

    if cuisine: set_setting("active_cuisine", cuisine)
    if diet: set_setting("active_diet", diet)

    with get_db() as conn:
        conn.execute("""
            UPDATE weekly_menu
            SET breakfast = ?, lunch = ?, dinner = ?, task1 = ?, task2 = ?
            WHERE day_name = ?
        """, (
            str(req.get("breakfast", "")).replace("+", ","),
            str(req.get("lunch", "")).replace("+", ","),
            str(req.get("dinner", "")).replace("+", ","),
            str(req.get("task1", "")).replace("+", ","),
            str(req.get("task2", "")).replace("+", ","),
            day
        ))
        conn.commit()
    return jsonify({"status": "updated"}), 200

@app.route('/api/ai-suggest', methods=['POST'])
def api_ai_suggest():
    req = request.get_json(force=True)
    target_day = req.get("day_name", "Monday")
    cuisine = req.get("cuisine", "Maharashtrian")
    diet = req.get("diet", "VEG")
    user_prompt = req.get("prompt", f"Healthy authentic {diet} {cuisine} menu with advance prep.")
    api_key = req.get("gemini_key") or GEMINI_API_KEY

    system_instruction = f"""
    You are the MealSync AI Sous-Chef.
    Generate a culinary plan matching Cuisine: {cuisine} and Diet: {diet}.
    Rules:
    1. Diet Constraint: If VEG, strictly pure vegetarian. If NON_VEG, include authentic meat/fish.
    2. Format: Use comma separators (", ") between meal items. Concise (<35 chars per line).
    3. Script: If cuisine is Maharashtrian/North Indian/Gujarati/Rajasthani/South Indian, use natural Devanagari script. If Continental/American, use English.
    4. task1: Today's immediate grocery/cooking prep task.
    5. task2: Overnight/advance prep for tomorrow.
    """

    prompt_text = f"Day: {target_day}\nCuisine: {cuisine}\nDiet: {diet}\nNotes: {user_prompt}\nReturn strict JSON with keys: breakfast, lunch, dinner, task1, task2."

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

@app.route('/log', methods=['POST'])
def receive_device_log():
    try:
        log_entry = request.get_json(force=True)
        now_str = datetime.now(IST).strftime("%d %b %Y, %I:%M:%S %p IST")
        log_entry['timestamp'] = now_str
        DEVICE_LOGS.insert(0, log_entry)
        if len(DEVICE_LOGS) > 300:
            DEVICE_LOGS.pop()
        return jsonify({"status": "logged"}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 400

@app.route('/logs', methods=['GET'])
def view_logs():
    html_template = """
    <!DOCTYPE html>
    <html>
    <head>
        <title>MealSync Telemetry Logs</title>
        <meta http-equiv="refresh" content="15">
        <style>
            body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0f172a; color: #f8fafc; padding: 24px; }
            table { width: 100%; border-collapse: collapse; background: #1e293b; border-radius: 8px; overflow: hidden; margin-top: 16px; }
            th, td { padding: 12px 16px; text-align: left; font-size: 13px; border-bottom: 1px solid #334155; }
            th { background: #334155; color: #cbd5e1; text-transform: uppercase; font-size: 11px; }
            .badge { padding: 4px 8px; border-radius: 4px; font-weight: bold; font-size: 11px; background: #0284c7; color: white; }
            .wifi-tag { color: #38bdf8; font-weight: 600; }
        </style>
    </head>
    <body>
        <h2>📊 MealSync Hardware Telemetry Logs</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (IST)</th>
                    <th>Action</th>
                    <th>Battery</th>
                    <th>Voltage</th>
                    <th>Wi-Fi Strength</th>
                    <th>Hash</th>
                    <th>Next Sleep</th>
                </tr>
            </thead>
            <tbody>
                {% for log in logs %}
                <tr>
                    <td>{{ log.timestamp }}</td>
                    <td><span class="badge">{{ log.event }}</span></td>
                    <td><b>{{ log.batt }}</b> ({{ log.pct }}%)</td>
                    <td>{{ log.v }} V</td>
                    <td><span class="wifi-tag">{{ log.wifi_strength }}</span></td>
                    <td><code>{{ log.hash }}</code></td>
                    <td>{{ log.sleep_sec }}s</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </body>
    </html>
    """
    return render_template_string(html_template, logs=DEVICE_LOGS)

# ============================================================================
# 5. FIXED E-PAPER BITMAP RENDERER (Matching App Language & Layout)
# ============================================================================
def safe_font(font_path, size_1x):
    try:
        if os.path.exists(font_path) and os.path.getsize(font_path) > 2000:
            return ImageFont.truetype(font_path, size_1x * SCALE)
    except Exception:
        pass
    return ImageFont.load_default()

def is_ascii(s):
    return all(ord(c) < 128 for c in s)

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

def draw_autofit_text(draw, text_str, x_1x, y_1x, max_w_1x, max_h_1x, max_size=18, min_size=13, max_lines=2, fill_color=0):
    text_str = str(text_str).strip()
    if not text_str:
        return
    font_file = FONT_ENGLISH_PATH if is_ascii(text_str) else FONT_MARATHI_PATH
    selected_font = None
    selected_lines = []
    line_mult = 1.35 if is_ascii(text_str) else 1.40
    
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

    line_h = int((selected_font.size) * line_mult) if hasattr(selected_font, 'size') else 36
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

        rssi = int(request.args.get('rssi', -50))
        batt_str = str(request.args.get('batt', '500d+'))
        batt_pct = int(request.args.get('pct', 100))

        img_2x = Image.new("L", (CANVAS_W, CANVAS_H), 255)
        draw = ImageDraw.Draw(img_2x)

        font_logo = safe_font(FONT_ENGLISH_PATH, 18)
        font_date = safe_font(FONT_ENGLISH_PATH, 13)
        font_badge = safe_font(FONT_ENGLISH_PATH, 13)
        font_section = safe_font(FONT_ENGLISH_PATH, 15)

        # 1. Header Bar (Preserved Fixed Layout)
        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255)

        # Wi-Fi Signal Bars
        signal_bars = 3 if rssi >= -67 else (2 if rssi >= -80 else 1)
        wifiX, wifiY = 96 * SCALE, 13 * SCALE
        draw.rectangle([wifiX + 4, wifiY + 20, wifiX + 8,  wifiY + 28], fill=255 if signal_bars >= 1 else 0)
        draw.rectangle([wifiX + 14, wifiY + 12, wifiX + 18, wifiY + 28], fill=255 if signal_bars >= 2 else 0)
        draw.rectangle([wifiX + 24, wifiY + 4,  wifiX + 28, wifiY + 28], fill=255 if signal_bars >= 3 else 0)

        # Date
        date_w = get_text_width(font_date, date_str)
        date_center_x = (CANVAS_W - date_w) // 2
        draw.text((date_center_x, 11 * SCALE), date_str, font=font_date, fill=255)

        # Battery
        batX, batY = 362 * SCALE, 12 * SCALE
        draw.rectangle([batX, batY, batX + 24 * SCALE, batY + 14 * SCALE], outline=255, width=SCALE)
        draw.rectangle([batX + 24 * SCALE, batY + 3 * SCALE, batX + 26 * SCALE, batY + 11 * SCALE], fill=255)

        fill_w = max(0, min(20 * SCALE, int((batt_pct / 100.0) * 20 * SCALE)))
        if fill_w > 0:
            draw.rectangle([batX + 2 * SCALE, batY + 2 * SCALE, batX + 2 * SCALE + fill_w, batY + 12 * SCALE], fill=255)

        badge_w = get_text_width(font_badge, batt_str)
        draw.text((batX - badge_w - 10, 11 * SCALE), batt_str, font=font_badge, fill=255)

        # 2. Sidebar Labels (Fixed Geometry)
        sidebar_w = 118 * SCALE
        draw.rectangle([0, 38 * SCALE, sidebar_w, CANVAS_H - 1], fill=0)
        
        draw.text((10 * SCALE, 52 * SCALE), "BREAKFAST", font=font_section, fill=255)
        draw.text((10 * SCALE, 112 * SCALE), "LUNCH", font=font_section, fill=255)
        draw.text((10 * SCALE, 175 * SCALE), "DINNER", font=font_section, fill=255)
        draw.text((10 * SCALE, 245 * SCALE), "TASKS", font=font_section, fill=255)

        for y_div in [98, 160, 228]:
            draw.line([(0, y_div * SCALE), (sidebar_w, y_div * SCALE)], fill=255, width=2 * SCALE)
            draw.line([(sidebar_w, y_div * SCALE), (CANVAS_W, y_div * SCALE)], fill=0, width=2 * SCALE)

        # 3. Dynamic Meals & Prep Tasks (Auto-switches to Devanagari font when Marathi is saved)
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)

        # Checkboxes
        draw.rectangle([128 * SCALE, 243 * SCALE, 142 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        draw.rectangle([264 * SCALE, 243 * SCALE, 278 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        # Downscaling & 1-bit dithering
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
# 6. COMPANION PROGRESSIVE WEB APP
# ============================================================================
@app.route('/')
def app_home():
    html_ui = """
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="UTF-8" />
      <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no" />
      <title>MealSync Hub • Smart Kitchen Dashboard</title>
      <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@600;700;800&family=Yantramanav:wght@500;700;900&display=swap" rel="stylesheet">
      <script src="https://cdn.tailwindcss.com"></script>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans min-h-screen flex flex-col items-center justify-between">

      <section id="screen-login" class="w-full max-w-md px-6 py-12 flex flex-col justify-center min-h-screen space-y-8">
        <div class="text-center space-y-3">
          <div class="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-gradient-to-tr from-teal-500 to-emerald-400 text-white shadow-xl text-4xl mb-2">🍳</div>
          <h1 class="text-3xl font-extrabold text-slate-900">MealSync Hub</h1>
          <p class="text-sm text-slate-500">Smart multilingual kitchen companion for your E-Paper display.</p>
        </div>

        <div class="bg-white border border-slate-200 rounded-3xl p-6 shadow-xl space-y-4">
          <div>
            <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1.5">Your Name</label>
            <input id="login-name" type="text" placeholder="e.g. Abhiram" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold outline-none focus:ring-2 focus:ring-teal-500" />
          </div>
          <div>
            <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1.5">Dietary Preference</label>
            <div class="grid grid-cols-3 gap-2">
              <button onclick="setDiet('VEG')" id="diet-btn-veg" class="py-2.5 rounded-xl border text-xs font-bold bg-emerald-50 border-emerald-500 text-emerald-700">🟢 Pure Veg</button>
              <button onclick="setDiet('NON_VEG')" id="diet-btn-nonveg" class="py-2.5 rounded-xl border text-xs font-bold bg-slate-50 border-slate-200 text-slate-600">🔴 Non-Veg</button>
              <button onclick="setDiet('BOTH')" id="diet-btn-both" class="py-2.5 rounded-xl border text-xs font-bold bg-slate-50 border-slate-200 text-slate-600">🟡 Both</button>
            </div>
          </div>
          <button onclick="handleLoginSubmit()" class="w-full bg-slate-900 hover:bg-slate-800 text-white font-bold text-sm py-4 rounded-xl shadow-lg transition-all">
            Choose Cuisine ➔
          </button>
        </div>
      </section>

      <section id="screen-cuisine" class="w-full max-w-md px-5 py-8 hidden space-y-6">
        <div class="space-y-1">
          <span class="text-xs font-bold text-teal-600 uppercase tracking-wider">Step 2 of 2</span>
          <h2 class="text-2xl font-extrabold text-slate-900">Select Cuisine & Language</h2>
          <p class="text-xs text-slate-500">Language and recipe suggestions will adapt automatically.</p>
        </div>
        <div class="grid grid-cols-2 gap-3">
          <button onclick="selectCuisineTheme('Maharashtrian')" class="p-3.5 bg-white border border-slate-200 rounded-2xl shadow-sm hover:border-teal-500 text-left">
            <span class="text-xl block mb-1">🌾</span>
            <div class="font-bold text-sm">Maharashtrian</div>
            <div class="text-[11px] text-teal-600 font-bold">पारंपारिक मराठी</div>
          </button>
          <button onclick="selectCuisineTheme('South Indian')" class="p-3.5 bg-white border border-slate-200 rounded-2xl shadow-sm hover:border-teal-500 text-left">
            <span class="text-xl block mb-1">🥥</span>
            <div class="font-bold text-sm">South Indian</div>
            <div class="text-[11px] text-teal-600 font-bold">इडली, डोसा, सांबार</div>
          </button>
          <button onclick="selectCuisineTheme('Gujarati')" class="p-3.5 bg-white border border-slate-200 rounded-2xl shadow-sm hover:border-teal-500 text-left">
            <span class="text-xl block mb-1">🥘</span>
            <div class="font-bold text-sm">Gujarati</div>
            <div class="text-[11px] text-teal-600 font-bold">थेपला, कढी</div>
          </button>
          <button onclick="selectCuisineTheme('Continental / European')" class="p-3.5 bg-white border border-slate-200 rounded-2xl shadow-sm hover:border-teal-500 text-left">
            <span class="text-xl block mb-1">🥗</span>
            <div class="font-bold text-sm">Continental</div>
            <div class="text-[11px] text-slate-500">Salads & Pasta</div>
          </button>
        </div>
      </section>

      <section id="screen-planner" class="w-full max-w-md px-4 py-4 hidden space-y-4">
        <div class="flex items-center justify-between pb-1 border-b border-slate-200">
          <div class="flex items-center gap-2">
            <span class="text-2xl">🍳</span>
            <div>
              <h2 class="font-extrabold text-base text-slate-900">MealSync Hub</h2>
              <div class="flex items-center gap-1.5 mt-0.5">
                <span id="active-cuisine-badge" class="text-[10px] font-bold text-teal-700 bg-teal-50 border border-teal-200 px-2 py-0.5 rounded-full">Maharashtrian</span>
                <span id="active-diet-badge" class="text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200 px-2 py-0.5 rounded-full">🟢 Veg</span>
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="togglePreviewModal()" class="bg-slate-900 text-white text-[11px] font-bold px-3 py-1.5 rounded-lg shadow-sm">
              📱 Preview
            </button>
          </div>
        </div>

        <div class="flex gap-1.5 overflow-x-auto pb-1" id="day-bar">
          <button onclick="selectDay('Monday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Mon</button>
          <button onclick="selectDay('Tuesday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Tue</button>
          <button onclick="selectDay('Wednesday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Wed</button>
          <button onclick="selectDay('Thursday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Thu</button>
          <button onclick="selectDay('Friday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Fri</button>
          <button onclick="selectDay('Saturday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Sat</button>
          <button onclick="selectDay('Sunday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200">Sun</button>
        </div>

        <div class="bg-gradient-to-br from-teal-50 to-indigo-50 border border-teal-200 rounded-2xl p-3 space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-[11px] font-extrabold text-teal-900">✨ AI Sous-Chef</span>
            <span id="active-day-label" class="text-[11px] font-bold text-slate-500">Planning: Monday</span>
          </div>
          <div class="flex gap-2">
            <input id="ai-theme-input" type="text" placeholder="Custom note (e.g. high protein)..." class="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs outline-none" />
            <button onclick="generatePlanForDay()" id="ai-gen-btn" class="bg-teal-600 text-white font-bold text-xs px-3 py-2 rounded-xl">Generate</button>
          </div>
        </div>

        <div class="space-y-2.5">
          <div class="bg-white border border-slate-200 rounded-2xl p-3.5 shadow-sm">
            <span class="text-[10px] font-extrabold uppercase text-slate-400 block mb-1">Breakfast</span>
            <input id="input-breakfast" type="text" class="w-full text-base font-bold text-slate-800 outline-none" />
          </div>
          <div class="bg-white border border-slate-200 rounded-2xl p-3.5 shadow-sm">
            <span class="text-[10px] font-extrabold uppercase text-slate-400 block mb-1">Lunch</span>
            <input id="input-lunch" type="text" class="w-full text-base font-bold text-slate-800 outline-none" />
          </div>
          <div class="bg-white border border-slate-200 rounded-2xl p-3.5 shadow-sm">
            <span class="text-[10px] font-extrabold uppercase text-slate-400 block mb-1">Dinner</span>
            <input id="input-dinner" type="text" class="w-full text-base font-bold text-slate-800 outline-none" />
          </div>

          <div class="grid grid-cols-2 gap-2.5">
            <div class="bg-white border border-slate-200 rounded-2xl p-3 shadow-sm">
              <span class="text-[9px] font-extrabold uppercase text-slate-400 block mb-1">Task 1 (Prep)</span>
              <input id="input-task1" type="text" class="w-full text-xs font-bold text-slate-800 outline-none" />
            </div>
            <div class="bg-white border border-slate-200 rounded-2xl p-3 shadow-sm">
              <span class="text-[9px] font-extrabold uppercase text-rose-500 block mb-1">Task 2 (Advance 🌙)</span>
              <input id="input-task2" type="text" class="w-full text-xs font-bold text-slate-800 outline-none" />
            </div>
          </div>
        </div>

        <button onclick="saveCurrentDayMenu()" id="save-btn" class="w-full bg-slate-900 text-white font-extrabold text-sm py-4 rounded-2xl shadow-lg transition-all">
          💾 Save & Sync E-Paper Now
        </button>
      </section>

      <div id="preview-modal" class="fixed inset-0 z-50 bg-slate-900/80 backdrop-blur-sm flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-3xl p-5 max-w-sm w-full shadow-2xl space-y-3">
          <div class="flex items-center justify-between pb-2 border-b border-slate-100">
            <div>
              <h3 class="font-extrabold text-slate-900 text-sm">4.2" E-Paper Preview</h3>
              <p class="text-[10px] text-slate-400">Live 1-bit bitmap stream (N1B4V02)</p>
            </div>
            <button onclick="togglePreviewModal()" class="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-bold">✕</button>
          </div>
          <div class="bg-slate-100 rounded-2xl p-1.5 border border-slate-200">
            <img id="epaper-stream-img" src="/display.bmp" alt="E-Paper Stream" class="w-full h-auto rounded-xl shadow-inner border border-slate-300" />
          </div>
          <div class="flex gap-2">
            <button onclick="refreshPreviewImage()" class="flex-1 bg-slate-100 text-slate-700 text-xs font-bold py-2 rounded-xl">🔄 Refresh</button>
            <a href="/logs" target="_blank" class="flex-1 bg-teal-50 text-teal-700 text-xs font-bold py-2 rounded-xl text-center">📊 Logs</a>
          </div>
        </div>
      </div>

      <div id="toast" class="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 bg-slate-900 text-white text-xs font-bold px-4 py-2.5 rounded-full opacity-0 pointer-events-none transition-all duration-300">
        Saved!
      </div>

      <script>
        let activeDay = "Monday";
        let activeCuisine = "Maharashtrian";
        let activeDiet = "VEG";
        let weeklyMenuCache = {};

        window.addEventListener('DOMContentLoaded', async () => {
          const storedUser = localStorage.getItem('MEALSYNC_USER');
          const storedCuisine = localStorage.getItem('MEALSYNC_CUISINE');
          const storedDiet = localStorage.getItem('MEALSYNC_DIET_PREF');

          if (storedDiet) activeDiet = storedDiet;
          if (storedCuisine) activeCuisine = storedCuisine;

          if (storedUser) {
            showScreen('planner');
            initTodayTab();
            await loadWeeklySchedule();
          } else {
            showScreen('login');
          }
        });

        function setDiet(diet) {
          activeDiet = diet;
          ['veg', 'nonveg', 'both'].forEach(d => {
            document.getElementById(`diet-btn-${d}`).className = "py-2.5 rounded-xl border text-xs font-bold bg-slate-50 border-slate-200 text-slate-600";
          });
          const btn = document.getElementById(`diet-btn-${diet.toLowerCase().replace('_','')}`);
          if (btn) btn.className = "py-2.5 rounded-xl border text-xs font-bold bg-emerald-50 border-emerald-500 text-emerald-700";
        }

        function showScreen(screen) {
          document.getElementById('screen-login').classList.add('hidden');
          document.getElementById('screen-cuisine').classList.add('hidden');
          document.getElementById('screen-planner').classList.add('hidden');

          if (screen === 'login') document.getElementById('screen-login').classList.remove('hidden');
          if (screen === 'cuisine') document.getElementById('screen-cuisine').classList.remove('hidden');
          if (screen === 'planner') {
            document.getElementById('screen-planner').classList.remove('hidden');
            document.getElementById('active-cuisine-badge').innerText = activeCuisine;
            document.getElementById('active-diet-badge').innerText = activeDiet === 'VEG' ? '🟢 Veg' : (activeDiet === 'NON_VEG' ? '🔴 Non-Veg' : '🟡 Both');
          }
        }

        function handleLoginSubmit() {
          const name = document.getElementById('login-name').value.trim();
          if (!name) return showToast("Please enter your name.");
          localStorage.setItem('MEALSYNC_USER', JSON.stringify({ name }));
          localStorage.setItem('MEALSYNC_DIET_PREF', activeDiet);
          showScreen('cuisine');
        }

        async function selectCuisineTheme(cuisine) {
          activeCuisine = cuisine;
          localStorage.setItem('MEALSYNC_CUISINE', cuisine);
          showScreen('planner');
          initTodayTab();
          await loadWeeklySchedule();
        }

        function initTodayTab() {
          const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
          selectDay(days[new Date().getDay()]);
        }

        function selectDay(day) {
          activeDay = day;
          document.getElementById('active-day-label').innerText = `Planning: ${day}`;
          document.querySelectorAll('.day-btn').forEach(btn => {
            if (btn.innerText.toLowerCase().startsWith(day.slice(0, 3).toLowerCase())) {
              btn.className = "day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-teal-600 text-white shadow-md";
            } else {
              btn.className = "day-btn px-3 py-1.5 rounded-xl text-xs font-bold bg-white border border-slate-200 text-slate-600";
            }
          });
          renderActiveDayInputs();
        }

        async function loadWeeklySchedule() {
          try {
            const res = await fetch('/api/menu');
            if (res.ok) {
              const data = await res.json();
              if (data.active_cuisine) activeCuisine = data.active_cuisine;
              if (data.active_diet) activeDiet = data.active_diet;
              data.menu.forEach(item => {
                weeklyMenuCache[item.day_name] = item;
              });
              document.getElementById('active-cuisine-badge').innerText = activeCuisine;
              document.getElementById('active-diet-badge').innerText = activeDiet === 'VEG' ? '🟢 Veg' : (activeDiet === 'NON_VEG' ? '🔴 Non-Veg' : '🟡 Both');
              renderActiveDayInputs();
            }
          } catch (err) {
            showToast("Failed to load schedule from cloud.");
          }
        }

        function renderActiveDayInputs() {
          const item = weeklyMenuCache[activeDay] || { breakfast: '', lunch: '', dinner: '', task1: '', task2: '' };
          document.getElementById('input-breakfast').value = item.breakfast || '';
          document.getElementById('input-lunch').value = item.lunch || '';
          document.getElementById('input-dinner').value = item.dinner || '';
          document.getElementById('input-task1').value = item.task1 || '';
          document.getElementById('input-task2').value = item.task2 || '';
        }

        async function saveCurrentDayMenu() {
          const saveBtn = document.getElementById('save-btn');
          saveBtn.innerText = "Syncing with Display...";
          saveBtn.disabled = true;

          const payload = {
            day_name: activeDay,
            cuisine: activeCuisine,
            diet: activeDiet,
            breakfast: document.getElementById('input-breakfast').value.trim(),
            lunch: document.getElementById('input-lunch').value.trim(),
            dinner: document.getElementById('input-dinner').value.trim(),
            task1: document.getElementById('input-task1').value.trim(),
            task2: document.getElementById('input-task2').value.trim()
          };

          try {
            const res = await fetch('/api/menu', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify(payload)
            });

            if (res.ok) {
              weeklyMenuCache[activeDay] = payload;
              showToast(`✅ ${activeDay} Synced to E-Paper!`);
            }
          } catch (err) {
            showToast("❌ Failed to save menu.");
          } finally {
            saveBtn.innerText = "💾 Save & Sync E-Paper Now";
            saveBtn.disabled = false;
          }
        }

        async function generatePlanForDay() {
          const aiBtn = document.getElementById('ai-gen-btn');
          aiBtn.innerText = "...";
          aiBtn.disabled = true;

          try {
            const res = await fetch('/api/ai-suggest', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                day_name: activeDay,
                cuisine: activeCuisine,
                diet: activeDiet,
                prompt: document.getElementById('ai-theme-input').value.trim() || `${activeDiet} ${activeCuisine} menu`
              })
            });

            if (res.ok) {
              const aiData = await res.json();
              document.getElementById('input-breakfast').value = aiData.breakfast || '';
              document.getElementById('input-lunch').value = aiData.lunch || '';
              document.getElementById('input-dinner').value = aiData.dinner || '';
              document.getElementById('input-task1').value = aiData.task1 || '';
              document.getElementById('input-task2').value = aiData.task2 || '';
              showToast("✨ AI Menu Ready! Tap Save & Sync.");
            } else {
              showToast("AI Error. Check key.");
            }
          } catch (e) {
            showToast("Error generating menu.");
          } finally {
            aiBtn.innerText = "Generate";
            aiBtn.disabled = false;
          }
        }

        function togglePreviewModal() {
          const modal = document.getElementById('preview-modal');
          modal.classList.toggle('hidden');
          if (!modal.classList.contains('hidden')) refreshPreviewImage();
        }

        function refreshPreviewImage() {
          const img = document.getElementById('epaper-stream-img');
          img.src = `/display.bmp?t=${new Date().getTime()}`;
        }

        function showToast(msg) {
          const toast = document.getElementById('toast');
          toast.innerText = msg;
          toast.classList.remove('opacity-0', 'pointer-events-none');
          setTimeout(() => {
            toast.classList.add('opacity-0', 'pointer-events-none');
          }, 2500);
        }
      </script>
    </body>
    </html>
    """
    return render_template_string(html_ui)

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
