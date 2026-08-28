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

# Central Gemini API Key from Render Environment (Optional if user enters BYOK)
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")

# E-Paper Canvas Dimensions for N1B4V02 (400x300 with 2x supersampling)
PANEL_WIDTH = 400
PANEL_HEIGHT = 300
SCALE = 2
CANVAS_W = PANEL_WIDTH * SCALE
CANVAS_H = PANEL_HEIGHT * SCALE

FONT_ENGLISH_PATH = "Rubik-Bold.ttf"
FONT_MARATHI_PATH = "Yantramanav-Bold.ttf"

DEVICE_LOGS = []

# ============================================================================
# 1. DATABASE INITIALIZATION & PRE-SEEDED ROTATIONS
# ============================================================================
def get_db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_db() as conn:
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
# 3. SCHEDULE & CONTENT HASH (9:00 PM IST Rollover)
# ============================================================================
def get_target_menu_data():
    now_ist = datetime.now(IST)
    if now_ist.hour >= 21:
        target_date = now_ist + timedelta(days=1)
    else:
        target_date = now_ist

    target_day = target_date.strftime("%A")
    date_str = target_date.strftime("%a, %d %b %Y").upper()

    with get_db() as conn:
        row = conn.execute("SELECT * FROM weekly_menu WHERE day_name = ?", (target_day,)).fetchone()
        if row:
            data = {
                "day": row["day_name"],
                "breakfast": (row["breakfast"] or "—").replace("+", ","),
                "lunch": (row["lunch"] or "—").replace("+", ","),
                "dinner": (row["dinner"] or "—").replace("+", ","),
                "task1": (row["task1"] or "—").replace("+", ","),
                "task2": (row["task2"] or "—").replace("+", ",")
            }
        else:
            data = {
                "day": target_day,
                "breakfast": "—", "lunch": "—", "dinner": "—", "task1": "—", "task2": "—"
            }

    return date_str, data

@app.route('/hash', methods=['GET'])
def get_content_hash():
    date_str, data = get_target_menu_data()
    payload = f"{date_str}|{data['breakfast']}|{data['lunch']}|{data['dinner']}|{data['task1']}|{data['task2']}"
    content_hash = hashlib.md5(payload.encode('utf-8')).hexdigest()[:10]
    return jsonify({"hash": content_hash}), 200

# ============================================================================
# 4. REST APIS & AI PRO GENERATOR
# ============================================================================
@app.route('/api/menu', methods=['GET'])
def api_get_menu():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM weekly_menu").fetchall()
        return jsonify([dict(ix) for ix in rows]), 200

@app.route('/api/menu', methods=['POST'])
def api_update_day_menu():
    req = request.get_json(force=True)
    day = req.get("day_name")
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

    if not api_key:
        return jsonify({"error": "No Gemini API key configured. Provide in Settings."}), 400

    system_instruction = f"""
    You are the MealSync AI Sous-Chef.
    Generate a culinary plan matching Cuisine: {cuisine} and Diet: {diet}.
    Directives:
    1. Diet Constraint: If VEG, strictly pure vegetarian (no meat, fish, eggs). If NON_VEG, include authentic poultry/meat/seafood dishes.
    2. Format: Use comma separators (", ") between meal items instead of "+". Keep concise (<35 chars).
    3. Script: For Indian regional cuisines, use natural Devanagari script. For international/Continental, use clear English.
    4. task1: Today's immediate grocery/cooking prep task.
    5. task2: Overnight/advance prep for tomorrow (soaking, fermenting, marinating).
    """

    prompt_text = f"Day: {target_day}\nCuisine: {cuisine}\nDiet: {diet}\nNotes: {user_prompt}\nReturn strict JSON schema."

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
# 5. TELEMETRY LOGGING
# ============================================================================
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
        <meta http-equiv="refresh" content="20">
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
        <h2>📊 MealSync Hardware Telemetry Logs (N1B4V02 / ESP32-C3)</h2>
        <table>
            <thead>
                <tr>
                    <th>Timestamp (IST)</th>
                    <th>Action</th>
                    <th>Battery</th>
                    <th>Voltage</th>
                    <th>Wi-Fi Strength</th>
                    <th>Hash</th>
                    <th>Next Sleep Slot</th>
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
# 6. E-PAPER BITMAP RENDERER (N1B4V02 400x300 Otsu 1-Bit Stream)
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

        # Header Bar
        draw.rectangle([0, 0, CANVAS_W - 1, 38 * SCALE], fill=0)
        draw.rectangle([0, 0, CANVAS_W - 1, CANVAS_H - 1], outline=0, width=2 * SCALE)
        draw.text((10 * SCALE, 9 * SCALE), "MealSync", font=font_logo, fill=255)

        # Wi-Fi Signal
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

        # Meals & Tasks
        draw_autofit_text(draw, data["breakfast"], 128, 44, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["lunch"], 128, 106, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)
        draw_autofit_text(draw, data["dinner"], 128, 170, 260, 48, max_size=18, min_size=13, max_lines=2, fill_color=0)

        draw.rectangle([128 * SCALE, 243 * SCALE, 142 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task1"], 148, 238, 112, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        draw.rectangle([264 * SCALE, 243 * SCALE, 278 * SCALE, 257 * SCALE], outline=0, width=2 * SCALE)
        draw_autofit_text(draw, data["task2"], 284, 238, 110, 32, max_size=17, min_size=14, max_lines=1, fill_color=0)

        # Cross-version safe Lanczos downsampling
        resample_mode = Image.LANCZOS if hasattr(Image, 'LANCZOS') else getattr(Image, 'ANTIALIAS', 1)
        img_downscaled = img_2x.resize((PANEL_WIDTH, PANEL_HEIGHT), resample=resample_mode)
        img_1bit = img_downscaled.point(lambda p: 255 if p > 160 else 0, mode="1")

        # Inverted raw byte delivery for ESP32 firmware
        if "ESP32" in request.headers.get("User-Agent", "") or request.args.get('raw') == '1':
            img_epd = ImageOps.invert(img_1bit.convert("L")).point(lambda p: 255 if p > 140 else 0, mode="1")
            return Response(img_epd.tobytes(), mimetype='application/octet-stream')

        # Standard BMP for browser preview
        buf = io.BytesIO()
        img_1bit.save(buf, format='BMP')
        buf.seek(0)
        return send_file(buf, mimetype='image/bmp')

    except Exception as err:
        traceback.print_exc()
        return f"Internal Error: {err}", 500

# ============================================================================
# 7. PROGRESSIVE WEB APP (MULTI-SCREEN COMPANION UI)
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
      
      <link rel="preconnect" href="https://fonts.googleapis.com">
      <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
      <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Plus+Jakarta+Sans:wght@600;700;800&family=Yantramanav:wght@500;700;900&display=swap" rel="stylesheet">
      
      <script src="https://cdn.tailwindcss.com"></script>
      <script>
        tailwind.config = {
          theme: {
            extend: {
              fontFamily: {
                sans: ['"Plus Jakarta Sans"', 'sans-serif'],
                english: ['"Outfit"', 'sans-serif'],
                marathi: ['"Yantramanav"', 'sans-serif'],
              }
            }
          }
        }
      </script>
      <style>
        .active-pulse {
          animation: subtle-pulse 2s infinite;
        }
        @keyframes subtle-pulse {
          0%, 100% { box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.4); }
          50% { box-shadow: 0 0 0 6px rgba(16, 185, 129, 0); }
        }
      </style>
    </head>
    <body class="bg-slate-50 text-slate-800 font-sans min-h-screen flex flex-col items-center justify-between">

      <!-- SCREEN 1: LOGIN / ONBOARDING -->
      <section id="screen-login" class="w-full max-w-md px-6 py-12 flex flex-col justify-center min-h-screen space-y-8">
        <div class="text-center space-y-3">
          <div class="inline-flex items-center justify-center w-20 h-20 rounded-3xl bg-gradient-to-tr from-teal-500 to-emerald-400 text-white shadow-xl shadow-teal-500/20 text-4xl mb-2">
            🍳
          </div>
          <h1 class="text-3xl font-extrabold tracking-tight text-slate-900">MealSync Hub</h1>
          <p class="text-sm text-slate-500 font-english leading-relaxed">Smart kitchen planner, AI sous-chef, and companion controller for your E-Paper kitchen board.</p>
        </div>

        <div class="bg-white border border-slate-200/80 rounded-3xl p-6 shadow-xl shadow-slate-200/50 space-y-4">
          <div>
            <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1.5">Your Name</label>
            <input id="login-name" type="text" placeholder="e.g. Abhiram" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 text-sm font-semibold outline-none focus:ring-2 focus:ring-teal-500 transition-all" />
          </div>
          
          <div>
            <label class="block text-xs font-bold text-slate-500 uppercase tracking-wider mb-1.5">Dietary Preference</label>
            <div class="grid grid-cols-3 gap-2">
              <button onclick="setDiet('VEG')" id="diet-btn-veg" class="diet-btn py-2.5 rounded-xl border text-xs font-bold transition-all bg-emerald-50 border-emerald-500 text-emerald-700">🟢 Pure Veg</button>
              <button onclick="setDiet('NON_VEG')" id="diet-btn-nonveg" class="diet-btn py-2.5 rounded-xl border text-xs font-bold transition-all bg-slate-50 border-slate-200 text-slate-600">🔴 Non-Veg</button>
              <button onclick="setDiet('BOTH')" id="diet-btn-both" class="diet-btn py-2.5 rounded-xl border text-xs font-bold transition-all bg-slate-50 border-slate-200 text-slate-600">🟡 Both</button>
            </div>
          </div>

          <button onclick="handleLoginSubmit()" class="w-full bg-slate-900 hover:bg-slate-800 text-white font-bold text-sm py-4 rounded-xl shadow-lg shadow-slate-900/10 active:scale-[0.99] transition-all">
            Choose Cuisine ➔
          </button>
        </div>
      </section>

      <!-- SCREEN 2: CUISINE SELECTION -->
      <section id="screen-cuisine" class="w-full max-w-md px-5 py-8 hidden space-y-6">
        <div class="space-y-1">
          <span class="text-xs font-bold text-teal-600 uppercase tracking-wider">Step 2 of 2</span>
          <h2 class="text-2xl font-extrabold text-slate-900">Choose Kitchen Cuisine</h2>
          <p class="text-xs text-slate-500 font-english">Select your culinary theme. Language and recipes adjust automatically.</p>
        </div>

        <div class="space-y-4">
          <div class="text-xs font-bold text-slate-400 uppercase tracking-wider px-1">Indian Regional Cuisines</div>
          <div class="grid grid-cols-2 gap-3">
            <button onclick="selectCuisineTheme('Maharashtrian')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all">
              <span class="text-xl block mb-1">🌾</span>
              <div class="font-bold text-sm text-slate-800">Maharashtrian</div>
              <div class="text-[11px] text-teal-600 font-marathi">पारंपारिक जेवण</div>
            </button>
            <button onclick="selectCuisineTheme('South Indian')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all">
              <span class="text-xl block mb-1">🥥</span>
              <div class="font-bold text-sm text-slate-800">South Indian</div>
              <div class="text-[11px] text-teal-600 font-marathi">इडली, डोसा, सांबार</div>
            </button>
            <button onclick="selectCuisineTheme('Gujarati')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all">
              <span class="text-xl block mb-1">🥘</span>
              <div class="font-bold text-sm text-slate-800">Gujarati</div>
              <div class="text-[11px] text-teal-600 font-marathi">थेपला, गुजराती कढी</div>
            </button>
            <button onclick="selectCuisineTheme('Marwadi / Rajasthani')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all">
              <span class="text-xl block mb-1">🍲</span>
              <div class="font-bold text-sm text-slate-800">Marwadi / Raj</div>
              <div class="text-[11px] text-teal-600 font-marathi">दाल बाटी, गट्टे भाजी</div>
            </button>
            <button onclick="selectCuisineTheme('North Indian / Punjabi')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all col-span-2">
              <span class="text-xl block mb-1">🫓</span>
              <div class="font-bold text-sm text-slate-800">North Indian / Punjabi</div>
              <div class="text-[11px] text-teal-600 font-marathi">पनीर, पराठा, दाल मखनी</div>
            </button>
          </div>

          <div class="text-xs font-bold text-slate-400 uppercase tracking-wider px-1 pt-2">Global & Continental</div>
          <div class="grid grid-cols-2 gap-3">
            <button onclick="selectCuisineTheme('Continental / European')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all">
              <span class="text-xl block mb-1">🥗</span>
              <div class="font-bold text-sm text-slate-800">Continental</div>
              <div class="text-[11px] text-slate-500">Pastas, Soups, Salads</div>
            </button>
            <button onclick="selectCuisineTheme('USA / American')" class="cuisine-card text-left p-3.5 bg-white border border-slate-200/80 rounded-2xl shadow-sm hover:border-teal-500 active:scale-95 transition-all">
              <span class="text-xl block mb-1">🥞</span>
              <div class="font-bold text-sm text-slate-800">American</div>
              <div class="text-[11px] text-slate-500">Oats, Bowls, Wraps</div>
            </button>
          </div>
        </div>
      </section>

      <!-- SCREEN 3: MAIN MEAL PLANNER & DASHBOARD -->
      <section id="screen-planner" class="w-full max-w-md px-4 py-4 hidden space-y-4">
        <div class="flex items-center justify-between pb-1 border-b border-slate-200/60">
          <div class="flex items-center gap-2">
            <span class="text-2xl">🍳</span>
            <div>
              <h2 class="font-extrabold text-base tracking-tight text-slate-900 leading-tight">MealSync Hub</h2>
              <div class="flex items-center gap-1.5 mt-0.5">
                <span id="active-cuisine-badge" class="text-[10px] font-bold text-teal-700 bg-teal-50 border border-teal-200/60 px-2 py-0.5 rounded-full">Maharashtrian</span>
                <span id="active-diet-badge" class="text-[10px] font-bold text-emerald-700 bg-emerald-50 border border-emerald-200/60 px-2 py-0.5 rounded-full">🟢 Veg</span>
              </div>
            </div>
          </div>
          <div class="flex items-center gap-2">
            <button onclick="togglePreviewModal()" class="flex items-center gap-1 bg-slate-900 hover:bg-slate-800 text-white text-[11px] font-bold px-2.5 py-1.5 rounded-lg shadow-sm transition-all">
              <span>📱 Preview</span>
            </button>
            <button onclick="toggleSettingsModal()" class="w-8 h-8 rounded-lg bg-slate-100 hover:bg-slate-200 flex items-center justify-center text-slate-600 font-bold transition-all">
              ⚙️
            </button>
          </div>
        </div>

        <!-- 7-Day Day Selector Bar -->
        <div class="flex gap-1.5 overflow-x-auto pb-1 scrollbar-none" id="day-bar">
          <button onclick="selectDay('Monday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Mon</button>
          <button onclick="selectDay('Tuesday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Tue</button>
          <button onclick="selectDay('Wednesday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Wed</button>
          <button onclick="selectDay('Thursday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Thu</button>
          <button onclick="selectDay('Friday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Fri</button>
          <button onclick="selectDay('Saturday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Sat</button>
          <button onclick="selectDay('Sunday')" class="day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600">Sun</button>
        </div>

        <!-- AI Pro Mode Generator Box -->
        <div class="bg-gradient-to-br from-teal-500/10 via-emerald-500/10 to-indigo-500/10 border border-teal-500/20 rounded-2xl p-3 shadow-sm space-y-2">
          <div class="flex items-center justify-between">
            <span class="text-[11px] font-extrabold text-teal-900 uppercase tracking-wider">✨ AI Sous-Chef</span>
            <span id="active-day-label" class="text-[11px] font-bold text-slate-500">Planning: Monday</span>
          </div>
          <div class="flex gap-2">
            <input id="ai-theme-input" type="text" placeholder="Custom note (e.g. fasting, high protein)..." class="w-full bg-white border border-slate-200 rounded-xl px-3 py-2 text-xs focus:ring-2 focus:ring-teal-500 outline-none text-slate-800" />
            <button onclick="generatePlanForDay()" id="ai-gen-btn" class="bg-teal-600 hover:bg-teal-700 active:scale-95 text-white font-bold text-xs px-3.5 py-2 rounded-xl transition-all whitespace-nowrap shadow-sm shadow-teal-600/30">
              Generate
            </button>
          </div>
        </div>

        <!-- Meal Cards with Real-Time Highlighting -->
        <div class="space-y-2.5">
          <div id="card-breakfast" class="meal-card bg-white border border-slate-200/80 rounded-2xl p-3.5 shadow-sm focus-within:border-amber-500 focus-within:ring-2 focus-within:ring-amber-500/20 transition-all">
            <div class="flex items-center justify-between mb-1">
              <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-full bg-amber-400"></span>
                <span class="text-[10px] font-extrabold uppercase tracking-wider text-slate-400">Breakfast</span>
              </div>
              <span id="badge-breakfast" class="hidden text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded bg-amber-100 text-amber-800">Active Now</span>
            </div>
            <input id="input-breakfast" type="text" class="w-full text-base font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. पोहे, चहा" />
          </div>

          <div id="card-lunch" class="meal-card bg-white border border-slate-200/80 rounded-2xl p-3.5 shadow-sm focus-within:border-emerald-500 focus-within:ring-2 focus-within:ring-emerald-500/20 transition-all">
            <div class="flex items-center justify-between mb-1">
              <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-full bg-emerald-500"></span>
                <span class="text-[10px] font-extrabold uppercase tracking-wider text-slate-400">Lunch</span>
              </div>
              <span id="badge-lunch" class="hidden text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-800">Active Now</span>
            </div>
            <input id="input-lunch" type="text" class="w-full text-base font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. वरण भात, पोळी, भेंडी भाजी" />
          </div>

          <div id="card-dinner" class="meal-card bg-white border border-slate-200/80 rounded-2xl p-3.5 shadow-sm focus-within:border-indigo-500 focus-within:ring-2 focus-within:ring-indigo-500/20 transition-all">
            <div class="flex items-center justify-between mb-1">
              <div class="flex items-center gap-1.5">
                <span class="w-2.5 h-2.5 rounded-full bg-indigo-500"></span>
                <span class="text-[10px] font-extrabold uppercase tracking-wider text-slate-400">Dinner</span>
              </div>
              <span id="badge-dinner" class="hidden text-[9px] font-extrabold uppercase px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-800">Active Now</span>
            </div>
            <input id="input-dinner" type="text" class="w-full text-base font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. मुगाची खिचडी, कढी, पापड" />
          </div>

          <!-- Tasks Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-2.5 pt-0.5">
            <div class="bg-white border border-slate-200/80 rounded-2xl p-3 shadow-sm focus-within:border-teal-500 focus-within:ring-2 focus-within:ring-teal-500/20 transition-all">
              <div class="flex items-center gap-1.5 mb-1">
                <span class="w-2 h-2 rounded-full bg-teal-500"></span>
                <span class="text-[9px] font-extrabold uppercase tracking-wider text-slate-400">Task 1 (Today Prep)</span>
              </div>
              <input id="input-task1" type="text" class="w-full text-xs font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. दूध आणणे, कोथिंबीर कापणे" />
            </div>

            <div class="bg-white border border-slate-200/80 rounded-2xl p-3 shadow-sm focus-within:border-rose-500 focus-within:ring-2 focus-within:ring-rose-500/20 transition-all">
              <div class="flex items-center gap-1.5 mb-1">
                <span class="w-2 h-2 rounded-full bg-rose-500"></span>
                <span class="text-[9px] font-extrabold uppercase tracking-wider text-rose-500">Task 2 (Advance Prep 🌙)</span>
              </div>
              <input id="input-task2" type="text" class="w-full text-xs font-bold text-slate-800 outline-none placeholder-slate-300" placeholder="e.g. उद्यासाठी साबुदाणा भिजवणे" />
            </div>
          </div>
        </div>

        <!-- Sync Button -->
        <button onclick="saveCurrentDayMenu()" id="save-btn" class="w-full bg-slate-900 hover:bg-slate-800 active:scale-[0.99] text-white font-extrabold text-sm py-3.5 rounded-2xl shadow-lg shadow-slate-900/10 transition-all flex items-center justify-center gap-2">
          <span>💾 Save & Sync E-Paper</span>
        </button>
      </section>

      <!-- SETTINGS MODAL -->
      <div id="settings-modal" class="fixed inset-0 z-50 bg-slate-900/80 backdrop-blur-sm flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-3xl p-5 max-w-sm w-full shadow-2xl space-y-4">
          <div class="flex items-center justify-between pb-2 border-b border-slate-100">
            <h3 class="font-extrabold text-slate-900 text-sm">Settings & Preferences</h3>
            <button onclick="toggleSettingsModal()" class="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-bold hover:bg-slate-200">✕</button>
          </div>

          <div class="space-y-3 text-xs">
            <div>
              <label class="block font-bold text-slate-600 mb-1">Active Cuisine Theme</label>
              <select id="setting-cuisine" onchange="changeCuisineFromSettings(this.value)" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 font-bold outline-none">
                <option value="Maharashtrian">Maharashtrian (पारंपारिक)</option>
                <option value="South Indian">South Indian (सांबार, डोसा)</option>
                <option value="North Indian / Punjabi">North Indian / Punjabi</option>
                <option value="Gujarati">Gujarati (थेपला, कढी)</option>
                <option value="Marwadi / Rajasthani">Marwadi / Rajasthani</option>
                <option value="Continental / European">Continental / European</option>
                <option value="USA / American">USA / American</option>
              </select>
            </div>

            <div>
              <label class="block font-bold text-slate-600 mb-1">Dietary Filter</label>
              <select id="setting-diet" onchange="changeDietFromSettings(this.value)" class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 font-bold outline-none">
                <option value="VEG">🟢 Pure Vegetarian</option>
                <option value="NON_VEG">🔴 Non-Vegetarian</option>
                <option value="BOTH">🟡 Both / Flexible</option>
              </select>
            </div>

            <div class="grid grid-cols-3 gap-2">
              <div>
                <label class="block font-bold text-slate-600 mb-1">Breakfast</label>
                <input id="time-bf" type="text" value="08:00-09:30" class="w-full bg-slate-50 border border-slate-200 rounded-lg p-1.5 text-center font-mono font-bold" />
              </div>
              <div>
                <label class="block font-bold text-slate-600 mb-1">Lunch</label>
                <input id="time-lunch" type="text" value="12:30-14:00" class="w-full bg-slate-50 border border-slate-200 rounded-lg p-1.5 text-center font-mono font-bold" />
              </div>
              <div>
                <label class="block font-bold text-slate-600 mb-1">Dinner</label>
                <input id="time-dinner" type="text" value="20:00-21:30" class="w-full bg-slate-50 border border-slate-200 rounded-lg p-1.5 text-center font-mono font-bold" />
              </div>
            </div>

            <div>
              <label class="block font-bold text-slate-600 mb-1">Custom Gemini API Key (BYOK)</label>
              <input id="user-gemini-key" type="password" placeholder="AIzaSy..." class="w-full bg-slate-50 border border-slate-200 rounded-xl px-3 py-2 font-mono text-[11px] outline-none" />
            </div>
          </div>

          <button onclick="saveSettings()" class="w-full bg-teal-600 hover:bg-teal-700 text-white font-bold text-xs py-3 rounded-xl transition-all">
            Save Preferences
          </button>
        </div>
      </div>

      <!-- LIVE E-PAPER PREVIEW MODAL -->
      <div id="preview-modal" class="fixed inset-0 z-50 bg-slate-900/80 backdrop-blur-sm flex items-center justify-center p-4 hidden">
        <div class="bg-white rounded-3xl p-5 max-w-sm w-full shadow-2xl border border-slate-100 space-y-3">
          <div class="flex items-center justify-between pb-2 border-b border-slate-100">
            <div>
              <h3 class="font-extrabold text-slate-900 text-sm">4.2" E-Paper Preview</h3>
              <p class="text-[10px] text-slate-400">Live 1-bit monochrome stream (N1B4V02 / 400x300)</p>
            </div>
            <button onclick="togglePreviewModal()" class="w-7 h-7 rounded-full bg-slate-100 flex items-center justify-center text-slate-500 font-bold hover:bg-slate-200">✕</button>
          </div>

          <div class="bg-slate-100 rounded-2xl p-1.5 flex items-center justify-center overflow-hidden border border-slate-200">
            <img id="epaper-stream-img" src="/display.bmp" alt="E-Paper Stream" class="w-full h-auto rounded-xl shadow-inner border border-slate-300" />
          </div>

          <div class="flex gap-2">
            <button onclick="refreshPreviewImage()" class="flex-1 bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-bold py-2 rounded-xl transition-all">
              🔄 Refresh
            </button>
            <a href="/logs" target="_blank" class="flex-1 bg-teal-50 text-teal-700 hover:bg-teal-100 text-xs font-bold py-2 rounded-xl text-center transition-all">
              📊 Logs
            </a>
          </div>
        </div>
      </div>

      <footer class="w-full max-w-md px-4 py-4 text-center text-[11px] text-slate-400 font-english">
        MealSync V1.0 • Connected Kitchen
      </footer>

      <div id="toast" class="fixed bottom-5 left-1/2 -translate-x-1/2 z-50 bg-slate-900 text-white text-xs font-bold px-4 py-2.5 rounded-full shadow-xl opacity-0 pointer-events-none transition-all duration-300">
        Menu Saved!
      </div>

      <script>
        let currentScreen = "login";
        let activeDay = "Monday";
        let activeCuisine = "Maharashtrian";
        let activeDiet = "VEG";
        let weeklyMenuCache = {};

        window.addEventListener('DOMContentLoaded', () => {
          const storedUser = localStorage.getItem('MEALSYNC_USER');
          const storedCuisine = localStorage.getItem('MEALSYNC_CUISINE');
          const storedDiet = localStorage.getItem('MEALSYNC_DIET_PREF');
          const storedKey = localStorage.getItem('MEALSYNC_USER_GEMINI_KEY');

          if (storedDiet) activeDiet = storedDiet;
          if (storedKey) document.getElementById('user-gemini-key').value = storedKey;

          if (storedUser && storedCuisine) {
            activeCuisine = storedCuisine;
            showScreen('planner');
            initTodayTab();
            loadWeeklySchedule();
          } else if (storedUser) {
            showScreen('cuisine');
          } else {
            showScreen('login');
          }

          if ("Notification" in window && Notification.permission === "default") {
            Notification.requestPermission();
          }

          setInterval(checkActiveMealWindow, 60000);
          checkActiveMealWindow();
        });

        function setDiet(diet) {
          activeDiet = diet;
          ['veg', 'nonveg', 'both'].forEach(d => {
            const btn = document.getElementById(`diet-btn-${d}`);
            btn.className = "diet-btn py-2.5 rounded-xl border text-xs font-bold transition-all bg-slate-50 border-slate-200 text-slate-600";
          });
          const activeBtn = document.getElementById(`diet-btn-${diet.toLowerCase().replace('_','')}`);
          if (activeBtn) {
            activeBtn.className = "diet-btn py-2.5 rounded-xl border text-xs font-bold transition-all bg-emerald-50 border-emerald-500 text-emerald-700";
          }
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
          currentScreen = screen;
        }

        function handleLoginSubmit() {
          const name = document.getElementById('login-name').value.trim();
          if (!name) {
            showToast("Please enter your name.");
            return;
          }
          localStorage.setItem('MEALSYNC_USER', JSON.stringify({ name }));
          localStorage.setItem('MEALSYNC_DIET_PREF', activeDiet);
          showScreen('cuisine');
        }

        function selectCuisineTheme(cuisine) {
          activeCuisine = cuisine;
          localStorage.setItem('MEALSYNC_CUISINE', cuisine);
          document.getElementById('setting-cuisine').value = cuisine;
          showScreen('planner');
          initTodayTab();
          loadWeeklySchedule();
        }

        function changeCuisineFromSettings(cuisine) {
          activeCuisine = cuisine;
          localStorage.setItem('MEALSYNC_CUISINE', cuisine);
          document.getElementById('active-cuisine-badge').innerText = cuisine;
          showToast(`Cuisine changed to ${cuisine}`);
        }

        function changeDietFromSettings(diet) {
          activeDiet = diet;
          localStorage.setItem('MEALSYNC_DIET_PREF', diet);
          document.getElementById('active-diet-badge').innerText = activeDiet === 'VEG' ? '🟢 Veg' : (activeDiet === 'NON_VEG' ? '🔴 Non-Veg' : '🟡 Both');
          showToast(`Diet filter updated: ${diet}`);
        }

        function initTodayTab() {
          const dayNames = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
          const today = dayNames[new Date().getDay()];
          selectDay(today);
        }

        function selectDay(day) {
          activeDay = day;
          document.getElementById('active-day-label').innerText = `Planning: ${day}`;

          document.querySelectorAll('.day-btn').forEach(btn => {
            if (btn.innerText.toLowerCase().startsWith(day.slice(0, 3).toLowerCase())) {
              btn.className = "day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-teal-600 text-white shadow-md shadow-teal-500/20";
            } else {
              btn.className = "day-btn px-3 py-1.5 rounded-xl text-xs font-bold transition-all bg-white border border-slate-200 text-slate-600 hover:bg-slate-100";
            }
          });

          renderActiveDayInputs();
        }

        async function loadWeeklySchedule() {
          try {
            const res = await fetch('/api/menu');
            if (res.ok) {
              const data = await res.json();
              data.forEach(item => {
                weeklyMenuCache[item.day_name] = item;
              });
              renderActiveDayInputs();
            }
          } catch (err) {
            showToast("Could not load menu from cloud.");
          }
        }

        function renderActiveDayInputs() {
          const item = weeklyMenuCache[activeDay] || { breakfast: '', lunch: '', dinner: '', task1: '', task2: '' };
          document.getElementById('input-breakfast').value = (item.breakfast || '').replace(/\\+/g, ',');
          document.getElementById('input-lunch').value = (item.lunch || '').replace(/\\+/g, ',');
          document.getElementById('input-dinner').value = (item.dinner || '').replace(/\\+/g, ',');
          document.getElementById('input-task1').value = (item.task1 || '').replace(/\\+/g, ',');
          document.getElementById('input-task2').value = (item.task2 || '').replace(/\\+/g, ',');
        }

        async function saveCurrentDayMenu() {
          const saveBtn = document.getElementById('save-btn');
          saveBtn.innerText = "Syncing...";
          saveBtn.disabled = true;

          const payload = {
            day_name: activeDay,
            breakfast: document.getElementById('input-breakfast').value.trim().replace(/\\+/g, ','),
            lunch: document.getElementById('input-lunch').value.trim().replace(/\\+/g, ','),
            dinner: document.getElementById('input-dinner').value.trim().replace(/\\+/g, ','),
            task1: document.getElementById('input-task1').value.trim().replace(/\\+/g, ','),
            task2: document.getElementById('input-task2').value.trim().replace(/\\+/g, ',')
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
            saveBtn.innerText = "💾 Save & Sync E-Paper";
            saveBtn.disabled = false;
          }
        }

        async function generatePlanForDay() {
          const aiBtn = document.getElementById('ai-gen-btn');
          const theme = document.getElementById('ai-theme-input').value.trim();
          const userKey = localStorage.getItem('MEALSYNC_USER_GEMINI_KEY') || "";

          aiBtn.innerText = "Cooking...";
          aiBtn.disabled = true;

          try {
            const res = await fetch('/api/ai-suggest', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({
                day_name: activeDay,
                cuisine: activeCuisine,
                diet: activeDiet,
                prompt: theme || `${activeDiet} ${activeCuisine} menu`,
                gemini_key: userKey
              })
            });

            if (res.ok) {
              const aiData = await res.json();
              document.getElementById('input-breakfast').value = (aiData.breakfast || '').replace(/\\+/g, ',');
              document.getElementById('input-lunch').value = (aiData.lunch || '').replace(/\\+/g, ',');
              document.getElementById('input-dinner').value = (aiData.dinner || '').replace(/\\+/g, ',');
              document.getElementById('input-task1').value = (aiData.task1 || '').replace(/\\+/g, ',');
              document.getElementById('input-task2').value = (aiData.task2 || '').replace(/\\+/g, ',');
              showToast("✨ AI Menu Generated! Tap Save to sync.");
            } else {
              showToast("API error. Check key in Settings.");
            }
          } catch (e) {
            showToast("Error generating menu.");
          } finally {
            aiBtn.innerText = "Generate";
            aiBtn.disabled = false;
          }
        }

        function parseRange(str) {
          try {
            const [start, end] = str.split('-');
            const [sh, sm] = start.split(':').map(Number);
            const [eh, em] = end.split(':').map(Number);
            return { sh, sm, eh, em };
          } catch {
            return null;
          }
        }

        function checkActiveMealWindow() {
          const now = new Date();
          const curMins = now.getHours() * 60 + now.getMinutes();

          const bf = parseRange(document.getElementById('time-bf').value || "08:00-09:30");
          const lunch = parseRange(document.getElementById('time-lunch').value || "12:30-14:00");
          const dinner = parseRange(document.getElementById('time-dinner').value || "20:00-21:30");

          ['breakfast', 'lunch', 'dinner'].forEach(m => {
            const card = document.getElementById(`card-${m}`);
            const badge = document.getElementById(`badge-${m}`);
            if (card) card.classList.remove('ring-2', 'ring-emerald-500', 'active-pulse');
            if (badge) badge.classList.add('hidden');
          });

          const check = (range, mealName) => {
            if (!range) return;
            const startMins = range.sh * 60 + range.sm;
            const endMins = range.eh * 60 + range.em;
            if (curMins >= startMins && curMins <= endMins) {
              const card = document.getElementById(`card-${mealName}`);
              const badge = document.getElementById(`badge-${mealName}`);
              if (card) card.classList.add('ring-2', 'ring-emerald-500', 'active-pulse');
              if (badge) badge.classList.remove('hidden');
            }
          };

          check(bf, 'breakfast');
          check(lunch, 'lunch');
          check(dinner, 'dinner');
        }

        function toggleSettingsModal() {
          document.getElementById('settings-modal').classList.toggle('hidden');
        }

        function saveSettings() {
          const key = document.getElementById('user-gemini-key').value.trim();
          if (key) localStorage.setItem('MEALSYNC_USER_GEMINI_KEY', key);
          toggleSettingsModal();
          showToast("Preferences saved!");
        }

        function togglePreviewModal() {
          const modal = document.getElementById('preview-modal');
          modal.classList.toggle('hidden');
          if (!modal.classList.contains('hidden')) {
            refreshPreviewImage();
          }
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
